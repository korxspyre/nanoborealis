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


class CrewMembers(CrewTool):
    name = "crew_members"
    description = ("Who is in the colony, the owner's crew of agents: each member's name, role, group and model, its "
                   "trust, and what it is doing right now (and with which tool). You and they are one collective: "
                   "message any of them with crew_send.")
    parameters = {"type": "object", "additionalProperties": False, "properties": {}}
    read_only = True

    async def execute(self, **_kw: Any) -> Any:
        try:
            found = (await asyncio.to_thread(call, "GET", "/nanoborealis/agent-hands/crew?what=members"))["members"]
        except HandsError as error:
            return ToolResult.error(str(error))
        return "\n".join(f"- {m['name']}: {m['role'] or 'member'}" + (f" ({m['group']})" if m.get("group") else "")
                         + f", {m['doing']}" + (f" ({m['tool']})" if m.get("tool") else "") + f", trust {m['trust']}"
                         for m in found) or "The colony has no members yet."


class CrewSend(CrewTool):
    name = "crew_send"
    description = ("Message a colony member directly (by name, e.g. scout or scout@civ), or everyone at once "
                   "(\"everyone\"). For a question, a heads-up, sharing what you found, or answering their mail "
                   "(give reply_to). Their answer comes back to your crew_inbox. For a piece of work that should be "
                   "done and checked, use crew_task instead.")
    parameters = {"type": "object", "additionalProperties": False, "required": ["to", "text"],
                  "properties": {"to": {"type": "string"}, "text": {"type": "string"},
                                 "reply_to": {"type": "integer", "description": "The mail you answer, if any."}}}

    async def execute(self, to: str, text: str, reply_to: int | None = None, **_kw: Any) -> Any:
        try:
            sent = await asyncio.to_thread(call, "POST", "/nanoborealis/agent-hands/crew",
                                           {"to": to, "text": text, **({"reply_to": reply_to} if reply_to else {})})
        except HandsError as error:
            return ToolResult.error(str(error))
        return f"Sent to {', '.join(sent.get('to') or [to])}. Answers arrive in crew_inbox."


class CrewInbox(CrewTool):
    name = "crew_inbox"
    description = ("Your mail from the colony: messages and answers the crew sent you. Mark an item done with `done` "
                   "once you've dealt with it. Check it when you start on something the crew may have weighed in on.")
    parameters = {"type": "object", "additionalProperties": False,
                  "properties": {"done": {"type": "integer", "description": "A mail item's number to mark done."}}}

    async def execute(self, done: int | None = None, **_kw: Any) -> Any:
        try:
            if done is not None:
                await asyncio.to_thread(call, "POST", "/nanoborealis/agent-hands/crew", {"done": int(done)})
                return f"Mail {done} is done."
            mail = (await asyncio.to_thread(call, "GET", "/nanoborealis/agent-hands/crew?what=inbox"))["mail"]
        except HandsError as error:
            return ToolResult.error(str(error))
        return "\n\n".join(f"[{m['id']}] from {m['sender']} ({m['kind']}): {m['text']}" for m in mail) or "No mail."
