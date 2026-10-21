# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Talks to this computer's pool node (exo) for `nanoborealis pool`.

    python3 pool.py wait            wait until the node answers
    python3 pool.py status          the pool's computers, their memory, and the models they serve
    python3 pool.py serve <model>   spread a model over the pool and wait until it answers
    python3 pool.py stop <model>    stop serving a model (its download stays on disk)

The loadout: as many models as the pool can run, each fast enough for an agent (loadout.py).
    python3 pool.py nodes           every computer: its backend, network, memory, and what it holds
    python3 pool.py plan [--json]   the loadout the planner proposes, and what would change
    python3 pool.py apply [--yes]   make the pool run the proposed loadout
    python3 pool.py drain <node>    take a computer out of the loadout (its models move elsewhere)
    python3 pool.py undrain <node>  let the planner use it again
    python3 pool.py bench [<model>...] [--prompt N] [--try <model> [--on a,b]]
                                    measure served models (or try one briefly) and remember it
    python3 pool.py bench --results [--json]   what has been measured so far
    python3 pool.py scores [refresh]   capability scores, or download LiveBench's newest
    python3 pool.py policy [key=value...]   show or change the speed floors, headroom, pins and bans
    python3 pool.py gpu             which pool node this computer runs (NVIDIA CUDA 13, or processor), and why

The API is exo's on this computer (NANOBOREALIS_POOL_API overrides it). The planner's state, its
bench measurements, scores and policy, lives in NANOBOREALIS_POOL_STATE (default
~/.local/state/nanoborealis/pool). LiveBench's scores are refreshed there every two weeks, unless
NANOBOREALIS_POOL_OFFLINE is set. Waiting for a model to get ready stops once nothing about it has
changed for an hour (NANOBOREALIS_POOL_PATIENCE, in seconds).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import loadout
import scores
from benchdb import BenchDB

def pool_api() -> str:
    """exo's API: any node's controls the whole pool. On a computer that coordinates the pool without
    a node of its own, nanoborealis-remote keeps /run/nanoborealis/pool-api pointing at one that
    answers; otherwise it's this computer's own node."""
    if os.environ.get("NANOBOREALIS_POOL_API"):
        return os.environ["NANOBOREALIS_POOL_API"]
    try:
        named = Path(os.environ.get("NANOBOREALIS_POOL_API_FILE") or "/run/nanoborealis/pool-api").read_text().strip()
    except OSError:
        named = ""
    return (named if named.startswith("http") else f"http://{named}") if named else "http://127.0.0.1:52415"


API = pool_api()
STATE_DIR = Path(os.environ.get("NANOBOREALIS_POOL_STATE") or Path.home() / ".local/state/nanoborealis/pool")
# How long a model may sit with nothing changing (no download progress, no new state) before
# waiting for it stops: downloads keep moving, so only a model that's stuck runs into it.
PATIENCE = int(os.environ.get("NANOBOREALIS_POOL_PATIENCE") or 3600)


