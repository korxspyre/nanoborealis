# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""What an agent may do on this device without asking: the permission check every action goes
through, the way Claude Code does it, but held to the scope of the request it came from.

Every job an agent sends arrives with a task: the request it is working on and the folders that
request is about. An action is then:

    deny   never (wrecking the system, reaching for credentials), whatever the mode or rules say
    ask    shown to the owner with the request it serves; they allow it once, always, or deny it
    allow  done without asking

decided in this order:
    1. the built-in never-list, then the owner's own deny rules
    2. out of scope: a working folder or a path outside the task's folders  -> ask (the agent drifted)
    3. the owner's allow rules (always-allowed commands)                     -> allow
    4. the mode: plan (read-only: anything that changes something is denied), ask (everything that
       changes something asks), scoped (changes inside the task's folders are allowed), trust (all
       allowed, still within the never-list)
    5. reading inside scope                                                   -> allow
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath

MODES = ("plan", "ask", "scoped", "trust")

# Never, in any mode: things that wreck the device or take what isn't the agent's to take.
NEVER = [
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)[a-z]*\s+(/|~|\$HOME|/\*)(\s|$)", "deletes the whole system or home"),
    (r"\bmkfs(\.\w+)?\b|\bwipefs\b|\bdd\b.*\bof=/dev/", "overwrites a disk"),
    (r"\b(format|diskpart|bcdedit|cipher\s+/w)\b", "formats or rewrites disks or boot"),
    (r"\b(shutdown|reboot|poweroff|halt|Stop-Computer|Restart-Computer)\b", "turns the device off"),
    (r"Remove-Item\b.*-Recurse.*\s([A-Za-z]:\\?|~|\$HOME|\$env:USERPROFILE)\s*$", "deletes a whole drive or home"),
    (r"\breg(\.exe)?\s+delete\b|Remove-ItemProperty\b.*HK(LM|CU)", "deletes registry keys"),
    (r"\b(chmod|chown)\s+-R\b.*\s/(\s|$)", "changes the whole system's permissions"),
    (r":\(\)\s*\{.*\};\s*:", "a fork bomb"),
    (r"\b(sudo|su|runas|doas)\b|Start-Process\b.*-Verb\s+RunAs", "asks for administrator rights"),
    (r"(\.ssh|\.gnupg|\.aws|\.config/gh|Login Data|Cookies|\.mozilla|AppData\\Roaming\\Mozilla|keychain|"
     r"\.password-store|KeePass|1Password|Bitwarden|wallet\.dat)", "reaches for passwords, keys or browser data"),
    (r"\b(crontab|schtasks|Register-ScheduledTask|systemctl\s+--user\s+enable)\b", "sets up something that runs by itself later"),
    (r"(curl|wget|iwr|Invoke-WebRequest)\b[^|]*\|\s*(ba)?sh\b|iex\s*\(", "runs code straight from the internet"),
]

# Always asked about, in every mode and even inside the request's folders: what can't be taken back. Changes to
# files can be undone (snapshots on NanoBorealis, git, the bin); these reach other people or destroy for good.
IRREVERSIBLE = [
    (r"\bgit\s+push\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f", "publishes or throws away git work"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgh\s+(release|pr|issue)\s+create\b",
     "publishes"),
    (r"\b(rm|del|erase|rmdir|rd)\b.*\s-[a-z]*r|\bRemove-Item\b.*-Recurse|\brm\s+-[a-z]*f", "deletes files for good"),
    (r"\b(sendmail|mail|mutt|Send-MailMessage)\b|\bcurl\b.*\s-(X\s*POST|d|F|-data)\b", "sends something to someone"),
    (r"\b(pip|npm|winget|choco|scoop|brew|apt(-get)?|dnf)\s+(uninstall|remove|purge)\b", "uninstalls software"),
]

# Commands that only look at things. Anything else counts as changing something.
READ_ONLY = re.compile(
    r"^\s*(ls|dir|cat|type|head|tail|less|more|wc|stat|file|find|tree|pwd|echo|grep|rg|which|where|whoami|id|uname|"
    r"hostname|date|df|du|free|ps|top|env|printenv|Get-ChildItem|Get-Content|Get-Item|Get-Location|Select-String|"
    r"Test-Path|Get-Process|Get-Command|git\s+(status|log|diff|show|branch|remote\s+-v|ls-files|blame)|"
    r"python3?\s+--version|node\s+--version)\b(?![^|;&]*(>|\btee\b))", re.I)


