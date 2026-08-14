"""
One Study Today slide renderer.

Turns a structured post dict into a folder of 1080x1350 PNGs, one per slide,
ready to be uploaded and handed to the Instagram Graph API.

Everything is deterministic: same post JSON in, byte-identical PNGs out. That
matters because the review step happens on the rendered images, and you should
never publish something different from what you approved.

Inline markup supported in headline/body text:
    **word**   -> rendered in the niche accent color
"""

from __future__ import annotations

import os
import re
import textwrap
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from theme import (
    FONTS,
    H,
    NICHES,
    SAFE,
    THEMES,
    W,
    Theme,
    hex_rgb,
    hex_rgba,
    mix,
)

TOKEN_RE = re.compile(r"(\*\*.+?\*\*)")


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------
_font_cache: Dict[Tuple[str, int, str], ImageFont.FreeTypeFont] = {}


def font(key: str, size: int, variation: Optional[str] = None) -> ImageFont.FreeTypeFont:
    ck = (key, size, variation or "")
    if ck in _font_cache:
        return _font_cache[ck]
    f = ImageFont.truetype(FONTS[key], size)
    if variation:
        try:
            f.set_variation_by_name(variation)
        except Exception:
            pass
    _font_cache[ck] = f
    return f


def text_w(d: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    if not s:
        return 0.0
    base = d.textlength(s, font=f)
    return base + tracking * max(0, len(s) - 1)


def draw_tracked(
    d: ImageDraw.ImageDraw,
    xy: Tuple[float, float],
    s: str,
    f: ImageFont.FreeTypeFont,
    fill,
    tracking: float = 0.0,
) -> float:
    """Draw text with letter-spacing. Returns final x."""
    x, y = xy
    if tracking == 0:
        d.text((x, y), s, font=f, fill=fill)
        return x + d.textlength(s, font=f)
    for chgit in s:
        d.text((x, y), chgit, font=f, fill=fill)
        x += d.textlength(chgit, font=f) + tracking
    return x


# ---------------------------------------------------------------------------
# Rich-text (accent markup) line layout
# ---------------------------------------------------------------------------
def _split_runs(s: str) -> List[Tuple[str, bool]]:
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]"""
    out = []
    for part in TOKEN_RE.split(s):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], True))
        else:
            out.append((part, False))
    return out


def _plain(s: str) -> str:
    return s.replace("**", "")


def _wrap_para(
    d: ImageDraw.ImageDraw,
    s: str,
    f: ImageFont.FreeTypeFont,
    max_w: float,
    tracking: float,
) -> List[List[Tuple[str, bool]]]:
    words: List[Tuple[str, bool]] = []
    for txt, acc in _split_runs(s):
        for p in txt.split(" "):
            if p:
                words.append((p, acc))
    lines: List[List[Tuple[str, bool]]] = []
    cur: List[Tuple[str, bool]] = []
    cur_txt = ""
    for w_, acc in words:
        trial = (cur_txt + " " + w_).strip()
        if text_w(d, trial, f, tracking) <= max_w or not cur:
            cur.append((w_, acc))
            cur_txt = trial
        else:
            lines.append(cur)
            cur = [(w_, acc)]
            cur_txt = w_
    if cur:
        lines.append(cur)
    return lines


def wrap_runs(
    d: ImageDraw.ImageDraw,
    s: str,
    f: ImageFont.FreeTypeFont,
    max_w: float,
    tracking: float = 0.0,
) -> List[List[Tuple[str, bool]]]:
    """Word-wrap while preserving accent runs and paragraph breaks.

    A blank line in the source becomes an empty line in the output, which the
    drawer renders as vertical space. Returns lines of runs.
    """
    out: List[List[Tuple[str, bool]]] = []
    paras = [p for p in re.split(r"\n\s*\n|\n", s)]
    first = True
    for para in paras:
        if not para.strip():
            continue
        if not first:
            out.append([])  # paragraph gap
        out.extend(_wrap_para(d, para.strip(), f, max_w, tracking))
        first = False
    return out


def fit_runs(
    d: ImageDraw.ImageDraw,
    s: str,
    font_key: str,
    max_w: float,
    max_h: float,
    size_hi: int,
    size_lo: int,
    leading: float,
    tracking: float = 0.0,
    variation: Optional[str] = None,
    max_lines: int = 99,
) -> Tuple[ImageFont.FreeTypeFont, List[List[Tuple[str, bool]]], int, int]:
    """Binary-search the largest font size where the wrapped text fits the box."""
    best = None
    lo, hi = size_lo, size_hi
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font(font_key, mid, variation)
        tr = tracking * (mid / 100.0)
        lines = wrap_runs(d, s, f, max_w, tr)
        lh = int(mid * leading)
        total = lh * len(lines)
        if total <= max_h and len(lines) <= max_lines:
            best = (f, lines, lh, mid)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        f = font(font_key, size_lo, variation)
        tr = tracking * (size_lo / 100.0)
        lines = wrap_runs(d, s, f, max_w, tr)
        best = (f, lines, int(size_lo * leading), size_lo)
    return best


def draw_runs(
    d: ImageDraw.ImageDraw,
    x: float,
    y: float,
    lines: List[List[Tuple[str, bool]]],
    f: ImageFont.FreeTypeFont,
    lh: int,
    fill,
    accent_fill,
    tracking: float = 0.0,
) -> float:
    space = d.textlength(" ", font=f)
    for line in lines:
        cx = x
        for i, (word, acc) in enumerate(line):
            col = accent_fill if acc else fill
            cx = draw_tracked(d, (cx, y), word, f, col, tracking)
            if i != len(line) - 1:
                cx += space + tracking
        y += lh
    return y


# ---------------------------------------------------------------------------
# Chrome: labels, badges, footers
# ---------------------------------------------------------------------------
def draw_label(d, th: Theme, niche: Dict, x: int, y: int, text: str, accent: str, on_color: bool):
    f = font(th.label_font, th.label_size)
    tr = 3.2
    tw = text_w(d, text, f, tr)
    if th.badge_style == "pill":
        pad_x, pad_y = 26, 15
        box = (x, y, x + tw + pad_x * 2, y + th.label_size + pad_y * 2)
        d.rounded_rectangle(box, radius=(th.label_size + pad_y * 2) // 2, fill=accent)
        draw_tracked(d, (x + pad_x, y + pad_y - 2), text, f, "#0B0B0F", tr)
        return box[3] - y
    if th.badge_style == "bar":
        d.rectangle((x, y, x + 74, y + 8), fill="#FFFFFF")
        draw_tracked(d, (x, y + 30), text, f, "#FFFFFF", tr)
        return 30 + th.label_size + 12
    # rule
    draw_tracked(d, (x, y), text, f, accent, tr)
    d.rectangle((x, y + th.label_size + 18, x + tw, y + th.label_size + 20), fill=accent)
    return th.label_size + 30


def draw_preprint_badge(d, th: Theme, x: int, y: int) -> int:
    """Non-negotiable badge. If a post is a preprint this is always drawn."""
    f = font("sans_bold", 24)
    txt = "PREPRINT · NOT YET PEER REVIEWED"
    tr = 2.4
    tw = text_w(d, txt, f, tr)
    pad_x, pad_y = 22, 14
    warn = "#FBBF24"
    box = (x, y, x + tw + pad_x * 2, y + 24 + pad_y * 2)
    d.rounded_rectangle(box, radius=10, fill=None, outline=warn, width=3)
    draw_tracked(d, (x + pad_x, y + pad_y - 3), txt, f, warn, tr)
    return box[3] - y


def draw_footer(d, th: Theme, post: Dict, idx: int, total: int, accent: str, on_color: bool):
    fg = th.fg
    muted = th.muted if not on_color else "#FFFFFF"
    f = font("sans_med", 24)
    y = H - SAFE - 30
    src = post["study"]["journal"]
    if post["study"].get("is_preprint"):
        src = f"{post['study']['server']} preprint"
    left = f"{src} · {post['study']['pub_date_display']}"
    d.text((SAFE, y), left, font=f, fill=hex_rgba(muted, 0.85 if on_color else 1.0))
    # slide counter
    cf = font("sans_bold", 24)
    ctxt = f"{idx}/{total}"
    cw = d.textlength(ctxt, font=cf)
    d.text((W - SAFE - cw, y), ctxt, font=cf, fill=accent if not on_color else "#FFFFFF")
    # progress rail
    rail_y = H - SAFE + 14
    d.rectangle((SAFE, rail_y, W - SAFE, rail_y + 4), fill=hex_rgba(muted, 0.28))
    seg = (W - SAFE * 2) / total
    d.rectangle((SAFE + seg * (idx - 1), rail_y, SAFE + seg * idx, rail_y + 4),
                fill=accent if not on_color else "#FFFFFF")


def _handle() -> str:
    """Read the handle lazily so tests and samples do not need a full env."""
    try:
        from config import settings
        return settings().handle
    except Exception:
        import os
        return os.environ.get("HANDLE", "@onestudytoday")


def draw_handle(d, th: Theme, accent: str, on_color: bool,
                align: str = "right", y: int = None, size: int = 26):
    """Account handle. Appears on the cover and the CTA slide only - putting it
    on every slide reads as insecurity, and the carousel is already branded by
    the colour system."""
    handle = _handle()
    f = font("sans_bold", size)
    tr = 2.0
    col = accent if not on_color else "#FFFFFF"
    y = SAFE if y is None else y
    if align == "right":
        w_ = text_w(d, handle, f, tr)
        draw_tracked(d, (W - SAFE - w_, y), handle, f, col, tr)
    else:
        draw_tracked(d, (SAFE, y), handle, f, col, tr)


# ---------------------------------------------------------------------------
# Background painters
# ---------------------------------------------------------------------------
def make_canvas(th: Theme, niche: Dict, kind: str) -> Image.Image:
    if th.use_niche_bg:
        base = niche["block_bg"]
        if kind != "cover":
            base = mix(base, "#000000", 0.18)
        img = Image.new("RGB", (W, H), hex_rgb(base))
        return img

    img = Image.new("RGB", (W, H), hex_rgb(th.bg))

    if th.key == "neon" and th.extras.get("glow"):
        # soft radial accent bloom, bottom-left, very low alpha
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        r = 520 if kind == "cover" else 380
        cx, cy = (170, H - 190) if kind == "cover" else (W - 120, 150)
        g.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hex_rgba(niche["accent"], 0.16))
        glow = glow.filter(ImageFilter.GaussianBlur(150))
        img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    if th.key == "editorial" and th.extras.get("hairline"):
        d = ImageDraw.Draw(img)
        d.rectangle((SAFE, SAFE + 62, W - SAFE, SAFE + 63), fill=hex_rgb(th.rule))
        d.rectangle((SAFE, H - SAFE - 62, W - SAFE, H - SAFE - 61), fill=hex_rgb(th.rule))
    return img


# ---------------------------------------------------------------------------
# Slide renderers
# ---------------------------------------------------------------------------
def render_cover(post: Dict, th: Theme, niche: Dict, idx: int, total: int) -> Image.Image:
    img = make_canvas(th, niche, "cover")
    d = ImageDraw.Draw(img)
    accent = niche["accent"]
    on_color = th.use_niche_bg

    y = SAFE
    label_h = draw_label(d, th, niche, SAFE, y, niche["label"], accent, on_color)
    # handle sits opposite the niche pill, optically centred against it
    draw_handle(d, th, accent, on_color, align="right",
                y=y + max(0, (label_h - 26) // 2) - 2)
    y += label_h + 26

    if post["study"].get("is_preprint"):
        y += draw_preprint_badge(d, th, SAFE, y) + 26

    box_w = W - SAFE * 2
    box_h = H - y - 300
    head = post["cover"]["headline"]
    if th.uppercase_head:
        head = head.upper()
    f, lines, lh, size = fit_runs(
        d, head, th.head_font, box_w, box_h,
        th.head_max, th.head_min, th.head_leading, th.head_tracking,
        variation="Bold" if th.head_font == "serif" else None,
    )
    # bottom-align the headline block so covers feel anchored
    block_h = lh * len(lines)
    y_head = H - SAFE - 150 - block_h
    y_head = max(y_head, y)
    tr = th.head_tracking * (size / 100.0)
    draw_runs(d, SAFE, y_head, lines, f, lh, th.fg, accent if not on_color else "#FFFFFF", tr)

    # kicker above headline
    if post["cover"].get("kicker"):
        kf = font("sans_med", 30)
        d.text((SAFE, y_head - 56), post["cover"]["kicker"], font=kf,
               fill=hex_rgba(th.muted, 0.9))

    draw_footer(d, th, post, idx, total, accent, on_color)
    return img


def render_body(post: Dict, slide: Dict, th: Theme, niche: Dict, idx: int, total: int) -> Image.Image:
    img = make_canvas(th, niche, "body")
    d = ImageDraw.Draw(img)
    accent = niche["accent"]
    on_color = th.use_niche_bg

    y = SAFE
    # eyebrow: THE SETUP / WHAT THEY FOUND / THE CATCH
    ef = font("sans_bold", 26)
    tr = 3.4
    draw_tracked(d, (SAFE, y), slide["eyebrow"].upper(), ef,
                 accent if not on_color else "#FFFFFF", tr)
    y += 62

    box_w = W - SAFE * 2
    # slide title
    tf, tlines, tlh, tsize = fit_runs(
        d, slide["title"], th.head_font, box_w, 300,
        int(th.head_max * 0.62), int(th.head_min * 0.82), th.head_leading + 0.04,
        th.head_tracking * 0.6,
        variation="Bold" if th.head_font == "serif" else None,
        max_lines=3,
    )
    ttr = th.head_tracking * 0.6 * (tsize / 100.0)
    y = draw_runs(d, SAFE, y, tlines, tf, tlh, th.fg,
                  accent if not on_color else "#FFFFFF", ttr) + 34

    # accent rule under title
    d.rectangle((SAFE, y - 12, SAFE + 120, y - 6),
                fill=accent if not on_color else "#FFFFFF")
    y += 26

    # body copy - reserve room for the stat callout so it can never collide
    # with the footer rail
    avail_h = H - y - 230 - (176 if slide.get("stat") else 0)
    bf, blines, blh, bsize = fit_runs(
        d, slide["body"], th.body_font, box_w, avail_h,
        th.body_size + 6, th.body_size - 10, th.body_leading, 0,
    )
    y = draw_runs(d, SAFE, y, blines, bf, blh,
                  th.fg if not on_color else "#FFFFFF",
                  accent if not on_color else "#FFFFFF", 0)

    # optional stat callout
    if slide.get("stat"):
        y += 26
        sf = font("sans_black", 86)
        d.text((SAFE, y), slide["stat"]["value"], font=sf,
               fill=accent if not on_color else "#FFFFFF")
        lf = font("sans_med", 28)
        d.text((SAFE, y + 100), slide["stat"]["label"], font=lf,
               fill=hex_rgba(th.muted if not on_color else "#FFFFFF", 0.9))

    draw_footer(d, th, post, idx, total, accent, on_color)
    return img


def render_caveat(post: Dict, th: Theme, niche: Dict, idx: int, total: int) -> Image.Image:
    """The honesty slide. Always present. This is the account's whole reputation."""
    img = make_canvas(th, niche, "body")
    d = ImageDraw.Draw(img)
    accent = niche["accent"]
    on_color = th.use_niche_bg
    warn = "#FBBF24" if th.key != "editorial" else "#B45309"

    y = SAFE
    ef = font("sans_bold", 26)
    draw_tracked(d, (SAFE, y), "HOLD ON — THE FINE PRINT", ef,
                 warn if not on_color else "#FFFFFF", 3.4)
    y += 76

    box_w = W - SAFE * 2
    items: List[str] = post["caveats"]
    bullet_font = font(th.body_font, th.body_size - 2)
    for it in items:
        # marker
        d.ellipse((SAFE, y + 16, SAFE + 14, y + 30),
                  fill=warn if not on_color else "#FFFFFF")
        bf, blines, blh, bsz = fit_runs(
            d, it, th.body_font, box_w - 46, 400,
            th.body_size, th.body_size - 8, th.body_leading, 0,
        )
        y = draw_runs(d, SAFE + 46, y, blines, bf, blh,
                      th.fg if not on_color else "#FFFFFF",
                      accent if not on_color else "#FFFFFF", 0)
        y += 30

    draw_footer(d, th, post, idx, total, accent, on_color)
    return img


