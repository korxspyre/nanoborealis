# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Inference sources besides the pool: anything that speaks the OpenAI chat API. A llama.cpp server,
Strata, Avalon, Project Maya or FreeToken on a PC down the hall, a notebook on a free GPU tier, or
OpenRouter's free models.

    python3 sources.py list
    python3 sources.py add <name> <url> [--key-env VAR] [--model M]... [--free-only] [--extra '{json}']
    python3 sources.py remove <name>
    python3 sources.py probe <name>      ask the source which models it serves
    python3 sources.py key <VARIABLE>    store a key (read from stdin) for the relay, root only

A source is reached through the pool relay at /src/<name>/v1/..., so the agent has one address and
one token whatever sits behind it. Where the source is decides what happens on the way:

  local     on the owner's own network (private, loopback, link-local, Tailscale-style addresses):
            the request goes as it is.
  external  anywhere else on the internet, including every free cloud model: the request is
            tokenized first (tokenizer.py), the answer is detokenized, and an image, audio clip or
            file is refused.

The kind is worked out from the address. A source can be marked "external" whatever its address,
but "local" is honoured only for an address that really is private, unless the owner also sets
"trusted": true. "allow_raw": true turns tokenization off for an external source, and nothing else does.

The list is /etc/nanoborealis/sources.json (NANOBOREALIS_SOURCES_FILE), which holds no secrets: a
key is named by `key_env` and lives in /etc/nanoborealis/sources.env, which systemd hands to the relay.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SOURCES_FILE = Path(os.environ.get("NANOBOREALIS_SOURCES_FILE") or "/etc/nanoborealis/sources.json")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
PATHS = ("/chat/completions", "/completions", "/models")  # all the relay lets through to a source


@dataclass
class Source:
    name: str
    url: str  # includes /v1, like an OpenAI base URL
    kind: str  # local | external, as worked out by resolve()
    key_env: str = ""
    models: list[str] = field(default_factory=list)  # empty: any model the source serves
    free_only: bool = False  # only models whose id ends in :free
    extra: dict = field(default_factory=dict)  # merged into every request body (OpenRouter's max_price, say)
    allow_raw: bool = False
    key_file: str = ""  # a file holding the key, for a service that has one of its own (the Hall's OpenRouter key)

    @property
    def tokenizes(self) -> bool:
        return self.kind == "external" and not self.allow_raw

    def key(self) -> str:
        if self.key_env and os.environ.get(self.key_env):
            return os.environ[self.key_env]
        try:
            return Path(self.key_file).read_text(encoding="utf-8").strip() if self.key_file else ""
        except OSError:
            return ""


def classify(url: str) -> str:
    """local when the address is on the owner's own network, else external."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        return "external"
    try:
        return "external" if ipaddress.ip_address(host).is_global else "local"
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")) or "." not in host:
        return "local"
    return "external"


def resolve(entry: dict) -> Source:
    url = str(entry["url"]).rstrip("/")
    declared = str(entry.get("kind") or "").lower()
    real = classify(url)
    if real == "external" and declared == "local" and entry.get("trusted"):
        kind = "local"  # the owner vouches for an address that looks like the internet (a port-forwarded home server)
    elif declared == "external" or real == "external":
        kind = "external"  # an address that isn't private is tokenized, whatever the entry claims
    else:
        kind = "local"
    return Source(str(entry["name"]), url, kind, str(entry.get("key_env") or ""),
                  [str(m) for m in entry.get("models") or []], bool(entry.get("free_only")),
                  entry.get("extra") if isinstance(entry.get("extra"), dict) else {}, bool(entry.get("allow_raw")))


def load(path: Path | None = None) -> list[Source]:
    try:
        data = json.loads((path or SOURCES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    for entry in (data.get("sources") if isinstance(data, dict) else None) or []:
        try:
            if NAME.match(str(entry.get("name"))):
                found.append(resolve(entry))
        except (KeyError, AttributeError, TypeError):
            continue
    return found


def find(name: str, path: Path | None = None) -> Source | None:
    return next((s for s in load(path) if s.name == name), None)


def allowed(source: Source, model: str) -> str:
    """"" if `model` may be asked of this source, else why not."""
    if source.free_only and not model.endswith(":free"):
        return f"{source.name} is free-only: {model} isn't a :free model"
    if source.models and model not in source.models:
        return f"{source.name} doesn't serve {model}"
    return ""


def merge(body: dict, extra: dict) -> dict:
    """`extra` laid under `body`: the request's own settings win, except that nested dicts merge."""
    out = dict(extra)
    for key, value in body.items():
        out[key] = merge(value, out[key]) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


# -- One question to one source ----------------------------------------------------------------------------


class SourceError(Exception):
    def __init__(self, status: int, message: str = "", retry_after: float = 0):
        super().__init__(f"{status} {message}".strip())
        self.status, self.retry_after = status, retry_after


def chat_once(route: str, model: str, messages: list[dict], **kw) -> str:
    """The text of one non-streaming chat answer; see chat_message."""
    return str(chat_message(route, model, messages, **kw).get("content") or "")


