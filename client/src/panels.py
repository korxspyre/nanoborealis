# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The side panel beside the conversation: a terminal on the computer, its
projects folder, and what has changed there.

Everything runs on the computer as the person who paired this device (terminal.run_command and
terminal.Terminal), so it sees exactly what they would see at its screen.
"""

from __future__ import annotations

import asyncio
import base64
import json
import posixpath  # paths on the computer, which is Linux whatever this device is
import shlex
import subprocess
import time
from typing import Callable

import flet as ft

import host
import theme as t
import transcript
import vt
from pairing import Machine, PairingError
from terminal import Terminal, plain, run_command

PROJECTS = "/srv/nanoborealis/projects"
PDF_PAGES = 12  # pages of a PDF the files panel draws
# The terminal's width in characters, as the panel shows them (a 12.5 px JetBrains Mono character is
# 7.5 px wide, less the panel's padding): told 110 columns, tables and progress bars wrapped in a
# panel that holds about 56, and the welcome banner's table came out in pieces.
TERMINAL_COLS = max(40, int((t.PANEL_WIDTH - 40) / 7.5))
FULL_COLS = max(40, int((t.PANEL_WIDTH - 52) / 7.1))  # 11 px JetBrains Mono, for full screen programs
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
    can't send. Full screen mode is a real terminal (vt.py) for top, nano, less, vim: click the screen and
    the keyboard goes to the program; click anywhere else to give it back."""

    def __init__(self, page: ft.Page, machine: Machine):
        self.page, self.machine = page, machine
        self.term: Terminal | None = None
        self.raw = ""
        self.full = False
        self.capturing = False
        self.sent = ""  # what of the key sink's text has gone to the terminal
        self.screen: vt.Screen | None = None
        self.output = ft.Text("", font_family=t.MONO, size=12.5, selectable=True, color=t.TEXT)
        self.full_button = t.quiet_button("Full screen", icon=ft.Icons.FULLSCREEN_ROUNDED,
                                          on_click=lambda _e: self.page.run_task(self.toggle_full))
        self.hint = t.muted("", size=11.5)
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
        # The tap is caught around the list: the list itself swallowed the container's own click.
        self.screen_box = ft.Container(ft.GestureDetector(ft.ListView([self.output], auto_scroll=True, padding=12, expand=True),
                                                          on_tap=lambda _e: self.grab(), expand=True), expand=True,
                                       bgcolor=t.SIDEBAR, border_radius=t.RADIUS, border=ft.Border.all(1, t.LINE),
                                       on_click=lambda _e: self.grab())
        self.line_row = ft.Row([self.input, t.icon_button(ft.Icons.PASSWORD_ROUNDED, "Hide what you type (for passwords)",
                                                          on_click=self.toggle_hidden)], spacing=4)
        # Holds the focus while a full-screen program has the keyboard, so typing doesn't also land in the chat box.
        # What you type arrives here as text (any keyboard layout, accents, pasting), and goes to the program;
        # on_key sends only what text can't carry: Enter, arrows, Ctrl and Alt combinations.
        self.sink = ft.TextField(width=1, height=1, opacity=0, border=ft.InputBorder.NONE, on_change=self.typed)
        self.view = ft.Column(expand=True, spacing=8, controls=[
            self.screen_box, self.line_row, keys, ft.Row([self.full_button, self.hint, self.sink], spacing=6),
        ])

    async def toggle_full(self) -> None:
        """Between the line-by-line console and a real terminal; either way a fresh shell."""
        self.full = not self.full
        self.capturing = False
        self.full_button.content = "Line by line" if self.full else "Full screen"
        self.full_button.icon = ft.Icons.FULLSCREEN_EXIT_ROUNDED if self.full else ft.Icons.FULLSCREEN_ROUNDED
        self.line_row.visible = not self.full
        self.hint.value = "Click the screen to type into it; click outside to stop." if self.full else ""
        self.stop()
        await self.start()

    def grab(self) -> None:
        if self.full and self.term is not None:
            self.capturing = True
            self.screen_box.border = ft.Border.all(1, t.ACCENT)
            self.hint.value = "Typing goes to the terminal. Click outside it to stop."
            self.page.update()
            self.page.run_task(self.sink.focus)

    def release(self) -> None:
        if self.capturing:
            self.capturing = False
            self.screen_box.border = ft.Border.all(1, t.LINE)
            self.hint.value = "Click the screen to type into it; click outside to stop."
            self.page.update()

    async def typed(self, event) -> None:
        """Send what was added since last time. The field isn't cleared on every keystroke: a clear racing the next
        key sent the first letter twice ("ttop"). It's cleared on Enter, when nothing is coming."""
        value = event.control.value or ""
        new = value[len(self.sent):] if value.startswith(self.sent) else value
        self.sent = value
        if new and self.term is not None:
            await self.term.send(new)

    def reset_sink(self) -> None:
        self.sink.value, self.sent = "", ""
        self.sink.update()

    async def on_key(self, event: ft.KeyboardEvent) -> None:
        if self.term is None:
            return
        name = (event.key or "").lower()
        if (len(name) == 1 or name == "space") and not event.ctrl and not event.alt:
            return  # a character: it comes in as text, through typed()
        data = vt.key_bytes(event.key or "", event.shift, event.ctrl, event.alt)
        if name == "backspace" and self.sent:
            self.sent = self.sent[:-1]  # the field lost that character too; keep in step
        if name in ("enter", "escape") or event.ctrl:
            self.reset_sink()
        if data:
            await self.term.send(data)

    async def start(self) -> None:
        if self.term is not None:
            return
        self.output.value = "Connecting..."
        self.page.update()
        # Full screen: a smaller font, lines cut rather than wrapped, and as many columns as fit the pane, so
        # programs that draw the whole screen (top, vim) line up.
        rows, cols = (36, FULL_COLS) if self.full else (40, TERMINAL_COLS)
        self.output.size = 11 if self.full else 12.5
        self.output.no_wrap = self.full
        self.output.selectable = not self.full  # selectable text takes the tap that hands the keyboard over
        self.screen = vt.Screen(rows, cols) if self.full else None
        term = Terminal(self.machine, rows=rows, cols=cols, console=not self.full)
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
            if self.screen is not None and self.term is term:
                self.screen.feed(chunk)
                self.output.value = self.screen.text_with_cursor()
            else:
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
        self.capturing = False
        if term is not None:
            term.close()


