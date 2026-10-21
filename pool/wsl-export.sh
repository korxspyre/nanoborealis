#!/bin/sh
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Turns a pool node image into the WSL system the NanoBorealis app imports on Windows
# (client/src/poolnode.py): the image's files, a wsl.conf that keeps the node away from the
# computer's drives and programs, and which build it is. Written as parts under GitHub's 2 GiB limit
# for release files, with wsl-<variant>.json listing each part's SHA-256 and the whole's.
#
#   pool/wsl-export.sh <variant> <image> <out-dir>
set -eu
variant="$1" image="$2" out="$3"
mkdir -p "$out"
work="$(mktemp -d)"
cid="$(podman create "$image")"
trap 'podman rm -f "$cid" >/dev/null 2>&1; rm -rf "$work"' EXIT

cat > "$work/wsl.conf" <<'EOF'
# The NanoBorealis pool node: none of this computer's drives, no Windows programs, exo as its user.
[automount]
enabled = false
mountFsTab = false

[interop]
enabled = false
appendWindowsPath = false

[user]
default = exo

[boot]
systemd = false
EOF
echo "$variant" > "$work/nanoborealis-variant"
podman cp "$work/wsl.conf" "$cid:/etc/wsl.conf"
podman cp "$work/nanoborealis-variant" "$cid:/etc/nanoborealis-variant"

podman export "$cid" | gzip -1 > "$out/wsl-$variant.tar.gz"
( cd "$out" && rm -f "wsl-$variant.tar.gz.part"* && split -b 1900m -d -a 2 "wsl-$variant.tar.gz" "wsl-$variant.tar.gz.part" )
python3 - "$out" "$variant" <<'EOF'
import hashlib, json, pathlib, sys
out, variant = pathlib.Path(sys.argv[1]), sys.argv[2]

def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()

parts = sorted(out.glob(f"wsl-{variant}.tar.gz.part*"))
manifest = {"variant": variant, "sha256": sha(out / f"wsl-{variant}.tar.gz"),
            "parts": [{"name": p.name, "sha256": sha(p), "size": p.stat().st_size} for p in parts]}
(out / f"wsl-{variant}.json").write_text(json.dumps(manifest, indent=1))
print(f"wsl-{variant}: {len(parts)} part(s), {sum(p['size'] for p in manifest['parts']) / 1e9:.2f} GB")
EOF
rm -f "$out/wsl-$variant.tar.gz"
