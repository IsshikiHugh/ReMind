"""High-level orchestration: the one call the CLI / TUI build on.

Composes the four layers — config (which devices, how they print), transport
(BLE), render (text→raster), protocol (M02 commands) — into "print this text".
Kept deliberately thin so each layer stays independently reusable.

Trust model: printing only ever targets a *registered* device (matched by
address), never a device that merely has a familiar-looking name.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .config import (
    DeviceRecord,
    PrintConfig,
    find_device,
    load_devices,
    save_devices,
)
from .protocol import print_raster, query_status
from .render import render_text
from .transport import (
    BLETransport,
    FoundDevice,
    scan_all,
    scan_by_addresses,
)

BATTERY_QUERY_TIMEOUT_S = 1.0
# BLE advertising intervals are slow and irregular; a cold printer can take a
# while to be seen at all, so give the scan real room before giving up.
DEFAULT_SCAN_TIMEOUT_S = 10.0


class PrinterNotFoundError(RuntimeError):
    """Raised when the target printer is not advertising (off / out of range)."""


class NoDeviceRegisteredError(PrinterNotFoundError):
    """Raised when nothing has been registered yet — nothing to even look for."""


@dataclass
class PrintResult:
    device: DeviceRecord  # the registered device that actually got the job
    lines: int
    battery: int | None = None  # level read after printing, if the printer told us


# ---------------------------------------------------------------------------
# Device registry (TOFU). The interactive "pick from list" UX belongs to the
# TUI; these are the lib building blocks it calls.
# ---------------------------------------------------------------------------


async def discover(
    name_prefix: str | None = None, timeout: float = 8.0, on_found=None
) -> list[FoundDevice]:
    """List nearby BLE devices for the user to choose from when registering.

    `on_found` fires as each new device appears, so a UI can fill in live.
    """
    return await scan_all(name_prefix=name_prefix, timeout=timeout, on_found=on_found)


def register_device(device: FoundDevice, *, order: int | None = None) -> DeviceRecord:
    """Persist a chosen device to the config (create or update by address).

    If order is None, a new device is appended after the current last one.
    Re-registering an existing address updates its name/platform in place and
    keeps that device's print config.
    """
    records = load_devices()
    existing = next((r for r in records if r.address == device.address), None)
    if existing is not None:
        existing.name = device.name
        existing.platform = sys.platform
        if order is not None:
            existing.order = order
        save_devices(records)
        return existing

    new_order = order if order is not None else max((r.order for r in records), default=-1) + 1
    record = DeviceRecord(
        name=device.name,
        address=device.address,
        platform=sys.platform,
        order=new_order,
    )
    records.append(record)
    save_devices(records)
    return record


def unregister_device(address: str) -> DeviceRecord | None:
    """Forget a device (and its print config). Returns the removed record."""
    records = load_devices()
    victim = next((r for r in records if r.address == address), None)
    if victim is None:
        return None
    records.remove(victim)
    save_devices(records)  # re-normalises order to 0..n-1
    return victim


def move_device(address: str, delta: int) -> bool:
    """Shift a device up/down the priority order. False if it can't move."""
    records = load_devices()  # sorted by order
    index = next((i for i, r in enumerate(records) if r.address == address), None)
    if index is None:
        return False
    target = index + delta
    if not 0 <= target < len(records):
        return False
    records[index], records[target] = records[target], records[index]
    for i, r in enumerate(records):
        r.order = i
    save_devices(records)
    return True


def resolve_device(spec: str | int | None) -> DeviceRecord | None:
    """Look up a device the way a caller would name it on a command line.

    Accepts the 1-based id from `list device`, a full address, or a name.
    None means "no preference" and resolves to the top-priority device.
    """
    records = load_devices()
    if not records:
        return None
    if spec is None:
        return records[0]
    text = str(spec).strip()
    if text.isdigit():
        index = int(text) - 1
        return records[index] if 0 <= index < len(records) else None
    for record in records:
        if record.address.lower() == text.lower():
            return record
    for record in records:
        if record.name.lower() == text.lower():
            return record
    return None


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


