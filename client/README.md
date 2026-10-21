# NanoBorealis client

The NanoBorealis app, for desktop and mobile: a coding-agent workspace for your agent, and a control center for it in the spirit of OpenClaw. Use it from the NanoBorealis computer itself (it comes with the OS), or from any other computer or phone.

Your chats live on the NanoBorealis computer, so they carry over everywhere: a chat started on the laptop, on your PC or on your phone shows up in all of them, and one open on two devices updates on both as the agent replies.

**The workspace**

- Chats in the sidebar, searchable, pinned ones first and the rest by day; rename, pin or delete one from its menu
- Replies stream in as Markdown; code blocks carry their language and a copy button
- The agent's thinking folds into "Thought for 8s", and each tool call is one line (**Run** `npm test`, **Edit** `app.py +12 −3`) that opens to the command's output or the change as a diff
- A status line while it works, with the time so far; **Stop** (or Esc) cancels a turn, and a message sent meanwhile joins it
- Under each answer: how long it took, the model, the tokens; beside the message box, how full the chat's context is
- The composer takes attachments (images, PDFs, documents, code), `/` for the agent's commands, `@` to mention a file from your projects, and the project folder the chat works in (optionally keeping the agent inside it)
- **Each chat picks its model**: search every model on OpenRouter; free ones are marked, paid ones show their price and ask before they're used
- A side panel with a terminal on the computer, its projects folder, and what changed there (per git repository, with diffs)
- Reconnects by itself; on phones the sidebar moves into a drawer
- Keys: Ctrl+N new chat, Ctrl+B sidebar, Ctrl+J terminal, Ctrl+E projects, Ctrl+K search, Esc stop

**Agent, Hall and Task modes.** With a [council](../docs/council/README.md) running, every new chat picks a mode: **Agent** (one agent, the chair or any seat), **Hall** (you and every seat in one room, with nametapes, vote cards and escalations marked) or **Task** (work posted to the board, followed through claims, reviews and the result). Without a council, chats are Agent chats.

**The control center**

- **Overview**: whether the agent is running (restart it from here), its model and fallbacks, and what OpenRouter has charged your key, by OpenRouter's own count
- **Models**: the default model and the ones it falls back to, in order; add any OpenRouter model
- **Channels**: talk to the agent from Telegram, Discord, WhatsApp, Slack, email, Signal, Matrix and more; each is set up here and its connection checked before it's saved
- **Schedules**: what the agent does by itself and when; turn one off, run it now, delete it, or ask for a new one
- **Skills**: the know-how it loads for a task; turn skills off, or search the skill hub and install more
- **Memory**: what it remembers, about you, its personality and standing instructions, as plain files you can edit
- **Pool**: the pool's computers (drain one, or let it back in), the proposed loadout and its changes, the bench table, and the policy
- **Council**: the seats with their capability, trust and state, and the autonomy and escalation switches
- **Devices**, **Logs** and **Settings** (its OpenRouter key, and "Free models only")

On the NanoBorealis computer itself the app is part of the desktop: KDE draws its window, it has its own icon in the dock, clicking the icon again brings the open window forward, and it says when the agent finishes while you're in another window.

**Free models only** is on by default: OpenRouter refuses any request that would cost money, whatever model is chosen. Picking a paid model asks first, and turns it off.

## Connect it to your agent

Pick your NanoBorealis computer in the app. The app finds computers on your network by itself: they announce themselves over mDNS, like printers, and where a router doesn't pass that on, the app also checks the other addresses of your home network for one. Or you can type an address. The computer shows a **6-digit PIN** on its screen, and typing it into the app pairs this device. That's all: no passwords to copy. On a NanoBorealis computer itself, the app comes with the OS and pairs by itself, with no PIN.

- **Encrypted.** Everything travels over TLS. While pairing, the app and the computer each pick a random number, and both work the PIN out from the two numbers and the computer's certificate. So a PIN that matches also proves to the app that nothing on the network is posing as your computer (the same numeric comparison Bluetooth pairing uses). From then on, the app trusts that computer's certificate and no other.
- **A long password per device.** Once the PIN checks out, the computer generates a long random password for this device and sends it over the encrypted connection. The app keeps it in its local storage and presents it every time. The computer keeps only a hash. Each device has its own password, so removing one doesn't affect the others.
- **Hard to guess.** A wrong PIN counts against the request (five tries), and the computer accepts only a few pairing requests a minute.
- **Found again when its address changes.** Routers hand out new addresses, for example after a computer sleeps for a day. When your computer stops answering at its address, the app looks for it by its certificate, first among the computers announcing themselves and then at the other addresses of its home network, and reconnects wherever it is. Another computer answering at the old address doesn't make the app forget the pairing.

