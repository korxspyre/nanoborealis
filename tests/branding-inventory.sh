#!/usr/bin/bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Prints the branding-relevant parts of an image (upstream Aurora or a NanoBorealis build):
# look-and-feel packages, wallpapers, boot splash, login theme, logos, fastfetch, and the
# config that selects them. Upstream's output is the baseline the reskin is checked against.
set -u
section() { printf '\n== %s\n' "$1"; }

section "os-release"
grep -E '^(NAME|PRETTY_NAME|ID|VARIANT_ID|LOGO|VERSION_ID)=' /usr/lib/os-release

section "look-and-feel packages"
ls /usr/share/plasma/look-and-feel/ 2>/dev/null
section "default look-and-feel and theme settings (/etc/xdg)"
grep -rH -E 'LookAndFeelPackage|ColorScheme|Theme=|Image=|widgetStyle|iconTheme|cursorTheme' \
    /etc/xdg/kdeglobals /etc/xdg/plasmarc /etc/xdg/kscreenlockerrc /etc/xdg/ksplashrc 2>/dev/null
section "look-and-feel defaults"
for d in /usr/share/plasma/look-and-feel/*/contents/defaults; do echo "-- $d"; cat "$d"; done 2>/dev/null | head -80
section "wallpapers"
ls /usr/share/wallpapers/ 2>/dev/null
section "plymouth"
plymouth-set-default-theme 2>/dev/null || grep -h Theme /etc/plymouth/plymouthd.conf /usr/share/plymouth/plymouthd.defaults 2>/dev/null
ls /usr/share/plymouth/themes/ 2>/dev/null
section "sddm"
ls /usr/share/sddm/themes/ 2>/dev/null
cat /etc/sddm.conf.d/*.conf /usr/lib/sddm/sddm.conf.d/*.conf 2>/dev/null | grep -v '^#' | grep -v '^$' | head -40
for f in /usr/share/sddm/themes/*/theme.conf.user; do echo "-- $f"; cat "$f"; done 2>/dev/null
section "logos and icons"
ls /usr/share/pixmaps/ 2>/dev/null | grep -i -E 'logo|aurora|fedora|system' | head -20
find /usr/share/icons/hicolor -iname '*aurora*' -o -iname '*distributor*' 2>/dev/null | head -20
section "fastfetch"
ls /usr/share/ublue-os/ 2>/dev/null | head -30
find /usr/share/ublue-os /etc/fastfetch /usr/share/fastfetch -maxdepth 2 -name '*.jsonc' 2>/dev/null | head
for f in $(find /usr/share/ublue-os -maxdepth 2 -name '*fastfetch*.jsonc' 2>/dev/null | head -2); do echo "-- $f"; head -40 "$f"; done
section "welcome and first boot"
ls /usr/share/applications/ 2>/dev/null | grep -i -E 'welcome|setup|aurora|ublue' | head -20
ls /etc/xdg/autostart/ 2>/dev/null | head -30
section "kickoff (app launcher) icon"
grep -rl -i 'kickoff' /usr/share/plasma/look-and-feel/*/contents/layouts 2>/dev/null | head -5
grep -rh -o -E 'icon[^,;]{0,80}' /usr/share/plasma/look-and-feel/*/contents/layouts/*.js 2>/dev/null | head -10

# --- What a brand-new account actually gets ---------------------------------------------------
section "display manager and first-boot setup"
readlink -f /etc/systemd/system/display-manager.service 2>/dev/null
ls /usr/lib/systemd/system/ 2>/dev/null | grep -i -E 'sddm|plasmalogin|plasma-login|plasma-setup|initial-setup|gdm'
rpm -qa 2>/dev/null | grep -i -E 'plasma-setup|initial-setup|plasma-login|plasmalogin|sddm|plasma-welcome|kde-settings|aurora|ublue|backgrounds|wallpaper' | sort
for p in plasma-setup plasma-login-manager plasmalogin; do
    rpm -q "$p" >/dev/null 2>&1 && { echo "-- files of $p"; rpm -ql "$p" | grep -v -E '/locale/|/doc/|/licenses/|\.mo$|/icons/' | head -60; }
done
for f in /etc/plasmalogin.conf /etc/plasmalogin.conf.d/* /usr/lib/plasmalogin/plasmalogin.conf.d/* /etc/xdg/plasmaloginrc /etc/xdg/plasma-setuprc; do
    [ -f "$f" ] && { echo "-- $f"; grep -v '^#' "$f" | grep -v '^$' | head -40; }
done

section "files a new account starts with (/etc/skel)"
find /etc/skel -type f 2>/dev/null | head -60
for f in $(find /etc/skel -type f -path '*/.config/*' 2>/dev/null | head -20); do echo "-- $f"; head -30 "$f"; done

section "KDE config search path and distro profile"
grep -rhs -E 'XDG_CONFIG_DIRS|XDG_DATA_DIRS' /etc/profile.d /usr/lib/environment.d /etc/environment.d /etc/environment /etc/xdg/plasma-workspace/env /usr/share/kde-settings | head
find /usr/share/kde-settings -type f 2>/dev/null | head -40
for f in $(find /usr/share/kde-settings -type f -name '*rc' -o -type f -name kdeglobals 2>/dev/null | head -12); do echo "-- $f"; head -40 "$f"; done

section "every KDE default in /etc/xdg"
ls -la /etc/xdg
for f in /etc/xdg/*rc /etc/xdg/kdeglobals; do [ -f "$f" ] && { echo "-- $f"; head -40 "$f"; }; done

section "wallpapers in detail"
ls -la /usr/share/wallpapers /usr/share/backgrounds 2>/dev/null
find /usr/share/backgrounds -maxdepth 3 2>/dev/null | head -40
grep -hs -E '"Id"|"Name"' /usr/share/wallpapers/*/metadata.json | head -40

