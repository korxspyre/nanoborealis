#!/bin/bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre

set -ouex pipefail

cp -avf "/ctx/system_files"/. /

# The license NanoBorealis is under (PolyForm-Noncommercial-1.0.0), its NOTICE, and the texts of the other licenses in it.
install -d /usr/share/licenses/nanoborealis
cp -avf /ctx/licenses/. /usr/share/licenses/nanoborealis/

# nft CLI for the agent's network guard (normally already present alongside firewalld).
dnf5 install -y nftables

chmod 0755 /usr/libexec/nanoborealis-firstrun \
           /usr/libexec/nanoborealis-firewall \
           /usr/libexec/nanoborealis-remote \
           /usr/bin/nanoborealis-app \
           /usr/libexec/nanoborealis-migrate \
           /usr/libexec/nanoborealis-setup-from-stick \
           /usr/libexec/nanoborealis-relay \
           /usr/libexec/nanoborealis-wifi-resume \
           /usr/lib/systemd/system-sleep/nanoborealis-wifi \
           /usr/share/nanoborealis/install.sh \
           /usr/share/nanoborealis/nanoborealis \
           /usr/share/nanoborealis/image/entrypoint.sh
ln -sf /usr/share/nanoborealis/nanoborealis /usr/bin/nanoborealis
# Earlier names, so existing habits, scripts and desktop icons keep working.
ln -sf /usr/share/nanoborealis/nanoborealis /usr/bin/nanoaurora
ln -sf /usr/share/nanoborealis/nanoborealis /usr/bin/nanobot-os

systemctl enable nanoborealis-firewall.service nanoborealis-migrate.service nanoborealis-wifi-resume.service

# Remote access: the NanoBorealis app pairs with a PIN shown on this screen, then connects over TLS
# with a password of its own (nanoborealis-remote). Avahi announces the computer so the app finds it.
systemctl enable nanoborealis-remote.service
systemctl enable avahi-daemon.service

# The lock screen as a live dashboard (the agent, devices, calendar, this computer): a service
# gathers it into one file, and Plasma's lock screen shows it while nobody is at the computer.
chmod 0755 /usr/libexec/nanoborealis-dashboard
systemctl enable nanoborealis-dashboard.service
lockscreen=/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen
install -m 0644 /usr/share/nanoborealis/lockscreen/NanoBorealisDashboard.qml "$lockscreen/"
python3 /ctx/patch-lockscreen.py "$lockscreen"

# The NanoBorealis app: this computer's own window onto its agent, the same app as on phones and
# PCs, from this commit's source (client/src). It gets a private Python environment, and Flet's
# Linux window program in its "light" build (no video or audio, so fewer system libraries), put
# where nanoborealis-app points Flet, so nothing downloads on first start.
app=/usr/lib/nanoborealis-app
python3 -m venv "$app/venv"
"$app/venv/bin/pip" install --no-cache-dir --quiet "flet[desktop]==1.0.1" "websockets>=14" "zeroconf>=0.130"
cp -r /ctx/client/src "$app/src"
rm -rf "$app/src/__pycache__"
flet_client="$(FLET_DESKTOP_FLAVOR=light "$app/venv/bin/python3" -c 'import flet_desktop; print(flet_desktop.get_artifact_filename())')"
flet_version="$("$app/venv/bin/python3" -c 'import flet_desktop.version; print(flet_desktop.version.version)')"
curl -fsSL --retry 3 -o /tmp/flet-client.tar.gz \
    "https://github.com/flet-dev/flet/releases/download/v${flet_version}/${flet_client}"
mkdir -p "$app/client"
tar -xzf /tmp/flet-client.tar.gz -C "$app/client"
rm -f /tmp/flet-client.tar.gz
test -x "$app/client/flet/flet"
# Whatever system libraries it needs that Aurora lacks, installed by the names it asks for
# (libgtk-3.so.0 and the like), so this stays right as Flet or Aurora change.
needs="$(ldd "$app/client/flet/flet" | awk '/not found/ {print $1 "()(64bit)"}' | sort -u)"
[ -z "$needs" ] || dnf5 install -y $needs
# And a proper KDE window: KWin's own title bar instead of Flet's GNOME-style one, and the app's
# own name and icon in the dock instead of Flet's (nanoborealis-window.c; the launcher loads it).
command -v gcc >/dev/null || dnf5 install -y gcc
gcc -O2 -Wall -shared -fPIC -nostdlib -o "$app/libnanoborealis-window.so" /ctx/nanoborealis-window.c
"$app/venv/bin/python3" -m compileall -q "$app/src" "$app/venv/lib" >/dev/null || true

