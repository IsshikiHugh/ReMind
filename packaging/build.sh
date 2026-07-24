#!/usr/bin/env bash
# Compile ReMind to machine code with Nuitka.
#
#   ./packaging/build.sh
#
# Why Nuitka and not PyInstaller: PyInstaller's onefile mode unpacks ~20MB into
# a *new* temp directory on every launch, and macOS re-validates those freshly
# written executables each time — measured at 5.3s per run, every run. Nuitka
# writes real machine code that stays put on disk, so validation is cached:
# 0.9s on the first run, ~0.06s after.
#
# macOS needs --mode=app here: PyObjC's Foundation refuses to be packaged any
# other way. That is not a downside — the bundle gives us an Info.plist, which
# is where the Bluetooth usage string has to live.
#
# Build on the platform you run on; there is no cross-compilation.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
OUT="$REPO/dist"

cd "$REPO"
rm -rf "$OUT/ReMind.app" "$OUT/remind"

"$PYTHON" -m nuitka \
    --mode=app \
    --output-dir="$OUT" \
    --output-filename=remind \
    --include-package=remind \
    --include-package=bleak.backends \
    --nofollow-import-to=tkinter \
    --nofollow-import-to=PyObjCTest \
    --macos-app-name=ReMind \
    --macos-app-protected-resource="NSBluetoothAlwaysUsageDescription:ReMind talks to your thermal printer over Bluetooth." \
    --assume-yes-for-downloads \
    --remove-output \
    packaging/entry.py

# The bundle is named after the entry script; give it the product name. The
# entry script cannot simply be called remind.py — that would shadow the
# `remind` package during compilation.
mv "$OUT/entry.app" "$OUT/ReMind.app"
rm -rf "$OUT/entry.dist"  # standalone intermediate, not needed once bundled

# Stable path for shell aliases / PATH, so they survive rebuilds.
ln -sf "ReMind.app/Contents/MacOS/remind" "$OUT/remind"

echo
echo "Built $OUT/ReMind.app  ->  $OUT/remind"
"$OUT/remind" --help >/dev/null && echo "smoke test: ok"
