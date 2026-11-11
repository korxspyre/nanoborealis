# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Mission control, Providers: where the free inference comes from.

The NanoBorealis computer runs a router (nanoborealis-router): the crew asks it, and it picks whichever free
provider has room right now, moving on when one is used up for the day. This page adds each provider's key and
shows what is left today.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import flet as ft

import admin
import agent_settings
import theme as t
from agent_link import LinkError
from pairing import PairingError

if TYPE_CHECKING:
    from control import ControlCenter

TITLE = "Providers"
SUBTITLE = ("Free inference from several providers at once. Your crew asks one router; it picks whoever has room, "
            "and moves on when one is used up for the day.")


def resting(seconds: int, why: str) -> str:
    minutes = max(1, seconds // 60)
    return (f"Resting {minutes // 60}h {minutes % 60}m: {why}" if minutes >= 60 else f"Resting {minutes}m: {why}")


async def build(center: "ControlCenter") -> ft.Control:
    page, machine = center.page, center.app.machine
    try:
        data = await asyncio.to_thread(admin.providers, machine)
    except PairingError as e:
        return center.frame(TITLE, SUBTITLE, [t.card(t.muted(f"{e} (this needs the computer's latest update)"))])

    async def change(name: str, field: str, value: Any) -> None:
        try:
            await asyncio.to_thread(admin.provider_set, machine, name, field, value)
        except PairingError as e:
            center.toast(str(e))
        await center.reload("providers")

    def add_key(name: str, label: str, where: str) -> None:
        box = ft.TextField(password=True, can_reveal_password=True, hint_text=f"Paste your {label} key", autofocus=True,
                           border_radius=t.RADIUS, border_color=t.LINE_HI, focused_border_color=t.ACCENT, width=460)

        async def save(_e=None) -> None:
            page.pop_dialog()
            center.toast(f"Saving the {label} key...")
            try:
                answer = await asyncio.to_thread(admin.provider_key, machine, name, box.value or "")
            except PairingError as e:
                center.toast(str(e))
                return
            moved = answer.get("moved") or []
            center.toast(f"{label} is in." + (f" Your crew ({', '.join(moved)}) now uses the router." if moved else ""))
            await center.reload("providers")
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"{label} key"),
            content=ft.Column([box, t.muted(f"Get one at {where}. It is kept on your NanoBorealis computer, readable only "
                                            "by the router." + (" Your agent uses the same key, and restarts with it."
                                                                if name == "openrouter" else " Leave it empty to remove the key."),
                                    size=12)],
                              tight=True, spacing=10, width=460),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: page.pop_dialog()), t.primary_button("Save", on_click=save)]))

    def limit(name: str, p: dict) -> None:
        box = ft.TextField(value=str(p["daily"]), label="Requests a day (0: no limit)", border_radius=t.RADIUS,
                           border_color=t.LINE_HI, focused_border_color=t.ACCENT, width=420, autofocus=True)

        async def save(_e=None) -> None:
            page.pop_dialog()
            if (box.value or "").strip().isdigit():
                await change(name, "daily", int(box.value))
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"{p['label']}: daily limit"),
            content=ft.Column([box, t.muted("What the router allows itself before it stops using this provider for the day. "
                                            "Set it to what your account's own limits page says.", size=12)],
                              tight=True, spacing=10, width=420),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: page.pop_dialog()), t.primary_button("Save", on_click=save)]))

    try:
        settings = await center.load_settings()
        guard = agent_settings.free_only(settings)
    except LinkError:
        settings, guard = None, True

    async def toggle_guard(event) -> None:
        try:
            await agent_settings.set_free_only(center.app.link, bool(event.control.value), center.settings)
        except LinkError as e:
            center.toast(str(e))
        await center.reload("providers")

    cards: list[ft.Control] = []
    total_left, with_keys = 0, 0
    for name, p in data["providers"].items():
        left = max(0, p["daily"] - p["used"]) if p["daily"] else None
        if p["has_key"]:
            with_keys += 1
            if p["enabled"] and left:
                total_left += left
        if not p["has_key"]:
            state, color = "No key yet", t.FAINT
        elif not p["enabled"]:
            state, color = "Off", t.FAINT
        elif p["resting"]:
            state, color = resting(p["resting"], p["resting_why"]), t.BAD
        elif left == 0:
            state, color = "Used up for today", t.BAD
        else:
            state, color = "Ready", t.GOOD
        head = ft.Row([
            ft.Icon(ft.Icons.CIRCLE, size=9, color=color),
            ft.Text(p["label"], size=15, weight=ft.FontWeight.W_600), t.muted(state, size=12.5, expand=True),
            *([ft.Switch(value=p["enabled"], active_color=t.ACCENT, scale=0.8, tooltip="Use this provider",
                         on_change=lambda e, n=name: page.run_task(change, n, "enabled", bool(e.control.value)))]
              if p["has_key"] else []),
            t.outline_button("Change key" if p["has_key"] else "Add key",
                             on_click=lambda _e, n=name, lab=p["label"], w=p["where"]: add_key(n, lab, w)),
        ], spacing=10)
        if p["has_key"]:
            models = " · ".join(m for m in (p["smart"], p["fast"]) if m) or "no models picked yet"
            only = f" · only requests under {p['max_request']:,} tokens" if p["max_request"] else ""
            body = [
                ft.ProgressBar(value=min(1.0, p["used"] / p["daily"]) if p["daily"] else 0, color=t.ACCENT, bgcolor=t.LINE,
                               height=5, border_radius=3),
                ft.Row([t.muted(f"{p['used']:,} of {p['daily']:,} requests today" if p["daily"] else f"{p['used']:,} requests today",
                                size=12.5),
                        t.muted(f"about {p['tokens'] / 1e6:.1f}M tokens", size=12.5, expand=True),
                        t.quiet_button("Daily limit", on_click=lambda _e, n=name, x=p: limit(n, x))], spacing=14),
                t.muted(models + only, size=12),
            ]
        else:
            body = [t.muted(f"Free key from {p['where']}.", size=12.5)]
        cards.append(t.card(ft.Column([head, *body], spacing=8)))
    if data.get("moved"):
        center.toast(f"The router took your agent's OpenRouter key. Your crew ({', '.join(data['moved'])}) now uses it.")
    summary = (f"{total_left:,} requests left today across {with_keys} provider{'s' if with_keys != 1 else ''}. "
               "Resets at midnight UTC." if with_keys else
               "Add a key to start. Each provider you add is more free requests a day for your crew.")
    free = t.card(ft.Row([
        ft.Column([ft.Text("Free models only", size=15, weight=ft.FontWeight.W_600),
                   t.muted("Nothing your agent or crew asks for can cost money: OpenRouter refuses paid models, and the "
                           "other providers are their free plans. Turn off only if you want to pay for a model.", size=12.5)],
                  spacing=4, expand=True),
        ft.Switch(value=guard, active_color=t.ACCENT, disabled=settings is None, on_change=toggle_guard)]))
    return center.frame(TITLE, SUBTITLE, [t.muted(summary, size=13), *cards, free])
