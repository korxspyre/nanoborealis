#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# NanoBorealis agent installer (any Fedora Atomic host with systemd + Podman works).
# Run from inside this folder as your normal user:  bash install.sh
# Safe to re-run: it rebuilds the image and restarts the agent, keeping its data.
set -euo pipefail

AGENT_USER="nanobot-agent"
IMAGE="localhost/nanoborealis-agent:latest"
NANOBOT_VERSION="0.3.5"
WEBUI_URL="http://127.0.0.1:8765"
RESET_KEY=0

usage() {
    cat <<'EOF'
Usage: bash install.sh [--version X.Y.Z] [--reset-key]

  --version X.Y.Z   nanobot release to install (default: 0.3.5)
  --reset-key       ask for a new OpenRouter API key even if one is stored
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version)   NANOBOT_VERSION="${2:?--version needs a value}"; shift 2 ;;
        --reset-key) RESET_KEY=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
ok()   { printf '    \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '    \033[33mwarn\033[0m  %s\n' "$1"; }
die()  { printf '    \033[31mfail\033[0m  %s\n' "$1" >&2; exit 1; }

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- preflight -----------------------------------------------------------------
say "Preflight"
[ "$(uname -s)" = "Linux" ] || die "this runs on the Aurora machine, not $(uname -s)"
[ "$(id -u)" -ne 0 ] || die "run as your normal user; the script uses sudo where it needs to"
for f in image/Containerfile image/entrypoint.sh image/seed/config.json nanobot.container nanoborealis; do
    [ -f "$KIT/$f" ] || die "missing $f - run this from inside the nanoborealis folder"
done
for c in podman systemctl loginctl sudo curl getent; do
    command -v "$c" >/dev/null 2>&1 || die "$c not found"
done
[ -d /run/systemd/system ] || die "systemd is not running as init"

pv="$(podman --version | awk '{print $3}')"
pmaj="${pv%%.*}"; prest="${pv#*.}"; pmin="${prest%%.*}"
if [ "$pmaj" -lt 4 ] || { [ "$pmaj" -eq 4 ] && [ "$pmin" -lt 8 ]; }; then
    die "podman $pv is too old; 4.8 or newer is required"
fi
ok "podman $pv"
sudo -v || die "sudo access is required"

# --- agent account -------------------------------------------------------------
say "Agent account"
if id "$AGENT_USER" >/dev/null 2>&1; then
    ok "$AGENT_USER already exists"
else
    sudo useradd --create-home --shell /usr/sbin/nologin --comment "NanoBorealis agent" "$AGENT_USER"
    ok "created $AGENT_USER (cannot log in)"
fi
AGENT_UID="$(id -u "$AGENT_USER")"
AGENT_HOME="$(getent passwd "$AGENT_USER" | cut -d: -f6)"
case "$AGENT_HOME" in
    /*/*) ;;
    *) die "unexpected home directory for $AGENT_USER: '$AGENT_HOME'" ;;
esac

if ! grep -q "^${AGENT_USER}:" /etc/subuid || ! grep -q "^${AGENT_USER}:" /etc/subgid; then
    die "$AGENT_USER has no subuid/subgid range, which rootless Podman needs (see 'man usermod', --add-subuids)"
fi
ok "subordinate id ranges present"

sudo loginctl enable-linger "$AGENT_USER"
for _ in $(seq 1 30); do
    sudo test -S "/run/user/$AGENT_UID/bus" && break
    sleep 1
done
sudo test -S "/run/user/$AGENT_UID/bus" || die "the user manager for $AGENT_USER did not start"
ok "lingering on; $AGENT_USER's services start at boot"

# --- shared projects folder ----------------------------------------------------
# Per-user ACLs rather than a shared group: a host group isn't visible inside the
# agent's rootless container, so group permissions would lock the agent out of your files.
say "Shared projects folder"
PROJECTS="/srv/nanoborealis/projects"
command -v setfacl >/dev/null 2>&1 || die "setfacl not found (package: acl)"
sudo install -d -m 0755 /srv/nanoborealis
sudo install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0770 "$PROJECTS"
sudo setfacl -m "u:$AGENT_USER:rwx,u:$USER:rwx,m::rwx,d:u:$AGENT_USER:rwx,d:u:$USER:rwx,d:m::rwx" "$PROJECTS"
if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    sudo chcon -R -t container_file_t -l s0 "$PROJECTS"
fi
ln -sfn "$PROJECTS" "$HOME/nanoborealis-projects"
ok "$PROJECTS (shortcut in your home: nanoborealis-projects)"

# --- network guard -------------------------------------------------------------
say "Network guard"
if systemctl cat nanoborealis-firewall.service >/dev/null 2>&1; then
    sudo systemctl restart nanoborealis-firewall.service
    # Read the rules first: `nft | grep -q` under pipefail can fail when grep stops reading early.
    rules="$(sudo nft list table inet nanoborealis 2>/dev/null || true)"
    if grep -q skuid <<<"$rules"; then
        ok "$AGENT_USER can reach the internet but not your local network"
    else
        warn "firewall rules did not load - check: systemctl status nanoborealis-firewall"
    fi
else
    warn "this system has no nanoborealis-firewall service; the agent can reach your local network"
fi

as_agent() {
    ( cd / && sudo -u "$AGENT_USER" env \
        HOME="$AGENT_HOME" \
        XDG_RUNTIME_DIR="/run/user/$AGENT_UID" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AGENT_UID/bus" \
        "$@" )
}
agentctl() { sudo systemctl --user -M "$AGENT_USER@" "$@"; }
# Whether the agent's service manager has a unit. Not `systemctl cat`: it refuses another
# user's units ("Cannot remotely cat units"), so it always reports the unit as missing.
agent_has() { [ "$(agentctl show -p LoadState --value "$1" 2>/dev/null)" = loaded ]; }

if agent_has nanobot.service; then
    agentctl stop nanobot.service || true
    ok "stopped the running agent for the upgrade"
fi

# --- image ---------------------------------------------------------------------
say "Agent image (first build downloads about 500 MB)"
BUILD="$AGENT_HOME/nanoborealis"
sudo rm -rf "$BUILD"
sudo install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0700 "$BUILD"
sudo cp -R "$KIT/image" "$BUILD/image"
printf '%s\n' "$NANOBOT_VERSION" | sudo tee "$BUILD/VERSION" >/dev/null
sudo chown -R "$AGENT_USER:$AGENT_USER" "$BUILD"
as_agent podman build --pull=newer \
    --build-arg "NANOBOT_VERSION=$NANOBOT_VERSION" \
    -t "$IMAGE" "$BUILD/image"
ok "built $IMAGE with nanobot $NANOBOT_VERSION"

# --- secrets -------------------------------------------------------------------
say "Secrets"
secret_exists() { as_agent podman secret inspect "$1" >/dev/null 2>&1; }

# A key from the NanoBorealis Installer: saved during install, or read now from the stick
# if it's still plugged in.
SETUP_JSON=/var/lib/nanoborealis/setup.json
if [ -x /usr/libexec/nanoborealis-setup-from-stick ] && ! sudo test -f "$SETUP_JSON"; then
    sudo /usr/libexec/nanoborealis-setup-from-stick >/dev/null 2>&1 || true
fi
stick_key=""
if sudo test -f "$SETUP_JSON"; then
    stick_key="$(sudo python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("openrouter_api_key", ""))' \
        "$SETUP_JSON" 2>/dev/null || true)"
fi

if secret_exists openrouter_api_key && [ "$RESET_KEY" -eq 0 ]; then
    ok "OpenRouter key already stored (re-run with --reset-key to replace it)"
elif [ -n "$stick_key" ] && [ "$RESET_KEY" -eq 0 ]; then
    printf '%s' "$stick_key" | as_agent podman secret create --replace openrouter_api_key - >/dev/null
    ok "OpenRouter key from the NanoBorealis Installer stored as a Podman secret"
else
    echo "    Paste the OpenRouter API key for this machine. Make it a dedicated key with"
    echo "    a low credit limit: the agent can read its own key."
    read -r -s -p "    OpenRouter API key: " or_key; echo
    [ -n "$or_key" ] || die "no key entered"
    printf '%s' "$or_key" | as_agent podman secret create --replace openrouter_api_key - >/dev/null
    unset or_key
    ok "OpenRouter key stored as a Podman secret"
fi
if [ -n "$stick_key" ]; then
    sudo rm -f "$SETUP_JSON"  # the key lives in the Podman secret now; don't leave a copy lying around
    ok "removed the installer's copy of the key"
fi
unset stick_key

webui_pw=""
if secret_exists nanobot_webui_secret; then
    ok "WebUI password already set (show it with: nanoborealis password)"
else
    webui_pw="$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')"
    printf '%s' "$webui_pw" | as_agent podman secret create nanobot_webui_secret - >/dev/null
    ok "generated a WebUI password"
fi
# Paired devices reach the WebUI through the remote-access service, which supplies this password
# for them (they have their own), so it keeps a copy only root can read.
if systemctl cat nanoborealis-remote.service >/dev/null 2>&1; then
    sudo install -d -m 0700 /var/lib/nanoborealis /var/lib/nanoborealis/remote
    as_agent podman secret inspect --showsecret --format '{{.SecretData}}' nanobot_webui_secret \
        | sudo tee /var/lib/nanoborealis/remote/webui-secret >/dev/null
    sudo chmod 0600 /var/lib/nanoborealis/remote/webui-secret
    sudo systemctl try-restart nanoborealis-remote.service
fi

# --- service -------------------------------------------------------------------
say "Service"
QDIR="$AGENT_HOME/.config/containers/systemd"
sudo install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0700 \
    "$AGENT_HOME/.config" "$AGENT_HOME/.config/containers" "$QDIR"
sudo install -o "$AGENT_USER" -g "$AGENT_USER" -m 0644 "$KIT/nanobot.container" "$QDIR/nanobot.container"
agentctl daemon-reload
# The generator runs on each reload. Right after the account's services start (first login),
# give it a few tries before calling it a failure.
for _ in 1 2 3 4 5; do
    agent_has nanobot.service && break
    sleep 3
    agentctl daemon-reload
done
if ! agent_has nanobot.service; then
    # Say why, in the setup window and in a log, instead of leaving it to guesswork.
    quadlet_log="$HOME/nanoborealis-setup-quadlet.log"
    quadlet="$(ls /usr/libexec/podman/quadlet /usr/lib/systemd/user-generators/podman-user-generator 2>/dev/null | head -n 1)"
    {
        echo "== $(date) =="; ls -la "$QDIR"; echo
        as_agent env QUADLET_UNIT_DIRS="$QDIR" "$quadlet" -dryrun -user 2>&1 | tail -40
        echo; sudo systemctl --user -M "$AGENT_USER@" status --no-pager 2>&1 | head -15
    } > "$quadlet_log" 2>&1
    warn "Quadlet did not turn the agent's service file into a service. What it reported:"
    grep -v '^\[' "$quadlet_log" | tail -20 | sed 's/^/          /'
    die "setup stopped here; the full report is in $quadlet_log"
fi
agentctl restart nanobot.service
ok "nanobot.service running as $AGENT_USER"

# --- helper and launcher -------------------------------------------------------
# On a NanoBorealis image both ship with the system; standalone installs add them here.
say "Helper and app launcher"
if [ -x /usr/bin/nanoborealis ]; then
    ok "nanoborealis command provided by the system image"
else
    sudo install -m 0755 "$KIT/nanoborealis" /usr/local/bin/nanoborealis
    ok "installed /usr/local/bin/nanoborealis"
fi

if [ -f /usr/share/applications/nanoborealis.desktop ]; then
    ok "app menu entry provided by the system image"
else
    apps="$HOME/.local/share/applications"
    mkdir -p "$apps"
    cat > "$apps/nanoborealis.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NanoBorealis
Comment=Open your NanoBorealis agent
Exec=xdg-open $WEBUI_URL
Icon=internet-web-browser
Categories=Utility;
EOF
    ok "added 'NanoBorealis' to the app menu"
fi

if [ -f /usr/share/applications/nanoborealis-update.desktop ]; then
    desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    mkdir -p "$desk"
    install -m 0755 /usr/share/applications/nanoborealis-update.desktop "$desk/nanoborealis-update.desktop"
    ok "added an 'Update NanoBorealis' icon to your desktop"
fi

# --- wait for the WebUI --------------------------------------------------------
say "Waiting for the WebUI"
code="000"
for _ in $(seq 1 90); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$WEBUI_URL/" || true)"
    [ "$code" != "000" ] && break
    sleep 2
done
if [ "$code" = "000" ]; then
    warn "no answer from $WEBUI_URL yet - check: nanoborealis logs"
else
    ok "WebUI answering at $WEBUI_URL"
fi

# --- done ----------------------------------------------------------------------
say "Done"
echo "    Open:      'NanoBorealis' in the app menu"
echo "    Projects:  ~/nanoborealis-projects   (shared with the agent)"
if systemctl is-enabled --quiet nanoborealis-remote.service 2>/dev/null; then
    echo "    Devices:   in the NanoBorealis app on your phone or PC, pick this computer. It shows a"
    echo "               PIN here; type it into the app to pair. No passwords to copy."
fi
echo "    Manage:    nanoborealis help"
echo
