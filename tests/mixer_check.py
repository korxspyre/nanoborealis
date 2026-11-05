# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the mixer (mixed intelligence): its recipes, its quotas and its privacy rules against stand-in
models, and the real pool relay's /mix route in front of a stand-in pool and a stand-in cloud model.

    python3 tests/mixer_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "system_files/usr/share/nanoborealis"
sys.path.insert(0, str(TOOLS))

import mixer  # noqa: E402
from mixer import Ledger, Member, Mixer, NoMember, Stats, Task  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


# -- The mixer on its own ------------------------------------------------------------------------------

LOCAL = Member("pool", "local-14b", "qwen", 42, private=True)                        # worker, on the owner's machines
SMALL = Member("cloud", "small:free", "gemma", 30, rpm=2)                            # worker, cloud
MID = Member("cloud", "mid:free", "llama", 52)                                       # specialist
BIG = Member("cloud", "big:free", "nvidia", 66)                                      # anchor
BIG2 = Member("cloud", "big2:free", "deepseek", 63)                                  # anchor, another family
ALL = [LOCAL, SMALL, MID, BIG, BIG2]

check([m.tier for m in (Member("r", "a", "f", 10), SMALL, LOCAL, MID, BIG)] == [0, 1, 1, 2, 3], "capability sorts members into four tiers")

now = [1000.0]
ledger = Ledger(clock=lambda: now[0])
check(ledger.ready(SMALL), "a fresh member is ready")
ledger.spend(SMALL)
ledger.spend(SMALL)
check(not ledger.ready(SMALL), "a member at its requests-a-minute limit rests")
now[0] += 61
check(ledger.ready(SMALL), "and is ready again a minute later")
day = Member("r", "d", "f", 50, per_day=3)
for _ in range(3):
    ledger.spend(day)
now[0] += 3600
check(not ledger.ready(day), "a daily limit holds for the day")
now[0] += 86400
check(ledger.ready(day), "and lifts after it")
ledger.cool(BIG, 30)
check(not ledger.ready(BIG), "a member that failed rests")
now[0] += 31
check(ledger.ready(BIG), "and comes back")


class Models:
    """Stand-in models: records every call, answers by role."""

    def __init__(self, check_after=0, broken=()):
        self.calls, self.broken, self.fixes = [], set(broken), 0

    def __call__(self, member, messages):
        self.calls.append((member.id, messages))
        if member.id in self.broken:
            error = RuntimeError("busy")
            error.retry_after = 120
            raise error
        system = messages[0]["content"] if messages[0]["role"] == "system" else ""
        if system.startswith("You review"):
            return "APPROVE" if "good" in messages[-1]["content"] else "REVISE: it is wrong"
        if system.startswith("Several models"):
            return "good synthesis"
        if "found wrong" in messages[-1]["content"]:
            self.fixes += 1
            return "good fix" if self.fixes >= 1 else "bad"
        return f"answer by {member.model}"

    def ids(self):
        return [c[0] for c in self.calls]


ask = [{"role": "system", "content": "secret history that only the proposer may see"},
       {"role": "user", "content": "write a sort"}]

models = Models()
result = Mixer(ALL, models, Ledger(), Stats()).run(Task(ask))
check(result.recipe == "single" and models.ids() == ["pool:local-14b"], "a plain request goes to one worker, a private one first")

models = Models()
result = Mixer(ALL, models, Ledger(), Stats()).run(Task(ask, kind="code"))
check(result.recipe == "reviewed" and models.ids()[0] == "pool:local-14b" and models.ids()[1] in ("cloud:big:free", "cloud:big2:free"),
      "code is written by a worker and reviewed by a strong model of another family")
review = models.calls[1][1]
check("secret history" not in json.dumps(review) and "write a sort" in json.dumps(review) and "answer by local-14b" in json.dumps(review),
      "the reviewer sees the question and the answer, not the history")
