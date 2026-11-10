# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""What a coding agent's workspace has beyond the transcript, as plain logic with no UI in it: checkpoints and
rewind, plan mode, the status bar, the crew strip, and the app's own slash commands. workspace.py wires them
to the screen (codeui.py draws them); dev/codefeatures_check.py tests them.

Checkpoints keep a copy of the project folder as it was before each message, in a *shadow* git repository of
their own on the NanoBorealis computer (~/.local/state/nanoborealis/checkpoints), never in the project's repo: it
works for a folder that isn't a repo, writes no refs or objects into one that is, and respects the project's
.gitignore. Restoring puts the files back as they were and removes the ones made since. It is code only: it
doesn't rewind the conversation (the agent has no way to take a message back), and, like Claude Code's, it
can't see what a command changed outside the folder.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from typing import Callable

# -- Checkpoints -----------------------------------------------------------------------------------------------

STATE = "${XDG_STATE_HOME:-$HOME/.local/state}/nanoborealis/checkpoints"
TOO_BIG_KB = 1_500_000  # a folder over about 1.5 GB isn't copied before every message
# Rebuilt anyway, and slow to copy before every message.
SKIP = " ".join(f"':(exclude,glob)**/{d}/**'" for d in ("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
                                                            ".mypy_cache", ".next", "target", ".worktrees", ".git"))
