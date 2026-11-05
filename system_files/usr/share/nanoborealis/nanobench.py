# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""NanoBench: the council's own benchmarks, run on the models that sit on it, graded by code.

    python3 nanobench.py run <model> [--route pool|<source>] [--n 6] [--seed S] [--json]
    python3 nanobench.py seats [--n 6] [--hall URL] [--admin-token-file PATH]
                                    run the suite on every pool seat's model and post the results to the Hall

Five areas, each a set of generated items with one right answer a program can check, so no model grades
another and nobody's opinion enters:
  reasoning  a word problem with an exact number, an ordering puzzle
  format     strict JSON with no other text; exact counts and required words
  fidelity   copy a privacy placeholder (<<EMAIL_...>>) character for character, invent none: the council
             and the tokenizing proxy depend on it
  tools      call the right tool with the right arguments
  review     tell a function with a planted bug from a correct one

The items are generated from a seed (by default the day), so they change from run to run and a model can't
be tuned to a fixed list. An item that errors (the model was down) is not a failure and not counted; a
run where half the items errored raises instead of reporting a score. A score is shown with its 95%
Wilson interval, because six items say little: the Hall trusts the lower end.

What this is not. It measures how a model does on these items on this computer, to rank the council's
members for the council's work. It isn't comparable with public leaderboards, and nothing here executes
model-written code.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SUITE = "nanobench-1"
AREAS = ("reasoning", "format", "fidelity", "tools", "review")
PLACEHOLDER = re.compile(r"<<[A-Za-z_]+_[0-9a-fA-F]{10,16}>>")


class BenchError(Exception):
    pass


@dataclass
class Item:
    area: str
    messages: list[dict]
    check: Callable[[dict], bool]  # given the assistant message, did it get it right?
    tools: list | None = None


def say(text: str, system: str = "") -> list[dict]:
    return ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text}]


def content(message: dict) -> str:
    return str(message.get("content") or "")


# -- The items ---------------------------------------------------------------------------------------------


def arithmetic(rng: random.Random) -> Item:
    a, b, c = rng.randint(12, 60), rng.randint(3, 9), rng.randint(5, 40)
    answer = a * b - c + b * b
    question = (f"A shop has {a} boxes with {b} items each. It sells {c} items, then receives {b} more boxes "
                f"with {b} items each. How many items does it have now? End your reply with a line "
                f"'ANSWER: <number>'.")
    return Item("reasoning", say(question), lambda m: _answer(content(m)) == str(answer))


def ordering(rng: random.Random) -> Item:
    names = rng.sample(["Ada", "Bram", "Cleo", "Dev", "Esme", "Finn", "Gus", "Hana", "Ines", "Jory"], 4)
    facts = [f"{names[i]} is taller than {names[i + 1]}." for i in range(3)]
    rng.shuffle(facts)
    question = (" ".join(facts) + " Who is the shortest? End your reply with a line 'ANSWER: <name>'.")
    return Item("reasoning", say(question), lambda m: _answer(content(m)).lower() == names[-1].lower())


def _answer(text: str) -> str:
    found = re.findall(r"ANSWER:\s*\**\s*([^\n*]+?)\s*\**\s*$", text, re.M)
    return found[-1].strip().rstrip(".") if found else ""


def json_only(rng: random.Random) -> Item:
    word, n = rng.choice(["falcon", "granite", "harbor", "juniper", "meadow"]), rng.randint(10, 90)
    tags = sorted(rng.sample(["red", "blue", "green", "amber", "plum"], 2))
    question = (f"Reply with only a JSON object, no code fences and no other text, with the keys \"name\" (the string "
                f"\"{word}\"), \"count\" (the number {n} plus 7, as an integer) and \"tags\" (a list of the words "
                f"{tags[1]} and {tags[0]} in alphabetical order).")

    def good(m: dict) -> bool:
        try:
            return json.loads(content(m).strip()) == {"name": word, "count": n + 7, "tags": tags}
        except ValueError:
            return False
    return Item("format", say(question), good)


