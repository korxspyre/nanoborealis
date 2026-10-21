# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The council in the app (docs/council/README.md): Hall chats, the task board, and one-on-one chats
with a council seat. The chair, the agent you already have, keeps its own chats (workspace.py).

Everything goes through the computer's pairing service to its Hall (admin.hall), where you speak
as "you". A Hall room refreshes itself while it's open: the seats answer one after another, and
on a pool of ordinary computers each answer can take a while.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import flet as ft

import admin
import theme as t
from pairing import PairingError

if TYPE_CHECKING:
    from main import NanoBorealisApp
    from workspace import Workspace

POLL_SECONDS = 2.0
STATE_COLORS = {"open": t.ACCENT, "claimed": t.VIOLET, "review": t.WARN, "done": t.GOOD, "failed": t.BAD}


def stamp(at: Any) -> str:
    if not isinstance(at, (int, float)):
        return ""
    when = datetime.fromtimestamp(at)
    return when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%b %d, %H:%M")


class CouncilView:
    def __init__(self, app: "NanoBorealisApp", workspace: "Workspace"):
        self.app = app
        self.workspace = workspace
        self.page = app.page
        self.rooms: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.seats: list[dict[str, Any]] = []
        self.problem: str | None = None  # why the council can't be shown (none on this computer, ...)
        self.showing: str | None = None  # the open room or task: "room:<id>" or "task:<id>"
        self.follower: object | None = None

    async def call(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(admin.hall, self.app.machine, method, path, body)

    async def load(self) -> None:
        """The rooms, tasks and seats for the sidebar; a problem instead when there's no council."""
        try:
            rooms, tasks, seats = await asyncio.gather(self.call("GET", "/rooms"), self.call("GET", "/tasks"),
                                                       self.call("GET", "/seats"))
        except PairingError as e:
            self.problem, self.rooms, self.tasks, self.seats = str(e), [], [], []
            return
        self.problem = None
        self.rooms, self.tasks, self.seats = rooms["rooms"], tasks["tasks"], seats["seats"]

    def seat_name(self, seat_id: str) -> str:
        if seat_id == "you":
            return "You"
        if seat_id == "advisor":
            return "Advisor"
        if seat_id == "hall":
            return "The Hall"
        return next((s["name"] for s in self.seats if s["id"] == seat_id), seat_id)

    # -- The sidebar -----------------------------------------------------------------------------------

    def side_row(self, label: str, key: str, icon: str, meta: ft.Control | None = None) -> ft.Control:
        selected = self.showing == key
        return ft.Container(
            content=ft.Row([ft.Icon(icon, size=15, color=t.ACCENT if selected else t.FAINT),
                            ft.Text(label, size=13.5, color=t.TEXT if selected else "#C9D3E3", no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS, expand=True), *([meta] if meta else [])],
                           spacing=8),
            padding=ft.Padding.only(left=12, right=8, top=6, bottom=6), border_radius=t.RADIUS, ink=True,
            bgcolor=t.RAISED if selected else None,
            on_click=lambda _e, k=key: self.page.run_task(self.workspace.show_section, f"council:{k}"),
        )

    def side_rows(self, mode: str) -> list[ft.Control]:
        if self.problem:
            return [ft.Container(t.muted(self.problem, size=12.5), padding=ft.Padding.only(left=12, right=8, top=8))]
        rows: list[ft.Control] = []
        if mode == "hall":
            for room in (r for r in self.rooms if r["kind"] == "hall"):
                rows.append(self.side_row(room["title"], f"room:{room['id']}", ft.Icons.GROUPS_OUTLINED))
            if not rows:
                rows.append(ft.Container(t.muted("No Hall chats yet. Start one above: the whole council answers."),
                                         padding=ft.Padding.only(left=12, right=8, top=8)))
        elif mode == "task":
            for task in self.tasks:
                rows.append(self.side_row(task["title"], f"task:{task['id']}", ft.Icons.TASK_ALT_ROUNDED,
                                          t.badge(task["state"].upper(), STATE_COLORS.get(task["state"], t.MUTED))))
            if not rows:
                rows.append(ft.Container(t.muted("No tasks yet. Post one above, and the council takes it on."),
                                         padding=ft.Padding.only(left=12, right=8, top=8)))
        return rows

    def seat_rows(self) -> list[ft.Control]:
        """For Agent mode: a one-on-one chat with each council seat, under the chair's own chats."""
        seats = [s for s in self.seats if not s.get("chair")]
        if self.problem or not seats:
            return []
        rows: list[ft.Control] = [ft.Container(t.muted("Council seats", size=11.5),
                                               padding=ft.Padding.only(left=12, top=10, bottom=2))]
        for seat in seats:
            room = next((r for r in self.rooms if r["kind"] == "direct" and r.get("seat") == seat["id"]), None)
            key = f"room:{room['id']}" if room else f"seat:{seat['id']}"
            rows.append(self.side_row(seat["name"], key, ft.Icons.SMART_TOY_OUTLINED,
                                      t.muted(f"trust {seat['trust']:g}", size=11)))
        return rows

    # -- Starting things --------------------------------------------------------------------------------

    async def new_hall_chat(self) -> None:
        topic = t.field("What's it about?", hint="Which database should the task board use?")

        async def start(_event=None) -> None:
            title = (topic.value or "").strip()
            if not title:
                return
            self.page.pop_dialog()
            try:
                room = await self.call("POST", "/rooms", {"title": title[:200]})
                await self.call("POST", f"/rooms/{room['id']}/messages", {"text": title})
            except PairingError as e:
                self.workspace.toast(str(e))
                return
            await self.workspace.show_section(f"council:room:{room['id']}")

        topic.on_submit = start
        self.dialog("New Hall chat", "Everyone on the council reads it, and each seat answers in turn or passes.",
                    [topic], "Start", start)

    async def new_task(self) -> None:
        title = t.field("The task", hint="Write a README for the council")
        detail = t.field("Details (optional)", multiline=True)

        async def post(_event=None) -> None:
            if not (title.value or "").strip():
                return
            self.page.pop_dialog()
            try:
                task = await self.call("POST", "/tasks", {"title": title.value.strip()[:200],
                                                          "detail": (detail.value or "").strip()})
            except PairingError as e:
                self.workspace.toast(str(e))
                return
            await self.workspace.show_section(f"council:task:{task['id']}")

        self.dialog("New task", "The council offers it to its seats: one takes it on, another reviews the result.",
                    [title, detail], "Post it", post)

    def dialog(self, title: str, blurb: str, fields: list[ft.Control], action: str, on_click) -> None:
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text(title, size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=480, content=ft.Column([t.muted(blurb), *fields], spacing=12, tight=True)),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button(action, on_click=on_click)],
        ))

    async def seat_room(self, seat_id: str) -> int:
        """The one-on-one room with a seat, made the first time."""
        await self.load()
        room = next((r for r in self.rooms if r["kind"] == "direct" and r.get("seat") == seat_id), None)
        if room is None:
            room = await self.call("POST", "/rooms", {"title": f"With {self.seat_name(seat_id)}", "kind": "direct",
                                                      "seat": seat_id})
        return int(room["id"])

    # -- The main area ---------------------------------------------------------------------------------

    async def view(self, key: str) -> tuple[str, ft.Control]:
        """The title and body for "room:<id>", "task:<id>" or "seat:<id>"."""
        kind, _, ident = key.partition(":")
        try:
            if kind == "seat":
                room_id = await self.seat_room(ident)
                key, kind, ident = f"room:{room_id}", "room", str(room_id)
            self.showing = key
            if kind == "room":
                return await self.room_view(int(ident))
            return await self.task_view(int(ident))
        except (PairingError, ValueError) as e:
            return "Council", self.frame([t.card(t.muted(str(e)))])

    def frame(self, body: list[ft.Control], composer: ft.Control | None = None) -> ft.Control:
        column = ft.ListView(body, expand=True, spacing=10, padding=ft.Padding.symmetric(horizontal=24, vertical=16),
                             auto_scroll=composer is not None)
        return ft.Column([column, *([ft.Container(composer, padding=ft.Padding.only(left=24, right=24, bottom=16))]
                                    if composer else [])], expand=True, spacing=0)

    def bubble(self, message: dict[str, Any]) -> ft.Control:
        author, kind = message["author"], message["kind"]
        if kind == "system":
            return ft.Container(t.muted(message["text"], size=12.5), alignment=ft.Alignment.CENTER,
                                padding=ft.Padding.symmetric(vertical=4))
        if kind == "pass":
            return t.muted(f"{self.seat_name(author)} passes", size=12)
        mine = author == "you"
        tape = ft.Row([t.badge(self.seat_name(author).upper(),
                               t.ACCENT if mine else t.WARN if author in ("advisor", "hall") else t.VIOLET),
                       t.muted(stamp(message.get("at")), size=11)], spacing=8)
        controls: list[ft.Control] = [tape, ft.Text(message["text"], size=14, selectable=True, color=t.TEXT)]
        if kind == "escalation":
            controls.insert(1, t.muted("asks for outside advice", size=11.5))
        if not mine and author not in ("advisor", "hall"):
            controls.append(ft.Row([
                t.icon_button(ft.Icons.THUMB_UP_OUTLINED, "Good answer: more trust for this seat", size=14,
                              on_click=lambda _e, a=author: self.page.run_task(self.feedback, a, True)),
                t.icon_button(ft.Icons.THUMB_DOWN_OUTLINED, "Poor answer: less trust", size=14,
                              on_click=lambda _e, a=author: self.page.run_task(self.feedback, a, False)),
            ], spacing=0))
        return ft.Container(ft.Column(controls, spacing=4), padding=12, border_radius=t.RADIUS_LG,
                            bgcolor=t.RAISED if mine else t.PANEL, border=ft.Border.all(1, t.LINE))

    async def feedback(self, seat: str, good: bool) -> None:
        try:
            await self.call("POST", "/feedback", {"seat": seat, "kind": "thumbs_up" if good else "thumbs_down"})
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        self.workspace.toast(f"Noted. {self.seat_name(seat)}'s trust {'rises' if good else 'drops'} a little.")

    async def room_view(self, room_id: int) -> tuple[str, ft.Control]:
        room = next((r for r in self.rooms if r["id"] == room_id), None)
        if room is None:
            await self.load()
            room = next((r for r in self.rooms if r["id"] == room_id), None)
        if room is None:
            raise ValueError("That chat isn't there anymore.")
        messages = (await self.call("GET", f"/rooms/{room_id}/messages"))["messages"]
        listing = ft.Column([self.bubble(m) for m in messages], spacing=10)
        if not messages:
            listing.controls.append(t.muted("Say something to start."))
        box = t.field(hint="Message the council" if room["kind"] == "hall" else f"Message {self.seat_name(room['seat'])}",
                      multiline=True)
        box.max_lines = 6
        box.shift_enter = True

        async def send(_event=None) -> None:
            text = (box.value or "").strip()
            if not text:
                return
            box.value = ""
            try:
                await self.call("POST", f"/rooms/{room_id}/messages", {"text": text})
            except PairingError as e:
                self.workspace.toast(str(e))
            await self.refresh_room(room_id, listing)

        box.on_submit = send
        actions: list[ft.Control] = [ft.Container(box, expand=True), t.primary_button("Send", on_click=send)]
        if room["kind"] == "hall":
            actions.insert(1, t.icon_button(ft.Icons.HOW_TO_VOTE_OUTLINED, "Ask the council to vote",
                                            on_click=lambda _e: self.page.run_task(self.new_vote, room_id, listing)))
        self.page.run_task(self.follow_room, room_id, listing, messages[-1]["id"] if messages else 0)
        title = room["title"] if room["kind"] == "hall" else self.seat_name(room.get("seat") or "")
        return title, self.frame([listing], ft.Row(actions, spacing=8, vertical_alignment=ft.CrossAxisAlignment.END))

    async def refresh_room(self, room_id: int, listing: ft.Column) -> None:
        messages = (await self.call("GET", f"/rooms/{room_id}/messages"))["messages"]
        listing.controls = [self.bubble(m) for m in messages]
        self.page.update()

    async def follow_room(self, room_id: int, listing: ft.Column, last: int) -> None:
        """New messages as the seats answer, while this room is the one open."""
        self.follower = follower = object()
        key = f"room:{room_id}"
        while self.follower is follower and self.showing == key and self.workspace.section.startswith("council:"):
            await asyncio.sleep(POLL_SECONDS)
            try:
                fresh = (await self.call("GET", f"/rooms/{room_id}/messages?after={last}"))["messages"]
            except PairingError:
                continue
            if fresh and self.follower is follower and self.showing == key:
                if listing.controls and isinstance(listing.controls[0], ft.Text):
                    listing.controls.clear()
                listing.controls.extend(self.bubble(m) for m in fresh)
                last = fresh[-1]["id"]
                self.page.update()

    async def new_vote(self, room_id: int, listing: ft.Column) -> None:
        question = t.field("The question", hint="Tabs or spaces?")
        options = t.field("The options, comma-separated", hint="tabs, spaces")

        async def ask(_event=None) -> None:
            choices = [o.strip() for o in (options.value or "").split(",") if o.strip()]
            if not (question.value or "").strip() or len(choices) < 2:
                return
            self.page.pop_dialog()
            try:
                await self.call("POST", "/votes", {"question": question.value.strip(), "options": choices,
                                                   "room": room_id})
            except PairingError as e:
                self.workspace.toast(str(e))
                return
            await self.refresh_room(room_id, listing)

        self.dialog("Ask the council to vote", "Each seat votes; ballots count by capability and earned trust. "
                    "A split vote goes to a free cloud model for advice.", [question, options], "Ask", ask)

    async def task_view(self, task_id: int) -> tuple[str, ft.Control]:
        task = await self.call("GET", f"/tasks/{task_id}")
        state = task["state"]
        head = ft.Row([t.badge(state.upper(), STATE_COLORS.get(state, t.MUTED)),
                       t.muted(f"posted by {self.seat_name(task['author'])}" +
                               (f" · {self.seat_name(task['claimed_by'])} on it" if task.get("claimed_by") else ""))],
                      spacing=8)
        body: list[ft.Control] = [head]
        if task.get("detail"):
            body.append(t.card(ft.Text(task["detail"], size=14, selectable=True)))
        for note in task.get("notes") or []:
            color = {"accept": t.GOOD, "reject": t.BAD, "advice": t.WARN}.get(note["kind"], t.MUTED)
            body.append(ft.Row([t.badge(self.seat_name(note["author"]).upper(), color),
                                ft.Text(f"{note['kind']}: {note['text']}", size=13, expand=True, selectable=True),
                                t.muted(stamp(note.get("at")), size=11)], spacing=8,
                               vertical_alignment=ft.CrossAxisAlignment.START))
        if task.get("result"):
            body.append(t.card(ft.Column([t.muted("RESULT", size=11), ft.Text(task["result"], size=14, selectable=True)],
                                         spacing=6)))
        actions: list[ft.Control] = []
        if state == "review":
            reason = t.field("What's wrong (to send it back)")
            actions += [t.primary_button("Accept", on_click=lambda _e: self.page.run_task(
                            self.review, task_id, "accept", "")),
                        ft.Container(reason, expand=True),
                        t.outline_button("Send back", on_click=lambda _e: self.page.run_task(
                            self.review, task_id, "reject", reason.value or ""))]
        elif state == "failed":
            actions.append(t.outline_button("Try again", icon=ft.Icons.REPLAY_ROUNDED,
                                            on_click=lambda _e: self.page.run_task(self.reopen, task_id)))
        elif state in ("open", "claimed"):
            body.append(t.muted("The council is on it. This page follows along.", size=12.5))
            self.page.run_task(self.follow_task, task_id, state, len(task.get("notes") or []))
        return task["title"], self.frame(body, ft.Row(actions, spacing=8) if actions else None)

    async def follow_task(self, task_id: int, state: str, notes: int) -> None:
        self.follower = follower = object()
        key = f"task:{task_id}"
        while self.follower is follower and self.showing == key and self.workspace.section.startswith("council:"):
            await asyncio.sleep(POLL_SECONDS * 2)
            try:
                task = await self.call("GET", f"/tasks/{task_id}")
            except PairingError:
                continue
            if task["state"] != state or len(task.get("notes") or []) != notes:
                if self.follower is follower and self.showing == key:
                    await self.workspace.show_section(f"council:{key}")
                return

    async def review(self, task_id: int, verdict: str, reason: str) -> None:
        if verdict == "reject" and not reason.strip():
            self.workspace.toast("Say what's wrong, so the seat can fix it.")
            return
        try:
            await self.call("POST", f"/tasks/{task_id}/review", {"verdict": verdict, "text": reason.strip()})
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        await self.workspace.show_section(f"council:task:{task_id}")

    async def reopen(self, task_id: int) -> None:
        try:
            await self.call("POST", f"/tasks/{task_id}/reopen")
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        await self.workspace.show_section(f"council:task:{task_id}")