check(not result.ok and models.ids()[2] in ("cloud:mid:free", "cloud:big:free", "cloud:big2:free") and result.answer == "good fix",
      "a rejected answer is fixed by a stronger model, and is not passed off as checked")
check(all(set(a) == {"role", "member", "tier", "private", "ok"} for a in result.audit), "the audit says who played which role and nothing else")

# An objective check escalates tier by tier.
verdicts = iter([(False, "test 1 failed"), (False, "test 2 failed"), (True, "")])
models = Models()
stats = Stats()
result = Mixer(ALL, models, Ledger(), stats).run(Task(ask, kind="code", check=lambda answer: next(verdicts)))
roles = [a["role"] for a in result.audit]
tiers = [a["tier"] for a in result.audit]
check(result.recipe == "cascade" and result.ok and roles == ["worker", "fixer", "fixer"] and tiers == [1, 2, 3],
      "a failing test escalates a worker, then a specialist, then an anchor, until it passes")
check(stats.rate(LOCAL, "code") < 0.5, "a worker whose answer failed is trusted less for that kind of task afterwards")

# A panel: different families, an unattributed synthesis, and a review when it matters.
models = Models()
result = Mixer(ALL, models, Ledger(), Stats()).run(Task(ask, kind="reasoning", stakes="high"))
proposers = [a["member"] for a in result.audit if a["role"] == "proposer"]
families = {m.family for m in ALL if m.id in proposers}
check(result.recipe == "panel" and len(proposers) == 3 and len(families) == 3, "a panel is three models of three different families")
synth = next(c[1] for c in models.calls if c[1][0]["content"].startswith("Several models"))
check("Answer A" in synth[-1]["content"] and "Answer C" in synth[-1]["content"]
      and not any(m.id in synth[-1]["content"] or m.family in synth[-1]["content"] for m in ALL),
      "the synthesis sees the answers labelled A, B and C, not by who wrote them")
check([a["role"] for a in result.audit][-2:] == ["aggregator", "reviewer"] and result.ok, "a high-stakes panel is reviewed after the synthesis")

# A member that fails rests, and the next one takes over.
models = Models(broken=["pool:local-14b"])
ledger = Ledger()
result = Mixer(ALL, models, ledger, Stats()).run(Task(ask))
check(models.ids()[0] == "pool:local-14b" and models.ids()[1] != "pool:local-14b" and not ledger.ready(LOCAL)
      and result.audit[0]["ok"] is False and result.audit[1]["ok"], "a failing member rests and the next one answers")

# Privacy: a local-only task never reaches a member that isn't private.
models = Models()
result = Mixer(ALL, models, Ledger(), Stats()).run(Task(ask, sensitivity="local-only"))
check(models.ids() == ["pool:local-14b"], "a local-only task is answered by a private member")
models = Models()
try:
    Mixer(ALL, models, Ledger(), Stats()).run(Task(ask, kind="reasoning", sensitivity="local-only"))
    panel_ok = True
except NoMember:
    panel_ok = False
check(panel_ok and set(models.ids()) == {"pool:local-14b"}, "a local-only panel is only the private members, however few")
models = Models()
try:
    Mixer([SMALL, MID, BIG], models, Ledger(), Stats()).run(Task(ask, sensitivity="local-only"))
    refused = False
except NoMember:
    refused = True
check(refused and models.calls == [], "with no private member a local-only task fails, and the cloud is not asked")
models = Models(broken=["pool:local-14b"])
try:
    Mixer(ALL, models, Ledger(), Stats()).run(Task(ask, sensitivity="local-only"))
    fell_back = True
except NoMember:
    fell_back = False
check(not fell_back and all(c[0] == "pool:local-14b" for c in models.calls), "when the private member fails, a local-only task still doesn't fall back to the cloud")
check(not mixer.eligible(BIG, Task(ask, sensitivity="local-only")) and mixer.eligible(LOCAL, Task(ask, sensitivity="local-only")),
      "eligibility is decided by the member, not the call")