On the computer, `nanoborealis devices` lists paired devices and `nanoborealis devices remove <id>` unpairs one. **Forget this computer** on the app's sign-in screen unpairs from the app side. Remote access is on by default; `nanoborealis remote off` turns it off, and `remote on` turns it back on.

## A terminal on the computer

The terminal button above the conversation (Ctrl+J) opens a console on the paired computer, over the same encrypted connection. It runs as the person who approved this device at the computer's screen (never as root), so `sudo` asks for that person's password. While that person is signed in at the computer, it runs in their own session there, just like a terminal they open themselves: the same files in `/tmp`, their containers, and settings that `sudo` can change. Type a command and press Enter. The buttons send what a text box can't: Ctrl+C stops what's running, Tab completes what you've typed, the arrows step through earlier commands. The key button hides what you type, for passwords. It's a line-by-line console: full-screen programs such as `top` or `nano` need a real terminal.

The PIN notification says that pairing allows this, so only approve devices you'd hand your keyboard to.

## Make an install stick

**Make an install stick** (on the sign-in screen and in the sidebar) turns a USB stick into a NanoBorealis installer:

1. Pick the stick. Only USB drives of 8 to 512 GB are offered, never the system disk or a large external drive.
2. Optionally paste your OpenRouter API key. It goes on the stick, and setup on the new computer uses it instead of asking.
3. Pick the **NanoBorealis build**: **Stable** (tested releases, recommended) or **Development** (every build, newest and least tested). Each build has its own installer, and the new computer keeps following the build you pick. If a build has no installer yet, the stick carries the stable one and the computer moves to your build at first login; the confirm screen says which installer you'll get.
4. Choose the latest release or an ISO you already have. The latest release streams straight onto the stick, with no copy on this computer.

Writing a whole disk needs administrator rights, so your system asks for permission once. The app writes the installer byte for byte, then the key block just past it, and reads everything back to check it. The installer image itself isn't modified, so its checksum and the installer's media check still pass. The key sits on the stick unencrypted, so treat the stick like a password.

## On an Android phone

Each release carries `NanoBorealis-<version>-android.apk`: open it on the phone and allow installing it from your browser. The phone pairs, chats, opens the computer's terminal and runs the control center like any computer; it finds NanoBorealis computers on the same Wi-Fi by itself. It doesn't write install sticks, lend its hardware or run a pool node. **App updates** opens the newest APK for Android's installer.

## Lend a device's hardware to the pool

