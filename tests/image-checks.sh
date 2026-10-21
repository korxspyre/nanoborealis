#!/usr/bin/bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Runs inside a built NanoBorealis image. Every build runs it before publishing, to dev or to
# stable, so an upstream Aurora change that breaks our layer stops here instead of on someone's
# computer.
set -u
fail=0
pass() { echo "ok   $1"; }
flunk() { echo "FAIL $1"; fail=1; }
expect() {  # description, command...
    local what="$1"; shift
    if "$@" >/dev/null 2>&1; then pass "$what"; else flunk "$what"; fi
}

# Identity: our name everywhere users see it, Aurora's ID where ublue tooling looks.
. /usr/lib/os-release
[ "${NAME:-}" = NanoBorealis ] && pass "os-release NAME is NanoBorealis" || flunk "os-release NAME is '${NAME:-}'"
[ "${ID:-}" = aurora ] && pass "os-release ID stays aurora" || flunk "os-release ID changed to '${ID:-}' upstream"

# Our commands and scripts.
expect "nanoborealis command" test -x /usr/bin/nanoborealis
expect "earlier command names still work" test -x /usr/bin/nanoaurora -a -x /usr/bin/nanobot-os
for f in /usr/libexec/nanoborealis-firewall /usr/libexec/nanoborealis-migrate /usr/libexec/nanoborealis-firstrun \
         /usr/libexec/nanoborealis-setup-from-stick /usr/libexec/nanoborealis-relay /usr/share/nanoborealis/install.sh \
         /usr/share/nanoborealis/nanoborealis /usr/share/nanoborealis/image/entrypoint.sh; do
    expect "$f is executable" test -x "$f"
done
for f in /usr/share/nanoborealis/install.sh /usr/share/nanoborealis/nanoborealis \
         /usr/libexec/nanoborealis-firewall /usr/libexec/nanoborealis-migrate /usr/libexec/nanoborealis-firstrun; do
    expect "$f parses" bash -n "$f"
done
expect "setup-from-stick compiles" python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' \
    /usr/libexec/nanoborealis-setup-from-stick
expect "compute-config compiles" python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' \
    /usr/share/nanoborealis/compute-config.py
for f in /usr/libexec/nanoborealis-relay /usr/share/nanoborealis/{pool,loadout,benchdb,scores}.py; do
    expect "$f compiles" python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$f"
done
# The loadout planner, bench database and scores load on the image's own Python (sqlite3 included).
expect "pool tools import" python3 -c 'import sys; sys.path.insert(0, "/usr/share/nanoborealis"); import pool'
expect "pool node service template" grep -q '^Image=ghcr.io/korxspyre/nanoborealis-pool:' \
    /usr/share/nanoborealis/exo.container
expect "pool relay service installed" test -f /usr/lib/systemd/system/nanoborealis-pool-relay.service

# The NVIDIA edition (built on aurora-nvidia-open): what `pool join` needs to run the NVIDIA node.
if command -v nvidia-smi >/dev/null 2>&1; then
    expect "NVIDIA edition: the container toolkit" command -v nvidia-ctk
    cdi_units="$(ls /usr/lib/systemd/system /etc/systemd/system 2>/dev/null | grep -iE 'cdi|nvctk' | sort -u | xargs)"
    [ -n "$cdi_units" ] && pass "NVIDIA edition: a service writes the CDI spec at boot ($cdi_units)" \
        || flunk "NVIDIA edition: no service writes the CDI spec at boot (containers wouldn't see the GPU)"
fi
# Pairing: the app's way in. A 6-digit PIN on this screen, then TLS and a password per device.
expect "remote-access service is executable" test -x /usr/libexec/nanoborealis-remote
expect "remote-access service compiles" python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' \
    /usr/libexec/nanoborealis-remote
expect "remote access is on by default" systemctl is-enabled nanoborealis-remote.service
expect "Avahi announces the pairing port" grep -q '<port>8766</port>' /etc/avahi/services/nanoborealis.service
expect "Avahi can read the announcement (well-formed XML)" python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' \
    /etc/avahi/services/nanoborealis.service
expect "Avahi is enabled" systemctl is-enabled avahi-daemon.service
for tool in openssl runuser setsid loginctl gdbus; do
    expect "$tool is present (pairing needs it)" command -v "$tool"
done

# The NanoBorealis app: this computer's own window onto its agent, not a web page.
expect "the app menu opens the NanoBorealis app" grep -q '^Exec=nanoborealis-app$' /usr/share/applications/nanoborealis.desktop
expect "the app's launcher is executable" test -x /usr/bin/nanoborealis-app
expect "the app's Python environment loads it" \
    env -C /usr/lib/nanoborealis-app/src /usr/lib/nanoborealis-app/venv/bin/python3 -c 'import main, pairing, terminal'
