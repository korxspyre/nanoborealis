# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Proves that two pool nodes serve one model together.

Waits until the node at API sees a second node, asks for an instance spread over at least two
nodes, then chats with it. Exits non-zero unless the model answered from two machines.

    python3 pooled.py http://127.0.0.1:52415 mlx-community/Qwen3-0.6B-8bit
"""

import json
import sys
import time
import urllib.error
import urllib.request

API, MODEL = sys.argv[1], sys.argv[2]


def call(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def nodes(state):
    return sorted(set(state.get("nodeIdentities") or {}) | set(state.get("lastSeen") or {}))


def instances(state):
    """(model, [node ids]) for each instance, whatever its kind."""
    found = []
    for wrapped in (state.get("instances") or {}).values():
        inner = next(iter(wrapped.values())) if isinstance(wrapped, dict) and len(wrapped) == 1 else wrapped
        shards = inner.get("shardAssignments") or {}
        found.append((shards.get("modelId"), sorted(shards.get("nodeToRunner") or {})))
    return found


def wait(what, seconds, check, every=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            result = check()
            if result:
                return result
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            print(f"  ({what}: {error})", flush=True)
        time.sleep(every)
    sys.exit(f"timed out: {what}")


def two_nodes():
    found = nodes(call("GET", "/state"))
    return found if len(found) >= 2 else None


print("nodes in the pool:", wait("a second node joins", 300, two_nodes), flush=True)


def spread():
    found = [i for i in instances(call("GET", "/state")) if i[0] == MODEL and len(i[1]) >= 2]
    return found[0] if found else None


# The two nodes need a moment to map the links between them, so a first request can be refused.
for attempt in range(1, 6):
    try:
        call("POST", "/place_instance", {"model_id": MODEL, "min_nodes": 2})
    except urllib.error.HTTPError as error:
        print(f"  placement refused: HTTP {error.code} {error.read()[:200]!r}", flush=True)
    deadline = time.time() + 120
    while time.time() < deadline and not spread():
        time.sleep(10)
    if spread():
        break
    print(f"  no instance across two nodes yet (attempt {attempt})", flush=True)
model, members = spread() or sys.exit("exo never placed the model across two nodes")
print(f"{model} runs across {len(members)} nodes: {members}", flush=True)


def answered():
    reply = call("POST", "/v1/chat/completions", {
        "model": MODEL, "max_tokens": 48,
        "messages": [{"role": "user", "content": "Say hello in five words."}]}, timeout=600)
    return reply["choices"][0]["message"]


message = wait("the pooled model answers", 1800, answered, every=15)
print("pooled reply:", (message.get("content") or "").strip()[:200], flush=True)
assert spread(), f"the instance no longer spans two nodes: {instances(call('GET', '/state'))}"
print("ok: one model, served by two machines together")