# Updates are opt-in: machines only move to a newer build when their owner runs
# `nanoborealis update`. Masking (not just disabling) keeps presets from re-enabling these.
systemctl mask uupd.timer bootc-fetch-apply-updates.timer rpm-ostreed-automatic.timer

# Rebrand: the boot menu, System Info, and fastfetch read these. ID and VARIANT_ID stay
# as Aurora's so Universal Blue's update tooling keeps recognising the system.
. /usr/lib/os-release
REPO_URL=https://github.com/korxspyre/nanoborealis
sed -i \
    -e 's|^NAME=.*|NAME="NanoBorealis"|' \
    -e "s|^PRETTY_NAME=.*|PRETTY_NAME=\"NanoBorealis ${VERSION_ID:-}\"|" \
    -e 's|^LOGO=.*|LOGO=distributor-logo|' \
    -e "s|^HOME_URL=.*|HOME_URL=\"$REPO_URL\"|" \
    -e "s|^DOCUMENTATION_URL=.*|DOCUMENTATION_URL=\"$REPO_URL#readme\"|" \
    -e "s|^SUPPORT_URL=.*|SUPPORT_URL=\"$REPO_URL/issues\"|" \
    -e "s|^BUG_REPORT_URL=.*|BUG_REPORT_URL=\"$REPO_URL/issues\"|" \
    /usr/lib/os-release
grep -q '^LOGO=' /usr/lib/os-release || echo 'LOGO=distributor-logo' >> /usr/lib/os-release
# The name a new computer suggests for itself (first-boot setup, the network).
sed -i 's|^DEFAULT_HOSTNAME=.*|DEFAULT_HOSTNAME="nanoborealis"|' /usr/lib/os-release
grep -q '^DEFAULT_HOSTNAME=' /usr/lib/os-release || echo 'DEFAULT_HOSTNAME="nanoborealis"' >> /usr/lib/os-release

# Setup's password prompt without sudo's first-use lecture.
chmod 0440 /etc/sudoers.d/nanoborealis
visudo -cf /etc/sudoers.d/nanoborealis

# --- The NanoBorealis look ------------------------------------------------------------------
# NanoBorealis has its own look: the aurora wallpaper, a night-and-aurora-teal color scheme, a
# floating dock, its own splash, lock screen and terminal. Aurora's themes, wallpapers and names
# are removed, so nothing can fall back to them. Every step has a check in tests/image-checks.sh,
# so an upstream update that brings Aurora's look back fails the build.
B=/ctx/branding

# The star, wherever KDE and other apps look for the distribution's logo.
install -Dm0644 "$B/nanoborealis.svg" /usr/share/icons/hicolor/scalable/apps/nanoborealis.svg
install -Dm0644 "$B/nanoborealis-mark.svg" /usr/share/icons/hicolor/scalable/places/distributor-logo.svg
install -Dm0644 "$B/nanoborealis-mark.svg" /usr/share/icons/hicolor/scalable/distributor-logo.svg
install -Dm0644 "$B/nanoborealis-symbolic.svg" /usr/share/icons/hicolor/scalable/places/distributor-logo-symbolic.svg
install -Dm0644 "$B/os/nanoborealis-mark-white.svg" /usr/share/icons/hicolor/scalable/places/distributor-logo-white.svg
install -Dm0644 "$B/os/system-logo.png" /usr/share/pixmaps/system-logo.png
install -Dm0644 "$B/os/system-logo-white.png" /usr/share/pixmaps/system-logo-white.png
gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true