@dataclass
class Task:
    """The request a job serves, and the folders it's about."""
    request: str
    folders: list[str]
    id: str = ""

    @staticmethod
    def from_json(data: object) -> "Task | None":
        if not isinstance(data, dict) or not isinstance(data.get("request"), str) or not data["request"].strip():
            return None
        folders = [str(f) for f in data.get("folders") or [] if isinstance(f, str) and f.strip()]
        return Task(data["request"].strip()[:2000], folders, str(data.get("id") or ""))


@dataclass
class Policy:
    mode: str = "ask"
    allow: list[str] = field(default_factory=list)  # command prefixes always allowed (in scope)
    deny: list[str] = field(default_factory=list)  # regexes never allowed
    roots: list[str] = field(default_factory=list)  # the only folders any task may name

    @staticmethod
    def load(path: Path) -> "Policy":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            policy = Policy(**{k: v for k, v in data.items() if k in ("mode", "allow", "deny", "roots")})
            if policy.mode not in MODES:
                policy.mode = "ask"
            return policy
        except (OSError, ValueError, TypeError):
            return Policy()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


@dataclass
class Decision:
    verdict: str  # allow | ask | deny
    reason: str


def _norm(path: str) -> str:
    text = os.path.expandvars(os.path.expanduser(path.strip().strip("'\"")))
    if os.name == "nt" or re.match(r"^[A-Za-z]:[\\/]", text):
        return str(PureWindowsPath(text)).lower().rstrip("\\")
    return os.path.normpath(text).rstrip("/") or "/"


def inside(path: str, folders: list[str]) -> bool:
    p = _norm(path)
    sep = "\\" if "\\" in p or re.match(r"^[a-z]:", p) else "/"
    return any(p == _norm(f) or p.startswith(_norm(f) + sep) for f in folders)


PATHLIKE = re.compile(r"""(?:(?<=\s)|^|(?<==)|(?<=["']))((?:[A-Za-z]:[\\/]|~[\\/]|/|\$HOME/|\$env:USERPROFILE\\)[^\s"';|&<>]*)""")


def paths_in(command: str) -> list[str]:
    """Absolute paths a command names (a heuristic: the agent is also told to stay in scope)."""
    found = [m.group(1) for m in PATHLIKE.finditer(command)]
    return [p for p in found if not re.match(r"^/(dev/null|tmp\b|proc/self)", p) and len(p) > 1
            and not re.match(r"^/[a-z]$", p, re.I)]  # /c-style options aren't paths


def decide(command: str, cwd: str, task: Task | None, policy: Policy) -> Decision:
    for pattern, why in NEVER:
        if re.search(pattern, command, re.I):
            return Decision("deny", f"never allowed: it {why}")
    for pattern in policy.deny:
        try:
            if re.search(pattern, command, re.I):
                return Decision("deny", f"you don't allow this ({pattern})")
        except re.error:
            continue
    if task is None:
        return Decision("deny", "the job came without a request it serves, so its scope is unknown")
    scope = [f for f in task.folders if not policy.roots or inside(f, policy.roots)]
    if task.folders and not scope:
        return Decision("deny", "the task names folders outside the ones you share")
    if not scope:
        return Decision("ask", "the task names no folders to work in")
    if not inside(cwd, scope):
        return Decision("ask", f"out of scope: it would work in {cwd}, not in the task's folders")
    stray = [p for p in paths_in(command) if not inside(p, scope)]
    if stray:
        return Decision("ask", f"out of scope: it touches {', '.join(stray[:3])}")
    reads = bool(READ_ONLY.match(command)) and not re.search(r"[;&|]\s*\S", command.replace("||", ""))
    if any(command.strip().startswith(prefix) for prefix in policy.allow if prefix.strip()):
        return Decision("allow", "you always allow this")
    if policy.mode == "plan":
        return Decision("allow", "reading, in plan mode") if reads else Decision("deny", "plan mode: nothing may change")
    if reads:
        return Decision("allow", "only reads, inside the task's folders")
    for pattern, why in IRREVERSIBLE:  # in every mode: what can't be taken back waits for you
        if re.search(pattern, command, re.I):
            return Decision("ask", f"it can't be undone: it {why}")
    if policy.mode == "trust":
        return Decision("allow", "trust mode")
    if policy.mode == "scoped":
        return Decision("allow", "changes stay inside the task's folders")
    return Decision("ask", "it changes something")


class Log:
    """What every job asked for, what was decided and why: hands.log next to the policy."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, **entry) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}) + "\n")
        except OSError:
            pass
