# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The colony in the app (docs/rigs/README.md): Colony chats, the task board, and one-on-one chats
with a colony seat. The chair, the agent you already have, keeps its own chats (workspace.py).

Everything goes through the computer's pairing service to its Colony (admin.colony), where you speak
as "you". A colony chat refreshes itself while it's open: the seats answer one after another, and
on a pool of ordinary computers each answer can take a while.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import flet as ft

import admin
import theme as t
import transcript
from pairing import PairingError

if TYPE_CHECKING:
    from main import NanoBorealisApp
    from workspace import Workspace

POLL_SECONDS = 2.0
def score_text(seat: dict) -> str:
    """A member's measured ability for a list: its benchmark score, or that it hasn't been tested yet."""
    return f"{seat['score']:.0f}%" if seat.get("score") is not None else "untested"


STATE_COLORS = {"open": t.ACCENT, "claimed": t.VIOLET, "review": t.WARN, "done": t.GOOD, "failed": t.BAD}


def stamp(at: Any) -> str:
    if not isinstance(at, (int, float)):
        return ""
    when = datetime.fromtimestamp(at)
    return when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%b %d, %H:%M")


class ColonyView:
    def __init__(self, app: "NanoBorealisApp", workspace: "Workspace"):
        self.app = app
        self.workspace = workspace
        self.page = app.page
        self.rooms: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.seats: list[dict[str, Any]] = []
        self.problem: str | None = None  # why the colony can't be shown (none on this computer, ...)
        self.showing: str | None = None  # the open room or task: "room:<id>" or "task:<id>"
        self.follower: object | None = None

    async def call(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(admin.colony, self.app.machine, method, path, body)

    async def load(self) -> None:
        """The rooms, tasks and seats for the sidebar; a problem instead when there's no colony."""
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
        if seat_id == "colony":
            return "Your colony"
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
            on_click=lambda _e, k=key: self.page.run_task(self.workspace.show_section, f"colony:{k}"),
        )

    def side_rows(self, mode: str) -> list[ft.Control]:
        if self.problem:
            return [ft.Container(t.muted(self.problem, size=12.5), padding=ft.Padding.only(left=12, right=8, top=8))]
        def heading(text: str) -> ft.Control:
            return ft.Container(t.muted(text, size=11.5), padding=ft.Padding.only(left=12, top=10, bottom=2))

        def empty(text: str) -> ft.Control:
            return ft.Container(t.muted(text, size=12.5), padding=ft.Padding.only(left=12, right=8, top=4))
        rooms = [self.side_row(r["title"], f"room:{r['id']}", ft.Icons.GROUPS_OUTLINED)
                 for r in self.rooms if r["kind"] == "colony"]
        board = [self.side_row(x["title"], f"task:{x['id']}", ft.Icons.TASK_ALT_ROUNDED,
                               t.badge(x["state"].upper(), STATE_COLORS.get(x["state"], t.MUTED))) for x in self.tasks]
        seats = [self.side_row(s["name"].split("@")[0].capitalize(), (lambda room: f"room:{room['id']}" if room else f"seat:{s['id']}")(
                     next((r for r in self.rooms if r["kind"] == "direct" and r.get("seat") == s["id"]), None)),
                     ft.Icons.SMART_TOY_OUTLINED, t.muted(score_text(s), size=11, tooltip="Benchmark score (tested by code)"))
                 for s in self.seats if not s.get("chair")]
        return [self.side_row("Live map", "map", ft.Icons.HUB_OUTLINED), heading("Chats"), *(rooms or [empty("None yet. Start one with New chat.")]),
                heading("Tasks"), *(board or [empty("Nothing yet. Give them work above.")]),
                *([heading("Crew"), *seats] if seats else [])]

    def seat_rows(self) -> list[ft.Control]:
        """For Agent mode: a one-on-one chat with each colony seat, under the chair's own chats."""
        seats = [s for s in self.seats if not s.get("chair")]
        if self.problem or not seats:
            return []
        rows: list[ft.Control] = [ft.Container(t.muted("Crew", size=11.5),
                                               padding=ft.Padding.only(left=12, top=10, bottom=2))]
        for seat in seats:
            room = next((r for r in self.rooms if r["kind"] == "direct" and r.get("seat") == seat["id"]), None)
            key = f"room:{room['id']}" if room else f"seat:{seat['id']}"
            rows.append(self.side_row(seat["name"].split("@")[0].capitalize(), key, ft.Icons.SMART_TOY_OUTLINED,
                                      t.muted(score_text(seat), size=11, tooltip="Benchmark score (tested by code)")))
        return rows

    # -- Starting things --------------------------------------------------------------------------------

    async def new_colony_chat(self) -> None:
        """A new chat with every seat: an empty conversation with its composer, like a new chat with your agent.
        The first message makes it (and names it)."""
        await self.workspace.show_section("colony:new")

    def new_view(self) -> tuple[str, ft.Control]:
        box = t.field(hint="Ask your colony anything: the members it fits best answer",
                      multiline=True, autofocus=True)

        async def send(_event=None) -> None:
            text = (box.value or "").strip()
            if not text:
                return
            box.value, box.disabled = "", True
            self.page.update()
            try:
                room = await self.call("POST", "/rooms", {"title": text.splitlines()[0][:200]})
                await self.call("POST", f"/rooms/{room['id']}/messages", {"text": text})
            except PairingError as e:
                box.value, box.disabled = text, False
                self.workspace.toast(str(e))
                self.page.update()
                return
            await self.workspace.show_section(f"colony:room:{room['id']}")
        box.on_submit = send
        box.shift_enter = True
        composer = ft.Row([ft.Container(box, expand=True),
                           ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, tooltip="Send (Enter)", bgcolor=t.ACCENT,
                                         icon_color=t.ON_ACCENT, on_click=send)], spacing=8)
        hello = ft.Container(ft.Column([
            ft.Icon(ft.Icons.GROUPS_ROUNDED, size=40, color=t.ACCENT),
            ft.Text("Talk to your colony", size=22, weight=ft.FontWeight.W_600),
            t.muted("The members your message names, or whose roles fit it best, answer. To have them do "
                    "something and check each other's work, use New task.", size=13),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10), alignment=ft.Alignment.CENTER, padding=48)
        return "New chat", self.frame([hello], composer)

    async def new_task(self, from_text: str = "") -> None:
        title = t.field("The task", hint="Write a README for my project", value=from_text.splitlines()[0][:200] if from_text else "")
        detail = t.field("Details (optional)", multiline=True, value=from_text or "")

        async def post(_event=None) -> None:
            if not (title.value or "").strip():
                title.error_text = "Say what the work is"
                self.page.update()
                return
            self.page.pop_dialog()
            try:
                task = await self.call("POST", "/tasks", {"title": title.value.strip()[:200],
                                                          "detail": (detail.value or "").strip()})
            except PairingError as e:
                self.workspace.toast(str(e))
                return
            await self.workspace.show_section(f"colony:task:{task['id']}")

        self.dialog("New task", "The crew is offered it: one takes it on, another reviews the result.",
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
        if kind == "new":
            self.showing = None
            return self.new_view()
        if kind == "map":  # who is doing what right now, and what passes between them (crewmap.py)
            import crewmap
            self.showing = key
            self.follower = follower = object()
            crew = crewmap.CrewMap(self)
            try:
                body = await crew.build()
            except PairingError as e:
                return "Live map", self.frame([t.card(t.muted(str(e)))])
            self.page.run_task(crew.follow, lambda: self.follower is follower and self.showing == "map"
                               and self.workspace.section == "colony:map")
            return "Live map", self.frame([body])
        try:
            if kind == "seat":
                room_id = await self.seat_room(ident)
                key, kind, ident = f"room:{room_id}", "room", str(room_id)
            self.showing = key
            if kind == "room":
                return await self.room_view(int(ident))
            return await self.task_view(int(ident))
        except (PairingError, ValueError) as e:
            return "Colony", self.frame([t.card(t.muted(str(e)))])

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
        text = message["text"]
        copy = (ft.Icons.CONTENT_COPY_ROUNDED, "Copy", lambda: self.workspace.copy_later(text))
        room = message.get("room")
        task = (ft.Icons.ASSIGNMENT_OUTLINED, "Make it a task for the crew", lambda: self.page.run_task(self.new_task, text))
        if author == "you":  # your message: as in the agent chat, a bubble on the right with its actions
            again = [(ft.Icons.EDIT_OUTLINED, "Edit (back into the message box)", lambda: self.edit_into_box(text)),
                     (ft.Icons.REFRESH_ROUNDED, "Send it again", lambda: self.page.run_task(self.resend, room, text))] if room else []
            return transcript.user_message(text, actions=[copy, *again, task])
        name = self.seat_name(author)
        actions = [] if author in ("advisor", "colony") else [
            (ft.Icons.REPLY_ROUNDED, f"Reply to {name}", lambda: self.edit_into_box(f"{name}, ")),
            (ft.Icons.REFRESH_ROUNDED, f"Ask {name} again",
             lambda: self.page.run_task(self.resend, room, f"{name}, please answer that again, another way.")),
            (ft.Icons.CHAT_OUTLINED, f"Talk with {name} alone", lambda: self.page.run_task(self.open_direct, author)),
            task,
            (ft.Icons.THUMB_UP_OUTLINED, "Good answer (goes on their record; only benchmarks set their standing)",
             lambda a=author: self.page.run_task(self.feedback, a, True)),
            (ft.Icons.THUMB_DOWN_OUTLINED, "Poor answer (goes on their record)",
             lambda a=author: self.page.run_task(self.feedback, a, False))]
        answer = transcript.Answer(text, on_copy=self.workspace.copy_later, actions=actions)
        answer.finish()  # code blocks get their header and copy button
        tape = ft.Row([t.badge(self.seat_name(author).upper(), t.WARN if author in ("advisor", "colony") else t.VIOLET),
                       *([t.muted("asks for outside advice", size=11.5)] if kind == "escalation" else []),
                       t.muted(stamp(message.get("at")), size=11)], spacing=8)
        return ft.Column([tape, answer.view], spacing=4)

    def edit_into_box(self, text: str) -> None:
        """Put text in the open chat's message box, for you to change and send."""
        box = getattr(self, "box", None)
        if box is None:
            return
        box.value = text
        self.page.update()
        self.page.run_task(box.focus)

    async def open_direct(self, seat_id: str) -> None:
        room = next((r for r in self.rooms if r["kind"] == "direct" and r.get("seat") == seat_id), None)
        await self.workspace.show_section(f"colony:room:{room['id']}" if room else f"colony:seat:{seat_id}")

    async def resend(self, room_id: int, text: str) -> None:
        try:
            await self.call("POST", f"/rooms/{room_id}/messages", {"text": text})
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        self.workspace.toast("Sent again.")

    async def feedback(self, seat: str, good: bool) -> None:
        try:
            await self.call("POST", "/feedback", {"seat": seat, "kind": "thumbs_up" if good else "thumbs_down"})
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        self.workspace.toast(f"Noted on {self.seat_name(seat)}'s record.")

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
        box = t.field(hint="Message your colony (Enter sends, Shift+Enter for a new line)" if room["kind"] == "colony"
                      else f"Message {self.seat_name(room['seat'])}", multiline=True)
        box.max_lines = 6
        box.shift_enter = True
        self.box = box
        working = transcript.StatusLine()
        listing.data = {"working": working}

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
        actions: list[ft.Control] = [ft.Container(box, expand=True),
                                     ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, tooltip="Send (Enter)", bgcolor=t.ACCENT,
                                                   icon_color=t.ON_ACCENT, on_click=send)]
        if room["kind"] == "colony":
            actions.insert(1, t.icon_button(ft.Icons.HOW_TO_VOTE_OUTLINED, "Ask the crew to vote",
                                            on_click=lambda _e: self.page.run_task(self.new_vote, room_id, listing)))
        self.page.run_task(self.follow_room, room_id, listing, messages[-1]["id"] if messages else 0)
        title = room["title"] if room["kind"] == "colony" else self.seat_name(room.get("seat") or "")
        composer = ft.Column([working.view, ft.Row(actions, spacing=8, vertical_alignment=ft.CrossAxisAlignment.END)],
                             spacing=6, tight=True)
        return title, self.frame([listing], composer)

    async def refresh_room(self, room_id: int, listing: ft.Column) -> None:
        messages = (await self.call("GET", f"/rooms/{room_id}/messages"))["messages"]
        listing.controls = [self.bubble(m) for m in messages]
        listing.data = {**(listing.data if isinstance(listing.data, dict) else {}),
                        "shown": {m["id"] for m in messages}}  # shown already: the follower doesn't add them again
        self.page.update()

    async def follow_room(self, room_id: int, listing: ft.Column, last: int) -> None:
        """New messages as the seats answer, while this room is the one open."""
        self.follower = follower = object()
        key = f"room:{room_id}"
        while self.follower is follower and self.showing == key and self.workspace.section.startswith("colony:"):
            await asyncio.sleep(POLL_SECONDS)
            try:
                fresh = (await self.call("GET", f"/rooms/{room_id}/messages?after={last}"))["messages"]
                busy = (await self.call("GET", f"/activity?since={time.time():.0f}")).get("busy", [])
            except PairingError:
                continue
            if self.follower is not follower or self.showing != key:
                break
            state = listing.data if isinstance(listing.data, dict) else {}
            working = state.get("working")
            answering = [b for b in busy if b.get("doing") in ("answering in a chat", "talking with you")]
            if working is not None:  # who is answering now, and the tool in use: the agent chat's "Working..." line
                if answering:
                    b = answering[0]
                    verb = f"{self.seat_name(b['seat'])} is answering" + (f" · {b['step']}" if b.get("step") else "")
                    if not working.view.visible:
                        working.start(verb)
                    else:
                        working.set_verb(verb)
                elif working.view.visible:
                    working.stop()
            if fresh:
                last = fresh[-1]["id"]
                shown = state.get("shown", set())
                fresh = [m for m in fresh if m["id"] not in shown]  # your own message was shown when you sent it
                if fresh:
                    if listing.controls and isinstance(listing.controls[0], ft.Text):
                        listing.controls.clear()
                    listing.controls.extend(self.bubble(m) for m in fresh)
                    state["shown"] = shown | {m["id"] for m in fresh}
                    listing.data = state
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

        self.dialog("Ask the crew to vote", "Each crew member votes; ballots count by their benchmark scores. "
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
            body.append(t.card(ft.Column([t.muted("RESULT", size=11), ft.Markdown(task["result"], selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)],
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
            body.append(t.muted("The crew is on it. This page follows along.", size=12.5))
            self.page.run_task(self.follow_task, task_id, state, len(task.get("notes") or []))
        return task["title"], self.frame(body, ft.Row(actions, spacing=8) if actions else None)

    async def follow_task(self, task_id: int, state: str, notes: int) -> None:
        self.follower = follower = object()
        key = f"task:{task_id}"
        while self.follower is follower and self.showing == key and self.workspace.section.startswith("colony:"):
            await asyncio.sleep(POLL_SECONDS * 2)
            try:
                task = await self.call("GET", f"/tasks/{task_id}")
            except PairingError:
                continue
            if task["state"] != state or len(task.get("notes") or []) != notes:
                if self.follower is follower and self.showing == key:
                    await self.workspace.show_section(f"colony:{key}")
                return

    async def review(self, task_id: int, verdict: str, reason: str) -> None:
        if verdict == "reject" and not reason.strip():
            self.workspace.toast("Say what's wrong, so they can fix it.")
            return
        try:
            await self.call("POST", f"/tasks/{task_id}/review", {"verdict": verdict, "text": reason.strip()})
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        await self.workspace.show_section(f"colony:task:{task_id}")

    async def reopen(self, task_id: int) -> None:
        try:
            await self.call("POST", f"/tasks/{task_id}/reopen")
        except PairingError as e:
            self.workspace.toast(str(e))
            return
        await self.workspace.show_section(f"colony:task:{task_id}")
