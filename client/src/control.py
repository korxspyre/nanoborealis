# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The control center: the agent's home screen, in the spirit of OpenClaw's dashboard.

Overview (is it running, on which model, what has it cost), Models, Channels (Telegram, Discord and
the rest), Schedules, Skills, Memory, Devices, Pool, Council, Logs and Settings. Most of it is nanobot's own
settings (AgentLink.request); what nanobot can't do itself goes through the computer's pairing
service (admin.py).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import flet as ft

import admin
import agent_settings
import host
import models_catalog
import theme as t
from agent_link import LinkError
from model_picker import ModelPicker
from pairing import PairingError

if TYPE_CHECKING:
    from main import NanoBorealisApp
    from workspace import Workspace

MEMORY_FILES = [
    ("memory/MEMORY.md", "What it remembers", "Facts it keeps between chats. It updates this itself; you can too."),
    ("USER.md", "About you", "What it knows about you and how you like to work."),
    ("SOUL.md", "Its personality", "How it talks and what it values."),
    ("AGENTS.md", "Its instructions", "Standing instructions for how it works."),
    ("HEARTBEAT.md", "Every half hour", "Tasks it checks on by itself every 30 minutes. Leave empty for none."),
    ("TOOLS.md", "Notes on its tools", "Notes about the tools and commands it can use."),
]


def when(ms: Any) -> str:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    stamp = datetime.fromtimestamp(ms / 1000)
    today = datetime.now().date()
    if stamp.date() == today:
        return stamp.strftime("today %H:%M")
    return stamp.strftime("%b %d, %H:%M")


def schedule_text(schedule: Any) -> str:
    if not isinstance(schedule, dict):
        return ""
    kind = schedule.get("kind")
    if kind == "every" and isinstance(schedule.get("every_ms"), (int, float)):
        minutes = schedule["every_ms"] / 60000
        if minutes < 60:
            return f"Every {minutes:.0f} minutes"
        if minutes < 1440:
            return f"Every {minutes / 60:g} hours"
        return f"Every {minutes / 1440:g} days"
    if kind == "cron":
        return f"cron {schedule.get('expr')}" + (f" ({schedule['tz']})" if schedule.get("tz") else "")
    if kind == "at":
        return f"Once, {when(schedule.get('at_ms'))}"
    return str(kind or "")


# What each chat channel needs, in a sentence, and the order they're listed in (the popular first).
CHANNEL_HINTS = {
    "telegram": "Talk to it through a Telegram bot: create one with @BotFather and paste its token.",
    "discord": "A Discord bot: create an application at discord.com/developers, add a bot, paste its token.",
    "whatsapp": "Link a WhatsApp account to it, the way WhatsApp Web links a computer.",
    "slack": "A Slack app in Socket Mode: paste its bot token and its app-level token.",
    "email": "Its own mailbox: it reads mail over IMAP and answers over SMTP.",
    "signal": "A Signal number of its own, through signal-cli.",
    "matrix": "A Matrix account for it, on any homeserver.",
    "msteams": "A Microsoft Teams bot.",
    "mattermost": "A Mattermost bot account.",
}
CHANNEL_ORDER = list(CHANNEL_HINTS)