section "plasma shell defaults and layouts"
cat /usr/share/plasma/shells/org.kde.plasma.desktop/contents/defaults 2>/dev/null
ls /usr/share/plasma/shells/org.kde.plasma.desktop/contents/ /usr/share/plasma/layout-templates 2>/dev/null
for d in /usr/share/plasma/look-and-feel/*/; do echo "-- $d"; find "$d" -maxdepth 3 | sed "s|$d||" | head -30; done
for f in /usr/share/plasma/look-and-feel/*/contents/layouts/*.js; do echo "-- $f"; head -120 "$f"; done 2>/dev/null

section "plasma-welcome customisation"
find /usr/share/plasma/plasma-welcome /usr/share/plasma-welcome /etc/xdg/plasma-welcome* 2>/dev/null | head -30
for f in $(find /usr/share/plasma/plasma-welcome /etc/xdg/plasma-welcome* -type f 2>/dev/null | head -10); do echo "-- $f"; head -40 "$f"; done
grep -l -i welcome /etc/xdg/autostart/* 2>/dev/null

section "hostname, time zone, locale defaults"
cat /etc/hostname 2>/dev/null; grep -Hs DEFAULT_HOSTNAME /usr/lib/os-release /etc/os-release
readlink /etc/localtime; cat /etc/locale.conf 2>/dev/null

section "Konsole, color schemes, Plasma styles, icon and cursor themes"
cat /etc/xdg/konsolerc 2>/dev/null; find /usr/share/konsole -type f 2>/dev/null | head -20
ls /usr/share/color-schemes /usr/share/plasma/desktoptheme /usr/share/aurorae/themes 2>/dev/null
ls /usr/share/icons 2>/dev/null

section "anything named aurora outside /usr/share/locale"
find / -xdev \( -path /proc -o -path /sys -o -path /var -o -path /tmp -o -path /usr/share/locale -o -path /usr/share/man \) -prune -o -iname '*aurora*' -print 2>/dev/null | head -100
