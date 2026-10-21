# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The conversation, drawn as a coding agent's transcript.

Your messages sit on the right in a bubble; the agent's answers read as plain text. Each tool call
is one compact line (what it did, to what, how it went) that opens up to show the command's
output, or the change to a file as a diff. Thinking folds away into "Thought for 8s". While the
agent works, a status line says so, with the time so far and how to stop it.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import random
import time
from typing import Any, Callable

import flet as ft

import theme as t

WORKING_VERBS = ["Working", "Thinking", "Pondering", "Figuring", "Tinkering", "Reasoning", "Brewing",
                 "Weaving", "Charting", "Glimmering", "Stargazing", "Orbiting"]


def clip(text: str, limit: int = 6000) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n... ({len(text) - limit:,} more characters)"


def markdown(text: str = "", on_tap_link: Callable | None = None) -> ft.Markdown:
    return ft.Markdown(
        text, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK, auto_follow_links=on_tap_link is None,
        on_tap_link=on_tap_link,
        code_style_sheet=ft.MarkdownStyleSheet(
            code_text_style=ft.TextStyle(font_family=t.MONO, size=12.5, height=1.5),
            codeblock_padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            codeblock_decoration=ft.BoxDecoration(border_radius=t.RADIUS, bgcolor=t.SIDEBAR,
                                                  border=ft.Border.all(1, t.LINE))),
        md_style_sheet=ft.MarkdownStyleSheet(
            p_text_style=ft.TextStyle(size=14.5, height=1.55, color=t.TEXT),
            code_text_style=ft.TextStyle(font_family=t.MONO, size=12.5, color=t.ACCENT_HI, bgcolor=t.RAISED),
            a_text_style=ft.TextStyle(color=t.ACCENT_HI, decoration=ft.TextDecoration.UNDERLINE),
            blockquote_decoration=ft.BoxDecoration(border=ft.Border.only(left=ft.BorderSide(3, t.LINE_HI))),
            horizontal_rule_decoration=ft.BoxDecoration(border=ft.Border.only(top=ft.BorderSide(1, t.LINE)))),
    )


# -- Messages -----------------------------------------------------------------------------------


def user_message(text: str, attachments: list[str] | None = None) -> ft.Control:
    body: list[ft.Control] = []
    for name in attachments or []:
        body.append(t.chip(name, icon=ft.Icons.ATTACH_FILE_ROUNDED, bgcolor=t.RAISED_HI))
    if text:
        body.append(ft.Text(text, size=14.5, color=t.TEXT, selectable=True))
    return ft.Row(alignment=ft.MainAxisAlignment.END, controls=[
        ft.Container(
            content=ft.Column(body, spacing=8, tight=True),
            bgcolor=t.RAISED, border_radius=ft.BorderRadius.only(top_left=16, top_right=16, bottom_left=16,
                                                                 bottom_right=6),
            padding=ft.Padding.symmetric(horizontal=16, vertical=11), margin=ft.Margin.only(left=72, top=6),
            # As wide as its text needs and no wider than the row: a long message wraps (on a phone
            # it ran off the screen), a short one stays a small bubble on the right.
            expand=True, expand_loose=True,
        ),
    ], wrap=False)


def split_code(text: str) -> list[tuple[str, str, str]]:
    """Markdown as ("text", "", body) and ("code", language, body) parts, fenced blocks apart."""
    parts: list[tuple[str, str, str]] = []
    lines, buffer, language, fence = text.split("\n"), [], "", None
    for line in lines:
        stripped = line.strip()
        if fence is None and stripped.startswith(("```", "~~~")):
            if buffer:
                parts.append(("text", "", "\n".join(buffer)))
            buffer, fence, language = [], stripped[:3], stripped[3:].strip().split(" ")[0]
        elif fence is not None and stripped.startswith(fence) and not stripped[3:].strip():
            parts.append(("code", language, "\n".join(buffer)))
            buffer, fence, language = [], None, ""
        else:
            buffer.append(line)
    if buffer:
        parts.append(("code" if fence else "text", language, "\n".join(buffer)))
    return parts


