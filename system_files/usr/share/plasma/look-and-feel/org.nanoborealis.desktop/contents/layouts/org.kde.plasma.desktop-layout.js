// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 korxspyre
// Part of the NanoBorealis look-and-feel package, which is GPL-2.0-or-later like Aurora's it's made from.
// The NanoBorealis desktop: the aurora wallpaper, and one floating dock centered at the bottom
// (Aurora and stock Plasma use a full-width bar).

var desktops = desktopsForActivity(currentActivity());
for (var i = 0; i < desktops.length; i++) {
    desktops[i].wallpaperPlugin = "org.kde.image";
    desktops[i].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    desktops[i].writeConfig("Image", "file:///usr/share/wallpapers/NanoBorealis/");
}

var panel = new Panel;
panel.location = "bottom";
panel.height = 2 * Math.ceil(gridUnit * 2.6 / 2);
panel.floating = true;
panel.alignment = "center";
panel.lengthMode = "fit";
panel.hiding = "none";

// The launcher gets the plain white star; the colored star tile is the agent's own app.
var launcher = panel.addWidget("org.kde.plasma.kickoff");
launcher.currentConfigGroup = ["General"];
launcher.writeConfig("icon", "distributor-logo-symbolic");
launcher.currentConfigGroup = ["Shortcuts"];
launcher.writeConfig("global", "Alt+F1");

var tasks = panel.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
    "applications:nanoborealis.desktop",
    "preferred://browser",
    "preferred://filemanager",
    "applications:org.kde.konsole.desktop"
]);

panel.addWidget("org.kde.plasma.marginsseparator");
panel.addWidget("org.kde.plasma.systemtray");
panel.addWidget("org.kde.plasma.digitalclock");