async def scan_registered(timeout: float = DEFAULT_SCAN_TIMEOUT_S) -> FoundDevice | None:
    """Scan registered devices by order; return the highest-priority one present.

    Scans all registered addresses at once, early-stopping if the #1 device is
    seen. Among whatever is advertising, returns the lowest-order (frontmost)
    device. None if none are registered or none are in range.
    """
    records = load_devices()  # already sorted by order asc
    if not records:
        return None
    addresses = [r.address for r in records]
    found = await scan_by_addresses(addresses, timeout=timeout, stop_on=records[0].address)
    if not found:
        return None
    order_by_addr = {r.address: r.order for r in records}
    found.sort(key=lambda fd: order_by_addr.get(fd.address, 1 << 30))
    return found[0]


async def find_printer(
    address: str | None = None, timeout: float = DEFAULT_SCAN_TIMEOUT_S
) -> tuple[FoundDevice, DeviceRecord]:
    """Locate the printer to use: a specific registered one, or the best online.

    Raises NoDeviceRegisteredError / PrinterNotFoundError with a message meant
    for a human — the caller can print it as-is.
    """
    records = load_devices()
    if not records:
        raise NoDeviceRegisteredError(
            "No printer registered. Open the TUI (run with no arguments) and "
            "register one under Devices."
        )

    if address is None:
        found = await scan_registered(timeout=timeout)
        if found is None:
            names = ", ".join(r.name for r in records)
            raise PrinterNotFoundError(
                f"None of the registered printers are in range ({names}). "
                "Is one powered on?"
            )
    else:
        record = find_device(address, records)
        if record is None:
            raise NoDeviceRegisteredError(f"{address} is not a registered device.")
        matches = await scan_by_addresses([address], timeout=timeout, stop_on=address)
        if not matches:
            raise PrinterNotFoundError(
                f"{record.name} is not in range. Is it powered on?"
            )
        found = matches[0]

    record = find_device(found.address, records)
    if record is None:  # can't happen: we only ever scan registered addresses
        raise PrinterNotFoundError(f"{found.address} is not a registered device.")
    return found, record


async def print_text(
    text: str,
    *,
    address: str | None = None,
    config: PrintConfig | None = None,
    timeout: float = DEFAULT_SCAN_TIMEOUT_S,
    query_battery: bool = True,
) -> PrintResult:
    """Find the printer, render text with *its* config, print it, disconnect.

    `address` targets one registered device; None uses the highest-priority one
    that answers. `config` overrides the device's stored settings for this job.
    """
    found, record = await find_printer(address=address, timeout=timeout)
    return await print_to(found, record, text, config=config, query_battery=query_battery)


def render_for(record: DeviceRecord, text: str, config: PrintConfig | None = None):
    """Render text with a device's own settings. Returns (raster, settings)."""
    settings = config or record.print_config
    raster = render_text(
        text,
        font_size=settings.font_size,
        margin_x=settings.margin_x,
        margin_top=settings.margin_top,
        margin_bottom=settings.margin_bottom,
        line_spacing=settings.line_spacing,
        paper_width_mm=settings.paper_width_mm,
    )
    return raster, settings


async def send_job(
    transport,
    record: DeviceRecord,
    text: str,
    *,
    config: PrintConfig | None = None,
    query_battery: bool = True,
) -> PrintResult:
    """Run one print job over a connection someone else owns.

    Does not connect or disconnect — that belongs to the caller, which is what
    lets a held-open session reuse one connection across several jobs.
    """
    raster, settings = render_for(record, text, config)
    await print_raster(
        transport, raster, density=settings.density, feed_dots=settings.feed_dots
    )

    battery: int | None = None
    if query_battery:
        # Piggybacked on the open connection — the paper is already out.
        status = await query_status(
            transport, ("battery",), timeout=BATTERY_QUERY_TIMEOUT_S
        )
        value = status.get("battery")
        battery = int(value) if isinstance(value, int) else None

    return PrintResult(
        device=record,
        lines=len(text.splitlines()) or 1,
        battery=battery,
    )


async def print_to(
    found: FoundDevice,
    record: DeviceRecord,
    text: str,
    *,
    config: PrintConfig | None = None,
    query_battery: bool = True,
) -> PrintResult:
    """Connect to an already-located printer, print one job, disconnect.

    Split out from print_text so a UI can report "searching" and "printing" as
    separate phases instead of one opaque wait.
    """
    async with BLETransport(found.device or found.address) as transport:
        return await send_job(
            transport, record, text, config=config, query_battery=query_battery
        )