def code_block(language: str, code: str, on_copy: Callable[[str], Any] | None) -> ft.Control:
    """A code block with its language and a copy button."""
    head = ft.Row([ft.Text(language or "code", size=11.5, color=t.FAINT, font_family=t.MONO, expand=True),
                   t.icon_button(ft.Icons.CONTENT_COPY_ROUNDED, "Copy the code", size=14,
                                 on_click=(lambda _e: on_copy(code)) if on_copy else None)],
                  height=30, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    body = ft.Markdown(f"```{language}\n{code}\n```", selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                       code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
                       code_style_sheet=ft.MarkdownStyleSheet(
                           code_text_style=ft.TextStyle(font_family=t.MONO, size=12.5, height=1.5),
                           codeblock_padding=ft.Padding.only(left=16, right=16, bottom=14, top=2),
                           codeblock_decoration=ft.BoxDecoration(bgcolor=t.SIDEBAR)))
    return ft.Container(ft.Column([ft.Container(head, padding=ft.Padding.only(left=14, right=4)), body], spacing=0),
                        bgcolor=t.SIDEBAR, border_radius=t.RADIUS, border=ft.Border.all(1, t.LINE),
                        clip_behavior=ft.ClipBehavior.HARD_EDGE)


class Answer:
    """The agent's text, streaming in as one markdown view; once it's done, code blocks get their
    own header and copy button, and the whole answer can be copied from the row under it."""

    def __init__(self, text: str = "", on_copy: Callable[[str], Any] | None = None,
                 on_tap_link: Callable | None = None):
        self.on_copy, self.on_tap_link = on_copy, on_tap_link
        self.md = markdown(text, on_tap_link)
        self.body = ft.Column([self.md], spacing=10)
        self.copy = t.icon_button(ft.Icons.CONTENT_COPY_ROUNDED, "Copy the answer", size=15,
                                  on_click=(lambda _e: on_copy(self.md.value or "")) if on_copy else None)
        self.actions = ft.Row([self.copy], spacing=0, visible=bool(text), height=30)
        self.view = ft.Column([self.body, self.actions], spacing=2)

    def set(self, text: str) -> None:
        self.md.value = text

    def finish(self) -> None:
        text = self.md.value or ""
        self.actions.visible = bool(text.strip())
        parts = split_code(text)
        if any(kind == "code" for kind, _, _ in parts):
            self.body.controls = [code_block(language, body, self.on_copy) if kind == "code"
                                  else markdown(body, self.on_tap_link)
                                  for kind, language, body in parts if body.strip() or kind == "code"]


class Thinking:
    """The model's reasoning: open while it streams, then folded into "Thought for 8s"."""

    def __init__(self, text: str = "", done: bool = False):
        self.started = time.monotonic()
        self.title = ft.Text("Thought" if done else "Thinking...", size=13, color=t.MUTED, italic=not done)
        self.chevron = ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=16, color=t.FAINT)
        self.body = ft.Text(text, size=13, color=t.MUTED, selectable=True, italic=True)
        self.body_box = ft.Container(self.body, visible=False,
                                     padding=ft.Padding.only(left=12, right=12, top=2, bottom=8),
                                     border=ft.Border.only(left=ft.BorderSide(2, t.LINE_HI)),
                                     margin=ft.Margin.only(left=8))
        self.view = ft.Column(spacing=2, controls=[
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED, size=15, color=t.FAINT), self.title,
                                self.chevron], spacing=6, tight=True),
                on_click=self.toggle, border_radius=8, padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            ),
            self.body_box,
        ])

    def append(self, text: str) -> None:
        self.body.value = (self.body.value or "") + text

    def finish(self) -> None:
        seconds = time.monotonic() - self.started
        self.title.value = f"Thought for {seconds:.0f}s" if seconds >= 1 else "Thought"
        self.title.italic = False

    async def toggle(self, _event=None) -> None:
        self.body_box.visible = not self.body_box.visible
        self.chevron.icon = ft.Icons.EXPAND_MORE_ROUNDED if self.body_box.visible else ft.Icons.CHEVRON_RIGHT_ROUNDED
        self.view.update()


