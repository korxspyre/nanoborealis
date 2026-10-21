#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
# Builds the NanoBorealis app for Android: an APK, with Flet's own build (Flutter, and Python for
# Android). Flet installs Flutter and the Android SDK it needs where they're missing.
#
#   client/packaging/build-android.sh <version> <build-number> <out-dir>
#
# Signed with the release key when FLET_ANDROID_SIGNING_KEY_STORE (and its passwords and alias)
# are set; otherwise with a debug key, which Android won't let update an app signed differently.
set -euo pipefail
version="$1" number="$2" out="$(realpath -m "$3")"
cd "$(dirname "$0")/.."

printf 'VERSION = "%s"\n' "$version" > src/version.py  # the app compares this with newer releases
python -m pip install --quiet "flet-cli==1.0.1" "flet==1.0.1"
if [ -z "${FLET_ANDROID_SIGNING_KEY_STORE:-}" ]; then
    echo "::warning::No Android release key (repository secrets ANDROID_KEYSTORE_BASE64 and co.): this APK is" \
         "debug-signed, and a phone must uninstall it before installing one signed differently."
fi
# The build downloads a Python runtime for each CPU type from GitHub, and now and then one arrives as
# a file of null bytes ("Unable to determine the current character... '^@'"), which fails the whole
# build for no reason of ours. Try again, from a clean build folder, up to three times.
for attempt in 1 2 3; do
    if flet build apk --yes --no-rich-output --skip-flutter-doctor \
        --build-version "${version%%-*}" --build-number "$number" -o "$out/build"; then
        break
    fi
    if [ "$attempt" = 3 ]; then
        echo "the Android build failed three times" >&2
        exit 1
    fi
    echo "::warning::The Android build failed (attempt $attempt of 3); its runtime download is unreliable. Trying again."
    rm -rf build "$out/build"
    sleep 30
done
mkdir -p "$out"
apk="$(find "$out/build" -name '*.apk' | head -n 1)"
[ -n "$apk" ] || { echo "no APK was built" >&2; exit 1; }
mv "$apk" "$out/NanoBorealis-$version-android.apk"
rm -rf "$out/build"
ls -la "$out"
