# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Renders the raster and color variants of the NanoBorealis star that the OS needs.

    python branding/make-icons.py      # needs pillow and resvg-py

Writes into branding/os/: the white mark, system logos, and the boot splash watermark.
The build copies them into the image (see build_files/build.sh).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import resvg_py
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE / "os"


def render(svg: str, size: int) -> Image.Image:
    data = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))
    return Image.open(io.BytesIO(data)).convert("RGBA")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    mark = (HERE / "nanoborealis-mark.svg").read_text(encoding="utf-8")

    # A plain white star for dark panels and the login screen.
    white = mark.replace('fill="url(#core)"', 'fill="#ffffff"')
    (OUT / "nanoborealis-mark-white.svg").write_text(white, encoding="utf-8")

    render(mark, 256).save(OUT / "system-logo.png")
    render(white, 256).save(OUT / "system-logo-white.png")

    # Boot splash watermark: the star with a soft halo, on transparency.
    star = render(mark, 200)
    canvas = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    halo = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    halo.paste(star, (60, 60), star)
    halo = halo.filter(ImageFilter.GaussianBlur(22))
    canvas = Image.alpha_composite(canvas, halo)
    canvas.alpha_composite(star, (60, 60))
    canvas.save(OUT / "plymouth-watermark.png")
    print("icons written to", OUT)

    # The app: the star on its night-sky tile, in the window, the title bar and the taskbar.
    assets = HERE.parent / "client" / "src" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    app = (HERE / "nanoborealis.svg").read_text(encoding="utf-8")
    render(app, 512).save(assets / "icon.png")
    render(app, 256).save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("app icons written to", assets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