def note(text: str, icon: str = ft.Icons.INFO_OUTLINE_ROUNDED, color: str = t.MUTED) -> ft.Control:
    return ft.Row([ft.Icon(icon, size=15, color=color),
                   ft.Text(text, size=12.5, color=color, expand=True, selectable=True)], spacing=8)


def error(text: str) -> ft.Control:
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.10, t.BAD), border_radius=t.RADIUS,
        border=ft.Border.all(1, ft.Colors.with_opacity(0.35, t.BAD)),
        padding=ft.Padding.symmetric(horizontal=12, vertical=9),
        content=ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=16, color=t.BAD),
                        ft.Text(text, size=13, color=t.TEXT, expand=True, selectable=True)], spacing=8),
    )


def turn_footer(seconds: float | None, model: str | None, usage: dict[str, Any] | None = None) -> ft.Control:
    """Under each answer: how long it took, on which model, and how many tokens it used."""
    parts = []
    if seconds is not None:
        parts.append(f"{seconds:.1f}s")
    if model:
        parts.append(model)
    if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int) and usage["total_tokens"]:
        parts.append(f"{usage['total_tokens']:,} tokens")
    return ft.Text(" · ".join(parts), size=11.5, color=t.FAINT)


# -- Tool calls ---------------------------------------------------------------------------------


def _first(args: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def describe(name: str, args: dict[str, Any]) -> tuple[str, str, str]:
    """(verb, target, icon) for a tool call: "Run", "npm test", terminal icon."""
    path = _first(args, "path", "file_path", "filename")
    if name == "exec":
        return "Run", _first(args, "command", "cmd"), ft.Icons.TERMINAL_ROUNDED
    if name == "read_file":
        return "Read", path, ft.Icons.DESCRIPTION_OUTLINED
    if name == "write_file":
        return "Write", path, ft.Icons.NOTE_ADD_OUTLINED
    if name in ("edit_file", "edit", "apply_patch"):
        return "Edit", path, ft.Icons.EDIT_OUTLINED
    if name == "list_dir":
        return "List", path or ".", ft.Icons.FOLDER_OPEN_OUTLINED
    if name == "grep":
        return "Search", _first(args, "pattern", "query"), ft.Icons.MANAGE_SEARCH_ROUNDED
    if name in ("find_files", "glob"):
        return "Find", _first(args, "query", "glob", "pattern", "path"), ft.Icons.TRAVEL_EXPLORE_ROUNDED
    if name == "web_search":
        return "Search the web", _first(args, "query"), ft.Icons.PUBLIC_ROUNDED
    if name == "web_fetch":
        return "Fetch", _first(args, "url"), ft.Icons.LANGUAGE_ROUNDED
    if name == "spawn":
        return "Subagent", _first(args, "label", "task"), ft.Icons.CALL_SPLIT_ROUNDED
    if name == "cron":
        return "Schedule", _first(args, "message", "action", "cron_expr"), ft.Icons.SCHEDULE_ROUNDED
    if name == "message":
        return "Message", _first(args, "content", "text"), ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED
    if name.startswith("mcp_"):
        return name[4:].replace("_", " "), _compact(args), ft.Icons.EXTENSION_OUTLINED
    return name.replace("_", " ").capitalize(), _compact(args), ft.Icons.BUILD_CIRCLE_OUTLINED


def _compact(args: Any) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v if isinstance(v, (int, float)) else json.dumps(v, ensure_ascii=False)}"
                         for k, v in args.items())
    return "" if args is None else str(args)


def edit_texts(args: dict[str, Any]) -> tuple[str, str] | None:
    old = _first(args, "old_text", "old_string", "old", "search")
    new = args.get("new_text", args.get("new_string", args.get("new", args.get("replace"))))
    if old or isinstance(new, str) and new:
        return old, new if isinstance(new, str) else ""
    return None


