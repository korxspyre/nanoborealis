# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks that a real model backend can drive the agent: one turn that has to use a tool.

Point a nanobot gateway at the backend (exo, or a cloud model), then:

    python dev/agent_check.py http://127.0.0.1:18765 <password> [timeout-seconds]

Passes when the agent calls a tool and then answers. Prints what happened either way.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_link import AgentLink  # noqa: E402

PROMPT = ("Use your list_dir tool on your workspace directory, then tell me in one sentence "
          "how many entries it has. You must call the tool.")


async def main(address: str, password: str, timeout: float) -> int:
    events: asyncio.Queue[dict] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        await events.put(event)

    link = AgentLink(address, password, on_event)
    runner = asyncio.create_task(link.run())
    started = time.monotonic()
    tools, answer, errors = [], "", []
    try:
        while (await asyncio.wait_for(events.get(), 60))["event"] != "link_up":
            pass
        chat = await link.new_chat()
        await link.send_message(chat, PROMPT)
        deadline = started + timeout
        while True:
            event = await asyncio.wait_for(events.get(), max(1.0, deadline - time.monotonic()))
            name = event["event"]
            if name == "message" and (event.get("kind") or event.get("tool_events")):
                for tool_event in event.get("tool_events") or []:
                    if tool_event.get("phase") == "start":
                        tools.append(f"{tool_event.get('name')}({tool_event.get('arguments')})")
            elif name in ("delta", "stream_end", "message"):
                answer += str(event.get("text") or "")
            elif name == "error":
                errors.append(str(event.get("detail")))
            elif name == "turn_model_updated":
                print(f"model: {event.get('model_name')}{' (fallback)' if event.get('fallback') else ''}")
            if name == "turn_end":
                break
    except asyncio.TimeoutError:
        errors.append(f"no finished turn within {timeout:.0f}s")
    finally:
        await link.close()
        runner.cancel()
    print(f"took {time.monotonic() - started:.0f}s")
    print("tool calls:", tools or "none")
    print("answer:", answer.strip()[:500] or "(none)")
    if errors:
        print("errors:", errors)
    ok = bool(tools) and bool(answer.strip()) and not errors
    print("PASS: the backend drove a tool-using agent turn" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 900)))
