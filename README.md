# ReMind

*receipt + mind* — notes and todos, printed on a Phomemo M02.

Two front ends over one library: a **TUI** for humans (register printers, edit
the note, tune the layout, print) and a **CLI** for machines (one command, no
prompts).

## Install

Needs Python 3.11+. Either run it from a checkout, or build a binary and forget
Python exists.

```sh
python -m venv .venv
.venv/bin/pip install -e .        # deps + a `remind` command in the venv
```

Without the editable install, `.venv/bin/python -m remind` works the same.

For daily use, most people want the compiled binary on their PATH — see
[Build a binary](#build-a-binary), then either symlink it somewhere already on
PATH or alias it:

```sh
ln -s "$PWD/dist/remind" ~/.local/bin/remind     # works in scripts too
alias remind "$PWD/dist/remind"                  # fish; interactive shells only
```

`dist/remind` is a symlink into the app bundle, so it keeps working after a
rebuild.

## Use

```sh
remind                          # no args -> TUI
remind --text "buy milk"        # print to the top printer that answers
remind --text - < notes.txt     # take the text from stdin
remind --text "x" --device 2    # target one registered printer
remind --text "x" --timeout 20  # wait longer for a printer to show up
remind list device              # id<TAB>name<TAB>address
```

The CLI never prompts and never registers anything: data on stdout, notes and
errors on stderr, exit code 1 on failure. Registering, reordering and print
config are TUI-only, because they need a human.

`--device` takes the id from `list device`, an address, or a name. The id is
just the current position in that list, so it changes when you reorder — use
the address if a script needs a stable reference.

### TUI keys

The menu is Edit / Print / Config / Devices / Quit.

| key | does |
|-----|------|
| `j` / `k`, arrows | move |
| `1`-`9` | jump to a row |
| first letter | select that menu row; press again to activate |
| `enter` / `l` | activate / edit / confirm |
| `esc` / `h` | back (cancels an in-flight edit) |
| `shift`+`j`/`k` | reorder printers |
| `d` | delete the selected printer |
| `r` | rescan |

The note itself is typed straight into the Content box, at the paper's own
width — no external editor, nothing to leave. Everything is text in there
(`h`, `j`, `d`, digits included), so `esc` is the way out; it keeps what you
wrote and hands the box back to the preview.

## Where config lives

In order of preference:

| location | when |
|----------|------|
| `$REMIND_HOME/config.toml` | always wins if set |
| `<repo>/config.toml` | running from a source checkout that already has one |
| `~/.config/remind/config.toml` | otherwise, and always for a packaged build |

A packaged build skips the repo rule on purpose: `__file__` then points inside
the bundle rather than at a checkout, so "next to the code" is the wrong place
to keep state.

If you registered a printer from a checkout and then switch to the binary, move
that `config.toml` to `~/.config/remind/` (or point `REMIND_HOME` at the
checkout) so both see the same printers.

## How it works

A printer is only ever trusted after you pick it once (TOFU) — printing matches
the stored address, never a familiar-looking name. Registered devices have an
order; a job goes to the highest-priority one that is actually advertising.

A cold print is dominated by BLE: the scan cannot finish until the printer
happens to broadcast, which costs seconds.

## Build a binary

Nuitka compiles the Python to C and then to machine code:

```sh
.venv/bin/pip install -e ".[dev]"
./packaging/build.sh
```

You get `dist/ReMind.app`, plus `dist/remind` symlinked to the executable
inside it. The target machine needs no Python installed.

Build on the platform you run on — there is no cross-compilation. The build
must not become `--onefile`, and macOS needs `--mode=app`; both have reasons,
kept in [CONTEXT.md](CONTEXT.md).

Fonts are deliberately not bundled: the renderer looks up system CJK fonts at
runtime. Moving an unsigned build to another machine still trips Gatekeeper.

## Layout

```
remind/
  transport.py   BLE: scan, connect, write            (no business logic)
  render/        text -> 1-bit raster, + TUI preview  (no I/O)
  protocol.py    M02 command sequence, status queries (no BLE specifics)
  config.py      registered devices + per-device print config
  core.py        composes the above into print_text()  (stateless)
  session.py     a warm connection with an idle timeout (TUI only)
  cli.py / tui.py  the two front ends
packaging/       build.sh + entry.py -> dist/ReMind.app
scripts/         debug_print.py times each phase against real hardware
```

[CONTEXT.md](CONTEXT.md) holds the design decisions, the protocol notes, and
the hardware measurements behind them.

## Acknowledgments

Protocol knowledge is mainly adapted from
[transcriptionstream/phomymo](https://github.com/transcriptionstream/phomymo).

## License

MIT — see [LICENSE](LICENSE).
