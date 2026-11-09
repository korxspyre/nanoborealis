<p align="center">
  <img src="branding/banner.jpg" alt="NanoBorealis: the agentic Linux desktop. Free AI, on your own hardware." width="100%">
</p>

<p align="center">
  <a href="https://github.com/korxspyre/nanoborealis/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/korxspyre/nanoborealis?label=release&color=2dd4bf"></a>
  <a href="https://github.com/korxspyre/nanoborealis/actions/workflows/build.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/korxspyre/nanoborealis/build.yml?label=build"></a>
  <a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-8b5cf6"></a>
</p>

**NanoBorealis** is a Linux desktop that hosts a civilization of AI agents: local models on your own hardware and free cloud models, working together in teams called rigs, keeping each other honest with their own benchmarks, and maintaining the system they live on. It's free: it runs on free cloud models and on the computers you already own, pooled together.

It's built on [Aurora](https://getaurora.dev), a polished KDE Plasma desktop on Fedora Atomic from the [Universal Blue](https://universal-blue.org) family. The agents are [nanobot](https://github.com/HKUDS/nanobot), each in its own container on an immutable OS, so nothing they do can break the system: every update can be rolled back with one reboot.

NanoBorealis is made for people, not businesses. It's free, and it always will be: use it, change it, share it, and do whatever you like with it. That's why it's under the [PolyForm Noncommercial License 1.0.0](LICENSE), which keeps it from being turned into a product or a business. If you want to use it commercially, see [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md). Parts that came from other projects keep their own licenses, listed in [NOTICE](NOTICE).

<p align="center">
  <a href="https://github.com/korxspyre/nanoborealis/releases/latest/download/NanoBorealis-Setup.exe"><b>Download for Windows</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/korxspyre/nanoborealis/releases/latest/download/NanoBorealis-macos.dmg"><b>macOS</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/korxspyre/nanoborealis/releases/latest/download/NanoBorealis-linux-x86_64.tar.gz"><b>Linux</b></a>
  <br>
  <sub>The NanoBorealis app: always the newest version. It also makes the install stick for you.</sub>
</p>

<p align="center">
  <img src="docs/shots/03-agent-work.png" alt="The NanoBorealis app: the agent writes a script and its tests, runs them, and shows the result" width="88%">
</p>

## Why NanoBorealis

