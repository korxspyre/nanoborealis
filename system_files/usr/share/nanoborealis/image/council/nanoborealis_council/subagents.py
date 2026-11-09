# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Named subagents, like Claude Code's: Markdown files with a little frontmatter, each a specialist the agent can
hand a task to with the `subagent` tool.

    ~/.nanobot/workspace/.nanoborealis/agents/reviewer.md
    ---
    name: reviewer
    description: Reviews a change for bugs before it's called done. Use after any non-trivial edit.
    ---
    You are a careful code reviewer. ...

The file's body is the subagent's instructions; the task follows them. It runs as nanobot's own subagent (the
`spawn` tool's machinery), on the chat's model, in the background or, with wait, inside the current turn. The
app's Subagents page writes these files; the agent can too.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nanobot.agent.tools.spawn import SpawnTool

AGENTS_DIR = Path(os.environ.get("NANOBOREALIS_AGENTS_DIR", str(Path.home() / ".nanobot/workspace/.nanoborealis/agents")))


def definitions() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            head, _, body = text[3:].partition("\n---")
            for line in head.splitlines():
                key, colon, value = line.partition(":")
                if colon:
                    meta[key.strip().lower()] = value.strip()
            body = body.lstrip("\n")
        name = meta.get("name") or path.stem
        found[name] = {"name": name, "description": meta.get("description", ""), "prompt": body.strip()}
    return found


class Subagent(SpawnTool):
    """Hand a task to one of the named subagents."""

    _plugin_discoverable = True
    parameters = {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Which subagent, by name (see this tool's description)."},
            "task": {"type": "string", "description": "The task, complete in itself: the subagent doesn't see this chat."},
            "wait": {"type": "boolean", "description": "Wait for its answer inside this turn (default: it works in the "
                                                       "background and reports back)."},
        },
        "required": ["agent", "task"],
        "additionalProperties": False,
    }

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return bool(definitions())

    @property
    def name(self) -> str:
        return "subagent"

    @property
    def description(self) -> str:
        listed = "; ".join(f"{d['name']}: {d['description'] or 'no description'}" for d in definitions().values())
        return ("Hand a task to a named specialist subagent, which works with its own instructions and reports back. "
                f"Available: {listed or 'none'}.")

    async def execute(self, agent: str = "", task: str = "", wait: bool = False, **kwargs: Any) -> str:  # type: ignore[override]
        found = definitions().get(agent)
        if found is None:
            return f"Error: there's no subagent called {agent!r}. Available: {', '.join(definitions()) or 'none'}."
        composed = f"{found['prompt']}\n\n---\nYour task:\n{task}" if found["prompt"] else task
        return await super().execute(task=composed, label=agent, wait=wait)