expect "Flet's window program is in place" test -x /usr/lib/nanoborealis-app/client/flet/flet
expect "the app's window is a KDE window (title bar, dock icon)" \
    grep -q gtk_window_set_titlebar /usr/lib/nanoborealis-app/libnanoborealis-window.so
expect "...and that fix loads into the app's Python" \
    env LD_PRELOAD=/usr/lib/nanoborealis-app/libnanoborealis-window.so /usr/lib/nanoborealis-app/venv/bin/python3 -c pass
missing="$(ldd /usr/lib/nanoborealis-app/client/flet/flet 2>&1 | grep 'not found' | sort -u)"
[ -z "$missing" ] && pass "Flet's window program finds all its libraries" \
    || flunk "Flet's window program misses libraries: $(echo $missing)"

# The agent's and the pool node's service files must turn into services with this image's own
# Podman: setup stops with "Quadlet did not turn the agent's service file into a service" otherwise.
# And setup must look for them in a way that works on another user's services: `systemctl cat`
# refuses those ("Cannot remotely cat units"), which once failed every install at this step.
expect "setup checks the agent's services without systemctl cat" \
    bash -c '! grep -nE "(agentctl|poolctl) cat|--user -M [^;|]* cat " /usr/share/nanoborealis/install.sh /usr/share/nanoborealis/nanoborealis'
quadlet="$(ls /usr/libexec/podman/quadlet /usr/lib/systemd/user-generators/podman-user-generator 2>/dev/null | head -n 1)"
units="$(mktemp -d)"
cp /usr/share/nanoborealis/nanobot.container "$units/"
# The pool node as `pool join` writes it: for the processor, and for an NVIDIA GPU.
sed -e 's|^Exec=@EXEC_ARGS@$||' -e 's|^@GPU@$||' /usr/share/nanoborealis/exo.container > "$units/exo.container"
sed -e 's|^Exec=@EXEC_ARGS@$||' -e 's|^Image=\(.*\):cpu$|Image=\1:cuda13|' \
    -e 's|^@GPU@$|AddDevice=nvidia.com/gpu=all\nEnvironment=OVERRIDE_MEMORY_MB=11258|' \
    /usr/share/nanoborealis/exo.container > "$units/exo-gpu.container"
generated="$(QUADLET_UNIT_DIRS="$units" "$quadlet" -dryrun -user 2>&1)"
if grep -q '^ExecStart=.*podman run' <<<"$generated" && grep -q -- '---nanobot.service---' <<<"$generated" \
    && grep -q -- '---exo.service---' <<<"$generated" && grep -q -- '---exo-gpu.service---' <<<"$generated" \
    && grep -q -- '--device nvidia.com/gpu=all' <<<"$generated" && grep -q 'OVERRIDE_MEMORY_MB=11258' <<<"$generated"; then
    pass "Quadlet turns the agent and pool service files, the NVIDIA node's too, into services ($(podman --version))"
else
    flunk "Quadlet rejects a service file ($(podman --version)):"
    sed 's/^/     /' <<<"$generated" | grep -v '^     \[' | head -25
fi
rm -rf "$units"

# Services and update policy.
expect "network guard enabled" systemctl is-enabled nanoborealis-firewall.service
expect "migration enabled" systemctl is-enabled nanoborealis-migrate.service
# Every model the agent starts with is a free one, the hidden "default" preset included (nanobot
# makes it from agents.defaults.model, which is a billed model when left out).
expect "the agent's seed config names only free models" python3 -c '
import json, sys
config = json.load(open("/usr/share/nanoborealis/image/seed/config.json"))
models = [p["model"] for p in config["modelPresets"].values()] + [config["agents"]["defaults"]["model"]]
sys.exit(0 if models and all(m.endswith(":free") for m in models) else 1)'
expect "...and has OpenRouter refuse anything that costs money" python3 -c '
import json, sys
limit = json.load(open("/usr/share/nanoborealis/image/seed/config.json"))["providers"]["openrouter"]["extraBody"]["provider"]["max_price"]
sys.exit(0 if limit.get("prompt") == 0 and limit.get("completion") == 0 else 1)'
expect "Wi-Fi repair after sleep enabled" systemctl is-enabled nanoborealis-wifi-resume.service
expect "Wi-Fi repair script parses" bash -n /usr/libexec/nanoborealis-wifi-resume
expect "Realtek Wi-Fi is kept out of sleep (unloaded before, loaded after, S3 where offered)" \
    sh -n /usr/lib/systemd/system-sleep/nanoborealis-wifi
