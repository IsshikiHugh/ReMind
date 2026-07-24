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
# --mode=app-dist, never --mode=app. Nuitka's "app" means *onefile* on every
# platform except macOS, so building with it on Linux would quietly hand us the
# unpack-every-launch behaviour above. "app-dist" is the opposite: standalone
# everywhere, except macOS where it still produces the .app bundle. macOS needs
# that bundle — PyObjC's Foundation refuses to be packaged any other way — and
# the bundle is also where Info.plist lives, which is where the Bluetooth usage
# string has to go.
#
# Build on the platform you run on; there is no cross-compilation.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
OUT="$REPO/dist"

case "$(uname -s)" in
    Darwin) PLATFORM=macos ;;
    Linux) PLATFORM=linux ;;
    *)
        echo "unsupported platform: $(uname -s)" >&2
        exit 1
        ;;
esac

cd "$REPO"
rm -rf "$OUT/ReMind.app" "$OUT/ReMind" "$OUT/remind" "$OUT/entry.app" "$OUT/entry.dist"

args=(
    --mode=app-dist
    --output-dir="$OUT"
    --output-filename=remind
    --include-package=remind
    --include-package=bleak.backends
    --nofollow-import-to=tkinter
    --nofollow-import-to=PyObjCTest
    --assume-yes-for-downloads
    --remove-output
)
if [ "$PLATFORM" = macos ]; then
    args+=(
        --macos-app-name=ReMind
        --macos-app-protected-resource="NSBluetoothAlwaysUsageDescription:ReMind talks to your thermal printer over Bluetooth."
    )
fi

"$PYTHON" -m nuitka "${args[@]}" packaging/entry.py

# Nuitka names its output after the entry script; give it the product name. The
# entry script cannot simply be called remind.py — that would shadow the
# `remind` package during compilation.
if [ "$PLATFORM" = macos ]; then
    mv "$OUT/entry.app" "$OUT/ReMind.app"
    rm -rf "$OUT/entry.dist" # standalone intermediate, not needed once bundled
    BUNDLE="ReMind.app"
    EXE="ReMind.app/Contents/MacOS/remind"
else
    mv "$OUT/entry.dist" "$OUT/ReMind"
    BUNDLE="ReMind"
    EXE="ReMind/remind"
fi

# Stable path for shell aliases / PATH, so they survive rebuilds.
ln -sf "$EXE" "$OUT/remind"

echo
echo "Built $OUT/$BUNDLE  ->  $OUT/remind"
"$OUT/remind" --help >/dev/null && echo "smoke test: ok"
