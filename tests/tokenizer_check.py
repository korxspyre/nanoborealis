# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the reversible tokenization proxy: the tokenizer itself, the inference-sources list, and the
real pool relay (nanoborealis-relay) in front of stand-in sources: one out on the internet, which must
never see a personal detail, and one on the owner's own network, which is sent requests as they are.

    python3 tests/tokenizer_check.py
"""

from __future__ import annotations

import json
import os
import random
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

import sources  # noqa: E402
import tokenizer  # noqa: E402
from tokenizer import SSERestorer, StreamRestorer, Vault  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


EMAIL = "alice.smith@example.com"
KEY = "sk-or-v1-" + "a1b2c3d4e5" * 4
PHONE = "+1 415 555 0132"
CARD = "4111 1111 1111 1111"
IBAN = "GB82 WEST 1234 5698 7654 32"
TEXT = (f"Mail {EMAIL} about Project Atlas, key {KEY}, call {PHONE}. Ask Alice Smith. Card {CARD}, IBAN {IBAN}, "
        f"host 10.0.0.5, files in /home/alice/work, home of 12 Maple Street.")
SECRETS = [EMAIL, KEY, PHONE, CARD, IBAN, "10.0.0.5", "alice/", "Alice Smith", "Project Atlas", "12 Maple Street"]


def vault_with_terms(folder=None) -> Vault:
    vault = Vault(folder)
    vault.add_terms({"Alice Smith": "PERSON", "Project Atlas": "PROJECT"})
    return vault


# -- The tokenizer -------------------------------------------------------------------------------------

vault = vault_with_terms()
out = vault.tokenize(TEXT)
check(not any(s in out for s in SECRETS), "emails, keys, phones, cards, IBANs, IPs, home paths, addresses and listed names are gone")
check(vault.detokenize(out) == TEXT, "detokenizing restores the text exactly")
check(vault.tokenize(TEXT) == out, "the same text always gets the same placeholders")
check(Vault().tokenize(TEXT) != out, "another vault's placeholders differ, so they say nothing about the value")
check(vault.tokenize(EMAIL) == vault.tokenize(EMAIL) and vault.tokenize(f"a {EMAIL} b").count("<<EMAIL_") == 1,
      "a repeated value is one placeholder")
check(vault.tokenize(out) == out, "placeholders aren't tokenized again")
a, b = vault.tokenize(EMAIL, "provider-a"), vault.tokenize(EMAIL, "provider-b")
check(a != b and a != vault.tokenize(EMAIL) and vault.tokenize(EMAIL, "provider-a") == a
      and vault.detokenize(a) == EMAIL == vault.detokenize(b),
      "each provider gets its own placeholders for the same value, so providers can't match what they were sent")
calm = "Released 2026-11-05 as version 1.2.3 on port 8080, order 1234 5678 9012 3456, from /home/nanobot/x, at 127.0.0.1."
check(vault.tokenize(calm) == calm, "dates, versions, ports, non-Luhn numbers, system paths and loopback are left alone")
check(vault.detokenize("<<EMAIL_ffffffffff>> stays") == "<<EMAIL_ffffffffff>> stays", "an unknown placeholder is left as it is")
lowered = out.lower()
check(vault.detokenize(lowered).count("example.com") >= 1, "a model that lowercases a placeholder still gets it restored")

request = {"model": "m", "messages": [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": [{"type": "text", "text": f"mail {EMAIL}"}]},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {
        "name": "send", "arguments": json.dumps({"to": EMAIL, "token": KEY})}}]},
    {"role": "tool", "tool_call_id": "1", "content": f"sent to {EMAIL} from 10.0.0.5"}],
    "tools": [{"type": "function", "function": {"name": "send", "description": f"mails people like {EMAIL}",
                                                "parameters": {"type": "object"}}}]}
safe = vault.protect_request(request)
blob = json.dumps(safe)
check(EMAIL not in blob and KEY not in blob and "10.0.0.5" not in blob,
      "a request is tokenized in messages, content parts, tool-call arguments, tool results and tool descriptions")
check(safe["tools"][0]["function"]["name"] == "send" and safe["model"] == "m" and request["messages"][1]["content"][0]["text"].endswith(EMAIL),
      "names and settings are kept, and the caller's request isn't changed")
check(json.loads(safe["messages"][2]["tool_calls"][0]["function"]["arguments"])["to"].startswith("<<EMAIL_"),
      "tool-call arguments stay valid JSON")
try:
    vault.protect_request({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:..."}}]}]})
    check(False, "an image is refused")
except tokenizer.Unprotectable:
    check(True, "an image is refused, not sent")

# A placeholder split anywhere in a stream still comes back whole.
full = vault.tokenize(f"a {EMAIL} b {PHONE} c <not a token> d <<x")
ok = True
for i in range(len(full) + 1):
    restorer = StreamRestorer(vault)
    ok &= restorer.feed(full[:i]) + restorer.feed(full[i:]) + restorer.flush() == vault.detokenize(full)
rng = random.Random(7)
for _ in range(200):
    cuts = sorted(rng.sample(range(len(full)), 5))
    pieces = [full[a:b] for a, b in zip([0, *cuts], [*cuts, len(full)])]
    restorer = StreamRestorer(vault)
    ok &= "".join(restorer.feed(p) for p in pieces) + restorer.flush() == vault.detokenize(full)
check(ok, "a placeholder split at any point of a stream is restored whole")

sse = SSERestorer(vault)
word = vault.tokenize(f"mail {EMAIL} now")
lines = [f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': word[i:i + 4]}}]})}\n".encode()
         for i in range(0, len(word), 4)]
lines += [b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n', b"\n", b"data: [DONE]\n"]
got = ""
for raw in lines:
    for line in sse.line(raw):
        if line.startswith(b"data: {"):
            got += (json.loads(line[6:])["choices"][0]["delta"].get("content") or "")
check(got == f"mail {EMAIL} now", "an event stream is restored chunk by chunk")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    first = Vault(tmp)
    token = first.tokenize(EMAIL)
    first.close()
    second = Vault(tmp)
    check(second.detokenize(token) == EMAIL, "the vault survives a restart")
    second.close()
    check(oct((Path(tmp) / "vault.key").stat().st_mode & 0o777) in ("0o600", "0o666") or os.name == "nt",
          "the vault's key is private to its owner")

# -- The sources list ---------------------------------------------------------------------------------

check(sources.classify("http://192.168.1.20:8080/v1") == "local" and sources.classify("http://100.101.102.103:8080/v1") == "local"
      and sources.classify("http://strata.local:8080/v1") == "local" and sources.classify("http://box:8080/v1") == "local",
      "private, Tailscale-style and .local addresses are local")
check(sources.classify("https://openrouter.ai/api/v1") == "external" and sources.classify("https://8.8.8.8/v1") == "external"
      and sources.classify("https://abc.trycloudflare.com/v1") == "external", "everything else is the internet")
check(sources.resolve({"name": "a", "url": "https://x.example/v1", "kind": "local"}).tokenizes,
      "an internet address claiming to be local is tokenized anyway")
check(not sources.resolve({"name": "a", "url": "https://x.example/v1", "kind": "local", "trusted": True}).tokenizes,
      "unless the owner vouches for it")
check(sources.resolve({"name": "a", "url": "http://192.168.1.20/v1", "kind": "external"}).tokenizes,
      "a local address can be marked external")
check(not sources.resolve({"name": "a", "url": "https://x.example/v1", "allow_raw": True}).tokenizes,
      "only allow_raw turns tokenization off for an external source")
check(sources.merge({"a": {"x": 1}, "b": 2}, {"a": {"x": 0, "y": 3}, "c": 4}) == {"a": {"x": 1, "y": 3}, "c": 4, "b": 2},
      "a source's extra settings sit under the request's own")


# -- The relay, in front of stand-in sources -------------------------------------------------------------


class Source(BaseHTTPRequestHandler):
    """Echoes what it is asked, as a cloud model would, and keeps every request body it was sent."""
    seen: list = []
    auth: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(json.dumps(body))
        type(self).auth.append(self.headers.get("Authorization"))
        text = body["messages"][-1]["content"]
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(0, len(text), 4):
                self.wfile.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': text[i:i + 4]}}]})}\n\n".encode())
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return
        reply = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": text, "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "send", "arguments": json.dumps({"q": text})}}]}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


def serve(handler_name: str):
    handler = type(handler_name, (Source,), {"seen": [], "auth": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port: int, path: str, body: dict | None, token: str = "t" * 20, stream: bool = False):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode()
            return response.status, raw
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


cloud, cloud_seen = serve("Cloud")
lan, lan_seen = serve("Lan")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    vault_dir = Path(tmp) / "vault"
    vault_dir.mkdir()
    (vault_dir / "protect.json").write_text(json.dumps({"terms": {"Alice Smith": "PERSON", "Project Atlas": "PROJECT"}}))
    listing = Path(tmp) / "sources.json"
    listing.write_text(json.dumps({"sources": [
        {"name": "cloud", "url": f"http://127.0.0.1:{cloud.server_port}/v1", "kind": "external", "key_env": "CLOUD_KEY",
         "free_only": True, "extra": {"provider": {"max_price": {"prompt": 0}}}},
        {"name": "lan", "url": f"http://127.0.0.1:{lan.server_port}/v1"}]}))
    port = free_port()
    env = {**os.environ, "NANOBOREALIS_RELAY_TOKEN": "t" * 20, "NANOBOREALIS_RELAY_LISTEN": f"127.0.0.1:{port}",
           "NANOBOREALIS_SOURCES_FILE": str(listing), "NANOBOREALIS_VAULT": str(vault_dir),
           "NANOBOREALIS_TOOLS": str(TOOLS), "CLOUD_KEY": "cloud-secret-key"}
    relay = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-relay")], env=env,
                             stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        text = f"Mail {EMAIL} about Project Atlas, key {KEY}, call {PHONE}. Ask Alice Smith."
        body = {"model": "vendor/model:free", "messages": [{"role": "user", "content": text}]}

        status, raw = call(port, "/src/cloud/v1/chat/completions", body)
        answer = json.loads(raw)
        sent = " ".join(cloud_seen.seen)
        check(status == 200 and answer["choices"][0]["message"]["content"] == text,
              "the agent gets the cloud model's answer with its own details back in it")
        check(json.loads(answer["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])["q"] == text,
              "tool-call arguments in the answer are restored too")
        check(not any(s in sent for s in (EMAIL, KEY, PHONE, "Alice Smith", "Project Atlas")) and "<<EMAIL_" in sent,
              "the cloud source never saw an email, key, phone number or name")
        check(cloud_seen.auth[-1] == "Bearer cloud-secret-key" and "t" * 20 not in " ".join(map(str, cloud_seen.auth)),
              "the source gets its own key, never the relay token")
        check('"max_price"' in sent, "the source's extra settings (free models only) are added to the request")

        status, raw = call(port, "/src/cloud/v1/chat/completions", {**body, "stream": True})
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: {")]
        streamed = "".join((c["choices"][0]["delta"].get("content") or "") for c in events)
        check(status == 200 and streamed == text and not any(s in " ".join(cloud_seen.seen) for s in (EMAIL, KEY)),
              "a streamed answer is restored whole, though its placeholders arrived in pieces")

        status, raw = call(port, "/src/lan/v1/chat/completions", {"model": "anything", "messages": [{"role": "user", "content": text}]})
        check(status == 200 and EMAIL in " ".join(lan_seen.seen), "a source on the owner's own network gets the request as it is")

        status, _ = call(port, "/src/cloud/v1/chat/completions", {**body, "model": "vendor/paid-model"})
        check(status == 403, "a free-only source refuses a model that isn't free")
        status, _ = call(port, "/src/cloud/v1/chat/completions", {"model": "m:free", "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]})
        check(status == 400 and not any("AAAA" in s for s in cloud_seen.seen), "an image never goes to the cloud source")
        check(call(port, "/src/nope/v1/chat/completions", body)[0] == 404, "an unknown source is a 404")
        check(call(port, "/src/cloud/v1/chat/completions", body, token="x" * 20)[0] == 401, "the relay token is still required")
        check(call(port, "/src/cloud/v1/embeddings", body)[0] == 403 and call(port, "/src/cloud/v2/models", None)[0] == 403,
              "only the chat API of a source is shared")
        check(call(port, "/v1/state", body)[0] == 403, "the pool's own paths are still limited to chat")
    finally:
        relay.terminate()
        relay.wait(10)
        cloud.shutdown()
        lan.shutdown()

print("tokenizer checks passed")
