#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Installs the NanoBorealis app for the current user: the app in ~/.local/share/nanoborealis,
# a launcher on PATH, and an entry in the app menu. Run it from the unpacked release folder:
#   ./install.sh            install or update
#   ./install.sh --remove   uninstall
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/nanoborealis"
BIN="$HOME/.local/bin/nanoborealis-app"
DESKTOP="$HOME/.local/share/applications/nanoborealis-app.desktop"
ICON="$HOME/.local/share/icons/hicolor/512x512/apps/nanoborealis-app.png"

if [ "${1:-}" = --remove ]; then
    rm -rf "$APP_DIR" "$BIN" "$DESKTOP" "$ICON"
    echo "NanoBorealis app removed."
    exit 0
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp -a "$HERE/NanoBorealis/." "$APP_DIR/"
ln -sf "$APP_DIR/NanoBorealis" "$BIN"
cp "$HERE/icon.png" "$ICON"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=NanoBorealis
Comment=Chat with your NanoBorealis agent, share this computer's hardware, make install sticks
Exec=$APP_DIR/NanoBorealis
Icon=nanoborealis-app
Categories=Utility;Development;
StartupWMClass=NanoBorealis
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true
echo "Installed. Open NanoBorealis from your app menu, or run: nanoborealis-app"
