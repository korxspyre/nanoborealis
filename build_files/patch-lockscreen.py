#!/usr/bin/python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Puts the NanoBorealis dashboard into Plasma's lock screen (LockScreenUi.qml of the desktop
shell): between the wallpaper and the unlock prompt, shown while nobody is using the computer and
faded out as soon as someone does, when the usual prompt appears. KDE's own clock steps aside
while the dashboard is up. Fails loudly if Plasma's file changed shape, so a Plasma update can't
quietly drop it.

    patch-lockscreen.py /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen
"""

import sys
from pathlib import Path

folder = Path(sys.argv[1])
ui = folder / "LockScreenUi.qml"
text = ui.read_text()
if "NanoBorealisDashboard" in text:
    sys.exit(0)


def swap(old: str, new: str) -> None:
    global text
    if text.count(old) != 1:
        sys.exit(f"patch-lockscreen: Plasma's lock screen changed; can't find: {old!r}")
    text = text.replace(old, new)


# Above the wallpaper, below the clock and the prompt.
swap("""        DropShadow {
            id: clockShadow""", """        Loader {
            id: nanoborealisDashboard
            anchors.fill: parent
            source: "NanoBorealisDashboard.qml"
            opacity: lockScreenRoot.uiVisible ? 0 : 1
            visible: opacity > 0
            Behavior on opacity {
                OpacityAnimator {
                    duration: Kirigami.Units.longDuration
                }
            }
        }

        DropShadow {
            id: clockShadow""")
swap("visible: !lockScreenUi.softwareRendering && config.alwaysShowClock",
     "visible: !lockScreenUi.softwareRendering && config.alwaysShowClock && nanoborealisDashboard.status !== Loader.Ready")
swap("visible: y > 0 && config.alwaysShowClock",
     "visible: y > 0 && config.alwaysShowClock && (nanoborealisDashboard.status !== Loader.Ready || lockScreenRoot.uiVisible)")
ui.write_text(text)
print(f"patch-lockscreen: the NanoBorealis dashboard is in {ui}")