loaded = mixer.load_members(None)
check(len(loaded) == 5 and {m.route for m in loaded} == {"openrouter"} and all(m.estimated for m in loaded),
      "the defaults are the agent's five free models, marked as estimates")

# -- The relay's /mix route ---------------------------------------------------------------------------------

EMAIL = "alice.smith@example.com"


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(json.dumps(body))
        messages = body["messages"]
        system = messages[0]["content"] if messages[0]["role"] == "system" else ""
        text = messages[-1]["content"]
        reply = "APPROVE" if system.startswith("You review") else f"{type(self).__name__} says: {text}"
        data = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def stub(name: str):
    handler = type(name, (Stub,), {"seen": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(port: int, body: dict, token: str = "t" * 20):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mix/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


pool, pool_seen = stub("Pool")
cloud, cloud_seen = stub("Cloud")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    tmp = Path(tmp)
    (tmp / "vault").mkdir()
    (tmp / "sources.json").write_text(json.dumps({"sources": [
        {"name": "cloud", "url": f"http://127.0.0.1:{cloud.server_port}/v1", "kind": "external"}]}))
    (tmp / "mix.json").write_text(json.dumps({"members": [
        {"route": "pool", "model": "local-14b", "family": "qwen", "capability": 42, "private": False},  # the file's word on privacy is ignored
        {"route": "cloud", "model": "big:free", "family": "nvidia", "capability": 66, "private": True}]}))
    port = free_port()
    env = {**os.environ, "NANOBOREALIS_RELAY_TOKEN": "t" * 20, "NANOBOREALIS_RELAY_LISTEN": f"127.0.0.1:{port}",
           "NANOBOREALIS_RELAY_UPSTREAM": f"127.0.0.1:{pool.server_port}", "NANOBOREALIS_SOURCES_FILE": str(tmp / "sources.json"),
           "NANOBOREALIS_MIX_FILE": str(tmp / "mix.json"), "NANOBOREALIS_VAULT": str(tmp / "vault"), "NANOBOREALIS_TOOLS": str(TOOLS)}
    relay = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-relay")], env=env, stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        question = f"Draft a reply to {EMAIL} about the budget."
        status, answer = post(port, {"messages": [{"role": "user", "content": question}], "mix": {"kind": "code"}})
        audit = answer.get("x_mix", {}).get("audit", [])
        check(status == 200 and answer["x_mix"]["recipe"] == "reviewed" and [a["role"] for a in audit] == ["worker", "reviewer"],
              "/mix runs a recipe across the pool and the cloud model")
        check(EMAIL in " ".join(pool_seen.seen), "the model on the owner's own computers is sent the request as it is")
        check(cloud_seen.seen and EMAIL not in " ".join(cloud_seen.seen) and "<<EMAIL_" in " ".join(cloud_seen.seen),
              "the cloud model is sent a tokenized request, though the reviewer was handed the pool's raw answer")
        check(EMAIL in answer["choices"][0]["message"]["content"], "the caller gets the answer with the real details in it")
        check(audit[0]["private"] and not audit[1]["private"], "which members are private is worked out from where they are, not from the file")

        before = len(cloud_seen.seen)
        status, answer = post(port, {"messages": [{"role": "user", "content": question}], "mix": {"kind": "code", "sensitivity": "local-only"}})
        check(status == 200 and len(cloud_seen.seen) == before and answer["x_mix"]["checked"] is False,
              "a local-only request never reaches the cloud, and says its answer wasn't reviewed")
        status, _ = post(port, {"messages": [{"role": "user", "content": "x"}]}, token="x" * 20)
        check(status == 401, "the relay token is still required")
        status, _ = post(port, {"nothing": 1})
        check(status == 400, "a request without messages is refused")
    finally:
        relay.terminate()
        relay.wait(10)
        pool.shutdown()
        cloud.shutdown()

print("mixer checks passed")
