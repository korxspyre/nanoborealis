# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks NanoBench: that its items are generated from a seed, that each grader accepts the right answer and
refuses the wrong ones, that errors aren't counted as failures, and that scores carry their uncertainty.

    python3 tests/nanobench_check.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system_files/usr/share/nanoborealis"))

import nanobench  # noqa: E402
from nanobench import BenchError, Item  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def text(item: Item) -> str:
    return item.messages[-1]["content"]


def msg(content: str = "", **extra) -> dict:
    return {"content": content, **extra}


# -- Items and graders ----------------------------------------------------------------------------------

same = [text(i) for i in nanobench.build(random.Random(5), 3)] == [text(i) for i in nanobench.build(random.Random(5), 3)]
other = [text(i) for i in nanobench.build(random.Random(6), 3)] != [text(i) for i in nanobench.build(random.Random(5), 3)]
check(same and other, "items come from a seed: the same seed gives the same items, another seed other ones")
check(len(nanobench.build(random.Random(1), 4)) == 4 * len(nanobench.AREAS), "n items in every area")

item = nanobench.arithmetic(random.Random(3))
a, b, c = map(int, re.search(r"has (\d+) boxes with (\d+) items each. It sells (\d+)", text(item)).groups())
right = a * b - c + b * b
check(item.check(msg(f"thinking...\nANSWER: {right}")) and item.check(msg(f"so\nANSWER: **{right}**"))
      and not item.check(msg(f"ANSWER: {right + 1}")) and not item.check(msg(f"it is {right}")),
      "arithmetic: the right number after ANSWER passes; a wrong one, or none, fails")

item = nanobench.ordering(random.Random(4))
left = set(re.findall(r"(\w+) is taller", text(item)))
shortest = next(n for n in re.findall(r"taller than (\w+)", text(item)) if n not in left)
wrong = next(n for n in left)
check(item.check(msg(f"ANSWER: {shortest}")) and not item.check(msg(f"ANSWER: {wrong}")), "ordering: the shortest name passes")

item = nanobench.json_only(random.Random(7))
word = re.search(r'the string "(\w+)"', text(item)).group(1)
n = int(re.search(r"the number (\d+) plus 7", text(item)).group(1))
tags = sorted(re.search(r"the words (\w+) and (\w+) in", text(item)).groups())
good = json.dumps({"name": word, "count": n + 7, "tags": tags})
check(item.check(msg(good)) and not item.check(msg("```json\n" + good + "\n```")) and not item.check(msg("Sure! " + good))
      and not item.check(msg(good.replace(str(n + 7), str(n)))), "format: strict JSON passes; fences, chatter and wrong values fail")

item = nanobench.bullets(random.Random(8))
k = int(re.search(r"exactly (\d+) bullet", text(item)).group(1))
w = re.search(r"the word '(\w+)'", text(item)).group(1)
lines = "\n".join(f"- a {w} thing {i}" for i in range(k))
check(item.check(msg(lines)) and not item.check(msg(lines + "\n- one more " + w)) and not item.check(msg(lines.replace(w, "x"))),
      "format: the exact count with the required word passes")

item = nanobench.fidelity(random.Random(9))
tokens = re.findall(r"<<[A-Z]+_[0-9a-f]{10}>>", text(item).split("Copy every")[0])
reply = f"Hello {tokens[0]}, {tokens[1]} will call {tokens[2]} tomorrow."
check(item.check(msg(reply)) and not item.check(msg(reply.replace(tokens[0], tokens[0].lower())))
      and not item.check(msg(reply.replace(tokens[1], tokens[1][:-3] + ">>"))) and not item.check(msg(reply + " cc <<EMAIL_0123456789>>"))
      and not item.check(msg(f"Hello {tokens[0]}, {tokens[1]} will call.")),
      "fidelity: placeholders copied exactly pass; changed, shortened, invented or missing ones fail")

for seed in range(20):  # both tools, whichever the seed picks
    item = nanobench.tool_call(random.Random(seed))
    q = text(item)
    if "plus" in q:
        x, y = map(int, re.search(r"is (\d+) plus (\d+)", q).groups())
        call = {"name": "add_numbers", "arguments": json.dumps({"a": x, "b": y})}
        bad = {"name": "add_numbers", "arguments": json.dumps({"a": x, "b": y + 1})}
    else:
        city = re.search(r"weather in (\w+)", q).group(1)
        call = {"name": "get_weather", "arguments": json.dumps({"city": city})}
        bad = {"name": "add_numbers", "arguments": json.dumps({"a": 1, "b": 2})}
    assert item.tools and item.check(msg("", tool_calls=[{"function": call}])), seed
    assert not item.check(msg("", tool_calls=[{"function": bad}])) and not item.check(msg("it is 4")), seed
