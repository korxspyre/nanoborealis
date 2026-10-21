#!/usr/bin/bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Lists every place a NanoBorealis user could still see Aurora's name or artwork: app menu
# entries, autostart, the welcome app, terminal greetings, ujust recipes, wallpapers, the
# login screen, boot menu, KDE metadata. Runs inside an image; prints file:line matches.
set -u
section() { printf '\n=== %s\n' "$1"; }
grepv() { grep -rIin --color=never -E "$1" "${@:2}" 2>/dev/null | grep -v -i 'nanoborealis' | cut -c1-220 | head -n 60; }

section "app menu and autostart entries naming Aurora (Name, GenericName, Comment, Icon)"
grepv '^(Name|GenericName|Comment|Icon|X-GNOME-FullName)(\[[a-z_@A-Z]+\])?=.*aurora' \
    /usr/share/applications /etc/xdg/autostart /usr/share/kservices6 /usr/share/kglobalaccel

section "desktop files whose id is Aurora's (shown as apps)"
ls /usr/share/applications /etc/xdg/autostart 2>/dev/null | grep -i aurora

section "Universal Blue / Aurora text shown in terminals (motd, fastfetch, ujust, bash/zsh)"
grepv 'aurora' /usr/share/ublue-os /etc/profile.d /etc/motd* /etc/issue* /usr/share/fish/vendor_conf.d 2>/dev/null

section "ujust recipes mentioning Aurora"
grepv 'aurora' /usr/share/ublue-os/just /usr/share/just 2>/dev/null

section "wallpaper packages"
ls /usr/share/wallpapers /usr/share/backgrounds 2>/dev/null

section "plasma look-and-feel, splash, and login screen themes"
ls /usr/share/plasma/look-and-feel /usr/share/sddm/themes /usr/share/plasma/plasmalogin* 2>/dev/null
grepv 'aurora' /etc/sddm.conf /etc/sddm.conf.d /usr/lib/sddm /etc/plasmalogin* /usr/share/plasmalogin* 2>/dev/null
grepv 'aurora' /etc/xdg/kdeglobals /etc/xdg/kscreenlockerrc /etc/xdg/plasma* /etc/xdg/ksplashrc /etc/xdg/kcm* 2>/dev/null

section "KDE welcome / about pages"
grepv 'aurora' /usr/share/plasma/plasma-welcome /usr/lib64/qt6/plugins/plasma/kcms 2>/dev/null | head -20
grepv 'aurora' /etc/xdg/kcm-about-distrorc /usr/share/kinfocenter 2>/dev/null

section "boot menu and os-release"
grep -i -E 'aurora' /usr/lib/os-release
grepv 'aurora' /usr/lib/bootupd /usr/share/grub /etc/default/grub 2>/dev/null

section "icons and pixmaps named aurora"
find /usr/share/icons /usr/share/pixmaps -iname '*aurora*' 2>/dev/null | head -30

section "flatpak/app store defaults and help links"
grepv 'getaurora|aurora' /usr/share/ublue-os/*.json /usr/share/metainfo /etc/flatpak 2>/dev/null | head -20
echo
echo "scan finished"