**Share this device's hardware** in the sidebar lends the computer you're using to your NanoBorealis pool, through [exo](https://github.com/exo-explore/exo): it becomes one of the pool's computers, so the pool's models can be spread over it, and the planner on your NanoBorealis computer decides what runs where (`nanoborealis pool plan`, then `apply`). It asks first, on every device, and does nothing until you choose **Lend to the pool**.

Your NanoBorealis computer needs a pool first. `nanoborealis pool coordinate` runs it without lending the computer itself, for a small computer that's only there to coordinate; `nanoborealis pool join` also lends it, when it has the hardware to.

**Two consents, each for all of it.** The GPU and the processor are lent separately:

- **Lend the GPU:** a node that holds models in the GPU's memory, all of it but what the driver needs. The processor stays yours: lend just the GPU on a computer whose processor still has work to do, like one running your home.
- **Lend the processor:** a node that holds models in the computer's memory, using the whole processor.
- **Both:** two nodes side by side, the second on its own ports (52424 and 52425).

A Mac's GPU and processor share one memory, so a Mac lends one node. The app runs nodes of exactly the pool's exo version:

- **Linux with Podman:** the pool's own node image. With an NVIDIA GPU from the RTX 20 series on, a driver from the 580 series and the NVIDIA Container Toolkit, the NVIDIA build; otherwise only the processor can be lent, and the app says what's missing.
- **Mac with Apple silicon:** a node bundle published for the pool's exo version, checked against its SHA-256; it uses the Mac's GPU.
- **Linux without Podman:** the app asks you to install Podman (one package), which also brings the NVIDIA build.
- **Windows 11 (22H2 or newer):** the nodes run in WSL2, as Linux systems of their own made from the pool's node image, which can't see your drives or start your programs. The first time, Windows asks for administrator rights once: to install WSL if it isn't there (which may need a restart) and to let the pool's traffic into WSL. The app then turns on WSL's mirrored networking in your `.wslconfig` (keeping the rest of it), so the nodes share the network with your other computers; WSL restarts once for that. An NVIDIA GPU is used through WSL's own CUDA support, with a driver from the 580 series on.
- **Phones** don't run a node.

The nodes run while the app is open and start again when you open it; the app tells your NanoBorealis computer where they are, so it reaches the pool through them. **Stop lending** ends them, and **Stop and delete downloads** also removes the parts of models they downloaded (on Windows, the nodes' WSL systems). When your pool moves to a new exo version, the nodes follow it, downloading again what they need.

## Run it

**Install it:** download it from the [latest release](https://github.com/korxspyre/nanoborealis/releases/latest). No Python needed.

- **Windows:** run `NanoBorealis-Setup-….exe`. It installs for your user, with Start menu and desktop shortcuts, and uninstalls from Settings → Apps.
- **macOS:** open `NanoBorealis-…-macos.dmg` and drag NanoBorealis to Applications. It isn't notarized yet, so the first time, right-click it and choose **Open**.
- **Linux:** unpack `NanoBorealis-…-linux-x86_64.tar.gz` and run `./install.sh` (`./install.sh --remove` uninstalls).

**Updates:** the app checks for a newer version each time it starts, and offers it; **App updates** in the sidebar checks on demand. Turn on **Install new versions automatically** there to skip the question. It also picks the **update channel**:

| Channel | Versions | What you get |
|---|---|---|
| **Stable** (default) | `0.3.0` | Tested releases |
| **Development** | `0.3.1-dev.72` | A build of every change to the app, plus everything above |

Moving to a steadier channel never downgrades: the app switches over with that channel's next newer release. Either way, a download only installs after it matches the checksums published with the release. On Windows and Linux the app then restarts by itself; on macOS, drag the new app over the old one.

**From source:** on Windows double-click `run-windows.cmd`, on Linux and macOS run `./run.sh`. Both need Python 3.10 or newer, and set up a private environment on first run. Releases are frozen with PyInstaller by [`.github/workflows/client-release.yml`](../.github/workflows/client-release.yml).

**Phones:** build an app with Flet. It installs Flutter the first time, so this takes a while:

```bash
pip install "flet[all]==1.0.1"
flet build apk     # Android; `flet build ipa` for iPhone needs a Mac with Xcode
```

**From source:** `pip install "flet[desktop]==1.0.1" websockets`, then `python src/main.py`.

## How it works

`src/agent_link.py` handles the protocol and has no UI code:

1. `GET /webui/bootstrap` returns a one-time WebSocket token and a short-lived API token. Every request goes through the computer's remote-access service over TLS pinned to its certificate, with this device's password in `X-NanoBorealis-Device`; the service adds the WebUI's own password. `src/pairing.py` does the pairing.
2. The WebSocket carries typed JSON envelopes: `new_chat`, `attach`, and `message` go out, and `delta`, `reasoning_delta`, `message`, `turn_end` and the rest come back.
3. `GET /api/sessions` lists chats, and `GET /api/sessions/<key>/webui-thread` loads one.

`src/main.py` is the Flet interface on top.

## Testing without spending tokens

`dev/stub_llm.py` is a stand-in OpenAI-compatible model that streams canned Markdown, emits reasoning, and calls a tool when your message contains "tool". Point a real nanobot gateway at it, put the OS's remote-access service in front in test mode (it writes each PIN to a file instead of the screen), pair, then run the protocol checks:

```bash
pip install nanobot-ai==0.3.5 "websockets>=14"
python dev/stub_llm.py 18080 &
nanobot gateway --foreground --config dev/gateway-config.json &
NANOBOREALIS_REMOTE_PORT=18866 NANOBOREALIS_REMOTE_UPSTREAM_PORT=18765 NANOBOREALIS_REMOTE_STATE=/tmp/remote \
  NANOBOREALIS_REMOTE_TEST_SECRET=test-password NANOBOREALIS_REMOTE_TEST_PIN_FILE=/tmp/pin \
  python ../system_files/usr/libexec/nanoborealis-remote &
python dev/pairing_check.py 127.0.0.1:18866 /tmp/pin /tmp/paired.json
python dev/smoke.py /tmp/paired.json
```

`dev/pairing_check.py` pairs and checks what pairing turns away: wrong PINs, unknown devices, other certificates, floods. For pairing alone, `dev/stub_gateway.py` stands in for the gateway.

CI runs all of them on every change to the client or the remote-access service.

## License

PolyForm Noncommercial 1.0.0; see [LICENSE](../LICENSE) and [NOTICE](../NOTICE). The app links to its source under **App updates**.
