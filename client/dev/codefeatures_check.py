# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the workspace's coding-agent features that have no screen in them (src/codefeatures.py): checkpoints
against a real git and shell, plan mode, the status bar's words, the crew strip and the app's slash commands.

    python dev/codefeatures_check.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import codefeatures as cf  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def find_bash() -> str:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", shutil.which("bash") or ""):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True)
            if probe.stdout.strip() == "ok":
                return candidate
    raise SystemExit("no working bash here")


BASH = find_bash()


def posix(path: Path) -> str:
    """The path as a POSIX shell wants it (Git Bash on Windows reads C:\\x as /c/x)."""
    if os.name != "nt":
        return str(path)
    p = PureWindowsPath(path)
    return "/" + p.drive[0].lower() + "/" + "/".join(p.parts[1:])


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    root = Path(tmp)
    state = root / "state"
    project = root / "project"
    (project / "sub").mkdir(parents=True)
    (project / "a.txt").write_text("one\n")
    (project / "sub" / "b.txt").write_text("bee\n")
    (project / ".gitignore").write_text("*.log\n")
    (project / "keep.log").write_text("ignored\n")
    env = {**os.environ, "XDG_STATE_HOME": posix(state)}

    def run(command: str) -> tuple[int | None, str]:
        done = subprocess.run([BASH, "-c", command], capture_output=True, text=True, env=env, timeout=120)
        return done.returncode, done.stdout + done.stderr

    # The project has a git repository of its own, which checkpoints must leave alone.
    run(f"cd {posix(project)} && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm first")
    own_refs = run(f"cd {posix(project)} && git for-each-ref")[1]

    points = cf.Checkpoints(run, posix(project))
    first = points.take("chat1", "make the readme")
    check(first is not None and first["files"] == 3, "a checkpoint copies the folder (the .gitignore's files left out) and says how many files")
    (project / "a.txt").write_text("two\n")
    (project / "c.txt").write_text("new\n")
    (project / "sub" / "b.txt").unlink()
    second = points.take("chat1", "rewrite everything")
    check(second is not None and second["sha"] != first["sha"] and second["files"] == 3, "...and the next one is a different copy")
    listed = points.list("chat1")
    check([i["label"] for i in listed] == ["make the readme", "rewrite everything"] and listed[0]["sha"] == first["sha"]
          and all(i["at"] > 0 for i in listed) and points.list("another-chat") == [], "they are listed in order, per chat")

    result = points.restore(first["sha"])
    check(result["ok"] and (project / "a.txt").read_text() == "one\n" and (project / "sub" / "b.txt").read_text() == "bee\n"
          and not (project / "c.txt").exists(), "rewind puts the files back as they were and removes the ones made since")
    check((project / "keep.log").exists() and result["removed"] == 1, "...leaving what the project ignores alone")
    check(run(f"cd {posix(project)} && git for-each-ref")[1] == own_refs and not (project / "pwned").exists(),
          "...and writes nothing into the project's own repository")
    back = points.restore(result["undo"])
    check(back["ok"] and (project / "a.txt").read_text() == "two\n" and (project / "c.txt").exists() and not (project / "sub" / "b.txt").exists(),
          "a rewind can itself be undone, because the present is saved first")
    check(not points.restore("0" * 40)["ok"], "rewinding to a checkpoint that isn't there says so, and changes nothing")
    try:
        cf.restore_command(posix(project), "x; rm -rf ~")
        refused = False
    except ValueError:
        refused = True
    check(refused, "only a git object id is accepted as a checkpoint")

    hostile = cf.Checkpoints(run, posix(project))
    hostile.take("c'; touch pwned; '", "x'; touch pwned; echo '")
    check(not (project / "pwned").exists() and not (Path.cwd() / "pwned").exists(), "chat ids and labels can't inject commands")
    check(cf.Checkpoints(run, posix(root / "nowhere")).take("c", "x") is None, "a folder that isn't there gives no checkpoint")
    old = cf.TOO_BIG_KB
    cf.TOO_BIG_KB = 0
    big = cf.Checkpoints(run, posix(project))
    check(big.take("c", "x") is None and "too big" in big.why_not("TOO_BIG"), "a folder that is too big isn't copied before every message")
    cf.TOO_BIG_KB = old

