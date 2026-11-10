# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Rigs: a team of seats written down as a file, with shared norms, and the logic around them.

Pure logic, no network and no database: the Hall (nanoborealis-hall) keeps the state and calls in here.
A rig is the idea OpenRig calls by that name, for NanoBorealis' council:

    [rig]
    name = "starter"
    culture = "We make small, tested changes. We say when we are unsure."

    [[pod]]
    id = "dev"
    guidance = "Builds and reviews the code."

    [[seat]]
    id = "build"              # becomes the seat "starter-build", addressed as build@starter
    pod = "dev"
    kind = "pool"             # run on the pool as its own agent; the Hall can ask it
    model = "mlx-community/Qwen3-14B-4bit"
    capability = 45

    [[seat]]
    id = "claude"
    pod = "dev"
    kind = "outside"          # joins over the Hall's API (Claude Code, say); the Hall can't ask it
    head = 1                  # first in line to be the head agent while it is available

    [[edge]]
    from = "build"
    to = "claude"
    kind = "reviews"          # claude reviews build's work

A seat's identity is its address; the conversation or process behind it can change. `culture` is what
every seat is told first, then its pod's guidance, then its own. TOML, because it is in Python's standard
library: nothing to install on the image.
"""

from __future__ import annotations

import re
import tomllib

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
POLICIES = ("floor", "inherit", "full_bypass")  # what a seat may do on its own: only the floor, what its rig allows, anything
# OpenRig's edge kinds, and ours from before (reviews, delegates, reports_to)
EDGE_KINDS = ("reviews", "delegates", "reports_to", "delegates_to", "spawned_by", "can_observe", "collaborates_with",
              "escalates_to")
# pool: its own agent on the pool; outside: joins over the API (Claude Code); cloud: a free OpenRouter model the Hall
# asks itself, through the tokenizer, so a rig runs on a computer without a pool or a GPU.
# agent: its own full agent in its own container (rigagent.py), every tool the owner's agent has, on a free
# OpenRouter model: what a seat is in OpenRig. The default.
KINDS = ("agent", "pool", "outside", "cloud")
MAX_SEATS = 32
MAX_AGENT_SEATS = 10  # each is a container with its own memory
FRESH = 120.0  # seconds a heartbeat counts as being there


class SpecError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def hall_id(rig: str, local: str) -> str:
    return f"{rig}-{local}"


def address(rig: str, local: str) -> str:
    return f"{local}@{rig}"


def parse(text: str) -> dict:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SpecError([f"the spec isn't valid TOML: {error}"]) from None
    return validate(data)


def validate(data: dict) -> dict:
    """The spec as the Hall stores it, or SpecError with everything wrong at once."""
    errors: list[str] = []
    rig = data.get("rig") if isinstance(data.get("rig"), dict) else {}
    name = str(rig.get("name") or "")
    if not NAME.fullmatch(name):
        errors.append("[rig] needs a name of lowercase letters, digits and dashes (20 at most)")
    culture = str(rig.get("culture") or "")[:4000]
    pods, seen_pods = [], set()
    for pod in data.get("pod") or []:
        pid = str(pod.get("id") or "") if isinstance(pod, dict) else ""
        if not NAME.fullmatch(pid) or pid in seen_pods:
            errors.append(f"pod {pid!r}: an id of lowercase letters, digits and dashes, used once")
            continue
        seen_pods.add(pid)
        pods.append({"id": pid, "guidance": str(pod.get("guidance") or "")[:2000]})
    seats, seen = [], set()
    for seat in data.get("seat") or []:
        local = str(seat.get("id") or "") if isinstance(seat, dict) else ""
        if not NAME.fullmatch(local) or local in seen:
            errors.append(f"seat {local!r}: an id of lowercase letters, digits and dashes, used once")
            continue
        seen.add(local)
        kind = str(seat.get("kind") or "agent")
        if kind not in KINDS:
            errors.append(f"seat {local}: kind is one of {', '.join(KINDS)}")
        model = str(seat.get("model") or ("an outside agent" if kind == "outside" else ""))
        if kind == "pool" and not model:
            errors.append(f"seat {local}: a pool seat names its model")
        if kind in ("cloud", "agent") and not model.endswith(":free"):
            errors.append(f"seat {local}: a {kind} seat names a free OpenRouter model (its id ends in :free)")
        capability = seat.get("capability", 50)
        if isinstance(capability, bool) or not isinstance(capability, (int, float)) or not 0 <= capability <= 100:
            errors.append(f"seat {local}: capability is 0 to 100")
        policy = str(seat.get("policy") or "inherit")
        if policy not in POLICIES:
            errors.append(f"seat {local}: policy is one of {', '.join(POLICIES)}")
        head = seat.get("head")
        if head is not None and (isinstance(head, bool) or not isinstance(head, int) or head < 1):
            errors.append(f"seat {local}: head is a rank, 1 for first in line")
        pod = str(seat.get("pod") or "")
        if pod and pod not in seen_pods:
            errors.append(f"seat {local}: there is no pod {pod!r}")
        if name and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", hall_id(name, local)):
            errors.append(f"seat {local}: {hall_id(name, local)!r} is too long for a seat id (40 at most)")
        seats.append({"local": local, "id": hall_id(name, local), "address": address(name, local), "pod": pod,
                      "role": str(seat.get("role") or "")[:80], "kind": kind, "model": model, "capability": capability,
                      "policy": policy, "head_rank": head, "guidance": str(seat.get("guidance") or "")[:2000]})
    if len(seats) > MAX_SEATS:
        errors.append(f"a rig has {MAX_SEATS} seats at most")
    if sum(s["kind"] == "agent" for s in seats) > MAX_AGENT_SEATS:
        errors.append(f"a rig has {MAX_AGENT_SEATS} agent seats at most: each is a container with its own memory")
    edges = []
    for edge in data.get("edge") or []:
        if not isinstance(edge, dict):
            continue
        src, dst, kind = str(edge.get("from") or ""), str(edge.get("to") or ""), str(edge.get("kind") or "")
        if src not in seen or dst not in seen or src == dst or kind not in EDGE_KINDS:
            errors.append(f"edge {src!r} -> {dst!r}: two different seats of this rig, and a kind of {', '.join(EDGE_KINDS)}")
            continue
        edges.append({"src": hall_id(name, src), "dst": hall_id(name, dst), "kind": kind})
    if errors:
        raise SpecError(errors)
    return {"name": name, "culture": culture, "pods": pods, "seats": seats, "edges": edges}


def plan(spec: dict, existing: dict[str, dict]) -> dict:
    """What applying `spec` would do, given the Hall's seats now (id -> seat): the `--plan` of a rig."""
    wanted = {s["id"]: s for s in spec["seats"]}
    mine = {i: s for i, s in existing.items() if s.get("rig") == spec["name"]}
    changed = lambda w, e: any(str(w[k]) != str(e.get(k) or "") for k in ("model", "capability", "pod", "role", "policy"))  # noqa: E731
    return {"create": [s["address"] for i, s in wanted.items() if i not in existing],
            "update": [s["address"] for i, s in wanted.items() if i in existing and changed(s, existing[i])],
            "keep": [s["address"] for i, s in wanted.items() if i in existing and not changed(s, existing[i])],
            "remove": [e.get("address") or i for i, e in mine.items() if i not in wanted],
            "taken": [i for i in wanted if i in existing and existing[i].get("rig") not in (None, "", spec["name"])]}