class TerminalTabs:
    """Several terminals side by side in tabs, each its own shell on the computer: + opens another, x closes one.
    Presents the same face as one TerminalPanel to the workspace (start, stop, keys)."""

    def __init__(self, page: ft.Page, machine: Machine):
        self.page, self.machine = page, machine
        self.tabs: list[TerminalPanel] = []
        self.active = 0
        self.bar = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO)
        self.body = ft.Container(expand=True)
        self.view = ft.Column([self.bar, self.body], expand=True, spacing=6)
        self.add(start=False)

    @property
    def current(self) -> TerminalPanel:
        return self.tabs[self.active]

    @property
    def capturing(self) -> bool:
        return self.current.capturing

    def paint(self) -> None:
        chips: list[ft.Control] = []
        for i, _tab in enumerate(self.tabs):
            on = i == self.active
            chips.append(ft.Container(
                ft.Row([ft.Icon(ft.Icons.TERMINAL_ROUNDED, size=13, color=t.ACCENT if on else t.FAINT),
                        ft.Text(f"Shell {i + 1}", size=12, color=t.TEXT if on else t.MUTED),
                        *([t.icon_button(ft.Icons.CLOSE_ROUNDED, "Close this shell", size=12,
                                         on_click=lambda _e, k=i: self.page.run_task(self.close, k))]
                          if len(self.tabs) > 1 else [])], spacing=4, tight=True),
                padding=ft.Padding.only(left=8, right=2, top=3, bottom=3), border_radius=8, ink=True,
                bgcolor=t.RAISED if on else None, border=ft.Border.all(1, t.ACCENT if on else t.LINE),
                on_click=lambda _e, k=i: self.page.run_task(self.pick, k)))
        chips.append(t.icon_button(ft.Icons.ADD_ROUNDED, "Another shell", size=15,
                                   on_click=lambda _e: self.page.run_task(self.new_tab)))
        self.bar.controls = chips
        self.body.content = self.current.view

    def add(self, start: bool = True) -> None:
        self.tabs.append(TerminalPanel(self.page, self.machine))
        self.active = len(self.tabs) - 1
        self.paint()

    async def new_tab(self) -> None:
        self.current.release()
        self.add()
        self.page.update()
        await self.current.start()

    async def pick(self, index: int) -> None:
        self.current.release()
        self.active = index
        self.paint()
        self.page.update()
        await self.current.start()  # a no-op for one already running

    async def close(self, index: int) -> None:
        tab = self.tabs.pop(index)
        tab.stop()
        self.active = min(self.active if index > self.active else max(0, self.active - 1), len(self.tabs) - 1)
        self.paint()
        self.page.update()

    async def start(self) -> None:
        self.paint()
        await self.current.start()

    def stop(self) -> None:
        for tab in self.tabs:
            tab.stop()

    def release(self) -> None:
        self.current.release()

    async def on_key(self, event: ft.KeyboardEvent) -> None:
        await self.current.on_key(event)


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

    async def show_pdf(self, path: str) -> ft.Control:
        """PDF pages drawn on the computer (pdftoppm) and shown as pictures, at most PDF_PAGES."""
        command = (f"d=$(mktemp -d); trap 'rm -rf \"$d\"' EXIT; pdftoppm -png -r 90 -l {PDF_PAGES} "
                   f"{shlex.quote(path)} \"$d/p\" 2>/dev/null || exit 3; "
                   f"pages=$(pdfinfo {shlex.quote(path)} 2>/dev/null | awk '/^Pages:/{{print $2}}'); echo \"@@pages ${{pages:-?}}\"; "
                   f"for f in $(ls \"$d\" | sort -V); do echo \"@@page\"; base64 -w0 \"$d/$f\"; echo; done")
        try:
            status, output = await asyncio.to_thread(run_command, self.machine, command, 60)
        except PairingError as e:
            return t.muted(str(e))
        if status != 0 or "@@page" not in output:
            return t.muted("This PDF couldn't be drawn.")
        total = output.split("@@pages ", 1)[1].split("\n", 1)[0].strip() if "@@pages " in output else "?"
        pages = [p.strip() for p in output.split("@@page\n")[1:] if p.strip()]
        controls: list[ft.Control] = [ft.Image(src=base64.b64decode(p), fit=ft.BoxFit.FIT_WIDTH,
                                               border_radius=4) for p in pages]
        if total not in ("?", str(len(pages))):
            controls.append(t.muted(f"Showing the first {len(pages)} of {total} pages.", size=12))
        return ft.Column(controls, spacing=10)

    async def show(self, path: str) -> None:
        if path.lower().endswith(".pdf"):
            await self.present(path, await self.show_pdf(path))
            return
        command = (f"f={shlex.quote(path)}; if LC_ALL=C grep -qP '\\x00' \"$f\" 2>/dev/null; then echo BINARY; "
                   f"else head -c 200000 \"$f\"; fi")
        try:
            _status, output = await asyncio.to_thread(run_command, self.machine, command, 30)
        except PairingError as e:
            output = str(e)
        name = posixpath.basename(path)
        if output.strip() == "BINARY":
            body: ft.Control = t.muted("This file isn't text.")
        elif name.lower().endswith((".md", ".markdown")):
            body = ft.Container(transcript.markdown(output), padding=12)
        else:
            body = ft.Container(t.mono(output, selectable=True), padding=12)
        await self.present(path, body)

    async def present(self, path: str, body: ft.Control) -> None:
        name = posixpath.basename(path)
        actions: list[ft.Control] = [t.quiet_button("Back", icon=ft.Icons.ARROW_BACK_ROUNDED,
                                                    on_click=lambda _e: self.page.run_task(self.load))]
        if host.is_host():
            actions.append(t.quiet_button("Open", icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                          on_click=lambda _e: subprocess.Popen(["xdg-open", path])))
        self.preview.content = ft.Column([
            ft.Row([*actions, ft.Text(name, size=13, weight=ft.FontWeight.W_600, expand=True, no_wrap=True,
                                      overflow=ft.TextOverflow.ELLIPSIS)], spacing=4),
            ft.Container(ft.ListView([body], expand=True), expand=True, bgcolor=t.SIDEBAR, border_radius=t.RADIUS,
                         border=ft.Border.all(1, t.LINE)),
        ], expand=True, spacing=8)
        self.list.visible = False
        self.preview.visible = True
        self.page.update()


