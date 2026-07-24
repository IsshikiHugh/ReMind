"""ReMind TUI — the human-facing half.

One page, three centered columns: menu | paper preview | context panel.
Everything is edited IN PLACE — no modal screens, and no leaving the app: the
note is typed inside the Content box at the paper's own width, the context
panel edits the active printer's config with a live re-render, and registered
devices are managed in the same panel.

Everything here is real: devices come from config.toml, scanning is real BLE,
and Print sends to the printer. Print settings are per device, so the preview
always shows the settings of the printer that is about to be used.

The terminal can't scale glyphs, so a larger font scales the *canvas* instead:
fewer cells per line ⇒ a narrower paper strip. Needs ~98 columns.
"""

from __future__ import annotations

import asyncio
import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    ContentSwitcher,
    DataTable,
    Header,
    Input,
    Static,
    TextArea,
)

from . import core
from .config import PrintConfig, load_devices, save_devices
from .render import preview_geometry, preview_layout
from .session import PrinterSession
from .transport import FoundDevice

MIN_WIDTH = 98
SCAN_SECONDS = 10.0
PRINT_SCAN_SECONDS = 10.0
QUIT_DISCONNECT_S = 1.0  # how long Quit will wait for a tidy disconnect

MENU = ["Edit", "Print", "Config", "Devices", "Quit"]

# Editable print settings, in display order. (label, PrintConfig field, lo, hi)
# `None` field = read-only row (which printer this config belongs to).
CONFIG_ROWS: list[tuple[str, str | None, int, int]] = [
    ("Printer", None, 0, 0),
    ("Paper width (mm)", "paper_width_mm", 10, 48),
    ("Font size (px)", "font_size", 8, 200),
    ("Margin ↔ (px)", "margin_x", 0, 150),
    ("Margin top (px)", "margin_top", 0, 400),
    ("Margin bottom (px)", "margin_bottom", 0, 400),
    ("Density (1-8)", "density", 1, 8),
]

# Shown in the preview when nothing is registered yet.
_UNREGISTERED_DEFAULTS = PrintConfig()


class ConfigInput(Input):
    """Integer field that still honours the TUI's h/l keys.

    Input swallows every printable key before bindings get a look, so without
    this h and l would silently vanish while editing instead of meaning
    back/confirm. Safe here because the field only ever accepts digits.
    """

    async def _on_key(self, event) -> None:
        if event.character == "h":
            event.stop()
            event.prevent_default()
            self.screen.action_back()
        elif event.character == "l":
            event.stop()
            event.prevent_default()
            self.screen.action_confirm()
        else:
            await super()._on_key(event)


class NoteArea(TextArea):
    """The note, typed straight into the Content box.

    The opposite call to ConfigInput's: here every printable key really is
    text — h, l, j, k, digits, q — so TextArea swallowing them before the
    screen's bindings is exactly right. That leaves escape as the way out,
    which tab_behavior="focus" (the default) leaves untouched.
    """

    BINDINGS = [Binding("escape", "done", "Done", show=False)]

    def action_done(self) -> None:
        self.screen.finish_edit()


# Battery is only knowable while connected, so a disconnected printer reads
# "??" rather than a stale number from the last time we spoke to it.
def _battery_text(pct: int | None, ok: str, warn: str, bad: str) -> Text:
    if pct is None:
        return Text("??", style="dim")
    color = ok if pct >= 50 else warn if pct >= 20 else bad
    return Text(f"{pct}%", style=f"bold {color}")


def _battery_markup(pct: int | None) -> str:
    if pct is None:
        return "[dim]🔋 ??[/]"
    tag = "$success" if pct >= 50 else "$warning" if pct >= 20 else "$error"
    return f"[{tag}]🔋 {pct}%[/]"


