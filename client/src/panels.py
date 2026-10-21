# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The side panel beside the conversation: a terminal on the computer, its
projects folder, and what has changed there.

Everything runs on the computer as the person who paired this device (terminal.run_command and
terminal.Terminal), so it sees exactly what they would see at its screen.
"""

from __future__ import annotations

import asyncio
import json
import posixpath  # paths on the computer, which is Linux whatever this device is
import shlex
import subprocess
from typing import Callable

import flet as ft

import host
import theme as t
import transcript
from pairing import Machine, PairingError
from terminal import Terminal, plain, run_command

PROJECTS = "/srv/nanoborealis/projects"
# The terminal's width in characters, as the panel shows them (a 12.5 px JetBrains Mono character is
# 7.5 px wide, less the panel's padding): told 110 columns, tables and progress bars wrapped in a
# panel that holds about 56, and the welcome banner's table came out in pieces.
TERMINAL_COLS = max(40, int((t.PANEL_WIDTH - 40) / 7.5))
TERMINAL_KEYS = [
    ("Ctrl+C", "\x03", "Stop what's running"),
    ("Tab", "\t", "Complete what you've typed"),
    ("↑", "\x1b[A", "Previous command"),
    ("↓", "\x1b[B", "Next command"),
    ("Ctrl+D", "\x04", "End input, or close the shell"),
    ("Esc", "\x1b", "Escape"),
]

LIST_DIR = ("import json, os, sys\n"
            "p = sys.argv[1]\n"
            "out = []\n"
            "for e in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower())):\n"
            "    if e.name.startswith('.'):\n"
            "        continue\n"
            "    try:\n"
            "        st = e.stat()\n"
            "    except OSError:\n"
            "        continue\n"
            "    out.append({'name': e.name, 'dir': e.is_dir(), 'size': st.st_size, 'mtime': int(st.st_mtime)})\n"
            "print(json.dumps(out))\n")


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


class TerminalPanel:
    """A shell on the computer: output above, a line to type into below, and the keys a text box
    can't send."""

    def __init__(self, page: ft.Page, machine: Machine):
        self.page, self.machine = page, machine
        self.term: Terminal | None = None
        self.raw = ""
        self.output = ft.Text("", font_family=t.MONO, size=12.5, selectable=True, color=t.TEXT)
        self.input = ft.TextField(
            hint_text="Type a command, then Enter", text_style=ft.TextStyle(font_family=t.MONO, size=13),
            on_submit=self.submit, expand=True, dense=True, border_radius=t.RADIUS, border_color=t.LINE_HI,
            focused_border_color=t.ACCENT, bgcolor=t.BG,
            prefix=ft.Text("$ ", font_family=t.MONO, size=13, color=t.ACCENT),
        )
        keys = ft.Row(wrap=True, spacing=4, controls=[
            ft.Container(t.mono(label, size=11.5, color=t.MUTED), tooltip=tip, border_radius=6, ink=True,
                         padding=ft.Padding.symmetric(horizontal=8, vertical=4), border=ft.Border.all(1, t.LINE_HI),
                         on_click=lambda _e, s=sequence: self.page.run_task(self.key, s))
            for label, sequence, tip in TERMINAL_KEYS
        ])
        self.view = ft.Column(expand=True, spacing=8, controls=[
            ft.Container(ft.ListView([self.output], auto_scroll=True, padding=12, expand=True), expand=True,
                         bgcolor=t.SIDEBAR, border_radius=t.RADIUS, border=ft.Border.all(1, t.LINE)),
            ft.Row([self.input, t.icon_button(ft.Icons.PASSWORD_ROUNDED, "Hide what you type (for passwords)",
                                              on_click=self.toggle_hidden)], spacing=4),
            keys,
        ])

    async def start(self) -> None:
        if self.term is not None:
            return
        self.output.value = "Connecting..."
        self.page.update()
        term = Terminal(self.machine, rows=40, cols=TERMINAL_COLS, console=True)
        try:
            await term.open()
        except PairingError as e:
            self.output.value = str(e)
            self.page.update()
            return
        self.term, self.raw = term, ""
        self.output.value = ""
        self.page.update()
        self.page.run_task(self.pump, term)

    async def pump(self, term: Terminal) -> None:
        while chunk := await term.read():
            self.raw = (self.raw + chunk.decode(errors="replace"))[-200_000:]
            self.output.value = plain(self.raw)
            self.page.update()
        if self.term is term:
            self.term = None
            ended = f" with status {term.exit_status}" if term.exit_status is not None else ""
            self.output.value = plain(self.raw) + f"\n[The shell ended{ended}. Press Enter for a new one.]"
            self.page.update()

    async def submit(self, _event=None) -> None:
        text = self.input.value or ""
        self.input.value = ""
        self.input.password = False
        self.page.update()
        if self.term is None:
            await self.start()
        else:
            await self.term.send(text + "\r")
        await self.input.focus()

    async def key(self, sequence: str) -> None:
        if self.term is None:
            return
        typed, self.input.value = self.input.value or "", ""
        self.page.update()
        await self.term.send(typed + sequence)
        await self.input.focus()

    async def toggle_hidden(self, _event=None) -> None:
        self.input.password = not self.input.password
        self.page.update()
        await self.input.focus()

    def stop(self) -> None:
        term, self.term = self.term, None
        if term is not None:
            term.close()