def briefing(rig: dict | None, pod: dict | None, seat: dict) -> str:
    """What a seat is told before anything else: the rig's norms, then its pod's guidance, then its own."""
    parts = []
    if rig and rig.get("culture"):
        parts.append(f"How this team works ({rig['name']}): {rig['culture']}")
    if pod and pod.get("guidance"):
        parts.append(f"Your pod ({pod['id']}): {pod['guidance']}")
    own = " ".join(x for x in (f"You are {seat.get('address') or seat.get('id')}." if seat.get("rig") else "",
                               f"Your role: {seat['role']}." if seat.get("role") else "", seat.get("guidance") or "") if x)
    if own:
        parts.append(own)
    return "\n".join(parts)


# -- Who is the head -------------------------------------------------------------------------------------------


def askable(seat: dict) -> bool:
    """The Hall can ask it any time: a pool seat (it has an API) or a cloud seat (the Hall calls its model)."""
    return seat.get("port") is not None or seat.get("kind") == "cloud"


def pick_head(seats: list[dict], presence: dict[str, dict], now: float) -> dict:
    """The head agent: the first seat in line (head_rank) that is there and not worn out, else the strongest seat
    the Hall can ask itself, else the chair. A seat that isn't run by the pool (Claude, say) is there while it
    keeps sending a heartbeat with status "ok"; "limited" means its usage is spent, so the next one steps in."""
    line = sorted((s for s in seats if s.get("head_rank")), key=lambda s: (s["head_rank"], -float(s.get("weight") or 0)))
    states = []
    for seat in line:
        beat = presence.get(seat["id"])
        if askable(seat):  # the Hall can ask it any time
            state = "ready"
        elif not beat or now - float(beat["at"]) > FRESH:
            state = "away"
        else:
            state = "limited" if beat.get("status") == "limited" else "ready"
        states.append({"seat": seat["id"], "rank": seat["head_rank"], "state": state})
    for entry in states:
        if entry["state"] == "ready":
            return {"head": entry["seat"], "reason": f"first in line that is here (rank {entry['rank']})", "line": states}
    asked = sorted((s for s in seats if askable(s)), key=lambda s: -float(s.get("weight") or 0))
    if asked:
        return {"head": asked[0]["id"], "reason": "nobody in line is here, so the strongest seat the Hall can ask steps in", "line": states}
    chair = next((s for s in seats if s.get("chair")), None)
    return {"head": chair["id"] if chair else None, "reason": "nobody else is here" if chair else "no seats", "line": states}


