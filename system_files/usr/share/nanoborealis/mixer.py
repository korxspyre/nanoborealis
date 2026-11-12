# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Mixed intelligence: several models of different strength and make working on one request.

Pure logic, no network: the relay hands run() a `call(member, messages) -> text` and every call goes
through it, so every cloud call goes through the tokenizing proxy (tokenizer.py, sources.py) and this
file never sees the internet.

Members come in four tiers by capability (the LiveBench-style 0-100 score the pool planner uses):
reflex < 25 <= worker < 45 <= specialist < 60 <= anchor. The idea is to spend the scarce quota of the
strongest free models only where it counts: small models write the bulk, strong ones write the short,
high-leverage parts (a fix, a review, a synthesis).

Recipes, chosen from the task:
  single    one worker answers.
  reviewed  a worker answers; a model of another family reviews it (APPROVE, or REVISE with what's wrong);
            on REVISE a stronger model fixes it once. For code and anything that has no test to run.
  cascade   a worker answers and an objective check (a test run) judges it; each failure escalates one
            tier with the failure shown. Used when the task brings a check.
  panel     up to three workers or specialists of different families answer in parallel; an anchor
            synthesizes, unattributed. On high stakes it is then reviewed too.

Privacy is a rule of the planner, not an option on the call:
  - A task marked "local-only" is only ever given to members that are `private` (the pool and sources
    on the owner's own network). With none available it fails, it does not fall back to the cloud.
  - A role sees only what it needs: a proposer the request, a reviewer the request's last question and
    the answer, an aggregator the question and unattributed answers. Never the whole history.
  - Nothing is logged but who played which role (the audit), never what was said.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

TIER_LIMITS = (25.0, 45.0, 60.0)  # capability below each limit is the tier of that index (reflex, worker, specialist, anchor)
DEFAULT_COOL = 60.0  # seconds a member rests after a failure that didn't say how long


def tier_of(capability: float) -> int:
    for i, limit in enumerate(TIER_LIMITS):
        if capability < limit:
            return i
    return 3


@dataclass(frozen=True)
class Member:
    route: str  # "pool", or the name of a source (sources.py)
    model: str
    family: str
    capability: float
    private: bool = False  # runs on the owner's own computers: may see anything, and local-only tasks may use it
    rpm: int = 0  # requests a minute it allows, 0: no limit
    per_day: int = 0  # requests a day it allows, 0: no limit
    estimated: bool = True  # capability is a guess, not a benchmark score

    @property
    def id(self) -> str:
        return f"{self.route}:{self.model}"

    @property
    def tier(self) -> int:
        return tier_of(self.capability)


# The free models the agent already knows (image/seed/config.json), with guessed capabilities until the
# pool planner's LiveBench scores are matched to them. Free tiers allow few requests, so the limits are modest.
DEFAULT_MEMBERS = [
    Member("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia", 65, rpm=10, per_day=40),
    # Checked against OpenRouter's model list on 2026-11-09: the super, inkling and qwen3.8 free tiers had ended.
    Member("openrouter", "nvidia/nemotron-3.5-super-vl:free", "nvidia", 55, rpm=15, per_day=40),
    Member("openrouter", "dots-studio/dots-3-note-preview:free", "dots", 50, rpm=15, per_day=40),
    Member("openrouter", "cohere/north-mini-code:free", "cohere", 45, rpm=15, per_day=40),
    Member("openrouter", "nvidia/nemotron-3.5-lightning:free", "nvidia", 35, rpm=20, per_day=40),
]


def load_members(path: Path | str | None) -> list[Member]:
    """The members in a mix.json ({"members": [{route, model, family, capability, ...}]}); the defaults without one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return list(DEFAULT_MEMBERS)
    found = []
    for entry in data.get("members") or []:
        try:
            found.append(Member(str(entry["route"]), str(entry["model"]), str(entry.get("family") or entry["model"]),
                                float(entry["capability"]), bool(entry.get("private")), int(entry.get("rpm") or 0),
                                int(entry.get("per_day") or 0), bool(entry.get("estimated", True))))
        except (KeyError, TypeError, ValueError):
            continue
    return found


# -- Who may be asked, and when -------------------------------------------------------------------------


class Ledger:
    """How many requests each member has had lately, and who is resting after a failure."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.sent: dict[str, deque] = {}
        self.until: dict[str, float] = {}

    def ready(self, member: Member) -> bool:
        now = self.clock()
        with self.lock:
            if self.until.get(member.id, 0) > now:
                return False
            stamps = self.sent.setdefault(member.id, deque())
            while stamps and stamps[0] < now - 86400:
                stamps.popleft()
            if member.per_day and len(stamps) >= member.per_day:
                return False
            return not (member.rpm and sum(1 for t in stamps if t > now - 60) >= member.rpm)

    def spend(self, member: Member) -> None:
        with self.lock:
            self.sent.setdefault(member.id, deque()).append(self.clock())

    def cool(self, member: Member, seconds: float = DEFAULT_COOL) -> None:
        with self.lock:
            self.until[member.id] = self.clock() + max(1.0, seconds)


class Stats:
    """Which member does well at which kind of task, from what reviews and checks found. Kept in memory,
    and in a SQLite file if given one. A member nobody has judged yet counts as middling (Laplace)."""

    def __init__(self, path: Path | str | None = None):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS outcomes (at REAL, member TEXT, kind TEXT, role TEXT, ok INTEGER)")
        self.db.commit()

    def record(self, member: Member, kind: str, role: str, ok: bool) -> None:
        with self.lock:
            self.db.execute("INSERT INTO outcomes VALUES (?, ?, ?, ?, ?)", (time.time(), member.id, kind, role, int(ok)))
            self.db.commit()

    def rate(self, member: Member, kind: str) -> float:
        with self.lock:
            good, total = self.db.execute("SELECT COALESCE(SUM(ok), 0), COUNT(*) FROM outcomes WHERE member = ? AND kind = ?",
                                          (member.id, kind)).fetchone()
        return (good + 1) / (total + 2)

    def close(self) -> None:
        with self.lock:
            self.db.close()


@dataclass
class Task:
    messages: list[dict]
    kind: str = "chat"  # chat | code | reasoning
    sensitivity: str = "open"  # open: the cloud may be asked, tokenized. local-only: it may not.
    stakes: str = "normal"  # normal | high
    check: Callable[[str], tuple[bool, str]] | None = None  # an objective test: (passed, what failed)


class NoMember(Exception):
    """Nobody eligible and ready can play this role. For a local-only task this is the answer, not a fault."""


@dataclass
class Result:
    answer: str
    recipe: str
    ok: bool = True  # False: a check or review still objected after the last try
    audit: list[dict] = field(default_factory=list)  # {"role", "member", "tier", "private", "ok"}: who, never what


def eligible(member: Member, task: Task) -> bool:
    return member.private or task.sensitivity != "local-only"


def last_user(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


def choose(task: Task) -> str:
    if task.check is not None:
        return "cascade"
    if task.kind == "reasoning" or task.stakes == "high":
        return "panel"
    return "reviewed" if task.kind == "code" else "single"


# -- Running a recipe -------------------------------------------------------------------------------------


class Mixer:
    def __init__(self, members: list[Member], call: Callable[[Member, list[dict]], str],
                 ledger: Ledger | None = None, stats: Stats | None = None):
        self.members, self.call = members, call
        self.ledger, self.stats = ledger or Ledger(), stats or Stats()

    def candidates(self, task: Task, tiers: tuple[int, ...], avoid_ids=(), avoid_families=(), prefer_private=True) -> list[Member]:
        """Members that may be asked, ready, in these tiers, best first. Another family than `avoid_families`
        comes first (diverse errors catch each other); private ones first when `prefer_private` (they cost
        nothing and send nothing anywhere)."""
        pool = [m for m in self.members if m.tier in tiers and eligible(m, task) and m.id not in avoid_ids
                and self.ledger.ready(m)]
        return sorted(pool, key=lambda m: (m.family in avoid_families, (not m.private) if prefer_private else 0,
                                           -(0.5 * self.stats.rate(m, task.kind) + 0.5 * m.capability / 100)))

    def ask(self, result: Result, role: str, task: Task, messages: list[dict], tier_groups, **kw) -> tuple[Member, str]:
        """The first member of the first tier group that answers. One that fails rests and the next is tried."""
        for tiers in tier_groups:
            for member in self.candidates(task, tiers, **kw):
                self.ledger.spend(member)
                try:
                    text = self.call(member, messages)
                except Exception as error:  # a rate limit, an outage: rest it, try the next one
                    self.ledger.cool(member, float(getattr(error, "retry_after", 0) or DEFAULT_COOL))
                    result.audit.append({"role": role, "member": member.id, "tier": member.tier,
                                         "private": member.private, "ok": False})
                    continue
                result.audit.append({"role": role, "member": member.id, "tier": member.tier,
                                     "private": member.private, "ok": True})
                return member, str(text or "").strip()
        why = "no local model is free to take a local-only task" if task.sensitivity == "local-only" else f"no model is free to be the {role}"
        raise NoMember(why)

    def run(self, task: Task) -> Result:
        recipe = choose(task)
        result = Result("", recipe)
        getattr(self, f"_{recipe}")(task, result)
        return result

    # -- The recipes ------------------------------------------------------------------------------------

    def _single(self, task: Task, result: Result) -> None:
        _, result.answer = self.ask(result, "worker", task, task.messages, [(1,), (2,), (0,), (3,)])

    def _fix(self, task: Task, answer: str, note: str) -> list[dict]:
        return [*task.messages, {"role": "assistant", "content": answer},
                {"role": "user", "content": f"That answer was found wrong:\n{note}\nReply with the corrected answer only."}]

    def _cascade(self, task: Task, result: Result) -> None:
        member, answer = self.ask(result, "worker", task, task.messages, [(1,), (2,), (0,), (3,)])
        ok, note = task.check(answer)
        self.stats.record(member, task.kind, "worker", ok)
        for tiers in ((2,), (3,)):
            if ok:
                break
            try:
                fixer, answer = self.ask(result, "fixer", task, self._fix(task, answer, note), [tiers], avoid_ids=(member.id,))
            except NoMember:
                continue
            ok, note = task.check(answer)
            self.stats.record(fixer, task.kind, "fixer", ok)
        result.answer, result.ok = answer, ok

    def _review(self, task: Task, result: Result, author: Member, answer: str) -> tuple[bool, str]:
        question = last_user(task.messages)
        messages = [{"role": "system", "content": "You review another model's answer to a task. Reply APPROVE if it is "
                                                  "correct and complete. Otherwise reply REVISE: and say exactly what is wrong."},
                    {"role": "user", "content": f"Task:\n{question}\n\nAnswer:\n{answer}"}]
        reviewer, verdict = self.ask(result, "reviewer", task, messages, [(3,), (2,), (1,)],
                                     avoid_ids=(author.id,), avoid_families=(author.family,), prefer_private=False)
        approved = bool(re.match(r"\W*APPROVE", verdict, re.I))
        self.stats.record(author, task.kind, "author", approved)
        return approved, verdict

    def _reviewed(self, task: Task, result: Result) -> None:
        author, answer = self.ask(result, "worker", task, task.messages, [(1,), (2,), (0,), (3,)])
        try:
            approved, note = self._review(task, result, author, answer)
        except NoMember:  # nobody to review it: say so rather than pass it off as checked
            result.answer, result.ok = answer, False
            return
        if not approved:
            _, answer = self.ask(result, "fixer", task, self._fix(task, answer, note), [(2,), (3,), (1,)], avoid_ids=(author.id,))
        result.answer, result.ok = answer, approved  # a fixed answer is better, but nobody has checked it again

    def _panel(self, task: Task, result: Result) -> None:
        chosen, families = [], set()
        for tiers in ((1, 2), (0, 3)):
            for member in self.candidates(task, tiers):
                if member.family not in families and len(chosen) < 3:
                    chosen.append(member)
                    families.add(member.family)
        if not chosen:
            raise NoMember("no local model is free to take a local-only task" if task.sensitivity == "local-only"
                           else "no model is free to answer")
        answers: list[tuple[Member, str]] = []

        def propose(member: Member):
            self.ledger.spend(member)
            try:
                text = str(self.call(member, task.messages) or "").strip()
            except Exception as error:
                self.ledger.cool(member, float(getattr(error, "retry_after", 0) or DEFAULT_COOL))
                return member, None
            return member, text

        with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
            for member, text in pool.map(propose, chosen):
                result.audit.append({"role": "proposer", "member": member.id, "tier": member.tier,
                                     "private": member.private, "ok": bool(text)})
                if text:
                    answers.append((member, text))
        if not answers:
            raise NoMember("every proposer failed")
        if len(answers) == 1:
            author, result.answer = answers[0]
        else:
            labelled = "\n\n".join(f"Answer {chr(65 + i)}:\n{text}" for i, (_, text) in enumerate(answers))
            messages = [{"role": "system", "content": "Several models answered the same task. Write the best final answer: keep what "
                                                      "is right, settle disagreements by reasoning, drop what is wrong. Reply with "
                                                      "the final answer only."},
                        {"role": "user", "content": f"Task:\n{last_user(task.messages)}\n\n{labelled}"}]
            author, result.answer = self.ask(result, "aggregator", task, messages, [(3,), (2,), (1,)],
                                             avoid_families=tuple(m.family for m, _ in answers), prefer_private=False)
        if task.stakes == "high":
            try:
                approved, note = self._review(task, result, author, result.answer)
            except NoMember:
                result.ok = False
                return
            if not approved:
                _, result.answer = self.ask(result, "fixer", task, self._fix(task, result.answer, note), [(3,), (2,)],
                                            avoid_ids=(author.id,))
