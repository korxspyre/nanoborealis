# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The crew, for the owner's own agent: hand a piece of work to the colony instead of doing it alone.

The colony's members are full agents of their own (research, building, review, record keeping), so they are what
subagents were: `crew_task` puts work on the colony's task list, one member takes it on with its own tools and
another reviews it; `crew_check` reads how it is going and the result. Both go through this computer's remote
service with the agent's own token, as the hands tools do.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

from .hands import TOKEN_FILE, HandsError, call


class CrewTool(Tool):
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return TOKEN_FILE.exists()


class CrewTask(CrewTool):
    name = "crew_task"
    description = ("Hand a piece of work to the colony, the owner's crew of agents (researchers, builders, a reviewer, "
                   "an archivist), each with its own tools. One takes it on and another reviews it before it counts as "
                   "done. Use it for work that can go on alongside yours: research, a build, a second opinion. "
                   "It returns the task's number; follow it with crew_check.")
    parameters = {"type": "object", "additionalProperties": False, "required": ["title"],
                  "properties": {"title": {"type": "string", "description": "The work, in one line."},
                                 "detail": {"type": "string", "description": "What done looks like, and anything they need."}}}

    async def execute(self, title: str, detail: str = "", **_kw: Any) -> Any:
        try:
            task = await asyncio.to_thread(call, "POST", "/nanoborealis/agent-hands/crew", {"title": title, "detail": detail})
        except HandsError as error:
            return ToolResult.error(str(error))
        return f"Task {task['id']} is with the crew: {task['title']}. Check on it with crew_check {task['id']}."


class CrewCheck(CrewTool):
    name = "crew_check"
    description = ("How a task given to the crew is going: who has it, its state (open, claimed, review, done, failed), "
                   "the notes between them, and the result once it is done. Without a task number: the crew's open tasks.")
    parameters = {"type": "object", "additionalProperties": False,
                  "properties": {"task": {"type": "integer", "description": "The task's number."}}}
    read_only = True

    async def execute(self, task: int | None = None, **_kw: Any) -> Any:
        try:
            if task is None:
                found = (await asyncio.to_thread(call, "GET", "/nanoborealis/agent-hands/crew"))["tasks"]
                return "\n".join(f"[{x['id']}] {x['state']}: {x['title']}" + (f" ({x['claimed_by']})" if x.get("claimed_by") else "")
                                 for x in found) or "The crew has no open tasks."
            x = await asyncio.to_thread(call, "GET", f"/nanoborealis/agent-hands/crew?task={int(task)}")
        except HandsError as error:
            return ToolResult.error(str(error))
        lines = [f"[{x['id']}] {x['state']}: {x['title']}" + (f", with {x['claimed_by']}" if x.get("claimed_by") else "")]
        lines += [f"  {n['author']} {n['kind']}: {n['text'][:600]}" for n in (x.get("notes") or [])[-8:]]
        if x.get("result"):
            lines.append("Result:\n" + str(x["result"])[:6000])
        return "\n".join(lines)
