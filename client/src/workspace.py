# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The app's main screen once connected, laid out as a coding agent's workspace.

On the left, the sidebar: a new chat, your chats (searchable, pinned ones first, then by day), and
the control center's pages. In the middle, the conversation, with the composer under it: model
picker, attachments, slash commands, send and stop. On the right, when you want it, a panel with
a terminal on the computer, its projects folder, and what changed there.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import flet as ft

import agent_settings
import host
import models_catalog
import panels
import theme as t
import transcript
from agent_link import AuthError, LinkError
from model_picker import ModelPicker

if TYPE_CHECKING:
    from main import NanoBorealisApp

SUGGESTIONS = [
    ("Start a project", "Help me start a small project in my projects folder. Ask me what it should do first.",
     ft.Icons.ROCKET_LAUNCH_OUTLINED),
    ("Look at my projects", "Look through my projects folder and tell me what is there.", ft.Icons.FOLDER_OPEN_OUTLINED),
    ("Fix a bug", "I have a bug to fix. Ask me which project, then find and fix it.", ft.Icons.BUG_REPORT_OUTLINED),
    ("Schedule a task", "Every morning at 8, check my projects for failing tests and tell me.",
     ft.Icons.SCHEDULE_OUTLINED),
]
# Commands the app handles itself; the agent's own come from /api/commands.
APP_COMMANDS = [("/model", "Choose this chat's model"), ("/new", "Start a new chat")]
CONTROL_PAGES = [
    ("overview", "Overview", ft.Icons.SPACE_DASHBOARD_OUTLINED),
    ("models", "Models", ft.Icons.AUTO_AWESOME_OUTLINED),
    ("channels", "Channels", ft.Icons.FORUM_OUTLINED),
    ("schedules", "Schedules", ft.Icons.SCHEDULE_OUTLINED),
    ("skills", "Skills", ft.Icons.EXTENSION_OUTLINED),
    ("memory", "Memory", ft.Icons.PSYCHOLOGY_OUTLINED),
    ("devices", "Devices", ft.Icons.DEVICES_OUTLINED),
    ("pool", "Pool", ft.Icons.HUB_OUTLINED),
    ("council", "Council", ft.Icons.GROUPS_OUTLINED),
    ("logs", "Logs", ft.Icons.RECEIPT_LONG_OUTLINED),
    ("settings", "Settings", ft.Icons.SETTINGS_OUTLINED),
]
SESSION = "websocket:"  # nanobot's key prefix for the app's chats
# What the agent accepts as attachments (nanobot's attachment_ingress): code and other text go as plain text.
ATTACH_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf", "application/json",
                "text/csv", "text/html", "text/markdown", "text/plain", "text/xml", "application/xml",
                "application/x-yaml", "application/yaml", "text/yaml", "application/toml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
ATTACH_MAX_FILES, ATTACH_MAX_BYTES = 4, 6 * 1024 * 1024
# The projects folder, as this computer's people see it and as the agent's container does.
HOST_PROJECTS, AGENT_PROJECTS = "/srv/nanoborealis/projects", "/home/nanobot/projects"


# The agent has the model write each chat's title, and a reasoning model (Nemotron) can spend the
# few words it's given thinking aloud, so the title saved is the start of its reasoning ("The user
# wants a concise title for the chat..."). Those open the same few ways; a real title never does.
REASONING_TITLE = re.compile(r"^(?:(?:the user|the chat|the conversation|we need|we should|we have to|let me|"
                             r"i need|i should)\b|(?:okay|first)[,.])", re.IGNORECASE)


def chat_title(chat: dict[str, Any], overrides: dict[str, str] | None = None) -> str:
    key = chat.get("key") or f"{SESSION}{chat.get('chat_id')}"
    if overrides and overrides.get(key):
        return overrides[key]
    title = (chat.get("title") or "").strip()
    if REASONING_TITLE.match(title):
        title = ""  # leaked reasoning: the chat's first message says more
    return (title or chat.get("preview") or "New chat").strip().splitlines()[0][:120]


def project_scope(folder: str | None, only_here: bool, on_the_computer: bool) -> dict[str, str]:
    """The folder a chat works in, as the agent takes it. Full access (the agent may leave the
    folder) is only ever granted to the computer itself: nanobot turns it down for any other device
    ("full workspace access is unavailable for this connection"), and the chip then showed a folder
    the agent wasn't in. From another device a chat is always kept inside its folder."""
    mode = "full" if on_the_computer and not only_here else "restricted"
    return {"project_path": f"{AGENT_PROJECTS}/{folder}" if folder else AGENT_PROJECTS, "access_mode": mode}


def mention_text(path: str) -> str:
    """A file in the projects folder, as the message names it: the agent's own path to it (the
    folder is /srv/nanoborealis/projects on the computer, /home/nanobot/projects in the agent's
    container), in backticks, since a message that starts with "/" is taken for a command."""
    if path == HOST_PROJECTS or path.startswith(HOST_PROJECTS + "/"):
        path = AGENT_PROJECTS + path[len(HOST_PROJECTS):]
    return f"`{path}`"


def chat_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # nanobot's own stamps ("2026-10-21T00:05:59.86") carry no zone and are UTC (its container runs
    # on UTC): read as local, every chat was hours off, and after 8 pm in New York "today's" chats
    # were dated tomorrow, which put them under "Previous 7 days" above "Today".
    return stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else \
        stamp.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def day_group(stamp: datetime | None) -> str:
    if stamp is None:
        return "Older"
    today = datetime.now().date()
    if stamp.date() == today:
        return "Today"
    if stamp.date() == today - timedelta(days=1):
        return "Yesterday"
    if stamp.date() > today - timedelta(days=7):
        return "Previous 7 days"
    return "Older"


def attachment_type(path: str) -> str:
    kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if kind in ATTACH_TYPES:
        return kind
    if kind.startswith("text/") or kind in ("application/javascript", "application/x-sh", "application/x-python"):
        return "text/plain"
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        head.decode("utf-8")
        return "text/plain" if b"\0" not in head else ""
    except (OSError, UnicodeDecodeError):
        return ""


