# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The screens for codefeatures.py: the status bar under the composer, the plan card, the rewind dialog and the
crew strip. Small builders that return Flet controls; workspace.py decides when to show them."""

from __future__ import annotations

import time
from typing import Callable

import flet as ft

import theme as t

TONES = {"ok": (t.GOOD, None), "warn": (t.WARN, None), "bad": (t.BAD, None), "accent": (t.ACCENT_HI, t.ACCENT_SOFT), "muted": (t.MUTED, None)}


def status_bar(segments: list[dict]) -> ft.Control:
    """One quiet line of small chips: mode, model, where it runs, context, changes, branch, cost, pool, crew."""
    chips = []
    for seg in segments:
        color, background = TONES.get(seg.get("tone", "muted"), TONES["muted"])
        chips.append(t.chip(seg["text"], color=color, bgcolor=background or ft.Colors.TRANSPARENT, tooltip=seg.get("tip"), size=11.5))
    return ft.Row(chips, spacing=2, run_spacing=0, wrap=True, alignment=ft.MainAxisAlignment.CENTER)


def plan_card(on_approve: Callable, on_revise: Callable) -> ft.Control:
    """Under a plan-mode answer: go ahead with it, or change it."""
    return t.card(ft.Row([
        ft.Icon(ft.Icons.CHECKLIST_RTL_ROUNDED, size=18, color=t.ACCENT),
        ft.Column([t.text("Ready to go ahead with this plan?", size=13.5, weight=ft.FontWeight.W_600),
                   t.muted("Approving turns plan mode off and asks the agent to do it, step by step.", size=12)], spacing=2, expand=True),
        t.quiet_button("Revise", on_click=on_revise),
        t.primary_button("Approve", on_click=on_approve, icon=ft.Icons.CHECK_ROUNDED),
    ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=12)


def when(stamp: int) -> str:
    ago = max(0, int(time.time()) - stamp)
    return ("just now" if ago < 60 else f"{ago // 60} min ago" if ago < 3600 else f"{ago // 3600} h ago" if ago < 86400
            else time.strftime("%d %b %H:%M", time.localtime(stamp)))


def rewind_dialog(items: list[dict], on_restore: Callable[[dict], None], on_close: Callable, note: str = "",
                  on_restore_as: Callable[[dict, str], None] | None = None) -> ft.AlertDialog:
    """Every message of this chat, newest first, as Claude Code's /rewind: go back to just before it in the
    conversation (a new chat holding everything before it; this one stays), in the project's files (when a
    checkpoint was saved for it), or both. An item has "label", "at", and "sha" (files) and/or "user_index"."""
    rows: list[ft.Control] = []
    for item in reversed(items):
        buttons: list[ft.Control] = []
        if on_restore_as is not None and item.get("user_index") is not None:
            buttons.append(t.quiet_button("Conversation", on_click=lambda _e, i=item: on_restore_as(i, "conversation")))
        if item.get("sha"):
            buttons.append(t.quiet_button("Files", on_click=lambda _e, i=item: (on_restore_as or (lambda x, _w: on_restore(x)))(i, "files")))
        if on_restore_as is not None and item.get("sha") and item.get("user_index") is not None:
            buttons.append(t.outline_button("Both", on_click=lambda _e, i=item: on_restore_as(i, "both")))
        if on_restore_as is None and item.get("sha"):
            buttons = [t.outline_button("Restore files", on_click=lambda _e, i=item: on_restore(i))]
        rows.append(ft.Container(
            ft.Row([ft.Column([t.text(item["label"] or "(a message)", size=13.5, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                               t.muted(when(item["at"]) if item.get("at") else "", size=11.5)], spacing=1, expand=True),
                    *buttons],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),  # wrap with an expanding child can't lay out
            padding=ft.Padding.symmetric(horizontal=8, vertical=8), border=ft.Border.only(bottom=ft.BorderSide(1, t.LINE))))
    if not rows:
        rows.append(ft.Container(t.muted(note or "No checkpoints in this chat yet. One is saved before each message you send "
                                         "while the chat has a project folder."), padding=16))
    return ft.AlertDialog(
        title=t.text("Rewind", size=16, weight=ft.FontWeight.W_600),
        content=ft.Container(ft.Column([t.muted("Go back to just before a message. Conversation: a new chat with everything "
                                                "before it (this chat stays as it is). Files: the project's files as they were "
                                                "then; the present is saved first, so you can undo it. Commands' effects "
                                                "outside the project aren't undone (snapshots cover your homes).", size=12),
                                        ft.Column(rows, scroll=ft.ScrollMode.AUTO, spacing=0, height=min(360, 64 * max(1, len(rows))))],
                                       spacing=10, tight=True), width=520),
        actions=[t.quiet_button("Close", on_click=on_close)], bgcolor=t.PANEL)


def crew_strip(chips: list[dict], on_open: Callable[[dict], None]) -> ft.Control:
    """The colony's seats above the composer: who leads, who is here, how far each can be trusted by its benchmarks."""
    out: list[ft.Control] = []
    for chip in chips:
        lead = chip["head"]
        color = t.ACCENT_HI if lead else t.GOOD if chip["state"] in ("here", "ready") else t.FAINT
        detail = f"{chip['name']} · {chip['benchmark']}" if chip.get("benchmark") else chip["name"]
        out.append(t.chip(("★ " if lead else "") + chip["name"], color=color, bgcolor=t.ACCENT_SOFT if lead else t.RAISED,
                          tooltip=f"{detail}\n{chip['model']}\nbenchmark {chip['benchmark']}\n{chip['state']}{' · head agent' if lead else ''}",
                          on_click=lambda _e, c=chip: on_open(c), size=11.5))
    return ft.Row(out, spacing=6, run_spacing=4, wrap=True)
