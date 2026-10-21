# NanoBorealis agent

This folder is the NanoBorealis agent kit. On NanoBorealis the setup opens by itself the first time an administrator logs in. To run it again:

```bash
bash /usr/share/nanoborealis/install.sh
```

Re-running is safe: it keeps the agent's OpenRouter key, WebUI password, and memory.

## What it sets up

- **`nanobot-agent`**, an account that can't log in, owns nothing else, and runs the agent
- **The agent:** nanobot 0.3.5 in a rootless Podman container that systemd runs through Quadlet. Its WebUI is at `http://127.0.0.1:8765`, behind a password
- **The NanoBorealis app, built in:** **NanoBorealis** in the app menu is the same app as on phones and PCs: a coding-agent workspace for the agent, and a control center for its models, chat channels, schedules, skills, memory and paired devices. On this computer it pairs by itself, with no PIN, for administrators signed in here (it asks the pairing service over `/run/nanoborealis/local.sock`, and the kernel says who is asking), and it updates with the OS
- **Pairing for your other devices:** the NanoBorealis app on a phone or PC pairs with a 6-digit PIN shown on this screen, then connects over TLS with a long password of its own (`nanoborealis-remote`, port 8766). A paired device can chat with the agent and open a terminal here as whoever approved it
- **Free Nemotron models with automatic fallback:** Ultra 550B, then Super 120B, then Lightning 30B. OpenRouter is told to refuse any request that would cost money, so a billed model can't slip in; the app's control center (Settings, "Free models only") turns that off if you want a paid model
- **Skills:** `code-review`, `debugging`, `git-workflow`, `new-project`, `planning`, `simplify`
- **`/srv/nanoborealis/projects`**, shared by you and the agent, with a shortcut at `~/nanoborealis-projects`

## What the agent can and can't do

| It can | It can't |
|---|---|
| Use `sudo` inside its container: install packages, compile, run services | Change the host operating system, or touch your files outside the projects folder |
| Keep tools it installs in its home; keep apt packages by listing them in `~/.config/nanoborealis/apt-packages` | Reach your local network. A root-owned firewall blocks it, though DNS still works |
| Reach the internet: OpenRouter, package mirrors, git hosts | Turn that firewall off, because it has no root on the host |

## How it's contained

| Layer | What it does |
|---|---|
| Dedicated `nanobot-agent` account | Owns nothing but the agent. Anything that escapes the container lands in an empty account, not yours |
| Rootless Podman | Root inside the container is an unprivileged user on the host |
| One shared folder | The only host path the agent sees is the projects folder |
| Network guard | `nanoborealis-firewall.service` loads at boot, before any user services start, and drops the agent's traffic to private, link-local, and CGNAT addresses. The only exceptions are this computer's own pool relay, which offers chat only, and, with a council, its Hall |
| SELinux | Enforcing, and the projects folder is labeled for container use |
| Loopback-only WebUI | The agent's own page listens on this machine only. Other devices reach it only after pairing: `nanoborealis-remote` shows a 6-digit PIN on this screen, then lets that device in over TLS with a long password of its own |
| Read-only OS | Aurora's system image can't be modified by anything the agent can reach |

## What it doesn't protect against

- **The agent can read its own OpenRouter key.** nanobot has to hold the key to call the API, and the agent's shell inherits it. Use a dedicated key with a low credit limit.
- **Internet access is unrestricted.** The firewall stops the local network, not the internet.
- **Free providers may log prompts.** Keep personal files and logged-in accounts off this machine.
- **Secrets aren't encrypted at rest.** Podman keeps them in a file only `nanobot-agent` and root can read.
- **A paired device is trusted like you.** Anyone who picks up a paired, unlocked phone or PC can chat with your agent, and open a terminal here as the person who approved that device (`sudo` still asks for their password). `nanoborealis devices remove <id>` unpairs a lost one. Pairing itself needs the PIN shown on this screen, so it can't happen without someone at this computer.
- **A pool trusts its network.** exo has no password, so once a computer joins a pool, anyone on the network can use the pool and pick what it serves, which makes its computers download models. The node runs in its own account and container, away from your files and the agent. Only join on networks you trust, or join with `--private <name>`.

## Day to day

