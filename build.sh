#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=== OID Browser build ==="

# Check dependencies
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "ERROR: tkinter not available. Install: sudo dnf install python3-tkinter"
    exit 1
fi

# Install PyInstaller if missing
if ! python3 -m PyInstaller --version &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install --user pyinstaller
fi

# Clean previous build
rm -rf build dist oidbrowser.spec

# Build
echo "Building..."
python3 -m PyInstaller \
    --onefile \
    --windowed \
    --name oidbrowser \
    --icon img/logo.png \
    main.py

echo ""
echo "Done: dist/oidbrowser ($(du -h dist/oidbrowser | cut -f1))"
