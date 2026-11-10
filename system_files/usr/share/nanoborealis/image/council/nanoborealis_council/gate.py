# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Permission modes for the agent's own tools, like Claude Code's.

nanobot runs its shell and file tools without asking; this wraps them so each chat's mode decides:

    auto          everything runs (the container and the hourly snapshots are the safety net); the default
    accept-edits  file edits run; other shell commands that change something ask the owner
    manual        anything that changes something asks the owner
    plan          nothing changes: edits and changing commands are refused, reading runs

The owner picks the mode in the app, per chat. It is kept by this computer's remote service, where the agent
can't change it, and the questions go to the owner's apps (their phone too) the same way a device's do. This
is how the owner stays in charge of the work; the hard boundary is still the container and the snapshots.

Loaded by a .pth file in nanobot's environment (see the image's Containerfile): `install()` patches the tool
classes once, and does nothing if nanobot's layout isn't what it expects.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .hands import TOKEN_FILE, URL

READ_ONLY = re.compile(
    r"^\s*(ls|cat|head|tail|less|wc|stat|file|find|tree|pwd|echo|grep|rg|which|whoami|id|uname|hostname|date|df|du|"
    r"free|ps|env|printenv|git\s+(status|log|diff|show|branch|remote\s+-v|ls-files|blame)|python3?\s+--version|"
    r"node\s+--version)\b(?![^|;&]*(>|\btee\b))")
ASK_MAX = 600
_installed = False


def _call(method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    token = TOKEN_FILE.read_text().strip()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # this same computer, with its own certificate
    request = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-NanoBorealis-Agent": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return json.loads(response.read())


def chat_now() -> str:
    try:
        from nanobot.agent.tools.context import current_request_context
        ctx = current_request_context()
        return str(ctx.chat_id) if ctx and ctx.chat_id else "default"
    except Exception:
        return "default"


def mode_for(chat: str) -> str:
    """The chat's mode; auto when the service can't be asked (no token yet, or tests), as before modes existed."""
    try:
        return str(_call("GET", "/nanoborealis/agent-hands/mode?" + urllib.parse.urlencode({"chat": chat}), timeout=10)["mode"])
    except Exception:
        return "auto"


def request_now() -> str:
    """The owner's own words that started this turn: what the question is for."""
    try:
        from nanobot.agent.tools.context import current_request_context
        ctx = current_request_context()
        return str(ctx.original_user_text or "")[:2000] if ctx else ""
    except Exception:
        return ""


def program_of(command: str) -> str:
    """The program a command runs, past `cd somewhere &&` and variable settings: what "always allow" means."""
    parts = re.split(r"\s*(?:&&|;)\s*", command.strip())
    for part in parts:
        words = [w for w in part.split() if "=" not in w.split("/")[0]]
        if words and words[0] not in ("cd", "pushd", "export", "source", "."):
            return words[0]
    return command.split()[0] if command.split() else "it"


def ask_owner(chat: str, what: str, reason: str, detail: dict | None = None) -> str:
    body: dict[str, Any] = {"chat": chat, "command": what, "reason": reason, "cwd": "the agent's computer",
                            "request": request_now(), "detail": detail or {}}
    deadline = time.time() + ASK_MAX
    while time.time() < deadline:
        try:
            answer = _call("POST", "/nanoborealis/agent-hands/ask", body, timeout=60)
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(5)
            continue
        if answer.get("answer") in ("once", "always", "deny"):
            return answer["answer"]
        body = {"id": answer.get("id")}
    return "deny"


def verdict(kind: str, what: str, mode: str) -> str:
    """run, ask or refuse, for a shell command (kind "exec") or a file edit (kind "edit")."""
    if mode == "auto":
        return "run"
    if kind == "exec" and READ_ONLY.match(what) and not re.search(r"[;&|]\s*\S", what.replace("||", "")):
        return "run"
    if mode == "plan":
        return "refuse"
    if mode == "accept-edits" and kind == "edit":
        return "run"
    return "ask"


# What the owner said "always" to: kept in a file they can read and edit (the app's Permissions page), so it
# outlives a restart and can be taken back. {"allow": ["git", "npm", "edit"]}: programs by name, "edit" for file edits.
RULES_FILE = Path(os.environ.get("NANOBOREALIS_PERMISSIONS_FILE",
                                 str(Path.home() / ".nanobot/workspace/.nanoborealis/permissions.json")))
ONCE_SECONDS = 600
_once: dict[tuple[str, str], float] = {}  # (chat, the exact action) -> when "yes" to it runs out: a retry doesn't ask again


def allowed() -> set[str]:
    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        return {str(x) for x in data.get("allow") or [] if isinstance(x, str)}
    except (OSError, ValueError, AttributeError):
        return set()


def allow_always(program: str) -> None:
    try:
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["allow"] = sorted({*(x for x in data.get("allow") or [] if isinstance(x, str)), program})
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"nanoborealis gate: couldn't save the rule ({type(e).__name__})", file=sys.stderr)

# -- Hooks, in Claude Code's format ------------------------------------------------------------------------------
# ~/.nanobot/workspace/.nanoborealis/hooks.json (the app's Hooks page edits it):
#   {"hooks": {"PreToolUse": [{"matcher": "exec|write_file", "hooks": [{"type": "command", "command": "...",
#                                                                       "timeout": 60}]}],
#              "PostToolUse": [...]}}
# A command gets the event as JSON on stdin (hook_event_name, tool_name, tool_input, chat_id, and for PostToolUse
# tool_response). For PreToolUse, exit status 2 blocks the tool and its stderr is the reason the agent is given.
# The matcher is a regular expression over the tool's name (exec, write_file, edit_file, apply_patch); empty or
# "*" matches every one. Hooks run inside the agent's container, as the agent.