# -- The watchdog's decisions ------------------------------------------------------------------------------------


def overdue_mail(items: list[dict], now: float, after: float = 120.0, tries: int = 3) -> tuple[list[dict], list[dict]]:
    """(to deliver again, to give up on): mail still new after `after` seconds is tried again, up to `tries`."""
    again, give_up = [], []
    for item in items:
        if item["state"] != "new" or now - item["created"] < after:
            continue
        (give_up if item["tries"] >= tries else again).append(item)
    return again, give_up


def stale_tasks(tasks: list[dict], now: float, after: float = 1800.0, nudges: int = 2) -> tuple[list[dict], list[dict]]:
    """(to nudge, to reopen): a task held for `after` seconds with no word from its seat is nudged; after
    `nudges` nudges it goes back on the board for someone else."""
    nudge, reopen = [], []
    for task in tasks:
        if task["state"] != "claimed" or now - task["last_word"] < after:
            continue
        (reopen if task["nudges"] >= nudges else nudge).append(task)
    return nudge, reopen


def restore_outcomes(snapshot: dict, current: dict[str, dict], models: set[str] | None) -> list[dict]:
    """Per seat in a snapshot, honestly: resumed (it is there as it was), fresh-primed (made again, with its
    open work handed back, but its agent has to be started), awaiting-decision (its model isn't available:
    you choose), or failed. `models` is the pool's available models, or None when that isn't known."""
    out = []
    for seat in snapshot.get("seats", []):
        now = current.get(seat["id"])
        if now is not None and now.get("model") == seat.get("model"):
            out.append({"seat": seat["address"] or seat["id"], "outcome": "resumed", "note": "its place, history and memory are as they were"})
        elif seat.get("kind") == "pool" and models is not None and seat.get("model") not in models:
            out.append({"seat": seat["address"] or seat["id"], "outcome": "awaiting-decision",
                        "note": f"its model {seat.get('model')} isn't in the pool now: pick another, or bring that model back"})
        else:
            out.append({"seat": seat["address"] or seat["id"], "outcome": "fresh-primed",
                        "note": "made again with its open work; start its agent with: nanoborealis council apply"
                        if seat.get("kind") == "pool" else "made again with a new token; its open work is waiting in its inbox"})
    return out
