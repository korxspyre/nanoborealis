# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Choosing the agent's model from everything on OpenRouter.

A search box over the whole catalog, with "Free only" on by default: free models cost nothing, and
every other one shows its price before it can be chosen, and asks once more. Models that can't use
tools are hidden unless asked for, because the agent can't work without them.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import flet as ft

import models_catalog
import theme as t

Choose = Callable[[models_catalog.Model], Awaitable[None]]


class ModelPicker:
    """A dialog: search, filter, pick. `on_choose` gets the chosen model (already confirmed if paid)."""

    def __init__(self, page: ft.Page, catalog: models_catalog.Catalog, current: str | None, on_choose: Choose,
                 title: str = "Choose a model", note: str | None = None):
        self.page, self.catalog, self.current, self.on_choose = page, catalog, current, on_choose
        self.query = ft.TextField(
            hint_text="Search every model on OpenRouter", autofocus=True, dense=True, text_size=14,
            prefix_icon=ft.Icons.SEARCH_ROUNDED, border_radius=t.RADIUS, border_color=t.LINE_HI,
            focused_border_color=t.ACCENT, bgcolor=t.BG, on_change=self.refresh,
        )
        self.free_only = ft.Switch(value=True, label="Free only", on_change=self.refresh, active_color=t.ACCENT,
                                   label_text_style=ft.TextStyle(size=13, color=t.TEXT))
        self.tools_only = ft.Switch(value=True, label="Can use tools", on_change=self.refresh, active_color=t.ACCENT,
                                    label_text_style=ft.TextStyle(size=13, color=t.TEXT),
                                    tooltip="The agent works through tools; models without them can't drive it")
        self.sort = ft.Dropdown(
            value="newest", dense=True, width=150, text_size=13, border_radius=t.RADIUS, border_color=t.LINE_HI,
            options=[ft.DropdownOption("newest", "Newest"), ft.DropdownOption("context", "Longest context"),
                     ft.DropdownOption("price", "Cheapest"), ft.DropdownOption("name", "Name")],
            on_select=self.refresh,
        )
        self.count = t.muted("")
        self.results = ft.ListView(spacing=2, expand=True)
        self.status = ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2),
                              t.muted("Loading OpenRouter's models...")], spacing=10)
        header = [t.muted(note, size=13)] if note else []
        self.dialog = ft.AlertDialog(
            modal=False, bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text(title, size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=640, height=min(560, (page.height or 800) - 180), content=ft.Column([
                *header,
                self.query,
                ft.Row([self.free_only, self.tools_only], spacing=16),
                ft.Row([ft.Container(self.count, expand=True), self.sort],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.status,
                self.results,
            ], spacing=10)),
            actions=[t.quiet_button("Close", on_click=self.close)],
        )

    async def open(self) -> None:
        self.page.show_dialog(self.dialog)
        try:
            await asyncio.to_thread(self.catalog.load)
        except Exception as e:  # offline
            self.status.controls = [t.muted(f"Couldn't reach OpenRouter: {e}")]
            self.page.update()
            return
        self.status.visible = False
        self.refresh()

    def close(self, _event=None) -> None:
        self.page.pop_dialog()

    def refresh(self, _event=None) -> None:
        if not self.catalog.models:
            return
        found = self.catalog.search(self.query.value or "", free_only=bool(self.free_only.value),
                                    tools_only=bool(self.tools_only.value), sort=self.sort.value or "newest")
        self.count.value = (f"{len(found)} of {len(self.catalog.models)} models"
                            + (" · all free" if self.free_only.value else ""))
        self.results.controls = [self.row(m) for m in found[:200]]
        if not found:
            self.results.controls = [ft.Container(t.muted("Nothing matches. Try other words, or turn off "
                                                          "Free only to see paid models too."), padding=16)]
        self.page.update()

    def row(self, model: models_catalog.Model) -> ft.Control:
        chosen = model.id == self.current
        tags: list[ft.Control] = [t.badge("FREE", t.GOOD) if model.free else t.badge(model.price_label(), t.WARN)]
        if model.context_label():
            tags.append(t.badge(model.context_label(), t.MUTED))
        if model.reasoning:
            tags.append(t.badge("reasoning", t.VIOLET))
        if model.vision:
            tags.append(t.badge("images", t.ACCENT_HI))
        if not model.tools:
            tags.append(t.badge("no tools", t.BAD))
        return ft.Container(
            content=ft.Row([
                ft.Column([
                    ft.Row([ft.Text(model.short_name, size=14, weight=ft.FontWeight.W_600, color=t.TEXT),
                            t.muted(model.maker, size=12)], spacing=8),
                    t.mono(model.id, size=11.5, color=t.FAINT, selectable=True),
                    ft.Row(tags, spacing=6, wrap=True),
                ], spacing=4, expand=True),
                ft.Icon(ft.Icons.CHECK_ROUNDED, color=t.ACCENT, size=20) if chosen else ft.Container(),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10), border_radius=t.RADIUS,
            bgcolor=t.ACCENT_SOFT if chosen else None, ink=True,
            on_click=lambda _e, m=model: self.page.run_task(self.pick, m),
            tooltip=model.description[:400] or None,
        )

    async def pick(self, model: models_catalog.Model) -> None:
        if not model.free and not await self.confirm_paid(model):
            return
        self.close()
        await self.on_choose(model)

    async def confirm_paid(self, model: models_catalog.Model) -> bool:
        answer: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        def done(value: bool):
            def handler(_event=None):
                if not answer.done():
                    answer.set_result(value)
                self.page.pop_dialog()
            return handler

        self.page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Row([ft.Icon(ft.Icons.PAYMENTS_OUTLINED, color=t.WARN), ft.Text("This model costs money")],
                         spacing=10),
            content=ft.Container(width=460, content=ft.Column([
                ft.Text(f"{model.short_name} is not free: OpenRouter charges your key "
                        f"{model.price_label()}.", size=14),
                t.muted("Every message the agent sends it is billed, and an agent sends many. The free models "
                        "cost nothing. Using it also turns off \"Free models only\" (Control center, Settings), "
                        "which otherwise has OpenRouter refuse it.", size=13),
            ], spacing=10, tight=True)),
            actions=[t.quiet_button("Keep free models", on_click=done(False)),
                     t.primary_button("Use it anyway", on_click=done(True))],
        ))
        return await answer