def render_cta(post: Dict, th: Theme, niche: Dict, idx: int, total: int) -> Image.Image:
    img = make_canvas(th, niche, "cover")
    d = ImageDraw.Draw(img)
    accent = niche["accent"]
    on_color = th.use_niche_bg

    box_w = W - SAFE * 2

    # ---- source card (height computed from content so nothing ever collides)
    pad = 34
    lbl_size, ttl_size, doi_size = 23, 27, 24
    lf = font("sans_bold", lbl_size)
    tf2 = font(th.body_font, ttl_size)
    df = font("sans_med", doi_size)

    card_w = W - SAFE * 2
    inner_w = card_w - pad * 2
    ttl_lines = _wrap_para(d, post["study"]["title"], tf2, inner_w, 0)[:3]
    ttl_lh = int(ttl_size * 1.34)

    card_h = pad + lbl_size + 22 + ttl_lh * len(ttl_lines) + 18 + doi_size + pad
    card_bottom = H - SAFE - 74
    card_top = card_bottom - card_h

    d.rounded_rectangle((SAFE, card_top, W - SAFE, card_bottom), radius=22,
                        outline=hex_rgba(th.muted if not on_color else "#FFFFFF", 0.45),
                        width=3)

    yy = card_top + pad
    draw_tracked(d, (SAFE + pad, yy), "READ THE ORIGINAL STUDY", lf,
                 accent if not on_color else "#FFFFFF", 2.6)
    yy += lbl_size + 22
    yy = draw_runs(d, SAFE + pad, yy, ttl_lines, tf2, ttl_lh,
                   th.fg if not on_color else "#FFFFFF",
                   accent if not on_color else "#FFFFFF", 0)
    yy += 18
    d.text((SAFE + pad, yy), post["study"]["doi_display"], font=df,
           fill=hex_rgba(th.muted if not on_color else "#FFFFFF", 0.9))

    # ---- headline + sub fill the space above the card
    sub_size = th.body_size - 4
    sub_lines = _wrap_para(d, post["cta"]["sub"], font(th.body_font, sub_size), box_w, 0)
    sub_h = int(sub_size * th.body_leading) * len(sub_lines)

    draw_handle(d, th, accent, on_color, align="left", y=SAFE, size=34)

    y = SAFE + 74
    avail = (card_top - 56) - y - sub_h - 40
    f, lines, lh, size = fit_runs(
        d, post["cta"]["headline"], th.head_font, box_w, avail,
        int(th.head_max * 0.82), th.head_min, th.head_leading, th.head_tracking,
        variation="Bold" if th.head_font == "serif" else None,
    )
    tr = th.head_tracking * (size / 100.0)
    y = draw_runs(d, SAFE, y, lines, f, lh, th.fg,
                  accent if not on_color else "#FFFFFF", tr) + 40
    y = draw_runs(d, SAFE, y, sub_lines, font(th.body_font, sub_size),
                  int(sub_size * th.body_leading),
                  hex_rgba(th.muted if not on_color else "#FFFFFF", 0.95),
                  accent if not on_color else "#FFFFFF", 0)

    draw_footer(d, th, post, idx, total, accent, on_color)
    return img


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def render_post(post: Dict, theme_key: str, outdir: str, prefix: str = "") -> List[str]:
    th = THEMES[theme_key]
    niche = NICHES[post["niche"]]
    os.makedirs(outdir, exist_ok=True)

    slides = post["slides"]
    total = 1 + len(slides) + (1 if post.get("caveats") else 0) + 1

    paths: List[str] = []
    i = 1
    img = render_cover(post, th, niche, i, total)
    p = os.path.join(outdir, f"{prefix}{i:02d}_cover.png")
    img.save(p, "PNG", optimize=True)
    paths.append(p)

    for s in slides:
        i += 1
        img = render_body(post, s, th, niche, i, total)
        p = os.path.join(outdir, f"{prefix}{i:02d}_{s['eyebrow'].lower().replace(' ', '_')}.png")
        img.save(p, "PNG", optimize=True)
        paths.append(p)

    if post.get("caveats"):
        i += 1
        img = render_caveat(post, th, niche, i, total)
        p = os.path.join(outdir, f"{prefix}{i:02d}_caveats.png")
        img.save(p, "PNG", optimize=True)
        paths.append(p)

    i += 1
    img = render_cta(post, th, niche, i, total)
    p = os.path.join(outdir, f"{prefix}{i:02d}_cta.png")
    img.save(p, "PNG", optimize=True)
    paths.append(p)

    return paths


def contact_sheet(paths: List[str], out: str, cols: int = 5, scale: float = 0.34):
    """Single strip image so you can eyeball a whole carousel at once."""
    ims = [Image.open(p) for p in paths]
    tw, thh = int(W * scale), int(H * scale)
    gap = 18
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * gap,
                              rows * thh + (rows + 1) * gap), (28, 28, 34))
    for i, im in enumerate(ims):
        r, c = divmod(i, cols)
        sheet.paste(im.resize((tw, thh), Image.LANCZOS),
                    (gap + c * (tw + gap), gap + r * (thh + gap)))
    sheet.save(out, "PNG", optimize=True)
    return out