def chat_message(route: str, model: str, messages: list[dict], *, tools: list | None = None, options: dict | None = None,
                 vault_factory=None, source: Source | None = None, pool_url: str = "http://127.0.0.1:52415",
                 timeout: float = 600, path: Path | None = None) -> dict:
    """The whole assistant message (content and any tool_calls) of one non-streaming chat answer from `route`
    ("pool" for exo, else a source). A source on the internet is asked in tokenized form, with placeholders of
    its own, and its answer is restored. `options` are extra request fields (temperature, max_tokens).
    Raises tokenizer.Unprotectable for an image, and SourceError when the source refuses or is down."""
    payload: dict = {"model": model, "messages": messages, **(options or {}), **({"tools": tools} if tools else {})}
    vault, key = None, ""
    if route == "pool":
        url = pool_url.rstrip("/") + "/v1/chat/completions"
    else:
        source = source or find(route, path)
        if source is None:
            raise SourceError(404, f"no inference source called {route}")
        why = allowed(source, model)
        if why:
            raise SourceError(403, why)
        payload = merge(payload, source.extra)
        url, key = source.url + "/chat/completions", source.key()
        if source.tokenizes:
            if vault_factory is None:
                raise SourceError(500, "an internet source can't be asked without the tokenizer")
            vault = vault_factory()
            payload = vault.protect_request(payload, scope=route)
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            wait = float(error.headers.get("Retry-After") or 0)
        except ValueError:
            wait = 0.0
        raise SourceError(error.code, "", wait) from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise SourceError(502, type(error).__name__) from None
    if vault:
        data = vault.restore(data)
    try:
        message = data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError
        return message
    except (KeyError, IndexError, TypeError):
        raise SourceError(502, "no answer in the reply") from None


# -- The command line --------------------------------------------------------------------------------


def _save(entries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"sources": entries}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _raw(path: Path) -> list[dict]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("sources") or [])
    except (OSError, ValueError, AttributeError):
        return []


def cmd_list(path: Path) -> None:
    sources = load(path)
    if not sources:
        print("No extra inference sources. Add one with: sources.py add <name> <url>")
    for s in sources:
        how = "tokenized" if s.tokenizes else ("local: sent as it is" if s.kind == "local" else "EXTERNAL, tokenization OFF")
        print(f"  {s.name:<18} {s.kind:<9} {s.url}  [{how}]" + ("  free-only" if s.free_only else ""))


def cmd_add(args: list[str], path: Path) -> None:
    if len(args) < 2 or not NAME.match(args[0]):
        sys.exit("usage: sources.py add <name> <url> [--key-env VAR] [--model M]... [--free-only] [--extra '{json}'] "
                 "[--allow-raw] [--trusted]   (a name is lowercase letters, digits and dashes)")
    entry: dict = {"name": args[0], "url": args[1]}
    it = iter(args[2:])
    for arg in it:
        if arg == "--key-env":
            entry["key_env"] = next(it, "")
        elif arg == "--model":
            entry.setdefault("models", []).append(next(it, ""))
        elif arg == "--free-only":
            entry["free_only"] = True
        elif arg == "--extra":
            entry["extra"] = json.loads(next(it, "{}"))
        elif arg == "--allow-raw":
            entry["allow_raw"] = True
        elif arg == "--trusted":
            entry["trusted"] = True
        else:
            sys.exit(f"unknown option {arg}")
    entries = [e for e in _raw(path) if e.get("name") != args[0]] + [entry]
    _save(entries, path)
    source = resolve(entry)
    print(f"{source.name}: {source.kind}" + (", tokenized" if source.tokenizes else ""))
    if source.allow_raw and source.kind == "external":
        print("warning: allow_raw is set, so this internet source sees your requests untokenized")


def cmd_probe(name: str, path: Path) -> None:
    source = find(name, path)
    if source is None:
        sys.exit(f"no source called {name}")
    request = urllib.request.Request(source.url + "/models", headers=(
        {"Authorization": f"Bearer {source.key()}"} if source.key() else {}))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except Exception as e:
        sys.exit(f"{name} didn't answer: {e}")
    ids = [m.get("id") for m in data.get("data") or [] if isinstance(m, dict)]
    print(f"{name} serves {len(ids)} model(s): " + ", ".join(str(i) for i in ids[:12]) + (" ..." if len(ids) > 12 else ""))


SOURCES_ENV = Path(os.environ.get("NANOBOREALIS_SOURCES_ENV") or "/etc/nanoborealis/sources.env")


def store_key(name: str, value: str, path: Path | None = None) -> None:
    """Keep `value` as NAME=value in the file systemd hands to the relay, readable by root only."""
    path = path or SOURCES_ENV
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", name) or not value.strip() or "\n" in value:
        raise ValueError("a key is stored under an UPPER_CASE name, and is one line")
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith(name + "=")]
    except OSError:
        lines = []
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join([*lines, f"{name}={value.strip()}"]) + "\n")
    tmp.replace(path)


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    action, rest = argv[0], argv[1:]
    if action == "key" and len(rest) == 1:  # the key comes on stdin, never on a command line
        try:
            store_key(rest[0], sys.stdin.read())
        except ValueError as error:
            sys.exit(str(error))
        print(f"stored {rest[0]}")
    elif action == "list":
        cmd_list(SOURCES_FILE)
    elif action == "add":
        cmd_add(rest, SOURCES_FILE)
    elif action == "remove" and len(rest) == 1:
        _save([e for e in _raw(SOURCES_FILE) if e.get("name") != rest[0]], SOURCES_FILE)
        print(f"removed {rest[0]}")
    elif action == "probe" and len(rest) == 1:
        cmd_probe(rest[0], SOURCES_FILE)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
