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
from typing import TYPE_CHECKING, Any

import flet as ft

import admin
import hands
import theme as t
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
        actions = [t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog())]
        if self.lending:
            actions.append(t.outline_button("Stop lending", on_click=lambda _e: self.page.run_task(save, False)))
        actions.append(t.primary_button("Save and lend" if not self.lending else "Save",
                                        on_click=lambda _e: self.page.run_task(save, True)))
        self.page.show_dialog(ft.AlertDialog(title=ft.Text("Lend this device's hands"),
                                             content=ft.Column([bullets, folders, mode], tight=True, spacing=12, width=520),
                                             actions=actions))

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

    def show_ask(self, ask: dict[str, Any]) -> None:
        async def answer(choice: str) -> None:
            self.page.pop_dialog()
            try:
                await asyncio.to_thread(admin.hands_answer, self.app.machine, ask["id"], choice)
            except PairingError as e:  # someone answered first, or it ran out of time
                if self.app.workspace:
                    self.app.workspace.toast(str(e))

        program = ask["command"].split()[0] if ask["command"].split() else "it"
        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f"An agent asks to run this on {ask['device_name']}"),
            content=ft.Column([
                t.muted(f"Why it's asking: {ask['reason']}", size=12.5),
                t.muted(f"For your request: {ask['request'] or '(none given)'}", size=12.5),
                t.muted(f"In: {ask['cwd']}", size=12.5),
                ft.Container(t.mono(ask["command"], selectable=True, size=12.5), padding=10, bgcolor=t.SIDEBAR,
                             border_radius=t.RADIUS),
                t.muted("Unanswered in 10 minutes, it's a no.", size=11.5),
            ], tight=True, spacing=8, width=560),
            actions=[t.quiet_button("Don't run it", on_click=lambda _e: self.page.run_task(answer, "deny")),
                     t.outline_button(f"Always allow {program} here", on_click=lambda _e: self.page.run_task(answer, "always")),
                     t.primary_button("Run it once", on_click=lambda _e: self.page.run_task(answer, "once"))],
        ))
