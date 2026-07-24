"""BLE transport for Phomemo M02.

Pure communication layer — knows nothing about rendering or the M02 command
protocol. Scans for the device, connects, sends bytes in chunks, disconnects.
Decoupled on purpose so a future daemon / HTTP server can reuse it unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from bleak import BleakScanner, BleakClient

# Phomemo BLE UUIDs (from phomymo's constants.js), 16-bit shorthand
# expanded to the full 128-bit form bleak expects. The characteristics live
# under service 0xff00, but bleak resolves them by their own UUID, so the
# service one is never needed.
WRITE_CHAR_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000ff03-0000-1000-8000-00805f9b34fb"

CHUNK_SIZE = 128
CHUNK_DELAY_S = 0.02


@dataclass
class FoundDevice:
    name: str
    address: str  # MAC on Linux, system UUID on macOS
    device: object = None  # raw bleak BLEDevice; pass to BleakClient to skip re-discovery
    rssi: int | None = None  # signal strength of the advertisement we saw


def _matches(name: str | None, name_prefix: str) -> bool:
    return bool(name) and name_prefix.lower() in name.lower()


def _rssi(adv) -> int | None:
    value = getattr(adv, "rssi", None)
    return int(value) if isinstance(value, (int, float)) else None


async def scan(name_prefix: str = "M02", timeout: float = 8.0) -> FoundDevice | None:
    """Scan for the first device whose name matches name_prefix.

    Stops the moment a match is seen (does NOT wait out the full timeout), so a
    printer that is already advertising is usually found in 1-3s. Returns None
    if nothing matched within timeout (printer off / out of range).
    """
    found: FoundDevice | None = None
    done = asyncio.Event()

    def on_detect(device, adv) -> None:
        nonlocal found
        name = device.name or getattr(adv, "local_name", None)
        if found is None and _matches(name, name_prefix):
            found = FoundDevice(
                name=name, address=device.address, device=device, rssi=_rssi(adv)
            )
            done.set()

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.wait_for(done.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return found


async def scan_all(
    name_prefix: str | None = None,
    timeout: float = 8.0,
    on_found: "Callable[[FoundDevice], None] | None" = None,
) -> list[FoundDevice]:
    """Collect every nearby named device over the full window (for a picker).

    Unlike scan(), this does not early-stop — it waits out timeout so the caller
    gets the complete list. Optionally filter by name_prefix. `on_found` fires
    once per newly-seen device so a UI can fill its list as results arrive.
    """
    found: dict[str, FoundDevice] = {}

    def on_detect(device, adv) -> None:
        name = device.name or getattr(adv, "local_name", None)
        if not name:
            return
        if name_prefix and name_prefix.lower() not in name.lower():
            return
        known = found.get(device.address)
        if known is not None:
            known.rssi = _rssi(adv) or known.rssi  # keep the freshest signal
            return
        fd = FoundDevice(name=name, address=device.address, device=device, rssi=_rssi(adv))
        found[device.address] = fd
        if on_found is not None:
            on_found(fd)

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.sleep(timeout)
    finally:
        await scanner.stop()
    return list(found.values())


async def scan_by_addresses(
    addresses: list[str], timeout: float = 8.0, stop_on: str | None = None
) -> list[FoundDevice]:
    """Collect devices whose address is in `addresses`, seen within timeout.

    Early-stops the moment `stop_on` (the top-priority address) is seen, since
    nothing can outrank it. Otherwise waits out the window and returns whatever
    matched, so the caller can pick the highest-priority one present.
    """
    wanted = set(addresses)
    found: dict[str, FoundDevice] = {}
    done = asyncio.Event()

    def on_detect(device, adv) -> None:
        if device.address in wanted and device.address not in found:
            name = device.name or getattr(adv, "local_name", None) or ""
            found[device.address] = FoundDevice(
                name=name, address=device.address, device=device, rssi=_rssi(adv)
            )
            if stop_on is not None and device.address == stop_on:
                done.set()

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.wait_for(done.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return list(found.values())


class BLETransport:
    """Connect to one device, send bytes, disconnect.

    Usable two ways: as an async context manager for a one-shot job, or held
    open across jobs (connect/disconnect) so a UI can keep a warm connection
    and skip the scan+connect cost on every print.
    """

    def __init__(self, target, on_disconnect: Callable[[], None] | None = None):
        # target may be an address string or a raw bleak BLEDevice. Passing the
        # BLEDevice (from scan) avoids a costly re-discovery on macOS connect.
        self._on_disconnect = on_disconnect
        self._client = BleakClient(target, disconnected_callback=self._disconnected)

    def _disconnected(self, _client) -> None:
        # The printer can drop us on its own (idle sleep, out of range), so a
        # held-open session has to hear about it rather than assume it is live.
        if self._on_disconnect is not None:
            self._on_disconnect()

    @property
    def is_connected(self) -> bool:
        return bool(self._client.is_connected)

    async def connect(self) -> "BLETransport":
        await self._client.connect()
        return self

    async def disconnect(self) -> None:
        try:
            await self._client.disconnect()
        except Exception:
            pass  # already gone; nothing to salvage

    async def __aenter__(self) -> "BLETransport":
        return await self.connect()

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def send(self, data: bytes) -> None:
        # response=True gives per-write flow control. Without it, the M02's
        # buffer overruns on bulk raster data and the print is truncated.
        await self._client.write_gatt_char(WRITE_CHAR_UUID, bytes(data), response=True)

    async def send_chunked(self, data: bytes) -> None:
        for i in range(0, len(data), CHUNK_SIZE):
            await self.send(data[i : i + CHUNK_SIZE])
            await asyncio.sleep(CHUNK_DELAY_S)

    async def start_notify(self, callback: Callable[[object, bytearray], None]) -> bool:
        """Subscribe to the printer's reply channel. False if it has none."""
        try:
            await self._client.start_notify(NOTIFY_CHAR_UUID, callback)
            return True
        except Exception:  # characteristic missing / already subscribed
            return False

    async def stop_notify(self) -> None:
        try:
            await self._client.stop_notify(NOTIFY_CHAR_UUID)
        except Exception:
            pass
