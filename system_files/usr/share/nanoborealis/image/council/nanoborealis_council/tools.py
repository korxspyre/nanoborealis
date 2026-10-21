# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The council's tools. Each talks to the Hall as this seat, with the seat's own token, and returns
short plain text the model can read. They're only enabled in a seat: an agent without
NANOBOREALIS_HALL_URL and NANOBOREALIS_HALL_TOKEN (today's single agent) doesn't get them.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

TIMEOUT = 30
SHOWN = 30  # most messages, tasks or votes one call shows


class HallError(Exception):
    pass


def hall_call(method: str, path: str, body: dict | None = None) -> Any:
    url = os.environ.get("NANOBOREALIS_HALL_URL", "").rstrip("/")
    token = os.environ.get("NANOBOREALIS_HALL_TOKEN", "")
    request = urllib.request.Request(url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error")
        except ValueError:
            message = None
        raise HallError(message or f"the Hall answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise HallError(f"the Hall didn't answer ({getattr(error, 'reason', error)})") from None


def clip(value: str, limit: int = 600) -> str:
    value = " ".join(value.split()) if "\n" not in value else value
    return value if len(value) <= limit else value[:limit] + "…"


def show_messages(messages: list[dict]) -> str:
    lines = []
    for m in messages[-SHOWN:]:
        if m["kind"] == "pass":
            lines.append(f"#{m['id']} [{m['author']}] (passes)")
        else:
            tag = {"system": " (Hall)", "escalation": " (asks for advice)"}.get(m["kind"], "")
            lines.append(f"#{m['id']} [{m['author']}]{tag}: {clip(m['text'], 2000)}")
    return "\n".join(lines) or "Nothing said yet."


def show_task(t: dict, full: bool = False) -> str:
    who = f", {t['claimed_by']} on it" if t.get("claimed_by") else ""
    head = f"task {t['id']} [{t['state']}{who}] {t['title']} (from {t['author']})"
    if not full:
        return head
    parts = [head]
    if t.get("detail"):
        parts.append(f"detail: {clip(t['detail'], 4000)}")
    parts += [f"  {n['author']} {n['kind']}: {clip(n['text'], 400)}" for n in t.get("notes") or []]
    if t.get("result"):
        parts.append(f"result: {clip(t['result'], 4000)}")
    return "\n".join(parts)


def show_vote(v: dict, me: str | None = None) -> str:
    state = "closed" if v["closed"] else ("waiting for your ballot" if me in v["waiting_for"] else "open")
    line = f"vote {v['id']} [{state}] {v['question']} options: {' / '.join(v['options'])}"
    if v["ballots"]:
        line += f"; leading: {v['leading']} (margin {v['margin']:.0%}{', split' if v['split'] else ''})"
    return line


class CouncilTool(Tool):
    """Common to the council's tools: enabled only in a seat, errors as tool errors."""

    _plugin_discoverable = True

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return bool(os.environ.get("NANOBOREALIS_HALL_URL") and os.environ.get("NANOBOREALIS_HALL_TOKEN"))

    async def call(self, method: str, path: str, body: dict | None = None) -> Any:
        return await asyncio.to_thread(hall_call, method, path, body)

    async def execute(self, **kwargs: Any) -> Any:
        try:
            return await self.run(**kwargs)
        except HallError as error:
            return ToolResult.error(str(error))

    async def run(self, **kwargs: Any) -> str:
        raise NotImplementedError


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


ROOM = {"type": "integer", "description": "The room's number."}
TASK = {"type": "integer", "description": "The task's number."}


class HallRead(CouncilTool):
    name = "hall_read"
    description = ("Read the council's Hall. Without a room: who is on the council, the rooms, open tasks, and votes "
                   "waiting for you. With a room: its messages (only newer than `after`, if given).")
    parameters = schema({"room": ROOM, "after": {"type": "integer", "description": "Only messages after this id."}})
    read_only = True

    async def run(self, room: int | None = None, after: int = 0) -> str:
        if room is not None:
            return show_messages((await self.call("GET", f"/rooms/{room}/messages?after={int(after)}"))["messages"])
        me = await self.call("GET", "/me")
        seats, rooms, tasks, votes = await asyncio.gather(
            self.call("GET", "/seats"), self.call("GET", "/rooms"), self.call("GET", "/tasks?state=open"),
            self.call("GET", "/votes?open=1"))
        lines = [f"You are {me['name']} ({me['id']}, {me['model']}): trust {me['trust']}, vote weight {me['weight']}.",
                 "Council: " + ", ".join(f"{s['name']} ({s['id']}{', chair' if s['chair'] else ''}, "
                                         f"weight {s['weight']})" for s in seats["seats"])]
        lines.append("Rooms: " + (", ".join(f"{r['id']}: {r['title']}" for r in rooms["rooms"][:SHOWN]) or "none"))
        lines += ["Open tasks:", *[f"  {show_task(t)}" for t in tasks["tasks"][:SHOWN]]] if tasks["tasks"] else \
            ["No open tasks."]
        mine = [v for v in votes["votes"] if me["id"] in v["waiting_for"]]
        lines += ["Votes waiting for you:", *[f"  {show_vote(v, me['id'])}" for v in mine]] if mine else \
            ["No votes waiting for you."]
        return "\n".join(lines)


class HallPost(CouncilTool):
    name = "hall_post"
    description = ("Speak in a Hall room, under your nametape. Say something only when it adds to what's been said; "
                   "otherwise pass.")
    parameters = schema({"room": ROOM, "text": {"type": "string", "description": "What you say."},
                         "reply_to": {"type": "integer", "description": "The message you answer, if any."},
                         "pass": {"type": "boolean", "description": "True to pass: nothing to add."}}, ["room"])

    async def run(self, room: int, text: str = "", reply_to: int | None = None, **kwargs: Any) -> str:
        body: dict = {"kind": "pass"} if kwargs.get("pass") else {"text": text}
        if reply_to is not None:
            body["reply_to"] = reply_to
        posted = await self.call("POST", f"/rooms/{room}/messages", body)
        return f"posted #{posted['id']} in room {room}"


class TaskList(CouncilTool):
    name = "task_list"
    description = "The task board: tasks in a state (open, claimed, review, done, failed), or one task in full."
    parameters = schema({"task": TASK, "state": {"type": "string", "enum": ["open", "claimed", "review", "done", "failed"]}})
    read_only = True

    async def run(self, task: int | None = None, state: str | None = None) -> str:
        if task is not None:
            return show_task(await self.call("GET", f"/tasks/{task}"), full=True)
        found = (await self.call("GET", "/tasks" + (f"?state={state}" if state else "")))["tasks"]
        return "\n".join(show_task(t) for t in found[:SHOWN]) or "No tasks."


class TaskClaim(CouncilTool):
    name = "task_claim"
    description = "Claim an open task, so you're the one working on it. Claim only what you can do well."
    parameters = schema({"task": TASK}, ["task"])

    async def run(self, task: int) -> str:
        return "claimed. " + show_task(await self.call("POST", f"/tasks/{task}/claim"), full=True)


class TaskUpdate(CouncilTool):
    name = "task_update"
    description = "Add a progress note to the task you're working on."
    parameters = schema({"task": TASK, "text": {"type": "string"}}, ["task", "text"])

    async def run(self, task: int, text: str) -> str:
        await self.call("POST", f"/tasks/{task}/update", {"text": text})
        return f"noted on task {task}"


class TaskSubmit(CouncilTool):
    name = "task_submit"
    description = "Hand in the result of the task you're working on. Another seat, or the person, reviews it."
    parameters = schema({"task": TASK, "result": {"type": "string", "description": "The finished work."}},
                        ["task", "result"])

    async def run(self, task: int, result: str) -> str:
        await self.call("POST", f"/tasks/{task}/submit", {"result": result})
        return f"task {task} is waiting for review"


class TaskReview(CouncilTool):
    name = "task_review"
    description = ("Review another seat's result: accept it, or reject it with what's wrong. Be honest: accepted work "
                   "earns its author trust, and rejected work costs it.")
    parameters = schema({"task": TASK, "verdict": {"type": "string", "enum": ["accept", "reject"]},
                         "text": {"type": "string", "description": "Why (needed to reject)."}}, ["task", "verdict"])

    async def run(self, task: int, verdict: str, text: str = "") -> str:
        found = await self.call("POST", f"/tasks/{task}/review", {"verdict": verdict, "text": text})
        return f"task {task} is now {found['state']}"


class Vote(CouncilTool):
    name = "vote"
    description = ("Council votes. To answer one, give `vote` and your `choice` (with a short reason). To ask the "
                   "council to decide, give a `question` and 2 to 8 `options` (and the room it belongs to). Each seat's "
                   "ballot counts by its capability and earned trust.")
    parameters = schema({"vote": {"type": "integer", "description": "The vote you answer."},
                         "choice": {"type": "string"}, "reason": {"type": "string"},
                         "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
                         "room": ROOM, "task": TASK})

    async def run(self, vote: int | None = None, choice: str | None = None, reason: str = "",
                  question: str | None = None, options: list[str] | None = None, room: int | None = None,
                  task: int | None = None) -> str:
        me = (await self.call("GET", "/me"))["id"]
        if vote is not None:
            if not choice:
                return show_vote(await self.call("GET", f"/votes/{vote}"), me)
            return show_vote(await self.call("POST", f"/votes/{vote}/ballot", {"choice": choice, "reason": reason}), me)
        if not question or not options:
            return ToolResult.error("Give a vote and your choice, or a question and its options.")
        body: dict = {"question": question, "options": options}
        body.update({k: v for k, v in (("room", room), ("task", task)) if v is not None})
        return "opened " + show_vote(await self.call("POST", "/votes", body), me)


class Escalate(CouncilTool):
    name = "escalate"
    description = ("Ask for outside advice when the council is stuck or split: the question goes to a free cloud model, "
                   "whose answer is posted in the room. It's advice, not a vote.")
    parameters = schema({"room": ROOM, "question": {"type": "string", "description": "What to ask, with the context."}},
                        ["room", "question"])

    async def run(self, room: int, question: str) -> str:
        if not (await self.call("GET", "/settings")).get("escalation"):
            return ToolResult.error("Escalation is turned off for this council.")
        posted = await self.call("POST", f"/rooms/{room}/messages", {"kind": "escalation", "text": question})
        return f"asked for advice (#{posted['id']}); the answer will be posted in room {room}"
