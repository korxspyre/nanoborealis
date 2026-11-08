# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A reversible tokenization proxy's engine: swaps personal details for stable placeholders on
the way out to a cloud model, and swaps them back in what comes home.

    <<EMAIL_3fa9c21b07>>   an email address        <<SECRET_...>>   a key, token or password
    <<PHONE_...>>          a phone number           <<CARD_...>>     a payment card (Luhn-checked)
    <<IP_...>>             an IPv4 address          <<IBAN_...>>     a bank account (mod-97 checked)
    <<ADDRESS_...>>        a street address         <<USER_...>>     the user name in a home path
    <<TERM_...>>           anything on the owner's own list (names, employers, projects)

The mapping (placeholder -> real value) lives in a SQLite file that never leaves this computer.
A placeholder is an HMAC of the value under a per-vault secret, so the same value always gets
the same placeholder: a model can still tell that two mentions are one person, and a later
request in the same conversation lines up with an earlier one. Placeholders are plain ASCII so
every tokenizer, JSON string and shell command passes them through untouched.

What this can and can't do. Patterns find the things that have a shape (emails, keys, cards,
numbers, paths). A name has no shape: it is only protected when it is on the owner's list
(protect.json: {"terms": {"Alice Smith": "PERSON"}}), until a local model finds names too. The
proxy also can't protect what it can't read: images, audio and files are refused, not sent.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

OPEN, CLOSE = "<<", ">>"
TOKEN = re.compile(r"<<([A-Za-z][A-Za-z_]{1,15})_([0-9a-fA-F]{10,16})>>")
PARTIAL = re.compile(r"<(?:<[A-Za-z0-9_]{0,40}>?)?")  # a placeholder that has begun but not finished
MAX_TOKEN = 40
MEDIA_PARTS = {"image_url", "input_image", "input_audio", "audio", "file", "video_url"}


class Unprotectable(Exception):
    """The request holds something that can't be tokenized (an image, a file), so it isn't sent."""


# -- What counts as sensitive -----------------------------------------------------------------------


def _luhn(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _iban(text: str) -> bool:
    flat = text.replace(" ", "").upper()
    if not 15 <= len(flat) <= 34 or not flat[:2].isalpha() or not flat[2:4].isdigit():
        return False
    return int("".join(str(int(c, 36)) for c in flat[4:] + flat[:4])) % 97 == 1


def _phone(text: str) -> bool:
    digits = sum(c.isdigit() for c in text)
    return 10 <= digits <= 15 and (text.startswith("+") or any(c in text for c in " -.()"))


def _ipv4(text: str) -> bool:
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return not (address.is_loopback or address.is_unspecified)


def _not_token(text: str) -> bool:
    return not text.startswith(OPEN)


_SYSTEM_USERS = {"root", "nanobot", "shared", "public", "default", "user", "all", "runner", "ubuntu", "admin"}

# (kind, pattern, group holding the value, check). Earlier entries win a tie on the same text.
DETECTORS: list[tuple[str, re.Pattern, int, object]] = [
    ("SECRET", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"), 0, None),
    ("SECRET", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
                          r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abprs]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{30,}"
                          r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), 0, None),
    ("SECRET", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|authorization)\b[\"']?\s*[:=]\s*"
                          r"[\"']?(?:Bearer\s+)?([^\s\"',;]{8,})"), 1, _not_token),
    ("SECRET", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:([^\s/@]+)@[^\s]+"), 1, _not_token),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), 0, None),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b"), 0, _iban),
    ("CARD", re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d])"), 0, _luhn),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0, None),
    ("IP", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.]*\d)"), 0, _ipv4),
    ("PHONE", re.compile(r"(?<![\w.])(?<!\d[\s.-])\+?\d{1,3}?[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\w|[\s.-]\d)"), 0, _phone),
    ("ADDRESS", re.compile(r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln"
                           r"|Drive|Dr|Court|Ct|Way|Place|Pl)\b\.?"), 0, None),
    ("USER", re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\Users\\)([A-Za-z0-9._-]+)"), 1,
     lambda v: v.lower() not in _SYSTEM_USERS and _not_token(v)),
]


