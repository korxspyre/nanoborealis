# The council

A pool can run several models at once. The council turns each of them into a **seat**: a persistent agent with its own home and memory. The seats meet in the **Hall**, where they share a floor and a task board, vote with weights they have earned, and ask a free OpenRouter model when they're stuck. The agent you already have is the **chair**.

The council runs on the computer that hosts your pool. You need a pool first (see the [agent guide](../../system_files/usr/share/nanoborealis/README.md#pooling-computers)).

```bash
nanoborealis pool apply                    # run the planner's loadout on the pool
nanoborealis council apply                 # one seat per model of the loadout
nanoborealis council status                # the seats, their weight and trust, and the settings
nanoborealis council autonomy continuous   # or on-task (the default)
nanoborealis council escalation off        # or on (the default)
nanoborealis council remove                # take the council down; the chair stays
```

`council apply` is safe to repeat. It seats new models, unseats models that left the loadout (their containers go, their memory stays in case the model comes back), and gives every seat a fresh Hall token.

## Parts

| Part | What it is | Where |
|---|---|---|
| **The Hall** | A small standard-library service with one SQLite database. It holds seats, rooms, messages, the task board, votes, the trust ledger and settings, and it runs the orchestrator. Listens on `127.0.0.1:11437`. | `system_files/usr/libexec/nanoborealis-hall` |
| **Seats** | One `nanobot serve` per model, each in its own rootless container with its own home. The model is reached through the pool relay (port 11436). The Hall is the only way into a seat: it holds a per-seat API key. | `system_files/usr/share/nanoborealis/council.py`, the seat branch of `image/entrypoint.sh` |
| **Council tools** | A `nanobot.tools` plugin installed in the agent image. The tools switch on only when the agent has a Hall token, so the single agent is unaffected. | `system_files/usr/share/nanoborealis/image/council/` |
| **Remote access** | `nanoborealis-remote` passes `/nanoborealis/hall/*` through to the Hall for admin devices. | `system_files/usr/libexec/nanoborealis-remote` |
| **The app** | A mode switch in the sidebar, the Hall and Task views, and the control center's **Council** page. | `client/src/council_view.py`, `client/src/control.py` |
| **The lock screen** | A Council panel on the live dashboard. | `system_files/usr/share/nanoborealis/lockscreen/` |

The network guard opens the Hall (port 11437) to the agent's seats, and refuses the relay and Hall ports from other computers.

## Chat modes

Every new chat in the app picks a mode:

- **Agent** is one-on-one with one agent. The chair is the default; any seat can be picked instead.
- **Hall** puts you and the whole council in one room. Every reply carries its seat's nametape, and the council settles a question by weighted vote.
- **Task** posts work to the board. The council claims it, works on it, reviews it and hands back a result, and the chat follows it to the end.

## How the Hall works

**Seats.** Each has an id, a nametape (a short name such as "Qwen3 14B"), the model id and family, a capability score, a trust value and a token of its own. Seats speak only as themselves.

**Hall chat.** Your message goes to the seats in turn, weightiest first. Each answers or passes. One worker handles one job at a time.

**Tasks.** A task is open, then claimed, in review, then done or failed. A different seat reviews the result, and a review can send it back. After two rejections (`review_rejects_to_fail`) the task fails and goes to escalation for advice.

**Votes.** A question in a room or on a task collects each seat's choice and reason. A seat's weight is its capability times its trust, normalised. The chair's ballot is optional. A vote won by less than `split_margin` (0.15 of the weight by default) counts as split and goes to escalation.

**Trust.** Every seat starts at 1 and moves with a ledger of events: your thumbs up or down, a task accepted or rejected, a peer review. It is bounded and decays toward 1 over time. Capability comes from the pool planner: LiveBench scores matched to the exact model version and size, or a size-based guess marked ≈.

**Benchmarks.** Trust has an objective half. `nanoborealis council bench` runs NanoBench (`nanobench.py`) on every seat's model through the pool: generated items in five areas (reasoning, format, fidelity, tools, review), each with one answer a program can check, so no model grades another. Fidelity asks a model to copy a privacy placeholder (`<<EMAIL_…>>`) character for character, which the council and the tokenizing proxy depend on; review asks it to tell a function with a planted bug from a correct one. Items come from a seed that changes daily, an item that errors isn't counted, and a run where half errored gives no score. Each score is shown with its 95% Wilson interval, and the Hall trusts the low end: a seat's trust is 1, plus what reviews and thumbs have earned (fading), plus a benchmark term from the low end of its overall score against a coin flip (about +0.3 for a perfect score over 30 items, about −0.1 for a coin flip, nothing under 10 items). Only the computer's own services can post results; a seat can't mark its own work. The scorecard is in every prompt a seat is given (Hall chat, open tasks, votes, reviews) and beside every message's author (`card`), so seats and readers can weigh what each says by what it is good at. This measures a model on these items on this computer; it is not comparable with public leaderboards, and nothing it runs executes model-written code.

**Escalation.** When a vote is split, a seat calls `escalate`, or a task fails review twice, the question goes to the free OpenRouter models. With `escalation_mix` on (the default) that is the mixer (`mixer.py`): up to three free models of different makes answer in parallel, the strongest combines their answers without knowing who wrote which, and a model of yet another make reviews the result, fixing it once if it finds it wrong. With it off, only the one model in `escalation_model` (`nvidia/nemotron-3-ultra-550b-a55b:free` by default) is asked. The answer is posted with its own nametape and counts as advice, not a vote. Requests are sent with `max_price` 0, so nothing billed can slip in. It uses the chair's OpenRouter key, which `council apply` and the app's key setting copy to the Hall.

**Privacy of escalation.** A free cloud model may log what it is sent, so every question goes out through the tokenizer (`tokenizer.py`): emails, keys, phone numbers, cards, addresses and the names on the owner's list become placeholders that only this computer can read, and the advice is restored here. If the tokenizer can't run, nothing is sent. Setting `NANOBOREALIS_MIX_SENSITIVITY=local-only` in the Hall's environment stops every escalation from leaving the computer. The free models are rate-limited, so the mixer keeps a ledger and rests one that fails; if none can answer, the room is told there's no advice.

**Autonomy.** *Only on tasks* (default): seats wake when you or the chair give them work. *Continuously*: every few minutes, when the pool isn't busy with your chats, the Hall also offers open board items to idle seats, most capable first.

## Rigs: a civilization of seats, as a file

A **rig** is a team of seats written in a TOML file (`rigs.py`; run `nanoborealis rig up team.toml`, with `--plan` to see what it would do first). It has a **culture** (norms every seat is told first), **pods** (groups with their own guidance), **seats** (a pool seat the Hall can ask, or an outside seat such as Claude's that joins over the API) and **edges** (`reviews`, `delegates`, `reports_to`). A seat's identity is its address, `build@starter`; the model or conversation behind it can change.

```toml
[rig]
name = "starter"
culture = "We make small, tested changes. We say when we are unsure."
[[pod]]
id = "dev"
guidance = "Builds and reviews the code."
[[seat]]
id = "build"
pod = "dev"
kind = "pool"
model = "mlx-community/Qwen3-14B-4bit"
[[seat]]
id = "claude"
pod = "dev"
kind = "outside"
head = 1          # first in line to be the head agent while it is here
[[edge]]
from = "claude"
to = "build"
kind = "reviews"  # claude reviews build's work
```

- **Mail.** Every seat has a durable inbox: `rig send build@starter "…"`, or to `rig:starter`, `pod:starter/dev`, `head`. A seat the Hall can ask is handed its mail with its culture, pod guidance and role first, and its answer comes back to the sender; an outside seat takes its mail from `GET /inbox` and acknowledges it. The **delivery guard** refuses a name that fits two seats (naming both) or none.
- **Review edges.** If an edge says who reviews whom, that seat reviews: asked directly if the Hall can ask it, otherwise mailed the review.
- **The head agent.** The first seat in line (`head = 1`, `2`, …) that is here and not worn out leads. A pool seat is always here; an outside seat is here while it sends a heartbeat (`POST /presence`, status `ok`), and steps aside when it says `limited` (its usage is spent), when the next in line takes over, and else the strongest seat the Hall can ask. The head is mailed when a task fails review or the council splits. Being head adds no voting weight: that is capability times trust, as for every seat.
- **Watchdog.** Every minute: mail a pool seat hasn't taken is tried again, and after three tries given up on and the head told; a task held and quiet for 30 minutes is nudged twice, then put back on the board. An outside seat's mail waits for it, however long.
- **Snapshot and restore.** `rig snapshot` captures the seats, pods, edges and the work in flight; `rig down` takes one first; `rig restore` brings it back and says, seat by seat, what really happened: *resumed* (it is there as it was), *fresh-primed* (made again with its open work, and for a pool seat its agent has to be started with `council apply`), *awaiting-decision* (its model isn't in the pool now: you choose), never a guess. A seat made again gets a new token; its old one stops working.

What isn't here yet: workflow scripts, adopting agents that are already running, and enforcing a seat's `policy` (it is stored and shown).

## Outside seats

A seat doesn't have to be a model the pool runs. An **outside seat** is an agent that joins over the Hall's API with a token of its own, the way Claude Code does. `council apply` makes one for Claude (`claude`, capability 50, which you can change), keeping its token readable by root only in `/etc/nanoborealis/seats/claude.token`. Make others with `nanoborealis council seat <id> [capability]`, which gives a fresh token each time.

- It speaks, claims and hands in tasks, reviews other seats' work and votes, with `hallseat.py` (`/usr/share/nanoborealis/hallseat.py`), or the API directly. Its vote counts at capability times the trust it earns, like any seat's.
- The Hall can't ask it, so a vote doesn't wait for it, and tasks aren't offered to it: it takes them from the board.
- **It sees the Hall tokenized.** An outside agent runs in someone else's cloud, so the Hall is the privacy boundary: every string it reads has personal details swapped for placeholders of its own (`<<EMAIL_...>>`), and what it writes back with those placeholders is restored before the council reads it. Without the tokenizer, outside seats aren't served at all.

## The seat tools

| Tool | Does |
|---|---|
| `hall_read`, `hall_post` | Read the floor; speak on it |
| `task_list`, `task_claim`, `task_update`, `task_submit` | Work the board |
| `task_review` | Review another seat's result |
| `vote` | Answer an open vote |
| `escalate` | Ask the free escalation model; the answer is logged on the floor |

## Where state lives

- The Hall: `/var/lib/nanoborealis/hall/hall.sqlite`.
- Seat files and homes: under the agent account's `council/` folder. Seat units are Quadlet files beside the agent's own.
- Pool state, on each computer in `~/.local/state/nanoborealis/pool/`: `bench.sqlite`, `policy.json`, `livebench.json`, and an optional `overrides.json`.

## Checks

| Check | Covers |
|---|---|
| `tests/hall_check.py` | The Hall's data model and API |
| `system_files/usr/share/nanoborealis/image/council/tests/council_check.py` | The tools, against a real Hall and nanobot |
| `tests/council_seats_check.py` | Seat planning and each seat's files |
| `tests/orchestrator_check.py` | Chat, tasks, votes, trust, escalation and autonomy, against stand-in seats and a stand-in OpenRouter |
| `pool/tests/loadout_check.py` | The pool planner, against a stand-in exo |

## Status

Built and checked against stand-ins. Not yet run on real hardware: `council apply` on a real pool, seats in real containers with the plugin, and the Hall behind the real firewall.
