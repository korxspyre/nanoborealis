# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks how the chat list shows chats: a saved title that is a reasoning model thinking aloud shows
the first message instead, real titles are left alone, and the agent's zone-less (UTC) times are
read as UTC. Also how a file from the projects panel is mentioned in a message, and which folder
scope a chat is given.

    python dev/chat_title_check.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import datetime, timezone  # noqa: E402

from control import remote_install_hint  # noqa: E402
from workspace import chat_time, chat_title, day_group, mention_text, project_scope  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def main() -> None:
    first = "Reply with just the word OK."
    for leaked in ("The user wants a concise title for the chat. The chat consi\u2026",
                   "The user asked me to use the list_dir tool on the workspace\u2026",
                   "We need to produce a short title", "Okay, the user is asking"):
        check(chat_title({"key": "c", "title": leaked, "preview": first}) == first, f"reasoning is not a title: {leaked[:30]}")
    for real in ("Comprehensive Environment Evaluation", "Building a TB-Scale NAS with ZFS",
                 "Theme Park Ride Ideas", "Weekly planning"):
        check(chat_title({"key": "c", "title": real, "preview": first}) == real, f"a real title stays: {real[:30]}")
    check(chat_title({"key": "c", "title": "The user wants x", "preview": ""}) == "New chat", "nothing to show: New chat")
    check(chat_title({"key": "c", "title": "The user wants x", "preview": first}, {"c": "Mine"}) == "Mine",
          "a title the owner typed always wins")

    # The agent's stamps have no zone and are UTC; an aware stamp is converted as it always was.
    naive = chat_time("2026-10-21T00:05:59.860051")
    aware = chat_time("2026-10-21T00:05:59.860051+00:00")
    check(naive == aware, "a stamp without a zone is read as UTC, like the same stamp with one")
    check(naive == datetime(2026, 10, 21, 0, 5, 59, 860051, tzinfo=timezone.utc).astimezone().replace(tzinfo=None),
          "and shown in this computer's time")
    check(day_group(datetime.now()) == "Today", "now is today")
    check(day_group(chat_time(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())) == "Today",
          "a chat made just now, stamped in UTC, is under Today whatever the time zone or hour")

    # Mentioning a file: the agent's path, in backticks (a message starting with "/" is a command).
    check(mention_text("/srv/nanoborealis/projects/nb-test/hello.py") == "`/home/nanobot/projects/nb-test/hello.py`",
          "a mentioned file is named by the agent's path")
    check(mention_text("/srv/nanoborealis/projects") == "`/home/nanobot/projects`", "the projects folder itself too")
    check(mention_text("/srv/nanoborealis/projects-old/x") == "`/srv/nanoborealis/projects-old/x`",
          "a folder that only starts with the same letters is left alone")
    check(not mention_text("/srv/nanoborealis/projects/a").startswith("/"), "a mention never starts with a slash")

    # The folder a chat works in: full access only for the computer itself.
    check(project_scope("nb-test", False, True) == {"project_path": "/home/nanobot/projects/nb-test", "access_mode": "full"},
          "on the computer, a project is full access unless asked to stay inside")
    check(project_scope("nb-test", True, True)["access_mode"] == "restricted", "on the computer, staying inside is honored")
    check(project_scope("nb-test", False, False)["access_mode"] == "restricted",
          "from another device a project is always kept inside its folder (nanobot refuses full access)")
    check(project_scope(None, False, False) == {"project_path": "/home/nanobot/projects", "access_mode": "restricted"},
          "all projects, from another device, is the projects folder, kept inside")

    # A channel that has to install a package is refused from another device: say so in plain words.
    refused = ("Installing optional features from a remote WebUI is disabled. Run this action from localhost or "
               "set tools.webuiAllowRemotePackageInstall to true.")
    hint = remote_install_hint(refused, "nanoborealis")
    check("Set it up from the NanoBorealis app on nanoborealis" in hint and refused in hint,
          "the refusal to install a channel's packages says what to do, and keeps the agent's own words")
    check(remote_install_hint("Token rejected", "x") == "Token rejected", "other reasons are shown as they are")
    print("all chat list checks passed")


if __name__ == "__main__":
    main()