HOOKS_FILE = Path(os.environ.get("NANOBOREALIS_HOOKS_FILE", str(Path.home() / ".nanobot/workspace/.nanoborealis/hooks.json")))


def hooks_for(event: str, tool: str) -> list[dict]:
    try:
        data = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    for group in (data.get("hooks") or {}).get(event) or []:
        matcher = str(group.get("matcher") or "*")
        try:
            hit = matcher in ("", "*") or re.fullmatch(matcher, tool) is not None
        except re.error:
            hit = False
        if hit:
            found += [h for h in group.get("hooks") or [] if h.get("type", "command") == "command" and h.get("command")]
    return found


async def run_hooks(event: str, tool: str, payload: dict) -> str | None:
    """Run the event's hooks; for PreToolUse, the reason if one blocked the tool (exit status 2)."""
    for hook in hooks_for(event, tool):
        try:
            process = await asyncio.create_subprocess_shell(
                hook["command"], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _out, err = await asyncio.wait_for(process.communicate(json.dumps({"hook_event_name": event, **payload},
                                                                                default=str).encode()),
                                               float(hook.get("timeout") or 60))
        except (OSError, asyncio.TimeoutError) as e:
            print(f"nanoborealis hook {event} failed: {type(e).__name__}", file=sys.stderr)
            continue
        if event == "PreToolUse" and process.returncode == 2:
            return err.decode(errors="replace").strip() or f"a {event} hook blocked it"
    return None


def change_detail(tool: str, kw: dict, program: str) -> dict:
    """What the owner is asked about, in a shape the app can draw as Claude Code does: a command, or each file's
    change (old and new text) to show as a diff."""
    if tool == "exec":
        return {"kind": "exec", "command": str(kw.get("command") or kw.get("cmd") or "")[:4000], "program": program,
                "cwd": str(kw.get("working_dir") or kw.get("workdir") or "")}
    if tool == "write_file":
        return {"kind": "edit", "edits": [{"path": str(kw.get("path") or ""), "old": None,
                                           "new": str(kw.get("content") or "")[:20000]}]}
    if tool == "edit_file":
        return {"kind": "edit", "edits": [{"path": str(kw.get("path") or ""), "old": str(kw.get("old_text") or "")[:20000],
                                           "new": str(kw.get("new_text") or "")[:20000]}]}
    edits = []
    for e in kw.get("edits") or []:
        if isinstance(e, dict):
            edits.append({"path": str(e.get("path") or ""), "old": e.get("old_text") if e.get("action") != "add" else None,
                          "new": str(e.get("new_text") or e.get("content") or "")[:20000],
                          "action": str(e.get("action") or "")})
    return {"kind": "edit", "edits": edits[:20]}


def wrap(cls: type, kind: str, describe, tool_name: str = "") -> None:
    original = cls.execute
    tool_name = tool_name or kind

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        what = describe(kwargs)
        chat = chat_now()
        blocked = await run_hooks("PreToolUse", tool_name, {"tool_name": tool_name, "tool_input": kwargs, "chat_id": chat})
        if blocked:
            return f"Error: blocked by a hook: {blocked}"
        result = await guarded(self, args, kwargs, what, chat)
        await run_hooks("PostToolUse", tool_name, {"tool_name": tool_name, "tool_input": kwargs, "chat_id": chat,
                                                   "tool_response": str(result)[:20000]})
        return result

    async def guarded(self, args: tuple, kwargs: dict, what: str, chat: str) -> Any:
        mode = await asyncio.to_thread(mode_for, chat)
        decision = verdict(kind, what, mode)
        program = program_of(what) if kind == "exec" else kind
        now = time.time()
        if decision == "ask" and (program in await asyncio.to_thread(allowed) or _once.get((chat, what), 0) > now):
            decision = "run"
        if decision == "refuse":
            return f"Error: this chat is in plan mode, so nothing may change. Propose this instead: {what[:300]}"
        if decision == "ask":
            reason = "it changes something" if mode == "manual" else "a command that changes something"
            detail = change_detail(tool_name, kwargs, program)
            answer = await asyncio.to_thread(ask_owner, chat, what, f"{mode} mode: {reason}", detail)
            if answer == "deny":
                return f"Error: the owner didn't allow this: {what[:300]}. Ask them, or try something else."
            if answer == "always":
                await asyncio.to_thread(allow_always, program)
            _once[(chat, what)] = now + ONCE_SECONDS
            for key in [k for k, until in _once.items() if until < now]:
                _once.pop(key, None)
        return await original(self, *args, **kwargs)
    execute.__wrapped__ = original  # type: ignore[attr-defined]
    cls.execute = execute


def install() -> None:
    global _installed
    if _installed:
        return
    try:
        from nanobot.agent.tools.apply_patch import ApplyPatchTool
        from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool
        from nanobot.agent.tools.shell import ExecTool
    except Exception as e:  # a nanobot without these: leave it as it is
        print(f"nanoborealis gate: not installed ({type(e).__name__})", file=sys.stderr)
        return
    wrap(ExecTool, "exec", lambda kw: str(kw.get("command") or kw.get("cmd") or ""), "exec")
    wrap(WriteFileTool, "edit", lambda kw: f"write {kw.get('path') or '?'} ({len(str(kw.get('content') or ''))} characters)",
         "write_file")
    wrap(EditFileTool, "edit", lambda kw: f"edit {kw.get('path') or '?'}\n- {str(kw.get('old_text') or '')[:800]}\n"
                                          f"+ {str(kw.get('new_text') or '')[:800]}", "edit_file")
    wrap(ApplyPatchTool, "edit", lambda kw: "apply a patch:\n" + json.dumps(kw.get("edits") or [], default=str)[:2000],
         "apply_patch")
    _installed = True
