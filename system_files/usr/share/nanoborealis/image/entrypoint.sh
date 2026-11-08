#!/bin/sh
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Seeds nanobot's config and skills into the persistent home on first start, reinstalls
# any apt packages the agent listed (the container itself is disposable), then starts nanobot.
set -eu
umask 002

state="$HOME/.nanobot"
mkdir -p "$state"
# Homes seeded under the earlier name carry .nanobot-os-seeded; never seed over them.
if [ ! -e "$state/.nanoborealis-seeded" ] && [ ! -e "$state/.nanobot-os-seeded" ]; then
    cp -R /opt/nanobot-seed/. "$state/"
    touch "$state/.nanoborealis-seeded"
fi
# Skills added in later NanoBorealis releases reach existing agents; ones the agent edited are left alone.
mkdir -p "$state/workspace/skills"
cp -Rn /opt/nanobot-seed/workspace/skills/. "$state/workspace/skills/" 2>/dev/null || true

# The agent's relative paths start in its workspace, so "projects/app" landed in a private folder
# there, and not in ~/projects, the folder shared with the desktop (the app's Projects and Changes
# panels watch it). Make the workspace's projects/ be the shared folder. Earlier work the agent put
# there moves over first, never replacing anything already in the shared folder.
ws="$state/workspace"
shared="$HOME/projects"
if [ -d "$shared" ] && [ ! -L "$ws/projects" ]; then
    mkdir -p "$ws"
    if [ -d "$ws/projects" ]; then
        for item in "$ws"/projects/* "$ws"/projects/.[!.]*; do
            [ -e "$item" ] || continue
            name="$(basename "$item")"
            [ -e "$shared/$name" ] && name="$name.from-workspace"  # same name already there: keep both
            mv -n "$item" "$shared/$name" 2>/dev/null || true
        done
        rmdir "$ws/projects" 2>/dev/null || true  # something would not move: it stays, and so does the folder
    fi
    [ -e "$ws/projects" ] || ln -s "$shared" "$ws/projects"
fi

# A council seat (`nanoborealis council apply`): its config and instructions are managed, and come
# in fresh on every start. What it remembers stays in its own home.
if [ -n "${NANOBOREALIS_SEAT:-}" ] && [ -f /opt/nanoborealis-seat/config.json ]; then
    cp /opt/nanoborealis-seat/config.json "$state/config.json"
    mkdir -p "$state/workspace"
    cp /opt/nanoborealis-seat/AGENTS.md "$state/workspace/AGENTS.md"
fi

# Every agent stays on the request in front of it: memory is an index of subjects, each in its own
# file, opened only when the request is about it, so one chat's subject doesn't leak into the next.
agents="$state/workspace/AGENTS.md"
if [ -f "$agents" ] && ! grep -q '^## Stay on the request' "$agents"; then
    cat >> "$agents" <<'EOF'

## Stay on the request

- Answer the request in front of you. Don't bring up other projects, past subjects or earlier chats unless the user does, or the request clearly depends on them.
- `memory/MEMORY.md` is only an index. Open a subject's file under `memory/subjects/` only when the current request is about that subject.
- When you save memory (including Dream), keep each subject in its own file under `memory/subjects/`, add one index line to `MEMORY.md` for a new subject, and never merge facts from one subject into another.
- If a request is ambiguous about which project it means, ask instead of guessing from memory.
EOF
fi

pkgs="$HOME/.config/nanoborealis/apt-packages"
if [ ! -e "$pkgs" ] && [ -s "$HOME/.config/nanoaurora/apt-packages" ]; then
    mkdir -p "$(dirname "$pkgs")"
    mv "$HOME/.config/nanoaurora/apt-packages" "$pkgs"  # the list's earlier home
fi
if [ -s "$pkgs" ]; then
    list="$(grep -v '^[[:space:]]*#' "$pkgs" | xargs)"
    if [ -n "$list" ]; then
        { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $list; } \
            || echo "nanoborealis: could not reinstall every package listed in $pkgs" >&2
    fi
fi

exec nanobot "$@"
