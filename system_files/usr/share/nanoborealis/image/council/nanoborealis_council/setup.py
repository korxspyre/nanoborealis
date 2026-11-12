# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The setup, for the owner's agent: read how this computer's NanoBorealis is set up, and change it with the owner's
yes. Crew members ask the agent for changes by mail; the agent decides and asks the owner. Keys never pass here."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

from .gate import ASK_MAX, chat_now
from .hands import TOKEN_FILE, HandsError, call


class SetupTool(Tool):
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return TOKEN_FILE.exists()


class SetupRead(SetupTool):
    name = "setup_read"
    description = ("How NanoBorealis is set up on this computer: each inference provider (key present, on or off, "
                   "limits, used today, resting), whether requests are tokenized, the colony's settings, each crew "
                   "member's model and benchmark score, and every model the providers serve. Read it before setup_change.")
    parameters = {"type": "object", "additionalProperties": False, "properties": {}}
    read_only = True

    async def execute(self, **_kw: Any) -> Any:
        try:
            return json.dumps(await asyncio.to_thread(call, "GET", "/nanoborealis/agent-hands/setup"), indent=1)[:20000]
        except HandsError as error:
            return ToolResult.error(str(error))


class SetupChange(SetupTool):
    name = "setup_change"
    description = ("Change the setup; the owner is asked first and nothing changes without their yes. One change per "
                   "call: {provider: nim|groq|openrouter, field: enabled|daily|per_minute|max_request, value}, "
                   "{colony: {autonomy: on-task|continuous, escalation: true|false}}, or {member, model} to move a crew "
                   "member to another free model (from setup_read's list). Keys are added in the app only.")
    parameters = {"type": "object", "additionalProperties": False, "required": ["change", "reason"],
                  "properties": {"change": {"type": "object", "description": "The one change, as above."},
                                 "reason": {"type": "string", "description": "Why, in a sentence the owner reads."}}}

    async def execute(self, change: dict, reason: str, **_kw: Any) -> Any:
        body: dict[str, Any] = {"change": change, "reason": reason, "chat": chat_now()}
        deadline = time.time() + ASK_MAX
        try:
            while time.time() < deadline:
                answer = await asyncio.to_thread(call, "POST", "/nanoborealis/agent-hands/setup", body, 120)
                if not answer.get("pending"):
                    break
                body = {"id": answer["id"]}
        except HandsError as error:
            return ToolResult.error(str(error))
        if answer.get("done"):
            return f"Done: {answer['what']}."
        return ToolResult.error(answer.get("error") or f"Not changed: {answer.get('why') or 'the owner said no'}.")