expect "...and that sleep hook is executable" test -x /usr/lib/systemd/system-sleep/nanoborealis-wifi
expect "lock-screen dashboard service enabled" systemctl is-enabled nanoborealis-dashboard.service
expect "...its collector parses" python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' /usr/libexec/nanoborealis-dashboard
expect "...and reads calendars (dateutil)" python3 -c 'import dateutil.rrule'
lockscreen=/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen
expect "Plasma's lock screen shows the dashboard" grep -q 'source: "NanoBorealisDashboard.qml"' "$lockscreen/LockScreenUi.qml"
expect "...which is installed next to it" test -f "$lockscreen/NanoBorealisDashboard.qml"
expect "...and may read its data file" grep -q '^QML_XHR_ALLOW_FILE_READ=1$' /usr/lib/environment.d/60-nanoborealis-lockscreen.conf
expect "plugged in, closing the lid at the login screen doesn't sleep" \
    grep -q '^HandleLidSwitchExternalPower=ignore$' /usr/lib/systemd/logind.conf.d/50-nanoborealis.conf
expect "Realtek rtw89 Wi-Fi stays out of the power states it can't wake from" \
    grep -q "^options rtw89_pci disable_clkreq=y" /usr/lib/modprobe.d/nanoborealis-wifi.conf
for timer in uupd.timer bootc-fetch-apply-updates.timer rpm-ostreed-automatic.timer; do
    state="$(systemctl is-enabled "$timer" 2>/dev/null || true)"
    [ "$state" = masked ] && pass "$timer masked (updates are opt-in)" || flunk "$timer is '$state', not masked"
done

# What first login and the menus need.
expect "first-login setup in autostart" test -f /etc/xdg/autostart/nanoborealis-firstrun.desktop
expect "app menu entries" test -f /usr/share/applications/nanoborealis.desktop -a -f /usr/share/applications/nanoborealis-update.desktop
if command -v desktop-file-validate >/dev/null; then
    expect "desktop entries validate" desktop-file-validate /usr/share/applications/nanoborealis.desktop \
        /usr/share/applications/nanoborealis-update.desktop /etc/xdg/autostart/nanoborealis-firstrun.desktop
fi
for tool in nft konsole podman python3 bootc flatpak; do
    expect "$tool is present" command -v "$tool"
done
expect "Avahi can announce this machine to clients (nanoborealis remote on)" test -d /etc/avahi/services

# The NanoBorealis look. Each line is an override an upstream update could quietly undo.
expect "distributor logo is the NanoBorealis star" grep -q NanoBorealis /usr/share/icons/hicolor/scalable/places/distributor-logo.svg
expect "app icon installed" test -f /usr/share/icons/hicolor/scalable/apps/nanoborealis.svg
expect "os-release logo is the star" grep -q '^LOGO=distributor-logo$' /usr/lib/os-release
expect "new computers suggest the name nanoborealis" grep -q '^DEFAULT_HOSTNAME="nanoborealis"$' /usr/lib/os-release
lnf=/usr/share/plasma/look-and-feel/org.nanoborealis.desktop
expect "NanoBorealis look-and-feel installed" grep -q '"Id": "org.nanoborealis.desktop"' "$lnf/metadata.json"
expect "its layout sets the aurora wallpaper" grep -q 'wallpapers/NanoBorealis' "$lnf/contents/layouts/org.kde.plasma.desktop-layout.js"
expect "its splash screen shows the star" test -f "$lnf/contents/splash/Splash.qml" -a -f "$lnf/contents/splash/images/nanoborealis.svg"
expect "color scheme and terminal colors installed" test -f /usr/share/color-schemes/NanoBorealis.colors \
    -a -f /usr/share/konsole/NanoBorealis.colorscheme -a -f /usr/share/konsole/NanoBorealis.profile
expect "wallpaper package installed" test -f /usr/share/wallpapers/NanoBorealis/contents/images/3840x2160.jpg
expect "the wallpaper KDE falls back to is the aurora" test "$(readlink -f /usr/share/wallpapers/Next)" = /usr/share/wallpapers/NanoBorealis
# Aurora fills Fedora's kde-settings profile with its own theme, and KDE reads that as well.
plugged_in() {  # powerdevilrc, key: the key's value for mains power
    awk -v key="$2" '/^\[/ { on = ($0 == "[AC][SuspendAndShutdown]") } on && index($0, key "=") == 1 { print substr($0, length(key) + 2) }' "$1"
}
for xdg in /etc/xdg /usr/share/kde-settings/kde-profile/default/xdg; do
    expect "$xdg: plugged in, the computer doesn't sleep by itself" test "$(plugged_in "$xdg/powerdevilrc" AutoSuspendAction)" = 0
    expect "$xdg: plugged in, closing the lid turns the screen off" test "$(plugged_in "$xdg/powerdevilrc" LidAction)" = 64
    expect "$xdg: plugged in, the screen stays on for the dashboard" \
        awk '/^\[/ { on = ($0 == "[AC][Display]") } on && $0 == "TurnOffDisplayWhenIdle=false" { found = 1 } END { exit !found }' "$xdg/powerdevilrc"
    expect "$xdg: look-and-feel is NanoBorealis" grep -q '^LookAndFeelPackage=org.nanoborealis.desktop$' "$xdg/kdeglobals"
    expect "$xdg: colors are NanoBorealis" grep -q '^ColorScheme=NanoBorealis$' "$xdg/kdeglobals"
    expect "$xdg: splash is NanoBorealis" grep -q '^Theme=org.nanoborealis.desktop$' "$xdg/ksplashrc"
    expect "$xdg: lock screen shows the aurora" grep -q 'wallpapers/NanoBorealis' "$xdg/kscreenlockerrc"
    expect "$xdg: About page names NanoBorealis" grep -q '^Name=NanoBorealis$' "$xdg/kcm-about-distrorc"
    expect "$xdg: Konsole uses the NanoBorealis profile" grep -q '^DefaultProfile=NanoBorealis.profile$' "$xdg/konsolerc"