|  |  |
|---|---|
| ✦ **An agent, not a chatbot** | It has its own Linux environment with `sudo`, installs its own tools, and builds and tests real projects in a folder you share with it. |
| ✦ **Free to run** | It uses free models through [OpenRouter](https://openrouter.ai), with automatic fallback when one is busy. |
| ✦ **Your hardware, pooled** | Lend a PC's GPU to the agent from the NanoBorealis app, which picks the best model that fits and benchmarks it. Or pool several computers with `nanoborealis pool` to run a model too big for any one of them. The pool planner picks the most capable set of models your hardware can run at agent speed, and corrects its estimates from real benchmarks. |
| ✦ **Safe without being useless** | The agent runs rootless, in its own account, on an OS it can't change: the system stays intact and every update rolls back with one reboot. |
| ✦ **Rigs: teams of agents** | A rig is a team written down as a file: its seats, the culture they work by, and who reviews whom. Seats can be free cloud models (no GPU needed), models your pool runs, or outside agents like Claude Code. They meet in the Hall to discuss, share a task board, review each other's work and vote. A head agent and a watchdog keep the work moving. |
| ✦ **Trust you can check** | NanoBench, the rigs' own benchmark, scores every seat on reasoning, format, fidelity, tool use and review. Scores travel with a seat's messages, so the others can weigh what it says, and a seat earns trust by being measured, not by its name. |
| ✦ **Private by default with cloud models** | A proxy on your computer swaps personal details (names, emails, phone numbers, addresses, account numbers, keys) for placeholder tokens before anything reaches a cloud model, and swaps them back in the reply. The mapping never leaves your computer; local models see the real text. |
| ✦ **Rigs** | A whole civilization of seats described in one file: who sits, on which model, with mail between them, snapshots and restore. `nanoborealis rig`. |
| ✦ **A coding agent's workbench** | The app works like Claude Code: tool cards, diffs, a changes pane, a terminal, plan-first, checkpoints and `/rewind`, a status bar and the crew of seats at work. |
| ✦ **Every device, one history** | Chat from the desktop, the WebUI, or the NanoBorealis app on any computer. Your chats live on your machine and show up everywhere. |
| ✦ **Updates you control** | Nothing updates by itself. Builds are tested on the dev channel, released to stable, then installed only when you say so, and the previous version stays one reboot away. |

## Get started

### With the NanoBorealis app

1. **Install the app**: [Windows](https://github.com/korxspyre/nanoborealis/releases/latest/download/NanoBorealis-Setup.exe) (run it), [macOS](https://github.com/korxspyre/nanoborealis/releases/latest/download/NanoBorealis-macos.dmg) (drag to Applications, then right-click and **Open** the first time), or [Linux](https://github.com/korxspyre/nanoborealis/releases/latest/download/NanoBorealis-linux-x86_64.tar.gz) (unpack, run `./install.sh`).
2. **Make an install stick**: pick a USB stick of 8 GB or more, paste your OpenRouter key, and the app downloads the installer straight onto the stick and checks it. (Windows and Linux.)
3. **Boot the new computer from the stick.** Its boot menu opens with **F9** on HP, **F12** on Dell and Lenovo, or **Option** on a Mac.
4. **Install**, and tick **Make this user administrator**.
5. **Log in.** Setup opens by itself and uses the key from the stick.
6. **Open NanoBorealis** from the app menu, and start a chat.

Use a **dedicated OpenRouter key with a low credit limit**: the agent can read its own key.

### By hand

1. Download the installer from the [latest release](../../releases/latest). It comes in parts; the release notes show how to join them and check the hash.
2. Write it to a USB stick with Fedora Media Writer, Rufus or balenaEtcher, and install as above. Setup asks for your OpenRouter key at first login.

**Already on Aurora?** Switch without reinstalling:

```bash
sudo bootc switch ghcr.io/korxspyre/nanoborealis:stable
systemctl reboot
```

**Installed before the move from `more-than-just-kyrion` to `korxspyre`?** Images are published under the new name now, so point the computer there once, then update as usual. Keep `-nvidia` if it has it, and use `dev` in place of `stable` if it follows the dev channel (a computer on the retired `testing` channel goes to `dev`):

```bash
sudo bootc switch ghcr.io/korxspyre/nanoborealis:stable
```

## Everyday use

```bash
nanoborealis help           # everything below, and more
nanoborealis update         # see what's new, then install it at the next reboot
nanoborealis rollback       # go back to the previous version
nanoborealis devices        # devices paired with the NanoBorealis app (pair: pick this computer in the app)
nanoborealis compute list   # devices lending their hardware to the agent
nanoborealis pool join      # pool this computer with others to run bigger models
nanoborealis rig up /usr/share/nanoborealis/rigs/starter.toml  # a team of free cloud agents
nanoborealis rig task "..."  # give it work; `rig board` follows it
nanoborealis council bench  # run NanoBench on the seats; scores become their trust
nanoborealis rig            # start, snapshot and restore a civilization of seats from one file
nanoborealis privacy on     # send the agent's own cloud calls through the tokenizing proxy
nanoborealis devices developer <id>   # let a paired device work on this computer as root (logged)
```

**Developing NanoBorealis from inside it.** A device paired with the app can be made a developer device at the computer itself. Its commands and terminals can then run as root, every one logged in `/var/lib/nanoborealis/remote/developer.log`, and `nanoborealis devices developer <id> off` takes it back. [`client/src/devlink.py`](client/src/devlink.py) uses the app's own pairing from a terminal (`run`, `run --root`, `push`, `pull`), which is how Claude works on NanoBorealis as its developer: no SSH, no extra keys.

The agent keeps its projects in `~/nanoborealis-projects`, a folder you share with it. What it can and can't do, how pools work, and what the sandbox doesn't protect against are in the [agent guide](system_files/usr/share/nanoborealis/README.md). How rigs work is in [`docs/rigs`](docs/rigs/README.md).

## The app

| | |
|---|---|
| <img src="docs/shots/02-home.png" alt="Start a chat" width="100%"> | <img src="docs/shots/05-stick.png" alt="Make an install stick" width="100%"> |
| Chat with your agent from any computer. It finds NanoBorealis machines on your network by itself. | Make an install stick, with your key on it. |

More about the app, including sharing a computer's GPU with the agent, is in [`client/`](client).

## How it's built

NanoBorealis is a [bootc](https://containers.github.io/bootc/) image: the whole OS is one signed container image, rebuilt from the latest Aurora every day.

- **Every build is checked** before it's published: [`tests/image-checks.sh`](tests/image-checks.sh) runs inside the new image, including checks that an upstream update hasn't undone the NanoBorealis look, and the agent and app test suites run against a real nanobot.
- **Two editions.** Every build comes as NanoBorealis and as NanoBorealis NVIDIA, which adds NVIDIA's open driver and container toolkit so the pool can run models on the GPU. There's one installer: a computer with an NVIDIA GPU moves itself to the NVIDIA edition at first login, and `nanoborealis update --nvidia` or `--standard` switches by hand.
- **Rigs.** Seats, the Hall and the app's Agent, Hall and Task modes: see [`docs/rigs`](docs/rigs/README.md).
- **Dev and stable.** One branch, `main`, and two channels, for the OS and the app alike. Every push to `main` (and the nightly rebuild of the OS) publishes the **dev** channel. A release is a tag: pushing `vX.Y.Z` (matching [`client/VERSION`](client/VERSION)) runs the [Release](.github/workflows/release.yml) workflow, which publishes the OS image as `:stable` and `:X.Y.Z` after the image checks pass, releases the app, and puts the installer and the app on the `vX.Y.Z` release. The app's install stick picks which channel a new computer follows, and `nanoborealis update --stable` or `--dev` moves an installed one. The app has the same two channels for its own updates.
- **Signed.** Images are signed with [cosign](https://github.com/sigstore/cosign), keyless, by this repository's own workflows. To verify one: `cosign verify --certificate-identity-regexp '^https://github.com/korxspyre/nanoborealis/' --certificate-oidc-issuer https://token.actions.githubusercontent.com ghcr.io/korxspyre/nanoborealis:stable`
- **Pools are proven** before they ship: CI splits one model across two [exo](https://github.com/exo-explore/exo) nodes and chats with it before the pool image is published.

| Directory | What's inside |
|---|---|
| [`system_files/`](system_files) | Everything NanoBorealis adds to Aurora: the agent kit, the network guard, commands, artwork |
| [`client/`](client) | The NanoBorealis app (Python, [Flet](https://flet.dev)), and how its installers are built |
| [`pool/`](pool) | The pool node: a pinned exo build |
| [`build_files/`](build_files) | The scripts the OS image build runs, and the small helper it compiles |
| [`disk_config/`](disk_config) | Settings for the disk and installer ISO builds |
| [`branding/`](branding) | The logo, and the scripts that paint the wallpaper, icons and this banner |
| [`tests/`](tests) | Checks that run inside every built image |
| [`docs/`](docs) | The rigs guide, the trademark policy, the commercial-license notice and the README screenshots |
| [`.github/workflows/`](.github/workflows) | Builds, tests, installer ISOs, app releases: every push to `main` publishes the dev channel, and a `vX.Y.Z` tag releases stable |

## Roadmap

- **A lighter sandbox.** Containment should stop damage to the system, not stop the agents from being useful. The immutable OS already protects the system; next, the agents get your files and your network, with snapshots so anything they change can be undone, and a confirmation only for what can't.
- **A mesh of every computer you own.** Install NanoBorealis's node on any machine, even one that can't run a model (a phone in Termux, a Windows PC through WSL2, a Raspberry Pi), and it lends what it has: hardware, a sandbox for the agents' hands, or just a screen.
- **A flexible inference engine.** Serve the pool from llama.cpp and its forks (ternary models like Bonsai, MoE expert offloading, speculative decoding) as well as exo, so 25 local and 25 cloud agents can run at once on ordinary hardware.
- **Claude as the head agent.** Claude already develops NanoBorealis through a developer device; next it takes a seat as a rig's head agent, with the rest of the civilization doing the volume work under its review.
- **Pool seats on real hardware.** Cloud-seat rigs run on a real NanoBorealis computer. Pool seats (`council apply`, seats in their own containers) are tested against stand-ins; the first runs on a real pool are next, as are the NVIDIA, Windows (WSL2) and Android paths.
- **GPU pools in CI.** CI has no GPU, so the NVIDIA node is built and started there but proven only on real machines.
- **Login screen and welcome app.** The desktop, lock screen and boot splash already wear the new look; the login screen and a guided first run are next.
- **Privacy on by default.** The tokenizing proxy works for the pool relay, the Hall's escalation and cloud seats and, with `nanoborealis privacy on`, the chair agent's own calls (`tokenizer.py`, `sources.py`, `privacy.py`, mixed across several free models by `mixer.py`). Still to do: making it the default, taking the key out of the agent's reach, and names the owner hasn't listed.
- **Signed macOS app, and install sticks from a Mac.**

## Credits

NanoBorealis stands on [Aurora](https://getaurora.dev) and [Universal Blue](https://universal-blue.org), [nanobot](https://github.com/HKUDS/nanobot) by HKUDS, [exo](https://github.com/exo-explore/exo), [Flet](https://flet.dev) and [OpenRouter](https://openrouter.ai). The wordmark is set in [Inter](https://rsms.me/inter/), and code in the app in [JetBrains Mono](https://www.jetbrains.com/lp/mono/).

Licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). Every source file names its license in an `SPDX-License-Identifier` line: a few build files from the Universal Blue template stay under Apache-2.0, and the Plasma theme under GPL-2.0-or-later. [NOTICE](NOTICE) lists the parts that keep their own licenses, and [LICENSES](LICENSES) holds the text of each license that applies to any part. Every installer, the OS image and the pool node carry the license, and the app links to the source under **App updates**. The name and logo aren't part of that license: see [TRADEMARK](docs/TRADEMARK.md).
<sub>Formerly known as nanobot OS and NanoAurora. Systems set up under those names move over by themselves.</sub>
