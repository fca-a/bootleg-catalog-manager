#!/bin/sh
# Meant to be run from the repository root (like its sibling build.bat);
# the script cds to the repo root itself, so either invocation works.
set -e

cd "$(dirname "$0")/.."

# Prefer the venv's own pyinstaller binary; fall back to activating the
# venv (POSIX layout: .venv/bin/activate), then to whatever is on PATH.
if [ -x .venv/bin/pyinstaller ]; then
    PYINSTALLER=.venv/bin/pyinstaller
elif [ -f .venv/bin/activate ]; then
    . .venv/bin/activate
    PYINSTALLER=pyinstaller
else
    PYINSTALLER=pyinstaller
fi

echo "Building Bootleg Catalog Manager..."
"$PYINSTALLER" bootleg-catalog-manager.spec --noconfirm

echo
echo "Build complete!"
echo "Output: dist/bootleg-catalog-manager/bootleg-catalog-manager"