def bullets(rng: random.Random) -> Item:
    k, word = rng.randint(3, 5), rng.choice(["careful", "simple", "local", "steady"])
    topic = rng.choice(["a bicycle", "a lighthouse", "bread", "a river"])
    question = (f"Write exactly {k} bullet points about {topic}. Each must start with '- ' and contain the word "
                f"'{word}'. Write nothing else.")

    def good(m: dict) -> bool:
        lines = [l for l in content(m).strip().splitlines() if l.strip()]
        return len(lines) == k and all(l.startswith("- ") and word in l.lower() for l in lines)
    return Item("format", say(question), good)


def _token(rng: random.Random, kind: str) -> str:
    return f"<<{kind}_{''.join(rng.choice('0123456789abcdef') for _ in range(10))}>>"


def fidelity(rng: random.Random) -> Item:
    email, person, phone = _token(rng, "EMAIL"), _token(rng, "PERSON"), _token(rng, "PHONE")
    question = (f"Write a two-sentence reply to {email} confirming that {person} will call {phone} tomorrow. Copy every "
                f"<<...>> placeholder exactly as written, character for character. Do not shorten, translate, change "
                f"or explain them, and do not make up any others.")

    def good(m: dict) -> bool:
        text = content(m)
        return all(t in text for t in (email, person, phone)) and set(PLACEHOLDER.findall(text)) <= {email, person, phone}
    return Item("fidelity", say(question), good)


