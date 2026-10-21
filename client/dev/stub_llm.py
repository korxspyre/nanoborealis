# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A stand-in OpenAI-compatible model for testing the client against a real nanobot gateway.

It streams a Markdown reply with a little reasoning first. If the user's message contains
the word "tool", the first round calls one of the agent's tools instead, so tool activity
shows up too. Free, offline, and deterministic.

    python dev/stub_llm.py [port]        # default 18080; base URL http://127.0.0.1:18080/v1
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK_DELAY_S = 0.04


def last_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


def pick_tool(tools: list[dict], user_text: str = "") -> dict | None:
    names = [t.get("function", {}).get("name", "") for t in tools]
    # "write a file" and "edit the file" ask for those tools (to see file changes in the app);
    # anything else mentioning a tool gets the first of these the gateway offers.
    wanted = ["write_file"] if "write" in user_text.lower() else ["edit_file"] if "edit" in user_text.lower() else []
    for name in [*wanted, "list_dir", "exec", "read_file"]:
        if name in names:
            return next(t for t in tools if t.get("function", {}).get("name") == name)
    return tools[0] if tools else None


DEMO = "def greet(name):\n    return 'Hello, ' + name\n\n\nprint(greet('world'))\n"


def tool_arguments(name: str) -> dict:
    return {"list_dir": {"path": "."}, "exec": {"command": "echo stub-tool-ran"}, "read_file": {"path": "USER.md"},
            "write_file": {"path": "demo/hello.py", "content": DEMO},
            "edit_file": {"path": "demo/hello.py", "old_text": "    return 'Hello, ' + name\n",
                          "new_text": "    # Friendlier, with an f-string.\n    return f'Hello, {name}!'\n"}}.get(name, {})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("stub_llm: " + fmt % args + "\n")

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [{"id": "stub", "object": "model"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json({"error": f"unsupported path {self.path}"}, 404)
            return
        messages = body.get("messages", [])
        user_text = last_user_text(messages)
        after_tool = bool(messages) and messages[-1].get("role") == "tool"
        wants_tool = any(word in user_text.lower() for word in ("tool", "write a file", "edit the file"))
        tool = pick_tool(body.get("tools") or [], user_text) if wants_tool and not after_tool else None
        sys.stderr.write(f"stub_llm: {len(messages)} messages, stream={body.get('stream')}, tool={tool and tool['function']['name']}\n")

        if tool is not None:
            name = tool["function"]["name"]
            call = {"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(tool_arguments(name))}}
            reasoning, text, calls = "The user asked for a tool, so I will call one.", "", [call]
        else:
            reasoning = "Let me think about how to answer this briefly."
            prefix = "The tool finished. " if after_tool else ""
            text = (
                f"{prefix}Stub reply to: *{user_text.strip()[:80]}*\n\n"
                "Here is some **Markdown** to check rendering:\n\n"
                "```python\nprint(\"hello from the stub model\")\n```\n\n"
                "- first point\n- second point\n"
            )
            calls = []

        if body.get("stream"):
            self._stream(reasoning, text, calls)
        else:
            message = {"role": "assistant", "content": text or None, "reasoning_content": reasoning}
            if calls:
                message["tool_calls"] = calls
            self._json({
                "id": "stub-1", "object": "chat.completion", "created": int(time.time()), "model": "stub",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            })

    def _stream(self, reasoning: str, text: str, calls: list[dict]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> None:
            payload = {"id": "stub-1", "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": "stub", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if usage:
                payload["usage"] = usage
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()
            time.sleep(CHUNK_DELAY_S)

        chunk({"role": "assistant", "content": ""})
        for word in reasoning.split(" "):
            chunk({"reasoning_content": word + " "})
        for i in range(0, len(text), 6):
            chunk({"content": text[i:i + 6]})
        for index, call in enumerate(calls):
            chunk({"tool_calls": [{"index": index, **call}]})
        chunk({}, "tool_calls" if calls else "stop",
              {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    print(f"stub model on http://127.0.0.1:{port}/v1", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
