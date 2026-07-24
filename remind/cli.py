"""ReMind CLI — the machine-facing half.

No prompts, no interactive fallback: given arguments it prints and exits.
Registration, reordering and print config all live in the TUI, which is what
you get when you run with no arguments at all.

    remind                          # launch the TUI
    remind --text "buy milk"        # print to the top online printer
    remind --text - < notes.txt     # read the text from stdin
    remind --text "x" --device 2    # target one registered printer
    remind list device              # id<TAB>name<TAB>address

Data goes to stdout, human notes and errors to stderr, failure to exit code 1.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Optional

import typer

from . import core

cli = typer.Typer(add_completion=False, help="Print notes/todos to a Phomemo M02.")
_list = typer.Typer(help="List resources (machine-readable, TSV).")
cli.add_typer(_list, name="list")


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


@cli.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    text: Optional[str] = typer.Option(
        None, "--text", help="Text to print; '-' reads stdin. Omit to launch the TUI."
    ),
    device: Optional[str] = typer.Option(
        None, "--device", help="Target printer: id from `list device`, address, or name."
    ),
    timeout: float = typer.Option(
        core.DEFAULT_SCAN_TIMEOUT_S, "--timeout", help="Seconds to look for the printer."
    ),
) -> None:
    """No args → TUI. With --text → print, one shot, no interaction."""
    if ctx.invoked_subcommand is not None:
        return

    if text is None:
        from .tui import run  # imported lazily: the CLI path needs no TUI deps

        run()
        raise typer.Exit()

    if text == "-":
        text = sys.stdin.read()
    if not text.strip():
        _fail("nothing to print")

    address = None
    if device is not None:
        record = core.resolve_device(device)
        if record is None:
            _fail(f"no registered device matching {device!r}")
        address = record.address

    started = time.perf_counter()
    try:
        result = asyncio.run(core.print_text(text.rstrip("\n"), address=address, timeout=timeout))
    except core.PrinterNotFoundError as exc:
        _fail(f"{exc} (waited {time.perf_counter() - started:.1f}s)")
    except Exception as exc:  # BLE stack failures, disconnects mid-job
        _fail(f"print failed after {time.perf_counter() - started:.1f}s: {exc}")

    typer.echo(
        f"printed {result.lines} line(s) to {result.device.name} "
        f"({result.device.address}) in {time.perf_counter() - started:.1f}s",
        err=True,
    )


@_list.command("device")
def list_device() -> None:
    """Registered printers as: id<TAB>name<TAB>address."""
    for i, record in enumerate(core.load_devices(), start=1):
        typer.echo(f"{i}\t{record.name}\t{record.address}")
