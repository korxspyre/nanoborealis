# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Hands: lend this device to the agents, so they can work on it the way a person at it would, as
you, with your files, tools and network, under a permission check that keeps them to the request
they're working on (permissions.py).

The app keeps asking the NanoBorealis computer it's paired with for work (POST /nanoborealis/hands/poll;
a phone or a PC behind a router can't be called, but it can call), checks each job, asks you when the
check says so, runs what's allowed in this device's own shell and answers with the result. Every
job and decision goes into hands.log.

Each job carries its task: the request it serves and the folders it's about. Inside those folders an
agent can read freely and, in the "scoped" mode, change things; anywhere else it has to ask, so an
agent drifting onto another subject shows up as a question, not as a change. A few things are never
allowed at all (see permissions.NEVER).

    python hands.py serve [--machine NAME] [--mode plan|ask|scoped|trust]   lend this device until stopped
    python hands.py check --task-folder DIR --cwd DIR CMD...                  what the check says, without running
    python hands.py policy [--mode M] [--root DIR ...] [--allow PREFIX ...]   show or change your rules
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairing  # noqa: E402
from permissions import Decision, Log, Policy, Task, decide  # noqa: E402

OUTPUT_LIMIT = 400_000  # the end of a job's output that's sent back


def home() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "NanoBorealis" / "hands"
    base.mkdir(parents=True, exist_ok=True)
    return base


def policy_path() -> Path:
    return home() / "policy.json"


def run_native(command: str, cwd: str, timeout: int) -> dict:
    """The command in this device's own shell, as you: PowerShell on Windows, bash elsewhere."""
    if os.name == "nt":
        argv = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        argv = ["bash", "-lc", command]
    try:
        done = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"exit": done.returncode, "timed_out": False,
                "output": done.stdout.decode(errors="replace")[-OUTPUT_LIMIT:]}
    except subprocess.TimeoutExpired as e:
        return {"exit": None, "timed_out": True, "output": (e.stdout or b"").decode(errors="replace")[-OUTPUT_LIMIT:]}
    except OSError as e:
        return {"exit": None, "timed_out": False, "output": f"couldn't run it here: {e}"}


Approver = Callable[[str, str, Task | None, Decision], str]  # -> "once" | "always" | "deny"


def ask_in_console(command: str, cwd: str, task: Task | None, decision: Decision) -> str:
    """The approval prompt when lending from a terminal. The app shows its own card."""
    if not sys.stdin or not sys.stdin.isatty():
        return "deny"
    print("\n" + "-" * 70)
    print(f"An agent asks to run this here ({decision.reason}):")
    print(f"  request: {task.request if task else '(none given)'}")
    print(f"  folder:  {cwd}")
    print(f"  command: {command}")
    answer = input("Allow? [y]es once / [a]lways / [N]o: ").strip().lower()
    return {"y": "once", "yes": "once", "a": "always", "always": "always"}.get(answer, "deny")


def handle(job: dict, policy: Policy, approve: Approver, log: Log) -> dict:
    """Check one job, ask if needed, run it if allowed. Returns its result for the computer that sent it."""
    command = str(job.get("command") or "")
    task = Task.from_json(job.get("task"))
    cwd = str(job.get("cwd") or (task.folders[0] if task and task.folders else Path.home()))
    decision = decide(command, cwd, task, policy)
    answer = "auto"
    if decision.verdict == "ask":
        answer = approve(command, cwd, task, decision)
        if answer == "always" and command.split():
            policy.allow.append(command.split()[0])  # the program, from now on, inside scope
            policy.save(policy_path())
        allowed = answer in ("once", "always")
    else:
        allowed = decision.verdict == "allow"
    log.write(job=job.get("id"), request=task.request if task else None, cwd=cwd, command=command,
              verdict=decision.verdict, reason=decision.reason, answer=answer, ran=allowed)
    if not allowed:
        why = decision.reason if decision.verdict == "deny" else f"the owner said no ({decision.reason})"
        return {"exit": None, "timed_out": False, "refused": True, "output": f"Not run on this device: {why}."}
    if not Path(cwd).is_dir():
        return {"exit": None, "timed_out": False, "output": f"No such folder here: {cwd}"}
    return run_native(command, cwd, int(job.get("timeout") or 300))


def describe(policy: Policy) -> dict:
    return {"kind": "native", "os": sys.platform, "mode": policy.mode, "folders": policy.roots,
            "shell": "powershell" if os.name == "nt" else "bash", "network": True}


def serve(machine: pairing.Machine, approve: Approver = ask_in_console, stop=lambda: False, progress=print,
          mode: str | None = None) -> None:
    """Ask the paired computer for work and do what the permission check allows, until stop() says so."""
    policy = Policy.load(policy_path())
    if mode:
        policy.mode = mode
        policy.save(policy_path())
    log = Log(home() / "hands.log")
    progress(f"lending this device's hands to {machine.name}: mode {policy.mode}, "
             f"shared folders {', '.join(policy.roots) or '(any the task names, asking first)'}")
    headers = {"X-NanoBorealis-Device": machine.password}
    while not stop():
        try:
            status, answer, _ = pairing.call(machine.host, machine.port, "POST", "/nanoborealis/hands/poll",
                                             context=machine.context(), body={"box": describe(policy)},
                                             headers=headers, timeout=60)
        except pairing.PairingError as e:
            progress(f"can't reach {machine.name}: {e}; trying again in 15 seconds")
            time.sleep(15)
            continue
        if status != 200:
            progress(f"{machine.name} said HTTP {status}: {answer.get('error', answer)}")
            time.sleep(30)
            continue
        job = answer.get("job")
        if not job:
            continue
        progress(f"job {job.get('id')}: {str(job.get('command'))[:120]}")
        result = handle(job, policy, approve, log)
        progress(f"job {job.get('id')}: {'refused' if result.get('refused') else 'exit ' + str(result['exit'])}")
        try:
            pairing.call(machine.host, machine.port, "POST", "/nanoborealis/hands/done", context=machine.context(),
                         body={"id": job.get("id"), **result}, headers=headers, timeout=60)
        except pairing.PairingError as e:
            progress(f"couldn't send job {job.get('id')}'s result: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--machine")
    s.add_argument("--mode", choices=("plan", "ask", "scoped", "trust"))
    c = sub.add_parser("check")
    c.add_argument("--task-folder", action="append", default=[])
    c.add_argument("--cwd", default=".")
    c.add_argument("words", nargs="+")
    p = sub.add_parser("policy")
    p.add_argument("--mode", choices=("plan", "ask", "scoped", "trust"))
    p.add_argument("--root", action="append")
    p.add_argument("--allow", action="append")
    args = parser.parse_args()
    policy = Policy.load(policy_path())
    if args.action == "check":
        d = decide(" ".join(args.words), os.path.abspath(args.cwd), Task("(checking)", args.task_folder), policy)
        print(f"{d.verdict}: {d.reason}")
        return 0
    if args.action == "policy":
        if args.mode:
            policy.mode = args.mode
        if args.root:
            policy.roots = [os.path.abspath(os.path.expanduser(r)) for r in args.root]
        if args.allow:
            policy.allow += args.allow
        if args.mode or args.root or args.allow:
            policy.save(policy_path())
        print(f"{policy_path()}\n{policy}")
        return 0
    import devlink
    serve(devlink.pick(args.machine), mode=args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