def tool_call(rng: random.Random) -> Item:
    tools = [{"type": "function", "function": {"name": "add_numbers", "description": "Add two integers",
                                               "parameters": {"type": "object", "properties": {
                                                   "a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}},
             {"type": "function", "function": {"name": "get_weather", "description": "The current weather in a city",
                                               "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                                                              "required": ["city"]}}}]
    if rng.random() < 0.5:
        a, b = rng.randint(2, 99), rng.randint(2, 99)
        question, name, want = f"What is {a} plus {b}? Use the tool.", "add_numbers", {"a": a, "b": b}
    else:
        city = rng.choice(["Oslo", "Lima", "Kyoto", "Cairo"])
        question, name, want = f"What is the weather in {city}? Use the tool.", "get_weather", {"city": city}

    def good(m: dict) -> bool:
        try:
            call = (m.get("tool_calls") or [])[0]["function"]
            return call["name"] == name and json.loads(call["arguments"]) == want
        except (IndexError, KeyError, TypeError, ValueError):
            return False
    return Item("tools", say(question), good, tools)


REVIEWS = [  # (function name, correct body, buggy body)
    ("last_item", "return items[len(items) - 1]", "return items[len(items)]"),
    ("is_even", "return n % 2 == 0", "return n % 2 == 1"),
    ("average", "return sum(xs) / len(xs)", "return sum(xs) / (len(xs) + 1)"),
    ("clamp", "return max(lo, min(x, hi))", "return min(lo, max(x, hi))"),
    ("is_empty", "return len(items) == 0", "return len(items) > 0"),
    ("square", "return n * n", "return n + n"),
]


def review(rng: random.Random) -> Item:
    name, right, wrong = rng.choice(REVIEWS)
    buggy = rng.random() < 0.5
    args = {"last_item": "items", "is_even": "n", "average": "xs", "clamp": "x, lo, hi", "is_empty": "items", "square": "n"}[name]
    code = f"def {name}({args}):\n    {wrong if buggy else right}"
    question = ("Review this function. Reply with the single word BUG if it does not do what its name says, or OK if "
                f"it is correct, then one sentence of why.\n\n```python\n{code}\n```")
    return Item("review", say(question), lambda m: re.match(r"\W*(BUG|OK)\b", content(m).strip(), re.I) is not None
                and re.match(r"\W*(BUG|OK)\b", content(m).strip(), re.I).group(1).upper() == ("BUG" if buggy else "OK"))


MAKERS = {"reasoning": [arithmetic, ordering], "format": [json_only, bullets], "fidelity": [fidelity],
          "tools": [tool_call], "review": [review]}


def build(rng: random.Random, n: int, areas=AREAS) -> list[Item]:
    items = []
    for area in areas:
        for i in range(n):
            items.append(MAKERS[area][i % len(MAKERS[area])](rng))
    return items


# -- Scoring ------------------------------------------------------------------------------------------------


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson interval for passed/n, as fractions: how far a small sample can be trusted."""
    if n <= 0:
        return 0.0, 1.0
    p = passed / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def run(call: Callable[[list[dict], list | None], dict], n: int = 6, seed: int | None = None, areas=AREAS) -> dict:
    """The suite against `call(messages, tools) -> assistant message`. Returns the results to post to the Hall."""
    seed = int(time.time() // 86400) if seed is None else seed
    results = {a: {"passed": 0, "n": 0} for a in areas}
    errors = 0
    items = build(random.Random(seed), n, areas)
    for item in items:
        try:
            message = call(item.messages, item.tools)
        except Exception:  # down, rate limited, refused: not the model's mistake, and not counted
            errors += 1
            continue
        results[item.area]["n"] += 1
        if item.check(message if isinstance(message, dict) else {"content": str(message or "")}):
            results[item.area]["passed"] += 1
    if errors * 2 > len(items):
        raise BenchError(f"{errors} of {len(items)} items errored: the model isn't answering, so there's no score")
    return {"suite": SUITE, "seed": seed, "results": {a: r for a, r in results.items() if r["n"]}, "errors": errors}


def summary(report: dict) -> list[str]:
    lines = []
    for area, r in report["results"].items():
        lo, hi = wilson(r["passed"], r["n"])
        lines.append(f"  {area:<10} {r['passed']}/{r['n']}  ({100 * lo:.0f}-{100 * hi:.0f}% sure)")
    return lines


# -- The command line ------------------------------------------------------------------------------------------


def pool_url() -> str:
    try:
        import pool
        return pool.API
    except Exception:
        return os.environ.get("NANOBOREALIS_POOL_API", "http://127.0.0.1:52415")


def caller(route: str, model: str):
    import sources
    options = {"temperature": 0, "max_tokens": 800}

    def call(messages: list[dict], tools: list | None) -> dict:
        return sources.chat_message(route, model, messages, tools=tools, options=options, pool_url=pool_url(), timeout=900)
    return call


def hall(url: str, token: str, method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def option(args: list[str], name: str, default=None):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    action, args = argv[0], argv[1:]
    n, seed = int(option(args, "--n", 6)), option(args, "--seed")
    seed = int(seed) if seed is not None else None
    if action == "run" and args and not args[0].startswith("--"):
        report = run(caller(option(args, "--route", "pool"), args[0]), n, seed)
        print(json.dumps(report, indent=2) if "--json" in args else f"{args[0]} on {SUITE} (seed {report['seed']}):\n"
              + "\n".join(summary(report)))
    elif action == "seats":
        url = option(args, "--hall", os.environ.get("NANOBOREALIS_HALL_URL", "http://127.0.0.1:11437"))
        token_file = option(args, "--admin-token-file", "/var/lib/nanoborealis/hall/admin.token")
        token = Path(token_file).read_text().strip()
        for seat in hall(url, token, "GET", "/seats")["seats"]:
            if seat.get("chair") or seat.get("external"):
                continue  # not a model the pool serves
            print(f"{seat['name']} ({seat['model']}):")
            try:
                report = run(caller("pool", seat["model"]), n, seed)
            except BenchError as error:
                print(f"  skipped: {error}")
                continue
            print("\n".join(summary(report)))
            hall(url, token, "POST", "/benchmarks", {"model": seat["model"], **report})
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
