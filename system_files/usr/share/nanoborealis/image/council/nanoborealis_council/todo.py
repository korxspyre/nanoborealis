# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The agent's to-do list for the work in front of it, like Claude Code's: for anything with several steps it writes
the steps down, marks the one it's on, and ticks them off. The app shows the list as a checklist in the chat, so the
owner can see where the work stands. It lives in the conversation only (each call sends the whole list)."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool

STATES = ("pending", "in_progress", "completed")


class TodoWrite(Tool):
    name = "todo"
    description = ("Keep a to-do list for the task in front of you when it has three or more steps: send the whole list "
                   "each time, each item with its status (pending, in_progress or completed). Mark one item in_progress "
                   "before you start it and completed as soon as it's done; add items you discover. The owner sees it as "
                   "a checklist. Don't use it for one-step requests.")
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "content": {"type": "string", "description": "The step, as a short imperative."},
                    "status": {"type": "string", "enum": list(STATES)},
                }, "required": ["content", "status"]},
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }
    read_only = True
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    async def execute(self, todos: list[dict] | None = None, **kwargs: Any) -> str:
        items = [t for t in todos or [] if isinstance(t, dict) and str(t.get("content") or "").strip()]
        if not items:
            return "The list is empty."
        marks = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}
        lines = [f"{marks.get(str(t.get('status')), '[ ]')} {str(t['content']).strip()}" for t in items[:50]]
        done = sum(1 for t in items if t.get("status") == "completed")
        return f"To-do list saved ({done} of {len(items)} done):\n" + "\n".join(lines)