class FilesPanel:
    """The projects folder: the agent's work, shared with you. Folders open in place; files show
    their contents, and on the computer itself open in their usual app."""

    def __init__(self, page: ft.Page, machine: Machine, on_mention: Callable[[str], None] | None = None):
        self.page, self.machine, self.on_mention = page, machine, on_mention
        self.path = PROJECTS
        self.crumbs = ft.Row(spacing=2, wrap=True)
        self.list = ft.ListView(spacing=1, expand=True)
        self.preview = ft.Container(visible=False, expand=True)
        self.view = ft.Column(expand=True, spacing=8, controls=[
            ft.Row([ft.Container(self.crumbs, expand=True),
                    t.icon_button(ft.Icons.REFRESH_ROUNDED, "Refresh", on_click=lambda _e: self.page.run_task(self.load))]),
            ft.Stack([self.list, self.preview], expand=True),
        ])

    async def start(self) -> None:
        await self.load()

    def stop(self) -> None:
        pass

    def set_crumbs(self) -> None:
        relative = posixpath.relpath(self.path, PROJECTS) if self.path != PROJECTS else ""
        parts = [p for p in relative.split("/") if p and p != "."]
        items: list[ft.Control] = [self.crumb("projects", PROJECTS)]
        for i, part in enumerate(parts):
            items.append(ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=14, color=t.FAINT))
            items.append(self.crumb(part, posixpath.join(PROJECTS, *parts[: i + 1])))
        self.crumbs.controls = items

    def crumb(self, label: str, path: str) -> ft.Control:
        return ft.Container(ft.Text(label, size=12.5, color=t.ACCENT_HI if path != self.path else t.TEXT),
                            on_click=lambda _e: self.page.run_task(self.go, path), border_radius=6,
                            padding=ft.Padding.symmetric(horizontal=6, vertical=3), ink=True)

    async def go(self, path: str) -> None:
        self.path = path
        await self.load()

    async def load(self) -> None:
        self.preview.visible = False
        self.list.visible = True
        self.set_crumbs()
        self.list.controls = [ft.Container(ft.ProgressRing(width=18, height=18, stroke_width=2), padding=16)]
        self.page.update()
        command = f"python3 -c {shlex.quote(LIST_DIR)} {shlex.quote(self.path)}"
        try:
            status, output = await asyncio.to_thread(run_command, self.machine, command, 30)
            entries = json.loads(output) if status == 0 else None
        except (PairingError, ValueError):
            entries = None
        if entries is None:
            self.list.controls = [ft.Container(t.muted("This folder can't be read."), padding=16)]
        elif not entries:
            self.list.controls = [ft.Container(t.muted("Nothing here yet. Ask the agent to start a project."),
                                               padding=16)]
        else:
            self.list.controls = [self.row(e) for e in entries]
        self.page.update()

    def row(self, entry: dict) -> ft.Control:
        path = posixpath.join(self.path, entry["name"])
        icon = ft.Icons.FOLDER_ROUNDED if entry["dir"] else ft.Icons.INSERT_DRIVE_FILE_OUTLINED
        trailing = [] if entry["dir"] else [t.muted(human_size(entry["size"]), size=11.5)]
        if self.on_mention is not None:
            trailing.append(t.icon_button(ft.Icons.ALTERNATE_EMAIL_ROUNDED, "Mention it in your message", size=15,
                                          on_click=lambda _e: self.on_mention(path)))
        return ft.Container(
            content=ft.Row([ft.Icon(icon, size=17, color=t.ACCENT if entry["dir"] else t.MUTED),
                            ft.Text(entry["name"], size=13, color=t.TEXT, expand=True, no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS), *trailing], spacing=10),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6), border_radius=8, ink=True,
            on_click=lambda _e: self.page.run_task(self.go if entry["dir"] else self.show, path),
        )

    async def show(self, path: str) -> None:
        command = (f"f={shlex.quote(path)}; if LC_ALL=C grep -qP '\\x00' \"$f\" 2>/dev/null; then echo BINARY; "
                   f"else head -c 200000 \"$f\"; fi")
        try:
            _status, output = await asyncio.to_thread(run_command, self.machine, command, 30)
        except PairingError as e:
            output = str(e)
        name = posixpath.basename(path)
        actions: list[ft.Control] = [t.quiet_button("Back", icon=ft.Icons.ARROW_BACK_ROUNDED,
                                                    on_click=lambda _e: self.page.run_task(self.load))]
        if host.is_host():
            actions.append(t.quiet_button("Open", icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                          on_click=lambda _e: subprocess.Popen(["xdg-open", path])))
        if output.strip() == "BINARY":
            body: ft.Control = t.muted("This file isn't text.")
        elif name.lower().endswith((".md", ".markdown")):
            body = ft.Container(transcript.markdown(output), padding=12)
        else:
            body = ft.Container(t.mono(output, selectable=True), padding=12)
        self.preview.content = ft.Column([
            ft.Row([*actions, ft.Text(name, size=13, weight=ft.FontWeight.W_600, expand=True, no_wrap=True,
                                      overflow=ft.TextOverflow.ELLIPSIS)], spacing=4),
            ft.Container(ft.ListView([body], expand=True), expand=True, bgcolor=t.SIDEBAR, border_radius=t.RADIUS,
                         border=ft.Border.all(1, t.LINE)),
        ], expand=True, spacing=8)
        self.list.visible = False
        self.preview.visible = True
        self.page.update()


