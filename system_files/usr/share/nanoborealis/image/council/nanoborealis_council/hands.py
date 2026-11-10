# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The agent's hands: run a command on one of the owner's other devices (their PC, their phone) that lends its
hands from the NanoBorealis app.

Every job names the request it serves and the folders that request is about. The device checks each job against
them and the owner's rules: inside those folders it runs; outside them, or anything risky, it waits for the owner
to answer on their phone or PC; a few things never run. So drifting onto another subject shows up as a question,
not a change. The tool talks to this computer's remote service with the agent's own token, which the service
keeps in the agent's home.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

URL = os.environ.get("NANOBOREALIS_HANDS_URL", "https://host.containers.internal:8766")
TOKEN_FILE = Path(os.environ.get("NANOBOREALIS_HANDS_TOKEN_FILE", str(Path.home() / ".config/nanoborealis/hands.token")))
SHOWN = 12_000  # the end of a job's output the model is shown


class HandsError(Exception):
    pass


def call(method: str, path: str, body: dict | None = None, timeout: float = 60) -> Any:
    try:
        token = TOKEN_FILE.read_text().strip()
    except OSError:
        raise HandsError("no hands token yet: this computer's remote service hands it out within a few minutes of starting") from None
    # The service is this same computer; its certificate is its own, so it isn't checked against a public authority.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-NanoBorealis-Agent": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error")
        except ValueError:
            message = None
        raise HandsError(message or f"the remote service answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise HandsError(f"the remote service didn't answer ({getattr(error, 'reason', error)})") from None


class HandsDevices(Tool):
    name = "hands_devices"
    description = ("List the owner's devices that are lending their hands right now (their PC, their phone): each one's name, "
                   "operating system, shell, and the folders it shares. Use before hands_run.")
    parameters = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    read_only = True
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return TOKEN_FILE.exists()

    async def execute(self, **kwargs: Any) -> Any:
        try:
            found = (await asyncio.to_thread(call, "GET", "/nanoborealis/agent-hands"))["devices"]
        except HandsError as error:
            return ToolResult.error(str(error))
        if not found:
            return "No device is lending its hands right now. The owner turns it on in the app: Settings, Lend hands."
        lines = []
        for d in found:
            box = d.get("box") or {}
            lines.append(f"- {d['name']}: {box.get('os', '?')}, shell {box.get('shell', '?')}, mode {box.get('mode', '?')}, "
                         f"shared folders: {', '.join(box.get('folders') or []) or '(any the job names, after asking)'}")
        return "\n".join(lines)


def folder_list(value: Any) -> list[str]:
    """The folders as a list, whether the model sent a list, a JSON string of one, or one path."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value] if value.strip() else []
    return [str(v) for v in value] if isinstance(value, list) else []


class HandsOpen(Tool):
    name = "hands_open"
    description = ("Open something on the screen of one of the owner's devices that lends its hands, the way they would "
                   "by double-clicking it: a file or folder in its usual app, a web page in their browser, or a program "
                   "(by name or path). Use it to show them what you made, or to bring up an app for them. A file inside "
                   "the request's folders opens right away; a web page, a program or anything else waits for their OK.")
    parameters = {
        "type": "object",
        "properties": {
            "device": {"type": "string", "description": "The device's name, as hands_devices lists it."},
            "target": {"type": "string", "description": "A path on that device, a URL, or a program."},
            "request": {"type": "string", "description": "The owner's request this serves, in their words."},
            # No type: models often send the list as a JSON string, which a strict "array" refused outright.
            "folders": {"description": "Folders on that device the request is about (absolute paths): a list."},
        },
        "required": ["device", "target", "request"],
        "additionalProperties": False,
    }
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return TOKEN_FILE.exists()

    async def execute(self, device: str, target: str, request: str, folders: list[str] | None = None, **kwargs: Any) -> Any:
        body = {"device": device, "open": target, "timeout": 60, "task": {"request": request, "folders": folder_list(folders)}}
        try:
            answer = await asyncio.to_thread(call, "POST", "/nanoborealis/agent-hands/run", body, 760)
        except HandsError as error:
            return ToolResult.error(str(error))
        if answer.get("refused"):
            return ToolResult.error(str(answer.get("output") or "not opened"))
        return f"[{device}] {answer.get('output') or 'opened'}"


class HandsRun(Tool):
    name = "hands_run"
    description = ("Run one shell command on one of the owner's devices that lends its hands (see hands_devices). Windows "
                   "devices run PowerShell; Linux and macOS run bash. Say which of the owner's requests this serves and the "
                   "folders it is about, on that device: inside them it runs; outside them, or if risky, it waits for the "
                   "owner's OK (up to 10 minutes); some things never run. Prefer reading and small steps. Returns the exit "
                   "status and output.")
    parameters = {
        "type": "object",
        "properties": {
            "device": {"type": "string", "description": "The device's name, as hands_devices lists it."},
            "command": {"type": "string", "description": "The command, in that device's shell."},
            "request": {"type": "string", "description": "The owner's request this serves, in their words."},
            # No type: models often send the list as a JSON string, which a strict "array" refused outright.
            "folders": {"description": "Folders on that device the request is about (absolute paths): a list."},
            "cwd": {"type": "string", "description": "Where to run it; defaults to the first folder."},
            "timeout": {"type": "integer", "description": "Seconds, at most 3600 (default 300)."},
        },
        "required": ["device", "command", "request", "folders"],
        "additionalProperties": False,
    }
    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return TOKEN_FILE.exists()

    async def execute(self, device: str, command: str, request: str, folders: list[str], cwd: str = "",
                      timeout: int = 300, **kwargs: Any) -> Any:
        timeout = max(10, min(int(timeout or 300), 3600))
        body = {"device": device, "command": command, "timeout": timeout, "cwd": cwd,
                "task": {"request": request, "folders": folder_list(folders)}}
        try:
            # The owner may take up to 10 minutes to answer a question about it, on top of the job's own time.
            answer = await asyncio.to_thread(call, "POST", "/nanoborealis/agent-hands/run", body, timeout + 700)
        except HandsError as error:
            return ToolResult.error(str(error))
        output = str(answer.get("output") or "")
        if len(output) > SHOWN:
            output = "…" + output[-SHOWN:]
        if answer.get("refused"):
            return ToolResult.error(output or "not run on that device")
        state = "timed out" if answer.get("timed_out") else f"exit {answer.get('exit')}"
        return f"[{state} on {device}]\n{output}"