def diff_counts(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def diff_view(old: str, new: str, limit: int = 400) -> ft.Control:
    """A unified diff: removed lines on red, added on green, like any code review."""
    rows: list[ft.Control] = []
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3))[2:]
    for line in lines[:limit]:
        if line.startswith("@@"):
            rows.append(ft.Container(t.mono(line, size=11.5, color=t.FAINT),
                                     padding=ft.Padding.symmetric(horizontal=10, vertical=3)))
            continue
        sign, rest = (line[:1], line[1:]) if line else (" ", "")
        fg, bg = {"+": (t.ADDED_FG, t.ADDED_BG), "-": (t.REMOVED_FG, t.REMOVED_BG)}.get(sign, (t.MUTED, None))
        rows.append(ft.Container(
            content=ft.Row([ft.Text(sign if sign in "+-" else " ", size=12, color=fg, font_family=t.MONO, width=12),
                            ft.Text(rest, size=12, color=fg if sign in "+-" else t.TEXT, font_family=t.MONO,
                                    selectable=True, expand=True, no_wrap=False)],
                           spacing=4, vertical_alignment=ft.CrossAxisAlignment.START),
            bgcolor=bg, padding=ft.Padding.symmetric(horizontal=10, vertical=1),
        ))
    if len(lines) > limit:
        rows.append(note(f"{len(lines) - limit} more lines"))
    if not rows:
        rows.append(note("No change"))
    return ft.Container(ft.Column(rows, spacing=0), bgcolor=t.SIDEBAR, border_radius=t.RADIUS,
                        border=ft.Border.all(1, t.LINE), padding=ft.Padding.symmetric(vertical=6),
                        clip_behavior=ft.ClipBehavior.HARD_EDGE)


def unified_view(diff: str, limit: int = 1500) -> ft.Control:
    """Git's diff output, colored: file headers, hunks, removed and added lines."""
    rows: list[ft.Control] = []
    lines = diff.splitlines()
    for line in lines[:limit]:
        if line.startswith(("diff --git", "index ", "--- ", "+++ ", "new file", "deleted file", "similarity",
                            "rename ")):
            if line.startswith("+++ "):
                rows.append(ft.Container(t.mono(line[4:].removeprefix("b/"), size=12, color=t.TEXT,
                                                weight=ft.FontWeight.W_600),
                                         padding=ft.Padding.only(left=10, right=10, top=8, bottom=2)))
            continue
        if line.startswith("@@"):
            rows.append(ft.Container(t.mono(line, size=11.5, color=t.FAINT),
                                     padding=ft.Padding.symmetric(horizontal=10, vertical=3)))
            continue
        sign = line[:1]
        fg, bg = {"+": (t.ADDED_FG, t.ADDED_BG), "-": (t.REMOVED_FG, t.REMOVED_BG)}.get(sign, (t.TEXT, None))
        rows.append(ft.Container(t.mono(line or " ", size=12, color=fg, selectable=True), bgcolor=bg,
                                 padding=ft.Padding.symmetric(horizontal=10, vertical=1)))
    if len(lines) > limit:
        rows.append(note(f"{len(lines) - limit} more lines"))
    if not rows:
        rows.append(note("No differences"))
    return ft.Container(ft.Column(rows, spacing=0), bgcolor=t.SIDEBAR, border_radius=t.RADIUS,
                        border=ft.Border.all(1, t.LINE), padding=ft.Padding.symmetric(vertical=6),
                        clip_behavior=ft.ClipBehavior.HARD_EDGE)


def output_block(text: str, prompt: str | None = None) -> ft.Control:
    body: list[ft.Control] = []
    if prompt:
        body.append(t.mono(f"$ {prompt}", color=t.ACCENT_HI, selectable=True))
    if text:
        body.append(t.mono(clip(text), color=t.TEXT, selectable=True))
    return ft.Container(
        content=ft.Column([ft.Column(body, spacing=6, scroll=ft.ScrollMode.AUTO)], height=None),
        bgcolor=t.SIDEBAR, border_radius=t.RADIUS, border=ft.Border.all(1, t.LINE),
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
    )


