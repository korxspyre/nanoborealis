# Claude Code parity: what the NanoBorealis client has, lacks and should build

Goal: the client (`client/`, Python + Flet) should offer the same features, UI and workflow as Claude Code (the CLI and the desktop Code tab), with two things Claude Code doesn't have: a **council of models** (Hall, seats, task board) and a **pool** of computers. One hard rule runs through every section: anything that reaches a cloud model goes through the reversible tokenization proxy (`system_files/usr/share/nanoborealis/tokenizer.py`, `sources.py`, used by `nanoborealis-relay`). No UI feature below may add a path around it.

Status: design only. No client code was changed.

## How this was checked

- **Claude Code facts** come from the public docs at `code.claude.com/docs/en/`, fetched on 2026-11-05: overview, permission-modes, checkpointing, desktop, hooks, statusline, sub-agents. For the long pages (desktop, hooks, permission-modes, sub-agents) the fetch tool read only the first ~100k characters of 120–290k, so details past that are unread.
- **Not verified against their own pages** (known only from the overview page's summary, so treat details as unconfirmed): skills frontmatter, MCP configuration, CLAUDE.md/auto-memory loading rules, session resume/branch, worktree internals, plugins, scheduled tasks, `/btw` behaviour beyond one line.
- **Client facts** come from reading `client/README.md` and the source files named below. **Agent facts** come from `system_files/usr/share/nanoborealis/README.md`, `docs/rigs/README.md` and the client's handling of nanobot events. I did not run nanobot 0.3.5, so what it can emit (for example approval events) is inferred from what the client handles, and is marked where it matters.

## 1. What the client is today (grounded)

| Area | Where | What it does |
|---|---|---|
| Connection | `agent_link.py` `AgentLink` (WebSocket envelopes `new_chat`, `attach`, `message`; REST `api_get`, `list_chats`, `load_thread`, `stop_turn`) | One socket per app; events come back as `delta`, `reasoning_delta`, `message`, `turn_end`, `goal_*`, `context_compaction`, `retry_status`, `turn_model_updated`, `session_updated`. |
| Main screen | `workspace.py` `Workspace` (`build`, `sidebar_column`, `composer_view`, `on_event`, `on_agent_message`, `render_history`) | Sidebar of chats, transcript, composer, side panel. Mode switch in `mode_switch()` (Agent / Hall / Task). |
| Transcript | `transcript.py` `user_message`, `Answer`, `Thinking`, `ToolCard`, `describe`, `diff_view`, `unified_view`, `goal_card`, `StatusLine`, `turn_footer`, `note`, `error` | Streaming Markdown, folded reasoning, one-line tool cards that open to output or diff, status line with elapsed time, per-answer footer (time, model, tokens). |
| Composer | `workspace.py` `composer_view`, `all_commands`, `mention`, `media`; `APP_COMMANDS` = `/model`, `/new` plus the agent's list from `/api/commands` (`load_commands`) | `/` palette, `@` file mention, attachments, project folder and "keep the agent inside it" (`project_scope`, `show_scope`), model picker (`model_picker.py`, `models_catalog.py`). |
| Side panel | `panels.py` `TerminalPanel`, `FilesPanel`, `ChangesPanel` (`parse`, `file_row`); `terminal.py` `Terminal`, `run_command` | Terminal on the computer, projects folder browser, per-repo git changes with diffs. |
| Control center | `control.py` `ControlCenter.page_view` and the page builders: Overview, Models, Channels, Schedules, Skills, Memory, Pool (`pool_nodes`, `pool_plan`, `pool_bench`, `pool_policy`, `fill_pool_job`), Council (~line 932), Devices, Logs, Settings. Backed by `admin.py` (`read_file`, `write_file`, `pool_*`, `hall`). | |
| Council | `council_view.py` `CouncilView` (`side_rows`, `seat_rows`, `room_view`, `follow_room`, `new_vote`, `task_view`, `follow_task`, `review`, `reopen`, `bubble`, `feedback`) | Hall rooms with nametaped bubbles and vote cards, task board with state badges and review/reopen, direct chats with a seat. |
| Pool | `poolnode.py` `Node`, `Lending`; `main.py` `show_pool_consent`, `show_pool_running` | This device lends hardware. The Pool control page shows nodes, plan, bench, policy. |
| Shell | `host.py` (single instance, notifications), `updater.py`, `pairing.py`, `discovery.py`, `theme.py` | |

Keys today (`Workspace.on_window_event`): Esc stop, Ctrl+N, Ctrl+B, Ctrl+J, Ctrl+E, Ctrl+K.

What the agent (nanobot in a rootless container) already does, as far as the client shows: tools `exec`, `read_file`, `write_file`, `edit_file`/`apply_patch`, `list_dir`, `grep`, `glob`, `web_search`, `web_fetch`, `spawn` (subagent), `cron`, `message`, `mcp_*` (these are the names `transcript.describe` renders); skills (`code-review`, `debugging`, `git-workflow`, `new-project`, `planning`, `simplify` are installed); memory files; schedules; `/goal`; automatic context compaction; model fallbacks; channels.

## 2. Feature map: Claude Code vs. NanoBorealis

"Agent can" means the nanobot side already has the capability; "Client has" means a UI exists today.

| # | Claude Code feature (source) | Client has | Agent can already | Missing | Notes on difference |
|---|---|---|---|---|---|
| 1 | **Transcript & streaming** (core) | Yes: `Answer`, `Thinking`, `ToolCard`, `StatusLine` | Yes | Message queueing indicator, per-turn collapsing, copy/regenerate actions, transcript search inside a chat | Closest to parity already. |
| 2 | **Tool-call cards** (desktop) | Yes: `describe` maps 11 tool names; edit diffs via `diff_view`; `unified_view` | Yes | Grouping of parallel calls, "Fetch"/"Search" result previews, per-tool icons for `mcp_*` servers, card for `spawn` that links to the subagent's work | |
| 3 | **Permission prompts** (desktop: diff you accept or reject in Manual mode; Allow once / Always allow / Deny) | **No.** `grep` finds no approval event in `workspace.py`, `transcript.py` or `agent_link.py` | The sandbox is the control: container, one shared folder, network guard (agent README, "How it's contained") | An approval round trip: agent pauses on a gated tool, the client shows a card with the diff or command, the user answers. **Unknown whether nanobot 0.3.5 can pause for approval.** If not, gate in the tool layer (agent image plugin, like the council tools) | The NanoBorealis trust model is "contain it" rather than "ask each time". Both belong: ask for what leaves the container (cloud call with new data class, `web_fetch` to new host, writes outside a repo). |
| 4 | **Permission modes**: `default` (Manual), `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`; Shift+Tab cycles; Desktop selector under the prompt box lists Manual, Accept edits, Plan, Auto, Bypass (permission-modes, desktop) | No | Only the project-folder "keep inside" switch (`project_scope`) | A mode chip under the composer, stored per chat. Needs #3 to mean anything. `auto` needs a classifier model, which `auto` in Claude Code is. A local council seat could play that role. | |
| 5 | **Plan mode**: Claude proposes, doesn't edit until you approve (permission-modes) | No | Skill `planning` exists; nothing enforces read-only | Enforced read-only turn (needs #3/#4), plan card with Approve / Edit / Reject, Plan pane | Cheap approximation first: a "plan first" composer toggle that prefixes the `planning` skill, plus an approve button that sends "go". |
| 6 | **Task list / Background tasks pane** (desktop "Plan" and "Background tasks": subagents, background shell commands, workflows) | Partial: `goal_card` (one goal); task board in `council_view.task_view` | `/goal`; `spawn`; Hall task board | A per-chat checklist card the agent updates; a "Background" pane listing subagents, long `exec` jobs and council tasks together | Council tasks are the natural source: see section 4. |
| 7 | **Diffs & file views** (desktop Changes pane with line comments; Files pane editable) | Yes: `ChangesPanel`, `FilesPanel`, `diff_view` | Yes | Inline line comments sent back to the agent; edit a file in the panel (admin has `write_file`, so the API exists); open file at line from a tool card; "review all changes" view per turn | |
| 8 | **Terminal pane** (Ctrl+`) | Yes: `TerminalPanel`, Ctrl+J. Line-by-line only; no full-screen programs | n/a | PTY emulation (`top`, `nano`); multiple tabs | |
| 9 | **Browser / preview pane** (dev servers, auto-verify, browser permission cards) | No | `web_fetch`, `web_search`; no browser tool known | Pane embedding the agent's dev server (Flet `WebView` on desktop/mobile where available), then a headless-browser tool in the agent image for "auto-verify" | Large; the container's network guard blocks the LAN, so previews must go through the remote service. |
| 10 | **Slash commands** | Yes: palette from `/api/commands` plus `/model`, `/new`; `/stop`, `/compact`, `/status`, `/help`, `/skill`, `/dream` handled | Yes | `/rewind`, `/context`, `/plan`, `/permissions`, `/memory`, `/agents`, `/hooks`, `/mcp`, `/btw`; arguments help; custom commands from files | |
| 11 | **Skills** | Control center Skills page (turn off, search hub, install) | Yes | Invoke from `/` as `/skill-name`; show "skill loaded" cards in the transcript; edit a skill | Skill frontmatter details unverified. |
| 12 | **Subagents** (Markdown files with `name`, `description`, `tools`, `model`, `permissionMode`, `isolation: worktree`, background/foreground; built-ins Explore, Plan, general-purpose) | Tool card "Subagent" for `spawn` only | `spawn` | Subagent definitions UI, live subagent child cards, foreground/background | **Council seats are the stronger version.** See section 4. |
| 13 | **Hooks** (events PreToolUse, PostToolUse, PermissionRequest, Stop, SessionStart, ..., types command/http/prompt/agent/mcp_tool; `settings.json`) | No | Unknown | A Hooks page and an agent-side hook runner | Mostly a nanobot feature; the client only edits config. Defer. |
| 14 | **MCP servers** | `mcp_*` tool cards render | Yes (nanobot) | Page to add/test/remove servers (like **Channels**, which already checks a connection before saving) | |
| 15 | **Memory** (CLAUDE.md, auto memory) | Control center Memory page edits the agent's files (`admin.read_file`/`write_file`) | Yes | Per-project instructions file (an `AGENTS.md`/`CLAUDE.md`-style file in the project folder), shown in the chat header with "loaded"; `#` quick-add; memory change cards when the agent writes one | |
| 16 | **Sessions** (sidebar, resume, archive, group/sort, split view, new window) | Yes: chats in `sidebar_column`, search, pin, rename, delete, day groups (`day_group`), cross-device sync | Yes | Archive, group by project, fork/branch a chat, split view of two chats, notification on finish exists (`host.notify`) | Cross-device sync is better than Claude Code's local sessions. |
| 17 | **Checkpoints & rewind** (every prompt snapshots code; `/rewind` or Esc Esc: restore code and conversation / conversation / code, summarize from here / up to here; 100 checkpoints; Bash-only changes untracked; subagent edits mostly untracked) | No | Compaction exists | Per-turn snapshot and a rewind menu. Proposal: a hidden git commit/ref per turn in the project's repo, taken through `terminal.run_command` before sending; "Restore code" = checkout of that tree. Conversation rewind needs a nanobot API to truncate a thread (unknown) | Same limits Claude documents apply to `exec`: use git as the real safety net. |
| 18 | **Worktrees** (Worktree toggle before a session; `.claude/worktrees/`) | No | `exec` can run git | A "Work in a separate copy" switch in the new-chat project dialog that runs `git worktree add` through `run_command` and sets the scope (`set_workspace_scope`) | Natural fit for council tasks: one worktree per claiming seat. |
| 19 | **Settings** | Control center Settings, Models, Overview | Yes | Per-project settings, permission rules list, theme, keymap editor | |
| 20 | **Status line** (script gets JSON: `model.display_name`, `context_window.used_percentage`, `cost.total_cost_usd`, `cost.total_lines_added/removed`, `rate_limits.*`, session id) | Partial: model chip, context fill (`show_context`), per-turn footer, status dot | Token and cost data from OpenRouter (Overview page) | One persistent footer bar: model, context %, session cost (the app has OpenRouter's count), lines +/−, git branch, **privacy state**, council/pool state. User-configurable segments | See section 3 for the privacy segment. |
| 21 | Side chat `/btw` (reads the main thread, adds nothing back; not saved) | `new_temporary_chat`/`discard_temporary_chat` already exist in `AgentLink` | Yes | UI entry (Ctrl+;) | Small. The primitives are there. |
| 22 | Scheduled tasks (desktop/cloud) | Schedules page | `cron` | Schedules shown as sessions with run history | |
| 23 | Remote control, channels | Paired devices, Channels page | Yes | Already stronger | |
| 24 | Computer use, Chrome integration, IDE extensions, GitHub/Slack integrations, Dispatch | No | No | Out of scope for the first slices | |

## 3. What cannot be copied because it depends on Claude

- **Claude's training for the tool loop.** Claude Code's prompts, tool schemas, compaction prompts and plan-mode behaviour are tuned to Claude. NanoBorealis runs OpenRouter free models and pool-served open models; plan mode, subagent delegation and `auto` mode classification will be less reliable. The pool bench already checks that a model makes a correct tool call (agent README, "What it knows"); treat that as a prerequisite for turning on any mode that depends on tool discipline.
- **`auto` permission mode.** Per the docs it runs "a classifier model reviewing actions in the background". A comparable classifier must be a model we run. If it runs in the cloud it must be tokenized like everything else, and a small local model is safer.
- **Anthropic-hosted features:** cloud sessions and scheduled routines on Anthropic infrastructure, Claude Code on the web, `--teleport`, the Claude mobile app handoff, Dispatch, the Slack/GitHub apps, `/code-review ultra`, claude.ai login and Anthropic plugin marketplace. The equivalents here are the user's own computer, paired devices and Channels.
- **Claude-specific model features:** extended thinking levels and effort controls, prompt caching, Claude's computer-use trained tiers. The client can show reasoning when a model emits it (`reasoning_delta`) but not tune it uniformly.
- **Exact behaviours of undocumented internals** (checkpoint file format, classifier prompts). We copy the user-visible workflow, not the implementation.

## 4. Council and pool: how multi-model work shows in the UI

Claude Code shows one agent (plus subagents of the same model). NanoBorealis has many models with identities, weights and trust. The UI should make that the headline, not a footnote.

1. **Nametapes everywhere.** `council_view.bubble` already tags Hall messages with the seat's name. Extend the same nametape to Agent-mode tool cards and `turn_footer` (it already has a model field) so every action says who did it and on which model/node.
2. **A Crew strip above the composer** (the status line's second row): the seats in this chat, each a chip with state (idle / working / waiting for review), trust, and the node it runs on. Clicking a chip opens that seat's work, like clicking a subagent in Claude Code's Background tasks pane.
3. **The task board is the Background pane.** Map Claude Code's "Background tasks" (subagents, long shell commands, workflows) onto: `spawn` calls, long `exec` jobs, and Hall tasks (`task_view` states open → claimed → in review → done/failed). One list, grouped by seat. The Task chat mode stays as the full view.
4. **Votes are the permission prompts of the council.** A vote card (`new_vote`) already exists in the Hall. Reuse its design for "ask the council before doing this": the card shows each seat's choice, reason and weight, the margin, and the escalation answer with its own nametape. Where Claude Code asks "Allow once / Always allow / Deny", the council can say "3 of 5 seats approve; the chair breaks nothing; you decide".
5. **Review as code review.** The two-rejection rule (`review_rejects_to_fail`) makes the reviewer seat a built-in second pair of eyes. In Changes, show "reviewed by Qwen3 14B: accepted" next to a diff, like the `/code-review` result in Claude Code.
6. **Worktree per claim.** When two seats work on the same repo (#18), each gets its own worktree; the Changes pane shows them side by side and merging is one reviewed action.
7. **Pool in the status line.** The status line segment shows pool health ("3 nodes, 2 models up, 1 draining") from `admin.pool_info`, and links to the existing Pool page. A slow pool should read as "waiting for node X" in the transcript status verb, not as a silent hang.
8. **Cost has two meters:** cloud spend (OpenRouter's own count; the app already reads it) and local compute (tokens served by the pool). Show both in the status line.

### Privacy must stay visible

- **Per-source indicator.** Every model the client names (chat model, seat, escalation model) carries a small badge: **local** (own pool), or **cloud · tokenized**. The relay knows `source.tokenizes` (relay lines ~93–101), so a read-only status endpoint on the remote service (outside this session's remit) can feed the badge.
- **Status line segment**: "Privacy: on, N values masked this chat". The count must come from the vault, never from the client guessing.
- **No bypass in the UI.** There is no "send raw" toggle. Anything the tokenizer refuses (`Unprotectable`: images, audio, files) shows as a refused-send note on the card (`transcript.error`), not as a retry that skips the proxy.
- **Known gaps to display honestly** (README roadmap): the chair's own OpenRouter calls aren't tokenized yet, and names are only masked when on the owner's list. Until closed, the badge for the chair's cloud model must read "cloud · not tokenized", not "protected".
- **Rewind, memory and logs** hold the real text on the computer; none of them may be exported to a cloud model except through the proxy. Rewind summaries ("Summarize from here") run on a model, so they inherit that model's badge.

## 5. Build order

Sizes: S ≈ 1–2 days, M ≈ 3–6 days, L ≈ 1–3 weeks of one developer. Dependencies in brackets.

| Order | Item | Size | Where | Why this order |
|---|---|---|---|---|
| 1 | **Status line bar** (model, context %, session cost, +/− lines, branch, privacy badge, pool/council state) | S–M | New `StatusBar` in `transcript.py`/`workspace.py`, reading `show_context`, `admin.usage`, `admin.pool_info`; privacy field stubbed until an endpoint exists | Visible change, all data nearly in hand, and it puts privacy on screen first. |
| 2 | **Permission-mode chip + approval cards** (Manual / Accept edits / Plan / Bypass-in-sandbox) | M–L | `composer_view`, new `ApprovalCard`; agent-side gate if nanobot can't pause [needs spike] | The defining Claude Code workflow. Spike first: can nanobot 0.3.5 pause a tool call for approval? |
| 3 | **Plan mode** (plan card, Approve/Reject) | S–M | Prefix via `planning` skill, then enforced read-only once #2 lands | Cheap version ships without #2. |
| 4 | **Checkpoints and `/rewind`** (git ref per turn; restore code; later restore conversation) | M–L | `workspace.send`, `terminal.run_command`, new rewind dialog | Safety net that makes auto-accept usable. Conversation truncation needs a nanobot API [spike]. |
| 5 | **Crew strip + Background pane** (seats, `spawn`, `exec` jobs, Hall tasks in one list) | M | `council_view.py`, `transcript.ToolCard` for `spawn` | The NanoBorealis differentiator. |
| 6 | **Task checklist card + `/` for skills + `/btw` side chat + more slash commands** | S each | `workspace.all_commands`, `AgentLink.new_temporary_chat` | Small wins. |
| 7 | **Worktree switch** and per-seat worktrees | M | project dialog in `workspace.show_scope` | Needs git; pairs with #5. |
| 8 | **Changes pane upgrades**: line comments, review-by-seat badge, edit-in-place | M | `ChangesPanel`, `FilesPanel` | |
| 9 | **MCP, Hooks and Subagent-definition pages** | M each | `control.py` | Config UIs, mostly nanobot features. |
| 10 | **Browser pane** with dev-server preview, then headless browser tool | L | `panels.py`, agent image | Large and network-guard sensitive. |

## 6. Prioritized first slice

Build next, in this order, as one small PR touching only `client/`:

1. **Status bar under the composer** (`Workspace`): model, context %, chat cost so far (from `admin.usage` delta), `+N −M` lines (from the turn's tool cards via `ToolCard.edit_counts`), git branch (via `terminal.run_command`), and a **privacy badge** per model (`local` / `cloud · tokenized` / `cloud · not tokenized`) driven by a table in the client for now (everything on `127.0.0.1` pool = local, OpenRouter = cloud · not tokenized for the chair until the roadmap item lands), then switched to the endpoint when it exists. Acceptance: shows on desktop and phone width, never claims "protected" on a path the roadmap lists as unfinished.
2. **Permission-mode chip with Plan** as the only enforced mode: a "Plan first" toggle that runs the next turn with the `planning` skill and shows a plan card with **Approve** (sends "go ahead") and **Revise**. No agent change needed.
3. **Crew strip** reading `CouncilView.seats` and the task list, shown when a council is present, with each chip opening the seat's room or task via the existing `view()` routes.
4. **Spike (not shipped):** write down whether nanobot 0.3.5 can pause for approval and truncate a thread, by reading its source. That decides the shape of #2 and #4 in section 5.

Out of this slice on purpose: approval cards, rewind and worktrees (they need the spike), and anything in the pool, relay, tokenizer or Hall files, which another session is editing.
