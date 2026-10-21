#!/bin/sh
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Opens the NanoBorealis client on Linux or macOS. The first run sets up its own Python
# environment; later runs update it whenever the client needs new packages.
set -e
venv="${XDG_DATA_HOME:-$HOME/.local/share}/nanoborealis/client-venv"
deps=2  # bump whenever the package list below changes
if [ ! -x "$venv/bin/python" ] || [ "$(cat "$venv/nanoborealis-deps.txt" 2>/dev/null)" != "$deps" ]; then
    echo "Setting up the NanoBorealis client's Python environment..."
    [ -x "$venv/bin/python" ] || python3 -m venv "$venv"
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check "flet[desktop]==1.0.1" "websockets>=14" "zeroconf>=0.130"
    echo "$deps" > "$venv/nanoborealis-deps.txt"
fi
exec "$venv/bin/python" "$(dirname "$0")/src/main.py"