class ToolCard:
    """One tool call, as one line: a spinner, then a check or a cross; what it did and to what.
    Clicking it shows the details: output, or the change as a diff."""

    def __init__(self, name: str, args: Any, hint: str = ""):
        self.name = name
        self.args = args if isinstance(args, dict) else {}
        self.started = time.monotonic()
        verb, target, icon = describe(name, self.args)
        if not target and hint:
            target = hint.strip().splitlines()[0]
        self.result: str = ""
        self.failed = False
        self.diff_text: str | None = None  # nanobot's own unified diff of the edit, when it sent one
        self.status = ft.Container(ft.ProgressRing(width=12, height=12, stroke_width=1.6, color=t.ACCENT),
                                   width=18, height=18, alignment=ft.Alignment.CENTER)
        self.meta = ft.Row(spacing=6, tight=True)
        texts = self.edit_counts()
        if texts:
            added, removed = texts
            self.meta.controls = [ft.Text(f"+{added}", size=11.5, color=t.ADDED_FG, font_family=t.MONO),
                                  ft.Text(f"−{removed}", size=11.5, color=t.REMOVED_FG, font_family=t.MONO)]
        self.chevron = ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=16, color=t.FAINT)
        self.details = ft.Container(visible=False, padding=ft.Padding.only(left=30, top=4, bottom=6))
        self.header = ft.Container(
            content=ft.Row([
                self.status,
                ft.Icon(icon, size=15, color=t.MUTED),
                ft.Text(verb, size=13, color=t.TEXT, weight=ft.FontWeight.W_600, no_wrap=True),
                ft.Text(target, size=12.5, color=t.MUTED, font_family=t.MONO, no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                self.meta, self.chevron,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            on_click=self.toggle, border_radius=8, padding=ft.Padding.symmetric(horizontal=6, vertical=5),
            ink=True,
        )
        self.view = ft.Column([self.header, self.details], spacing=0)

    def set_edit(self, edit: dict[str, Any]) -> None:
        """nanobot's file_edit report for this call: its line counts and, once done, its diff."""
        added, deleted = edit.get("added"), edit.get("deleted")
        if isinstance(added, int) and isinstance(deleted, int):
            self.meta.controls = [ft.Text(f"+{added}", size=11.5, color=t.ADDED_FG, font_family=t.MONO),
                                  ft.Text(f"−{deleted}", size=11.5, color=t.REMOVED_FG, font_family=t.MONO)]
        diff = edit.get("diff")
        if isinstance(diff, dict) and isinstance(diff.get("text"), str) and diff["text"].strip():
            self.diff_text = diff["text"]
            self.details.content = None  # rebuilt with the diff next time it's opened
        if edit.get("status") == "error":
            self.finish(str(edit.get("error") or "The edit failed."), True)
        elif edit.get("status") == "done" and not self.result:
            self.finish(f"{'Deleted' if edit.get('operation') == 'delete' else 'Saved'} {edit.get('path', '')}")

    def edit_counts(self) -> tuple[int, int] | None:
        if self.name in ("edit_file", "edit"):
            texts = edit_texts(self.args)
            return diff_counts(*texts) if texts else None
        if self.name == "write_file":
            content = self.args.get("content")
            return (len(content.splitlines()), 0) if isinstance(content, str) else None
        return None

    def finish(self, result: Any, failed: bool = False) -> None:
        self.result = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        self.failed = failed
        self.status.content = ft.Icon(ft.Icons.CLOSE_ROUNDED if failed else ft.Icons.CHECK_ROUNDED, size=15,
                                      color=t.BAD if failed else t.GOOD)
        seconds = time.monotonic() - self.started
        if seconds >= 2:
            self.meta.controls.append(ft.Text(f"{seconds:.0f}s", size=11.5, color=t.FAINT))

    async def toggle(self, _event=None) -> None:
        if not self.details.visible and self.details.content is None:
            self.details.content = self.detail_view()
        self.details.visible = not self.details.visible
        self.chevron.icon = ft.Icons.EXPAND_MORE_ROUNDED if self.details.visible else ft.Icons.CHEVRON_RIGHT_ROUNDED
        self.view.update()

    def detail_view(self) -> ft.Control:
        name, args, result = self.name, self.args, self.result
        parts: list[ft.Control] = []
        if self.diff_text:
            parts.append(unified_view(self.diff_text))
            if self.failed and result:
                parts.append(output_block(result))
        elif name == "exec":
            parts.append(output_block(result, _first(args, "command", "cmd")))
        elif name in ("edit_file", "edit") and edit_texts(args):
            parts.append(diff_view(*edit_texts(args)))
            if self.failed and result:
                parts.append(output_block(result))
        elif name == "write_file" and isinstance(args.get("content"), str):
            parts.append(diff_view("", args["content"]))
        else:
            if args:
                parts.append(output_block(json.dumps(args, ensure_ascii=False, indent=2)))
            if result:
                parts.append(output_block(result))
        if not parts:
            parts.append(note("No output"))
        return ft.Column(parts, spacing=6)


def goal_card(state: dict[str, Any]) -> ft.Control:
    """The agent's goal for this chat (/goal, or one it set itself): what, and how it's going."""
    status = str(state.get("status") or ("active" if state.get("active") else "done"))
    color = {"active": t.ACCENT, "completed": t.GOOD, "complete": t.GOOD, "blocked": t.WARN,
             "cancelled": t.MUTED}.get(status, t.ACCENT)
    body: list[ft.Control] = [ft.Row([ft.Icon(ft.Icons.FLAG_OUTLINED, size=16, color=color),
                                      ft.Text("Goal", size=12.5, color=t.MUTED), t.badge(status.upper(), color)],
                                     spacing=8)]
    if state.get("objective"):
        body.append(ft.Text(str(state["objective"]), size=14, color=t.TEXT, selectable=True))
    summary = state.get("recap") or state.get("ui_summary")
    if summary:
        body.append(ft.Text(str(summary), size=13, color=t.MUTED, selectable=True))
    return ft.Container(ft.Column(body, spacing=6), padding=14, bgcolor=t.PANEL, border_radius=t.RADIUS_LG,
                        border=ft.Border.all(1, ft.Colors.with_opacity(0.5, color)))


# -- While it works -----------------------------------------------------------------------------


class StatusLine:
    """"✦ Pondering... 12s · Esc to stop": the agent is working, for how long, and how to stop it."""

    def __init__(self) -> None:
        self.star = ft.Container(ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, size=16, color=t.ACCENT),
                                 animate_opacity=ft.Animation(700, ft.AnimationCurve.EASE_IN_OUT), opacity=1)
        self.verb = ft.Text("Working...", size=13, color=t.ACCENT_HI, weight=ft.FontWeight.W_500)
        self.elapsed = ft.Text("", size=12.5, color=t.FAINT)
        self.view = ft.Row([self.star, self.verb, self.elapsed], spacing=8, visible=False)
        self.started = 0.0
        self._task: asyncio.Task | None = None

    def start(self, verb: str | None = None) -> None:
        if self.view.visible:
            return
        self.started = time.monotonic()
        self.verb.value = f"{verb or random.choice(WORKING_VERBS)}..."
        self.elapsed.value = "· Esc to stop"
        self.view.visible = True
        self._task = asyncio.ensure_future(self._tick())

    def set_verb(self, verb: str) -> None:
        self.verb.value = f"{verb}..."

    def stop(self) -> None:
        self.view.visible = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _tick(self) -> None:
        try:
            while self.view.visible:
                await asyncio.sleep(0.8)
                seconds = int(time.monotonic() - self.started)
                self.star.opacity = 0.35 if self.star.opacity == 1 else 1
                self.elapsed.value = f"{seconds}s · Esc to stop" if seconds else "· Esc to stop"
                try:
                    self.view.update()
                except Exception:
                    return  # the chat was closed
        except asyncio.CancelledError:
            pass
