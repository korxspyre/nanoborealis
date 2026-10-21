# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Renders the README banner: the wallpaper, the star, and the NanoBorealis wordmark.

    python branding/make-banner.py     # needs pillow and resvg-py; run make-wallpapers.py first

Text is set in Inter (SIL Open Font License), fetched from the Google Fonts repository into
branding/.cache on first run.
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

import resvg_py
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf"
WIDTH, HEIGHT = 2400, 800


def inter(size: int, weight: int) -> ImageFont.FreeTypeFont:
    cache = HERE / ".cache" / "Inter.ttf"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(urllib.request.urlopen(FONT_URL, timeout=60).read())
    font = ImageFont.truetype(str(cache), size)
    try:
        axes = font.get_variation_axes()
        font.set_variation_by_axes([min(max(size, a["minimum"]), a["maximum"]) if "opsz" in str(a.get("name", "")).lower()
                                    else weight for a in axes])
    except (OSError, AttributeError):
        pass
    return font


def svg_image(path: Path, size: int) -> Image.Image:
    data = bytes(resvg_py.svg_to_bytes(svg_string=path.read_text(encoding="utf-8"), width=size, height=size))
    return Image.open(io.BytesIO(data)).convert("RGBA")


def main() -> int:
    base = Image.open(HERE / "banner-background.jpg").convert("RGBA").resize((WIDTH, HEIGHT), Image.LANCZOS)

    # Darken the left side a little so the wordmark reads, without a visible box.
    shade = Image.new("L", (WIDTH, HEIGHT))
    for x in range(WIDTH):
        value = int(max(0.0, 1 - x / (WIDTH * 0.72)) ** 1.6 * 150)
        shade.paste(value, (x, 0, x + 1, HEIGHT))
    base = Image.composite(Image.new("RGBA", base.size, (4, 6, 18, 255)), base, shade)

    # The star, with a soft halo.
    star_size = 330
    star = svg_image(HERE / "nanoborealis-mark.svg", star_size)
    sx, sy = 170, (HEIGHT - star_size) // 2
    halo = Image.new("RGBA", base.size, (0, 0, 0, 0))
    glow = star.copy()
    glow.putalpha(glow.getchannel("A").point(lambda a: int(a * 0.8)))
    halo.paste(glow, (sx, sy), glow)
    halo = halo.filter(ImageFilter.GaussianBlur(38))
    base = Image.alpha_composite(base, halo)
    base = Image.alpha_composite(base, halo)
    base.alpha_composite(star, (sx, sy))

    draw = ImageDraw.Draw(base)
    title = inter(168, 700)
    tag = inter(50, 500)
    tx = sx + star_size + 70
    draw.text((tx, HEIGHT // 2 - 40), "NanoBorealis", font=title, fill=(245, 250, 255, 255), anchor="ls")
    draw.text((tx + 6, HEIGHT // 2 + 70), "The agentic Linux desktop. Free AI, on your own hardware.",
              font=tag, fill=(200, 226, 232, 235), anchor="ls")

    base.convert("RGB").save(HERE / "banner.jpg", quality=92, optimize=True, progressive=True)
    print("banner written to", HERE / "banner.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