# -- Plan mode, status bar, crew, commands ---------------------------------------------------------------------

planned = cf.plan_message("add a login page")
check(planned.startswith("[Plan mode]") and planned.endswith("add a login page") and "Do not edit" in planned and "numbered plan" in planned,
      "plan mode asks for a plan and says not to change anything")
check(cf.privacy_label("device-gpu", None) == ("local", "ok") and cf.privacy_label("openrouter", True) == ("cloud · tokenized", "ok")
      and cf.privacy_label("openrouter", False) == ("cloud · not tokenized", "bad") and cf.privacy_label("openrouter", None) == ("cloud · unknown", "warn"),
      "the privacy label never says tokenized unless privacy is known to be on")
routed = {"providers": [{"name": "openrouter", "api_base": "http://host.containers.internal:11436/src/openrouter/v1"}]}
direct = {"providers": [{"name": "openrouter", "api_base": None}]}
check(cf.privacy_from_settings(routed) is True and cf.privacy_from_settings(direct) is False and cf.privacy_from_settings({}) is None,
      "whether the agent's cloud calls are tokenized is read from the provider address the agent reports")
check(cf.provider_of({"model_presets": {"ultra": {"model": "nvidia/x:free", "provider": "openrouter"}, "gpu": {"model": "m/q", "provider": "device-gpu"}}}, "m/q") == "device-gpu"
      and cf.provider_of({}, "x") == "", "the provider of the chat's model comes from the agent's presets")
segments = cf.status_segments(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter", used=90_000, window=100_000, cost=0.0,
                              added=12, removed=3, branch="main", plan=True, privacy_on=False, pool="3 nodes", crew="5 seats")
keys = [s["key"] for s in segments]
check(keys == ["mode", "model", "privacy", "context", "lines", "branch", "cost", "pool", "crew"]
      and next(s for s in segments if s["key"] == "context")["tone"] == "warn" and next(s for s in segments if s["key"] == "cost")["text"] == "free"
      and next(s for s in segments if s["key"] == "privacy")["tone"] == "bad", "the status bar shows mode, model, privacy, context, changes, branch, cost, pool and crew")
check([s["key"] for s in cf.status_segments(provider="openrouter", privacy_on=True)] == ["privacy"], "a segment with nothing to say is left out")
check(cf.count_changes([("edit_file", {"old_string": "a\nb", "new_string": "a\nb\nc"}), ("exec", {"command": "ls"}),
                        ("write_file", {"content": "x\ny\n"})]) == (5, 2), "lines added and removed come from the edit calls")

seats = [{"id": "a", "name": "A", "weight": 0.4, "trust": 1.1, "scorecard": {"overall": {"score": 83, "lo": 70, "n": 30}}},
         {"id": "claude", "name": "Claude", "weight": 0.5, "external": True, "here": True, "trust": 1.0},
         {"id": "chair", "name": "Chair", "chair": True, "weight": 0.1, "trust": 1.0},
         {"id": "far", "name": "Far", "external": True, "here": False, "weight": 0.2}]
chips = cf.crew_chips(seats, "claude")
check([c["id"] for c in chips][0] == "claude" and chips[0]["head"] and {c["id"]: c["state"] for c in chips} == {"claude": "here", "a": "ready", "far": "away", "chair": "chair"}
      and next(c for c in chips if c["id"] == "a")["benchmark"] == "83 (≥70)" and next(c for c in chips if c["id"] == "far")["benchmark"] == "not benchmarked",
      "the crew strip puts the head first and shows who is here, and each seat's benchmark")
check(cf.crew_summary(seats, "claude") == "4 crew · Claude leads" and cf.crew_summary([], None) == "", "...with a summary for the status bar")
check(cf.parse_command("/rewind") == ("/rewind", "") and cf.parse_command("  /checkpoint before the refactor ") == ("/checkpoint", "before the refactor")
      and cf.parse_command("/compact") is None and cf.parse_command("hello /plan") is None, "the app's own slash commands are told from the agent's")
check(all(c.startswith("/") and d for c, d in cf.CLIENT_COMMANDS) and len({c for c, _ in cf.CLIENT_COMMANDS}) == len(cf.CLIENT_COMMANDS), "every command has a description")

print("codefeatures checks passed")
