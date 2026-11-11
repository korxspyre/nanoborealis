# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The live map of the crew: who is working on what right now, and what passes between them.

Every crew member is a card, in a column for its sector, with you at the top. A member at work lights up and says
what it is doing (the task, and the tool in use: a search, a command, a file). A line is drawn for a while between
two members when one sends the other a message, hands work in, or reviews it. Under the map, the same things as a
list, newest first. It follows along by asking the colony for /activity every couple of seconds.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import flet as ft
import flet.canvas as cv

import theme as t
from pairing import PairingError

if TYPE_CHECKING:
    from colony import ColonyView

WIDTH, NODE_W, NODE_H, ROW = 880, 164, 62, 80
TOP = 104           # where the sectors' first cards sit
LINE_SECONDS = 120  # how long a line between two members stays, fading
EVERY = 2.0
VERBS = {"claim": "took", "submit": "handed in", "accept": "accepted", "reject": "sent back", "update": "noted on",
         "reopen": "reopened", "fail": "gave up on", "done": "finished"}


def short(seat_id: str) -> str:
    return "You" if seat_id in ("you", "admin") else seat_id.split("@")[0].split("-", 1)[-1].capitalize()


class CrewMap:
    def __init__(self, colony: "ColonyView"):
        self.colony = colony
        self.page = colony.page
        self.where: dict[str, tuple[float, float]] = {}  # seat id -> the middle of its card
        self.cards: dict[str, dict[str, Any]] = {}
        self.ties: list[dict] = []
        self.canvas = cv.Canvas(width=WIDTH, height=10)
        self.feed = ft.Column(spacing=6)
        self.summary = t.muted("", size=12.5)
        self.height = 10

    async def build(self) -> ft.Control:
        colony = self.colony
        rigs = (await colony.call("GET", "/rigs"))["rigs"]
        details = await asyncio.gather(*(colony.call("GET", f"/rigs/{r['name']}") for r in rigs))
        seats = [s for d in details for s in d["seats"]]
        self.ties = [e for d in details for e in d["edges"]]
        if not seats:
            return t.card(t.muted("No crew yet. Start a colony in Mission control, Crew."))
        sectors: dict[str, list[dict]] = {}
        for seat in seats:
            sectors.setdefault(seat.get("pod") or "crew", []).append(seat)
        column = WIDTH / len(sectors)
        placed: list[ft.Control] = []
        self.where["you"] = (WIDTH / 2, 30)
        placed.append(self.card("you", "You", "ask, give tasks, review", WIDTH / 2, 30))
        for i, (sector, members) in enumerate(sectors.items()):
            x = column * i + column / 2
            placed.append(ft.Container(t.muted(sector.upper(), size=10.5, weight=ft.FontWeight.W_600), left=x - NODE_W / 2,
                                       top=TOP - 22, width=NODE_W, alignment=ft.Alignment.CENTER))
            for j, seat in enumerate(members):
                y = TOP + NODE_H / 2 + j * ROW
                self.where[seat["id"]] = (x, y)
                role = (seat.get("role") or "").split(":")[0].strip()
                placed.append(self.card(seat["id"], short(seat["id"]), role, x, y))
        self.height = int(TOP + max(len(m) for m in sectors.values()) * ROW + 8)
        self.canvas.height = self.height
        stack = ft.Stack([self.canvas, *placed], width=WIDTH, height=self.height)
        legend = ft.Row([self.key(t.ACCENT, "at work"), self.key(t.VIOLET, "just sent or handed over"),
                         self.key(t.LINE_HI, "who reviews or delegates to whom")], spacing=18)
        await self.refresh()
        return ft.Column([
            ft.Row([ft.Text("Live map", size=20, weight=ft.FontWeight.W_600), ft.Container(self.summary, expand=True)],
                   spacing=14, vertical_alignment=ft.CrossAxisAlignment.END),
            t.card(ft.Column([ft.Row([stack], scroll=ft.ScrollMode.AUTO), legend], spacing=10)),
            t.muted("WHAT HAPPENED", size=10.5, weight=ft.FontWeight.W_600), self.feed,
        ], spacing=12)

    def key(self, color: str, label: str) -> ft.Control:
        return ft.Row([ft.Container(width=18, height=3, bgcolor=color, border_radius=2), t.muted(label, size=11.5)], spacing=6)

    def card(self, seat_id: str, name: str, role: str, x: float, y: float) -> ft.Control:
        title = ft.Text(name, size=13.5, weight=ft.FontWeight.W_600, no_wrap=True)
        line = t.muted(role, size=11.5, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        ring = ft.ProgressRing(width=11, height=11, stroke_width=2, color=t.ACCENT, visible=False)
        box = ft.Container(ft.Column([ft.Row([title, ring], spacing=8, alignment=ft.MainAxisAlignment.SPACE_BETWEEN), line],
                                     spacing=2, tight=True),
                           left=x - NODE_W / 2, top=y - NODE_H / 2, width=NODE_W, height=NODE_H, bgcolor=t.RAISED,
                           border=ft.Border.all(1, t.LINE), border_radius=12,
                           padding=ft.Padding.symmetric(horizontal=12, vertical=9))
        self.cards[seat_id] = {"box": box, "line": line, "ring": ring, "role": role}
        return box

    async def refresh(self) -> None:
        try:
            data = await self.colony.call("GET", f"/activity?since={time.time() - 3600:.0f}")
        except PairingError:
            self.summary.value = "Waiting for the colony's computer (this needs its latest update)."
            self.canvas.shapes = [self.line(e["src"], e["dst"], t.LINE_HI, 1, 0.55) for e in self.ties
                                  if e["src"] in self.where and e["dst"] in self.where]
            return
        now = data["now"]
        busy = {b["seat"]: b for b in data["busy"]}
        for seat_id, card in self.cards.items():
            b = busy.get(seat_id)
            card["ring"].visible = b is not None
            card["box"].border = ft.Border.all(1.5 if b else 1, t.ACCENT if b else t.LINE)
            card["box"].bgcolor = t.ACCENT_SOFT if b else t.RAISED
            card["box"].tooltip = None
            if b:
                step = b.get("step") or b["doing"]
                card["line"].value = step
                card["line"].color = t.TEXT
                card["box"].tooltip = f"{b['doing']} for {int(now - b['since'])}s" + (f"\n{b['step']}" if b.get("step") else "")
            else:
                card["line"].value, card["line"].color = card["role"], t.MUTED
        shapes: list[Any] = []
        for tie in self.ties:  # who reviews or delegates to whom: always there, faint
            if tie["src"] in self.where and tie["dst"] in self.where:
                shapes.append(self.line(tie["src"], tie["dst"], t.LINE_HI, 1, 0.55))
        for event in data["events"]:  # what just passed between two members: bright, fading
            age = now - event["at"]
            src, dst = event["src"], event.get("dst")
            if dst and age < LINE_SECONDS and src in self.where and dst in self.where:
                shapes.append(self.line(src, dst, t.VIOLET, 2.5, max(0.2, 1 - age / LINE_SECONDS)))
                x, y = self.where[dst]
                shapes.append(cv.Circle(x, y - NODE_H / 2, 4, paint=ft.Paint(color=ft.Colors.with_opacity(
                    max(0.3, 1 - age / LINE_SECONDS), t.VIOLET))))
        self.canvas.shapes = shapes
        working = [f"{short(s)} is {b['doing']}" for s, b in busy.items()]
        self.summary.value = " · ".join(working) if working else "Nobody is working right now."
        self.feed.controls = [self.feed_row(e) for e in reversed(data["events"][-40:])] or [
            t.muted("Nothing in the last hour. Give the crew a task and watch it move.", size=12.5)]

    def line(self, src: str, dst: str, color: str, width: float, opacity: float) -> Any:
        (x1, y1), (x2, y2) = self.where[src], self.where[dst]
        return cv.Line(x1, y1, x2, y2, paint=ft.Paint(color=ft.Colors.with_opacity(opacity, color), stroke_width=width,
                                                       stroke_cap=ft.StrokeCap.ROUND))

    def feed_row(self, event: dict) -> ft.Control:
        kind, src, dst, task = event["kind"], short(event["src"]), event.get("dst"), event.get("task")
        if kind == "message":
            what, icon = f"→ {short(dst or 'everyone')}", ft.Icons.MAIL_OUTLINE_ROUNDED
        elif kind == "say":
            what, icon = "said in a chat", ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED
        else:
            what = f"{VERBS.get(kind, kind)} task {task}" + (f" ({short(dst)}'s)" if dst else "")
            icon = {"accept": ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, "reject": ft.Icons.UNDO_ROUNDED,
                    "submit": ft.Icons.UPLOAD_ROUNDED}.get(kind, ft.Icons.TASK_ALT_ROUNDED)
        return ft.Row([
            t.muted(datetime.fromtimestamp(event["at"]).strftime("%H:%M"), size=11.5, width=38),
            ft.Icon(icon, size=14, color=t.BAD if kind == "reject" else t.GOOD if kind == "accept" else t.MUTED),
            ft.Text(src, size=12.5, weight=ft.FontWeight.W_600), t.muted(what, size=12.5),
            t.muted(" ".join(str(event.get("text") or "").split())[:160], size=12.5, expand=True, no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(event.get("text") or "")[:600] or None),
        ], spacing=8, on_click=None)

    async def follow(self, still_showing) -> None:
        while still_showing():
            await asyncio.sleep(EVERY)
            if not still_showing():
                return
            await self.refresh()
            try:
                self.page.update()
            except Exception:
                return