| Command | Does |
|---|---|
| `nanoborealis update` | Check the build this computer follows for something newer, show what's new, and download it if you agree, along with app updates. It takes effect at the next reboot. `--stable` or `--dev` moves the computer to that build: stable is tested releases, dev is every build. (The old testing channel is gone; computers on it move to dev.) `--nvidia` moves it to the NVIDIA edition, with NVIDIA's driver so the pool can use its GPU (a computer with an NVIDIA GPU does this by itself at first login), and `--standard` back |
| `nanoborealis rollback` | Go back to the build you ran before the last update (then reboot) |
| `nanoborealis version` | Show which build is running, which one is staged, and which channel this system follows |
| `nanoborealis open` | Open the NanoBorealis app (`--web`: the WebUI in a browser instead) |
| `nanoborealis status` / `logs` | Show whether the agent is running, or follow its logs |
| `nanoborealis restart` | Restart the agent in a fresh container |
| `nanoborealis shell` | Open a shell inside the agent's container |
| `nanoborealis password` | Show the WebUI password, which the agent's page asks for once in this computer's browser. The NanoBorealis app never needs it, here or on other devices |
| `nanoborealis remote on` / `off` / `status` | Let the NanoBorealis app on your other devices pair with this computer (on by default), or keep the agent to this machine only. While it's on, this machine announces itself over mDNS so the app finds it |
| `nanoborealis devices` / `devices remove <id>` | List the devices paired with this computer, or unpair one |
| `nanoborealis pool join` / `serve` / `stop` / `status` / `logs` / `leave` | Pool this computer with others on your network so together they run models too big for any one of them. See [Pooling computers](#pooling-computers) |
| `nanoborealis pool nodes` / `plan` / `apply` / `drain` / `undrain` / `bench` / `scores` / `policy` | Run as many models on the pool at once as it can, each fast enough for an agent. See [A council of models](#a-council-of-models) |
| `nanoborealis council apply` / `status` / `autonomy` / `escalation` / `remove` | Seat one agent per model of the pool's loadout, meeting in the Hall. See [A council of models](#a-council-of-models) |
| `nanoborealis pool gpu` / `coordinate` | Show whether this computer would run the NVIDIA node or the processor one; run a pool coordinator without lending this computer |
| `nanoborealis set-key` | Swap the OpenRouter key |
| `nanoborealis rebuild [X.Y.Z]` | Rebuild the agent's container, optionally at a new nanobot version |
| `nanoborealis reset` | **Delete** the agent's home (memory, sessions, settings, tools) and start fresh. Projects are kept |
| `nanoborealis uninstall` | Remove the agent and its account. Projects are kept |

The earlier names `nanoaurora` and `nanobot-os` still work as well.

**The container is disposable; the home is not.** The agent's home directory, including config, memory, sessions, skills, and anything installed there, lives on the `nanobot-home` volume and survives restarts and rebuilds. Packages installed with apt disappear when the container is recreated, unless they're listed in `~/.config/nanoborealis/apt-packages`.

**The lock screen is a live dashboard.** While nobody is at the computer, the lock screen shows:
- the time and date;
- what the agent is doing right now (the request it's answering and from where, each step it takes);
- its recent chats with their latest messages;
- its scheduled tasks, with their last and next runs;
- a calendar that merges your own calendars with the agent's schedules;
- the paired devices, and what each connected one is doing (chatting with the agent, using a terminal);
- this computer's power, network, memory and disk;
- anything that needs attention, such as the OpenRouter key being refused or running on battery.

Move the mouse or press a key, and the usual unlock prompt comes back. Anyone who can see the screen can read the dashboard, including message previews. To add your calendars, put one per line in `~/.config/nanoborealis/calendars`: either an iCalendar link (Google Calendar's "Secret address in iCal format", a Nextcloud or Outlook calendar's ICS link) or the path of an `.ics` file. The dashboard reads them every minute. `nanoborealis-dashboard.service` gathers everything into `/run/nanoborealis-dashboard/dashboard.json`, which only root and administrators can read.

**Plugged in, the computer stays awake.** The agent answers chat apps, runs its schedules and takes paired devices' requests only while the computer is awake, so on mains power it doesn't go to sleep by itself, closing the lid only turns the screen off, and when idle the screen dims but stays on, showing the dashboard. On battery, KDE's usual behavior applies: it sleeps when idle or when the lid closes. Change either in System Settings → Power Management.

## Pooling computers

Every computer that runs `nanoborealis pool join` becomes a node of one pool, and their memory adds up: a model too big for any one of them is split into layers across several. The nodes run [exo](https://github.com/exo-explore/exo), pinned to a build that CI first proves by splitting a model across two nodes. Computers without NanoBorealis can join too, by running the same exo version.

```bash
nanoborealis pool join                                      # on every computer that lends its memory
nanoborealis pool serve mlx-community/Qwen3-30B-A3B-4bit    # on the computer with the agent
nanoborealis pool status                                    # the computers, their free memory, what's served
```

`serve` places the model where it fits, waits while each computer downloads its part, then makes it the agent's first choice. The cloud models stay as fallbacks, and `nanoborealis pool stop <model>` puts them first again. The agent reaches the pool through a relay on its own computer that needs a token and passes on chat requests only, so it can't make the pool download or delete anything. `leave` takes the computer out and keeps its downloaded models for next time; `leave --delete-models` removes them.

**NVIDIA GPUs.** `pool join` runs the NVIDIA build of the node (CUDA 13) on a computer whose GPU can: an RTX 20 series or newer, a driver from the 580 series on, and the NVIDIA Container Toolkit with its CDI spec (`sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`). exo then splits models by the GPU's memory rather than the computer's, keeping 1 GB for the driver, and the planner puts the models only a GPU runs fast on it. When something is missing, the node runs on the processor and `join` says what to fix; `nanoborealis pool gpu` shows the verdict before joining, and `join --cpu` leaves the GPU out. A laptop's GPU works too while it's plugged in; with 4 to 8 GB it takes a smaller part of a model. Computers without a GPU still help: they hold parts of big models, or run small ones. Another engine, such as a fork of exo, builds from [`pool/Containerfile`](https://github.com/korxspyre/nanoborealis/blob/main/pool/Containerfile) with `EXO_REPO` and `EXO_REF`.

## A council of models

A pool can run several models at once, each spread over the computers the planner picks. `plan` proposes the most capable set the pool can run with every model fast enough for an agent (by default 10 tokens a second writing and 150 reading), and `apply` makes the pool run it:

```bash
nanoborealis pool nodes          # the computers: GPU or processor, network, memory, what each holds
nanoborealis pool plan           # the proposed models, where each goes, how fast, and what would change
nanoborealis pool apply          # start them (and stop what the plan drops)
nanoborealis pool bench          # measure what runs; the planner learns from every measurement
nanoborealis pool bench --try mlx-community/Qwen3-30B-A3B-4bit    # try a model briefly, then stop it
nanoborealis pool drain laptop   # move the models off a computer so it can leave
```

- **How it decides.** exo splits a model's layers over its computers in proportion to their free memory, and the slowest computer in the chain sets the pace, so the planner keeps GPUs together for the models only they can run fast, and gives laptops small models or mixture-of-experts ones (large, but light per token, like Qwen3-30B-A3B). Each extra computer costs every token a network hop, Wi-Fi most of all.
- **Growing and shrinking.** `apply` keeps what already runs and fills the room around it, so a computer that joins adds models without restarting others; `apply --rebuild` plans everything afresh. `drain <computer>` moves only the models that used it; `undrain` lets the planner use it again.
- **What it knows.** Speeds start as estimates per kind of hardware and are corrected by every `bench` run, which also checks each model makes a correct tool call; a model that fails is left out. Capability comes from [LiveBench](https://livebench.ai), whose newest leaderboard the planner downloads every two weeks (`scores refresh` does it now), matched only to the same model version and size; otherwise it's a guess from the model's size, marked ≈. Scores in `~/.local/state/nanoborealis/pool/overrides.json` win over both.
- **Your settings.** `policy` shows and changes them: the speed floors, memory kept free on every computer (`headroom_gb`), how many models and computers per model, and models to pin or ban, e.g. `nanoborealis pool policy min_gen_tps=15 banned=mlx-community/Qwen3-4B-4bit`.

### Seats and the Hall

With a pool running, `nanoborealis council apply` goes one step further: each model of the loadout becomes a **seat**, a persistent agent with its own container, home and memory, and the agent you already have is the chair. The seats meet in the Hall (`nanoborealis-hall`, port 11437), where they share a floor and a task board, vote with weights from capability and earned trust, and ask a free OpenRouter model when stuck. In the app, every new chat picks **Agent**, **Hall** or **Task**. `council autonomy continuous` lets seats pick up open tasks by themselves; `council escalation off` stops them asking OpenRouter. Seats can reach the Hall and the pool relay but, like the chair, not your local network. The full design is in [`docs/council`](https://github.com/korxspyre/nanoborealis/blob/main/docs/council/README.md).

## Changing things

- **Models:** **Settings → Models** in the WebUI. Changes are saved in the agent's home.
- **nanobot version:** `nanoborealis rebuild 0.3.6`
- **Use the agent from another device:** open the [NanoBorealis app](https://github.com/korxspyre/nanoborealis/tree/main/client) there and pick this computer. It shows a 6-digit PIN here; typing it into the app pairs that device, and from then on it connects by itself, encrypted.