DU_SKIP = " ".join(f"--exclude={d}" for d in ("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                                                   ".next", "target", ".worktrees", ".git"))
IDENT = ("GIT_AUTHOR_NAME=NanoBorealis GIT_AUTHOR_EMAIL=checkpoint@nanoborealis.invalid "
         "GIT_COMMITTER_NAME=NanoBorealis GIT_COMMITTER_EMAIL=checkpoint@nanoborealis.invalid")
Run = Callable[[str], "tuple[int | None, str]"]  # runs a shell command on the computer: (exit status, output)


def shadow_dir(folder: str) -> str:
    """Where a folder's checkpoints live: one bare repository per folder, named by its path."""
    return f"{STATE}/{hashlib.sha256(folder.encode()).hexdigest()[:16]}.git"


def _setup(folder: str) -> str:
    return (f"folder={shlex.quote(folder)}; shadow=\"{shadow_dir(folder)}\"; "
            "[ -d \"$folder\" ] || { echo NO_FOLDER; exit 0; }; "
            "command -v git >/dev/null 2>&1 || { echo NO_GIT; exit 0; }; "
            "[ -d \"$shadow\" ] || { mkdir -p \"$shadow\" && git init -q --bare \"$shadow\"; }; "
            "export GIT_DIR=\"$shadow\" GIT_WORK_TREE=\"$folder\"; cd \"$folder\" || exit 1; ")


def take_command(folder: str, chat: str, label: str) -> str:
    chat = re.sub(r"[^A-Za-z0-9_-]", "_", chat)[:60] or "chat"
    label = " ".join(label.split())[:160]
    return (_setup(folder)
            + f"[ \"$(du -sk {DU_SKIP} . 2>/dev/null | cut -f1)\" -le {TOO_BIG_KB} ] || {{ echo TOO_BIG; exit 0; }}; "
            "export GIT_INDEX_FILE=\"$(mktemp -u)\"; git add -A -- . " + SKIP + " >/dev/null 2>&1; tree=$(git write-tree) || exit 1; "
            f"parent=$(git rev-parse -q --verify \"refs/latest/{chat}\" || true); "
            f"commit=$(printf '%s\\n' {shlex.quote(label)} | env {IDENT} git commit-tree \"$tree\" ${{parent:+-p $parent}}) || exit 1; "
            f"git update-ref \"refs/checkpoints/{chat}/$(date +%s)-$(git for-each-ref refs/checkpoints/{chat}/ | wc -l | tr -d ' ')\" \"$commit\"; "
            f"git update-ref \"refs/latest/{chat}\" \"$commit\"; rm -f \"$GIT_INDEX_FILE\"; "
            "echo \"CHECKPOINT $commit $(git ls-tree -r --name-only \"$tree\" | wc -l | tr -d ' ')\"")


def list_command(folder: str, chat: str) -> str:
    chat = re.sub(r"[^A-Za-z0-9_-]", "_", chat)[:60] or "chat"
    return (_setup(folder) + f"git for-each-ref --sort=creatordate "
            f"--format='CP|%(objectname)|%(creatordate:unix)|%(contents:subject)' refs/checkpoints/{chat}/")


def restore_command(folder: str, sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{7,64}", sha):
        raise ValueError("a checkpoint is named by a git object id")
    return (_setup(folder)
            + f"git cat-file -e {sha}^{{commit}} 2>/dev/null || {{ echo NO_SUCH_CHECKPOINT; exit 1; }}; "
            # Only what differs is touched: rewriting every file of a big folder (all projects) hit files this
            # account can't write ("unable to unlink ... Permission denied") in projects the rewind wasn't about.
            "tmp=$(mktemp -d); export GIT_INDEX_FILE=\"$tmp/index\"; export LC_ALL=C; "
            "git add -A -- . " + SKIP + " >/dev/null 2>&1; now=$(git write-tree) || exit 1; "
            f"git diff-tree -r --name-only --no-renames --diff-filter=AMT \"$now\" {sha} > \"$tmp/back\"; "
            f"git diff-tree -r --name-only --no-renames --diff-filter=D \"$now\" {sha} > \"$tmp/gone\"; "
            f"git read-tree {sha} || exit 1; "
            "[ -s \"$tmp/back\" ] && { tr '\\n' '\\0' < \"$tmp/back\" | xargs -0 git checkout-index -f -- || exit 1; }; "
            "removed=0; while IFS= read -r p; do rm -f -- \"$p\" && removed=$((removed + 1)); done < \"$tmp/gone\"; "
            "echo \"RESTORED $(wc -l < \"$tmp/back\" | tr -d ' ') $removed\"; rm -rf \"$tmp\"")


class Checkpoints:
    """A project folder's checkpoints for one chat. `run` runs a shell command on the NanoBorealis computer."""

    def __init__(self, run: Run, folder: str):
        self.run, self.folder = run, folder.rstrip("/") or "/"

    def take(self, chat: str, label: str) -> dict | None:
        """Copy the folder as it is now. None when it can't be (no git there, a folder too big, a missing folder)."""
        _status, out = self.run(take_command(self.folder, chat, label))
        found = re.search(r"^CHECKPOINT ([0-9a-f]{40,64}) (\d+)$", out, re.M)
        return {"sha": found.group(1), "files": int(found.group(2)), "label": label} if found else None

    def why_not(self, out: str) -> str:
        return {"NO_FOLDER": "the project folder isn't there", "NO_GIT": "git isn't installed on the computer",
                "TOO_BIG": "the folder is too big to copy before every message"}.get(out.strip().splitlines()[-1] if out.strip() else "", "")

    def list(self, chat: str) -> list[dict]:
        _status, out = self.run(list_command(self.folder, chat))
        items = []
        for line in out.splitlines():
            if line.startswith("CP|"):
                _, sha, when, label = (line.split("|", 3) + [""])[:4]
                if re.fullmatch(r"[0-9a-f]{40,64}", sha) and when.isdigit():
                    items.append({"sha": sha, "at": int(when), "label": label})
        return items

    def restore(self, sha: str) -> dict:
        """Put the folder back as it was at a checkpoint. Takes one of the present first, so a restore can be undone."""
        before = self.take("undo", "before a restore")
        status, out = self.run(restore_command(self.folder, sha))
        found = re.search(r"^RESTORED (\d+) (\d+)$", out, re.M)
        if not found:
            return {"ok": False, "error": "that checkpoint isn't there" if "NO_SUCH" in out else (out.strip().splitlines() or ["it failed"])[-1][:200]}
        return {"ok": True, "files": int(found.group(1)), "removed": int(found.group(2)), "undo": before["sha"] if before else None}


# -- Plan mode -------------------------------------------------------------------------------------------------

PLAN_PREFIX = ("[Plan mode] Do not edit, create or delete any file and do not run any command that changes anything. You may read "
               "and search. Work out how you would do the following, and end with a short numbered plan, the files you would "
               "touch, and anything you are unsure of. Then stop and wait.\n\n")
APPROVE = "Go ahead with the plan you just wrote. Do the steps in order and tell me when each is done."


def plan_message(text: str) -> str:
    return PLAN_PREFIX + text


# -- Status bar ------------------------------------------------------------------------------------------------


def privacy_label(provider: str, privacy_on: bool | None) -> tuple[str, str]:
    """(words, tone) for where a model runs and whether what it is sent is tokenized. Never says 'protected' for a path
    that isn't: the chair's OpenRouter calls are only tokenized once `nanoborealis privacy on`, which the app reads from
    the agent's own folder (None: it couldn't tell)."""
    provider = (provider or "").lower()
    if provider.startswith(("device-", "pool")):
        return "local", "ok"
    if privacy_on:
        return "cloud · tokenized", "ok"
    return ("cloud · not tokenized", "bad") if privacy_on is False else ("cloud · unknown", "warn")


def privacy_from_settings(settings: dict) -> bool | None:
    """Whether the agent's OpenRouter calls go through the tokenizing relay (`nanoborealis privacy on`), from the provider
    list the agent reports: True when its address is the relay's, False when it isn't, None when it can't be told."""
    for provider in (settings or {}).get("providers") or []:
        if isinstance(provider, dict) and (provider.get("name") or provider.get("provider")) == "openrouter":
            base = str(provider.get("api_base") or provider.get("apiBase") or "")
            return "/src/openrouter/" in base if base or "api_base" in provider or "apiBase" in provider else False
    return None


def provider_of(settings: dict, model_id: str | None) -> str:
    """The provider the chat's current model is served by, as the agent's presets name it ("" when unknown)."""
    raw = (settings or {}).get("model_presets")
    presets = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
    for preset in presets:
        if isinstance(preset, dict) and preset.get("model") == model_id:
            return str(preset.get("provider") or "")
    return ""


def status_segments(*, model: str = "", provider: str = "", used: int | None = None, window: int | None = None,
                    cost: float | None = None, added: int = 0, removed: int = 0, branch: str = "", plan: bool = False,
                    privacy_on: bool | None = None, pool: str = "", crew: str = "") -> list[dict]:
    """The bar under the composer: what is in use, what it costs, what changed, where it runs. A segment with
    nothing to say is left out."""
    segs: list[dict] = []
    if plan:
        segs.append({"key": "mode", "text": "Plan first", "tone": "accent", "tip": "The next message gets a plan, not changes (/plan to turn it off)"})
    if model:
        segs.append({"key": "model", "text": model.rsplit("/", 1)[-1][:34], "tone": "muted", "tip": model})
    words, tone = privacy_label(provider, privacy_on)
    segs.append({"key": "privacy", "text": words, "tone": tone,
                 "tip": "Where this chat's model runs. Cloud models only see placeholders for emails, keys, numbers and "
                        "addresses while `nanoborealis privacy` is on."})
    if isinstance(used, int) and isinstance(window, int) and window > 0:
        share = used / window
        segs.append({"key": "context", "text": f"{share:.0%} context", "tone": "warn" if share > 0.8 else "muted",
                     "tip": f"{used:,} of {window:,} tokens"})
    if added or removed:
        segs.append({"key": "lines", "text": f"+{added} −{removed}", "tone": "muted", "tip": "Lines changed by the agent this chat"})
    if branch:
        segs.append({"key": "branch", "text": branch[:30], "tone": "muted", "tip": "The project's git branch"})
    if cost is not None:
        segs.append({"key": "cost", "text": f"${cost:.2f}" if cost >= 0.005 else "free", "tone": "muted", "tip": "What OpenRouter has charged your key"})
    if pool:
        segs.append({"key": "pool", "text": pool, "tone": "muted", "tip": "Your pool of computers"})
    if crew:
        segs.append({"key": "crew", "text": crew, "tone": "muted", "tip": "Your crew (/crew)"})
    return segs


def count_changes(tool_cards: list[tuple[str, dict]]) -> tuple[int, int]:
    """(+lines, −lines) from this chat's edit tool calls, as their arguments describe them."""
    added = removed = 0
    for name, args in tool_cards:
        if not isinstance(args, dict) or name not in ("edit_file", "write_file", "apply_patch", "edit"):
            continue
        new = str(args.get("new_string") or args.get("content") or args.get("new") or "")
        old = str(args.get("old_string") or args.get("old") or "")
        added += len([l for l in new.splitlines() if l.strip()]) if new else 0
        removed += len([l for l in old.splitlines() if l.strip()]) if old else 0
    return added, removed


# -- The crew --------------------------------------------------------------------------------------------------


def crew_chips(seats: list[dict], head: str | None) -> list[dict]:
    """The colony's seats as chips: who they are, whether they are here, how far to trust them, who leads."""
    chips = []
    for seat in sorted(seats, key=lambda s: (s["id"] != head, -float(s.get("weight") or 0))):
        card = (seat.get("scorecard") or {}).get("overall")
        if seat.get("external"):
            state = "here" if seat.get("here", True) else "away"
        else:
            state = "chair" if seat.get("chair") else "ready"
        chips.append({"id": seat["id"], "name": seat.get("name") or seat["id"], "head": seat["id"] == head, "state": state,
                      "trust": seat.get("trust"), "benchmark": f"{card['score']:.0f} (≥{card['lo']:.0f})" if card else "not benchmarked",
                      "model": seat.get("model", "")})
    return chips


def crew_summary(seats: list[dict], head: str | None) -> str:
    if not seats:
        return ""
    name = next((s.get("name") or s["id"] for s in seats if s["id"] == head), "")
    return f"{len(seats)} crew" + (f" · {name} leads" if name else "")


# -- The app's own slash commands ------------------------------------------------------------------------------

CLIENT_COMMANDS = [
    ("/plan", "Plan first: the next message gets a plan, not changes"),
    ("/rewind", "Put the project's files back as they were before an earlier message"),
    ("/checkpoint", "Save the project's files now, with a name"),
    ("/context", "How full this chat's context is, and what to do about it"),
    ("/crew", "Show or hide your crew, who leads, and their benchmarks"),
    ("/head", "Who is the head agent now"),
    ("/cost", "What the cloud models have cost so far"),
    ("/diff", "Open the changes the agent has made"),
    ("/privacy", "Where this chat's model runs and whether it is tokenized"),
    ("/memory", "Open what the agent remembers"),
    ("/agents", "Open your colony"),
    ("/permissions", "How freely the agent's tools run in this chat: auto, accept edits, ask first, plan only"),
    ("/btw", "A side question that doesn't go into this chat: /btw what does this error mean?"),
    ("/mcp", "Open the MCP servers"),
    ("/hooks", "Open the hooks"),
    ("/subagents", "Open the subagents"),
    ("/skills", "Open the skills; /skill-name runs one"),
    ("/hands", "Let agents use this device, or undo an agent's job on it"),
]
CLIENT_COMMAND_NAMES = {c for c, _ in CLIENT_COMMANDS}


def parse_command(text: str) -> tuple[str, str] | None:
    """('/rewind', 'rest') if `text` is one of the app's own commands, else None (it goes to the agent)."""
    word, _, rest = text.strip().partition(" ")
    return (word, rest.strip()) if word in CLIENT_COMMAND_NAMES else None
