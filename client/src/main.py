# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""NanoBorealis: chat with your NanoBorealis agent from any device.

    python src/main.py                    # desktop window
    FLET_FORCE_WEB_SERVER=true FLET_SERVER_PORT=8550 python src/main.py   # serve to a browser
    flet build apk                        # Android (also ipa, windows, macos, linux)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from typing import Any

import flet as ft

import admin
import discovery
import host
import models_catalog
import pairing
import poolnode
import stickmaker
import theme
import updater
from agent_link import AgentLink, AuthError, LinkError, Unreachable, WrongComputer
from version import VERSION
from workspace import Workspace

NARROW = theme.NARROW
MONO = theme.MONO
PREF_ADDRESS = "nanoborealis.address"  # the last address typed on the sign-in screen
PREF_PASSWORD = "nanoborealis.password"  # before pairing: a WebUI password; cleared on start
PREF_MACHINES = "nanoborealis.machines"  # JSON: the paired computers (pairing.Machine)
PREF_LAST_MACHINE = "nanoborealis.last_machine"  # the one to connect to on start
PREF_CLIENT_ID = "nanoborealis.client_id"
PREF_COMPUTE = "nanoborealis.compute"  # JSON: consents to lend this device to the pool (pool_gpu, pool_cpu)
PREF_AUTO_UPDATE = "nanoborealis.auto_update"  # "1": install new app versions without asking
PREF_UPDATE_CHANNEL = "nanoborealis.update_channel"  # stable or dev
PREF_UPDATE_TRIED = "nanoborealis.update_tried"  # the version an automatic update last tried to install
# The NanoBorealis build an install stick sets up: the same two channels as the OS images.
STICK_BUILDS = [
    ("stable", "Stable: tested releases (recommended)"),
    ("dev", "Development: every build, newest and least tested"),
]
CHANNEL_NAMES = {
    "stable": ("Stable", "Tested releases. Recommended."),
    "dev": ("Development", "Built from every change. Newest features, occasionally broken."),
}


ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def logo(size: float) -> ft.Control:
    """The NanoBorealis star on its night-sky tile (rendered by branding/make-icons.py)."""
    return ft.Image(src="icon.png", width=size, height=size, fit=ft.BoxFit.CONTAIN)


def on(fn, *args):
    """An event handler that runs coroutine function fn(*args)."""

    async def handler(_event=None):
        await fn(*args)

    return handler


class NanoBorealisApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.prefs = ft.SharedPreferences()
        self.clipboard = ft.Clipboard()
        self.link: AgentLink | None = None
        self.client_id = ""
        self.address = ""
        self.machines: dict[str, pairing.Machine] = {}
        self.machine: pairing.Machine | None = None
        self.pairing: pairing.Pairing | None = None
        self.was_up = False
        self.in_chat_view = False
        self.workspace: Workspace | None = None  # the main screen, once connected
        self.catalog = models_catalog.Catalog()  # OpenRouter's models, for the model picker
        self.version = VERSION
        # Compute sharing: this device hosting a model for the agent, with the owner's consent.
        self.share: dict[str, Any] = {}
        self.share_busy = False
        # Or lending this device to the pool: a pool node here (poolnode.py).
        self.node: poolnode.Node | None = None
        self.url_launcher = ft.UrlLauncher()
        # A phone: it chats, pairs and manages the computer, but doesn't write install sticks, lend
        # its hardware, or run a pool node.
        self.mobile = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS) or updater.ANDROID
        # Install sticks: this app writes them too.
        self.file_picker = ft.FilePicker()
        self.sticks: list[stickmaker.Disk] = []
        self.stick_writing = False

    @staticmethod
    def logo(size: float) -> ft.Control:
        return logo(size)

    # -- Startup and connecting ------------------------------------------------

    async def start(self) -> None:
        p = self.page
        p.title = "NanoBorealis"
        p.fonts = {MONO: "fonts/JetBrainsMono.ttf"}
        if p.window is not None and os.path.exists(os.path.join(ASSETS, "icon.ico")):
            p.window.icon = os.path.join(ASSETS, "icon.ico")  # title bar and taskbar on Windows
        # NanoBorealis's own colors (the OS's color scheme), and on a NanoBorealis computer its font.
        p.theme_mode = ft.ThemeMode.DARK
        p.theme = p.dark_theme = theme.app_theme(host.system_font())
        p.bgcolor = theme.BG
        p.padding = 0
        p.spacing = 0
        p.on_resize = self.on_resize
        host.forget_window_shim()
        if host.is_host() and p.window is not None:
            p.window.maximized = True  # the computer's own agent window, like a full-screen app
        # Draw first: services such as SharedPreferences reach the device with the first
        # page update, and calling them before that waits for a listener that isn't there.
        self.show_connect()
        self.client_id = await self.pref_get(PREF_CLIENT_ID)
        if not self.client_id:
            self.client_id = f"nanoborealis-client-{uuid.uuid4().hex[:12]}"
            await self.pref_set(PREF_CLIENT_ID, self.client_id)
        await self.pref_set(PREF_PASSWORD, None)  # kept by versions before pairing; nothing uses it now
        self.address = await self.pref_get(PREF_ADDRESS)
        self.machines = await self.load_machines()
        self.page.run_task(self.check_for_update)
        self.page.run_task(self.clean_old_windows)
        dev = os.environ.get("NANOBOREALIS_DEV_MACHINE")  # development: start connected to a dev/remote.py pairing
        if dev:
            with open(dev) as f:
                machine = pairing.Machine.from_json(json.load(f))
            if machine is not None:
                self.machines[machine.key] = machine
                await self.connect(machine)
                return
        # On a NanoBorealis computer, the app is that computer's own window onto its agent: it
        # pairs with it by itself, no PIN, and opens straight into the chat.
        here = next((m for m in self.machines.values() if m.host == "127.0.0.1"), None)
        if here is None:
            try:
                here = await asyncio.to_thread(pairing.pair_here)
            except pairing.PairingError as e:
                print(f"nanoborealis-client: couldn't pair with this computer: {e}")
            if here is not None:
                self.machines[here.key] = here
                await self.save_machines()
        last = self.machines.get(await self.pref_get(PREF_LAST_MACHINE)) or here
        if last is not None:
            await self.connect(last)
        else:
            self.show_connect()

    # -- App updates -------------------------------------------------------------

    async def update_channel(self) -> str:
        """The owner's choice, or else the channel this build came from."""
        chosen = await self.pref_get(PREF_UPDATE_CHANNEL)
        if chosen == "testing":  # the retired middle channel: its candidates are dev builds now
            chosen = "dev"
        return chosen if chosen in updater.CHANNELS else updater.channel_of(VERSION)

    async def clean_old_windows(self) -> None:
        """Windows an earlier version left frozen on screen, then earlier versions' copies of Flet's
        window program (see updater.close_orphaned_windows and clean_old_windows)."""
        try:
            closed = await asyncio.to_thread(updater.close_orphaned_windows)
            if closed:
                print(f"nanoborealis-client: closed {closed} frozen window(s) an earlier version left behind")
            freed = await asyncio.to_thread(updater.clean_old_windows)
        except Exception as e:
            print(f"nanoborealis-client: couldn't clear earlier versions' window programs: {e}")
            return
        if freed:
            print(f"nanoborealis-client: cleared earlier versions' window programs ({freed / 1e6:.0f} MB)")

    async def check_for_update(self, quiet: bool = True) -> None:
        """Offer a newer app release, or install it straight away when auto-update is on."""
        if not updater.can_update():
            if not quiet:
                self.show_update_dialog(None, "stable", note="This copy runs from source; update it with git pull.")
            return
        channel = await self.update_channel()
        try:
            update = await asyncio.to_thread(updater.check, channel)
        except Exception as e:  # offline, rate-limited: try again next start
            if not quiet:
                self.show_update_dialog(None, channel, note=f"Couldn't check for updates: {e}")
            return
        if update is None:
            if not quiet:
                self.show_update_dialog(None, channel)
            return
        if await self.pref_get(PREF_AUTO_UPDATE) == "1":
            # Once per version: if installing it didn't take, don't try again at every start.
            if await self.pref_get(PREF_UPDATE_TRIED) == update.version:
                self.show_update_dialog(update, channel, note=f"Installing {update.version} didn't work last time. "
                                        f"Its log: {updater.install_log()}")
                return
            await self.pref_set(PREF_UPDATE_TRIED, update.version)
            await self.apply_update(update)
        else:
            self.show_update_dialog(update, channel)

    def show_update_dialog(self, update: updater.Update | None, channel: str, note: str = "") -> None:
        async def toggle(e) -> None:
            await self.pref_set(PREF_AUTO_UPDATE, "1" if e.control.value else "0")

        auto = ft.Switch(label="Install new versions automatically", value=False, on_change=toggle)

        async def load_switch() -> None:
            auto.value = await self.pref_get(PREF_AUTO_UPDATE) == "1"
            self.page.update()

        async def switch_channel(e) -> None:
            if e.control.value and e.control.value != channel:
                await self.pref_set(PREF_UPDATE_CHANNEL, e.control.value)
                self.page.pop_dialog()
                await self.check_for_update(quiet=False)  # look again on the new channel

        picker = ft.Dropdown(
            label="Update channel", value=channel, dense=True, on_select=switch_channel,
            options=[ft.DropdownOption(key=key, text=f"{name}: {about}") for key, (name, about) in CHANNEL_NAMES.items()],
        )

        if update is None:
            body = note or f"NanoBorealis {VERSION} is the newest version on the {CHANNEL_NAMES[channel][0]} channel."
            steadiness = {"dev": 0, "stable": 1}
            if not note and steadiness[updater.channel_of(VERSION)] < steadiness[channel]:
                # e.g. running a dev build with Stable chosen: nothing gets downgraded
                body += " You're running a newer build than it has, so you'll move over with its next release."
            actions = [ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())]
        else:
            body = f"NanoBorealis {update.version} is available. You have {VERSION}."
            actions = [ft.TextButton("Later", on_click=lambda _: self.page.pop_dialog()),
                       ft.FilledButton("Update", icon=ft.Icons.DOWNLOAD_ROUNDED, on_click=on(self.apply_update, update))]
        controls = [ft.Text(body, size=14)]
        if update is not None and update.notes_url:
            controls.append(ft.TextButton("What's new", icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                          on_click=lambda _: self.page.run_task(self.url_launcher.launch_url, update.notes_url)))
        if updater.can_update():
            controls.append(picker)
            controls.append(auto)
            controls.append(ft.Text("Updates come from this project's GitHub releases and are checked against "
                                    "their published checksums before they install.",
                                    size=12, color=ft.Colors.ON_SURFACE_VARIANT))
        # Whoever uses NanoBorealis can get the license and the source it's built from.
        source_url = f"https://github.com/{updater.REPO}"
        controls.append(ft.Text("NanoBorealis is under the PolyForm Noncommercial License 1.0.0. "
                                "It comes with no warranty.",
                                size=12, color=ft.Colors.ON_SURFACE_VARIANT))
        controls.append(ft.TextButton("Source code and license", icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                      on_click=lambda _: self.page.run_task(self.url_launcher.launch_url,
                                                                            f"{source_url}#readme")))
        self.page.show_dialog(ft.AlertDialog(
            modal=True, scrollable=True,
            title=ft.Text("App updates", size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(ft.Column(controls, tight=True, spacing=10), width=440),
            actions=actions,
        ))
        self.page.run_task(load_switch)

    async def apply_update(self, update: updater.Update) -> None:
        bar = ft.ProgressBar(value=0, width=400)
        status = ft.Text(f"Downloading NanoBorealis {update.version}...", size=13)
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Updating", size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(ft.Column([status, bar], tight=True, spacing=12), width=440),
        ))
        progress = [0, update.size]

        def report(done: int, total: int) -> None:
            progress[0], progress[1] = done, total or progress[1]

        if updater.ANDROID:
            # The app downloads the APK itself and hands it to Android's installer: handed to the
            # browser, the download ran in an in-app browser tab, which stalls on files this size.
            try:
                folder = await asyncio.to_thread(updater.android_folder)
            except Exception:  # no pyjnius: let Chrome itself (not an in-app tab) fetch it
                self.page.pop_dialog()
                await self.url_launcher.launch_url(update.asset_url, mode=ft.LaunchMode.EXTERNAL_APPLICATION)
                return
            task = asyncio.ensure_future(asyncio.to_thread(updater.download, update, report, folder))
        else:
            task = asyncio.ensure_future(asyncio.to_thread(updater.download, update, report))
        while not task.done():
            bar.value = progress[0] / progress[1] if progress[1] else None
            self.page.update()
            await asyncio.sleep(0.4)
        try:
            path = task.result()
        except Exception as e:
            status.value = f"The update didn't install: {e}"
            bar.visible = False
            self.page.update()
            return
        if updater.ANDROID:
            bar.visible = False
            try:
                opened = await asyncio.to_thread(updater.install_android, path)
            except Exception as e:
                status.value = f"Android's installer didn't open: {e}"
                self.page.update()
                return
            status.value = ("Android's installer is open: tap Update." if opened else
                            "Allow NanoBorealis to install apps in the settings that opened, "
                            "then come back and update again.")
            self.page.update()
            return
        status.value = "Installing. NanoBorealis restarts by itself." if sys.platform != "darwin" \
            else "Drag NanoBorealis to Applications in the window that opens, then reopen it."
        bar.value = None
        self.page.update()
        updater.install(path)
        if sys.platform != "darwin":
            # The installer replaces these files and reopens the app. Close the window through
            # Flet: it's Flet's own program (under ~/.flet), which the installer doesn't know to
            # close, and ending only this process would leave it on screen, frozen. Flet's run
            # loop then ends, and so does this process (see the end of this file).
            await asyncio.sleep(1)
            try:
                await self.page.window.destroy()
            except Exception:
                pass
            await asyncio.sleep(5)
            os._exit(0)

    async def pref_get(self, key: str) -> str:
        try:
            value = str(await self.prefs.get(key) or "")
            if not value and key.startswith("nanoborealis."):
                # Saved before the rename to NanoBorealis.
                value = str(await self.prefs.get("nanoaurora." + key.split(".", 1)[1]) or "")
            return value
        except Exception as e:  # storage is a convenience; never let it stop the app
            print(f"nanoborealis-client: could not read {key}: {e}")
            return ""

    async def pref_set(self, key: str, value: str | None) -> None:
        try:
            if value is None:
                await self.prefs.remove(key)
            else:
                await self.prefs.set(key, value)
        except Exception as e:
            print(f"nanoborealis-client: could not save {key}: {e}")

    # -- Pairing and connecting -------------------------------------------------
    # A NanoBorealis computer shows a 6-digit PIN when a device asks to pair; typing it here pairs
    # this device for good (pairing.py). After that the app connects over TLS pinned to that
    # computer's certificate, with a long password of this device's own. No password to copy.

    async def load_machines(self) -> dict[str, pairing.Machine]:
        try:
            rows = json.loads(await self.pref_get(PREF_MACHINES) or "[]")
        except ValueError:
            rows = []
        machines = {}
        for row in rows if isinstance(rows, list) else []:
            machine = pairing.Machine.from_json(row) if isinstance(row, dict) else None
            if machine is not None:
                machines[machine.key] = machine
        return machines

    async def save_machines(self) -> None:
        await self.pref_set(PREF_MACHINES, json.dumps([m.to_json() for m in self.machines.values()]))

    async def drop_machine(self, machine: pairing.Machine) -> None:
        self.machines.pop(machine.key, None)
        await self.save_machines()
        if await self.pref_get(PREF_LAST_MACHINE) == machine.key:
            await self.pref_set(PREF_LAST_MACHINE, None)

    def show_connect(self, error: str = "", busy: str = "") -> None:
        self.in_chat_view = False
        self.page.drawer = None
        self.address_field = ft.TextField(
            label="Or type its address", hint_text="192.168.1.20 or laptop.local", value=self.address,
            prefix_icon=ft.Icons.LINK_ROUNDED, on_submit=self.on_pair_address, expand=True, dense=True,
        )
        self.found_status = ft.Text("Looking for NanoBorealis on your network...", size=12,
                                    color=ft.Colors.ON_SURFACE_VARIANT)
        self.found_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self.connect_status = ft.Text(error or busy, color=ft.Colors.ERROR if error else ft.Colors.ON_SURFACE_VARIANT,
                                      size=13, visible=bool(error or busy), expand=True)
        self.connect_progress = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=bool(busy))
        mono = ft.TextStyle(font_family=MONO, weight=ft.FontWeight.W_600)
        help_text = ft.Text(
            size=12, color=ft.Colors.ON_SURFACE_VARIANT,
            spans=[
                ft.TextSpan("Pick your NanoBorealis computer: it shows a 6-digit PIN on its screen, and typing "
                            "it here pairs this device. Connections are encrypted. Not listed? Run "),
                ft.TextSpan("nanoborealis remote on", style=mono),
                ft.TextSpan(" there."),
            ],
        )
        paired = [self.machine_row(m) for m in self.machines.values()]
        self.connect_card = ft.Container(
            width=self.card_width(),
            padding=28,
            border_radius=20,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            content=ft.Column(
                tight=True, spacing=14,
                controls=[
                    ft.Row([logo(44), ft.Column([
                        ft.Text("NanoBorealis", size=24, weight=ft.FontWeight.W_600),
                        ft.Text("Connect to your agent", color=ft.Colors.ON_SURFACE_VARIANT),
                    ], spacing=0, tight=True)], spacing=14),
                    *([ft.Text("Your computers", size=12, color=ft.Colors.ON_SURFACE_VARIANT), *paired] if paired else []),
                    self.found_status,
                    self.found_row,
                    ft.Row([self.address_field,
                            ft.IconButton(ft.Icons.ARROW_FORWARD_ROUNDED, tooltip="Pair",
                                          on_click=self.on_pair_address)], spacing=4),
                    ft.Row([self.connect_progress, self.connect_status], spacing=10),
                    help_text,
                    *([] if self.mobile else [
                        ft.Divider(height=1),
                        ft.TextButton("No NanoBorealis computer yet? Make an install stick",
                                      icon=ft.Icons.USB_ROUNDED, on_click=on(self.open_stick_dialog))]),
                ],
            ),
        )
        self.show_screen(ft.Container(content=self.connect_card, alignment=ft.Alignment.CENTER, expand=True, padding=16))
        self.page.run_task(self.find_machines, self.found_row)

    def machine_row(self, machine: pairing.Machine) -> ft.Control:
        return ft.Container(
            border_radius=12, ink=True, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            padding=ft.Padding.only(left=14, right=4, top=6, bottom=6),
            on_click=on(self.connect, machine),
            content=ft.Row([
                ft.Icon(ft.Icons.COMPUTER_ROUNDED, color=ft.Colors.PRIMARY),
                ft.Column([
                    ft.Text(machine.name, weight=ft.FontWeight.W_600),
                    ft.Text(f"{machine.host} · paired", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ], spacing=0, tight=True, expand=True),
                ft.IconButton(ft.Icons.LINK_OFF_ROUNDED, icon_size=18, tooltip="Forget this computer",
                              on_click=on(self.forget_machine, machine)),
            ], spacing=12),
        )

    async def find_machines(self, row: ft.Row) -> None:
        """List NanoBorealis computers on this network: announcing themselves, or answering nearby."""
        found = await asyncio.to_thread(discovery.find, 3.0)
        if self.in_chat_view or row is not self.found_row:
            return  # the sign-in screen was replaced meanwhile
        paired_hosts = {m.host for m in self.machines.values()}
        new = [f for f in found if f.address not in paired_hosts]
        row.controls = [
            ft.OutlinedButton(f"{f.name} ({f.address})", icon=ft.Icons.COMPUTER_ROUNDED, on_click=on(self.use_found, f))
            for f in new
        ]
        row.controls.append(ft.TextButton("Search again", icon=ft.Icons.REFRESH_ROUNDED,
                                          on_click=on(self.search_again)))
        if new:
            self.found_status.value = "Found on your network (click to pair):"
        elif self.machines:
            self.found_status.value = "No other NanoBorealis computers on this network."
        else:
            self.found_status.value = "No NanoBorealis found on this network. Type its address below."
        self.page.update()

    async def search_again(self) -> None:
        self.found_status.value = "Looking for NanoBorealis on your network..."
        self.found_row.controls = []
        self.page.update()
        await self.find_machines(self.found_row)

    def card_width(self) -> float:
        return min(460, max(280, (self.page.width or 460) - 32))

    def set_connect_status(self, text: str, error: bool = False, busy: bool = False) -> None:
        self.connect_status.value = text
        self.connect_status.color = ft.Colors.ERROR if error else ft.Colors.ON_SURFACE_VARIANT
        self.connect_status.visible = bool(text)
        self.connect_progress.visible = busy
        self.page.update()

    async def use_found(self, found: discovery.Found) -> None:
        await self.pair_or_connect(found.address, found.port)

    async def on_pair_address(self, _event=None) -> None:
        raw = self.address_field.value or ""
        try:
            host, port = pairing.split_address(raw)
        except ValueError as e:
            self.set_connect_status(str(e), error=True)
            return
        self.address = raw.strip()
        await self.pref_set(PREF_ADDRESS, self.address)
        await self.pair_or_connect(host, port)

    async def pair_or_connect(self, host: str, port: int) -> None:
        self.set_connect_status(f"Reaching {host}...", busy=True)
        try:
            key = await asyncio.to_thread(pairing.probe_key, host, port)
        except pairing.PairingError as e:
            self.set_connect_status(str(e), error=True)
            return
        known = self.machines.get(key)
        if known is not None:  # paired before, and it has moved to this address
            known.host, known.port = host, port
            await self.save_machines()
            await self.connect(known)
            return
        self.pairing = pairing.Pairing(host, port)
        self.set_connect_status(f"Asking {host} to show a PIN...", busy=True)
        try:
            await asyncio.to_thread(self.pairing.start)
            await asyncio.to_thread(self.pairing.reveal)
        except pairing.PairingError as e:
            self.pairing = None
            self.set_connect_status(str(e), error=True)
            return
        self.show_pin_entry()

    def show_pin_entry(self, error: str = "") -> None:
        session = self.pairing
        if session is None:
            return
        self.pin_field = ft.TextField(
            label="PIN", hint_text="6 digits", max_length=6, keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True, text_size=26, text_align=ft.TextAlign.CENTER, width=240, on_submit=self.on_pin_submit,
        )
        self.pin_status = ft.Text(error, color=ft.Colors.ERROR, size=13, visible=bool(error))
        self.pin_progress = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)
        self.pin_button = ft.FilledButton("Pair", icon=ft.Icons.LINK_ROUNDED, height=44, on_click=self.on_pin_submit)
        card = ft.Container(
            width=self.card_width(), padding=28, border_radius=20, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            content=ft.Column(tight=True, spacing=16, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Icon(ft.Icons.PIN_ROUNDED, size=40, color=ft.Colors.PRIMARY),
                ft.Text(f"Pair with {session.machine}", size=20, weight=ft.FontWeight.W_600),
                ft.Text(f"A 6-digit PIN just appeared on {session.machine}'s screen. Type it here to pair "
                        f"this device.", color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.CENTER),
                self.pin_field,
                self.pin_status,
                ft.Row([ft.TextButton("Cancel", on_click=on(self.cancel_pairing)), self.pin_progress, self.pin_button],
                       alignment=ft.MainAxisAlignment.CENTER, spacing=12),
            ]),
        )
        self.show_screen(ft.Container(content=card, alignment=ft.Alignment.CENTER, expand=True, padding=16))
        self.page.update()

    async def on_pin_submit(self, _event=None) -> None:
        session = self.pairing
        pin = "".join(ch for ch in (self.pin_field.value or "") if ch.isdigit())
        if session is None:
            return
        if len(pin) != 6:
            self.pin_status.value = "The PIN has 6 digits."
            self.pin_status.visible = True
            self.page.update()
            return
        self.pin_button.disabled = True
        self.pin_progress.visible = True
        self.pin_status.visible = False
        self.page.update()
        try:
            machine = await asyncio.to_thread(session.finish, pin)
        except pairing.WrongPin as e:
            if "too many" in str(e).lower():
                self.pairing = None
                self.show_connect(str(e))
            else:
                self.show_pin_entry(str(e))
            return
        except pairing.PairingError as e:
            self.pairing = None
            self.show_connect(str(e))
            return
        self.pairing = None
        self.machines[machine.key] = machine
        await self.save_machines()
        await self.connect(machine)

    async def cancel_pairing(self) -> None:
        self.pairing = None
        self.show_connect()

    async def forget_machine(self, machine: pairing.Machine) -> None:
        await asyncio.to_thread(pairing.unpair, machine)  # best effort: it may be off
        await self.drop_machine(machine)
        self.show_connect(f"Forgot {machine.name}. Pick it again to pair.")

    async def connect(self, machine: pairing.Machine, looked: bool = False) -> None:
        self.show_connect(busy=f"Connecting to {machine.name}...")
        link = AgentLink(machine, self.on_event, client_id=self.client_id, locate=discovery.locate)
        try:
            await link.bootstrap()  # fail fast when it's off, or doesn't know this device anymore
        except (Unreachable, WrongComputer) as e:
            if machine.host in ("127.0.0.1", "::1", "localhost"):  # this computer: its service isn't up yet
                self.show_connect(f"{machine.name}: {e}")
                return
            # Routers hand out new addresses (after a long sleep, say): look for it by its certificate.
            if not looked:
                self.show_connect(busy=f"{machine.name} isn't at {machine.host}. Looking for it on this network...")
                address = await asyncio.to_thread(discovery.locate, machine)
                if address:
                    machine.host = address
                    await self.save_machines()
                    await self.connect(machine, looked=True)
                    return
            there = "Another computer answers" if isinstance(e, WrongComputer) else "Nothing answers"
            self.show_connect(f"{machine.name} wasn't found on this network ({there} at {machine.host}). Is it on "
                              f"and awake? If it has a new address, type it below.")
            return
        except AuthError as e:
            await self.drop_machine(machine)
            self.show_connect(f"{machine.name}: {e}")
            return
        except LinkError as e:
            self.show_connect(f"{machine.name}: {e}")
            return
        await self.pref_set(PREF_LAST_MACHINE, machine.key)
        self.machine = machine
        self.link = link
        self.was_up = False
        self.show_chat()
        self.page.run_task(self.keep_linked, link)
        if self.node is None:
            self.page.run_task(self.resume_sharing_on_start)

    async def keep_linked(self, link: AgentLink) -> None:
        try:
            await link.run()
        except AuthError as e:
            if self.link is link:
                self.link = None
                await self.drop_machine(link.machine)
                self.show_connect(f"{link.machine.name}: {e}")

    def show_chat(self) -> None:
        if self.workspace is not None:
            self.workspace.stop()
        self.in_chat_view = True
        self.workspace = Workspace(self)
        self.show_screen(self.workspace.build())
        self.page.update()

    async def on_event(self, event: dict[str, Any]) -> None:
        if event.get("event") == "moved":  # the link found the computer at a new address
            await self.save_machines()
        if self.workspace is not None and self.in_chat_view:
            await self.workspace.on_event(event)

    async def on_resize(self, _event=None) -> None:
        if self.workspace is not None and self.in_chat_view:
            await self.workspace.on_resize()
            return
        if getattr(self, "connect_card", None) is not None:
            self.connect_card.width = self.card_width()
        self.page.update()

    def show_screen(self, screen: ft.Control) -> None:
        """Put a screen on the page, clear of a phone's status bar at the top and its navigation bar at
        the bottom (SafeArea changes nothing on a computer, which has neither)."""
        self.page.controls.clear()
        self.page.add(ft.SafeArea(screen, expand=True))

    def refresh_sidebar(self) -> None:
        if self.workspace is not None and self.in_chat_view:
            self.workspace.refresh_sidebar()

    async def disconnect(self) -> None:
        link, self.link = self.link, None
        if link is not None:
            await link.close()
        if self.workspace is not None:
            self.workspace.stop()
            self.workspace = None
        await self.pref_set(PREF_LAST_MACHINE, None)  # don't connect by itself next time
        self.show_connect()

    # -- Compute sharing -----------------------------------------------------------

    def share_summary(self) -> str:
        if self.node is not None and self.node.how is not None:
            return "Lent to the pool"
        if self.share.get("pool_consent"):
            return "Paused. Open to resume."
        return "Lend this GPU and CPU to the pool"


    async def load_share(self) -> None:
        raw = await self.pref_get(PREF_COMPUTE)
        try:
            self.share = json.loads(raw) if raw else {}
        except ValueError:
            self.share = {}

    async def save_share(self) -> None:
        await self.pref_set(PREF_COMPUTE, json.dumps(self.share))

    async def resume_sharing_on_start(self) -> None:
        """Share again after a restart, but only if the owner already allowed it on this device."""
        await self.load_share()
        if self.share.get("consent"):
            # Consent given by earlier versions, which shared one model through Ollama: sharing is
            # lending to the pool through exo now, and that consent carries over to it.
            self.share["pool_consent"] = True
            for old in ("consent", "served", "tag", "token", "context", "gen_tps", "prompt_tps"):
                self.share.pop(old, None)
            await self.save_share()
        if self.share.get("pool_consent") and "pool_gpu" not in self.share and "pool_cpu" not in self.share:
            # A consent from before the GPU and the processor were asked for separately meant all of it.
            self.share["pool_gpu"] = self.share["pool_cpu"] = True
            await self.save_share()
        if self.share.get("pool_consent"):
            info = await self.pool_info()
            chosen = await asyncio.to_thread(poolnode.plan)
            if info is not None and chosen.how in ("container", "bundle", "wsl"):
                try:
                    await self.start_node(info, chosen)
                except poolnode.NeedsSetup as e:  # consent given, one Windows step to go: ask for it
                    await self.open_share_dialog()
                    self.show_windows_setup(info, chosen, e.install_wsl)
                except poolnode.NodeError as e:
                    print(f"nanoborealis-client: couldn't lend this device to the pool again: {e}")
            self.refresh_sidebar()
            return
        self.refresh_sidebar()

    def set_share_body(self, controls: list[ft.Control], actions: list[ft.Control]) -> None:
        self.share_body.controls = controls
        self.share_actions.controls = actions
        self.page.update()

    @staticmethod
    def bullet(text: str) -> ft.Control:
        return ft.Row([ft.Text("•", size=13), ft.Text(text, size=13, expand=True)],
                      spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)

    @staticmethod
    def working(text: str) -> ft.Control:
        return ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2), ft.Text(text, size=13)], spacing=10)

    async def open_share_dialog(self) -> None:
        if self.page.width and self.page.width < NARROW:
            await self.page.close_drawer()
        self.share_body = ft.Column(tight=True, spacing=12)
        self.share_actions = ft.Row(spacing=8, wrap=True, alignment=ft.MainAxisAlignment.END)
        self.share_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Share this device's hardware", size=18, weight=ft.FontWeight.W_600),
            scrollable=True,
            content=ft.Container(self.share_body, width=500),
            actions=[self.share_actions],
        )
        self.page.show_dialog(self.share_dialog)
        if self.node is not None and self.node.how is not None:
            self.show_pool_running()
        elif self.share_busy:
            self.set_share_body([self.working("Starting the pool node; this keeps going in the background.")],
                                [ft.TextButton("Hide", on_click=on(self.close_share_dialog))])
        else:
            await self.show_share_consent()

    async def close_share_dialog(self) -> None:
        self.page.pop_dialog()

    async def pool_info(self) -> poolnode.PoolInfo | None:
        """The pool the paired computer is in, if any, and if it lets this device ask."""
        if self.machine is None:
            return None
        try:
            return poolnode.PoolInfo.from_answer(await asyncio.to_thread(admin.pool_info, self.machine))
        except (pairing.PairingError, OSError, ValueError):
            return None

    async def show_share_consent(self) -> None:
        """Lend this device to the pool, through exo: when the computer has a pool and this device can
        run a node. Otherwise, say what's missing."""
        self.set_share_body([self.working("Checking this device...")], [])
        info = await self.pool_info()
        chosen = await asyncio.to_thread(poolnode.plan)
        if info is not None and chosen.how in ("container", "bundle", "wsl"):
            return self.show_pool_consent(info, chosen)
        reason = (chosen.reason if info is not None else
                  "Your NanoBorealis computer isn't running a pool yet. On it, run: nanoborealis pool coordinate "
                  "(or nanoborealis pool join, if it has the hardware to lend too). Then come back here.")
        self.set_share_body([ft.Text(reason, size=14)], [ft.FilledButton("Close", on_click=on(self.close_share_dialog))])

    def show_pool_consent(self, info: poolnode.PoolInfo, chosen: poolnode.Plan) -> None:
        gpu = chosen.gpu is not None and chosen.gpu.variant == "cuda13"
        mac = chosen.bundle == "macos-arm64"
        # Two consents, each for the whole of that part: the pool may use all of it, as hard as it likes.
        # Lend just the GPU on a computer whose processor you still need, and the other way round.
        gpu_switch = ft.Switch(value=bool(self.share.get("pool_gpu", True)) and (gpu or mac), disabled=not (gpu or mac),
                               label=("Lend the GPU and its memory: all of it" if gpu else
                                      "Lend this Mac: its GPU and memory, all of it" if mac else
                                      "No GPU here the pool can use"))
        cpu_switch = ft.Switch(value=bool(self.share.get("pool_cpu", True)) and not mac, disabled=mac,
                               label="Lend the processor and the computer's memory: all of it" if not mac else
                                     "(a Mac's processor and GPU share one memory: lent together)")
        self.set_share_body([
            ft.Text("Lend this device to your NanoBorealis pool, so the pool's models can be spread over its "
                    "memory" + (" and its GPU." if gpu or mac else "."), size=14),
            gpu_switch, cpu_switch,
            self.bullet("What you lend, the pool uses fully: leave out the part this computer still needs for "
                        "its own work, like a processor serving your home while its GPU sits idle."),
            self.bullet(chosen.reason),
            self.bullet(f"It runs a pool node of the pool's own exo version ({info.ref}). The planner on your "
                        "NanoBorealis computer decides which models, or parts of them, run here."),
            self.bullet("The first start downloads the node" + (" (about 8 GB for the NVIDIA build)" if gpu else "")
                        + ", and later the parts of models placed here."),
            self.bullet(f"While this app is open the node uses ports {poolnode.PORTS} on your network; your system "
                        "may ask to allow that. Only lend on networks you trust."),
            *([self.bullet("On Windows it runs in WSL2, as a Linux system of its own that can't see your drives or "
                           "start your programs. The first time, Windows asks for administrator rights once, to "
                           "install WSL if needed and let the pool's traffic reach it; installing WSL may need a "
                           "restart. WSL also restarts once to share the network, which stops any Linux you have "
                           "running in it.")] if chosen.how == "wsl" else []),
            self.bullet("Stop lending at any time, and delete what it downloaded if you like."),
        ], [
            ft.TextButton("Not now", on_click=on(self.close_share_dialog)),
            ft.FilledButton("Lend to the pool", icon=ft.Icons.HUB_ROUNDED,
                            on_click=lambda _e: self.page.run_task(self.allow_pool, info, chosen,
                                                                    bool(gpu_switch.value), bool(cpu_switch.value))),
        ])

    async def start_node(self, info: poolnode.PoolInfo, chosen: poolnode.Plan,
                         progress=lambda _text: None) -> None:
        lending = poolnode.Lending(info, poolnode.default_home())
        await asyncio.to_thread(lending.start, chosen, bool(self.share.get("pool_gpu")),
                                bool(self.share.get("pool_cpu")), progress)
        self.node = lending
        await self.check_in_to_pool(True)

    async def check_in_to_pool(self, lending: bool) -> None:
        """Tell the NanoBorealis computer where this device's node is, so a computer that coordinates
        the pool without a node of its own can reach the pool through it. Older computers don't ask."""
        if self.machine is None:
            return
        try:
            ports = self.node.api_ports if lending and self.node is not None else []
            await asyncio.to_thread(admin.pool_lend, self.machine, lending, ports)
        except pairing.PairingError:
            pass

    async def allow_pool(self, info: poolnode.PoolInfo, chosen: poolnode.Plan, gpu: bool | None = None,
                         cpu: bool | None = None) -> None:
        """Lend what the owner allowed: the GPU, the processor, or both (None: as they chose before)."""
        if gpu is not None:
            self.share["pool_gpu"] = gpu
        if cpu is not None:
            self.share["pool_cpu"] = cpu
        self.share["pool_consent"] = bool(self.share.get("pool_gpu") or self.share.get("pool_cpu"))
        await self.save_share()
        if not self.share["pool_consent"]:
            return self.set_share_body([ft.Text("Nothing is lent: allow the GPU, the processor, or both.", size=13)],
                                       [ft.TextButton("Back", on_click=on(self.show_share_consent))])
        status = ft.Text("Starting the pool node...", size=13)
        self.set_share_body([ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2), status], spacing=10)],
                            [ft.TextButton("Hide", on_click=on(self.close_share_dialog))])
        loop = asyncio.get_running_loop()

        def progress(text: str) -> None:  # runs on the worker thread
            def apply() -> None:
                status.value = text
                try:
                    self.page.update()
                except Exception:
                    pass
            loop.call_soon_threadsafe(apply)

        self.share_busy = True
        try:
            await self.start_node(info, chosen, progress)
            for _ in range(60):  # the nodes answer once they're up
                if await asyncio.to_thread(self.node.answers):
                    break
                await asyncio.sleep(2)
        except poolnode.NeedsSetup as e:
            self.share_busy = False
            return self.show_windows_setup(info, chosen, e.install_wsl)
        except poolnode.NodeError as e:
            self.share["pool_consent"] = False
            await self.save_share()
            self.set_share_body([ft.Text(str(e), size=13, color=ft.Colors.ERROR)],
                                [ft.TextButton("Close", on_click=on(self.close_share_dialog))])
            return
        finally:
            self.share_busy = False
        self.refresh_sidebar()
        self.show_pool_running()

    def show_windows_setup(self, info: poolnode.PoolInfo, chosen: poolnode.Plan, install_wsl: bool) -> None:
        self.set_share_body([
            ft.Text("Windows needs a one-time step with administrator rights:", size=14),
            *([self.bullet("Install WSL, Windows' own way of running Linux (no Linux system of its own yet).")]
              if install_wsl else []),
            self.bullet("Let the pool's traffic into WSL, through the firewall Windows keeps for it."),
            ft.Text("Windows asks you to allow it.", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ], [
            ft.TextButton("Not now", on_click=on(self.close_share_dialog)),
            ft.FilledButton("Continue", icon=ft.Icons.ADMIN_PANEL_SETTINGS_ROUNDED,
                            on_click=on(self.run_windows_setup, info, chosen, install_wsl)),
        ])

    async def run_windows_setup(self, info: poolnode.PoolInfo, chosen: poolnode.Plan, install_wsl: bool) -> None:
        self.set_share_body([self.working("Waiting for Windows...")], [])
        if not await asyncio.to_thread(poolnode.run_admin_setup, install_wsl):
            return self.set_share_body([ft.Text("Windows didn't allow it, so nothing changed.", size=14)],
                                       [ft.FilledButton("Close", on_click=on(self.close_share_dialog))])
        if install_wsl and not await asyncio.to_thread(poolnode.wsl_installed):
            self.share["pool_consent"] = True  # carry on by itself at the next start
            await self.save_share()
            return self.set_share_body([
                ft.Text("WSL is installed. Restart Windows to finish, then open this app again: it goes on "
                        "lending by itself.", size=14)],
                [ft.FilledButton("Done", on_click=on(self.close_share_dialog))])
        await self.allow_pool(info, chosen)

    def show_pool_running(self) -> None:
        self.set_share_body([
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, color=ft.Colors.GREEN_400),
                    ft.Text("This device is in the pool.", size=14, expand=True)], spacing=10),
            ft.Text("Put models on it from your NanoBorealis computer: nanoborealis pool plan shows what would "
                    "run where, and nanoborealis pool apply starts it.", size=13),
            ft.Text("It lends while this app is open, and again when you next open it.", size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT),
        ], [
            ft.TextButton("Stop and delete downloads", on_click=on(self.stop_pool, True)),
            ft.TextButton("Stop lending", on_click=on(self.stop_pool, False)),
            ft.FilledButton("Done", on_click=on(self.close_share_dialog)),
        ])

    async def stop_pool(self, delete: bool) -> None:
        node, self.node = self.node, None
        if node is not None:
            self.set_share_body([self.working("Stopping the pool node...")], [])
            await asyncio.to_thread(node.leave, delete)
            await self.check_in_to_pool(False)
        self.share["pool_consent"] = False  # don't start again on the next launch
        await self.save_share()
        self.set_share_body([
            ft.Text("This device has left the pool" + (", and what it downloaded is deleted." if delete else "."),
                    size=14),
            ft.Text("Run nanoborealis pool apply on your NanoBorealis computer to move its models elsewhere.",
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ], [ft.FilledButton("Done", on_click=on(self.close_share_dialog))])
        self.refresh_sidebar()

    # -- Install sticks ------------------------------------------------------------

    # -- Terminal --------------------------------------------------------------
    # A console on the paired computer, as the person who approved this device at its screen
    # (terminal.py): the app is that computer's remote control, not just the agent's chat.







    async def open_stick_dialog(self) -> None:
        if self.in_chat_view and self.page.width and self.page.width < NARROW:
            await self.page.close_drawer()
        self.stick_body = ft.Column(tight=True, spacing=12)
        self.stick_actions = ft.Row(spacing=8, wrap=True, alignment=ft.MainAxisAlignment.END)
        self.stick_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Make a NanoBorealis install stick", size=18, weight=ft.FontWeight.W_600),
            scrollable=True,
            content=ft.Container(self.stick_body, width=520),
            actions=[self.stick_actions],
        )
        self.page.show_dialog(self.stick_dialog)
        if not self.stick_writing:
            await self.show_stick_form()

    def set_stick_body(self, controls: list[ft.Control], actions: list[ft.Control]) -> None:
        self.stick_body.controls = controls
        self.stick_actions.controls = actions
        self.page.update()

    async def close_stick_dialog(self) -> None:
        self.page.pop_dialog()

    async def show_stick_form(self) -> None:
        self.set_stick_body([self.working("Looking for USB sticks...")], [])
        try:
            self.sticks = await asyncio.to_thread(stickmaker.list_sticks)
        except Exception as e:
            self.sticks = []
            print(f"nanoborealis-client: listing sticks failed: {e}")
        self.stick_choice = ft.Dropdown(
            label="USB stick", expand=True,
            options=[ft.DropdownOption(key=s.id, text=s.label) for s in self.sticks],
            value=self.sticks[0].id if len(self.sticks) == 1 else None,
        )
        self.stick_key = ft.TextField(
            label="OpenRouter API key (optional)", password=True, can_reveal_password=True,
            helper="Saved on the stick so setup doesn't ask for it. Use a dedicated key with a low credit limit.",
            helper_max_lines=2, expand=True,
        )
        self.stick_channel = ft.Dropdown(
            label="NanoBorealis build", value="stable", dense=True, expand=True,
            options=[ft.DropdownOption(key=key, text=text) for key, text in STICK_BUILDS],
        )
        self.stick_iso = ""
        self.stick_iso_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.stick_source = ft.RadioGroup(value="latest", content=ft.Column(tight=True, spacing=0, controls=[
            ft.Radio(value="latest", label="Download the latest NanoBorealis (about 5.3 GB, straight onto the stick)"),
            ft.Radio(value="file", label="Use an installer ISO I already have"),
        ]))
        if self.sticks:
            pick = ft.Row([self.stick_choice, ft.IconButton(ft.Icons.REFRESH_ROUNDED, tooltip="Look again",
                                                            on_click=on(self.show_stick_form))])
        else:
            pick = ft.Row([ft.Icon(ft.Icons.USB_ROUNDED, color=ft.Colors.ON_SURFACE_VARIANT),
                           ft.Text("Plug in a USB stick of 8 GB or more, then look again.", size=13, expand=True),
                           ft.IconButton(ft.Icons.REFRESH_ROUNDED, tooltip="Look again", on_click=on(self.show_stick_form))])
        self.set_stick_body([
            ft.Text("The stick is erased and becomes a NanoBorealis installer. Boot a computer from it to install.",
                    size=13, color=ft.Colors.ON_SURFACE_VARIANT),
            pick,
            ft.Row([self.stick_key]),
            ft.Row([self.stick_channel]),
            ft.Text("Each build has its own installer. If one isn't published yet, the stick carries the stable "
                    "installer and the new computer moves to your build at first login.",
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            self.stick_source,
            ft.Row([ft.OutlinedButton("Choose ISO file", icon=ft.Icons.FOLDER_OPEN_ROUNDED, on_click=on(self.pick_iso)),
                    self.stick_iso_text], spacing=10),
        ], [
            ft.TextButton("Cancel", on_click=on(self.close_stick_dialog)),
            ft.FilledButton("Next", icon=ft.Icons.ARROW_FORWARD_ROUNDED, on_click=on(self.confirm_stick),
                            disabled=not self.sticks),
        ])

    async def pick_iso(self) -> None:
        files = await self.file_picker.pick_files(dialog_title="Choose a NanoBorealis installer ISO",
                                                  allowed_extensions=["iso"])
        if files and files[0].path:
            self.stick_iso = files[0].path
            self.stick_iso_text.value = os.path.basename(self.stick_iso)
            self.stick_source.value = "file"
            self.page.update()

    async def confirm_stick(self) -> None:
        stick = next((s for s in self.sticks if s.id == self.stick_choice.value), None)
        if stick is None:
            self.stick_choice.error_text = "Choose a stick"
            self.page.update()
            return
        if self.stick_source.value == "file" and not self.stick_iso:
            self.stick_iso_text.value = "Choose the ISO file first"
            self.page.update()
            return
        source = "latest" if self.stick_source.value == "latest" else self.stick_iso
        key = (self.stick_key.value or "").strip()
        channel = self.stick_channel.value or "stable"
        # What the installed system reads from the stick: the key, and which build to follow.
        setup: dict[str, str] | None = {"written_by": "NanoBorealis client"}
        if key:
            setup["openrouter_api_key"] = key
        if channel != "stable":
            setup["channel"] = channel
        if len(setup) == 1:
            setup = None
        build = dict(STICK_BUILDS)[channel].split(":")[0]
        details = [ft.Text(f"Build: {build}", size=13)]
        if source == "latest":
            self.set_stick_body([self.working("Finding the installer...")], [])
            try:
                release = await asyncio.to_thread(stickmaker.latest_release, channel)
                details.append(ft.Text(f"Installer: {release.iso_name}", size=13, selectable=True))
                if channel != "stable" and not release.tag.startswith(f"nanoborealis-{channel}-"):
                    details.append(ft.Text(f"There's no {build} installer yet, so this is the stable one. The new "
                                           f"computer moves to {build} at first login.",
                                           size=12, color=ft.Colors.ON_SURFACE_VARIANT))
            except Exception as e:
                print(f"nanoborealis-client: finding the installer failed: {e}")
        elif channel != "stable":
            details.append(ft.Text(f"The new computer moves to {build} at first login.",
                                   size=12, color=ft.Colors.ON_SURFACE_VARIANT))
        self.set_stick_body([
            ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.ERROR),
                    ft.Text(f"Erase {stick.label}? Everything on it will be lost.", size=14, expand=True)], spacing=10),
            *details,
            ft.Text("Windows will ask for permission to write the stick." if sys.platform == "win32"
                    else "Your system will ask for your password to write the stick.",
                    size=13, color=ft.Colors.ON_SURFACE_VARIANT),
        ], [
            ft.TextButton("Back", on_click=on(self.show_stick_form)),
            ft.FilledButton("Erase and write", icon=ft.Icons.USB_ROUNDED, bgcolor=ft.Colors.ERROR,
                            color=ft.Colors.ON_ERROR, on_click=on(self.write_stick, stick, source, setup)),
        ])

    async def write_stick(self, stick: stickmaker.Disk, source: str, setup: dict | None) -> None:
        status = ft.Text("Waiting for permission...", size=13)
        bar = ft.ProgressBar(value=None)
        self.set_stick_body([status, bar,
                             ft.Text("Keep the stick plugged in. This takes about ten minutes.", size=12,
                                     color=ft.Colors.ON_SURFACE_VARIANT)],
                            [ft.TextButton("Hide", on_click=on(self.close_stick_dialog))])
        progress_file = os.path.join(tempfile.gettempdir(), f"nanoborealis-stick-{uuid.uuid4().hex[:8]}.json")
        try:
            await asyncio.to_thread(stickmaker.launch_elevated, stick, source, setup, progress_file)
        except stickmaker.StickError as e:
            self.show_stick_result(False, str(e))
            return
        self.stick_writing = True
        started = time.monotonic()
        last = None
        try:
            while True:
                await asyncio.sleep(0.5)
                try:
                    with open(progress_file, encoding="utf-8") as f:
                        state = json.load(f)
                except (OSError, ValueError):
                    if time.monotonic() - started > 120 and last is None:
                        self.show_stick_result(False, "The writer never started. Was the permission prompt declined?")
                        return
                    continue
                last = state
                if state.get("phase") == "error":
                    self.show_stick_result(False, state.get("error") or "Something went wrong")
                    return
                if state.get("phase") == "done":
                    self.show_stick_result(True, "")
                    return
                total = state.get("total") or 0
                bar.value = (state.get("done", 0) / total) if total else None
                verb = {"write": "Writing", "verify": "Checking"}.get(state.get("phase"), "")
                status.value = state.get("message", "")
                if total and verb:
                    status.value += f" ({state.get('done', 0) / 1e9:.1f} of {total / 1e9:.1f} GB)"
                self.page.update()
        finally:
            self.stick_writing = False
            try:
                os.remove(progress_file)
            except OSError:
                pass

    def show_stick_result(self, ok: bool, error: str) -> None:
        if ok:
            self.set_stick_body([
                ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, color=ft.Colors.GREEN_400),
                        ft.Text("The stick is ready and verified.", size=14, expand=True)], spacing=10),
                ft.Text("Boot the new computer from it: open its boot menu at power-on (F9 on HP, F12 on Dell and "
                        "Lenovo, Option on a Mac), install, and tick \"Make this user administrator\". At first "
                        "login, setup uses the key you saved on the stick.", size=13),
            ], [ft.FilledButton("Done", on_click=on(self.close_stick_dialog))])
        else:
            self.set_stick_body([ft.Text(f"The stick wasn't written: {error}", size=13, color=ft.Colors.ERROR)],
                                [ft.TextButton("Close", on_click=on(self.close_stick_dialog)),
                                 ft.FilledButton("Try again", on_click=on(self.show_stick_form))])

    # -- Sending -----------------------------------------------------------------





async def main(page: ft.Page) -> None:
    await NanoBorealisApp(page).start()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "write":
        # The packaged app relaunching itself, elevated, to write an install stick.
        sys.exit(stickmaker._cli(sys.argv[1:]))
    if host.is_host() and not host.claim_single_instance():
        sys.exit(0)  # already open: its window was brought forward instead
    # Under pythonw (no console) these are None, and Flet writes download progress to them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    ft.run(main, assets_dir=ASSETS)
    poolnode.stop_active()  # a node lent to the pool stops with the app
    # The window is gone: don't linger on background work (a download, compute sharing), which
    # would keep this process, and the app's files, busy while an update installs.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
