"""Compiled-binary entry point (see build.sh).

The packager compiles this as a top-level script, so `remind/__main__.py`
(which uses relative imports) can't be the target. It also can't be named
remind.py — that would shadow the `remind` package during compilation.
"""

from remind.cli import cli

if __name__ == "__main__":
    cli()
