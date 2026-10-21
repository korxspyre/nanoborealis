# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Paints the installer (Anaconda) artwork, replacing Fedora's, at the sizes Anaconda expects.

    python branding/make-installer-art.py      # needs pillow

Writes branding/installer/pixmaps/: the sidebar, logo, top bar and header for Anaconda's
default look and each variant it may pick (atomic, silverblue, workstation, cloud, server),
plus stylesheets in NanoBorealis colors. The installer build packs them into product.img.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "installer" / "pixmaps"
WALL = HERE.parent / "system_files/usr/share/wallpapers/NanoBorealis/contents/images/1920x1080.jpg"
STAR = HERE / "os" / "system-logo-white.png"
FONT = HERE / ".cache" / "Inter.ttf"
NIGHT = "#070d1f"  # the wallpaper's sky, behind everything the images don't cover

# Anaconda's own sizes: file -> (width, height), per variant folder ("" is the default look).
LAYOUT = {
    "": {"sidebar-bg": (406, 767), "sidebar-logo": (150, 69), "topbar-bg": (1040, 132)},
    "atomic": {"sidebar-bg": (657, 500), "sidebar-logo": (73, 97), "topbar-bg": (657, 497)},
    "silverblue": {"sidebar-bg": (544, 503), "sidebar-logo": (112, 97), "topbar-bg": (544, 503)},
    "workstation": {"sidebar-bg": (544, 503), "sidebar-logo": (129, 97), "topbar-bg": (544, 503)},
    "cloud": {"sidebar-bg": (657, 500), "sidebar-logo": (63, 97), "topbar-bg": (657, 497)},
    "server": {"sidebar-bg": (637, 508), "sidebar-logo": (70, 97), "topbar-bg": (636, 450)},
}
CSS = {"": "fedora.css", "atomic": "fedora-atomic.css", "silverblue": "fedora-silverblue.css",
       "workstation": "fedora-workstation.css"}


def font(size: int) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(str(FONT), size)
    try:
        face.set_variation_by_axes([600])  # Inter's variable weight: semibold
    except Exception:
        pass
    return face


def sky(size: tuple[int, int], focus: float) -> Image.Image:
    """A crop of the aurora wallpaper, darkened so white text reads on it."""
    wall = Image.open(WALL).convert("RGB")
    w, h = size
    scale = max(w / wall.width, h / wall.height) * 1.15
    wall = wall.resize((int(wall.width * scale), int(wall.height * scale)), Image.LANCZOS)
    left = int((wall.width - w) * focus)
    top = int((wall.height - h) * 0.35)
    crop = wall.crop((left, top, left + w, top + h))
    crop = ImageEnhance.Brightness(crop).enhance(0.72)
    shade = Image.new("L", size)
    ImageDraw.Draw(shade).rectangle((0, 0, w, h), fill=0)
    for y in range(h):  # heavier toward the bottom, where Anaconda puts help text
        shade.putpixel((0, y), int(150 * (y / h) ** 1.6))
    shade = shade.resize(size).filter(ImageFilter.BoxBlur(0))
    night = Image.new("RGB", size, NIGHT)
    return Image.composite(night, crop, shade.point(lambda v: v)).convert("RGBA")


def star(px: int) -> Image.Image:
    return Image.open(STAR).convert("RGBA").resize((px, px), Image.LANCZOS)


def wordmark_logo(size: tuple[int, int]) -> Image.Image:
    """The star with 'NanoBorealis', side by side when wide, stacked when tall."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if w >= h * 1.5:
        s = int(h * 0.62)
        face = font(max(10, int(h * 0.26)))
        text_w = draw.textlength("NanoBorealis", font=face)
        while s + 6 + text_w > w and face.size > 8:
            face = font(face.size - 1)
            text_w = draw.textlength("NanoBorealis", font=face)
        x = int((w - (s + 6 + text_w)) / 2)
        img.alpha_composite(star(s), (x, (h - s) // 2))
        draw.text((x + s + 6, h / 2), "NanoBorealis", font=face, fill="white", anchor="lm")
    else:
        s = int(min(w * 0.7, h * 0.62))
        face = font(max(8, int(h * 0.16)))
        while draw.textlength("NanoBorealis", font=face) > w and face.size > 7:
            face = font(face.size - 1)
        img.alpha_composite(star(s), ((w - s) // 2, 2))
        draw.text((w / 2, h - 3), "NanoBorealis", font=face, fill="white", anchor="md")
    return img


def main() -> int:
    for folder, files in LAYOUT.items():
        target = OUT / folder
        target.mkdir(parents=True, exist_ok=True)
        for name, size in files.items():
            if name == "sidebar-logo":
                art = wordmark_logo(size)
            else:
                art = sky(size, focus=0.5 if name == "sidebar-bg" else 0.3)
            art.save(target / f"{name}.png")
        if folder in CSS:
            prefix = f"/usr/share/anaconda/pixmaps/{folder + '/' if folder else ''}"
            (target / CSS[folder]).write_text(f"""/* SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */
/* Copyright (C) 2026 korxspyre */
/* Anaconda look for NanoBorealis (replaces Fedora's) */
@define-color fedora {NIGHT};

.logo-sidebar {{
    background-image: url('{prefix}sidebar-bg.png');
    background-color: @fedora;
    background-repeat: no-repeat;
}}

.logo {{
    background-image: url('{prefix}sidebar-logo.png');
    background-position: 50% 20px;
    background-repeat: no-repeat;
    background-color: transparent;
}}

.product-logo {{
    background-image: none;
    background-color: transparent;
}}

AnacondaSpokeWindow #nav-box {{
    background-color: @fedora;
    background-image: url('{prefix}topbar-bg.png');
    background-repeat: repeat;
    color: white;
}}
""", encoding="utf-8")
    wordmark_logo((119, 36)).save(OUT / "anaconda_header.png")
    print("installer artwork written to", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