class Workspace:
    def __init__(self, app: "NanoBorealisApp"):
        self.app = app
        self.page = app.page
        self.chat_id: str | None = None
        self.chats: list[dict[str, Any]] = []
        self.sidebar_state: dict[str, Any] = {}
        self.section = "chats"
        self.busy = False
        self.sidebar_open = True
        self.focused = True
        self.settings: dict[str, Any] = {}  # the agent's settings: its presets and default model
        self.agent_commands: list[tuple[str, str]] = []
        self.model_id: str | None = None  # this chat's model
        self.pending_preset: str | None = None  # chosen for a chat that doesn't exist yet
        self.turn_model: str | None = None
        self.context_window: int | None = None
        self.attachments: list[dict[str, Any]] = []
        self.scope: dict[str, Any] | None = None  # the project folder of the chat (or the next new one)
        self.command_done: asyncio.Future | None = None
        # The reply streaming in right now.
        self.thinking: transcript.Thinking | None = None
        self.answers: dict[str, tuple[transcript.Answer, list[str]]] = {}
        self.tools: dict[str, transcript.ToolCard] = {}
        self.last_answer = ""
        self.dirty: dict[int, ft.Control] = {}
        self.flush_task: asyncio.Task | None = None
        self.scroll_task: asyncio.Future | None = None
        self.refresh_pending = False
        self.panel_kind: str | None = None
        self.panel_views: dict[str, Any] = {}
        self.control: Any = None
        # The chat modes, like Chat / Cowork / Code: Agent (your agent, the chair, or one council seat),
        # Hall (the whole council) and Task (the council's board). See council_view.py.
        self.mode = "agent"
        self.council: Any = None

    # -- Layout -----------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        self.search = ft.TextField(
            hint_text="Search chats", dense=True, text_size=13, prefix_icon=ft.Icons.SEARCH_ROUNDED,
            border_radius=t.RADIUS, border_color=t.LINE, focused_border_color=t.ACCENT, bgcolor=t.BG,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8), on_change=self.on_search,
        )
        self.side_list = ft.ListView(expand=True, spacing=1, padding=ft.Padding.symmetric(horizontal=8))
        self.sidebar = ft.Container(width=t.SIDEBAR_WIDTH, bgcolor=t.SIDEBAR, content=self.sidebar_column(),
                                    border=ft.Border.only(right=ft.BorderSide(1, t.LINE)))
        self.drawer = ft.NavigationDrawer(bgcolor=t.SIDEBAR, controls=[])
        self.page.drawer = self.drawer

        self.menu_button = t.icon_button(ft.Icons.MENU_ROUNDED, "Sidebar (Ctrl+B)", on_click=self.toggle_sidebar)
        self.title_text = ft.Text("New chat", size=14.5, weight=ft.FontWeight.W_600, color=t.TEXT, max_lines=1,
                                  overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        self.status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor=t.WARN, tooltip="Connecting")
        self.panel_buttons = {
            kind: t.icon_button(icon, tip, on_click=lambda _e, k=kind: self.page.run_task(self.toggle_panel, k))
            for kind, icon, tip in (("terminal", ft.Icons.TERMINAL_ROUNDED, "Terminal (Ctrl+J)"),
                                    ("files", ft.Icons.FOLDER_OUTLINED, "Projects folder (Ctrl+E)"),
                                    ("changes", ft.Icons.DIFFERENCE_OUTLINED, "Changes"))
        }
        header = ft.Container(
            height=50, padding=ft.Padding.only(left=10, right=12), bgcolor=t.BG,
            border=ft.Border.only(bottom=ft.BorderSide(1, t.LINE)),
            content=ft.Row([self.menu_button, self.title_text, *self.panel_buttons.values(), ft.Container(width=4),
                            self.status_dot], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
        self.banner_text = ft.Text("", size=12.5, color=t.TEXT, expand=True)
        self.banner = ft.Container(
            visible=False, bgcolor=ft.Colors.with_opacity(0.12, t.WARN), padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            content=ft.Row([ft.Icon(ft.Icons.WIFI_OFF_ROUNDED, size=16, color=t.WARN), self.banner_text], spacing=10),
        )
        self.messages = ft.ListView(expand=True, spacing=10)
        self.welcome = self.welcome_view()
        self.status_line = transcript.StatusLine()
        self.status_holder = ft.Container(self.status_line.view)
        self.conversation = ft.Column(expand=True, spacing=0, controls=[
            self.banner, ft.Stack([self.messages, self.welcome], expand=True), self.status_holder, self.composer_view(),
        ])
        self.panel = ft.Container(width=t.PANEL_WIDTH, visible=False, bgcolor=t.PANEL, padding=12,
                                  border=ft.Border.only(left=ft.BorderSide(1, t.LINE)))
        self.control_host = ft.Container(expand=True, visible=False, bgcolor=t.BG)
        self.main_area = ft.Column(expand=True, spacing=0, controls=[
            header,
            ft.Row([ft.Stack([self.conversation, self.control_host], expand=True), self.panel], expand=True,
                   spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH),
        ])
        self.page.on_keyboard_event = self.on_key
        if self.page.window is not None:
            self.page.window.on_event = self.on_window_event
        root = ft.Row([self.sidebar, self.main_area], expand=True, spacing=0,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self.apply_layout()
        self.render_sidebar()
        return root

    def apply_layout(self) -> None:
        width = self.page.width or 1200
        narrow = width < t.NARROW
        self.sidebar.visible = self.sidebar_open and not narrow
        side_width = t.SIDEBAR_WIDTH if self.sidebar.visible else 0
        self.panel.width = t.PANEL_WIDTH if width - side_width - t.PANEL_WIDTH >= 420 else max(300, width - side_width - 360)
        room = width - side_width - (self.panel.width if self.panel.visible else 0)
        side = max(16, (room - t.READABLE) / 2)
        self.messages.padding = ft.Padding.only(left=side, right=side, top=16, bottom=12)
        self.composer_box.padding = ft.Padding.only(left=side, right=side, bottom=14)
        self.status_holder.padding = ft.Padding.only(left=side + 4, right=side, top=2, bottom=6)

    async def on_resize(self) -> None:
        self.apply_layout()
        self.page.update()

    async def toggle_sidebar(self, _event=None) -> None:
        if (self.page.width or 1200) < t.NARROW:
            self.drawer.controls = [ft.Container(self.sidebar_column(drawer=True), height=(self.page.height or 800))]
            await self.page.show_drawer()
            return
        self.sidebar_open = not self.sidebar_open
        self.apply_layout()
        self.page.update()

    # -- Sidebar ----------------------------------------------------------------------------------

    def nav_row(self, label: str, icon: str, selected: bool, on_click, trailing: ft.Control | None = None) -> ft.Control:
        return ft.Container(
            content=ft.Row([ft.Icon(icon, size=17, color=t.ACCENT if selected else t.MUTED),
                            ft.Text(label, size=13.5, color=t.TEXT, weight=ft.FontWeight.W_600 if selected else None,
                                    expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                            *([trailing] if trailing else [])], spacing=10),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8), border_radius=t.RADIUS, ink=True,
            bgcolor=t.RAISED if selected else None, on_click=on_click,
        )

    def sidebar_column(self, drawer: bool = False) -> ft.Column:
        app = self.app
        top = ft.Container(padding=ft.Padding.only(left=14, right=6, top=12, bottom=4), content=ft.Row([
            app.logo(24), ft.Text("NanoBorealis", size=15, weight=ft.FontWeight.W_600, color=t.TEXT, expand=True),
            ft.Container() if drawer else t.icon_button(ft.Icons.KEYBOARD_DOUBLE_ARROW_LEFT_ROUNDED,
                                                        "Hide the sidebar (Ctrl+B)", on_click=self.toggle_sidebar),
        ], spacing=10))
        new_label = {"agent": "New chat", "hall": "New Hall chat", "task": "New task"}[self.mode]
        in_mode = self.section == "chats" or self.section.startswith("council:")
        nav = ft.Column(spacing=2, controls=[
            self.mode_switch(),
            self.nav_row(new_label, ft.Icons.EDIT_NOTE_ROUNDED, False,
                         lambda _e: self.page.run_task(self.new_in_mode), trailing=t.muted("Ctrl N", size=11)),
            self.nav_row({"agent": "Chats", "hall": "Hall chats", "task": "Tasks"}[self.mode],
                         ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED, in_mode,
                         lambda _e: self.page.run_task(self.show_section, "chats")),
            self.nav_row("Control center", ft.Icons.TUNE_ROUNDED, not in_mode,
                         lambda _e: self.page.run_task(self.show_section, "overview")),
        ])
        body: list[ft.Control] = []
        if self.section == "chats":
            body.append(ft.Container(self.search, padding=ft.Padding.only(left=10, right=10, top=6, bottom=4)))
        listing = self.side_list if not drawer else ft.Column(self.side_rows(), spacing=1, scroll=ft.ScrollMode.AUTO,
                                                              expand=True)
        return ft.Column(expand=True, spacing=2, controls=[
            top, ft.Container(nav, padding=ft.Padding.symmetric(horizontal=8)), ft.Divider(), *body, listing,
            ft.Divider(), *self.bottom_rows(),
        ])

    def mode_switch(self) -> ft.Control:
        modes = [("agent", "Agent", "Your agent, or one council seat"), ("hall", "Hall", "The whole council at once"),
                 ("task", "Task", "Give the council work")]
        chips = [ft.Container(
            content=ft.Text(label, size=12.5, weight=ft.FontWeight.W_600,
                            color=t.ON_ACCENT if key == self.mode else t.MUTED),
            bgcolor=t.ACCENT if key == self.mode else None, border_radius=8, expand=True,
            alignment=ft.Alignment.CENTER, padding=ft.Padding.symmetric(vertical=6), tooltip=tip, ink=True,
            on_click=lambda _e, k=key: self.page.run_task(self.set_mode, k)) for key, label, tip in modes]
        return ft.Container(ft.Row(chips, spacing=2), bgcolor=t.RAISED, border_radius=10, padding=3,
                            margin=ft.Margin.only(bottom=6))

    def council_view(self) -> Any:
        if self.council is None:
            import council_view
            self.council = council_view.CouncilView(self.app, self)
        return self.council

    async def set_mode(self, mode: str) -> None:
        """Switch mode: its list, or its own sidebar when a control center page is open."""
        self.mode = mode
        await self.council_view().load()  # the Agent mode lists the council's seats too
        if self.section == "chats" or self.section.startswith("council:"):
            await self.show_section("chats")
        else:
            self.refresh_sidebar()

    async def new_in_mode(self) -> None:
        if self.mode == "hall":
            await self.council_view().new_hall_chat()
        elif self.mode == "task":
            await self.council_view().new_task()
        else:
            await self.open_chat(None)

    def toast(self, text: str) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(text), duration=3500))

    def bottom_rows(self) -> list[ft.Control]:
        app = self.app
        rows: list[ft.Control] = []
        if not app.mobile:  # a phone neither lends its hardware nor writes install sticks
            sharing = app.node is not None and app.node.how is not None
            rows.append(self.nav_row("Sharing this device" if sharing else "Share this device's hardware",
                                     ft.Icons.MEMORY_ROUNDED, False, lambda _e: self.page.run_task(app.open_share_dialog)))
            rows.append(self.nav_row("Make an install stick", ft.Icons.USB_ROUNDED, False,
                                     lambda _e: self.page.run_task(app.open_stick_dialog)))
        if not host.comes_with_os():
            rows.append(self.nav_row(f"App updates ({app.version})", ft.Icons.SYSTEM_UPDATE_ALT_ROUNDED, False,
                                     lambda _e: self.page.run_task(app.check_for_update, False)))
        machine = app.machine
        here = host.is_host() and machine is not None and machine.host == "127.0.0.1"
        where = ft.Row([
            ft.Icon(ft.Icons.COMPUTER_ROUNDED if here else ft.Icons.LOCK_ROUNDED, size=15, color=t.MUTED),
            ft.Column([ft.Text(machine.name if machine else "", size=12.5, color=t.TEXT, no_wrap=True,
                               overflow=ft.TextOverflow.ELLIPSIS),
                       t.muted("This computer" if here else f"Paired, encrypted · {machine.host if machine else ''}",
                               size=11)], spacing=0, tight=True, expand=True),
            *([] if here else [t.icon_button(ft.Icons.LOGOUT_ROUNDED, "Disconnect",
                                             on_click=lambda _e: self.page.run_task(app.disconnect), size=16)]),
        ], spacing=8)
        rows.append(ft.Container(where, padding=ft.Padding.only(left=16, right=6, top=4, bottom=10)))
        return [ft.Container(ft.Column(rows, spacing=2), padding=ft.Padding.symmetric(horizontal=8))]

    def chat_row(self, chat: dict[str, Any], pinned: bool) -> ft.Control:
        title = chat_title(chat, self.sidebar_state.get("title_overrides"))
        selected = chat["chat_id"] == self.chat_id
        running = bool(chat.get("run_started_at"))
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_HORIZ_ROUNDED, icon_size=16, icon_color=t.FAINT, tooltip="More",
            items=[ft.PopupMenuItem(content=ft.Text("Unpin" if pinned else "Pin"), icon=ft.Icons.PUSH_PIN_OUTLINED,
                                    on_click=lambda _e: self.page.run_task(self.pin_chat, chat, not pinned)),
                   ft.PopupMenuItem(content=ft.Text("Rename"), icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE_ROUNDED,
                                    on_click=lambda _e: self.page.run_task(self.rename_chat, chat)),
                   ft.PopupMenuItem(content=ft.Text("Delete", color=t.BAD), icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                    on_click=lambda _e: self.page.run_task(self.delete_chat, chat))],
        )
        lead = ft.ProgressRing(width=11, height=11, stroke_width=1.6, color=t.ACCENT) if running else \
            ft.Icon(ft.Icons.PUSH_PIN_ROUNDED, size=12, color=t.FAINT) if pinned else None
        return ft.Container(
            content=ft.Row([*([lead] if lead else []),
                            ft.Text(title, size=13.5, color=t.TEXT if selected else "#C9D3E3", no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS, expand=True), menu], spacing=6),
            padding=ft.Padding.only(left=12, right=2, top=1, bottom=1), border_radius=t.RADIUS, ink=True,
            bgcolor=t.RAISED if selected else None, tooltip=title if len(title) > 30 else None,
            on_click=lambda _e, c=chat["chat_id"]: self.page.run_task(self.open_chat, c),
        )

    def side_rows(self) -> list[ft.Control]:
        if self.section != "chats" and not self.section.startswith("council:"):
            return [self.nav_row(label, icon, key == self.section,
                                 lambda _e, k=key: self.page.run_task(self.show_section, k))
                    for key, label, icon in CONTROL_PAGES]
        if self.mode != "agent":
            return self.council_view().side_rows(self.mode)
        seats = self.council.seat_rows() if self.council is not None else []
        query = (self.search.value or "").lower().strip()
        pinned_keys = set(self.sidebar_state.get("pinned_keys") or [])
        archived = set(self.sidebar_state.get("archived_keys") or [])
        rows: list[ft.Control] = []
        group = None
        visible = [c for c in self.chats if c.get("key") not in archived
                   and (not query or query in chat_title(c, self.sidebar_state.get("title_overrides")).lower())]
        visible.sort(key=lambda c: c.get("key") not in pinned_keys)  # stable: pinned first, then as listed
        for chat in visible:
            pinned = chat.get("key") in pinned_keys
            label = "Pinned" if pinned else day_group(chat_time(chat.get("updated_at")))
            if label != group:
                group = label
                rows.append(ft.Container(t.muted(group, size=11.5), padding=ft.Padding.only(left=12, top=10, bottom=2)))
            rows.append(self.chat_row(chat, pinned))
        if not rows:
            rows.append(ft.Container(t.muted("No chats match." if query else "No chats yet. Start one above."),
                                     padding=ft.Padding.only(left=12, top=8)))
        return rows + seats

    async def on_search(self, _event=None) -> None:
        self.render_sidebar()
        self.side_list.update()

    def render_sidebar(self) -> None:
        self.side_list.controls = self.side_rows()
        if self.section == "chats":
            for chat in self.chats:
                if chat["chat_id"] == self.chat_id:
                    self.title_text.value = chat_title(chat, self.sidebar_state.get("title_overrides"))

    def refresh_sidebar(self) -> None:
        self.sidebar.content = self.sidebar_column()
        self.render_sidebar()
        self.page.update()

    async def show_section(self, section: str) -> None:
        if (self.page.width or 1200) < t.NARROW:
            await self.page.close_drawer()
        if section == "chats" and self.mode != "agent":
            section = "council:list"  # a mode's own list: the Hall's chats, or the board
        self.section = section
        in_chats = section == "chats"
        in_council = section.startswith("council:")
        self.conversation.visible = in_chats
        self.control_host.visible = not in_chats
        for button in self.panel_buttons.values():
            button.visible = in_chats
        if in_chats:
            self.title_text.value = next((chat_title(c, self.sidebar_state.get("title_overrides"))
                                          for c in self.chats if c["chat_id"] == self.chat_id), "New chat")
        elif in_council:
            council = self.council_view()
            self.control_host.content = ft.Container(ft.ProgressRing(width=22, height=22, stroke_width=2),
                                                     alignment=ft.Alignment.CENTER, expand=True)
            self.sidebar.content = self.sidebar_column()
            self.render_sidebar()
            self.page.update()
            key = section.removeprefix("council:")
            if key == "list":
                council.showing = None
                await council.load()
                title = "Hall" if self.mode == "hall" else "Tasks"
                body = ft.Container(ft.Column([
                    ft.Text(title, size=20, weight=ft.FontWeight.W_600),
                    t.muted(council.problem or ("Pick a Hall chat on the left, or start one: every seat reads it, "
                                                "and each answers in turn." if self.mode == "hall" else
                                                "Pick a task on the left, or post one: the council offers it to its "
                                                "seats, and another seat reviews the result.")),
                ], spacing=8), padding=32)
            else:
                title, body = await council.view(key)
                await council.load()
            self.title_text.value = title
            self.control_host.content = body
            self.render_sidebar()
            self.page.update()
            return
        else:
            import control
            if self.control is None:
                self.control = control.ControlCenter(self.app, self)
            self.title_text.value = next(label for key, label, _ in CONTROL_PAGES if key == section)
            self.control_host.content = self.control.placeholder()
        self.sidebar.content = self.sidebar_column()  # the mode switch and the selected row follow
        self.render_sidebar()
        self.page.update()
        if not in_chats:
            self.control_host.content = await self.control.page_view(section)
            self.page.update()

    # -- Chat housekeeping (the WebUI's sidebar state: pins, names, archive) -----------------------

    async def load_sidebar_state(self) -> None:
        link = self.app.link
        if link is None:
            return
        try:
            state = await link.api_get("/api/webui/sidebar-state")
        except (LinkError, AuthError):
            return
        self.sidebar_state = state if isinstance(state, dict) else {}

    async def save_sidebar_state(self) -> None:
        try:
            await self.app.link.request("sidebar.update", {"state": self.sidebar_state})
        except (LinkError, AttributeError) as e:
            self.page.show_dialog(ft.SnackBar(ft.Text(f"Couldn't save that: {e}"), duration=4000))
        self.render_sidebar()
        self.page.update()

    async def pin_chat(self, chat: dict[str, Any], pin: bool) -> None:
        pinned = [k for k in self.sidebar_state.get("pinned_keys") or [] if k != chat["key"]]
        self.sidebar_state["pinned_keys"] = ([chat["key"]] + pinned) if pin else pinned
        await self.save_sidebar_state()

    async def rename_chat(self, chat: dict[str, Any]) -> None:
        field = t.field("Name", chat_title(chat, self.sidebar_state.get("title_overrides")), autofocus=True)

        async def save(_event=None) -> None:
            name = (field.value or "").strip()
            overrides = dict(self.sidebar_state.get("title_overrides") or {})
            if name:
                overrides[chat["key"]] = name[:120]
            else:
                overrides.pop(chat["key"], None)
            self.sidebar_state["title_overrides"] = overrides
            self.page.pop_dialog()
            await self.save_sidebar_state()

        field.on_submit = save
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG), title=ft.Text("Rename chat"),
            content=ft.Container(field, width=420),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Save", on_click=save)]))

    async def delete_chat(self, chat: dict[str, Any]) -> None:
        async def delete(_event=None) -> None:
            self.page.pop_dialog()
            try:
                result = await self.app.link.request("session.delete", {"key": chat["key"]})
            except LinkError as e:
                self.page.show_dialog(ft.SnackBar(ft.Text(str(e)), duration=4000))
                return
            if isinstance(result, dict) and result.get("blocked_by_automations"):
                self.page.show_dialog(ft.SnackBar(ft.Text("This chat has schedules. Delete them first "
                                                          "(Control center, Schedules)."), duration=5000))
                return
            if chat["chat_id"] == self.chat_id:
                await self.open_chat(None)
            await self.refresh_chats()

        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG), title=ft.Text("Delete this chat?"),
            content=t.muted(f"\"{chat_title(chat, self.sidebar_state.get('title_overrides'))}\" and its history are "
                            f"deleted from the computer.", size=13.5),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Delete", on_click=delete)]))

    # -- Welcome and composer --------------------------------------------------------------------

    def welcome_view(self) -> ft.Control:
        cards = [
            ft.Container(
                content=ft.Row([ft.Icon(icon, size=18, color=t.ACCENT), ft.Text(label, size=13.5, color=t.TEXT)],
                               spacing=10, tight=True),
                padding=ft.Padding.symmetric(horizontal=14, vertical=11), border_radius=t.RADIUS_LG,
                border=ft.Border.all(1, t.LINE_HI), bgcolor=t.PANEL, ink=True,
                on_click=lambda _e, p=prompt: self.page.run_task(self.send_text, p),
            )
            for label, prompt, icon in SUGGESTIONS
        ]
        hour = datetime.now().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        return ft.Container(
            expand=True, alignment=ft.Alignment.CENTER, padding=24,
            content=ft.Column(tight=True, spacing=14, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                self.app.logo(52),
                ft.Text(f"{greeting}. What should we build?", size=26, weight=ft.FontWeight.W_500, color=t.TEXT,
                        text_align=ft.TextAlign.CENTER),
                t.muted("Your agent works on your NanoBorealis computer: it writes, runs and fixes code in your "
                        "projects folder, and remembers what matters.", size=14),
                ft.Container(height=4),
                ft.Row(cards, wrap=True, spacing=10, run_spacing=10, alignment=ft.MainAxisAlignment.CENTER),
            ]),
        )

    def composer_view(self) -> ft.Control:
        self.composer = ft.TextField(
            hint_text="Ask NanoBorealis to build, fix or explain anything", multiline=True, min_lines=1, max_lines=10,
            shift_enter=True, border=ft.InputBorder.NONE, autofocus=True, text_size=14.5,
            hint_style=ft.TextStyle(color=t.FAINT, size=14.5), cursor_color=t.ACCENT,
            content_padding=ft.Padding.only(left=4, right=4, top=10, bottom=4), on_submit=self.on_send,
            on_change=self.on_compose,
        )
        self.commands = ft.Container(visible=False, bgcolor=t.PANEL, border_radius=t.RADIUS,
                                     border=ft.Border.all(1, t.LINE_HI), padding=6)
        self.attached = ft.Row(spacing=6, wrap=True, visible=False)
        self.model_label = ft.Text("Model", size=12.5, color=t.MUTED, no_wrap=True)
        self.model_chip = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED, size=14, color=t.ACCENT), self.model_label,
                            ft.Icon(ft.Icons.EXPAND_MORE_ROUNDED, size=16, color=t.FAINT)], spacing=6, tight=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6), border_radius=20, ink=True,
            on_click=lambda _e: self.page.run_task(self.pick_model), tooltip="Choose this chat's model",
        )
        self.context_meter = ft.Text("", size=11.5, color=t.FAINT, tooltip="How full this chat's context is")
        self.project_label = ft.Text("All projects", size=12.5, color=t.MUTED, no_wrap=True)
        self.project_chip = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.FOLDER_OUTLINED, size=14, color=t.MUTED), self.project_label],
                           spacing=6, tight=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6), border_radius=20, ink=True,
            on_click=lambda _e: self.page.run_task(self.pick_project),
            tooltip="Which project this chat works in",
        )
        self.send_button = ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, tooltip="Send (Enter)", icon_size=18,
                                         bgcolor=t.ACCENT, icon_color=t.ON_ACCENT, on_click=self.on_send,
                                         style=ft.ButtonStyle(shape=ft.CircleBorder(), padding=8))
        self.stop_button = ft.IconButton(ft.Icons.STOP_ROUNDED, tooltip="Stop (Esc)", icon_size=18, visible=False,
                                         bgcolor=t.TEXT, icon_color=t.BG, on_click=self.on_stop,
                                         style=ft.ButtonStyle(shape=ft.CircleBorder(), padding=8))
        box = ft.Container(
            bgcolor=t.RAISED, border_radius=t.RADIUS_LG + 2, border=ft.Border.all(1, t.LINE_HI),
            padding=ft.Padding.only(left=12, right=8, top=8, bottom=8),
            content=ft.Column(tight=True, spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
                self.attached,
                self.composer,
                ft.Row([
                    t.icon_button(ft.Icons.ADD_ROUNDED, "Attach files or images", on_click=self.attach, size=18),
                    t.icon_button(ft.Icons.ALTERNATE_EMAIL_ROUNDED, "Mention a file from your projects",
                                  on_click=lambda _e: self.page.run_task(self.open_panel, "files"), size=17),
                    self.project_chip,
                    self.context_meter, self.model_chip, self.send_button, self.stop_button,
                ], spacing=2, run_spacing=2, wrap=True, alignment=ft.MainAxisAlignment.END,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ]),
        )
        self.composer_box = ft.Container(content=ft.Column([
            self.commands, box,
            ft.Text("Enter to send · Shift+Enter for a new line · / for commands. The agent can make mistakes; "
                    "check its work.", size=11, color=t.FAINT, text_align=ft.TextAlign.CENTER),
        ], spacing=6, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        return self.composer_box

    def all_commands(self) -> list[tuple[str, str]]:
        seen, merged = set(), []
        for command, description in [*APP_COMMANDS, *self.agent_commands]:
            if command not in seen:
                seen.add(command)
                merged.append((command, description))
        return merged

    async def on_compose(self, _event=None) -> None:
        text = self.composer.value or ""
        if text.startswith("/") and "\n" not in text and " " not in text:
            matches = [(c, d) for c, d in self.all_commands() if c.startswith(text)][:9]
            self.commands.content = ft.Column(spacing=0, controls=[
                ft.Container(ft.Row([ft.Container(t.mono(c, color=t.ACCENT_HI), width=120),
                                     t.muted(d, size=12.5, expand=True, no_wrap=True)], spacing=12),
                             padding=ft.Padding.symmetric(horizontal=10, vertical=7), border_radius=8, ink=True,
                             on_click=lambda _e, c=c: self.page.run_task(self.pick_command, c))
                for c, d in matches])
            self.commands.visible = bool(matches)
        elif self.commands.visible:
            self.commands.visible = False
        else:
            return
        self.commands.update()

    async def pick_command(self, command: str) -> None:
        self.commands.visible = False
        if command in ("/model", "/new", "/stop", "/status", "/compact", "/help", "/skill", "/dream"):
            self.composer.value = command
            await self.on_send()
        else:  # one that takes an argument: let it be typed
            self.composer.value = command + " "
            self.page.update()
            await self.composer.focus()

    def mention(self, path: str) -> None:
        self.page.run_task(self.add_mention, path)

    async def add_mention(self, path: str) -> None:
        text = self.composer.value or ""
        self.composer.value = (text + (" " if text and not text.endswith(" ") else "") + mention_text(path) + " ")
        self.composer.update()
        await self.composer.focus()

    async def attach(self, _event=None) -> None:
        files = await self.app.file_picker.pick_files(dialog_title="Attach files", allow_multiple=True)
        for file in files or []:
            path = getattr(file, "path", None)
            if not path or len(self.attachments) >= ATTACH_MAX_FILES:
                continue
            kind = attachment_type(path)
            size = os.path.getsize(path)
            if not kind or size > ATTACH_MAX_BYTES:
                self.page.show_dialog(ft.SnackBar(ft.Text(f"{os.path.basename(path)} can't be attached: "
                                                          f"{'too big (6 MB at most)' if kind else 'not a kind the agent reads'}."),
                                                  duration=4000))
                continue
            self.attachments.append({"path": path, "name": os.path.basename(path), "type": kind})
        self.render_attachments()

    def render_attachments(self) -> None:
        self.attached.controls = [
            ft.Container(ft.Row([ft.Icon(ft.Icons.IMAGE_OUTLINED if a["type"].startswith("image/") else
                                         ft.Icons.DESCRIPTION_OUTLINED, size=14, color=t.MUTED),
                                 ft.Text(a["name"], size=12, color=t.TEXT, no_wrap=True),
                                 ft.Container(ft.Icon(ft.Icons.CLOSE_ROUNDED, size=13, color=t.FAINT),
                                              on_click=lambda _e, a=a: self.page.run_task(self.drop_attachment, a))], spacing=5, tight=True),
                         bgcolor=t.RAISED_HI, border_radius=8, padding=ft.Padding.symmetric(horizontal=8, vertical=4))
            for a in self.attachments]
        self.attached.visible = bool(self.attachments)
        self.page.update()

    async def drop_attachment(self, attachment: dict[str, Any]) -> None:
        self.attachments = [a for a in self.attachments if a is not attachment]
        self.render_attachments()

    def media(self) -> list[dict[str, str]]:
        media = []
        for a in self.attachments:
            with open(a["path"], "rb") as f:
                data = base64.b64encode(f.read()).decode()
            media.append({"data_url": f"data:{a['type']};base64,{data}", "name": a["name"]})
        return media

    # -- Side panel --------------------------------------------------------------------------------

    async def toggle_panel(self, kind: str) -> None:
        if self.panel.visible and self.panel_kind == kind:
            self.close_panel()
        else:
            await self.open_panel(kind)

    async def open_panel(self, kind: str) -> None:
        machine = self.app.machine
        if machine is None:
            return
        view = self.panel_views.get(kind)
        if view is None:
            view = {"terminal": lambda: panels.TerminalPanel(self.page, machine),
                    "files": lambda: panels.FilesPanel(self.page, machine, self.mention),
                    "changes": lambda: panels.ChangesPanel(self.page, machine)}[kind]()
            self.panel_views[kind] = view
        titles = {"terminal": f"Terminal · {machine.name}", "files": "Projects", "changes": "Changes"}
        self.panel_kind = kind
        self.panel.content = ft.Column(expand=True, spacing=10, controls=[
            ft.Row([ft.Text(titles[kind], size=13.5, weight=ft.FontWeight.W_600, color=t.TEXT, expand=True),
                    t.icon_button(ft.Icons.CLOSE_ROUNDED, "Close", on_click=lambda _e: self.close_panel(), size=16)]),
            view.view,
        ])
        self.panel.visible = True
        for name, button in self.panel_buttons.items():
            button.icon_color = t.ACCENT if name == kind else t.MUTED
        self.apply_layout()
        self.page.update()
        await view.start()

    def close_panel(self) -> None:
        self.panel.visible = False
        self.panel_kind = None
        for button in self.panel_buttons.values():
            button.icon_color = t.MUTED
        self.apply_layout()
        self.page.update()

    def stop(self) -> None:
        for view in self.panel_views.values():
            view.stop()
        self.status_line.stop()

    # -- Project folder ----------------------------------------------------------------------------

    def show_scope(self, scope: Any) -> None:
        self.scope = scope if isinstance(scope, dict) and scope.get("project_path") else None
        path = str((self.scope or {}).get("project_path") or "")
        name = path.rstrip("/").rsplit("/", 1)[-1] if path.startswith(AGENT_PROJECTS + "/") else ""
        restricted = (self.scope or {}).get("access_mode") == "restricted"
        self.project_label.value = (name or "All projects") + (" · only here" if restricted and name else "")
        self.project_label.color = t.TEXT if name else t.MUTED
        try:
            self.project_chip.update()
        except Exception:
            pass

    async def pick_project(self) -> None:
        machine = self.app.machine
        if machine is None:
            return
        from terminal import run_command
        listing = f"ls -1 -p {HOST_PROJECTS} 2>/dev/null | grep '/$' | head -100"
        try:
            _status, output = await asyncio.to_thread(run_command, machine, listing, 20)
        except Exception as e:
            self.page.show_dialog(ft.SnackBar(ft.Text(f"Couldn't list your projects: {e}"), duration=4000))
            return
        folders = [line.rstrip("/") for line in output.splitlines() if line.strip()]
        current = str((self.scope or {}).get("project_path") or "")
        on_the_computer = host.is_host()
        only_here = ft.Checkbox(label="Keep the agent inside this folder" if on_the_computer else
                                "The agent stays inside this folder (from this device)",
                                value=(self.scope or {}).get("access_mode") == "restricted" or not on_the_computer,
                                disabled=not on_the_computer, active_color=t.ACCENT)

        async def choose(folder: str | None) -> None:
            self.page.pop_dialog()
            wanted = project_scope(folder, bool(only_here.value), on_the_computer)
            scope = wanted if folder else None
            if self.chat_id is not None and self.app.link is not None:
                try:
                    await self.app.link.set_workspace_scope(self.chat_id, wanted)
                except LinkError as e:
                    self.page.show_dialog(ft.SnackBar(ft.Text(str(e)), duration=4000))
                    return
            self.show_scope(scope)

        def row(label: str, icon: str, folder: str | None, chosen: bool) -> ft.Control:
            return ft.Container(
                ft.Row([ft.Icon(icon, size=18, color=t.ACCENT if chosen else t.MUTED),
                        ft.Text(label, size=13.5, expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Icon(ft.Icons.CHECK_ROUNDED, size=16, color=t.ACCENT) if chosen else ft.Container()],
                       spacing=10),
                padding=ft.Padding.symmetric(horizontal=12, vertical=9), border_radius=t.RADIUS, ink=True,
                bgcolor=t.ACCENT_SOFT if chosen else None,
                on_click=lambda _e: self.page.run_task(choose, folder))

        rows = [row("All projects", ft.Icons.FOLDER_SPECIAL_OUTLINED, None, not current)]
        rows += [row(folder, ft.Icons.FOLDER_OUTLINED, folder, current == f"{AGENT_PROJECTS}/{folder}")
                 for folder in folders]
        if not folders:
            rows.append(ft.Container(t.muted("No project folders yet. Ask the agent to start one."), padding=12))
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text("Work in", size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=420, height=min(420, (self.page.height or 800) - 260), content=ft.Column([
                t.muted("The project this chat works in, from your projects folder.", size=13),
                ft.ListView(rows, spacing=2, expand=True), only_here], spacing=10)),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog())]))

    # -- Models ------------------------------------------------------------------------------------

    def set_model(self, model_id: Any, fallback: bool = False) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            return
        self.model_id = model_id.strip()
        label = models_catalog.label(self.model_id, self.app.catalog if self.app.catalog.models else None)
        self.model_label.value = label + (" (fallback)" if fallback else "")
        self.model_chip.tooltip = f"{self.model_id}. Choose this chat's model"
        try:
            self.model_chip.update()
        except Exception:
            pass

    def set_preset(self, name: Any) -> None:
        """A chat's model, as nanobot names it (a preset); None means the agent's default."""
        if not isinstance(name, str) or not name:
            order = agent_settings.call_order(self.settings) if self.settings else []
            name = order[0] if order else None
        model = agent_settings.preset_model(self.settings, name) if name else None
        if model:
            self.set_model(model)

    async def pick_model(self) -> None:
        picker = ModelPicker(self.page, self.app.catalog, self.model_id, self.use_model,
                             note="For this chat, from its next message. The default for new chats is in the "
                                  "control center, under Models.")
        await picker.open()

    async def use_model(self, model: models_catalog.Model) -> None:
        link = self.app.link
        if link is None:
            return
        try:
            if not model.free and agent_settings.free_only(self.settings):
                await agent_settings.set_free_only(link, False, self.settings)  # confirmed in the picker
            name = await agent_settings.ensure_preset(link, model, self.settings)
            self.settings = await agent_settings.load(link)
            if self.chat_id is None:
                self.pending_preset = name
            else:
                await self.run_command(f"/model {name}")
        except (LinkError, asyncio.TimeoutError) as e:
            self.page.show_dialog(ft.SnackBar(ft.Text(f"Couldn't switch models: {e}"), duration=5000))
            return
        self.set_model(model.id)
        self.page.show_dialog(ft.SnackBar(ft.Text(f"This chat uses {model.short_name} "
                                                  f"({'free' if model.free else 'paid'}) from its next message."),
                                          duration=3000))

    async def run_command(self, command: str) -> None:
        """A slash command in this chat, waiting for the agent's reply before anything else is sent."""
        link = self.app.link
        self.command_done = asyncio.get_running_loop().create_future()
        await link.send_message(self.chat_id, command)
        try:
            await asyncio.wait_for(self.command_done, 15)
        except asyncio.TimeoutError:
            pass
        self.command_done = None

    # -- Chats ---------------------------------------------------------------------------------------

    async def refresh_chats(self) -> None:
        link = self.app.link
        if link is None:
            return
        try:
            self.chats = await link.list_chats()
        except (LinkError, AuthError):
            return
        self.render_sidebar()
        self.page.update()

    async def refresh_chats_soon(self) -> None:
        if self.refresh_pending:
            return
        self.refresh_pending = True
        await asyncio.sleep(0.8)
        self.refresh_pending = False
        await self.refresh_chats()

    async def open_chat(self, chat_id: str | None) -> None:
        if (self.page.width or 1200) < t.NARROW:
            await self.page.close_drawer()
        if self.mode != "agent":  # the chair's chats are Agent mode's
            self.mode = "agent"
            self.sidebar.content = self.sidebar_column()
        if self.section != "chats":
            await self.show_section("chats")
        self.chat_id = chat_id
        self.reset_turn()
        self.set_busy(False)
        self.messages.controls.clear()
        self.welcome.visible = chat_id is None
        self.title_text.value = "New chat"
        self.context_meter.value = ""
        self.pending_preset = None
        row = next((c for c in self.chats if c.get("chat_id") == chat_id), None) if chat_id else None
        self.show_scope((row or {}).get("workspace_scope") if chat_id else self.scope)
        link = self.app.link
        if chat_id is None:
            if link is not None:
                link.chat_id = None
            self.set_preset(None)
            self.render_sidebar()
            self.page.update()
            await self.composer.focus()
            return
        self.render_sidebar()
        self.page.update()
        if link is None:
            return
        thread: dict[str, Any] | None = None
        for attempt in range(3):  # right after the agent restarts, the first try can time out
            try:
                await link.attach(chat_id)
                thread = await link.load_thread(chat_id)
                break
            except (LinkError, AuthError, asyncio.TimeoutError) as e:
                if attempt == 2 or isinstance(e, AuthError):
                    self.add(transcript.error(f"Could not open this chat: {str(e) or type(e).__name__}"))
                    break
                await asyncio.sleep(2)
        if thread is not None:
            if self.chat_id != chat_id:
                return  # moved on while this loaded
            self.render_history(thread.get("messages") or [])
            if thread.get("active_turn_id"):
                self.set_busy(True)
        self.page.update()
        self.scroll_to_end()

    def render_history(self, messages: list[dict[str, Any]]) -> None:
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            media = [x.get("name") or "attachment" for x in m.get("media") or [] if isinstance(x, dict)]
            if role == "user":
                self.messages.controls.append(transcript.user_message(text, media))
            elif role == "assistant":
                if m.get("kind") == "compaction":
                    self.messages.controls.append(transcript.note("Earlier messages were summarized to make room.",
                                                                  ft.Icons.COMPRESS_ROUNDED))
                    continue
                if isinstance(m.get("reasoning"), str) and m["reasoning"].strip():
                    self.messages.controls.append(transcript.Thinking(m["reasoning"], done=True).view)
                if text.strip():
                    answer = transcript.Answer(text, on_copy=self.copy_later)
                    answer.finish()
                    self.messages.controls.append(answer.view)
                usage = m.get("usage") if isinstance(m.get("usage"), dict) else None
                if isinstance(m.get("latencyMs"), (int, float)) or usage:
                    seconds = m["latencyMs"] / 1000 if isinstance(m.get("latencyMs"), (int, float)) else None
                    self.messages.controls.append(transcript.turn_footer(seconds, None, usage))
            elif role == "tool":
                events = [e for e in (m.get("toolEvents") or []) if isinstance(e, dict)]
                edits = [e for e in (m.get("fileEdits") or []) if isinstance(e, dict) and e.get("path")]
                edits_by_call = {e.get("call_id"): e for e in edits}
                for event in events:
                    card = transcript.ToolCard(str(event.get("name") or "tool"), event.get("arguments"))
                    result = event.get("error") or event.get("result") or ""
                    card.finish(result, bool(event.get("error")))
                    if event.get("call_id") in edits_by_call:
                        card.set_edit(edits_by_call.pop(event.get("call_id")))
                    self.messages.controls.append(card.view)
                for edit in edits_by_call.values():
                    card = transcript.ToolCard(edit.get("tool") or "edit_file", {"path": edit["path"]})
                    card.set_edit(edit)
                    if not card.result:
                        card.finish(f"{edit.get('status') or 'done'}: {edit['path']}", edit.get("status") == "error")
                    self.messages.controls.append(card.view)
                if not events and not edits and text.strip():
                    self.messages.controls.append(transcript.note(text.strip().splitlines()[0][:200],
                                                                  ft.Icons.BUILD_CIRCLE_OUTLINED))

    # -- The conversation -------------------------------------------------------------------------

    def add(self, control: ft.Control) -> None:
        self.welcome.visible = False
        self.messages.controls.append(control)
        self.page.update()
        self.scroll_to_end()

    def scroll_to_end(self) -> None:
        """Follow the conversation as it grows (ListView.auto_scroll stops once the list moves)."""
        if self.scroll_task is not None and not self.scroll_task.done():
            return

        async def scroll() -> None:
            await asyncio.sleep(0.05)
            try:
                await self.messages.scroll_to(offset=-1, duration=200)
            except Exception:
                pass

        self.scroll_task = asyncio.ensure_future(scroll())

    def copy_later(self, text: str) -> None:
        """For the transcript's copy buttons, whose handlers are plain callbacks."""
        self.page.run_task(self.copy_text, text)

    async def copy_text(self, text: str) -> None:
        await self.app.clipboard.set(text)
        self.page.show_dialog(ft.SnackBar(ft.Text("Copied"), duration=1500))

    def reset_turn(self) -> None:
        self.thinking = None
        self.answers = {}
        self.tools = {}
        self.last_answer = ""

    def mark_dirty(self, control: ft.Control) -> None:
        self.dirty[id(control)] = control
        if self.flush_task is None or self.flush_task.done():
            self.flush_task = asyncio.ensure_future(self.flush_soon())

    async def flush_soon(self) -> None:
        await asyncio.sleep(0.05)  # batch streamed text into ~20 redraws a second
        controls = list(self.dirty.values())
        self.dirty.clear()
        for control in controls:
            try:
                control.update()
            except Exception:
                pass
        if self.busy:
            self.scroll_to_end()

    def set_busy(self, busy: bool) -> None:
        if busy and not self.busy:
            self.status_line.start()
        elif not busy:
            self.status_line.stop()
        self.busy = busy
        self.send_button.visible = not busy
        self.stop_button.visible = busy

    def close_thinking(self) -> None:
        if self.thinking is not None:
            self.thinking.finish()
            self.mark_dirty(self.thinking.view)
            self.thinking = None

    def show_context(self, used: Any, window: Any) -> None:
        if isinstance(window, int) and window > 0:
            self.context_window = window
        if isinstance(used, int) and self.context_window:
            share = used / self.context_window
            self.context_meter.value = f"{share:.0%} context"
            self.context_meter.color = t.WARN if share > 0.8 else t.FAINT
            self.context_meter.tooltip = (f"{used:,} of {self.context_window:,} tokens. Near the end, the agent "
                                          f"summarizes older messages by itself (or type /compact).")

    def tool_card(self, call_id: str, name: str = "tool", args: Any = None, hint: str = "") -> transcript.ToolCard:
        card = self.tools.get(call_id)
        if card is None:
            card = transcript.ToolCard(name, args, hint)
            self.tools[call_id] = card
            self.add(card.view)
            verb, _target, _icon = transcript.describe(card.name, card.args)
            self.status_line.set_verb({"Run": "Running", "Read": "Reading", "Edit": "Editing",
                                       "Write": "Writing"}.get(verb, "Working"))
        return card

    async def on_event(self, event: dict[str, Any]) -> None:
        name = event["event"]
        if name == "link_up":
            await self.on_link_up()
            return
        if name == "link_down":
            self.status_dot.bgcolor = t.WARN
            self.status_dot.tooltip = "Reconnecting"
            retry = event.get("retry_in")
            self.banner_text.value = ((f"Connection lost. Retrying in {retry:.0f}s. " if retry else "Connection lost. ")
                                      + str(event.get("detail", "")))
            self.banner.visible = True
            self.page.update()
            return
        if name == "moved":  # found again at a new address; the link reconnects there
            self.render_sidebar()
            self.page.update()
            return
        if name in ("session_updated", "turn_end"):
            self.page.run_task(self.refresh_chats_soon)
        if name == "sidebar_state_updated" and isinstance(event.get("state"), dict):
            self.sidebar_state = event["state"]
            self.render_sidebar()
            self.page.update()
            return
        if name == "runtime_model_updated":
            if self.chat_id is None:
                self.set_model(event.get("model_name"))
            self.page.run_task(self.load_settings)
            return
        if event.get("chat_id") != self.chat_id or self.chat_id is None:
            if name == "turn_end" and not self.focused:
                host.notify("NanoBorealis finished", "A chat you're not looking at has an answer.")
            return

        if name == "attached":
            self.set_preset(event.get("model_preset"))
        elif name == "session_updated" and isinstance(event.get("workspace_scope"), dict):
            self.show_scope(event["workspace_scope"])
        elif name == "turn_model_updated":
            self.turn_model = event.get("model_name") if isinstance(event.get("model_name"), str) else None
            self.set_model(self.turn_model, bool(event.get("fallback")))
            self.show_context(None, event.get("context_window_tokens"))
        elif name == "goal_status":
            if event.get("status") == "running":
                self.set_busy(True)
            elif event.get("status") == "idle":
                self.close_thinking()
                self.set_busy(False)
                if self.command_done is not None and not self.command_done.done():
                    self.command_done.set_result(True)
            self.page.update()
        elif name == "goal_state" and isinstance(event.get("goal_state"), dict):
            state = event["goal_state"]
            if state.get("objective") or state.get("recap"):
                self.add(transcript.goal_card(state))
        elif name == "retry_status":
            state = event.get("state")
            if state == "waiting":
                wait = event.get("retry_after_s")
                self.add(transcript.note(f"The model had a problem ({event.get('error_kind') or 'error'}); trying "
                                         f"again" + (f" in {wait:.0f}s" if isinstance(wait, (int, float)) else "")
                                         + f" (attempt {event.get('attempt')}).", ft.Icons.REFRESH_ROUNDED, t.WARN))
            elif state == "exhausted":
                self.add(transcript.note("The model kept failing; the agent gave up on this try.",
                                         ft.Icons.ERROR_OUTLINE_ROUNDED, t.BAD))
        elif name == "context_compaction":
            phase = event.get("phase")
            if phase == "started":
                self.status_line.set_verb("Summarizing earlier messages")
            elif phase == "succeeded":
                self.add(transcript.note("Earlier messages were summarized to make room.", ft.Icons.COMPRESS_ROUNDED))
        elif name == "recovery_state":
            self.add_recovery(event)
        elif name == "reasoning_delta":
            if self.thinking is None:
                self.thinking = transcript.Thinking()
                self.add(self.thinking.view)
                self.status_line.set_verb("Thinking")
            self.thinking.append(str(event.get("text") or ""))
            self.mark_dirty(self.thinking.body)
        elif name == "reasoning_end":
            self.close_thinking()
        elif name == "delta":
            self.close_thinking()
            key = str(event.get("stream_id") or "")
            if key not in self.answers:
                answer = transcript.Answer(on_copy=self.copy_later)
                self.answers[key] = (answer, [])
                self.add(answer.view)
                self.status_line.set_verb("Writing")
            answer, parts = self.answers[key]
            parts.append(str(event.get("text") or ""))
            answer.set("".join(parts))
            self.mark_dirty(answer.md)
        elif name == "stream_end":
            key = str(event.get("stream_id") or "")
            if key in self.answers:
                answer, _parts = self.answers.pop(key)
                if isinstance(event.get("text"), str):
                    answer.set(event["text"])
                answer.finish()
                self.last_answer = answer.md.value or ""
                self.mark_dirty(answer.view)
        elif name == "file_edit":
            for edit in event.get("edits") or []:
                if isinstance(edit, dict):
                    card = self.tool_card(str(edit.get("call_id") or edit.get("path")), str(edit.get("tool") or "edit_file"),
                                          {"path": edit.get("path")})
                    card.set_edit(edit)
                    self.mark_dirty(card.view)
        elif name == "message":
            self.close_thinking()
            self.on_agent_message(event)
            if self.command_done is not None and not self.command_done.done() and not event.get("kind"):
                self.command_done.set_result(True)
        elif name == "user_message":
            self.add(transcript.user_message(str(event.get("text") or ""),
                                             [m.get("name") or "attachment" for m in event.get("media_urls") or []
                                              if isinstance(m, dict)]))
            self.set_busy(True)
        elif name == "turn_end":
            self.close_thinking()
            self.set_busy(False)
            if self.command_done is not None and not self.command_done.done():
                self.command_done.set_result(True)
            if event.get("outcome") == "failed":
                self.add(transcript.error(str(event.get("failure_message") or "The turn failed.")))
            latency = event.get("latency_ms")
            seconds = latency / 1000 if isinstance(latency, (int, float)) else None
            model_id = self.turn_model or self.model_id
            model = models_catalog.label(model_id, self.app.catalog) if model_id else None
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
            if seconds is not None or usage:
                self.add(transcript.turn_footer(seconds, model, usage))
            self.show_context((usage or {}).get("context_tokens") or (usage or {}).get("prompt_tokens"),
                              event.get("context_window_tokens"))
            if not self.focused:
                host.notify("NanoBorealis finished", (self.last_answer or "Your agent is done.").strip())
            self.reset_turn()
            self.page.update()
        elif name == "error":
            detail = str(event.get("detail") or "error")
            if event.get("reason"):
                detail = f"{detail}: {event['reason']}"
            self.add(transcript.error(detail))
            if detail.startswith(("message_rejected", "access_denied", "missing content", "attachment_rejected",
                                  "text_too_large")):
                self.set_busy(False)
                self.page.update()

    def add_recovery(self, event: dict[str, Any]) -> None:
        if event.get("status") not in ("pending", "available", "interrupted") or not event.get("can_continue", True):
            return
        recovery_id = event.get("recovery_id")

        async def act(action: str) -> None:
            try:
                await self.app.link.request(f"recovery.{action}", {"chat_id": self.chat_id, "recovery_id": recovery_id})
            except LinkError as e:
                self.page.show_dialog(ft.SnackBar(ft.Text(str(e)), duration=4000))
            row.visible = False
            self.page.update()

        row = ft.Container(ft.Row([
            ft.Icon(ft.Icons.RESTART_ALT_ROUNDED, size=16, color=t.WARN),
            ft.Text("The agent was interrupted before it finished (a restart, or a lost connection).", size=13,
                    expand=True),
            t.primary_button("Continue", on_click=lambda _e: self.page.run_task(act, "continue")),
            t.quiet_button("Dismiss", on_click=lambda _e: self.page.run_task(act, "dismiss")),
        ], spacing=10), padding=12, bgcolor=ft.Colors.with_opacity(0.10, t.WARN), border_radius=t.RADIUS)
        self.add(row)

    def on_agent_message(self, event: dict[str, Any]) -> None:
        text = str(event.get("text") or "")
        kind = event.get("kind")
        tool_events = [e for e in (event.get("tool_events") or []) if isinstance(e, dict)]
        if kind in ("tool_hint", "progress") or tool_events:
            for tool_event in tool_events:
                card = self.tool_card(str(tool_event.get("call_id") or len(self.tools)),
                                      str(tool_event.get("name") or "tool"), tool_event.get("arguments"), text)
                if tool_event.get("phase") in ("end", "error"):
                    result = tool_event.get("error") or tool_event.get("result") or ""
                    card.finish(result, bool(tool_event.get("error")) or tool_event.get("phase") == "error")
                    self.mark_dirty(card.view)
            if not tool_events and text.strip():
                self.add(transcript.note(text.strip().splitlines()[0][:200], ft.Icons.BUILD_CIRCLE_OUTLINED))
            return
        if not text.strip() or text.strip() == self.last_answer.strip():
            return  # already shown by the stream
        answer = transcript.Answer(text, on_copy=self.copy_later)
        answer.finish()
        self.last_answer = text
        self.add(answer.view)

    async def on_link_up(self) -> None:
        app = self.app
        self.status_dot.bgcolor = t.GOOD
        self.status_dot.tooltip = f"Connected to {app.machine.name if app.machine else ''}, encrypted"
        self.banner.visible = False
        self.page.update()
        reconnect = app.was_up
        app.was_up = True
        await asyncio.gather(self.load_settings(), self.load_sidebar_state(), self.load_commands(),
                             return_exceptions=True)
        await self.refresh_chats()
        if reconnect and self.chat_id:
            await self.open_chat(self.chat_id)  # catch up on anything missed while offline
        elif self.chat_id is None:
            self.set_preset(None)
        if not reconnect and app.link is not None:
            self.page.run_task(self.keep_chats_fresh, app.link)
            self.page.run_task(self.load_catalog)

    async def load_settings(self) -> None:
        link = self.app.link
        if link is None:
            return
        try:
            self.settings = await agent_settings.load(link)
        except (LinkError, AuthError):
            return
        if self.chat_id is None and self.model_id is None:
            self.set_preset(None)

    async def load_commands(self) -> None:
        link = self.app.link
        if link is None:
            return
        try:
            data = await link.api_get("/api/commands")
        except (LinkError, AuthError):
            return
        rows = data.get("commands") if isinstance(data, dict) else []
        self.agent_commands = [(str(c.get("command")), str(c.get("description") or c.get("title") or ""))
                               for c in rows or [] if isinstance(c, dict) and str(c.get("command", "")).startswith("/")]

    async def load_catalog(self) -> None:
        try:
            await asyncio.to_thread(self.app.catalog.load)
        except Exception:
            return  # offline: labels fall back to the model ids
        if self.model_id:
            self.set_model(self.model_id)

    async def keep_chats_fresh(self, link) -> None:
        """Chats live on the computer; ones started on another device show up here too."""
        while self.app.link is link and self.app.workspace is self:
            await asyncio.sleep(20)
            if self.app.link is link and link.connected:
                await self.refresh_chats()

    # -- Sending -----------------------------------------------------------------------------------

    async def send_text(self, text: str, intent: str | None = None) -> None:
        self.composer.value = text
        await self.on_send(intent=intent)

    async def on_send(self, _event=None, intent: str | None = None) -> None:
        text = (self.composer.value or "").strip()
        self.commands.visible = False
        if not text and not self.attachments:
            return
        if text in ("/new", "/clear"):
            self.composer.value = ""
            await self.open_chat(None)
            return
        if text == "/model":
            self.composer.value = ""
            self.page.update()
            await self.pick_model()
            return
        if text == "/stop":
            self.composer.value = ""
            await self.on_stop()
            return
        link = self.app.link
        if link is None or not link.connected:
            self.add(transcript.error("Not connected to the agent yet."))
            return
        try:
            media = self.media()
        except OSError as e:
            self.add(transcript.error(f"Couldn't read an attachment: {e}"))
            return
        names = [a["name"] for a in self.attachments]
        self.composer.value = ""
        self.attachments = []
        self.render_attachments()
        if self.chat_id is None:
            try:
                self.chat_id = await link.new_chat(self.scope)
            except (LinkError, asyncio.TimeoutError) as e:
                self.composer.value = text
                self.add(transcript.error(f"Could not start a chat: {e}"))
                return
            self.title_text.value = (text or names[0]).splitlines()[0][:80]
            if self.pending_preset:
                await self.run_command(f"/model {self.pending_preset}")
                self.pending_preset = None
        self.reset_turn()
        self.add(transcript.user_message(text, names))
        if not text.startswith("/"):
            self.set_busy(True)  # while busy, a new message joins the running turn
        self.page.update()
        try:
            await link.send_message(self.chat_id, text, media=media or None,
                                    **({"intent": intent} if intent else {}))
        except LinkError as e:
            self.add(transcript.error(str(e)))
            self.set_busy(False)
            self.page.update()
        await self.composer.focus()

    async def on_stop(self, _event=None) -> None:
        link = self.app.link
        if link is None or self.chat_id is None or not self.busy:
            return
        try:
            await link.stop_turn(self.chat_id)
        except LinkError as e:
            self.add(transcript.error(str(e)))
            return
        self.close_thinking()
        self.reset_turn()
        self.set_busy(False)  # the agent says what it stopped, in a message of its own
        self.page.update()

    # -- Keys and focus ------------------------------------------------------------------------------

    async def on_key(self, event: ft.KeyboardEvent) -> None:
        key = (event.key or "").lower()
        if key == "escape" and self.busy:
            await self.on_stop()
        elif event.ctrl and key == "n":
            await self.new_in_mode()
        elif event.ctrl and key == "b":
            await self.toggle_sidebar()
        elif event.ctrl and key == "j":
            await self.toggle_panel("terminal")
        elif event.ctrl and key == "e":
            await self.toggle_panel("files")
        elif event.ctrl and key == "k":
            if self.section != "chats":
                await self.show_section("chats")
            await self.search.focus()

    def on_window_event(self, event) -> None:
        kind = str(getattr(event, "type", "") or getattr(event, "data", "")).lower()
        if "blur" in kind:
            self.focused = False
        elif "focus" in kind:
            self.focused = True