def call(method: str, path: str, body: object = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def unwrap(value):
    """exo tags variants as {"KindName": {...}}; returns (kind, fields)."""
    if isinstance(value, dict) and len(value) == 1:
        kind, inner = next(iter(value.items()))
        if kind[:1].isupper() and isinstance(inner, dict):
            return kind, inner
    return "", value if isinstance(value, dict) else {}


def size(memory) -> int:
    if isinstance(memory, dict):
        return int(memory.get("inBytes") or 0)
    return int(memory or 0)


def gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


def find(value, key: str):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for inner in value.values():
            found = find(inner, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for inner in value:
            found = find(inner, key)
            if found is not None:
                return found
    return None


def instances(state) -> list[dict]:
    found = []
    for instance_id, wrapped in (state.get("instances") or {}).items():
        _, inner = unwrap(wrapped)
        shards = inner.get("shardAssignments") or {}
        found.append({"id": instance_id, "model": shards.get("modelId"),
                      "nodes": sorted(shards.get("nodeToRunner") or {}),
                      "runners": list((shards.get("nodeToRunner") or {}).values())})
    return found


def names(state) -> dict[str, str]:
    identities = state.get("nodeIdentities") or {}
    return {node: (identities.get(node) or {}).get("friendlyName") or node[:12]
            for node in set(identities) | set(state.get("lastSeen") or {})}


def runner_states(state, runners: list[str]) -> list[str]:
    return [unwrap((state.get("runners") or {}).get(r))[0].removeprefix("Runner") or "?" for r in runners]


def downloads(state, model: str | None = None) -> list[str]:
    lines = []
    for node, entries in (state.get("downloads") or {}).items():
        for entry in entries or []:
            kind, inner = unwrap(entry)
            model_id = find(inner.get("shardMetadata"), "modelId")
            if model and model_id != model:
                continue
            who = names(state).get(node, node[:12])
            if kind == "DownloadOngoing":
                progress = inner.get("downloadProgress") or {}
                done, total = size(progress.get("downloaded")), size(progress.get("total"))
                percent = f"{100 * done / total:.0f}%" if total else "starting"
                lines.append(f"{who}: downloading {model_id} ({percent} of {gb(total)})")
            elif kind == "DownloadFailed":
                lines.append(f"{who}: download of {model_id} failed: {inner.get('errorMessage')}")
            elif kind == "DownloadPending" and model:
                lines.append(f"{who}: waiting to download {model_id}")
    return lines


def wait_for_node(seconds: int = 300) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            call("GET", "/node_id", timeout=5)
            return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(3)
    sys.exit("the pool node didn't start; see: nanoborealis pool logs")


def status() -> None:
    try:
        state = call("GET", "/state")
    except (urllib.error.URLError, OSError) as error:
        sys.exit(f"this computer's pool node isn't answering ({error}); start it with: nanoborealis pool join")
    who = names(state)
    memory = state.get("nodeMemory") or {}
    seen = state.get("lastSeen") or {}
    now = datetime.now(timezone.utc)
    total_ram = free_ram = 0
    print(f"Computers in the pool: {len(who)}")
    for node, name in sorted(who.items(), key=lambda item: item[1].lower()):
        ram = memory.get(node) or {}
        total, free = size(ram.get("ramTotal")), size(ram.get("ramAvailable"))
        total_ram, free_ram = total_ram + total, free_ram + free
        age = ""
        if node in seen:
            try:
                last = datetime.fromisoformat(str(seen[node]).replace("Z", "+00:00"))
                age = f", seen {int((now - last).total_seconds())} s ago"
            except ValueError:
                pass
        print(f"  {name:<28} {gb(free)} free of {gb(total)}{age}")
    print(f"  {'together':<28} {gb(free_ram)} free of {gb(total_ram)}")
    running = instances(state)
    print(f"Models served: {len(running)}")
    for instance in running:
        spread = ", ".join(who.get(n, n[:12]) for n in instance["nodes"])
        states = "/".join(sorted(set(runner_states(state, instance["runners"]))))
        print(f"  {instance['model']}  on {spread}  ({states})")
    for line in downloads(state):
        print(f"  {line}")


def serve(model: str) -> None:
    wait_for_node()
    if not any(i["model"] == model for i in instances(call("GET", "/state"))):
        try:
            call("POST", "/place_instance", {"model_id": model}, timeout=120)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            sys.exit(f"the pool can't serve {model}: HTTP {error.code} {detail}")
    print(f"Spreading {model} over the pool. The first time, every computer that holds a part of it")
    print("downloads that part, which can take a long while for big models. Ctrl+C stops waiting;")
    print("the pool carries on, and 'nanoborealis pool status' shows how far it got.")
    if not wait_serving(model):
        sys.exit(f"{model} isn't ready; see: nanoborealis pool status, and nanoborealis pool logs")


def wait_serving(model: str) -> bool:
    """Wait until every part of `model` is loaded and it answers, reporting downloads meanwhile.
    False when nothing has changed for PATIENCE seconds: the model is stuck where it is."""
    last, started = "", time.time()
    changed = started
    while True:
        state = call("GET", "/state")
        mine = [i for i in instances(state) if i["model"] == model]
        progress = downloads(state, model)
        states = runner_states(state, mine[0]["runners"]) if mine else []
        line = (f"on {len(mine[0]['nodes'])} computer(s): {'/'.join(sorted(set(states)))}" if mine
                else "waiting for the pool to place it")
        line = "; ".join([line, *progress])
        if line != last:  # before the answer check, which can take minutes on a slow pool
            print(f"  {line}", flush=True)
            last, changed = line, time.time()
        if states and all(s in ("Ready", "Running") for s in states):
            if answers(model):
                print(f"{model} is ready, served by {len(mine[0]['nodes'])} computer(s) "
                      f"after {int(time.time() - started)} s.")
                return True
            print("  it's loaded, but didn't answer yet", flush=True)
        if any("failed" in p for p in progress):
            sys.exit("a download failed; try again later, or pick another model")
        if time.time() - changed > PATIENCE:
            waited = f"{PATIENCE // 60} minutes" if PATIENCE >= 120 else f"{PATIENCE} seconds"
            print(f"  nothing has changed for {waited}, so this stops waiting for {model}", flush=True)
            return False
        time.sleep(10)


def answers(model: str) -> bool:
    try:
        reply = call("POST", "/v1/chat/completions", {
            "model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Reply with OK."}]},
            timeout=900)
        return bool(reply.get("choices"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def stop(model: str) -> None:
    stopped = 0
    for instance in instances(call("GET", "/state")):
        if instance["model"] == model:
            call("DELETE", f"/instance/{instance['id']}")
            stopped += 1
    print(f"stopped serving {model}" if stopped else f"{model} wasn't being served")


# -- The loadout ------------------------------------------------------------------------------------

POLICY_FILE = STATE_DIR / "policy.json"
ENGINE = os.environ.get("NANOBOREALIS_POOL_ENGINE") or "exo"


class PlacementError(Exception):
    pass


def load_policy() -> loadout.Policy:
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return loadout.Policy.from_dict(data if isinstance(data, dict) else {})


def save_policy(policy: loadout.Policy) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = POLICY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(POLICY_FILE)


def survey():
    """exo's state, the pool's computers, the catalog, and what's known about every model."""
    state = call("GET", "/state")
    models = loadout.models_from_list(call("GET", "/models"))
    nodes = loadout.nodes_from_state(state, {m.id: m.size for m in models})
    if not os.environ.get("NANOBOREALIS_POOL_OFFLINE"):
        scores.ensure(STATE_DIR)
    facts = BenchDB(STATE_DIR / "bench.sqlite").facts(scores.capabilities(models, STATE_DIR))
    return state, nodes, models, facts


def find_node(nodes: list[loadout.Node], wanted: str) -> loadout.Node:
    for node in nodes:
        if wanted in (node.id, node.name) or node.name.lower() == wanted.lower():
            return node
    starts = [n for n in nodes if n.id.startswith(wanted)]
    if len(starts) == 1:
        return starts[0]
    sys.exit(f"no computer in the pool is called {wanted}; see: nanoborealis pool nodes")


def proposal(rebuild: bool = False):
    state, nodes, models, facts = survey()
    policy = load_policy()
    running = loadout.served(state)
    keep = None if rebuild else loadout.running(running, nodes, models, facts, policy)
    plan = loadout.plan(nodes, models, facts, policy, keep)
    stop_list, start_list, keep_list = loadout.diff(plan, running)
    return nodes, plan, stop_list, start_list, keep_list


def where(ids, nodes: list[loadout.Node]) -> str:
    names = {n.id: n.name for n in nodes}
    return ", ".join(names.get(i, i[:12]) for i in sorted(ids, key=lambda i: names.get(i, i)))


def show_plan(nodes, plan, stop_list, start_list, keep_list) -> None:
    if plan.placements:
        print(f"Proposed loadout: {len(plan.placements)} model(s)")
        print("\n".join(loadout.describe(plan, nodes)))
    else:
        print("The planner has nothing to run on this pool.")
    for note in plan.notes:
        print(f"  note: {note}")
    if not stop_list and not start_list:
        print("The pool already runs this loadout.")
        return
    print("Changes:")
    for instance in stop_list:
        print(f"  stop   {instance['model']} on {where(instance['shares'], nodes)}")
    for placement in start_list:
        print(f"  start  {placement.model.id} on {where(placement.nodes, nodes)}")
    if keep_list:
        print(f"  keep   {len(keep_list)} model(s) already running")


def plan_json(nodes, plan, stop_list, start_list, keep_list) -> dict:
    names = {n.id: n.name for n in nodes}
    return {
        "placements": [{
            "model": p.model.id, "family": p.model.family, "size_gb": round(p.model.size / loadout.GB, 2),
            "moe": p.model.moe, "capability": p.capability, "estimated": p.estimated, "tools": p.tools,
            "gen_tps": round(p.speed.gen_tps, 1), "prompt_tps": round(p.speed.prompt_tps, 1),
            "measured": p.speed.measured,
            "nodes": [{"id": i, "name": names.get(i, i), "share": round(p.shares[i], 3)} for i in p.nodes],
        } for p in plan.placements],
        "notes": plan.notes,
        "stop": [{"id": i["id"], "model": i["model"], "nodes": sorted(i["shares"])} for i in stop_list],
        "start": [p.model.id for p in start_list],
        "keep": [p.model.id for p in keep_list],
    }


def place_on(model: str, node_ids) -> None:
    """Start `model` on exactly these computers: exo previews every placement that includes them,
    and the one using no others is created as it stands."""
    query = urllib.parse.urlencode([("model_id", model), *[("node_ids", n) for n in node_ids]])
    previews = (call("GET", f"/instance/previews?{query}", timeout=120) or {}).get("previews") or []
    wanted, errors = set(node_ids), []
    for preview in previews:
        if preview.get("error") or not preview.get("instance"):
            errors.append(str(preview.get("error") or "no instance"))
            continue
        if preview.get("sharding") != "Pipeline":
            continue
        _, inner = unwrap(preview["instance"])
        assignments = inner.get("shardAssignments") or inner.get("shard_assignments") or {}
        if set(assignments.get("nodeToRunner") or assignments.get("node_to_runner") or {}) == wanted:
            try:
                call("POST", "/instance", {"instance": preview["instance"]}, timeout=120)
            except urllib.error.HTTPError as error:
                raise PlacementError(f"HTTP {error.code} {error.read().decode(errors='replace')[:300]}") from error
            return
    raise PlacementError(errors[0] if errors else "exo offers no placement on exactly those computers")


def apply_changes(nodes, stop_list, start_list) -> bool:
    ok = True
    for instance in stop_list:  # first, so their memory is free for what starts
        call("DELETE", f"/instance/{instance['id']}")
        print(f"stopped {instance['model']} on {where(instance['shares'], nodes)}")
    for placement in start_list:
        print(f"starting {placement.model.id} on {where(placement.nodes, nodes)}")
        try:
            place_on(placement.model.id, placement.nodes)
        except PlacementError as error:
            print(f"  exo wouldn't place it: {error}")
            ok = False
            continue
        if not wait_serving(placement.model.id):
            ok = False
    return ok


def confirm(question: str, args: list[str]) -> bool:
    if "--yes" in args:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False


def cmd_nodes(args: list[str]) -> None:
    state, nodes, models, _ = survey()
    policy = load_policy()
    held: dict[str, list[str]] = {}
    for instance in loadout.served(state):
        for node, share in instance["shares"].items():
            held.setdefault(node, []).append(f"{instance['model']} ({share:.0%})")
    if "--json" in args:
        print(json.dumps([{**n.__dict__, "drained": loadout.excluded(n, policy), "holds": held.get(n.id, [])}
                          for n in nodes], indent=2))
        return
    print(f"Computers in the pool: {len(nodes)}")
    for node in sorted(nodes, key=lambda n: (-loadout.BACKEND_RANK[n.backend], n.name.lower())):
        flag = "  DRAINED" if loadout.excluded(node, policy) else ""
        print(f"  {node.name:<24} {node.backend:<5} {node.link:<11} {gb(node.mem_capacity)} for models "
              f"of {gb(node.mem_total)}{flag}")
        for line in held.get(node.id, []):
            print(f"      holds {line}")


def cmd_plan(args: list[str]) -> None:
    result = proposal(rebuild="--rebuild" in args)
    if "--json" in args:
        print(json.dumps(plan_json(*result), indent=2))
    else:
        show_plan(*result)


def cmd_apply(args: list[str]) -> None:
    nodes, plan, stop_list, start_list, keep_list = proposal(rebuild="--rebuild" in args)
    show_plan(nodes, plan, stop_list, start_list, keep_list)
    if not stop_list and not start_list:
        return
    if not confirm("Apply it?", args):
        print("Nothing changed.")
        return
    if not apply_changes(nodes, stop_list, start_list):
        sys.exit("some models couldn't be placed; see above")
    print("The pool runs the new loadout.")


def cmd_drain(args: list[str]) -> None:
    names = [a for a in args if not a.startswith("--")]
    if len(names) != 1:
        sys.exit("usage: pool.py drain <computer> [--yes]")
    _, nodes, _, _ = survey()
    node = find_node(nodes, names[0])
    policy = load_policy()
    if node.id not in policy.excluded_nodes:
        policy.excluded_nodes.append(node.id)
        save_policy(policy)
    nodes, plan, stop_list, start_list, keep_list = proposal()
    show_plan(nodes, plan, stop_list, start_list, keep_list)
    if (stop_list or start_list) and confirm(f"Move the models off {node.name}?", args):
        if not apply_changes(nodes, stop_list, start_list):
            sys.exit(f"some models couldn't move off {node.name}; see above. The planner won't put new "
                     f"ones on it, and 'nanoborealis pool apply' tries again.")
    print(f"{node.name} is drained: the planner won't put models on it. It can leave the pool now "
          f"(on it: nanoborealis pool leave), or come back with: nanoborealis pool undrain {node.name}")


def cmd_undrain(args: list[str]) -> None:
    if len(args) != 1:
        sys.exit("usage: pool.py undrain <computer>")
    policy = load_policy()
    _, nodes, _, _ = survey()
    node = next((n for n in nodes if args[0] in (n.id, n.name) or n.name.lower() == args[0].lower()), None)
    gone = {args[0]} | ({node.id, node.name} if node else set())
    policy.excluded_nodes = [n for n in policy.excluded_nodes if n not in gone]
    save_policy(policy)
    print(f"The planner can use {node.name if node else args[0]} again; grow the loadout onto it with: "
          "nanoborealis pool apply")


FILLER = ("def merge(intervals):\n    intervals.sort(key=lambda pair: pair[0])\n    merged = []\n"
          "    for start, end in intervals:\n        if merged and start <= merged[-1][1]:\n"
          "            merged[-1][1] = max(merged[-1][1], end)\n        else:\n"
          "            merged.append([start, end])\n    return merged\n\n")


def measure(model: str, prompt_tokens: int) -> tuple[float, float, int]:
    """(prompt tok/s, writing tok/s, prompt tokens) from exo's bench endpoint, on an agent-sized prompt."""
    repeats = max(1, prompt_tokens // 80)  # the filler is about 80 tokens
    prompt = FILLER * repeats + "Summarise the code above in one sentence."
    reply = call("POST", "/bench/chat/completions", {
        "model": model, "max_tokens": 128, "messages": [{"role": "user", "content": prompt}]}, timeout=3600)
    stats = reply.get("generation_stats") or reply.get("generationStats") or {}
    return (float(stats.get("prompt_tps") or stats.get("promptTps") or 0),
            float(stats.get("generation_tps") or stats.get("generationTps") or 0),
            int(stats.get("prompt_tokens") or stats.get("promptTokens") or 0))


def tool_check(model: str) -> bool:
    """Does the model call a tool when it should, in the form agents need?"""
    try:
        reply = call("POST", "/v1/chat/completions", {
            "model": model, "max_tokens": 512,
            "messages": [{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
            "tools": [{"type": "function", "function": {
                "name": "get_weather", "description": "The current weather in a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}],
        }, timeout=900)
        calls = ((reply.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
        return bool(calls) and (calls[0].get("function") or {}).get("name") == "get_weather"
    except (urllib.error.URLError, OSError, ValueError, IndexError, AttributeError):
        return False


def bench_one(instance: dict, nodes, models, prompt_tokens: int, check_tools: bool) -> None:
    by_node = {n.id: n for n in nodes}
    group = [by_node[i] for i in instance["shares"] if i in by_node]
    model = next((m for m in models if m.id == instance["model"]), None)
    policy = load_policy()
    prompt_tps, gen_tps, tokens = measure(instance["model"], prompt_tokens)
    tools = tool_check(instance["model"]) if check_tools else None
    predicted = loadout.predict(model, group, instance["shares"]) if model and group else None
    BenchDB(STATE_DIR / "bench.sqlite").record(
        engine=ENGINE, model=instance["model"], nodes={n.id: n.name for n in group},
        backends={n.backend for n in group}, prompt_tokens=tokens or prompt_tokens,
        prompt_tps=prompt_tps, gen_tps=gen_tps, tools=tools, predicted=predicted)
    fast = gen_tps >= policy.min_gen_tps and prompt_tps >= policy.min_prompt_tps
    verdict = "fast enough" if fast else "too slow"
    tool_text = {True: "tool call ok", False: "tool call FAILED", None: "tools not checked"}[tools]
    print(f"  {instance['model']} on {where(instance['shares'], nodes)}: {gen_tps:.1f} tok/s writing, "
          f"{prompt_tps:.0f} reading {tokens or prompt_tokens} tokens; {tool_text}; {verdict} for an agent")


def show_results(as_json: bool = False) -> None:
    rows = BenchDB(STATE_DIR / "bench.sqlite").latest()
    policy = load_policy()
    if as_json:
        print(json.dumps([{**dict(row), "fast": row["gen_tps"] >= policy.min_gen_tps and
                           row["prompt_tps"] >= policy.min_prompt_tps} for row in rows], indent=2))
        return
    if not rows:
        print("Nothing measured yet. Measure what the pool serves with: nanoborealis pool bench")
        return
    print("What runs on this pool (newest measurement of each model on each set of computers):")
    for row in rows:
        fast = row["gen_tps"] >= policy.min_gen_tps and row["prompt_tps"] >= policy.min_prompt_tps
        tools = {1: "tools ok", 0: "tools FAILED", None: "tools ?"}[row["tools"]]
        print(f"  {'✓' if fast else '✗'} {row['model']:<44} {row['gen_tps']:6.1f} tok/s  "
              f"{row['prompt_tps']:7.0f} reading  {tools:<12} on {row['names']}")


def cmd_bench(args: list[str]) -> None:
    prompt_tokens, check_tools, trial, on, wanted = 4096, True, None, None, []
    rest = iter(args)
    for arg in rest:
        if arg == "--prompt":
            prompt_tokens = int(next(rest, "4096"))
        elif arg == "--no-tools":
            check_tools = False
        elif arg == "--try":
            trial = next(rest, None)
        elif arg == "--on":
            on = [a for a in next(rest, "").split(",") if a]
        elif arg == "--results":
            show_results(as_json="--json" in args)
            return
        else:
            wanted.append(arg)
    state, nodes, models, facts = survey()
    if trial:
        model = next((m for m in models if m.id == trial), None)
        if model is None:
            sys.exit(f"{trial} isn't in exo's catalog")
        if any(i["model"] == trial for i in loadout.served(state)):
            sys.exit(f"{trial} is served already; measure it where it runs with: nanoborealis pool bench {trial}")
        if on:
            group = [find_node(nodes, n).id for n in on]
        else:  # where the planner would put it, speed floors aside: that's what the bench finds out
            relaxed = loadout.Policy.from_dict({**load_policy().to_dict(), "min_gen_tps": 0, "min_prompt_tps": 0,
                                                "pinned": [], "max_models": 1})
            placed = loadout.plan(nodes, [model], facts, relaxed).placements
            if not placed:
                sys.exit(f"{trial} doesn't fit on the pool's free memory")
            group = list(placed[0].nodes)
        print(f"Trying {trial} on {where(group, nodes)}")
        try:
            place_on(trial, group)
        except PlacementError as error:
            sys.exit(f"exo wouldn't place it: {error}")
        if not wait_serving(trial):
            stop(trial)
            sys.exit(f"{trial} didn't get ready, so it wasn't measured")
        state = call("GET", "/state")
        mine = [i for i in loadout.served(state) if i["model"] == trial]
        try:
            for instance in mine:
                bench_one(instance, nodes, models, prompt_tokens, check_tools)
        finally:
            for instance in mine:
                call("DELETE", f"/instance/{instance['id']}")
            print(f"stopped {trial} again (its download stays for next time)")
        return
    targets = [i for i in loadout.served(state) if not wanted or i["model"] in wanted]
    if not targets:
        sys.exit("nothing to measure: the pool serves no model" + (" of those" if wanted else "") +
                 ". Try one with: nanoborealis pool bench --try <model>")
    print(f"Measuring {len(targets)} model(s) on a {prompt_tokens}-token prompt:")
    for instance in targets:
        bench_one(instance, nodes, models, prompt_tokens, check_tools)


def cmd_scores(args: list[str]) -> None:
    if args[:1] == ["refresh"]:
        release, count = scores.refresh(STATE_DIR)
        print(f"LiveBench {release}: {count} models saved")
        return
    models = loadout.models_from_list(call("GET", "/models"))
    rows = sorted(((scores.details(m, STATE_DIR), m) for m in models), key=lambda r: -r[0]["overall"])
    print("Capability of the models in exo's catalog (≈: an estimate):")
    for info, model in rows:
        mark = "≈" if info["source"] == "guessed from size" or model.quant.lower() in scores.QUANT_FACTOR else " "
        print(f"  {mark}{info['overall']:5.1f}  {model.id:<48} {info['source']}")


def cmd_policy(args: list[str]) -> None:
    policy = load_policy()
    data = policy.to_dict()
    for pair in args:
        key, _, value = pair.partition("=")
        if key not in data:
            sys.exit(f"unknown setting {key}; the settings are: {', '.join(data)}")
        current = data[key]
        if isinstance(current, list):
            data[key] = [v for v in value.split(",") if v]
        elif isinstance(current, int) and not isinstance(current, bool):
            data[key] = int(value)
        else:
            data[key] = float(value)
    if args:
        policy = loadout.Policy.from_dict(data)
        save_policy(policy)
    print(json.dumps(policy.to_dict(), indent=2))


# -- This computer's GPU ----------------------------------------------------------------------------

MIN_COMPUTE = (7, 5)  # CUDA 13 runs on Turing (RTX 20 series) and newer
MIN_DRIVER = 580  # the first NVIDIA driver series with CUDA 13
DRIVER_RESERVE_MB = 1024  # VRAM the driver and CUDA's own context keep for themselves
CDI_DIRS = os.environ.get("NANOBOREALIS_CDI_DIRS") or "/etc/cdi:/var/run/cdi"


def containers_see_gpu() -> bool:
    """Whether the NVIDIA Container Toolkit has described the GPU for containers (a CDI spec)."""
    for folder in filter(None, CDI_DIRS.split(":")):
        try:
            for spec in Path(folder).iterdir():
                if spec.suffix in (".yaml", ".yml", ".json") and "nvidia.com/gpu" in spec.read_text(errors="replace"):
                    return True
        except OSError:
            continue
    return False


def gpu_report() -> dict:
    """Which pool node this computer should run: {"variant": "cuda13" or "cpu", "reason": why, and
    for an NVIDIA GPU its name, compute capability, driver, VRAM and the memory exo should report}.
    exo counts a node's system memory; on a GPU the model lives in VRAM, so the node is told to
    report that instead (OVERRIDE_MEMORY_MB), and exo splits models by it."""
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap,driver_version,memory.total",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {"variant": "cpu", "reason": "no NVIDIA GPU found"}
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        return {"variant": "cpu", "reason": "nvidia-smi sees no GPU; is the NVIDIA driver loaded?"}
    fields = [f.strip() for f in lines[0].split(",")]
    if len(fields) < 4:
        return {"variant": "cpu", "reason": f"nvidia-smi answered something unexpected: {lines[0]}"}
    name, compute, driver, memory = fields[:4]
    report = {"variant": "cpu", "gpu": name, "gpus": len(lines), "compute": compute, "driver": driver}
    try:
        report["vram_mb"] = int(float(memory))
        capability = tuple(int(p) for p in compute.split(".")[:2])
        driver_major = int(driver.split(".")[0])
    except ValueError:
        report["reason"] = f"nvidia-smi answered something unexpected: {lines[0]}"
        return report
    if capability < MIN_COMPUTE:
        report["reason"] = f"the {name} is too old for CUDA 13, which needs an RTX 20 series or newer"
    elif driver_major < MIN_DRIVER:
        report["reason"] = f"its driver {driver} is older than {MIN_DRIVER}, which CUDA 13 needs; update the NVIDIA driver"
    elif not containers_see_gpu():
        report["reason"] = ("containers can't reach it yet: install the NVIDIA Container Toolkit, then "
                            "sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml")
    else:
        report["variant"] = "cuda13"
        report["override_mb"] = max(512, report["vram_mb"] - DRIVER_RESERVE_MB)
        report["reason"] = f"the {name} runs the pool's models, with {report['override_mb'] / 1024:.1f} GB of its memory"
    return report


def cmd_gpu(args: list[str]) -> None:
    print(json.dumps(gpu_report(), indent=None if "--line" in args else 2))


COMMANDS = {"nodes": cmd_nodes, "plan": cmd_plan, "apply": cmd_apply, "drain": cmd_drain, "undrain": cmd_undrain,
            "bench": cmd_bench, "scores": cmd_scores, "policy": cmd_policy, "gpu": cmd_gpu}


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if action == "wait":
            wait_for_node()
        elif action == "status":
            status()
        elif action == "serve" and len(sys.argv) == 3:
            serve(sys.argv[2])
        elif action == "stop" and len(sys.argv) == 3:
            stop(sys.argv[2])
        elif action in COMMANDS:
            COMMANDS[action](sys.argv[2:])
        else:
            sys.exit(__doc__)
    except KeyboardInterrupt:
        print("\nStopped waiting; the pool carries on. Check on it with: nanoborealis pool status")
    except urllib.error.URLError as error:  # HTTPError too: exo is down, or turned a request down
        sys.exit(f"this computer's pool node didn't answer ({getattr(error, 'reason', error)}); "
                 "see: nanoborealis pool status")
