"""
Builds the Instagram profile picture, matching the account's own visual
system exactly instead of a generic AI-illustration style: the same
near-black background as every carousel slide, and the same five weekday
accent colors from theme.py, arranged as an interlocking ring with the
"OST" wordmark set inside it.

Why this shape: the account's whole identity is "five niches, one rhythm."
A ring built from the five real accent colors, each overlapping into the
next, reads as one continuous loop rather than five separate pieces - the
five niches functioning as one account. "OST" (One Study Today) sits in
the ring's clear center, so the mark reads as a wordmark and a colophon
for the weekday rotation at the same time.

The overlap: each segment's clockwise (trailing) end is drawn on top of
the next segment's counter-clockwise (leading) end, all the way around -
green over purple, purple over red, red over blue, blue over yellow,
yellow over green. A closed loop can't satisfy that with a single global
draw order (it's a five-way cycle), so the one seam a plain top-to-bottom
draw order can't resolve - yellow/green - gets an explicit second pass
that redraws just that small overlapping wedge on top, after everything
else. Every seam ends up looking identical even though it took two
passes to get there.

Renders at 4x and downsamples for clean anti-aliased edges (PIL's own drawing
primitives are aliased at native resolution). Output is well above Instagram's
minimum (320x320) and safely square, since Instagram crops profile pictures
to a circle itself - nothing here depends on that crop being exact.

    python assets/build_profile_pic.py
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from theme import NICHES, FONTS  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out" / "profile_picture.png"

SCALE = 4
SIZE = 1024 * SCALE
BG = (11, 11, 15)  # #0B0B0F, identical to every carousel slide's background

# Monday -> Friday, the same order the account actually posts in, and the
# order the ring reads in clockwise from 12 o'clock.
ORDER = ["nature", "psych", "health", "physics", "wildcard"]

# How far each segment's trailing end pushes into the next segment's
# territory, in degrees. This is what makes the ring read as interlocking
# rather than five pieces with gaps between them.
OVERLAP_DEG = 14


def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def build():
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)

    cx, cy = SIZE / 2, SIZE / 2
    r = SIZE * 0.30
    width = int(SIZE * 0.075)
    bbox = [cx - r, cy - r, cx + r, cy + r]

    n = len(ORDER)
    seg_deg = 360 / n + OVERLAP_DEG
    start = -90 - (360 / n) / 2  # keeps each segment centered on its niche's clock position

    segs = []
    for i, niche in enumerate(ORDER):
        a0 = start + i * (360 / n)
        a1 = a0 + seg_deg
        segs.append((i, niche, a0, a1))

    def draw_cap(angle_deg, color):
        rad = math.radians(angle_deg)
        x, y = cx + r * math.cos(rad), cy + r * math.sin(rad)
        d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=color)

    # Base pass, drawn in reverse weekday order so nature ends up on top
    # overall - this alone gives the right overlap direction at four of
    # the five seams (green over purple, purple over red, red over blue,
    # blue over yellow).
    for i, niche, a0, a1 in sorted(segs, key=lambda s: -s[0]):
        color = _hex(NICHES[niche]["accent"])
        d.arc(bbox, a0, a1, fill=color, width=width)
        draw_cap(a0, color)
        draw_cap(a1, color)

    # Patch pass: redraw just the small overlapping wedge at wildcard's
    # tail on top of everything else, so the wildcard/nature seam also
    # goes yellow-over-green like every other seam in the ring.
    _, wc_niche, _wc_a0, wc_a1 = segs[4]
    wc_color = _hex(NICHES[wc_niche]["accent"])
    patch_deg = OVERLAP_DEG + 10
    d.arc(bbox, wc_a1 - patch_deg, wc_a1, fill=wc_color, width=width)
    draw_cap(wc_a1, wc_color)

    # "OST" set inside the ring's clear inner circle.
    inner_r = r - width / 2
    target_w = inner_r * 2 * 0.62  # leave a clear margin inside the ring

    probe_size = int(SIZE * 0.10)
    probe_font = ImageFont.truetype(FONTS["sans_black"], probe_size)
    pb = d.textbbox((0, 0), "OST", font=probe_font)
    probe_w = pb[2] - pb[0]
    font_size = max(1, int(probe_size * (target_w / probe_w)))
    font = ImageFont.truetype(FONTS["sans_black"], font_size)

    bb = d.textbbox((0, 0), "OST", font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx = cx - tw / 2 - bb[0]
    ty = cy - th / 2 - bb[1]
    d.text((tx, ty), "OST", font=font, fill=(255, 255, 255))

    # Soft glow pass, consistent with the carousel renderer's own glow accents.
    glow = img.filter(ImageFilter.GaussianBlur(radius=SIZE * 0.016))
    img = Image.blend(glow, img, 0.78)

    img = img.resize((1024, 1024), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT}  {img.size}")


if __name__ == "__main__":
    build()
