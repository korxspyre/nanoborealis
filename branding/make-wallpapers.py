# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Renders the NanoBorealis wallpaper: an aurora over mountains and a still lake, at night.

    python branding/make-wallpapers.py            # needs numpy and pillow

Writes the KDE wallpaper package under system_files/usr/share/wallpapers/NanoBorealis and the
background of the README banner. Deterministic: the same seed paints the same sky.

The scene is built the way a matte painting is: sky and stars, then the aurora (one hero curtain
folding across the upper third, fainter ones behind it, rays converging overhead, a green core with
a magenta fringe), then bloom, then mountain ridges with atmospheric perspective and aurora rim
light, then the lake that mirrors it all, then a grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "system_files/usr/share/wallpapers/NanoBorealis"
F = np.float32


# -- Helpers -----------------------------------------------------------------------------------------


def smooth(values: np.ndarray, sigma_px: float) -> np.ndarray:
    """Gaussian-smooth a 1D signal (sigma in pixels)."""
    sigma = max(1.0, sigma_px)
    radius = int(3 * sigma)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    return np.convolve(np.pad(values, radius, mode="reflect"), kernel / kernel.sum(), mode="valid")


def fbm1d(n: int, rng: np.random.Generator, octaves: int, base: int, gain: float = 0.5) -> np.ndarray:
    """Fractal noise along one axis in 0..1, cosine-interpolated so it has no corners."""
    xs = np.linspace(0, 1, n)
    out = np.zeros(n)
    amp, total = 1.0, 0.0
    for octave in range(octaves):
        points = base * 2 ** octave + 1
        ctrl = rng.random(points)
        pos = xs * (points - 1)
        i0 = np.floor(pos).astype(int).clip(0, points - 2)
        t = (1 - np.cos((pos - i0) * np.pi)) / 2
        out += amp * (ctrl[i0] * (1 - t) + ctrl[i0 + 1] * t)
        total += amp
        amp *= gain
    out /= total
    return ((out - out.min()) / (np.ptp(out) or 1)).astype(F)


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Exact Gaussian blur (FFT, reflect-padded) of an (H, W) or (H, W, C) float image."""
    if img.ndim == 3:
        return np.stack([blur(img[..., c], sigma) for c in range(img.shape[2])], axis=-1)
    pad = int(3 * sigma) + 1
    a = np.pad(img, pad, mode="reflect")
    fy = np.fft.fftfreq(a.shape[0]).astype(F)[:, None]
    fx = np.fft.rfftfreq(a.shape[1]).astype(F)[None, :]
    kernel = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    out = np.fft.irfft2(np.fft.rfft2(a) * kernel, s=a.shape)
    return out[pad:-pad, pad:-pad].astype(F)


def ramp(t: np.ndarray, stops: list[tuple[float, tuple[float, float, float]]]) -> np.ndarray:
    t = np.clip(t, 0, 1)
    pos = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops], dtype=float)
    return np.stack([np.interp(t, pos, cols[:, c]).astype(F) for c in range(3)], axis=-1)


# -- The painting --------------------------------------------------------------------------------------


def render(width: int, height: int, seed: int = 11) -> Image.Image:
    rng = np.random.default_rng(seed)
    horizon = 0.74  # where the lake begins, as a fraction of the height
    sky_h = int(height * horizon)
    ys = np.linspace(0, horizon, sky_h, dtype=F)[:, None]  # sky rows, in whole-image units
    xs = np.linspace(0, 1, width, dtype=F)[None, :]
    xline = xs[0]
    scale = width / 3840

    # Sky: deep indigo at the zenith, a cool teal airglow toward the horizon.
    sky = ramp(np.broadcast_to(ys / horizon, (sky_h, width)),
               [(0.0, (9, 9, 30)), (0.35, (12, 16, 46)), (0.75, (14, 36, 62)), (1.0, (22, 54, 70))])

    # Milky Way: a faint diagonal band of haze and denser stars.
    band = np.exp(-0.5 * ((ys - (0.12 + 0.55 * xs)) / 0.09) ** 2).astype(F)
    haze = blur(rng.random((sky_h, width), dtype=F) ** 3, 18 * scale) * 2.2
    sky += (band * (0.35 + haze))[..., None] * np.array([26, 24, 40], dtype=F)

    # Stars: many faint, a few bright, warm to cool, denser in the Milky Way, fewer near the horizon.
    n = int(width * sky_h / 700)
    sx = rng.integers(0, width, n)
    sy = rng.integers(0, sky_h, n)
    weight = (1 - sy / sky_h) ** 0.7 * (0.55 + 1.6 * band[sy, sx])
    stars = np.zeros((sky_h, width), dtype=F)
    np.add.at(stars, (sy, sx), (rng.pareto(3.0, n) * 0.30 * weight).astype(F))
    point = blur(stars, 0.7 * scale) * 2.6 + blur(stars, 2.2 * scale) * 1.1
    temperature = ramp(blur(rng.random((sky_h, width), dtype=F), 40 * scale),
                       [(0.0, (255, 214, 190)), (0.5, (255, 255, 255)), (1.0, (190, 210, 255))])
    sky += np.clip(point, 0, 3)[..., None] * temperature

    # Aurora. Each curtain hangs from a hem line; its light climbs along rays that lean toward a
    # vanishing point overhead, brightest just above the hem and where the curtain folds.
    aurora = np.zeros((sky_h, width, 3), dtype=F)
    vanish_x = 0.58

    def curtain(hem: np.ndarray, reach: float, strength: float, offset: int, fringe: float,
                focus: float, spread: float) -> None:
        crng = np.random.default_rng(seed * 97 + offset)
        above = hem[None, :] - ys
        up = np.clip(above, 0, None)
        u = np.clip(xs - (xs - vanish_x) * up * 0.28, 0, 1)           # a slight perspective lean
        idx = (u * (width * 2 - 1)).astype(np.int32)
        fine = fbm1d(width * 2, crng, octaves=5, base=90, gain=0.62)
        coarse = fbm1d(width * 2, crng, octaves=3, base=14, gain=0.55)
        rays = (0.22 + 0.78 * fine[idx] ** 1.7) * (0.45 + 0.55 * coarse[idx])
        curvature = np.abs(np.gradient(np.gradient(smooth(hem, width * 0.004))))
        fold = (1 + 1.6 * curvature / (curvature.max() + 1e-9)).astype(F)
        # Above the hem the curtain glows; just below it, a little light scatters into the air.
        glow = np.where(above >= 0, np.exp(-up / reach) * (1 - np.exp(-up / 0.012)),
                        np.exp(above / 0.035) * 0.10).astype(F)
        hemline = np.exp(-0.5 * (above / 0.0045) ** 2).astype(F)
        # Brightest around its focus; the ends of the curtain fade into the sky.
        envelope = (0.12 + 0.88 * np.exp(-0.5 * ((xline - focus) / spread) ** 2)).astype(F)
        light = (glow * rays + hemline * rays ** 2 * 0.55) * (fold * envelope)[None, :] * strength
        tint = ramp(up / (reach * 2.2), [(0.0, (150, 255, 210)), (0.06, (70, 250, 170)), (0.35, (40, 215, 170)),
                                         (0.62, (60, 150, 200)), (0.82, (150, 90, 150 + 70 * fringe)),
                                         (1.0, (210, 80, 190))])
        aurora[...] += light[..., None] * tint

    # The hero curtain rises from the lower left, folds past the center and trails off to the right.
    hero = (0.50 - 0.20 * xline + 0.05 * np.sin(xline * np.pi * 2.3 + 0.6)
            + 0.035 * (fbm1d(width, np.random.default_rng(seed + 1), 3, 3) - 0.5))
    curtain(smooth(hero, width * 0.006).astype(F), reach=0.17, strength=1.2, offset=1, fringe=1.0, focus=0.62, spread=0.24)
    back = 0.30 - 0.08 * xline + 0.04 * np.sin(xline * np.pi * 3.1 + 2.0)
    curtain(smooth(back, width * 0.008).astype(F), reach=0.16, strength=0.5, offset=2, fringe=0.6, focus=0.30, spread=0.22)
    low = 0.64 - 0.03 * xline + 0.015 * np.sin(xline * np.pi * 5 + 1.0)
    curtain(smooth(low, width * 0.01).astype(F), reach=0.08, strength=0.35, offset=3, fringe=0.2, focus=0.82, spread=0.18)

    # Bloom: the aurora lights the air around it.
    sky += aurora + blur(aurora, width / 120) * 0.55 + blur(aurora, width / 40) * 0.35

    # Ridges, far to near: hazier and bluer with distance, rim-lit by the aurora above them.
    light_above = aurora.mean(axis=0)
    row = np.arange(sky_h, dtype=F)[:, None]
    ridges = [(0.62, 0.050, 5, (24, 44, 70), 0.55), (0.67, 0.070, 6, (13, 26, 46), 0.35),
              (0.715, 0.055, 7, (5, 9, 20), 0.18)]
    for i, (level, rough, octaves, color, rim_strength) in enumerate(ridges):
        profile = fbm1d(width, np.random.default_rng(seed * 13 + i), octaves=octaves, base=5, gain=0.52)
        ridge = ((level - rough * profile + 0.02 * np.sin(xline * np.pi * (1.4 + i))) * height).astype(F)
        below = row - ridge[None, :]
        inside = np.clip(below + 1, 0, 1)
        depth = np.clip(below / (height * 0.12), 0, 1)
        body = np.array(color, dtype=F)[None, None, :] * (1 - 0.35 * depth[..., None])
        rim = np.exp(-np.clip(below, 0, None) / (height * 0.004))
        body = body + rim[..., None] * light_above[None, :, :] * (rim_strength * 0.9)
        sky = sky * (1 - inside[..., None]) + body * inside[..., None]

    # The lake mirrors the sky, slightly squashed, rippled, darker and cooler.
    lake_h = height - sky_h
    rows = np.arange(lake_h)
    reflection = sky[np.clip(sky_h - 1 - (rows * 1.05).astype(int), 0, sky_h - 1)]
    ripple = np.sin(rows[:, None] * 0.9 + xline[None, :] * 60) * 0.5 + np.sin(rows[:, None] * 2.3 + xline[None, :] * 170) * 0.5
    shift = (ripple * (1.5 + rows[:, None] / lake_h * 6)).astype(int)
    reflection = reflection[rows[:, None], np.clip(np.arange(width)[None, :] + shift, 0, width - 1)]
    lake = reflection * np.linspace(0.62, 0.30, lake_h, dtype=F)[:, None, None] * np.array([0.82, 0.92, 1.0], dtype=F)
    lake = lake + np.array([2, 5, 10], dtype=F)
    lake *= (1 - 0.7 * np.exp(-rows / (height * 0.004)))[:, None, None].astype(F)  # dark shoreline
    image = np.concatenate([sky, lake], axis=0) / 255.0

    # Grade: soft highlight shoulder, vignette, fine monochrome grain.
    image = image / (1 + image * 0.35) * 1.18
    yy = np.linspace(0, 1, height, dtype=F)[:, None]
    xx = np.linspace(0, 1, width, dtype=F)[None, :]
    vignette = 1 - 0.35 * ((((xx - 0.55) * width / height / 1.6) ** 2) + ((yy - 0.45) * 1.1) ** 2)
    image = np.clip(image * vignette[..., None], 0, None) ** 0.96
    image = np.clip(image + rng.normal(0, 0.0045, (height, width, 1)).astype(F), 0, 1)
    return Image.fromarray((image * 255 + 0.5).astype(np.uint8), "RGB")


def main() -> int:
    (PACKAGE / "contents/images").mkdir(parents=True, exist_ok=True)
    full = render(3840, 2160)
    full.save(PACKAGE / "contents/images/3840x2160.jpg", quality=93, optimize=True, progressive=True)
    for size in ((2560, 1440), (1920, 1080)):
        full.resize(size, Image.LANCZOS).save(PACKAGE / f"contents/images/{size[0]}x{size[1]}.jpg", quality=92, optimize=True)
    full.resize((1280, 720), Image.LANCZOS).save(PACKAGE / "contents/screenshot.jpg", quality=88, optimize=True)
    (PACKAGE / "metadata.json").write_text(
        '{\n  "KPlugin": {\n    "Authors": [{"Name": "NanoBorealis"}],\n    "Id": "NanoBorealis",\n'
        '    "Name": "NanoBorealis"\n  }\n}\n', encoding="utf-8")
    full.crop((0, 160, 3840, 160 + 1280)).resize((2400, 800), Image.LANCZOS).save(
        ROOT / "branding/banner-background.jpg", quality=92, optimize=True)
    print("wallpapers written to", PACKAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
