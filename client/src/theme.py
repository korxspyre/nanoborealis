# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The app's look: NanoBorealis's own colors, laid out as a coding agent's workspace.

The colors are the OS's color scheme (system_files/usr/share/color-schemes/NanoBorealis.colors):
night-sky navy surfaces, soft white text and an aurora teal, so on a NanoBorealis computer the app
looks like the rest of the desktop, and on phones and PCs it still looks like NanoBorealis.
"""

from __future__ import annotations

import flet as ft

# Surfaces, darkest to lightest.
SIDEBAR = "#0C1325"  # Header: the sidebar and the title strip
BG = "#0B1222"  # View: the conversation
PANEL = "#111A2E"  # Window: side panels, dialogs
RAISED = "#1A2640"  # Button: the composer, your messages, cards
RAISED_HI = "#22314F"  # hover, selected rows
LINE = "#1F2C48"  # hairlines between areas
LINE_HI = "#2B3B5E"

TEXT = "#E6EDF7"
MUTED = "#8B9AB3"
FAINT = "#5E6E8C"

ACCENT = "#2DD4BF"  # aurora teal
ACCENT_HI = "#5EEAD4"
ON_ACCENT = "#04221F"
ACCENT_SOFT = "#123B40"  # teal on navy, for quiet highlights

GOOD = "#34D399"
BAD = "#FB7185"
WARN = "#FBBF24"
VIOLET = "#C4B5FD"

ADDED_BG = "#0F3B2E"
ADDED_FG = "#86EFAC"
REMOVED_BG = "#431C2A"
REMOVED_FG = "#FDA4AF"

# Bundled in assets/fonts (SIL OFL): Flutter doesn't resolve a generic "monospace" family.
MONO = "JetBrains Mono"

READABLE = 820  # widest the conversation column gets
NARROW = 760  # below this width the sidebar moves into a drawer
SIDEBAR_WIDTH = 272
PANEL_WIDTH = 460

RADIUS = 10
RADIUS_LG = 16


def app_theme(font: str | None = None) -> ft.Theme:
    return ft.Theme(
        font_family=font,
        color_scheme=ft.ColorScheme(
            primary=ACCENT, on_primary=ON_ACCENT, primary_container=ACCENT_SOFT, on_primary_container=ACCENT_HI,
            secondary=ACCENT_HI, on_secondary=ON_ACCENT, secondary_container=RAISED, on_secondary_container=TEXT,
            tertiary=VIOLET, on_tertiary=ON_ACCENT,
            error=BAD, on_error=ON_ACCENT,
            surface=BG, on_surface=TEXT, on_surface_variant=MUTED,
            surface_container_lowest=SIDEBAR, surface_container_low=PANEL, surface_container=RAISED,
            surface_container_high=RAISED_HI, surface_container_highest=LINE_HI,
            surface_tint=ft.Colors.TRANSPARENT,
            outline=LINE_HI, outline_variant=LINE,
            inverse_surface=TEXT, on_inverse_surface=BG, inverse_primary=ON_ACCENT,
            shadow="#000000", scrim="#000000",
        ),
        scrollbar_theme=ft.ScrollbarTheme(thickness=6, radius=3, thumb_color=LINE_HI,
                                          main_axis_margin=2, cross_axis_margin=2),
        tooltip_theme=ft.TooltipTheme(text_style=ft.TextStyle(size=12, color=TEXT),
                                      decoration=ft.BoxDecoration(bgcolor=RAISED_HI, border_radius=6),
                                      wait_duration=400),
        divider_theme=ft.DividerTheme(color=LINE, thickness=1, space=1),
        splash_color=ft.Colors.TRANSPARENT,
        visual_density=ft.VisualDensity.COMPACT,
    )


def text(value: str = "", size: float = 14, color: str = TEXT, weight: ft.FontWeight | None = None,
         **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=color, weight=weight, **kwargs)


def muted(value: str = "", size: float = 12.5, **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=MUTED, **kwargs)


def mono(value: str = "", size: float = 12.5, color: str = TEXT, **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=color, font_family=MONO, **kwargs)


def icon_button(icon: str, tooltip: str, on_click=None, size: float = 18, color: str = MUTED,
                selected: bool = False, **kwargs) -> ft.IconButton:
    return ft.IconButton(
        icon=icon, icon_size=size, tooltip=tooltip, on_click=on_click,
        icon_color=ft.Colors.with_opacity(0.35, FAINT) if kwargs.get("disabled") else ACCENT if selected else color,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), padding=6,
                             bgcolor={ft.ControlState.HOVERED: RAISED_HI,
                                      ft.ControlState.DEFAULT: RAISED if selected else ft.Colors.TRANSPARENT}),
        **kwargs,
    )


def primary_button(label: str, on_click=None, icon: str | None = None, **kwargs) -> ft.FilledButton:
    return ft.FilledButton(
        label, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(bgcolor={ft.ControlState.DISABLED: RAISED, ft.ControlState.DEFAULT: ACCENT},
                             color={ft.ControlState.DISABLED: FAINT, ft.ControlState.DEFAULT: ON_ACCENT},
                             shape=ft.RoundedRectangleBorder(radius=RADIUS),
                             padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                             text_style=ft.TextStyle(size=13.5, weight=ft.FontWeight.W_600)),
        **kwargs,
    )


def quiet_button(label: str, on_click=None, icon: str | None = None, color: str = TEXT, **kwargs) -> ft.TextButton:
    return ft.TextButton(
        label, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(color=color, icon_color=color,
                             bgcolor={ft.ControlState.HOVERED: RAISED_HI, ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                             shape=ft.RoundedRectangleBorder(radius=RADIUS),
                             padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                             text_style=ft.TextStyle(size=13.5)),
        **kwargs,
    )


def outline_button(label: str, on_click=None, icon: str | None = None, **kwargs) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        label, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(color=TEXT, icon_color=MUTED, side=ft.BorderSide(1, LINE_HI),
                             bgcolor={ft.ControlState.HOVERED: RAISED_HI, ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                             shape=ft.RoundedRectangleBorder(radius=RADIUS),
                             padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                             text_style=ft.TextStyle(size=13)),
        **kwargs,
    )


def chip(label: str, color: str = MUTED, bgcolor: str = RAISED, icon: str | None = None, tooltip: str | None = None,
         on_click=None, size: float = 12) -> ft.Container:
    row = [ft.Icon(icon, size=size + 2, color=color)] if icon else []
    row.append(ft.Text(label, size=size, color=color, no_wrap=True))
    return ft.Container(
        content=ft.Row(row, spacing=5, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=bgcolor, border_radius=20, padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        tooltip=tooltip, on_click=on_click, ink=on_click is not None,
    )


def badge(label: str, color: str, bgcolor: str | None = None) -> ft.Container:
    return ft.Container(
        content=ft.Text(label, size=10.5, color=color, weight=ft.FontWeight.W_600, no_wrap=True),
        bgcolor=bgcolor or ft.Colors.with_opacity(0.14, color), border_radius=6,
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
    )


def card(content: ft.Control, padding: float = 16, bgcolor: str = PANEL, **kwargs) -> ft.Container:
    return ft.Container(content=content, padding=padding, bgcolor=bgcolor, border_radius=RADIUS_LG,
                        border=ft.Border.all(1, LINE), **kwargs)


def field(label: str | None = None, value: str = "", hint: str | None = None, password: bool = False,
          multiline: bool = False, **kwargs) -> ft.TextField:
    return ft.TextField(
        label=label, value=value, hint_text=hint, password=password, can_reveal_password=password,
        multiline=multiline, min_lines=3 if multiline else None, max_lines=12 if multiline else 1,
        text_size=13.5, border_radius=RADIUS, border_color=LINE_HI, focused_border_color=ACCENT,
        bgcolor=BG, cursor_color=ACCENT, label_style=ft.TextStyle(color=MUTED, size=13),
        hint_style=ft.TextStyle(color=FAINT, size=13.5), content_padding=ft.Padding.symmetric(horizontal=12, vertical=12),
        **kwargs,
    )


def section_title(title: str, subtitle: str | None = None) -> ft.Column:
    controls: list[ft.Control] = [ft.Text(title, size=20, weight=ft.FontWeight.W_600, color=TEXT)]
    if subtitle:
        controls.append(ft.Text(subtitle, size=13, color=MUTED))
    return ft.Column(controls, spacing=4)
