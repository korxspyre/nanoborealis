# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The developer's link: reach a NanoBorealis computer through this app's own pairing, from a terminal.

This is how a developer (a person, or Claude in Claude Code on the same PC as the app) works on the
host OS hands-on once the app is paired with it: no SSH, no extra keys. It reads the pairing the app
saved and talks to the computer's nanoborealis-remote, which checks the device's password and pins
its certificate. Commands run as the person who approved the pairing; with --root, as root, which the
computer allows only for a device granted it at that computer:

    nanoborealis devices developer <id>      (on the NanoBorealis computer; `nanoborealis devices` lists ids)

Usage:
    python devlink.py machines                         the paired computers, and this device's id on each
    python devlink.py run [--root] [-t SECS] CMD...    run a shell command there, print its output, exit with its code
    python devlink.py run [--root] -f SCRIPT           run a local script file there (bash)
    python devlink.py push [--root] LOCAL REMOTE       copy a file there (up to ~12 MB)
    python devlink.py pull [--root] REMOTE LOCAL       copy a file back
    --machine NAME picks one when several are paired; NANOBOREALIS_PREFS points at the app's saved settings.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairing  # noqa: E402

PREF_KEYS = ("flutter.nanoborealis.machines", "nanoborealis.machines")


def prefs_file() -> Path:
    """Where the app keeps its settings (Flutter's shared_preferences)."""
    if os.environ.get("NANOBOREALIS_PREFS"):
        return Path(os.environ["NANOBOREALIS_PREFS"])
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / "NanoBorealis contributors" / "NanoBorealis" / "shared_preferences.json"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    for candidate in (base / "com.nanoborealis.app" / "shared_preferences.json",
                      base / "NanoBorealis" / "shared_preferences.json"):
        if candidate.exists():
            return candidate
    found = sorted(base.glob("*/shared_preferences.json"))
    return next((p for p in found if "machines" in p.read_text(errors="replace")), base / "shared_preferences.json")


def machines() -> list[pairing.Machine]:
    path = prefs_file()
    try:
        prefs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"Can't read the app's pairings at {path}: {e}. Pair with the computer in the NanoBorealis app first.")
    raw = next((prefs[k] for k in PREF_KEYS if k in prefs), "[]")
    found = [pairing.Machine.from_json(m) for m in json.loads(raw)]
    return [m for m in found if m]


def pick(name: str | None) -> pairing.Machine:
    found = machines()
    if not found:
        sys.exit("This app isn't paired with any computer yet.")
    if name:
        chosen = [m for m in found if name.lower() in (m.name.lower(), m.host.lower())]
        if not chosen:
            sys.exit(f"No paired computer called {name}: {', '.join(m.name for m in found)}")
        return chosen[0]
    if len(found) > 1:
        sys.exit(f"Several computers are paired; pick one with --machine: {', '.join(m.name for m in found)}")
    return found[0]


def command(machine: pairing.Machine, text: str, root: bool, timeout: int) -> dict:
    # The command travels base64-encoded, so no quoting survives or breaks on the way.
    wrapped = f"echo {base64.b64encode(text.encode()).decode()} | base64 -d | bash"
    status, answer, _ = pairing.call(machine.host, machine.port, "POST", "/nanoborealis/command",
                                     context=machine.context(),
                                     body={"command": wrapped, "timeout": timeout, **({"as": "root"} if root else {})},
                                     headers={"X-NanoBorealis-Device": machine.password}, timeout=timeout + 60)
    if status != 200:
        sys.exit(f"{machine.name} said HTTP {status}: {answer.get('error', answer)}")
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--machine")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("machines")
    run = sub.add_parser("run")
    run.add_argument("--root", action="store_true")
    run.add_argument("-t", "--timeout", type=int, default=300)
    run.add_argument("-f", "--file")
    run.add_argument("words", nargs="*")
    h = sub.add_parser("hands", help="give a job to another device lending its hands, through this computer")
    h.add_argument("device")
    h.add_argument("--request", required=True, help="the request this job serves")
    h.add_argument("--folder", action="append", default=[], help="a folder the request is about (on that device)")
    h.add_argument("--cwd", default="")
    h.add_argument("-t", "--timeout", type=int, default=300)
    h.add_argument("words", nargs="+")
    for name in ("push", "pull"):
        p = sub.add_parser(name)
        p.add_argument("--root", action="store_true")
        p.add_argument("source")
        p.add_argument("target")
    args = parser.parse_args()

    if args.action == "machines":
        for m in machines():
            print(f"{m.name}  {m.host}:{m.port}  this device's id there: {m.device_id}")
        return 0
    machine = pick(args.machine)
    if args.action == "hands":
        status, answer, _ = pairing.call(
            machine.host, machine.port, "POST", "/nanoborealis/hands/run", context=machine.context(),
            body={"device": args.device, "command": " ".join(args.words), "timeout": args.timeout, "cwd": args.cwd,
                  "task": {"request": args.request, "folders": args.folder}},
            headers={"X-NanoBorealis-Device": machine.password}, timeout=args.timeout + 120)
        if status != 200:
            sys.exit(f"HTTP {status}: {answer.get('error', answer)}")
        sys.stdout.write(answer.get("output", ""))
        return int(answer.get("exit") or 0) if not answer.get("refused") else 3
    if args.action == "run":
        text = Path(args.file).read_text(encoding="utf-8") if args.file else " ".join(args.words)
        if not text.strip():
            sys.exit("Nothing to run.")
        answer = command(machine, text, args.root, args.timeout)
        sys.stdout.buffer.write(answer.get("output", "").encode("utf-8", "replace"))
        sys.stdout.flush()
        if answer.get("timed_out"):
            print(f"\n[timed out after {args.timeout}s]", file=sys.stderr)
            return 124
        return int(answer.get("exit") or 0)
    if args.action == "push":
        data = base64.b64encode(Path(args.source).read_bytes()).decode()
        target = shlex.quote(args.target)
        answer = command(machine, f"mkdir -p \"$(dirname {target})\" && base64 -d > {target} <<'NB_EOF'\n{data}\nNB_EOF\n"
                         f"ls -l {target}", args.root, 300)
    else:
        answer = command(machine, f"base64 -w0 {shlex.quote(args.source)}", args.root, 300)
        if answer.get("exit") == 0:
            Path(args.target).write_bytes(base64.b64decode(answer.get("output", "")))
            print(f"{args.target}: {Path(args.target).stat().st_size} bytes")
            return 0
    sys.stdout.write(answer.get("output", ""))
    return int(answer.get("exit") or 0)


if __name__ == "__main__":
    sys.exit(main())