CHANGES = r"""
cd /srv/nanoborealis/projects 2>/dev/null || exit 0
found=0
for g in */.git */*/.git; do
  [ -d "$g" ] || continue
  found=1
  r=${g%/.git}
  echo "@@repo $r"
  git -C "$r" status --porcelain=v1 --untracked-files=all 2>/dev/null | head -200
  echo "@@numstat"
  git -C "$r" diff HEAD --numstat 2>/dev/null | head -200
done
if [ $found = 0 ]; then
  echo "@@recent"
  find . -type f -mmin -2880 -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/.venv/*' \
    -printf '%T@ %s %p\n' 2>/dev/null | sort -rn | head -60
fi
"""


class ChangesPanel:
    """What changed in the projects folder: per git repository, each changed file with its line
    counts, and its diff on click; without git, the files changed in the last two days."""

    def __init__(self, page: ft.Page, machine: Machine):
        self.page, self.machine = page, machine
        self.list = ft.ListView(spacing=1, expand=True)
        self.detail = ft.Container(visible=False, expand=True)
        self.view = ft.Column(expand=True, spacing=8, controls=[
            ft.Row([t.muted("In your projects folder", size=12.5),
                    ft.Container(expand=True),
                    t.icon_button(ft.Icons.REFRESH_ROUNDED, "Refresh", on_click=lambda _e: self.page.run_task(self.load))]),
            ft.Stack([self.list, self.detail], expand=True),
        ])

    async def start(self) -> None:
        await self.load()

    def stop(self) -> None:
        pass

    async def load(self) -> None:
        self.detail.visible = False
        self.list.visible = True
        self.list.controls = [ft.Container(ft.ProgressRing(width=18, height=18, stroke_width=2), padding=16)]
        self.page.update()
        try:
            _status, output = await asyncio.to_thread(run_command, self.machine, CHANGES, 60)
        except PairingError as e:
            self.list.controls = [ft.Container(t.muted(str(e)), padding=16)]
            self.page.update()
            return
        self.list.controls = self.parse(output)
        self.page.update()

    def parse(self, output: str) -> list[ft.Control]:
        rows: list[ft.Control] = []
        repo, section, counts, changed = None, "", {}, []

        def flush() -> None:
            if repo is None:
                return
            rows.append(ft.Container(ft.Row([ft.Icon(ft.Icons.ACCOUNT_TREE_OUTLINED, size=15, color=t.ACCENT),
                                              ft.Text(repo, size=13, weight=ft.FontWeight.W_600)], spacing=8),
                                     padding=ft.Padding.only(left=6, top=10, bottom=4)))
            if not changed:
                rows.append(ft.Container(t.muted("No changes", size=12), padding=ft.Padding.only(left=30)))
            for code, path in changed:
                added, removed = counts.get(path, ("", ""))
                rows.append(self.file_row(repo, code, path, added, removed))

        for line in output.splitlines():
            if line.startswith("@@repo "):
                flush()
                repo, section, counts, changed = line[7:].strip(), "status", {}, []
            elif line == "@@numstat":
                section = "numstat"
            elif line == "@@recent":
                section = "recent"
            elif section == "status" and len(line) > 3 and not line.startswith("##"):
                changed.append((line[:2].strip() or "M", line[3:].strip()))
            elif section == "numstat" and "\t" in line:
                added, removed, path = (line.split("\t") + ["", ""])[:3]
                counts[path] = (added, removed)
            elif section == "recent" and line.strip():
                _stamp, size, path = (line.split(" ", 2) + ["", ""])[:3]
                rows.append(ft.Container(
                    content=ft.Row([ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=15, color=t.MUTED),
                                    ft.Text(path.removeprefix("./"), size=12.5, font_family=t.MONO, expand=True,
                                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                    t.muted(human_size(int(size)) if size.isdigit() else "", size=11.5)], spacing=8),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=5)))
        flush()
        if "@@recent" in output:
            rows.insert(0, ft.Container(t.muted("No git repositories here, so these are the files changed in the "
                                                "last two days.", size=12), padding=ft.Padding.only(left=6, bottom=6)))
        if not rows:
            rows.append(ft.Container(t.muted("Nothing has changed in your projects folder."), padding=16))
        return rows

    def file_row(self, repo: str, code: str, path: str, added: str, removed: str) -> ft.Control:
        color = {"A": t.GOOD, "??": t.GOOD, "D": t.BAD, "M": t.WARN}.get(code, t.MUTED)
        label = {"??": "U"}.get(code, code[:1])
        stats = []
        if added and added != "-":
            stats.append(ft.Text(f"+{added}", size=11.5, color=t.ADDED_FG, font_family=t.MONO))
        if removed and removed != "-":
            stats.append(ft.Text(f"−{removed}", size=11.5, color=t.REMOVED_FG, font_family=t.MONO))
        return ft.Container(
            content=ft.Row([ft.Container(ft.Text(label, size=11, color=color, weight=ft.FontWeight.W_700,
                                                 font_family=t.MONO), width=16),
                            ft.Text(path, size=12.5, font_family=t.MONO, expand=True, no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS), *stats], spacing=8),
            padding=ft.Padding.only(left=24, right=10, top=5, bottom=5), border_radius=8, ink=True,
            on_click=lambda _e: self.page.run_task(self.show_diff, repo, code, path),
        )

    async def show_diff(self, repo: str, code: str, path: str) -> None:
        where = f"{PROJECTS}/{repo}"
        if code == "??":
            command = f"head -c 200000 {shlex.quote(where + '/' + path)}"
        else:
            command = f"git -C {shlex.quote(where)} diff HEAD -- {shlex.quote(path)} | head -c 400000"
        try:
            _status, output = await asyncio.to_thread(run_command, self.machine, command, 30)
        except PairingError as e:
            output = str(e)
        body = transcript.diff_view("", output) if code == "??" else transcript.unified_view(output)
        self.detail.content = ft.Column([
            ft.Row([t.quiet_button("Back", icon=ft.Icons.ARROW_BACK_ROUNDED,
                                   on_click=lambda _e: self.page.run_task(self.load)),
                    ft.Text(path, size=12.5, font_family=t.MONO, expand=True, no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS)], spacing=4),
            ft.ListView([body], expand=True),
        ], expand=True, spacing=8)
        self.list.visible = False
        self.detail.visible = True
        self.page.update()
