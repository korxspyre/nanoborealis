# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""End-to-end protocol check: drives a real nanobot gateway through agent_link.

Start dev/stub_llm.py, a gateway whose custom provider points at it, and the remote-access
service in front of it; pair with dev/pairing_check.py, which saves the paired computer; then:

    python dev/smoke.py /tmp/paired.json

Everything goes the way the app goes: TLS pinned to the computer's certificate, with the device's
password. Exits non-zero on the first failed check.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_link import AgentLink, AuthError  # noqa: E402
from pairing import Machine  # noqa: E402


async def main(machine_file: str) -> None:
    with open(machine_file) as f:
        machine = Machine.from_json(json.load(f))
    assert machine is not None, f"{machine_file} doesn't hold a paired computer"
    events: asyncio.Queue[dict] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        await events.put(event)

    async def until(name: str, timeout: float = 60) -> list[dict]:
        seen = []
        while True:
            event = await asyncio.wait_for(events.get(), timeout)
            seen.append(event)
            if event["event"] == name:
                return seen

    def check(ok: bool, what: str) -> None:
        print(("ok   " if ok else "FAIL ") + what, flush=True)
        if not ok:
            raise SystemExit(1)

    # A device the computer doesn't know must fail fast with AuthError, not retry forever.
    stranger = Machine(machine.host, machine.port, machine.name, machine.certificate, "unknown-device-" * 3, "0000")
    bad = AgentLink(stranger, on_event)
    try:
        await asyncio.wait_for(bad.run(), 20)
        check(False, "an unknown device is turned away")
    except AuthError:
        check(True, "an unknown device is turned away")

    link = AgentLink(machine, on_event)
    runner = asyncio.create_task(link.run())
    try:
        await until("link_up", 30)
        check(True, "connected")

        chat_id = await link.new_chat()
        check(bool(chat_id), f"new chat {chat_id}")

        await link.send_message(chat_id, "hello from the smoke test")
        seen = await until("turn_end")
        names = [e["event"] for e in seen]
        print("     events:", " ".join(dict.fromkeys(names)))
        streamed = "".join(e.get("text", "") for e in seen if e["event"] == "delta" and e.get("chat_id") == chat_id)
        finals = [e.get("text", "") for e in seen if e["event"] == "message" and not e.get("kind")]
        answer = streamed or " ".join(finals)
        check("Stub reply to" in answer, "streamed a reply")
        check(any(e["event"] == "reasoning_delta" for e in seen), "reasoning arrived")

        await link.send_message(chat_id, "please use a tool")
        seen = await until("turn_end")
        hints = [e for e in seen if e["event"] == "message" and e.get("kind") in ("tool_hint", "progress")]
        print("     tool activity:", [h.get("text", "")[:60] for h in hints])
        check(bool(hints), "tool activity arrived")
        check("The tool finished" in "".join(e.get("text", "") for e in seen if e["event"] in ("delta", "stream_end", "message")),
              "answered after the tool")

        # nanobot answers /stop with a message and sends no turn_end for the cancelled turn.
        await link.send_message(chat_id, "use a tool, then get stopped")
        await asyncio.sleep(1.0)
        await link.stop_turn(chat_id)
        stopped = False
        try:
            while not stopped:
                event = await asyncio.wait_for(events.get(), 20)
                stopped = event["event"] == "message" and str(event.get("text", "")).startswith("Stopped")
        except asyncio.TimeoutError:
            pass
        check(stopped, "stop cancels a running turn")

        chats = await link.list_chats()
        check(any(c["chat_id"] == chat_id for c in chats), f"chat is listed ({len(chats)} chats)")
        row = next(c for c in chats if c["chat_id"] == chat_id)
        print("     chat row keys:", sorted(row))

        thread = await link.load_thread(chat_id)
        print("     thread keys:", sorted(thread))
        check(bool(thread), "thread history loads")

        other_events: list[dict] = []

        async def on_other(event: dict) -> None:
            other_events.append(event)

        second = AgentLink(machine, on_other)
        second_task = asyncio.create_task(second.run())
        for _ in range(100):
            if second.connected:
                break
            await asyncio.sleep(0.1)
        await second.attach(chat_id)
        check(second.chat_id == chat_id, "a second client can attach to the chat")
        await link.send_message(chat_id, "hello again")
        await until("turn_end")
        await asyncio.sleep(0.5)
        check(any(e["event"] in ("delta", "message", "stream_end") for e in other_events),
              "the second client sees the reply too")
        await second.close()
        second_task.cancel()

        # What the app's control center, model picker and chat list use: nanobot's WebUI API.
        await asyncio.sleep(0.5)
        while not events.empty():  # the last turn's stragglers (its goal_status) belong to no check below
            events.get_nowait()
        await link.send_message(chat_id, "one more")
        seen = await until("turn_end")
        check(any(e["event"] == "message_accepted" for e in seen), "messages are sent as the WebUI sends them")
        settings = await link.api_get("/api/settings")
        presets = [p.get("name") for p in settings.get("model_presets") or []]
        check("stub" in presets, f"settings list the model presets ({presets})")
        await link.request("settings.model_configuration.create", {
            "name": "stub-two", "model": "stub", "provider": "custom", "max_tokens": 1024,
            "context_window_tokens": 65536})
        presets = [p.get("name") for p in (await link.api_get("/api/settings")).get("model_presets") or []]
        check("stub-two" in presets, "a model preset can be added")
        await asyncio.sleep(0.5)
        while not events.empty():
            events.get_nowait()
        await link.send_message(chat_id, "/model stub-two")
        await until("turn_end")  # a command run while idle: its reply, then turn_end
        await link.send_message(chat_id, "which model now?")
        seen = await until("turn_end")
        models = [e.get("model_preset") for e in seen if e["event"] == "turn_model_updated"]
        check("stub-two" in models, f"/model switches this chat's model ({models})")
        commands = [c.get("command") for c in (await link.api_get("/api/commands")).get("commands") or []]
        check("/model" in commands and "/stop" in commands, f"the agent lists its commands ({len(commands)})")

        temporary = await link.new_temporary_chat()
        await link.send_message(temporary, "hello, nobody keeps this")
        seen = await until("turn_end")
        check(any(e.get("chat_id") == temporary for e in seen), "a temporary chat answers")
        await link.discard_temporary_chat(temporary)

        note = "data:text/plain;base64,aGVsbG8gZnJvbSBhIGZpbGU="  # "hello from a file"
        await link.send_message(chat_id, "what's in this file?", media=[{"data_url": note, "name": "note.txt"}])
        seen = await until("turn_end")
        check(not any(e["event"] == "error" for e in seen), "an attachment is accepted")

        key = f"websocket:{chat_id}"
        await link.request("sidebar.update", {"state": {"pinned_keys": [key], "title_overrides": {key: "Renamed"}}})
        state = await link.api_get("/api/webui/sidebar-state")
        check(key in state.get("pinned_keys", []) and state.get("title_overrides", {}).get(key) == "Renamed",
              "chats can be pinned and renamed")
        spare = await link.new_chat()
        await link.send_message(spare, "a chat to delete")
        await until("turn_end")
        result = await link.request("session.delete", {"key": f"websocket:{spare}"})
        chats = await link.list_chats()
        check(bool(result and result.get("deleted")) and not any(c["chat_id"] == spare for c in chats),
              "a chat can be deleted")
        await link.attach(chat_id)

        jobs = await link.api_get("/api/webui/automations")
        check(isinstance(jobs.get("jobs"), list), f"schedules are listed ({len(jobs.get('jobs', []))})")
        skills = await link.api_get("/api/webui/skills")
        check(isinstance(skills.get("skills"), list) and skills["skills"], f"skills are listed ({len(skills['skills'])})")
        features = (await link.api_get("/api/settings/nanobot-features", timeout=90)).get("features") or []
        telegram = next((f for f in features if f.get("name") == "telegram"), {})
        fields = {f.get("field"): f.get("kind") for f in (telegram.get("setup") or {}).get("fields") or []}
        check(fields.get("token") == "secret" and fields.get("allowFrom") == "list",
              "channels come with their setup fields")
    finally:
        await link.close()
        runner.cancel()
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
