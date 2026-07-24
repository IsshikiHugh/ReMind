"""ReMind — notes/todo printer for Phomemo M02.

Four decoupled modules plus the two front ends:

    core       print_text(), discover(), register_device(), resolve_device()
    transport  scan(), scan_all(), scan_by_addresses(), BLETransport
    render     render_text(), preview_layout(), Raster   text -> 1-bit raster
    protocol   print_raster(), query_status()            M02 command sequence
    config     DeviceRecord, PrintConfig, load/save_devices  (print config is
               per device, never a global singleton)
    cli / tui  machine-facing and human-facing entry points
"""

from .config import (
    DeviceRecord,
    PrintConfig,
    find_device,
    load_devices,
    save_devices,
)
from .core import (
    NoDeviceRegisteredError,
    PrinterNotFoundError,
    PrintResult,
    discover,
    find_printer,
    move_device,
    print_text,
    print_to,
    register_device,
    render_for,
    send_job,
    resolve_device,
    scan_registered,
    unregister_device,
)
from .protocol import print_raster, query_status
from .session import PrinterSession
from .render import Raster, preview_layout, render_text
from .transport import BLETransport, FoundDevice, scan, scan_all, scan_by_addresses

__all__ = [
    # high-level
    "print_text",
    "print_to",
    "send_job",
    "render_for",
    "PrintResult",
    "PrinterSession",
    "find_printer",
    "PrinterNotFoundError",
    "NoDeviceRegisteredError",
    # registry
    "discover",
    "register_device",
    "unregister_device",
    "move_device",
    "resolve_device",
    "scan_registered",
    # config
    "DeviceRecord",
    "PrintConfig",
    "load_devices",
    "save_devices",
    "find_device",
    # transport
    "scan",
    "scan_all",
    "scan_by_addresses",
    "BLETransport",
    "FoundDevice",
    # render / protocol
    "render_text",
    "preview_layout",
    "Raster",
    "print_raster",
    "query_status",
]