def subagent_reported(messages: list[dict], task_id: str) -> bool:
    """Whether a background subagent has reported, from the chat's history: nanobot doesn't mark its report there,
    but the report always comes as the agent speaking again after acknowledging the spawn, before you say anything
    else. So: after the spawn call that started `task_id`, the acknowledgement, then another answer of the agent's
    with no message of yours in between (or the subagent's own words, when nanobot keeps them as their own row)."""
    start = next((i for i, m in enumerate(messages)
                  if f"(id: {task_id})" in json.dumps(m.get("toolEvents") or m.get("content") or "", default=str)), None)
    if start is None:
        return False
    answers = 0
    for m in messages[start + 1:]:
        if m.get("role") == "user":
            return False
        if m.get("role") == "assistant" and str(m.get("content") or "").strip():
            answers += 1
            if answers >= 2:
                return True
    return False


class BackgroundPanel:
    """Everything working on the side, in one list, like Claude Code's background tasks: this chat's tool calls
    still running, subagents working in the background until they report, the rigs' board, and the agents' jobs
    on your devices with what they're waiting on you for. It refreshes every few seconds while it's open."""

    EVERY = 4.0

    def __init__(self, page: ft.Page, machine: Machine, workspace):
        self.page, self.machine, self.workspace = page, machine, workspace
        self.list = ft.ListView(spacing=6, expand=True)
        self.view = ft.Column([self.list], expand=True)
        self.running = False

    async def start(self) -> None:
        if not self.running:
            self.running = True
            self.page.run_task(self.loop)

    def stop(self) -> None:
        self.running = False

    async def loop(self) -> None:
        while self.running:
            try:
                await self.refresh()
            except Exception as e:  # one bad read never stops the pane
                print(f"nanoborealis-client: background pane: {e!r}")
            await asyncio.sleep(self.EVERY)

    def section(self, title: str, rows: list[ft.Control], empty: str) -> list[ft.Control]:
        return [t.muted(title, size=11), *(rows or [t.muted(empty, size=12)])]

    def item(self, icon: str, title: str, detail: str, color: str = t.ACCENT, on_click=None) -> ft.Control:
        return ft.Container(ft.Row([ft.Icon(icon, size=16, color=color),
                                    ft.Column([ft.Text(title, size=13, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                               t.muted(detail, size=11.5)], spacing=0, expand=True)], spacing=8),
                            padding=ft.Padding.symmetric(horizontal=8, vertical=6), border_radius=8, bgcolor=t.SIDEBAR,
                            ink=on_click is not None, on_click=on_click)

    async def refresh(self) -> None:
        import admin
        ws = self.workspace
        now = time.time()
        tools = [self.item(ft.Icons.PENDING_OUTLINED, f"{c.name}", f"running for {time.monotonic() - c.started:.0f}s")
                 for c in list(ws.tools.values()) if getattr(c, "running", False)]
        # Subagents: done once their report is in their chat's history.
        pending = {tid: b for tid, b in ws.background.items() if not b["done"]}
        if pending and ws.app.link is not None:
            for chat in {b["chat_id"] for b in pending.values()}:
                try:
                    thread = await ws.app.link.load_thread(chat)
                except Exception:
                    continue
                messages = thread.get("messages") or []
                for tid, b in pending.items():
                    if b["chat_id"] == chat and subagent_reported(messages, tid):
                        b["done"] = now
        subagents = [self.item(ft.Icons.CALL_SPLIT_ROUNDED if not b["done"] else ft.Icons.CHECK_ROUNDED, b["label"],
                               (f"reported {int(now - b['done'])}s ago" if b["done"] else
                                f"working for {int(now - b['started'])}s" if now - b["started"] < 1800 else
                                "no report seen: look in its chat"),
                               t.GOOD if b["done"] else t.VIOLET)
                     for b in sorted(ws.background.values(), key=lambda b: -b["started"])[:12]]
        try:
            tasks = [x for x in (await asyncio.to_thread(admin.hall, self.machine, "GET", "/tasks"))["tasks"]
                     if x["state"] in ("open", "claimed", "review")]
        except Exception:
            tasks = []
        board = [self.item(ft.Icons.ASSIGNMENT_OUTLINED, x["title"], f"{x['state']}" + (f" · {x['claimed_by']}" if x.get("claimed_by") else ""),
                           on_click=lambda _e, tid=x["id"]: self.page.run_task(ws.show_section, f"council:task:{tid}"))
                 for x in tasks[:12]]
        try:
            lending = await asyncio.to_thread(admin.hands_devices, self.machine)
            asks = await asyncio.to_thread(admin.hands_asks, self.machine)
        except Exception:
            lending, asks = [], []
        devices = [self.item(ft.Icons.FRONT_HAND_OUTLINED, d["name"], f"{d['running']} running, {d['queued']} waiting")
                   for d in lending if d.get("running") or d.get("queued")]
        devices += [self.item(ft.Icons.HELP_OUTLINE_ROUNDED, f"Waiting for you: {a['command'][:60]}",
                              f"{a['device_name']} · {a['reason']}", t.WARN,
                              on_click=lambda _e, a=a: ws.app.hands.show_ask(a)) for a in asks]
        self.list.controls = [*self.section("THIS CHAT", tools, "Nothing running."),
                              *self.section("SUBAGENTS", subagents, "None in the background."),
                              *self.section("RIGS' BOARD", board, "Nothing on the board."),
                              *self.section("YOUR DEVICES", devices, "No agent jobs on your devices.")]
        self.page.update()


CHANGES = r"""
cd /srv/nanoborealis/projects 2>/dev/null || exit 0
found=0
for g in */.git */*/.git .worktrees/*/.git; do
  [ -e "$g" ] || continue  # a worktree's .git is a file
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
    -not -path '*/__pycache__/*' -not -path '*/.pytest_cache/*' -not -path '*/.mypy_cache/*' -not -path './.worktrees/*' \
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
                    padding=ft.Padding.symmetric(horizontal=10, vertical=5), border_radius=8, ink=True,
                    # No git, so no diff: the file as it is now.
                    on_click=lambda _e, p=path.removeprefix("./"): self.page.run_task(self.show_diff, ".", "??", p)))
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