check(True, "tools: the right call with the right arguments passes; wrong arguments, the wrong tool or no call fail")

for seed in range(20):
    item = nanobench.review(random.Random(seed))
    code = text(item).split("```python\n")[1].split("```")[0]
    name = re.search(r"def (\w+)", code).group(1)
    body = code.split("\n")[1].strip()
    entry = next(e for e in nanobench.REVIEWS if e[0] == name)
    buggy = body == entry[2]
    assert item.check(msg("BUG: wrong" if buggy else "OK, it is fine")), seed
    assert not item.check(msg("OK" if buggy else "BUG")), seed
check(True, "review: telling the planted bug from the correct function passes, the other way fails")

# -- Running and scoring -----------------------------------------------------------------------------------


def solver(messages, tools):
    """A model that gets everything right, by solving the prompts the way the tests above do."""
    q = messages[-1]["content"]
    if "boxes" in q:
        a, b, c = map(int, re.search(r"has (\d+) boxes with (\d+) items each. It sells (\d+)", q).groups())
        return msg(f"ANSWER: {a * b - c + b * b}")
    if "taller" in q:
        left = set(re.findall(r"(\w+) is taller", q))
        return msg("ANSWER: " + next(n for n in re.findall(r"taller than (\w+)", q) if n not in left))
    if "JSON" in q:
        word = re.search(r'the string "(\w+)"', q).group(1)
        n = int(re.search(r"the number (\d+) plus 7", q).group(1))
        return msg(json.dumps({"name": word, "count": n + 7, "tags": sorted(re.search(r"the words (\w+) and (\w+) in", q).groups())}))
    if "bullet" in q:
        k, w = int(re.search(r"exactly (\d+) bullet", q).group(1)), re.search(r"the word '(\w+)'", q).group(1)
        return msg("\n".join(f"- {w} {i}" for i in range(k)))
    if "placeholder" in q:
        t = re.findall(r"<<[A-Z]+_[0-9a-f]{10}>>", q.split("Copy every")[0])
        return msg(f"Hi {t[0]}. {t[1]} will call {t[2]}.")
    if tools:
        if "plus" in q:
            x, y = map(int, re.search(r"is (\d+) plus (\d+)", q).groups())
            return msg("", tool_calls=[{"function": {"name": "add_numbers", "arguments": json.dumps({"a": x, "b": y})}}])
        return msg("", tool_calls=[{"function": {"name": "get_weather", "arguments": json.dumps(
            {"city": re.search(r"weather in (\w+)", q).group(1)})}}])
    code = q.split("```python\n")[1].split("```")[0]
    entry = next(e for e in nanobench.REVIEWS if e[0] == re.search(r"def (\w+)", code).group(1))
    return msg("BUG" if code.split("\n")[1].strip() == entry[2] else "OK")


report = nanobench.run(solver, n=4, seed=11)
check(report["suite"] == nanobench.SUITE and set(report["results"]) == set(nanobench.AREAS)
      and all(r["passed"] == r["n"] == 4 for r in report["results"].values()), "a model that solves everything scores full marks in every area")
none = nanobench.run(lambda m, t: msg("no idea"), n=4, seed=11)
check(all(r["passed"] == 0 and r["n"] == 4 for r in none["results"].values()), "a model that answers nothing useful scores zero")

flip = iter(range(10**6))


def flaky(messages, tools):
    if next(flip) % 4 == 0:
        raise ConnectionError("down")
    return solver(messages, tools)


part = nanobench.run(flaky, n=4, seed=11)
total = sum(r["n"] for r in part["results"].values())
check(part["errors"] > 0 and total == 20 - part["errors"] and all(r["passed"] == r["n"] for r in part["results"].values()),
      "an item that errors is not counted, and is not a failure")


def down(messages, tools):
    raise ConnectionError("down")


try:
    nanobench.run(down, n=4, seed=11)
    refused = False
except BenchError:
    refused = True
check(refused, "a model that isn't answering gets no score, not a zero")

lo, hi = nanobench.wilson(30, 30)
lo6, _ = nanobench.wilson(6, 6)
check(0.88 < lo < 0.89 and hi == 1.0 and lo6 < lo and nanobench.wilson(0, 0) == (0.0, 1.0),
      "a perfect score over few items is trusted less than over many (95% Wilson interval)")
check(len(nanobench.summary(report)) == 5 and "sure" in nanobench.summary(report)[0], "the summary shows each area with how sure it is")

print("nanobench checks passed")
