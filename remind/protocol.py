"""M02 print protocol — command sequence to print a raster.

Pure protocol layer: given a transport (anything with async send / send_chunked)
and a raster, run the M02 print sequence. No BLE specifics, no rendering.
Sequence adapted from phomymo's printer.js `printM02` (see Acknowledgments).
"""

from __future__ import annotations

import asyncio

# Density 1-8 -> ESC 7 heat time (higher = darker). Index by density-1.
_HEAT_TIMES = [40, 60, 80, 100, 120, 140, 160, 200]

PREFIX = bytes([0x10, 0xFF, 0xFE, 0x01])  # M02-specific prefix
INIT = bytes([0x1B, 0x40])                # ESC @

# Status queries: write 1F 11 XX, the printer answers on the notify channel
# with 1A <type> <value...>. (phomymo src/web/ble.js)
QUERY_COMMANDS = {
    "battery": bytes([0x1F, 0x11, 0x08]),
    "paper": bytes([0x1F, 0x11, 0x11]),
    "firmware": bytes([0x1F, 0x11, 0x07]),
    "serial": bytes([0x1F, 0x11, 0x09]),
}

# Low-battery replies are coded rather than a plain percentage.
_BATTERY_CODES = {0xA4: 0, 0xA3: 3, 0xA2: 5, 0xA1: 10}


def _heat(heat_time: int) -> bytes:
    return bytes([0x1B, 0x37, 0x07, heat_time, 0x02])


def _raster_header(width_bytes: int, height_lines: int) -> bytes:
    return bytes(
        [0x1D, 0x76, 0x30, 0x00,
         width_bytes, 0x00,
         height_lines & 0xFF, (height_lines >> 8) & 0xFF]
    )


def _feed(dots: int) -> bytes:
    return bytes([0x1B, 0x4A, dots & 0xFF])


async def print_raster(transport, raster, density: int = 6, feed_dots: int = 8) -> None:
    """Send the full M02 print sequence for one raster."""
    heat_time = _HEAT_TIMES[max(0, min(7, density - 1))]

    await transport.send(PREFIX)
    await asyncio.sleep(0.05)
    await transport.send(INIT)
    await asyncio.sleep(0.1)
    await transport.send(_heat(heat_time))
    await asyncio.sleep(0.03)
    await transport.send(_raster_header(raster.width_bytes, raster.height_lines))
    await transport.send_chunked(raster.data)
    await asyncio.sleep(0.3)
    await transport.send(_feed(feed_dots))
    await asyncio.sleep(0.5)


async def query_status(
    transport, fields: tuple[str, ...] = ("battery",), timeout: float = 1.5
) -> dict:
    """Ask the printer about itself. Best effort — missing keys mean no reply.

    Only useful while connected, so callers piggyback it on a print job rather
    than paying for a connection just to read the battery.
    """
    wanted = [f for f in fields if f in QUERY_COMMANDS]
    if not wanted:
        return {}

    result: dict = {}
    done = asyncio.Event()

    def on_notify(_sender, data: bytearray) -> None:
        parsed = _parse_status(bytes(data))
        if parsed is None:
            return
        key, value = parsed
        result[key] = value
        if all(f in result for f in wanted):
            done.set()

    if not await transport.start_notify(on_notify):
        return {}
    try:
        for field in wanted:
            await transport.send(QUERY_COMMANDS[field])
            await asyncio.sleep(0.1)
        await asyncio.wait_for(done.wait(), timeout)
    except asyncio.TimeoutError:
        pass  # partial answers are still worth keeping
    finally:
        await transport.stop_notify()
    return result


def _parse_status(data: bytes) -> tuple[str, object] | None:
    """Decode one 1A <type> <value...> reply. None if it isn't one."""
    if len(data) < 3 or data[0] != 0x1A:
        return None
    kind, value = data[1], data[2]
    if kind == 0x04:
        return "battery", _BATTERY_CODES.get(value, value)
    if kind == 0x06:
        return "paper", "out" if value == 0x88 else "ok"
    if kind == 0x07:
        return "firmware", ".".join(str(b) for b in data[2:])
    if kind == 0x08:
        return "serial", data[2:].decode("ascii", "replace")
    return None