def _mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _signal(rssi: int | None, ok: str, warn: str, bad: str) -> Text:
    if rssi is None:
        return Text("····", style="dim")
    level = 4 if rssi >= -55 else 3 if rssi >= -67 else 2 if rssi >= -78 else 1
    bars = "".join("▁▃▅▇"[i] if i < level else "·" for i in range(4))
    color = ok if level >= 3 else warn if level == 2 else bad
    return Text(bars, style=color)


class HomeScreen(Screen):
    BINDINGS = [
        Binding("j", "nav(1)", "Down", show=False),
        Binding("k", "nav(-1)", "Up", show=False),
        *[Binding(str(i), f"jump({i - 1})", show=False) for i in range(1, 10)],
        # vim-style pair: h goes back like escape, l confirms like enter.
        Binding("escape", "back", "Back", show=False),
        Binding("h", "back", "Back", show=False),
        Binding("l", "confirm", "Confirm", show=False),
        Binding("d", "dev_delete", "Delete", show=False),
        Binding("K", "dev_move(-1)", "Move up", show=False),
        Binding("J", "dev_move(1)", "Move down", show=False),
        Binding("r", "scan_refresh", "Refresh", show=False),
    ]

    content = ""
    _editing_key: str | None = None
    _editing_row: int = -1
    _editing_orig: int = 0

    def __init__(self) -> None:
        super().__init__()
        self.devices = load_devices()
        self._found: list[FoundDevice] = []
        self._scan_worker = None
        self._printing = False
        self._progress_timer = None
        self._phase = ""
        self._phase_t0 = 0.0
        # One warm connection, reused across edits and prints, dropped when idle.
        self.session = PrinterSession()
        self._busy = False  # a BLE worker owns the connection right now
        self._editing = False  # the Content box is in edit mode

    # -- layout --

    def compose(self) -> ComposeResult:
        # (No clock — it was dropped when Edit still suspended the app for an
        # external editor, where a once-a-second repaint deadlocked the driver.
        # Nothing suspends any more, so it could come back safely.)
        yield Header()
        yield Static(f"Terminal too narrow — widen to ≥ {MIN_WIDTH} columns.", id="toonarrow")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield DataTable(id="menu", cursor_type="row", show_header=False)
            with Vertical(id="middle"):
                yield Static(id="preview")
                yield NoteArea(id="note", classes="hidden", tab_behavior="focus")
            with Vertical(id="right"):
                yield Static(id="printer")
                with ContentSwitcher(id="ctx", initial="config-pane"):
                    with Vertical(id="config-pane"):
                        yield DataTable(id="cfg", cursor_type="row", show_header=False)
                        yield ConfigInput(id="editor", classes="hidden", type="integer")
                    with Vertical(id="devices-pane"):
                        yield DataTable(id="devtbl", cursor_type="row")
                    with Vertical(id="scan-pane"):
                        yield Static("", id="scanstatus")
                        yield DataTable(id="scantbl", cursor_type="row")

    def on_mount(self) -> None:
        self.app.sub_title = "home"
        menu = self.query_one("#menu", DataTable)
        menu.add_column("item")
        for _ in MENU:
            menu.add_row(" ")
        menu.border_title = "Menu"
        menu.border_subtitle = "q quit"

        cfg = self.query_one("#cfg", DataTable)
        cfg.add_column("Setting", width=20)
        cfg.add_column("Value")
        cfg.border_title = "Print config"
        cfg.border_subtitle = "enter edit"

        self.query_one("#printer").border_title = "Printer"
        self.query_one("#preview", Static).border_title = "Content"

        note = self.query_one("#note", NoteArea)
        note.border_title = "Content"
        note.border_subtitle = "esc done"

        self._reload_config()
        self._refresh_printer()
        self._refresh_preview()
        menu.focus()
        self._update_pointers()
        self._check_width()
        self.set_interval(1.0, self._tick_session)

    def on_resize(self, event) -> None:
        self._check_width()

    def _check_width(self) -> None:
        narrow = self.size.width < MIN_WIDTH
        self.query_one("#body").display = not narrow
        self.query_one("#toonarrow").display = narrow

    # -- the device this page is about --

    def _active(self):
        """The printer this page is about, and whose config it edits.

        While connected that is the printer we are actually talking to — which
        is not always the top of the list, since a job falls back to whichever
        registered device answers. Showing #1's settings while printing with
        #2's would make the preview quietly lie.
        """
        held = self.session.device
        if held is not None:
            for device in self.devices:
                if device.address == held.address:
                    return device
        return self.devices[0] if self.devices else None

    def _config(self) -> PrintConfig:
        device = self._active()
        return device.print_config if device else _UNREGISTERED_DEFAULTS

    def _persist(self) -> None:
        save_devices(self.devices)

    # -- pointer folded into the first column; only the focused table shows it --

    def _first_cell(self, tid: str, r: int) -> Text:
        if tid == "menu":
            label = MENU[r]
            return Text.assemble((label[0], "bold"), (label[1:], ""))  # accelerator letter
        if tid == "cfg":
            return Text(CONFIG_ROWS[r][0])
        if tid == "devtbl":
            return Text(str(r + 1), style="dim") if r < len(self.devices) else Text("+")
        if tid == "scantbl":
            return Text(str(r + 1), style="dim")
        return Text("")

    def _point(self, table: DataTable, row: int | None = None) -> None:
        if row is None:
            row = table.cursor_row
        accent = self.app.current_theme.accent
        for r in range(table.row_count):
            head = ("▶ ", f"bold {accent}") if r == row else ("  ", "")
            table.update_cell_at(Coordinate(r, 0), Text.assemble(head, self._first_cell(table.id, r)))

    def _active_table(self) -> DataTable | None:
        w = self.focused
        if w is not None and getattr(w, "id", None) == "editor":
            return self.query_one("#cfg", DataTable)
        return w if isinstance(w, DataTable) else None

    def _update_pointers(self) -> None:
        active = self._active_table()
        for tid in ("menu", "cfg", "devtbl", "scantbl"):
            table = self.query_one(f"#{tid}", DataTable)
            self._point(table, table.cursor_row if table is active else -1)

    def on_descendant_focus(self, event) -> None:
        self.call_after_refresh(self._update_pointers)

    def on_descendant_blur(self, event) -> None:
        self.call_after_refresh(self._update_pointers)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table is self._active_table():
            self._point(event.data_table, event.cursor_row)

    def action_nav(self, delta: int) -> None:
        w = self.focused
        if isinstance(w, DataTable) and w.row_count:
            w.move_cursor(row=max(0, min(w.cursor_row + delta, w.row_count - 1)))

    def action_jump(self, index: int) -> None:
        w = self.focused
        if isinstance(w, DataTable) and index < w.row_count:
            w.move_cursor(row=index)

    def _fid(self) -> str | None:
        return self.focused.id if self.focused else None

    # -- enter routing --

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._activate(event.data_table.id)

    def _activate(self, tid: str | None) -> None:
        """What enter (and l) does, per pane."""
        if tid == "menu":
            self._menu_select()
        elif tid == "cfg":
            self._config_edit()
        elif tid == "devtbl":
            if self.query_one("#devtbl", DataTable).cursor_row == len(self.devices):
                self._show_scan()  # the "+" row
        elif tid == "scantbl":
            self._scan_register()

    def action_confirm(self) -> None:
        if self._editing_key is not None:
            self._persist()  # same as enter: commit the value
            self._end_config_edit()
            return
        self._activate(self._fid())

    def _menu_select(self) -> None:
        label = MENU[self.query_one("#menu", DataTable).cursor_row]
        if label == "Edit":
            self._edit()
        elif label == "Print":
            self._print()
        elif label == "Config":
            self.query_one("#ctx", ContentSwitcher).current = "config-pane"
            self.query_one("#cfg", DataTable).focus()
        elif label == "Devices":
            self._show_devices()
        elif label == "Quit":
            self._quit()

    @work(group="quit")
    async def _quit(self) -> None:
        # Best effort only: quitting must never wait on Bluetooth. The OS drops
        # the link when the process dies anyway, so a slow disconnect is not a
        # reason to keep the user staring at a frozen screen.
        try:
            await asyncio.wait_for(self.session.close(), QUIT_DISCONNECT_S)
        except Exception:
            pass
        self.app.exit()

    def on_key(self, event) -> None:
        # First-letter accelerators, only while the menu is focused.
        # First press selects the row; pressing it again (row already selected)
        # activates it — same as pressing enter on it.
        if self._fid() != "menu":
            return
        menu = self.query_one("#menu", DataTable)
        for i, label in enumerate(MENU):
            if event.key == label[0].lower():
                if menu.cursor_row == i:
                    self._menu_select()
                else:
                    menu.move_cursor(row=i)
                event.stop()
                return

    def action_back(self) -> None:
        if self._editing_key is not None:
            self._cancel_config_edit()
            return
        fid = self._fid()
        if fid == "scantbl":
            self._stop_scan()
            self._show_devices()
        elif fid in ("cfg", "devtbl"):
            self.query_one("#ctx", ContentSwitcher).current = "config-pane"
            self.query_one("#menu", DataTable).focus()

    # -- panels --

    def _refresh_printer(self) -> None:
        # Background workers outlive the screen on quit; touching widgets then
        # raises NoMatches and takes the app down with it. (is_mounted is not
        # the guard to use here — it is still False during on_mount.)
        try:
            panel = self.query_one("#printer", Static)
        except NoMatches:
            return
        device = self._active()
        if device is None:
            panel.update("[$warning]no printer registered[/]")
            return
        if self.session.connected:
            # Live: real battery, and how long the connection has left.
            tail = f"  [$success]●[/] [dim]{_mmss(self.session.seconds_left)}[/]"
            battery = _battery_markup(self.session.battery)
        else:
            tail = "  [dim]○ offline[/]"
            battery = _battery_markup(None)
        panel.update(f"🖨 [b]{device.name}[/]  {battery}{tail}")

    def _tick_session(self) -> None:
        """Run the idle countdown down and let go when it hits zero."""
        if self._busy or self._progress_timer is not None:
            return  # a job is running; the progress readout owns the panel
        if self.session.expired():
            self._expire_session()
        elif self.session.connected:
            self._refresh_printer()  # just redraw the countdown

    @work(group="session")
    async def _expire_session(self) -> None:
        await self.session.close()
        self._refresh_printer()
        self._reload_devices_if_visible()
        self.notify("Disconnected after 5 min idle.", severity="information")

    @work(group="session")
    async def _drop_session(self) -> None:
        """Let go now — e.g. the device this session belongs to is gone."""
        await self.session.close()
        self._refresh_printer()
        self._reload_devices_if_visible()

    def _resync_session(self) -> None:
        """Close the session if the device it belongs to is no longer registered."""
        held = self.session.device
        if held is None:
            return
        if not any(device.address == held.address for device in self.devices):
            self._drop_session()

    def _reload_devices_if_visible(self) -> None:
        try:
            switcher = self.query_one("#ctx", ContentSwitcher)
        except NoMatches:
            return
        if switcher.current == "devices-pane":
            self._reload_devices()

    def _refresh_preview(self) -> None:
        preview = self.query_one("#preview", Static)
        cfg = self._config()
        preview.border_subtitle = f"{cfg.paper_width_mm}mm · {cfg.font_size}px"
        if not self.content.strip():
            preview.styles.width = 14
            preview.update(Text("(empty)", style="italic #888888"))
            return
        rows, box = preview_layout(
            self.content,
            font_size=cfg.font_size,
            margin_x=cfg.margin_x,
            margin_top=cfg.margin_top,
            margin_bottom=cfg.margin_bottom,
            paper_width_mm=cfg.paper_width_mm,
        )
        preview.styles.width = box + 2  # border only (no padding)
        preview.update("\n".join(rows))

    def _reload_config(self) -> None:
        cfg = self.query_one("#cfg", DataTable)
        keep = cfg.cursor_row
        settings = self._config()
        device = self._active()
        cfg.clear()
        for _label, key, _lo, _hi in CONFIG_ROWS:
            if key is None:
                val = Text(device.name if device else "—", style="dim")
            else:
                val = Text(str(getattr(settings, key)))
            cfg.add_row(" ", val)
        cfg.move_cursor(row=max(0, min(keep, len(CONFIG_ROWS) - 1)))
        self._update_pointers()

    # -- config: in-place editing with live preview --

    def _config_edit(self) -> None:
        row = self.query_one("#cfg", DataTable).cursor_row
        label, key, _lo, _hi = CONFIG_ROWS[row]
        if key is None:
            self.notify("The config belongs to the top printer — reorder in Devices.")
            return
        if self._active() is None:
            self.notify("Register a printer first (Devices → +).", severity="warning")
            return
        self._editing_key = key
        self._editing_row = row
        self._editing_orig = getattr(self._config(), key)
        editor = self.query_one("#editor", Input)
        editor.border_title = label
        editor.value = str(self._editing_orig)
        editor.remove_class("hidden")
        editor.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._editing_key is None or not event.value:
            return
        try:
            value = int(event.value)
        except ValueError:
            return
        _label, _key, lo, hi = CONFIG_ROWS[self._editing_row]
        if not lo <= value <= hi:
            return
        setattr(self._config(), self._editing_key, value)
        self.query_one("#cfg", DataTable).update_cell_at(
            Coordinate(self._editing_row, 1), str(value)
        )
        self._refresh_preview()  # live!

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._persist()  # only touch the file once the value is committed
        self._end_config_edit()

    def _end_config_edit(self) -> None:
        self._editing_key = None
        self.query_one("#editor", Input).add_class("hidden")
        self.query_one("#cfg", DataTable).focus()

    def _cancel_config_edit(self) -> None:
        if self._editing_key is not None:
            setattr(self._config(), self._editing_key, self._editing_orig)
            self._reload_config()
            self._refresh_preview()
        self._end_config_edit()

    # -- devices: in-place --

    def _show_devices(self) -> None:
        self.query_one("#ctx", ContentSwitcher).current = "devices-pane"
        self._reload_devices()
        self.query_one("#devtbl", DataTable).focus()

    def _palette(self) -> tuple[str, str, str, str]:
        th = self.app.current_theme
        return (th.success or "green", th.warning or "yellow", th.error or "red", th.accent or "cyan")

    def _reload_devices(self, cursor: int | None = None) -> None:
        table = self.query_one("#devtbl", DataTable)
        if not table.columns:
            table.add_column("#", width=4)
            table.add_column("Name")
            table.add_column("Battery", width=8)
            table.border_title = "Registered printers"
            table.border_subtitle = "shift reorder · d del"
        keep = table.cursor_row if cursor is None else cursor
        table.clear()
        ok, warn, bad, _ = self._palette()
        held = self.session.device
        for device in self.devices:
            # Only the printer we are actually talking to can report a level.
            connected = held is not None and held.address == device.address
            level = self.session.battery if connected else None
            table.add_row(" ", device.name, _battery_text(level, ok, warn, bad))
        table.add_row(" ", Text("add device…", style="dim italic"), "")  # the "+" row
        table.move_cursor(row=max(0, min(keep, table.row_count - 1)))
        self._update_pointers()

    def action_dev_delete(self) -> None:
        if self._fid() != "devtbl":
            return
        index = self.query_one("#devtbl", DataTable).cursor_row
        if index >= len(self.devices):  # the "+" row
            return
        removed = core.unregister_device(self.devices[index].address)
        self.devices = load_devices()
        self._resync_session()
        self._reload_devices()
        self._reload_config()
        self._refresh_printer()
        self._refresh_preview()
        if removed is not None:
            self.notify(f"Removed {removed.name}", severity="warning")

    def action_dev_move(self, delta: int) -> None:
        if self._fid() != "devtbl":
            return
        index = self.query_one("#devtbl", DataTable).cursor_row
        if index >= len(self.devices):
            return
        if not core.move_device(self.devices[index].address, delta):
            return
        self.devices = load_devices()
        self._resync_session()  # the top device may have changed under us
        self._reload_devices(cursor=index + delta)
        self._reload_config()
        self._refresh_printer()
        self._refresh_preview()

    # -- scan: in-place, real BLE --

    def _show_scan(self) -> None:
        self.query_one("#ctx", ContentSwitcher).current = "scan-pane"
        table = self.query_one("#scantbl", DataTable)
        if not table.columns:
            table.add_column("#", width=4)
            table.add_column("Name")
            table.add_column("Signal", width=7)
            table.border_title = "Nearby devices"
            table.border_subtitle = "enter register · r refresh"
        table.focus()
        self._start_scan()

    def _start_scan(self) -> None:
        self._found = []
        self.query_one("#scantbl", DataTable).clear()
        self.query_one("#scanstatus", Static).update("[$accent]◐[/] scanning…")
        self._scan_worker = self._run_scan()

    @work(exclusive=True, group="scan")
    async def _run_scan(self) -> None:
        """Stream discoveries into the table as the BLE scan finds them."""
        queue: asyncio.Queue[FoundDevice] = asyncio.Queue()
        scan = asyncio.create_task(
            core.discover(timeout=SCAN_SECONDS, on_found=queue.put_nowait)
        )
        try:
            while not scan.done() or not queue.empty():
                try:
                    device = await asyncio.wait_for(queue.get(), 0.2)
                except asyncio.TimeoutError:
                    continue
                self._add_scan_row(device)
            await scan
        except asyncio.CancelledError:
            scan.cancel()
            raise
        except Exception as exc:  # BLE off, no adapter, permissions…
            self.query_one("#scanstatus", Static).update(f"[$error]✗[/] {exc}")
            return
        found = len(self._found)
        mark = "[$success]✓[/]" if found else "[$warning]·[/]"
        self.query_one("#scanstatus", Static).update(f"{mark} found {found}")

    def _add_scan_row(self, device: FoundDevice) -> None:
        self._found.append(device)
        registered = any(d.address == device.address for d in self.devices)
        name = Text(device.name or "(unnamed)")
        if registered:
            name.append(" ✓")
            name.stylize("dim")
        ok, warn, bad, _ = self._palette()
        table = self.query_one("#scantbl", DataTable)
        table.add_row(" ", name, _signal(device.rssi, ok, warn, bad))
        if len(self._found) == 1:
            table.move_cursor(row=0)
        self._update_pointers()
        self.query_one("#scanstatus", Static).update(
            f"[$accent]◐[/] scanning… {len(self._found)} found"
        )

    def _stop_scan(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel()
            self._scan_worker = None

    def action_scan_refresh(self) -> None:
        if self._fid() == "scantbl":
            self._stop_scan()
            self._start_scan()

    def _scan_register(self) -> None:
        if not self._found:
            return
        row = self.query_one("#scantbl", DataTable).cursor_row
        if row >= len(self._found):
            return
        device = self._found[row]
        if any(d.address == device.address for d in self.devices):
            self.notify(f"{device.name} is already registered.", severity="warning")
            return
        record = core.register_device(device)
        self.devices = load_devices()
        self._stop_scan()
        self._show_devices()
        self._reload_config()
        self._refresh_printer()
        self._refresh_preview()
        self.notify(f"Registered {record.name} (#{record.order + 1})")

    # -- edit / print --

    def _paper_geometry(self) -> tuple[int, int, int, int]:
        """(box, pad_cols, top, bottom) cells for the empty paper strip.

        _refresh_preview widens the box past this when a wrapped line needs the
        room; the editor wants the honest paper width so that what wraps while
        you type wraps the same way on paper. The margins come along too, so the
        editor reserves the same left/right indent and top/bottom blank rows the
        preview draws — otherwise the note reflows the instant you press escape.
        """
        cfg = self._config()
        return preview_geometry(
            font_size=cfg.font_size,
            margin_x=cfg.margin_x,
            margin_top=cfg.margin_top,
            margin_bottom=cfg.margin_bottom,
            paper_width_mm=cfg.paper_width_mm,
        )

    def _edit(self) -> None:
        """Turn the Content box into the note itself.

        Never gated on the printer: writing a note has nothing to do with
        Bluetooth, so the connection is warmed alongside the editing rather
        than in front of it, and a printer that is off costs nothing.
        """
        if self._editing:
            return
        if self.devices and not self.session.connected and not self._busy:
            self._warm_up()
        note = self.query_one("#note", NoteArea)
        box, pad_cols, top, bottom = self._paper_geometry()
        # border-box width, so the border (2) and the margin padding live inside
        # box + 2 — the same outer width the preview uses. The padding is the
        # margin: it indents the text and shrinks the wrap width to the content
        # column, so typing wraps and sits exactly where the preview will draw.
        note.styles.width = box + 2
        note.styles.padding = (top, pad_cols, bottom, pad_cols)
        note.text = self.content
        note.move_cursor(note.document.end)
        self.query_one("#preview", Static).add_class("hidden")
        note.remove_class("hidden")
        note.focus()
        self._editing = True

    def finish_edit(self) -> None:
        """Escape: keep what was typed and hand the box back to the preview."""
        if not self._editing:
            return
        note = self.query_one("#note", NoteArea)
        self.content = note.text.rstrip("\n")
        self._editing = False
        note.add_class("hidden")
        self.query_one("#preview", Static).remove_class("hidden")
        self._refresh_preview()
        self.session.touch()  # editing counts as activity
        self._refresh_printer()
        self.query_one("#menu", DataTable).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Typing is activity too, or a long note would expire the connection
        # it just opened.
        self.session.touch()

    @work(group="warm")
    async def _warm_up(self) -> None:
        """Speculative connect. Stays quiet — Print reports properly if it matters."""
        self._busy = True
        try:
            await self.session.ensure(timeout=PRINT_SCAN_SECONDS)
        except Exception:
            pass
        finally:
            self._busy = False
        self._refresh_printer()

    def _print(self) -> None:
        if not self.devices:
            self.notify("No printer registered. Choose 'Devices'.", severity="warning")
            return
        if not self.content.strip():
            self.notify("Nothing to print.", severity="warning")
            return
        if self._printing:
            self.notify("Already printing…", severity="warning")
            return
        self._do_print(self.content)

    # -- live "how long have I been waiting" readout, shown in the Printer box --

    def _progress_start(self, phase: str) -> None:
        self._progress_stop_timer()  # phase change: restart the clock, one timer
        self._phase = phase
        self._phase_t0 = time.perf_counter()
        self._progress_timer = self.set_interval(0.1, self._progress_tick)
        self._progress_tick()

    def _progress_tick(self) -> None:
        try:
            panel = self.query_one("#printer", Static)
        except NoMatches:
            return
        waited = time.perf_counter() - self._phase_t0
        budget = f" / {PRINT_SCAN_SECONDS:.0f}s" if self._phase == "searching" else ""
        panel.update(f"[$accent]◐[/] {self._phase}… [b]{waited:.1f}s[/]{budget}")

    def _progress_stop_timer(self) -> None:
        if self._progress_timer is not None:
            self._progress_timer.stop()
            self._progress_timer = None

    def _progress_stop(self) -> None:
        self._progress_stop_timer()
        self._refresh_printer()

    @work(exclusive=True, group="print")
    async def _do_print(self, text: str) -> None:
        self._printing = True
        self._busy = True
        started = time.perf_counter()
        warm = self.session.connected
        try:
            if not warm:
                self.notify(f"Looking for the printer… (up to {PRINT_SCAN_SECONDS:.0f}s)")
                self._progress_start("searching")
                await self.session.ensure(timeout=PRINT_SCAN_SECONDS)
            self._progress_start("printing")
            result = await self.session.print(text)
        except core.PrinterNotFoundError as exc:
            waited = time.perf_counter() - started
            self.notify(f"{exc} (waited {waited:.1f}s)", severity="error", timeout=8)
            return
        except Exception as exc:
            waited = time.perf_counter() - started
            self.notify(f"Print failed after {waited:.1f}s: {exc}", severity="error", timeout=8)
            return
        finally:
            self._printing = False
            self._busy = False
            self._progress_stop()
        self.devices = load_devices()
        self._reload_devices_if_visible()
        self._refresh_printer()
        elapsed = time.perf_counter() - started
        how = "warm" if warm else "cold"
        self.notify(
            f"✓ Printed {result.lines} line(s) to {result.device.name} "
            f"in {elapsed:.1f}s ({how})"
        )


class ReMindTUI(App):
    TITLE = "ReMind"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #toonarrow { display: none; width: 1fr; height: 1fr; content-align: center middle; color: $warning; }

    Screen { align: center top; }
    #body { width: 98; height: 1fr; padding: 1 0; }

    #left { width: 26; margin-right: 2; }
    #menu {
        height: auto;
        border: round $primary;
        border-title-align: left;
        border-subtitle-align: right;
        padding: 1 1;
    }

    #middle { width: 32; align: center top; }
    #preview {
        width: 24;
        height: auto;
        max-height: 100%;
        border: panel $accent;
        border-title-align: center;
        border-subtitle-align: center;
        padding: 0;
        background: #f6f5ef;
        color: #1c1c1c;
    }
    /* The editor stands in for the preview, in the same box at the same
       width, so typing looks like writing on the paper. */
    #note {
        width: 24;
        height: auto;
        min-height: 6;
        max-height: 100%;
        border: panel $accent;
        border-title-align: center;
        border-subtitle-align: center;
        padding: 0;
        background: #f6f5ef;
        color: #1c1c1c;
    }
    #note:focus { border: panel $accent; }
    #note .text-area--cursor-line { background: #eae7da; }
    #note .text-area--cursor { color: #f6f5ef; background: #1c1c1c; }
    #note .text-area--selection { color: #f6f5ef; background: #4c6a92; }
    #preview.hidden, #note.hidden { display: none; }

    #right { width: 36; margin-left: 2; }
    #printer {
        height: 3;
        border: round $primary;
        border-title-align: left;
        content-align: left middle;
        padding: 0 2;
        margin-bottom: 1;
    }
    ContentSwitcher, #config-pane, #devices-pane, #scan-pane { height: auto; }
    #editor {
        height: 3;
        border: round $accent;
        border-title-align: left;
        margin-top: 1;
    }
    #editor.hidden { display: none; }
    #scanstatus { height: 1; padding: 0 1; margin-bottom: 1; color: $text-muted; }

    DataTable {
        height: auto;
        border: round $primary;
        border-title-align: left;
        border-subtitle-align: right;
        padding: 0 1;
        scrollbar-size-horizontal: 0;
    }
    DataTable:focus { border: round $accent; }
    #devtbl, #scantbl { min-height: 12; }   /* reserve room for ≥ 8 devices */
    DataTable > .datatable--header { text-style: bold; color: $accent; }
    DataTable > .datatable--cursor { background: transparent; }
    DataTable:focus > .datatable--cursor {
        background: $accent 15%;
        color: $text;
        text-style: bold;
    }
    """

    def on_mount(self) -> None:
        self.theme = "nord"
        self.push_screen(HomeScreen())


def run() -> None:
    ReMindTUI().run()
