# SPDX-License-Identifier: Apache-2.0
# From the Universal Blue image template (https://github.com/ublue-os/image-template), changed for
# NanoBorealis by korxspyre. Like the template, this file is under the Apache License 2.0.
# NanoBorealis: Aurora with a sandboxed nanobot agent built in.
# Build scripts sit in a throwaway stage so they don't end up in the image.
# BASE_IMAGE picks the edition: Aurora, or Aurora with NVIDIA's open driver and container toolkit
# (aurora-nvidia-open) for computers whose NVIDIA GPU the pool uses.
ARG BASE_IMAGE=ghcr.io/ublue-os/aurora:stable

FROM scratch AS ctx
COPY build_files /
COPY system_files /system_files
COPY branding /branding
COPY client/src /client/src
COPY LICENSE NOTICE /licenses/

FROM ${BASE_IMAGE}

RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    /ctx/build.sh

# NanoBorealis's own files are under the PolyForm Noncommercial License 1.0.0 (the text is in /usr/share/licenses/nanoborealis);
# what Aurora, Fedora and the other software in the image are under is what each of them says.
LABEL org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"

RUN bootc container lint
