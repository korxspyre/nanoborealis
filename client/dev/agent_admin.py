# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Talk to a paired computer's agent the way the app does, from a terminal: its WebUI's REST reads,
its management requests, and a throwaway question.

    python dev/agent_admin.py laptop.json get /api/settings
    python dev/agent_admin.py laptop.json request settings.agent.update '{"model": "x/y:free", "provider": "openrouter"}'
    python dev/agent_admin.py laptop.json ask "Reply with OK"      # in a temporary chat: nothing is saved

laptop.json is what dev/remote.py pair saves.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_link import AgentLink  # noqa: E402
from pairing import Machine  # noqa: E402


async def main(argv: list[str]) -> int:
    with open(argv[0]) as f:
        machine = Machine.from_json(json.load(f))
    if machine is None:
        raise SystemExit(f"{argv[0]} doesn't hold a paired computer")
    events: asyncio.Queue[dict] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        events.put_nowait(event)

    link = AgentLink(machine, on_event)
    if argv[1] == "get":
        print(json.dumps(await link.api_get(argv[2]), indent=2, ensure_ascii=False))
        return 0
    runner = asyncio.create_task(link.run())
    try:
        while (await asyncio.wait_for(events.get(), 30))["event"] != "link_up":
            pass
        if argv[1] == "request":
            result = await link.request(argv[2], json.loads(argv[3]) if len(argv) > 3 else {})
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif argv[1] == "ask":
            chat_id = await link.new_temporary_chat()
            await link.send_message(chat_id, argv[2])
            text, model = "", None
            while True:
                event = await asyncio.wait_for(events.get(), 300)
                if event.get("chat_id") != chat_id:
                    continue
                if event["event"] == "turn_model_updated":
                    model = event.get("model_name")
                elif event["event"] == "delta":
                    text += str(event.get("text") or "")
                elif event["event"] == "message" and not event.get("kind"):
                    text = text or str(event.get("text") or "")
                elif event["event"] == "turn_end":
                    print(f"model: {model}\nusage: {event.get('usage')}\noutcome: {event.get('outcome', 'ok')} "
                          f"{event.get('failure_message') or ''}\nanswer: {text.strip()[:500]}")
                    break
            await link.discard_temporary_chat(chat_id)
        else:
            raise SystemExit(__doc__)
    finally:
        await link.close()
        runner.cancel()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
