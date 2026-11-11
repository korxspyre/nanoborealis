# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The router tokenizes: a fake provider sees placeholders, never the email or key in the request; the answer
(plain or streamed) comes back with the real values; an image is refused; with tokenizing off, the text goes as is."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(tempfile.mkdtemp())
os.environ["NANOBOREALIS_ROUTER_STATE"] = str(STATE)
os.environ["NANOBOREALIS_SHARE"] = str(ROOT / "system_files/usr/share/nanoborealis")
loader = importlib.machinery.SourceFileLoader("router", str(ROOT / "system_files/usr/libexec/nanoborealis-router"))
spec = importlib.util.spec_from_loader("router", loader)
router = importlib.util.module_from_spec(spec)
loader.exec_module(router)

SEEN: list[str] = []
failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(("ok   " if ok else "FAIL ") + what)
    failures += 0 if ok else 1


class Echo(BaseHTTPRequestHandler):
    """A provider that answers with what it was sent, so the test sees both sides."""

    def log_message(self, *_a) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        said = body["messages"][-1]["content"]
        SEEN.append(json.dumps(body))
        if body.get("stream"):
            half = len(said) // 2
            chunks = [said[:half], said[half:]]  # a placeholder may be cut in two
            data = "".join(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': c}}]})}\n\n" for c in chunks)
            data += f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\ndata: [DONE]\n\n"
            out, kind = data.encode(), "text/event-stream"
        else:
            out = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "You said: " + said}}]}).encode()
            kind = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


echo = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
threading.Thread(target=echo.serve_forever, daemon=True).start()
router.BUILT_IN["groq"]["url"] = f"http://127.0.0.1:{echo.server_port}"
router.write("providers.json", {"groq": {"key": "x", "smart": "m", "fast": "m"}})
front = ThreadingHTTPServer(("127.0.0.1", 0), router.Handler)
threading.Thread(target=front.serve_forever, daemon=True).start()
TOKEN = router.client_token()


def ask(content, stream: bool = False) -> tuple[int, str]:
    body = {"model": "groq:m", "stream": stream, "messages": [{"role": "user", "content": content}]}
    request = urllib.request.Request(f"http://127.0.0.1:{front.server_port}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


PRIVATE = "Write to jane.doe@example.com, key sk-or-v1-abcdef0123456789abcdef0123456789"
code, answer = ask(PRIVATE)
check(code == 200, "a request goes through with tokenizing on (the default)")
check("jane.doe@example.com" not in SEEN[-1] and "sk-or-v1-abcdef" not in SEEN[-1] and "<<EMAIL_" in SEEN[-1],
      "the provider sees placeholders, not the email or the key")
check("jane.doe@example.com" in answer and "sk-or-v1-abcdef0123456789abcdef0123456789" in answer and "<<" not in answer,
      "the answer comes back with the real values")

code, answer = ask(PRIVATE, stream=True)
text = "".join(json.loads(line[5:])["choices"][0]["delta"].get("content", "") for line in answer.splitlines()
               if line.startswith("data:") and "[DONE]" not in line)
check("jane.doe@example.com" not in SEEN[-1], "a streamed request is tokenized too")
check("jane.doe@example.com" in text and "<<" not in text, "a streamed answer is restored, a placeholder split in two included")

code, answer = ask([{"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
check(code == 400 and ("image" in answer.lower() or "media" in answer.lower()), "an image is refused, not sent")

router.main(["privacy", "off"])
ask(PRIVATE)
check("jane.doe@example.com" in SEEN[-1], "with tokenizing off, the text goes as it is")
check(router.status()["tokenize"] is False, "status says tokenizing is off")
router.main(["privacy", "on"])
check(router.status()["tokenize"] is True, "and on again")

echo.shutdown()
front.shutdown()
print("router tokenize checks passed" if not failures else f"{failures} router tokenize check(s) failed")
sys.exit(1 if failures else 0)
