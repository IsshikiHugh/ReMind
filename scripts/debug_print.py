"""Debug / bench harness: print text and time each phase.

Not the production path — that's `remind.print_text()`. This script
deliberately drives the three layers directly so it can time scan / connect /
send / disconnect separately, which is how we diagnose BLE slowness.

Usage:
    .venv/bin/python scripts/debug_print.py [text] [font_size]
"""

import asyncio
import sys
import time
from pathlib import Path

# Allow running as a plain script from anywhere: put repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remind import protocol, render  # noqa: E402
from remind.transport import BLETransport, scan  # noqa: E402


async def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    font_size = int(sys.argv[2]) if len(sys.argv) > 2 else 48

    t0 = time.perf_counter()
    print("Scanning for M02 ...")
    dev = await scan(name_prefix="M02", timeout=8.0)
    t_scan = time.perf_counter()
    if dev is None:
        print("  No M02 found. Is the printer powered on and in range?")
        return 1
    print(f"  Found: {dev.name}  ({dev.address})   [{t_scan - t0:.1f}s]")

    raster = render.render_text(text, font_size=font_size)
    print(f"Rendered: {raster.width_bytes}x{raster.height_lines} "
          f"({len(raster.data)} bytes)")

    print("Connecting ...")
    async with BLETransport(dev.device or dev.address) as tp:
        t_conn = time.perf_counter()
        print(f"  Connected   [{t_conn - t_scan:.1f}s]")
        print("Printing ...")
        await protocol.print_raster(tp, raster)
        t_print = time.perf_counter()
        print(f"  Sent        [{t_print - t_conn:.1f}s]")

    t_end = time.perf_counter()
    print(f"Done. Total {t_end - t0:.1f}s "
          f"(scan {t_scan - t0:.1f} / connect {t_conn - t_scan:.1f} / "
          f"print {t_print - t_conn:.1f} / disconnect {t_end - t_print:.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