# Aurora's desktop themes and wallpapers go. Wallpapers no package owns are Aurora's additions;
# KDE's and Fedora's own stay. KDE falls back to the "Next" wallpaper when nothing else is set,
# so that becomes ours as well.
rm -rf /usr/share/plasma/look-and-feel/dev.getaurora.*
for wall in /usr/share/wallpapers/*; do
    [ "$(basename "$wall")" = NanoBorealis ] && continue
    if [[ "$(readlink -f "$wall")" == /usr/share/backgrounds/aurora/* ]] || ! rpm -qf "$wall" >/dev/null 2>&1; then
        rm -rf "$wall"
    fi
done
rm -rf /usr/share/backgrounds/aurora
find /usr/share/backgrounds -maxdepth 1 -xtype l -delete
rm -rf /usr/share/wallpapers/Next
ln -s NanoBorealis /usr/share/wallpapers/Next

# Our look-and-feel (system_files) gets the star for its splash screen.
install -Dm0644 "$B/nanoborealis.svg" \
    /usr/share/plasma/look-and-feel/org.nanoborealis.desktop/contents/splash/images/nanoborealis.svg

# KDE's defaults for new accounts, and for the login and first-boot screens. Fedora's kde-settings
# profile is also on KDE's config path, and Aurora fills it with its own theme, so the same keys go
# into both: whichever KDE reads first, it finds NanoBorealis.
for xdg in /etc/xdg /usr/share/kde-settings/kde-profile/default/xdg; do
    mkdir -p "$xdg"
    kwriteconfig6 --file "$xdg/kdeglobals" --group KDE --key LookAndFeelPackage org.nanoborealis.desktop
    kwriteconfig6 --file "$xdg/kdeglobals" --group KDE --key widgetStyle Breeze
    kwriteconfig6 --file "$xdg/kdeglobals" --group KDE --key ColorScheme NanoBorealis
    kwriteconfig6 --file "$xdg/kdeglobals" --group General --key ColorScheme NanoBorealis
    kwriteconfig6 --file "$xdg/kdeglobals" --group Icons --key Theme breeze-dark
    kwriteconfig6 --file "$xdg/plasmarc" --group Theme --key name default
    kwriteconfig6 --file "$xdg/ksplashrc" --group KSplash --key Engine KSplashQML
    kwriteconfig6 --file "$xdg/ksplashrc" --group KSplash --key Theme org.nanoborealis.desktop
    kwriteconfig6 --file "$xdg/kscreenlockerrc" --group Greeter --key WallpaperPlugin org.kde.image
    kwriteconfig6 --file "$xdg/kscreenlockerrc" --group Greeter --group Wallpaper --group org.kde.image \
        --group General --key Image "file:///usr/share/wallpapers/NanoBorealis/"
    kwriteconfig6 --file "$xdg/kscreenlockerrc" --group Greeter --group Wallpaper --group org.kde.image \
        --group General --key PreviewImage "file:///usr/share/wallpapers/NanoBorealis/"
    kwriteconfig6 --file "$xdg/kwinrc" --group org.kde.kdecoration2 --key library org.kde.breeze
    kwriteconfig6 --file "$xdg/kwinrc" --group org.kde.kdecoration2 --key theme Breeze
    kwriteconfig6 --file "$xdg/konsolerc" --group "Desktop Entry" --key DefaultProfile NanoBorealis.profile
    # Plugged in, the computer stays awake for its agent: chat apps, schedules and paired devices
    # reach it only while it's awake. The screen still dims and turns off, and closing the lid only
    # turns the screen off (64). On battery, KDE's own defaults apply.
    kwriteconfig6 --file "$xdg/powerdevilrc" --group AC --group SuspendAndShutdown --key AutoSuspendAction 0
    kwriteconfig6 --file "$xdg/powerdevilrc" --group AC --group SuspendAndShutdown --key LidAction 64
    # ...and its screen stays on, dimmed, showing the lock-screen dashboard instead of going dark.
    kwriteconfig6 --file "$xdg/powerdevilrc" --group AC --group Display --key TurnOffDisplayWhenIdle false
    kwriteconfig6 --file "$xdg/kcm-about-distrorc" --group General --key Name NanoBorealis
    kwriteconfig6 --file "$xdg/kcm-about-distrorc" --group General --key Website "$REPO_URL"
    kwriteconfig6 --file "$xdg/kcm-about-distrorc" --group General --key LogoPath /usr/share/pixmaps/system-logo-white.png
    # The launcher's favorites: the agent instead of Aurora's docs.
    printf '[General]\nPrepend=%s\nIgnoreDefaults=true\n' \
        'preferred://browser;nanoborealis.desktop;systemsettings.desktop;org.kde.dolphin.desktop;org.kde.kate.desktop;org.kde.konsole.desktop;io.github.kolunmi.Bazaar.desktop' \
        > "$xdg/kicker-extra-favoritesrc"
done
# The colors themselves too, not just the scheme's name, so every KDE app draws with them from
# the first login on.
python3 - <<'PY'
from pathlib import Path
blocks, keep = [], False
for line in Path("/usr/share/color-schemes/NanoBorealis.colors").read_text().splitlines():
    if line.startswith("["):
        keep = line.startswith(("[Colors:", "[ColorEffects:", "[WM]"))
    if keep:
        blocks.append(line)
kdeglobals = Path("/etc/xdg/kdeglobals")
kdeglobals.write_text(kdeglobals.read_text().rstrip() + "\n\n" + "\n".join(blocks).strip() + "\n")
PY

# Aurora's own launchers: its documentation, forum and updater (NanoBorealis updates with
# `nanoborealis update`; the desktop has its icon). Aurora's settings page stays in System
# Settings, out of the app menu.
rm -f /usr/share/applications/dev.getaurora.documentation.desktop \
      /usr/share/applications/dev.getaurora.discussions.desktop \
      /usr/share/applications/dev.getaurora.offline-docs.desktop \
      /usr/share/applications/dev.getaurora.system-update.desktop \
      /usr/share/kglobalaccel/dev.getaurora.offline-docs.desktop
rm -rf /usr/share/doc/aurora
if [ -f /usr/share/applications/kcm_ublue.desktop ]; then
    grep -q '^NoDisplay=' /usr/share/applications/kcm_ublue.desktop \
        || sed -i '/^\[Desktop Entry\]/a NoDisplay=true' /usr/share/applications/kcm_ublue.desktop
fi

# fastfetch shows the star, in NanoBorealis colors.
sed -i -e 's|/usr/share/ublue-os/aurora-ascii-logo.txt|/usr/share/nanoborealis/fastfetch-logo.txt|' \
       -e 's|"1": "white"|"1": "#5eead4"|' \
       -e 's|"user": "#b75761"|"user": "#5eead4"|' -e 's|"at": "#a64c6a"|"at": "#8b9ab3"|' \
       -e 's|"host": "#4075bf"|"host": "#a78bfa"|' -e 's|"keyColor": "#ad67d7"|"keyColor": "#5eead4"|' \
    /usr/share/ublue-os/fastfetch.jsonc

# Boot splash: the star on a night sky, with Fedora's spinner. The initramfs carries the theme,
# so it's rebuilt the way Aurora builds it.
theme=/usr/share/plymouth/themes/nanoborealis
mkdir -p "$theme"
cp /usr/share/plymouth/themes/spinner/*.png "$theme/"
install -m0644 "$B/os/plymouth-watermark.png" "$theme/watermark.png"
cat > "$theme/nanoborealis.plymouth" <<EOF
[Plymouth Theme]
Name=NanoBorealis
Description=The NanoBorealis star while the computer starts
ModuleName=two-step

[two-step]
ImageDir=$theme
DialogHorizontalAlignment=.5
DialogVerticalAlignment=.382
TitleHorizontalAlignment=.5
TitleVerticalAlignment=.382
HorizontalAlignment=.5
VerticalAlignment=.72
WatermarkHorizontalAlignment=.5
WatermarkVerticalAlignment=.42
Transition=none
TransitionDuration=0.0
BackgroundStartColor=0x060a1d
BackgroundEndColor=0x060a1d
EOF
plymouth-set-default-theme nanoborealis
KERNEL_VERSION="$(rpm -q --queryformat='%{evr}.%{arch}' kernel-core)"
export DRACUT_NO_XATTR=1
/usr/bin/dracut --kver "$KERNEL_VERSION" --reproducible -v -f "/usr/lib/modules/$KERNEL_VERSION/initramfs.img"
chmod 0600 "/usr/lib/modules/$KERNEL_VERSION/initramfs.img"