def humanize(name: str) -> str:
    """A setting's name as a label: "replyToMessage" -> "Reply to message", "webhookURL" -> "Webhook URL"."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " "))
    words = [w if w.isupper() and len(w) > 1 else w.lower() for w in spaced.split()]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def money(value: Any) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) else "–"


def gb(value: Any) -> str:
    return f"{value / 1024**3:.1f} GB" if isinstance(value, (int, float)) else "–"


def remote_install_hint(message: str, computer: str) -> str:
    """The agent's own reason, with what to do about it when it's the one about installing a channel's
    packages: nanobot only does that for a request from the computer itself, so from another device
    (which is every device the app pairs) setting up Telegram, Discord and the like fails, and the
    reason was clipped to its first few words."""
    if "from a remote WebUI is disabled" not in message:
        return message
    where = f"the NanoBorealis app on {computer}" if computer else "the NanoBorealis app on that computer"
    return ("This channel needs the agent to install something first, and it only does that for a request "
            f"from the computer itself, not from this device. Set it up from {where}. ({message})")


class ControlCenter:
    def __init__(self, app: "NanoBorealisApp", workspace: "Workspace"):
        self.app = app
        self.workspace = workspace
        self.page = app.page
        self.settings: dict[str, Any] = {}

    # -- Frame -------------------------------------------------------------------------------------

    def placeholder(self) -> ft.Control:
        return ft.Container(ft.ProgressRing(width=22, height=22, stroke_width=2), alignment=ft.Alignment.CENTER,
                            expand=True)

    def frame(self, title: str, subtitle: str, body: list[ft.Control], actions: list[ft.Control] | None = None) -> ft.Control:
        head = ft.Row([ft.Container(t.section_title(title, subtitle), expand=True), *(actions or [])],
                      vertical_alignment=ft.CrossAxisAlignment.START)
        return ft.ListView(expand=True, padding=ft.Padding.symmetric(horizontal=32, vertical=24), spacing=16,
                           controls=[ft.Container(ft.Column([head, *body], spacing=16), width=860)])

    def problem(self, text: str) -> ft.Control:
        return t.card(ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color=t.BAD, size=18),
                              ft.Text(text, size=13.5, expand=True, selectable=True)], spacing=10))

    def toast(self, text: str) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(text), duration=3500))

    async def page_view(self, key: str) -> ft.Control:
        builders: dict[str, Callable[[], Awaitable[ft.Control]]] = {
            "overview": self.overview, "models": self.models, "channels": self.channels,
            "schedules": self.schedules, "skills": self.skills, "memory": self.memory,
            "devices": self.devices, "pool": self.pool, "council": self.council, "logs": self.logs, "settings": self.settings_page,
        }
        try:
            return await builders[key]()
        except (LinkError, PairingError, asyncio.TimeoutError) as e:
            return self.frame(key.capitalize(), "", [self.problem(str(e) or type(e).__name__)])

    async def reload(self, key: str) -> None:
        if self.workspace.section != key:
            return
        self.workspace.control_host.content = await self.page_view(key)
        self.page.update()

    async def load_settings(self) -> dict[str, Any]:
        if self.app.link is None:
            raise LinkError("Not connected to the agent.")
        self.settings = await agent_settings.load(self.app.link)
        return self.settings

    async def catalog(self) -> models_catalog.Catalog:
        try:
            await asyncio.to_thread(self.app.catalog.load)
        except Exception:
            pass
        return self.app.catalog

    def model_badge(self, model_id: str | None) -> ft.Control:
        if not model_id:
            return t.badge("unknown", t.MUTED)
        model = self.app.catalog.get(model_id) if self.app.catalog.models else None
        free = model.free if model else model_id.endswith(":free")
        return t.badge("FREE", t.GOOD) if free else t.badge(model.price_label() if model else "PAID", t.WARN)

    # -- Overview ----------------------------------------------------------------------------------

    async def overview(self) -> ft.Control:
        machine = self.app.machine
        info, usage, settings, spend = await asyncio.gather(
            asyncio.to_thread(admin.overview, machine), self.app.link.api_get("/api/settings/usage"),
            self.load_settings(), asyncio.to_thread(admin.usage, machine), return_exceptions=True)
        await self.catalog()
        cards: list[ft.Control] = []

        agent = info.get("agent", {}) if isinstance(info, dict) else {}
        running = bool(agent.get("running"))
        status = t.card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.CIRCLE, size=12, color=t.GOOD if running else t.BAD),
                    ft.Text("Running" if running else "Stopped", size=16, weight=ft.FontWeight.W_600),
                    ft.Container(expand=True),
                    t.outline_button("Restart", icon=ft.Icons.RESTART_ALT_ROUNDED,
                                     on_click=lambda _e: self.page.run_task(self.restart))]),
            t.muted(f"{info.get('machine', '')} · {(info.get('os') or {}).get('pretty_name', '')}"
                    if isinstance(info, dict) else str(info)),
        ], spacing=6))
        cards.append(status)

        if isinstance(settings, dict):
            order = agent_settings.call_order(settings)
            first = agent_settings.preset_model(settings, order[0]) if order else None
            rows = [ft.Row([ft.Text(models_catalog.label(first or "", self.app.catalog) if first else "–", size=15,
                                    weight=ft.FontWeight.W_600), self.model_badge(first)], spacing=10)]
            if len(order) > 1:
                rows.append(t.muted("Falls back to " + ", ".join(
                    models_catalog.label(agent_settings.preset_model(settings, n) or n, self.app.catalog)
                    for n in order[1:])))
            guard = agent_settings.free_only(settings)
            rows.append(ft.Row([ft.Icon(ft.Icons.VERIFIED_USER_OUTLINED if guard else ft.Icons.WARNING_AMBER_ROUNDED,
                                        size=15, color=t.GOOD if guard else t.WARN),
                                t.muted("OpenRouter refuses anything that costs money" if guard else
                                        "Paid models are allowed")], spacing=6))
            cards.append(t.card(ft.Column([
                ft.Row([t.muted("MODEL", size=11), ft.Container(expand=True),
                        t.quiet_button("Change", on_click=lambda _e: self.page.run_task(self.workspace.show_section,
                                                                                         "models"))]),
                *rows], spacing=6)))

        spent_rows: list[ft.Control] = []
        if isinstance(spend, dict) and "error" not in spend:
            spent_rows.append(ft.Row([
                ft.Column([t.muted("Today", size=11), ft.Text(money(spend.get("usage_daily")), size=18,
                                                              weight=ft.FontWeight.W_600)], spacing=2),
                ft.Column([t.muted("This month", size=11), ft.Text(money(spend.get("usage_monthly")), size=18,
                                                                   weight=ft.FontWeight.W_600)], spacing=2),
                ft.Column([t.muted("All time", size=11), ft.Text(money(spend.get("usage")), size=18,
                                                                 weight=ft.FontWeight.W_600)], spacing=2),
            ], spacing=32))
            if spend.get("limit") is not None:
                spent_rows.append(t.muted(f"Credit limit {money(spend.get('limit'))}, "
                                          f"{money(spend.get('limit_remaining'))} left"))
        else:
            reason = spend.get("error") if isinstance(spend, dict) else str(spend)
            spent_rows.append(t.muted(f"OpenRouter's count isn't available: {reason}"))
        if isinstance(usage, dict):
            spent_rows.append(t.muted(f"{usage.get('requests_30d', 0):,} requests and "
                                      f"{usage.get('total_tokens_30d', 0):,} tokens in the last 30 days"))
        cards.append(t.card(ft.Column([t.muted("SPENT ON OPENROUTER", size=11), *spent_rows], spacing=8)))

        return self.frame("Overview", "Your agent at a glance.", cards)

    async def restart(self) -> None:
        self.toast("Restarting the agent. It's back in a few seconds.")
        try:
            await asyncio.to_thread(admin.restart, self.app.machine)
        except PairingError as e:
            self.toast(str(e))
        await self.reload("overview")

    # -- Models ------------------------------------------------------------------------------------

    async def models(self) -> ft.Control:
        settings = await self.load_settings()
        await self.catalog()
        order = agent_settings.call_order(settings)
        rows: list[ft.Control] = []
        for i, name in enumerate(order):
            model_id = agent_settings.preset_model(settings, name) or ""
            label = models_catalog.label(model_id, self.app.catalog) if model_id else name
            rows.append(ft.Container(
                content=ft.Row([
                    ft.Container(ft.Text("Default" if i == 0 else f"Fallback {i}", size=11.5,
                                         color=t.ACCENT if i == 0 else t.MUTED), width=76),
                    ft.Column([ft.Row([ft.Text(label, size=14, weight=ft.FontWeight.W_600), self.model_badge(model_id)],
                                      spacing=8),
                               t.mono(model_id or name, size=11.5, color=t.FAINT)], spacing=2, expand=True),
                    t.icon_button(ft.Icons.ARROW_UPWARD_ROUNDED, "Try it earlier", size=16, disabled=i == 0,
                                  on_click=lambda _e, i=i: self.page.run_task(self.move, order, i, -1)),
                    t.icon_button(ft.Icons.ARROW_DOWNWARD_ROUNDED, "Try it later", size=16, disabled=i == len(order) - 1,
                                  on_click=lambda _e, i=i: self.page.run_task(self.move, order, i, 1)),
                    t.icon_button(ft.Icons.CLOSE_ROUNDED, "Remove from the list", size=16, disabled=len(order) == 1,
                                  on_click=lambda _e, n=name: self.page.run_task(self.set_order,
                                                                                 [o for o in order if o != n])),
                ], spacing=6),
                padding=ft.Padding.symmetric(horizontal=14, vertical=10), bgcolor=t.PANEL, border_radius=t.RADIUS,
                border=ft.Border.all(1, t.LINE),
            ))
        paid_default = agent_settings.preset_model(settings, "default")
        notes: list[ft.Control] = []
        if paid_default and not paid_default.endswith(":free"):
            notes.append(self.problem(f"nanobot's built-in \"default\" model is {paid_default}, which costs money. "
                                      "NanoBorealis never uses it, and fixes it with the next OS update."))
        body = [
            t.muted("The agent uses the first model, and moves down the list when one is busy or down. Every chat "
                    "can pick its own from the model button under the message box."),
            ft.Column(rows, spacing=6),
            ft.Row([t.primary_button("Add a model", icon=ft.Icons.ADD_ROUNDED,
                                     on_click=lambda _e: self.page.run_task(self.add_model, order, False)),
                    t.outline_button("Make another model the default", icon=ft.Icons.STAR_OUTLINE_ROUNDED,
                                     on_click=lambda _e: self.page.run_task(self.add_model, order, True))], spacing=10),
            *notes,
        ]
        return self.frame("Models", "Search every model on OpenRouter; free ones are marked.", body)

    async def move(self, order: list[str], index: int, step: int) -> None:
        order = list(order)
        order[index], order[index + step] = order[index + step], order[index]
        await self.set_order(order)

    async def set_order(self, order: list[str]) -> None:
        try:
            await agent_settings.set_call_order(self.app.link, order)
        except LinkError as e:
            self.toast(str(e))
        await self.reload("models")

    async def add_model(self, order: list[str], first: bool) -> None:
        async def chosen(model: models_catalog.Model) -> None:
            try:
                if not model.free and agent_settings.free_only(self.settings):
                    await agent_settings.set_free_only(self.app.link, False, self.settings)  # confirmed in the picker
                name = await agent_settings.ensure_preset(self.app.link, model, self.settings)
                rest = [o for o in order if o != name]
                await agent_settings.set_call_order(self.app.link, [name, *rest] if first else [*rest, name])
            except LinkError as e:
                self.toast(str(e))
                return
            self.toast(f"{model.short_name} is {'the default now' if first else 'on the list'}.")
            if first:
                self.workspace.set_model(model.id)
            await self.reload("models")

        picker = ModelPicker(self.page, self.app.catalog, None, chosen,
                             title="Make a model the default" if first else "Add a fallback model")
        await picker.open()

    # -- Channels ----------------------------------------------------------------------------------

    async def channels(self) -> ft.Control:
        data = await self.app.link.api_get("/api/settings/nanobot-features", timeout=90)  # it checks each one
        features = [f for f in (data.get("features") if isinstance(data, dict) else []) or []
                    if isinstance(f, dict) and f.get("type") == "channel" and f.get("name") != "websocket"
                    and f.get("settings_visible", True)]
        rows: list[ft.Control] = []
        rank = {name: i for i, name in enumerate(CHANNEL_ORDER)}
        for feature in sorted(features, key=lambda f: (not f.get("enabled"), rank.get(f.get("name"), 99),
                                                       str(f.get("display_name")))):
            status = str(feature.get("status") or "")
            badge = {"enabled": t.badge("ON", t.GOOD), "running": t.badge("RUNNING", t.GOOD),
                     "invalid_config": t.badge("NEEDS FIXING", t.BAD),
                     "missing_dependency": t.badge("NOT INSTALLED", t.MUTED)}.get(status, t.badge("OFF", t.MUTED))
            actions: list[ft.Control] = [t.outline_button("Set up" if not feature.get("configured") else "Settings",
                                                          on_click=lambda _e, f=feature: self.page.run_task(
                                                              self.channel_setup, f))]
            if feature.get("enabled"):
                actions.append(t.quiet_button("Turn off", on_click=lambda _e, f=feature: self.page.run_task(
                    self.channel_off, f)))
            rows.append(t.card(ft.Row([
                ft.Column([ft.Row([ft.Text(str(feature.get("display_name") or feature.get("name")), size=15,
                                           weight=ft.FontWeight.W_600), badge], spacing=8),
                           t.muted(self.channel_blurb(feature))], spacing=4, expand=True),
                *actions,
            ], spacing=8), padding=14))
        if not rows:
            rows.append(t.muted("This agent's nanobot has no chat channels available."))
        return self.frame("Channels", "Talk to your agent from Telegram, Discord, Slack, WhatsApp, email and more.",
                          [t.muted("Anyone who can message the channel's bot can talk to your agent, so list who's "
                                   "allowed (\"Allow from\") when you set one up."), *rows])

    @staticmethod
    def channel_blurb(feature: dict[str, Any]) -> str:
        if feature.get("enabled"):
            return "Connected: messages there reach your agent."
        hint = CHANNEL_HINTS.get(str(feature.get("name")), "Set it up with the details the service gives you.")
        return hint if feature.get("installed") else f"{hint} (It installs itself.)"

    async def channel_setup(self, feature: dict[str, Any]) -> None:
        # nanobot's setup contract (channels/contracts.py, to_public_dict): a list of writable fields
        # (field, kind, choices, required, default_value) and groups of fields, one of which must be set.
        setup = feature.get("setup") if isinstance(feature.get("setup"), dict) else {}
        fields = {f["field"]: f for f in setup.get("fields") or [] if isinstance(f, dict) and f.get("field")}
        required = {name for name, f in fields.items() if f.get("required")}
        for requirement in setup.get("requirements") or []:
            alternatives = requirement.get("alternatives") if isinstance(requirement, dict) else None
            if alternatives:
                required |= {str(key).rsplit(".", 1)[-1] for key in alternatives[0]}
        values = next((feature[k] for k in ("values", "snapshot", "config") if isinstance(feature.get(k), dict)), {})
        inputs: dict[str, tuple[str, ft.Control]] = {}
        controls: list[ft.Control] = []
        more: list[ft.Control] = []
        # What's needed to get going, plus who may talk to it, up front; the rest under "More settings".
        first = [name for name in fields if name in required or name in ("allowFrom", "groupPolicy")]
        rest = [name for name in fields if name not in first]
        for name in first + rest:
            spec = fields[name]
            kind = spec.get("kind") or "string"
            label = {"allowFrom": "Allow from (who may talk to it, comma-separated)",
                     "groupPolicy": "In group chats, answer"}.get(name, humanize(name))
            current = values.get(name, spec.get("default_value"))
            if kind == "bool":
                control: ft.Control = ft.Switch(label=label, value=bool(current), active_color=t.ACCENT)
            elif kind == "enum" and spec.get("choices"):
                control = ft.Dropdown(label=label, value=str(current or ""), dense=True, border_radius=t.RADIUS,
                                      options=[ft.DropdownOption(str(c)) for c in sorted(spec["choices"])])
            else:
                shown = ", ".join(map(str, current)) if isinstance(current, list) else ("" if current is None else str(current))
                control = t.field(label, "" if kind == "secret" else shown, password=kind == "secret",
                                  hint="saved; leave empty to keep it" if kind == "secret" and feature.get("configured")
                                  else None)
            inputs[name] = (kind, control)
            (controls if name in first else more).append(control)
        link = setup.get("official_url")
        result = t.muted("")
        busy = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

        def gather() -> dict[str, Any]:
            out: dict[str, Any] = {}
            for name, (kind, control) in inputs.items():
                value = control.value
                if kind == "list":
                    out[name] = [v.strip() for v in str(value or "").split(",") if v.strip()]
                elif kind == "secret":
                    if value:
                        out[name] = value
                elif kind == "int":
                    try:
                        out[name] = int(value)
                    except (TypeError, ValueError):
                        pass
                elif kind == "float":
                    try:
                        out[name] = float(value)
                    except (TypeError, ValueError):
                        pass
                else:
                    out[name] = value
            return out

        async def save(_event=None) -> None:
            busy.visible = True
            result.value = "Checking the connection..."
            self.page.update()
            name = feature["name"]
            try:
                if setup.get("verifies_connection", True):
                    await self.app.link.request("settings.channel.validate", {"name": name, "values": gather()},
                                                timeout=90)
                await self.app.link.request("settings.channel.configure",
                                            {"name": name, "values": gather(), "enable": True}, timeout=300)
            except LinkError as e:
                busy.visible = False
                result.value = remote_install_hint(str(e), self.app.machine.name if self.app.machine else "")
                result.color = t.BAD
                self.page.update()
                return
            self.page.pop_dialog()
            self.toast(f"{feature.get('display_name')} is set up. Its bot answers from now on.")
            await self.reload("channels")

        extra: list[ft.Control] = []
        if link:
            extra.append(t.quiet_button(f"Where to get a {feature.get('display_name')} token",
                                        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                        on_click=lambda _e: self.page.run_task(self.app.url_launcher.launch_url, link)))
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text(f"Set up {feature.get('display_name')}", size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=480, height=min(520, (self.page.height or 800) - 220), content=ft.Column([
                *extra, *controls,
                *([ft.ExpansionTile(title=ft.Text("More settings", size=13.5), controls=more, dense=True,
                                    controls_padding=ft.Padding.only(top=8), tile_padding=ft.Padding.all(0))]
                  if more else []),
                ft.Row([busy, ft.Container(result, expand=True)], spacing=8),  # expand: a long reason wraps, not clips
            ], spacing=12, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Save and turn on", on_click=save)],
        ))

    async def channel_off(self, feature: dict[str, Any]) -> None:
        try:
            await self.app.link.request("settings.feature.disable", {"name": feature["name"]}, timeout=120)
        except LinkError as e:
            self.toast(str(e))
        await self.reload("channels")

    # -- Schedules ---------------------------------------------------------------------------------

    async def schedules(self) -> ft.Control:
        data = await self.app.link.api_get("/api/webui/automations")
        jobs = [j for j in (data.get("jobs") if isinstance(data, dict) else []) or [] if isinstance(j, dict)]
        rows: list[ft.Control] = []
        for job in jobs:
            state = job.get("state") if isinstance(job.get("state"), dict) else {}
            message = str((job.get("payload") or {}).get("message") or "")
            meta = [schedule_text(job.get("schedule"))]
            if state.get("next_run_at_ms") and job.get("enabled"):
                meta.append(f"next {when(state['next_run_at_ms'])}")
            if state.get("last_status"):
                meta.append(f"last run: {state['last_status']}")
            rows.append(t.card(ft.Row([
                ft.Switch(value=bool(job.get("enabled")), active_color=t.ACCENT, disabled=bool(job.get("protected")),
                          on_change=lambda e, j=job: self.page.run_task(
                              self.job_action, "automation.enable" if e.control.value else "automation.disable", j)),
                ft.Column([ft.Text(str(job.get("name") or message[:60] or job.get("id")), size=14.5,
                                   weight=ft.FontWeight.W_600),
                           t.muted(" · ".join(m for m in meta if m)),
                           t.muted(message[:240], size=12.5) if message else ft.Container()], spacing=3, expand=True),
                t.icon_button(ft.Icons.PLAY_ARROW_ROUNDED, "Run it now" if not job.get("protected") else
                              "The agent's own schedules run when they're due", size=18, disabled=bool(job.get("protected")),
                              on_click=lambda _e, j=job: self.page.run_task(self.job_action, "automation.run", j)),
                t.icon_button(ft.Icons.DELETE_OUTLINE_ROUNDED, "Delete", size=18, disabled=bool(job.get("protected")),
                              on_click=lambda _e, j=job: self.page.run_task(self.delete_schedule, j)),
            ], spacing=10), padding=14))
        if not rows:
            rows.append(t.card(t.muted("Nothing scheduled yet. Ask your agent to do something regularly, like "
                                       "\"every morning at 8, check my projects' tests and tell me\".")))
        return self.frame("Schedules", "What your agent does by itself, and when.", rows,
                          actions=[t.primary_button("New schedule", icon=ft.Icons.ADD_ROUNDED,
                                                    on_click=lambda _e: self.page.run_task(self.new_schedule))])

    async def delete_schedule(self, job: dict[str, Any]) -> None:
        """Ask first: one click on the bin used to remove a schedule for good."""
        name = str(job.get("name") or job.get("id") or "This schedule")

        async def go(_event=None) -> None:
            self.page.pop_dialog()
            await self.job_action("automation.delete", job)

        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text("Delete this schedule?", size=18, weight=ft.FontWeight.W_600),
            content=ft.Text(f"{name} stops running, and it can't be brought back.", size=13.5, color=t.MUTED),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Delete", on_click=go)]))

    async def job_action(self, action: str, job: dict[str, Any]) -> None:
        try:
            await self.app.link.request(action, {"id": job.get("id")}, timeout=60)
        except LinkError as e:
            self.toast(str(e))
            await self.reload("schedules")  # the switch goes back to what the agent has
            return  # (this used to fall through to "Running it now" after a refusal)
        if action == "automation.run":
            self.toast("Running it now. Its result shows up in its chat.")
        await self.reload("schedules")

    async def new_schedule(self) -> None:
        what = t.field("What should it do?", multiline=True,
                       hint="Check my projects for failing tests and tell me what broke")
        when_field = t.field("When?", hint="every weekday at 8:00, every 2 hours, tomorrow at 17:30")

        async def create(_event=None) -> None:
            if not (what.value or "").strip() or not (when_field.value or "").strip():
                return
            self.page.pop_dialog()
            await self.workspace.open_chat(None)
            await self.workspace.send_text(f"Schedule this: {what.value.strip()}\nWhen: {when_field.value.strip()}",
                                           intent="create_automation")

        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text("New schedule", size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=480, content=ft.Column([
                t.muted("Your agent sets it up and confirms in a new chat."), what, when_field], spacing=12, tight=True)),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Schedule it", on_click=create)],
        ))

    # -- Skills ------------------------------------------------------------------------------------

    async def skills(self) -> ft.Control:
        data = await self.app.link.api_get("/api/webui/skills")
        skills = [s for s in (data.get("skills") if isinstance(data, dict) else []) or [] if isinstance(s, dict)]
        rows: list[ft.Control] = []
        for skill in sorted(skills, key=lambda s: (str(s.get("source")) != "workspace", str(s.get("name")))):
            rows.append(t.card(ft.Row([
                ft.Column([ft.Row([ft.Text(str(skill.get("name")), size=14.5, weight=ft.FontWeight.W_600),
                                   t.badge(str(skill.get("source") or "built-in"), t.MUTED)], spacing=8),
                           t.muted(str(skill.get("description") or ""), size=12.5),
                           t.muted(str(skill.get("unavailable_reason") or ""), size=12) if not skill.get(
                               "available", True) else ft.Container()], spacing=3, expand=True),
                ft.Switch(value=bool(skill.get("enabled", True)), active_color=t.ACCENT,
                          on_change=lambda e, s=skill: self.page.run_task(self.skill_enable, s, e.control.value)),
            ], spacing=10), padding=14))
        self.skill_query = t.field(hint="Search the skill hub: git, docker, pdf, web scraping...")
        self.skill_query.on_submit = lambda _e: self.page.run_task(self.skill_search)
        self.skill_results = ft.Column(spacing=8)
        hub = t.card(ft.Column([ft.Text("Get more skills", size=15, weight=ft.FontWeight.W_600),
                                ft.Row([ft.Container(self.skill_query, expand=True),
                                        t.primary_button("Search", on_click=lambda _e: self.page.run_task(
                                            self.skill_search))]), self.skill_results], spacing=10))
        return self.frame("Skills", "Know-how your agent loads when a task calls for it.", [hub, *rows])

    async def skill_enable(self, skill: dict[str, Any], on: bool) -> None:
        try:
            await self.app.link.request("skill.update", {"name": skill.get("name"), "enabled": on})
        except LinkError as e:
            self.toast(str(e))

    async def skill_search(self) -> None:
        query = (self.skill_query.value or "").strip()
        if not query:
            return
        self.skill_results.controls = [ft.ProgressRing(width=18, height=18, stroke_width=2)]
        self.page.update()
        import urllib.parse
        try:
            data = await self.app.link.api_get("/api/webui/skills/search?" + urllib.parse.urlencode({"q": query}))
        except LinkError as e:
            self.skill_results.controls = [t.muted(str(e))]
            self.page.update()
            return
        found = data.get("results") or data.get("skills") or [] if isinstance(data, dict) else []
        self.skill_results.controls = [
            ft.Row([ft.Column([ft.Text(str(r.get("name") or r.get("skill")), size=13.5, weight=ft.FontWeight.W_600),
                               t.muted(str(r.get("description") or "")[:200], size=12)], spacing=2, expand=True),
                    t.outline_button("Install", on_click=lambda _e, r=r: self.page.run_task(self.skill_install, r))])
            for r in found[:12] if isinstance(r, dict)] or [t.muted("Nothing found.")]
        self.page.update()

    async def skill_install(self, found: dict[str, Any]) -> None:
        payload = {k: found.get(k) for k in ("provider", "source", "skill", "version") if found.get(k) is not None}
        payload.setdefault("skill", found.get("name"))
        try:
            await self.app.link.request("skill.install", payload, timeout=300)
        except LinkError as e:
            self.toast(str(e))
            return
        self.toast(f"Installed {found.get('name') or found.get('skill')}.")
        await self.reload("skills")

    # -- Memory ------------------------------------------------------------------------------------

    async def memory(self) -> ft.Control:
        machine = self.app.machine
        files = await asyncio.to_thread(admin.list_files, machine)
        present = {f["path"].removeprefix(admin.WORKSPACE + "/") for f in files if isinstance(f, dict)}
        rows: list[ft.Control] = []
        for path, title, blurb in MEMORY_FILES:
            rows.append(t.card(ft.Row([
                ft.Column([ft.Text(title, size=14.5, weight=ft.FontWeight.W_600), t.muted(blurb, size=12.5),
                           t.mono(path, size=11, color=t.FAINT)], spacing=3, expand=True),
                t.outline_button("Open" if path in present else "Write", on_click=lambda _e, p=path, ti=title:
                                 self.page.run_task(self.edit_file, p, ti)),
            ], spacing=10), padding=14))
        return self.frame("Memory", "What your agent keeps between chats, in plain files you can edit.", rows)

    async def edit_file(self, path: str, title: str) -> None:
        full = f"{admin.WORKSPACE}/{path}"
        try:
            content = await asyncio.to_thread(admin.read_file, self.app.machine, full)
        except PairingError:
            content = ""
        editor = ft.TextField(value=content, multiline=True, min_lines=16, max_lines=24, text_size=13,
                              text_style=ft.TextStyle(font_family=t.MONO), border_radius=t.RADIUS,
                              border_color=t.LINE_HI, focused_border_color=t.ACCENT, bgcolor=t.BG)

        async def save(_event=None) -> None:
            try:
                await asyncio.to_thread(admin.write_file, self.app.machine, full, editor.value or "")
            except PairingError as e:
                self.toast(str(e))
                return
            self.page.pop_dialog()
            self.toast(f"Saved {title.lower()}.")

        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text(title, size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=720, content=editor),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Save", on_click=save)],
        ))

    # -- Devices -----------------------------------------------------------------------------------

    async def devices(self) -> ft.Control:
        devices = await asyncio.to_thread(admin.devices, self.app.machine)
        rows: list[ft.Control] = []
        for device in devices:
            seen = device.get("seen")
            meta = [f"approved by {device.get('owner')}" if device.get("owner") else "",
                    f"last seen {datetime.fromtimestamp(seen).strftime('%b %d, %H:%M')}" if seen else "",
                    str(device.get("address") or "")]
            rows.append(t.card(ft.Row([
                ft.Icon(ft.Icons.COMPUTER_ROUNDED if device.get("here") else ft.Icons.PHONE_ANDROID_ROUNDED,
                        color=t.ACCENT if device.get("this") else t.MUTED),
                ft.Column([ft.Row([ft.Text(str(device.get("name")), size=14.5, weight=ft.FontWeight.W_600),
                                   t.badge("THIS DEVICE", t.ACCENT) if device.get("this") else ft.Container()],
                                  spacing=8),
                           t.muted(" · ".join(m for m in meta if m), size=12.5)], spacing=3, expand=True),
                ft.Container() if device.get("this") else t.quiet_button(
                    "Unpair", color=t.BAD, on_click=lambda _e, d=device: self.page.run_task(self.unpair, d)),
            ], spacing=12), padding=14))
        return self.frame("Devices", "Phones and computers paired with this NanoBorealis computer.",
                          [t.muted("To add one, open the NanoBorealis app on it and pick this computer; a PIN shows "
                                   "up here to approve it."), *rows])

    async def unpair(self, device: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(admin.remove_device, self.app.machine, device["id"])
        except PairingError as e:
            self.toast(str(e))
        await self.reload("devices")

    # -- Pool --------------------------------------------------------------------------------------

    async def pool(self, rebuild: bool = False) -> ft.Control:
        data = await asyncio.to_thread(admin.pool_overview, self.app.machine, rebuild)
        title, subtitle = "Pool", "Your computers, and the council of models the planner runs on them."
        if not data.get("in_pool"):
            return self.frame(title, subtitle, [t.card(ft.Column([
                ft.Text("This computer isn't in a pool yet", size=15, weight=ft.FontWeight.W_600),
                t.muted("A pool lends the memory of several computers to each other, so together they run models "
                        "none could alone. Start one here in a terminal:"),
                t.mono("nanoborealis pool join", selectable=True),
                t.muted("Other computers join the same way, or from the NanoBorealis app on them: \"Lend this "
                        "computer to the pool\"."),
            ], spacing=8))])
        job = data.get("job") or {}
        job_box = ft.Column(spacing=6)
        body: list[ft.Control] = [t.card(job_box, visible=bool(job.get("what")))]
        self.fill_pool_job(job_box, job)
        if data.get("error"):
            body.append(self.problem(f"The pool didn't answer: {data['error']}"))
        else:
            body += [self.pool_nodes(data.get("nodes") or []),
                     self.pool_plan(data.get("plan") or {}, rebuild, bool(job.get("running"))),
                     self.pool_bench(data.get("bench") or []), self.pool_policy(data.get("policy") or {})]
        actions = [t.outline_button("Refresh", icon=ft.Icons.REFRESH_ROUNDED,
                                    on_click=lambda _e: self.page.run_task(self.reload, "pool"))]
        if job.get("running"):
            self.page.run_task(self.follow_pool_job, job_box)
        name = f" · private pool {data['namespace']}" if data.get("namespace") else ""
        return self.frame(title, subtitle + name, body, actions=actions)

    def fill_pool_job(self, box: ft.Column, job: dict[str, Any]) -> None:
        """The change running on the pool (or the last one), with its newest output."""
        if not job.get("what"):
            return
        running = bool(job.get("running"))
        state = (ft.ProgressRing(width=14, height=14, stroke_width=2) if running else
                 ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED if job.get("ok") else ft.Icons.ERROR_OUTLINE_ROUNDED,
                         size=16, color=t.GOOD if job.get("ok") else t.BAD))
        verb = "Now" if running else ("Done" if job.get("ok") else "Didn't finish")
        lines = [str(line) for line in job.get("output") or []][-14:]
        box.controls = [ft.Row([state, ft.Text(f"{verb}: {job['what']}", size=14, weight=ft.FontWeight.W_600)],
                               spacing=8),
                        t.mono("\n".join(lines) or "Starting...", size=11.5, color=t.MUTED, selectable=True)]

    async def follow_pool_job(self, box: ft.Column) -> None:
        self.pool_follower = follower = object()  # the newest page's follower is the only one
        while self.workspace.section == "pool" and getattr(self, "pool_follower", None) is follower:
            await asyncio.sleep(2)
            try:
                job = await asyncio.to_thread(admin.pool_job, self.app.machine)
            except PairingError:
                return
            if getattr(self, "pool_follower", None) is not follower:
                return
            self.fill_pool_job(box, job)
            self.page.update()
            if not job.get("running"):
                self.toast(f"The pool is done {job.get('what')}." if job.get("ok") else
                           f"The pool couldn't finish {job.get('what')}; see what it said.")
                await self.reload("pool")
                return

    async def pool_change(self, start: Callable[[], Any], message: str) -> None:
        try:
            await asyncio.to_thread(start)
        except PairingError as e:
            self.toast(str(e))
            return
        self.toast(message)
        await self.reload("pool")

    def pool_nodes(self, nodes: list[dict[str, Any]]) -> ft.Control:
        backends = {"cuda": ("NVIDIA", t.GOOD), "metal": ("APPLE GPU", t.VIOLET), "cpu": ("PROCESSOR", t.MUTED)}
        rows: list[ft.Control] = []
        for node in sorted(nodes, key=lambda n: (n.get("backend") == "cpu", str(n.get("name")).lower())):
            label, color = backends.get(str(node.get("backend")), (str(node.get("backend")).upper(), t.MUTED))
            memory = f"{gb(node.get('mem_capacity'))} free for models, of {gb(node.get('mem_total'))}"
            drained = bool(node.get("drained"))
            holds = ", ".join(node.get("holds") or []) or "nothing yet"
            node_id = str(node.get("id"))
            button = (t.outline_button("Let it back in", on_click=lambda _e, n=node_id, nm=node.get("name"):
                                       self.page.run_task(self.pool_change, lambda: admin.pool_undrain(
                                           self.app.machine, n), f"The planner can use {nm} again. Apply the "
                                                                 "plan to put models on it."))
                      if drained else
                      t.quiet_button("Drain", on_click=lambda _e, n=node_id, nm=node.get("name"):
                                     self.page.run_task(self.pool_change, lambda: admin.pool_drain(
                                         self.app.machine, n), f"Moving the models off {nm}.")))
            rows.append(ft.Container(ft.Row([
                ft.Column([ft.Row([ft.Text(str(node.get("name")), size=14, weight=ft.FontWeight.W_600),
                                   t.badge(label, color), t.badge(str(node.get("link") or "").upper(), t.MUTED),
                                   t.badge("DRAINED", t.WARN) if drained else ft.Container()], spacing=6),
                           t.muted(f"{memory} · holds {holds}", size=12.5)], spacing=3, expand=True),
                button], spacing=10), padding=ft.Padding.symmetric(vertical=6)))
        return t.card(ft.Column([
            t.muted(f"COMPUTERS · {len(nodes)}", size=11), *rows,
            t.muted("To grow the pool, run nanoborealis pool join on another computer, or open the NanoBorealis app "
                    "there and lend it. Draining a computer moves its models elsewhere, so it can leave.", size=12),
        ], spacing=4))

    def pool_plan(self, plan: dict[str, Any], rebuild: bool, busy: bool) -> ft.Control:
        rows: list[ft.Control] = []
        for placement in plan.get("placements") or []:
            where = ", ".join(f"{n.get('name')} {float(n.get('share') or 0):.0%}" for n in placement.get("nodes") or [])
            speed = f"{placement.get('gen_tps')} tok/s" + ("" if placement.get("measured") else " (estimate)")
            tools = {True: ("TOOLS OK", t.GOOD), False: ("TOOLS FAIL", t.BAD)}.get(placement.get("tools"),
                                                                                   ("TOOLS ?", t.MUTED))
            score = f"{'≈' if placement.get('estimated') else ''}{float(placement.get('capability') or 0):.0f}"
            rows.append(ft.Container(ft.Column([
                ft.Row([ft.Text(str(placement.get("model")), size=13.5, weight=ft.FontWeight.W_600, expand=True),
                        t.badge(f"SCORE {score}", t.ACCENT), t.badge(tools[0], tools[1])], spacing=6),
                t.muted(f"{placement.get('size_gb')} GB · {speed} · on {where}", size=12.5),
            ], spacing=2), padding=ft.Padding.symmetric(vertical=5)))
        if not rows:
            rows.append(t.muted("The planner has nothing to run on this pool."))
        changes: list[ft.Control] = []
        for stop in plan.get("stop") or []:
            changes.append(ft.Row([t.badge("STOP", t.BAD), t.mono(str(stop.get("model")), size=12)], spacing=8))
        for model in plan.get("start") or []:
            changes.append(ft.Row([t.badge("START", t.GOOD), t.mono(str(model), size=12)], spacing=8))
        pending = bool(changes)
        if plan.get("keep"):
            changes.append(t.muted(f"Keeps {len(plan['keep'])} model(s) that already run.", size=12.5))
        if not pending:
            changes.append(t.muted("The pool already runs this loadout.", size=12.5))
        notes = [t.muted(f"· {note}", size=12) for note in plan.get("notes") or []]
        apply = t.primary_button("Rebuild it" if rebuild else "Apply", icon=ft.Icons.PLAY_ARROW_ROUNDED,
                                 disabled=busy or not pending,
                                 on_click=lambda _e: self.page.run_task(self.pool_change, lambda: admin.pool_apply(
                                     self.app.machine, rebuild), "Changing the pool's models. It takes a few minutes."))
        other = (t.quiet_button("Back to keeping what runs", on_click=lambda _e: self.page.run_task(self.reload, "pool"))
                 if rebuild else
                 t.quiet_button("Plan from scratch", on_click=lambda _e: self.page.run_task(self.pool_rebuild)))
        return t.card(ft.Column([
            t.muted("PROPOSED LOADOUT, FROM SCRATCH" if rebuild else "PROPOSED LOADOUT", size=11), *rows, *notes,
            ft.Divider(height=8, color=t.LINE), *changes, ft.Row([apply, other], spacing=10),
        ], spacing=4))

    async def pool_rebuild(self) -> None:
        if self.workspace.section != "pool":
            return
        self.workspace.control_host.content = self.placeholder()
        self.page.update()
        try:
            self.workspace.control_host.content = await self.pool(rebuild=True)
        except PairingError as e:
            self.workspace.control_host.content = self.frame("Pool", "", [self.problem(str(e))])
        self.page.update()

    def pool_bench(self, rows: list[dict[str, Any]]) -> ft.Control:
        lines: list[ft.Control] = []
        for row in rows[:20]:
            tools = {1: "tools ok", 0: "tools failed"}.get(row.get("tools"), "tools unchecked")
            lines.append(ft.Row([
                ft.Icon(ft.Icons.CHECK_ROUNDED if row.get("fast") else ft.Icons.CLOSE_ROUNDED, size=16,
                        color=t.GOOD if row.get("fast") else t.BAD),
                ft.Column([t.mono(str(row.get("model")), size=12),
                           t.muted(f"{float(row.get('gen_tps') or 0):.1f} tok/s writing · "
                                   f"{float(row.get('prompt_tps') or 0):.0f} reading · {tools} · on {row.get('names')}",
                                   size=12)], spacing=1, expand=True)], spacing=8))
        if not lines:
            lines.append(t.muted("Nothing measured yet. The planner guesses speeds until it has measured them."))
        return t.card(ft.Column([
            t.muted("MEASURED", size=11), *lines,
            ft.Row([t.outline_button("Measure what runs", icon=ft.Icons.SPEED_ROUNDED,
                                     on_click=lambda _e: self.page.run_task(self.pool_change, lambda: admin.pool_bench(
                                         self.app.machine), "Measuring every model the pool serves.")),
                    t.quiet_button("Try a model", on_click=lambda _e: self.page.run_task(self.pool_try))], spacing=10),
        ], spacing=6))

    async def pool_try(self) -> None:
        model = t.field("Model", hint="mlx-community/Qwen3-14B-4bit")
        on = t.field("On these computers (optional)", hint="names, comma-separated; empty lets the planner pick")

        async def start(_event=None) -> None:
            wanted = (model.value or "").strip()
            if not wanted:
                return
            self.page.pop_dialog()
            computers = [c.strip() for c in (on.value or "").split(",") if c.strip()]
            await self.pool_change(lambda: admin.pool_bench(self.app.machine, wanted, computers),
                                   f"Trying {wanted}: it runs briefly, gets measured, then stops.")

        self.page.show_dialog(ft.AlertDialog(
            bgcolor=t.PANEL, shape=ft.RoundedRectangleBorder(radius=t.RADIUS_LG),
            title=ft.Text("Try a model", size=18, weight=ft.FontWeight.W_600),
            content=ft.Container(width=480, content=ft.Column([
                t.muted("The pool runs it for a moment, measures its speed and checks that it can call tools, then "
                        "stops it. The planner remembers what it learned."), model, on], spacing=12, tight=True)),
            actions=[t.quiet_button("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                     t.primary_button("Try it", on_click=start)],
        ))

    def pool_policy(self, policy: dict[str, Any]) -> ft.Control:
        numbers = [("min_gen_tps", "Slowest writing (tok/s)"), ("min_prompt_tps", "Slowest reading (tok/s)"),
                   ("headroom_gb", "Kept free on each computer (GB)"), ("max_models", "Most models at once"),
                   ("max_nodes", "Most computers per model")]
        lists = [("pinned", "Always run when they fit", "model ids, comma-separated"),
                 ("banned", "Never run", "model ids, comma-separated")]
        inputs: dict[str, ft.TextField] = {}
        for key, label in numbers:
            inputs[key] = t.field(label, "" if policy.get(key) is None else f"{policy[key]:g}", width=200)
        for key, label, hint in lists:
            inputs[key] = t.field(label, ", ".join(policy.get(key) or []), hint=hint)

        async def save(_event=None) -> None:
            changes: dict[str, Any] = {}
            try:
                for key, _label in numbers:
                    value = float((inputs[key].value or "").strip())
                    if value != policy.get(key):
                        changes[key] = int(value) if key.startswith("max_") else value
            except ValueError:
                self.toast("The numbers need to be numbers.")
                return
            for key, _label, _hint in lists:
                value = [v.strip() for v in (inputs[key].value or "").split(",") if v.strip()]
                if value != (policy.get(key) or []):
                    changes[key] = value
            if not changes:
                self.toast("Nothing changed.")
                return
            await self.pool_change(lambda: admin.pool_policy(self.app.machine, changes),
                                   "Saved. The proposed loadout follows the new settings.")

        return t.card(ft.Column([
            t.muted("POLICY", size=11),
            t.muted("What the planner aims for. Models slower than the floors are left out; pinned ones run "
                    "whenever they fit.", size=12.5),
            ft.Row([inputs[key] for key, _ in numbers], wrap=True, spacing=10, run_spacing=10),
            *[inputs[key] for key, _, _ in lists],
            ft.Row([t.primary_button("Save", on_click=save)]),
        ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

    # -- Council -----------------------------------------------------------------------------------

    async def council(self) -> ft.Control:
        title, subtitle = "Council", "One agent per model of the pool, meeting in the Hall. Your agent is the chair."
        try:
            seats, settings = await asyncio.gather(asyncio.to_thread(admin.hall, self.app.machine, "GET", "/seats"),
                                                   asyncio.to_thread(admin.hall, self.app.machine, "GET", "/settings"))
        except PairingError as e:
            return self.frame(title, subtitle, [t.card(ft.Column([
                ft.Text("No council on this computer yet", size=15, weight=ft.FontWeight.W_600),
                t.muted(str(e)),
                t.muted("A council seats one agent per model the pool runs. Once the pool serves its loadout, run "
                        "this in a terminal on the computer:"),
                t.mono("nanoborealis council apply", selectable=True),
            ], spacing=8))])
        rows: list[ft.Control] = []
        for seat in seats["seats"]:
            rows.append(ft.Container(ft.Row([
                ft.Icon(ft.Icons.STAR_ROUNDED if seat["chair"] else ft.Icons.SMART_TOY_OUTLINED, size=18,
                        color=t.ACCENT if seat["chair"] else t.VIOLET),
                ft.Column([ft.Row([ft.Text(seat["name"], size=14, weight=ft.FontWeight.W_600),
                                   t.badge("CHAIR", t.ACCENT) if seat["chair"] else ft.Container()], spacing=6),
                           t.muted(f"{seat['model']} · capability {seat['capability']:g}", size=12)],
                          spacing=2, expand=True),
                ft.Column([t.muted("TRUST", size=10.5), ft.Text(f"{seat['trust']:g}", size=15,
                                                                 color=t.GOOD if seat["trust"] > 1 else
                                                                 t.BAD if seat["trust"] < 1 else t.TEXT)],
                          spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END),
                ft.Column([t.muted("VOTE WEIGHT", size=10.5), ft.Text(f"{seat['weight']:g}", size=15)],
                          spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END),
            ], spacing=16), padding=ft.Padding.symmetric(vertical=6)))

        async def change(changes: dict[str, Any], message: str) -> None:
            try:
                await asyncio.to_thread(admin.hall, self.app.machine, "POST", "/settings", changes)
            except PairingError as e:
                self.toast(str(e))
            else:
                self.toast(message)
            await self.reload("council")

        continuous = settings["autonomy"] == "continuous"
        body = [
            t.card(ft.Column([t.muted(f"SEATS · {len(seats['seats'])}", size=11), *rows,
                              t.muted("A seat's vote counts by its model's capability times the trust it has earned: "
                                      "accepted work and your thumbs up raise it, rejected work lowers it, and it "
                                      "drifts back toward 1 over a month.", size=12)], spacing=4)),
            t.card(ft.Row([ft.Column([ft.Text("Work continuously", size=15, weight=ft.FontWeight.W_600),
                                      t.muted("Off: seats act when you give them a chat or a task. On: they also pick "
                                              "up open tasks from the board by themselves, every few minutes, when "
                                              "the council is idle.")], spacing=4, expand=True),
                           ft.Switch(value=continuous, active_color=t.ACCENT, on_change=lambda e: self.page.run_task(
                               change, {"autonomy": "continuous" if e.control.value else "on-task"},
                               "The council works continuously." if e.control.value else
                               "The council acts when given work."))])),
            t.card(ft.Row([ft.Column([ft.Text("Ask for outside advice", size=15, weight=ft.FontWeight.W_600),
                                      t.muted(f"When the council is stuck or split, it asks {settings['escalation_model']}"
                                              ", a free model on OpenRouter. Free models only: it never costs money.")],
                                     spacing=4, expand=True),
                           ft.Switch(value=bool(settings["escalation"]), active_color=t.ACCENT,
                                     on_change=lambda e: self.page.run_task(
                                         change, {"escalation": bool(e.control.value)},
                                         "Escalation is on." if e.control.value else "Escalation is off."))])),
        ]
        return self.frame(title, subtitle, body)

    # -- Logs --------------------------------------------------------------------------------------

    async def logs(self) -> ft.Control:
        lines = await asyncio.to_thread(admin.logs, self.app.machine, 400)
        text = "\n".join(lines) or "Nothing logged yet."
        box = ft.Container(ft.ListView([t.mono(text, size=11.5, selectable=True)], auto_scroll=True, padding=12),
                           height=max(300, (self.page.height or 800) - 220), bgcolor=t.SIDEBAR,
                           border_radius=t.RADIUS, border=ft.Border.all(1, t.LINE))
        return self.frame("Logs", "The agent's own log, newest at the bottom.", [box],
                          actions=[t.outline_button("Refresh", icon=ft.Icons.REFRESH_ROUNDED,
                                                    on_click=lambda _e: self.page.run_task(self.reload, "logs"))])

    # -- Settings ----------------------------------------------------------------------------------

    async def settings_page(self) -> ft.Control:
        settings = await self.load_settings()
        guard = agent_settings.free_only(settings)
        key = t.field("New OpenRouter key", password=True, hint="sk-or-v1-...")

        async def save_key(_event=None) -> None:
            value = (key.value or "").strip()
            if not value:
                return
            try:
                await asyncio.to_thread(admin.set_key, self.app.machine, value)
            except PairingError as e:
                self.toast(str(e))
                return
            key.value = ""
            self.toast("Saved. The agent restarted with the new key.")
            self.page.update()

        async def toggle_guard(event) -> None:
            try:
                await agent_settings.set_free_only(self.app.link, bool(event.control.value), self.settings)
            except LinkError as e:
                self.toast(str(e))
            await self.reload("settings")

        body = [
            t.card(ft.Column([
                ft.Row([ft.Column([ft.Text("Free models only", size=15, weight=ft.FontWeight.W_600),
                                   t.muted("OpenRouter refuses any request that would cost money, whatever model "
                                           "is chosen. Turn off only if you want to pay for a model.")],
                                  spacing=4, expand=True),
                        ft.Switch(value=guard, active_color=t.ACCENT, on_change=toggle_guard)]),
            ], spacing=8)),
            t.card(ft.Column([
                ft.Text("OpenRouter key", size=15, weight=ft.FontWeight.W_600),
                t.muted("The agent's key for OpenRouter. It never leaves this computer's agent; the app only "
                        "sends a new one there. Use a key with a low credit limit: the agent can read it."),
                ft.Row([ft.Container(key, expand=True), t.primary_button("Save", on_click=save_key)]),
            ], spacing=8)),
            t.card(ft.Column([
                ft.Text("This app", size=15, weight=ft.FontWeight.W_600),
                t.muted(f"NanoBorealis app {self.app.version}" + (", updated with the OS" if host.comes_with_os()
                                                                   else "")),
            ], spacing=6)),
        ]
        return self.frame("Settings", "Keys, spending, and the agent itself.", body)
