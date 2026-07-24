"""Persistence for registered devices (TOFU: trust on first use).

A standalone concern, decoupled from transport/render/protocol. Stores the
devices the user has explicitly registered, so printing matches by identity
(address) rather than blindly trusting a name.

Print settings are **per device**, not global: each record carries its own
PrintConfig, so a 48mm belt printer and a narrower one can coexist.

Config format: TOML. Location, in order of preference:

    $REMIND_HOME/config.toml   explicit override
    <repo>/config.toml              a source checkout that already has one
    ~/.config/remind/config.toml

A packaged build skips the repo rule entirely: __file__ then points inside the
bundle rather than at a checkout, so "next to the code" is the wrong place for
state — and under a onefile packer that directory is deleted on exit, taking
every registration with it.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ENV_HOME = "REMIND_HOME"


def _is_frozen() -> bool:
    """True inside a packaged build, whichever packer made it.

    PyInstaller sets sys.frozen; Nuitka sets __compiled__ on each module.
    Either way __file__ points into the bundle, not into a checkout.
    """
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _resolve_config_path() -> Path:
    home = os.environ.get(ENV_HOME)
    if home:
        return Path(home).expanduser() / "config.toml"
    if not _is_frozen():
        # Source checkout: keep a dev tree and its config together.
        repo = Path(__file__).resolve().parent.parent / "config.toml"
        if repo.exists():
            return repo
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "remind" / "config.toml"


CONFIG_PATH = _resolve_config_path()


@dataclass
class PrintConfig:
    """How one device prints. Every field really drives the renderer."""

    paper_width_mm: int = 48  # M02 belt width; caps the usable print width
    font_size: int = 48  # px; ~12mm tall at 203 DPI
    margin_x: int = 12  # px, both sides
    margin_top: int = 12  # px
    margin_bottom: int = 12  # px
    line_spacing: int = 10  # px between baselines, on top of the font metrics
    density: int = 6  # 1-8, heat time (darker = slower, more battery)
    feed_dots: int = 8  # paper fed after the job

    @classmethod
    def from_dict(cls, data: dict) -> "PrintConfig":
        """Build from parsed TOML, ignoring unknown/invalid keys."""
        known = {f.name for f in fields(cls)}
        out = cls()
        for key, value in data.items():
            if key in known:
                try:
                    setattr(out, key, int(value))
                except (TypeError, ValueError):
                    pass
        return out

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class DeviceRecord:
    name: str
    address: str  # MAC on Linux, system UUID on macOS
    platform: str  # sys.platform at registration time (e.g. "darwin", "linux")
    order: int  # lower = higher priority when multiple are registered
    print_config: PrintConfig = field(default_factory=PrintConfig)


def load_devices() -> list[DeviceRecord]:
    """Return registered devices sorted by order (highest priority first)."""
    if not CONFIG_PATH.exists():
        return []
    with CONFIG_PATH.open("rb") as f:
        data = tomllib.load(f)
    records = [
        DeviceRecord(
            name=d["name"],
            address=d["address"],
            platform=d.get("platform", ""),
            order=int(d.get("order", 0)),
            print_config=PrintConfig.from_dict(d.get("print", {})),
        )
        for d in data.get("device", [])
    ]
    records.sort(key=lambda r: r.order)
    return records


def save_devices(records: list[DeviceRecord]) -> None:
    """Overwrite the config with the given records.

    Order is normalised to 0..n-1 on the way out, so the stored order always
    matches list position (and therefore the ids the CLI/TUI show).
    """
    ordered = sorted(records, key=lambda r: r.order)
    lines = ["# ReMind registered devices. Lower `order` = higher priority.\n"]
    for i, r in enumerate(ordered):
        r.order = i
        lines.append("\n[[device]]\n")
        lines.append(f'name = "{_esc(r.name)}"\n')
        lines.append(f'address = "{_esc(r.address)}"\n')
        lines.append(f'platform = "{_esc(r.platform)}"\n')
        lines.append(f"order = {r.order}\n")
        # Sub-table of the [[device]] element just above it.
        lines.append("\n[device.print]\n")
        for key, value in r.print_config.to_dict().items():
            lines.append(f"{key} = {value}\n")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("".join(lines), encoding="utf-8")


def find_device(address: str, records: list[DeviceRecord] | None = None) -> DeviceRecord | None:
    """Look up one registered device by address."""
    for r in records if records is not None else load_devices():
        if r.address == address:
            return r
    return None


def _esc(s: str) -> str:
    """Minimal TOML basic-string escaping for our known fields."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