done
expect "system colors carry the scheme itself" grep -q '^\[Colors:Window\]$' /etc/xdg/kdeglobals
expect "no Aurora look-and-feel left" bash -c '! ls /usr/share/plasma/look-and-feel | grep -qi aurora'
expect "no Aurora wallpapers left" bash -c '! ls /usr/share/wallpapers /usr/share/backgrounds | grep -qi aurora'
expect "Welcome Center greets as NanoBorealis" grep -q '^Name=Welcome to NanoBorealis$' \
    /usr/share/plasma/plasma-welcome/intro-customization.desktop
expect "terminal greeting and tips are NanoBorealis's" bash -c \
    'grep -q "Welcome to NanoBorealis" /usr/share/ublue-os/motd/template.md && ! grep -rqi aurora /usr/share/ublue-os/motd'
visible_aurora="$(grep -lis '^Name=.*aurora' /usr/share/applications/*.desktop | while read -r f; do
    grep -qiE '^(NoDisplay|Hidden)=true' "$f" || echo "$f"; done)"
[ -z "$visible_aurora" ] && pass "no Aurora apps in the app menu" || flunk "Aurora apps in the app menu: $visible_aurora"
expect "sudo settings drop-in parses" visudo -cf /etc/sudoers.d/nanoborealis
expect "fastfetch shows the star" grep -q /usr/share/nanoborealis/fastfetch-logo.txt /usr/share/ublue-os/fastfetch.jsonc
theme="$(plymouth-set-default-theme 2>/dev/null || true)"
[ "$theme" = nanoborealis ] && pass "boot splash is NanoBorealis" || flunk "boot splash is '$theme'"
if lsinitrd /usr/lib/modules/*/initramfs.img 2>/dev/null | grep -q 'plymouth/themes/nanoborealis/watermark.png'; then
    pass "the initramfs carries the boot splash"
else
    flunk "the initramfs doesn't carry the boot splash"
fi

# The migration from earlier names, run against a simulated NanoAurora-era layout. This runs in
# a throwaway container, so it may change things. On bootc /srv links to /var/srv, which a booted
# system creates but a bare container doesn't.
[ -L /srv ] && mkdir -p "$(readlink -f /srv)"
mkdir -p /srv/nanoaurora/projects /var/home/olduser/Desktop /etc/nanoaurora/compute.d
echo marker > /srv/nanoaurora/projects/kept.txt
ln -s /srv/nanoaurora/projects /var/home/olduser/nanoaurora-projects
cp /usr/share/applications/nanoborealis-update.desktop /var/home/olduser/Desktop/nanoaurora-update.desktop
printf 'ip=10.0.0.5\nport=11435\n' > /etc/nanoaurora/compute.d/laptop2
first="$(/usr/libexec/nanoborealis-migrate 2>&1)"
echo "$first" | sed 's/^/     /'
expect "migration moved the projects folder and its files" test -f /srv/nanoborealis/projects/kept.txt -a ! -e /srv/nanoaurora
[ "$(readlink /var/home/olduser/nanoborealis-projects)" = /srv/nanoborealis/projects ] \
    && [ ! -e /var/home/olduser/nanoaurora-projects ] && pass "migration relinked the user's projects shortcut" \
    || flunk "migration relinked the user's projects shortcut"
expect "migration replaced the desktop update icon" test -f /var/home/olduser/Desktop/nanoborealis-update.desktop \
    -a ! -e /var/home/olduser/Desktop/nanoaurora-update.desktop
expect "migration kept approved compute devices" test -f /etc/nanoborealis/compute.d/laptop2
second="$(/usr/libexec/nanoborealis-migrate 2>&1)"
[ "$second" = "nanoborealis-migrate: nothing to migrate" ] && pass "a second migration run changes nothing" \
    || flunk "a second migration run said: $second"

if [ "$fail" -eq 0 ]; then echo "all image checks passed"; else echo "image checks FAILED"; fi
exit "$fail"
