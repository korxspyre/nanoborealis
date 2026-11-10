# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Hands in the app: lend this device's hands to the agents, and answer what they ask.

Lending runs hands.serve in the background while the app is open: the agents' jobs come from the paired computer,
each is checked against the request it serves and your rules (permissions.py), and what's allowed runs here, as you.
A job outside its request's folders waits for you: every app paired by an administrator (your phone too) shows it
as a card, and the first answer wins.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import TYPE_CHECKING, Any

import flet as ft

import admin
import checkpoints
import hands
import theme as t
import transcript
from pairing import PairingError
from permissions import Policy

if TYPE_CHECKING:
    from main import NanoBorealisApp

PREF_HANDS = "nanoborealis.hands"  # "on" while you lend this device's hands
ASK_EVERY = 4.0  # seconds between looks for questions from your devices
MODES = [("scoped", "Scoped: works inside the request's folders; asks about anything else, or anything permanent"),
         ("ask", "Ask: asks before anything that changes something"),
         ("plan", "Plan: only reads, nothing changes"),
         ("trust", "Trust: works in what you share; asks only before what can't be undone")]


class HandsUI:
    def __init__(self, app: "NanoBorealisApp"):
        self.app = app
        self.page = app.page
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.status = ""
        self.shown: set[str] = set()  # questions already on screen
        self.watching = False

    # -- Lending -------------------------------------------------------------------------------------

    @property
    def lending(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    async def resume(self) -> None:
        """On connecting: answer questions from now on, and lend again if you were lending."""
        if not self.watching:
            self.watching = True
            self.page.run_task(self.watch_asks)
        if await self.app.pref_get(PREF_HANDS) == "on" and not self.lending:
            self.start()

    def start(self) -> None:
        machine = self.app.machine
        if machine is None or self.lending:
            return
        self.stop_event = threading.Event()
        stop = self.stop_event

        def progress(line: str) -> None:
            self.status = line

        def run() -> None:
            hands.serve(machine, approve=hands.ask_owner(machine, stop.is_set), stop=stop.is_set, progress=progress)
        self.thread = threading.Thread(target=run, name="nanoborealis-hands", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread = None
        self.watching = False

    async def open_dialog(self) -> None:
        policy = Policy.load(hands.policy_path())
        folders = ft.TextField(value="\n".join(policy.roots), multiline=True, min_lines=2, max_lines=5,
                               hint_text=os.path.expanduser("~/projects"), label="Folders you share (one per line)",
                               border_radius=t.RADIUS, border_color=t.LINE_HI, focused_border_color=t.ACCENT)
        mode = ft.Dropdown(value=policy.mode, options=[ft.dropdown.Option(k, text) for k, text in MODES], label="How freely",
                           border_radius=t.RADIUS)

        async def save(on: bool) -> None:
            policy.roots = [os.path.abspath(os.path.expanduser(line.strip())) for line in (folders.value or "").splitlines()
                            if line.strip()]
            policy.mode = mode.value or "scoped"
            policy.save(hands.policy_path())
            await self.app.pref_set(PREF_HANDS, "on" if on else None)
            self.page.pop_dialog()
            if on:
                self.stop()
                self.start()
                if not self.watching:
                    self.watching = True
                    self.page.run_task(self.watch_asks)
                self.app.workspace.toast("This device lends its hands while the app is open.") if self.app.workspace else None
            else:
                self.stop()
                self.app.workspace.toast("This device stopped lending its hands.") if self.app.workspace else None
            self.app.refresh_sidebar()

        bullets = ft.Column([
            t.muted("Agents can then work on this device the way you would at it: as you, with your files, tools and network.",
                    size=13),
            t.muted("Every job names the request it serves and the folders it's about. Inside those it runs; outside them, "
                    "it waits for you to answer here or on your phone. A few things never run (formatting disks, "
                    "deleting your home, piping downloads into a shell).", size=13),
            t.muted(f"Every job and every answer is written to {hands.home() / 'hands.log'}.", size=12),
        ], spacing=8)
        undo = self.undo_list()
        actions = [t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog())]
        if self.lending:
            actions.append(t.outline_button("Stop lending", on_click=lambda _e: self.page.run_task(save, False)))
        actions.append(t.primary_button("Save and lend" if not self.lending else "Save",
                                        on_click=lambda _e: self.page.run_task(save, True)))
        self.page.show_dialog(ft.AlertDialog(title=ft.Text("Lend this device's hands"),
                                             content=ft.Column([bullets, folders, mode, undo], tight=True, spacing=12,
                                                               width=520, scroll=ft.ScrollMode.AUTO),
                                             actions=actions))

    def undo_list(self) -> ft.Control:
        """The latest checkpoints agents' jobs took here, each with an Undo that puts its folders back."""
        found = checkpoints.entries()[:8]
        if not found:
            return t.muted("Nothing to undo yet: before each job that may change something, its folders are copied, "
                           "so it can be undone here.", size=12)

        async def undo(entry: dict) -> None:
            self.page.pop_dialog()
            try:
                done = await asyncio.to_thread(checkpoints.restore, entry["id"])
            except (OSError, ValueError, KeyError) as e:
                message = f"Couldn't undo it: {e}"
            else:
                message = (f"Put back as before: {', '.join(done)}. What was there is kept beside it (.before-undo)."
                           if done else "Nothing was copied for that job, so there's nothing to put back.")
            if self.app.workspace:
                self.app.workspace.toast(message)
        rows: list[ft.Control] = [t.muted("UNDO AN AGENT'S JOB", size=11)]
        for entry in found:
            when = time.strftime("%b %d %H:%M", time.localtime(entry.get("at") or 0))
            rows.append(ft.Row([
                ft.Column([t.mono(entry.get("command", "")[:70], size=12), t.muted(f"{when} · {', '.join(entry.get('copied') or []) or 'nothing copied'}",
                                                                              size=11)], spacing=0, expand=True),
                t.quiet_button("Undo", icon=ft.Icons.UNDO_ROUNDED, on_click=lambda _e, e=entry: self.page.run_task(undo, e)),
            ]))
        return ft.Column(rows, spacing=4)

    # -- Answering -----------------------------------------------------------------------------------

    async def watch_asks(self) -> None:
        """Look for questions from your devices every few seconds while the app is connected."""
        while self.watching and self.app.machine is not None:
            try:
                asks = await asyncio.to_thread(admin.hands_asks, self.app.machine)
            except (PairingError, OSError, ValueError, KeyError):
                asks = []
            for ask in asks:
                if ask["id"] not in self.shown:
                    self.shown.add(ask["id"])
                    self.show_ask(ask)
            await asyncio.sleep(ASK_EVERY)

    def ask_parts(self, ask: dict[str, Any]) -> tuple[str, list[ft.Control], str, str]:
        """(title, what to show, the "always" label, the "yes" label) for a question."""
        own = ask.get("device") == "agent"  # the agent's own tools, in a chat's Ask first or Accept edits mode
        detail = ask.get("detail") or {}
        if own and detail.get("kind") == "edit" and detail.get("edits"):
            edits = detail["edits"]
            names = ", ".join(str(e.get("path", "")).rsplit("/", 1)[-1] for e in edits)
            body: list[ft.Control] = []
            for e in edits:
                body.append(t.mono(str(e.get("path", "")), size=12, color=t.MUTED))
                body.append(transcript.diff_view(str(e.get("old") or ""), str(e.get("new") or "")))
            return f"Make this edit to {names}?", body, "Always allow edits in this chat", "Yes, edit"
        program = (detail.get("program") or (ask["command"].split()[0] if ask["command"].split() else "it"))
        body = [ft.Container(t.mono(ask["command"], selectable=True, size=12.5), padding=10, bgcolor=t.SIDEBAR,
                             border_radius=t.RADIUS)]
        if own:
            return "Run this command?", body, f"Always allow {program} in this chat", "Yes, run it"
        return f"Run this on {ask['device_name']}?", body, f"Always allow {program} there", "Yes, run it"

    def show_ask(self, ask: dict[str, Any]) -> None:
        """In the chat it belongs to, when that chat is open (as Claude Code asks, in place); else as a dialog."""
        ws = self.app.workspace
        title, body, always, yes = self.ask_parts(ask)
        why = [t.muted(f"Why: {ask['reason']}", size=12)]
        if ask.get("request"):
            why.append(t.muted(f"For: {ask['request'][:200]}", size=12))
        if ask.get("device") != "agent":
            why.append(t.muted(f"In: {ask['cwd']}", size=12))
        inline = (ask.get("device") == "agent" and ws is not None and ask.get("chat") and ask.get("chat") == ws.chat_id
                  and ws.section == "chats")
        card: ft.Container | None = None

        async def answer(choice: str) -> None:
            if inline and card is not None:
                said = {"once": "Allowed once", "always": "Allowed from now on in this chat", "deny": "Not allowed"}[choice]
                card.content = ft.Row([ft.Icon(ft.Icons.CHECK_ROUNDED if choice != "deny" else ft.Icons.BLOCK_ROUNDED,
                                               size=15, color=t.GOOD if choice != "deny" else t.BAD),
                                       t.muted(f"{title} {said}.", size=12.5)], spacing=8)
                self.page.update()
            else:
                self.page.pop_dialog()
            try:
                await asyncio.to_thread(admin.hands_answer, self.app.machine, ask["id"], choice)
            except PairingError as e:  # someone answered first, or it ran out of time
                if ws:
                    ws.toast(str(e))
            if inline and choice == "deny" and ws is not None:
                ws.composer.hint_text = "What should it do instead?"
                self.page.update()
                await ws.composer.focus()

        buttons = [t.primary_button(yes, on_click=lambda _e: self.page.run_task(answer, "once")),
                   t.outline_button(always, on_click=lambda _e: self.page.run_task(answer, "always")),
                   t.quiet_button("No, and tell it what to do differently" if inline else "No",
                                  on_click=lambda _e: self.page.run_task(answer, "deny"))]
        if inline:
            card = ft.Container(ft.Column([ft.Text(title, size=14.5, weight=ft.FontWeight.W_600), *body, *why,
                                           ft.Row(buttons, spacing=6, wrap=True)], spacing=8),
                                padding=14, border_radius=t.RADIUS_LG, bgcolor=t.PANEL,
                                border=ft.Border.all(1, t.ACCENT))
            ws.add(card)
            ws.page.update()
            ws.scroll_to_end()
            return
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(title),
            content=ft.Column([*body, *why, t.muted("Unanswered in 10 minutes, it's a no.", size=11.5)],
                              tight=True, spacing=8, width=620, scroll=ft.ScrollMode.AUTO),
            actions=list(reversed(buttons))))