class Vault:
    """Placeholders and what they stand for, kept on this computer. `folder=None` keeps them in memory."""

    def __init__(self, folder: Path | str | None = None):
        self.lock = threading.RLock()
        self.terms: dict[str, str] = {}
        self._terms_re: re.Pattern | None = None
        if folder is None:
            self.db = sqlite3.connect(":memory:", check_same_thread=False)
            self.secret = secrets.token_bytes(32)
        else:
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(folder, 0o700)
            except OSError:
                pass
            keyfile = folder / "vault.key"
            if not keyfile.exists():
                try:
                    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(secrets.token_bytes(32))
                except FileExistsError:  # another process made it a moment ago: use its key
                    for _ in range(50):
                        if keyfile.stat().st_size >= 32:
                            break
                        time.sleep(0.02)
            self.secret = keyfile.read_bytes()
            self.db = sqlite3.connect(folder / "vault.sqlite", check_same_thread=False)
            try:
                os.chmod(folder / "vault.sqlite", 0o600)
            except OSError:
                pass
            self.load_terms(folder / "protect.json")
        self.db.execute("CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                        "value TEXT NOT NULL, at REAL DEFAULT (strftime('%s','now')))")
        self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # -- The owner's own list ---------------------------------------------------------------------

    def load_terms(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        terms = data.get("terms") if isinstance(data, dict) else None
        if isinstance(terms, list):
            terms = {str(t): "TERM" for t in terms}
        if isinstance(terms, dict):
            self.add_terms(terms)

    def add_terms(self, terms: dict[str, str]) -> None:
        for text, kind in terms.items():
            text = str(text).strip()
            if len(text) >= 2:
                self.terms[text] = re.sub(r"[^A-Z]", "", str(kind).upper())[:12] or "TERM"
        ordered = sorted(self.terms, key=len, reverse=True)
        self._terms_re = (re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in ordered) + r")(?!\w)", re.I)
                          if ordered else None)

    # -- Finding ----------------------------------------------------------------------------------

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        """Non-overlapping (start, end, kind) of everything sensitive in `text`, skipping existing placeholders."""
        taken = [m.span() for m in TOKEN.finditer(text)]
        candidates: list[tuple[int, int, int, str]] = []
        for priority, (kind, pattern, group, check) in enumerate(DETECTORS):
            for m in pattern.finditer(text):
                value = m.group(group)
                if value and (check is None or check(value)):
                    candidates.append((m.start(group), m.end(group), priority, kind))
        if self._terms_re:
            for m in self._terms_re.finditer(text):
                candidates.append((m.start(), m.end(), -1, self.terms.get(self._canon(m.group(0)), "TERM")))
        candidates.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
        chosen: list[tuple[int, int, str]] = []
        end = 0
        for start, stop, _, kind in candidates:
            if start < end or any(start < b and stop > a for a, b in taken):
                continue
            chosen.append((start, stop, kind))
            end = stop
        return chosen

    def _canon(self, matched: str) -> str:
        for term in self.terms:
            if term.lower() == matched.lower():
                return term
        return matched

    # -- Swapping ---------------------------------------------------------------------------------

    def placeholder(self, kind: str, value: str, scope: str = "") -> str:
        """The placeholder for `value`. `scope` names who it is for (a source, a task): the same value gets a
        different placeholder in each scope, so two providers can't match what they were sent."""
        mac = hmac.new(self.secret, f"{kind}\0{scope}\0{value}".encode(), hashlib.sha256).hexdigest()
        with self.lock:
            for length in (10, 12, 16):
                token = f"{OPEN}{kind}_{mac[:length]}{CLOSE}"
                row = self.db.execute("SELECT value FROM tokens WHERE token = ?", (token.lower(),)).fetchone()
                if row is None:
                    self.db.execute("INSERT INTO tokens (token, kind, value) VALUES (?, ?, ?)",
                                    (token.lower(), kind, value))
                    self.db.commit()
                    return token
                if row[0] == value:
                    return token
        raise RuntimeError("placeholder collision")  # 64 bits of HMAC: not reachable in practice

    def tokenize(self, text: str, scope: str = "") -> str:
        if not text:
            return text
        out, last = [], 0
        for start, stop, kind in self.spans(text):
            out.append(text[last:start])
            out.append(self.placeholder(kind, text[start:stop], scope))
            last = stop
        out.append(text[last:])
        return "".join(out)

    def detokenize(self, text: str) -> str:
        if not text or OPEN not in text:
            return text

        def real(m: re.Match) -> str:
            with self.lock:
                row = self.db.execute("SELECT value FROM tokens WHERE token = ?", (m.group(0).lower(),)).fetchone()
            return row[0] if row else m.group(0)
        return TOKEN.sub(real, text)

    # -- A whole request and its answer -----------------------------------------------------------

    def protect_request(self, body: dict, scope: str = "") -> dict:
        """A copy of an OpenAI-style request with its text tokenized for `scope`. Raises Unprotectable for
        images, audio and files: they can't be tokenized, so they don't go to a cloud model."""
        body = copy.deepcopy(body)

        def text_of(value):
            if isinstance(value, str):
                return self.tokenize(value, scope)
            if isinstance(value, list):
                return [text_of(v) for v in value]
            if isinstance(value, dict):
                kind = str(value.get("type") or "")
                if kind in MEDIA_PARTS or any(k in value for k in ("image_url", "input_audio", "file_data")):
                    raise Unprotectable(f"a {kind or 'media'} part can't be tokenized, so it isn't sent to a cloud model")
                return {k: (self.tokenize(v, scope) if k == "text" and isinstance(v, str) else text_of(v)
                            if isinstance(v, (list, dict)) else v) for k, v in value.items()}
            return value

        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            if "content" in message:
                message["content"] = text_of(message["content"])
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    function["arguments"] = self.tokenize(function["arguments"], scope)
            for key in ("reasoning", "reasoning_content"):
                if isinstance(message.get(key), str):
                    message[key] = self.tokenize(message[key], scope)
        for key in ("prompt", "system", "instructions", "input"):
            if key in body:
                body[key] = text_of(body[key])
        for tool in body.get("tools") or []:  # the descriptions are text too; names and schemas stay as they are
            self._describe(tool, scope)
        return body

    def _describe(self, node, scope: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    node[key] = self.tokenize(value, scope)
                else:
                    self._describe(value, scope)
        elif isinstance(node, list):
            for item in node:
                self._describe(item, scope)

    def restore(self, node):
        """Detokenize every string in a parsed JSON answer."""
        if isinstance(node, str):
            return self.detokenize(node)
        if isinstance(node, list):
            return [self.restore(v) for v in node]
        if isinstance(node, dict):
            return {k: self.restore(v) for k, v in node.items()}
        return node


# -- Streaming ---------------------------------------------------------------------------------------


class StreamRestorer:
    """Detokenizes text that arrives in pieces: a placeholder split across two pieces is held back until
    it is whole (or can't be one)."""

    def __init__(self, vault: Vault):
        self.vault = vault
        self.held = ""

    def feed(self, piece: str) -> str:
        text = self.held + piece
        cut = len(text)
        for i in range(max(0, len(text) - MAX_TOKEN), len(text)):
            if text[i] == "<" and PARTIAL.fullmatch(text, i):
                cut = i
                break
        self.held = text[cut:]
        return self.vault.detokenize(text[:cut])

    def flush(self) -> str:
        text, self.held = self.held, ""
        return self.vault.detokenize(text)


class SSERestorer:
    """Restores an OpenAI-style server-sent-event stream, line by line. Every string in a chunk has its
    own restorer, keyed by where it sits in the JSON, so a placeholder split across chunks comes back whole."""

    def __init__(self, vault: Vault):
        self.vault = vault
        self.parts: dict[str, StreamRestorer] = {}

    def _walk(self, node, path: str, last: bool):
        if isinstance(node, str):
            part = self.parts.setdefault(path, StreamRestorer(self.vault))
            return part.feed(node) + (part.flush() if last else "")
        if isinstance(node, list):
            return [self._walk(v, f"{path}[{i}]", last) for i, v in enumerate(node)]
        if isinstance(node, dict):
            return {k: self._walk(v, f"{path}.{k}", last) if k not in ("id", "model", "object", "role", "type", "finish_reason")
                    else v for k, v in node.items()}
        return node

    def line(self, raw: bytes) -> list[bytes]:
        """The lines to send on for one line received."""
        text = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not text.startswith("data:"):
            return [raw]
        payload = text[5:].strip()
        if payload == "[DONE]":
            out = []
            leftovers = {p: r.flush() for p, r in self.parts.items() if r.held}
            if leftovers:
                out.append(self._synthetic(leftovers))
            out.append(raw)
            return out
        try:
            chunk = json.loads(payload)
        except ValueError:
            return [raw]
        last = any((c or {}).get("finish_reason") for c in (chunk.get("choices") or []) if isinstance(c, dict))
        chunk = self._walk(chunk, "", last)
        return [f"data: {json.dumps(chunk, ensure_ascii=False)}\n".encode()]

    def _synthetic(self, leftovers: dict[str, str]) -> bytes:
        """What was still held back when the stream ended, as one more chunk of content."""
        text = "".join(leftovers.values())
        return ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": text}}]}) + "\n").encode()


def default_vault() -> Vault:
    """The vault the relay and Hall use: NANOBOREALIS_VAULT, else /var/lib/nanoborealis/vault."""
    return Vault(os.environ.get("NANOBOREALIS_VAULT") or "/var/lib/nanoborealis/vault")
