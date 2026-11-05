# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Builds the coding-agent screens (src/codeui.py) and the workspace around them without a window, so a typo in a
control or a missing attribute fails here and not when someone opens the app. It can't judge how it looks.

    python dev/codeui_check.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet as ft  # noqa: E402

import codefeatures as cf  # noqa: E402
import codeui  # noqa: E402
import workspace  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


segments = cf.status_segments(model="a/b:free", provider="openrouter", used=10, window=100, cost=0.0, added=3, removed=1, branch="main",
                              plan=True, privacy_on=True, pool="3 nodes", crew="4 seats")
check(isinstance(codeui.status_bar(segments), ft.Row) and len(codeui.status_bar(segments).controls) == len(segments), "the status bar draws one chip per segment")
check(isinstance(codeui.plan_card(lambda _e: None, lambda _e: None), ft.Container), "the plan card draws")
items = [{"sha": "a" * 40, "at": 1_700_000_000, "label": "make the readme"}, {"sha": "b" * 40, "at": 1_700_000_100, "label": ""}]
dialog = codeui.rewind_dialog(items, lambda _i: None, lambda _e: None)
check(isinstance(dialog, ft.AlertDialog) and isinstance(codeui.rewind_dialog([], lambda _i: None, lambda _e: None), ft.AlertDialog),
      "the rewind dialog draws, with checkpoints and with none")
chips = cf.crew_chips([{"id": "a", "name": "A", "weight": 1, "trust": 1.0}, {"id": "c", "name": "Claude", "external": True, "here": True, "weight": 0.5}], "c")
check(len(codeui.crew_strip(chips, lambda _c: None).controls) == 2, "the crew strip draws a chip per seat")
check(codeui.when(0).count(" ") >= 1, "times read as words")

# The workspace builds with the new controls in the composer.
app = MagicMock()
app.page = MagicMock()
app.page.width = 1200
app.mobile = False
app.machine = None
check(isinstance(workspace.Workspace(app).build(), ft.Row), "the whole workspace lays out")
space = workspace.Workspace(app)
space.composer_view()
check(isinstance(space.plan_chip, ft.Container) and isinstance(space.status_bar_holder, ft.Container) and isinstance(space.crew_holder, ft.Container),
      "the composer has a Plan chip, a status bar and a crew strip")
check(any(c == "/rewind" for c, _ in space.all_commands()) and any(c == "/plan" for c, _ in space.all_commands()), "the app's commands are in the / palette")
space.toggle_plan()
check(space.plan_mode and "Plan first" in space.plan_chip.content.content.controls[-1].value, "the Plan chip turns on")
space.toggle_plan()
check(not space.plan_mode, "...and off")

space.scope = {"project_path": "/home/nanobot/projects/demo", "access_mode": "restricted"}
check(space.host_folder() == "/srv/nanoborealis/projects/demo", "a chat's project folder is found on the computer")
space.scope = {"project_path": "/home/nanobot/projects", "access_mode": "restricted"}
check(space.host_folder() is None, "'all projects' isn't copied before every message")
space.scope = None
space.settings = {"providers": [{"name": "openrouter", "api_base": "http://x:11436/src/openrouter/v1"}],
                  "model_presets": {"ultra": {"model": "n/u:free", "provider": "openrouter"}}}
space.model_id = "n/u:free"
space.council = MagicMock(seats=[{"id": "c", "name": "Claude"}])
asyncio.run(space.refresh_status())
bar = space.status_bar_holder.content
check(isinstance(bar, ft.Row) and any("tokenized" in str(getattr(getattr(c.content, "controls", [None])[-1], "value", "")) for c in bar.controls),
      "the status bar says the chat's cloud model is tokenized when the agent routes through the relay")

sent: list[str] = []


async def fake_send(text, intent=None):
    sent.append(text)

space.send_text = fake_send  # type: ignore[assignment]
asyncio.run(space.approve_plan())
check(sent == [cf.APPROVE] and not space.plan_mode, "approving a plan turns plan mode off and asks the agent to go ahead")

print("codeui checks passed")
