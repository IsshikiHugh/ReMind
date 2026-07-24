"""A printer connection held open across jobs.

The CLI stays stateless — connect, print, disconnect, exit. The TUI is a
different situation: you sit in it, edit a note, print, edit again. Paying the
scan+connect cost (several seconds) for every one of those is the wrong trade,
so the TUI opens a session and keeps it warm.

The session is idle-expiring, not permanent: any activity refreshes a deadline,
and once nothing has happened for IDLE_TIMEOUT_S it disconnects on its own. A
thermal printer left connected indefinitely is a battery drain and blocks other
devices from pairing.

Battery is a *live* reading here. If we are not connected we do not know it,
and the UI says so rather than showing a stale number.
"""

from __future__ import annotations

import asyncio
import time

from .config import DeviceRecord, PrintConfig, find_device
from .core import PrintResult, find_printer, send_job
from .protocol import query_status
from .transport import BLETransport

IDLE_TIMEOUT_S = 300.0  # 5 minutes of no edit/print, then let go
STATUS_QUERY_TIMEOUT_S = 1.5


class PrinterSession:
    """One warm connection to one registered printer, or nothing."""

    def __init__(self, idle_timeout: float = IDLE_TIMEOUT_S) -> None:
        self.idle_timeout = idle_timeout
        self._transport: BLETransport | None = None
        self._record: DeviceRecord | None = None
        self._battery: int | None = None
        self._deadline: float = 0.0
        self._lock = asyncio.Lock()  # BLE work must not interleave
        self._pending: asyncio.Task | None = None  # in-flight scan, cancellable
        self._closing = False

    # -- state anyone can read --

    @property
    def connected(self) -> bool:
        return self._transport is not None and self._transport.is_connected

    @property
    def device(self) -> DeviceRecord | None:
        return self._record if self.connected else None

    @property
    def battery(self) -> int | None:
        """Live level, or None when we are not connected (never a stale value)."""
        return self._battery if self.connected else None

    @property
    def seconds_left(self) -> float:
        if not self.connected:
            return 0.0
        return max(0.0, self._deadline - time.monotonic())

    def touch(self) -> None:
        """Activity happened — restart the idle countdown."""
        if self.connected:
            self._deadline = time.monotonic() + self.idle_timeout

    def expired(self) -> bool:
        return self.connected and time.monotonic() >= self._deadline

    # -- lifecycle --

    async def ensure(self, address: str | None = None, timeout: float = 10.0) -> DeviceRecord:
        """Connect if needed and return the device. Refreshes the deadline.

        Raises the same errors as core.find_printer when nothing is reachable.
        """
        if self.connected and (address is None or self._record.address == address):
            self.touch()
            return self._record

        # The scan runs OUTSIDE the lock. It is the slow part — up to `timeout`
        # seconds — and holding a lock across it makes close() (and therefore
        # Quit) wait the whole scan out, which the user just sees as a hang.
        task = asyncio.ensure_future(find_printer(address=address, timeout=timeout))
        self._pending = task
        try:
            found, record = await task
        finally:
            if self._pending is task:
                self._pending = None

        async with self._lock:
            if self._closing:
                raise RuntimeError("session closed while connecting")
            await self._teardown()
            transport = BLETransport(
                found.device or found.address, on_disconnect=self._dropped
            )
            await transport.connect()
            self._transport = transport
            self._record = record
            self.touch()

        await self.refresh_battery()
        return record

    def _dropped(self) -> None:
        """The printer hung up on us (idle sleep, out of range)."""
        self._battery = None
        self._deadline = 0.0

    async def close(self) -> None:
        """Let go now. Cancels an in-flight scan rather than waiting it out."""
        self._closing = True
        task, self._pending = self._pending, None
        if task is not None and not task.done():
            task.cancel()
        try:
            async with self._lock:
                await self._teardown()
        finally:
            self._closing = False

    async def _teardown(self) -> None:
        if self._transport is not None:
            await self._transport.disconnect()
        self._transport = None
        self._record = None
        self._battery = None
        self._deadline = 0.0

    # -- work --

    async def refresh_battery(self) -> int | None:
        """Ask the printer how it is doing. Cheap: the connection is already up."""
        if not self.connected:
            return None
        async with self._lock:
            if not self.connected:
                return None
            status = await query_status(
                self._transport, ("battery",), timeout=STATUS_QUERY_TIMEOUT_S
            )
        value = status.get("battery")
        self._battery = int(value) if isinstance(value, int) else None
        return self._battery

    async def print(
        self, text: str, *, config: PrintConfig | None = None
    ) -> PrintResult:
        """Print over the warm connection. Refreshes the deadline.

        Re-reads the device's stored settings first. The record captured when
        we connected is a snapshot, and since connecting now happens as soon
        as you open the editor, anything you change afterwards — font size,
        margins — would otherwise never reach the paper.
        """
        if not self.connected:
            raise RuntimeError("print() called without a live session")
        current = find_device(self._record.address)
        if current is not None:
            self._record = current
        async with self._lock:
            result = await send_job(self._transport, self._record, text, config=config)
        self._battery = result.battery
        self.touch()
        return result
