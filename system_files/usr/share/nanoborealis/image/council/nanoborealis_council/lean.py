# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Tools on demand: each request carries the full description of only the tools the agent uses all the time; the
rest are named, with a line each, in tools_load, and a chat gets a tool's full description once it loads it.

Every request to a model repeats every tool's description (about 7,000 tokens for all of them), on every step of
every turn. That spends rate limits on what the model mostly doesn't use, shuts out providers with small request
caps, and makes weaker models lose the thread. A listed tool still runs if the model calls it without loading it.

Patched in from tools.py the same way gate.py is; does nothing if nanobot's layout isn't what it expects.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from nanobot.agent.tools.base import Tool

# Sent in full with every request: reading, changing and running things, and the web.
CORE = {"read_file", "write_file", "edit_file", "apply_patch", "list_dir", "find_files", "grep", "exec",
        "web_search", "web_fetch", "todo", "tools_load"}
# A crew member's own work happens on the colony's board: those stay in full for it.
SEAT_CORE = {"colony_read", "colony_post", "task_list", "task_claim", "task_update", "task_submit", "task_review",
             "vote", "escalate", "rig_send", "rig_inbox"}
_loaded: dict[str, set[str]] = {}  # chat -> the tools it loaded
_installed = False


def _chat() -> str:
    try:
        from nanobot.agent.tools.context import current_request_context
        ctx = current_request_context()
        return f"{getattr(ctx, 'channel', '')}:{getattr(ctx, 'chat_id', '')}" if ctx else "default"
    except Exception:
        return "default"


def _core() -> set[str]:
    return CORE | SEAT_CORE if os.environ.get("NANOBOREALIS_COLONY_URL") else CORE


def _name(schema: dict) -> str:
    fn = schema.get("function")
    return str((fn if isinstance(fn, dict) else schema).get("name") or "")


def _summary(description: str) -> str:
    first = " ".join(str(description or "").split())
    for stop in (". ", ": "):
        if stop in first[:140]:
            first = first[:first.index(stop, 0, 140)]
            break
    return first[:110]


class ToolsLoad(Tool):
    name = "tools_load"
    description = "Load tools by name before you use them (more are listed here once the agent starts)."
    parameters = {
        "type": "object",
        "properties": {"names": {"type": "array", "items": {"type": "string"},
                                 "description": "The tools to load, by name."}},
        "required": ["names"],
        "additionalProperties": False,
    }
    read_only = True
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    async def execute(self, names: list[str] | None = None, **kwargs: Any) -> str:
        registry = _registry
        if registry is None:
            return "Every tool is already loaded."
        known = set(registry._tools)
        asked = [str(n).strip() for n in names or [] if str(n).strip()]
        found = [n for n in asked if n in known]
        missing = [n for n in asked if n not in known]
        _loaded.setdefault(_chat(), set()).update(found)
        out = f"Loaded {', '.join(found)}: their full descriptions come with your next step." if found else "Loaded nothing."
        if missing:
            out += f" Not tools here: {', '.join(missing)}."
        return out


_registry = None


def install() -> None:
    global _installed
    if _installed or os.environ.get("NANOBOREALIS_ALL_TOOLS") == "1":
        return
    try:
        from nanobot.agent.tools.registry import ToolRegistry
    except Exception as e:
        print(f"nanoborealis lean: not installed ({type(e).__name__})", file=sys.stderr)
        return
    whole = ToolRegistry.get_definitions

    def get_definitions(self) -> list[dict[str, Any]]:
        global _registry
        everything = whole(self)
        if "tools_load" not in self._tools:  # a registry without the loader (a subagent's, a test's): all of them
            return everything
        _registry = self
        keep = _core() | _loaded.get(_chat(), set())
        sent, listed = [], []
        for schema in everything:
            name = _name(schema)
            if name in keep:
                sent.append(schema)
            else:
                fn = schema.get("function") if isinstance(schema.get("function"), dict) else schema
                listed.append(f"{name}: {_summary(fn.get('description', ''))}")
        if not listed:
            return everything
        loader = next((s for s in sent if _name(s) == "tools_load"), None)
        if loader is not None:
            loader = {**loader, "function": {**loader["function"], "description":
                      "Load more tools by name, then call them. Load one before its first use in a chat; it stays "
                      "loaded for the chat. Available:\n- " + "\n- ".join(listed)}}
            sent = [loader if _name(s) == "tools_load" else s for s in sent]
        return sent

    ToolRegistry.get_definitions = get_definitions
    _installed = True
