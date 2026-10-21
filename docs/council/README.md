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

**Escalation.** When a vote is split, a seat calls `escalate`, or a task fails review twice, the question goes to a free OpenRouter model (`nvidia/nemotron-3-ultra-550b-a55b:free` by default). Its answer is posted with its own nametape and counts as advice, not a vote. Requests are sent with `max_price` 0, so nothing billed can slip in. It uses the chair's OpenRouter key, which `council apply` and the app's key setting copy to the Hall.

**Autonomy.** *Only on tasks* (default): seats wake when you or the chair give them work. *Continuously*: every few minutes, when the pool isn't busy with your chats, the Hall also offers open board items to idle seats, most capable first.

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
