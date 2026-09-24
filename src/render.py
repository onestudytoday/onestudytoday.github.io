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
from urllib.parse import urlsplit
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
    stroke: int = 0,
    stroke_fill=None,
) -> float:
    """Draw text with letter-spacing. Returns final x.

    `stroke` outlines each glyph. It exists so the cover can keep a real
    photograph visible behind the headline instead of dimming the picture
    until it is texture: an outline buys contrast at the glyph edge, which is
    where legibility is actually decided, rather than across the whole frame.
    """
    kw = {"stroke_width": stroke, "stroke_fill": stroke_fill} if stroke else {}
    x, y = xy
    if tracking == 0:
        d.text((x, y), s, font=f, fill=fill, **kw)
        return x + d.textlength(s, font=f)
    for chgit in s:
        d.text((x, y), chgit, font=f, fill=fill, **kw)
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
    # Each word carries a third flag: "join me to the word before, with no
    # space".
    #
    # THE BUG THIS FIXES. An accent run that ends mid-phrase is followed by
    # punctuation outside the asterisks - "**switches on**, not just firing
    # it." _split_runs returns ("...switches on", accent) and (", not just...",
    # plain); this function then split each run on spaces independently, so the
    # comma became a WORD of its own and the drawer put a space in front of it:
    #
    #     genes a cell switches on , not just firing it.
    #
    # Live on the cover of any post whose highlighted phrase did not happen to
    # end a sentence. The implication-first cover makes that the common case,
    # because the accent now lands on the stake in the middle of the line
    # rather than on the finding at the end of it.
    words: List[Tuple[str, bool, bool]] = []
    open_word = False          # the previous run ended without whitespace
    for txt, acc in _split_runs(s):
        pieces = txt.split(" ")
        for i, p in enumerate(pieces):
            if not p:
                # An empty piece means there was real whitespace here.
                open_word = False
                continue
            words.append((p, acc, open_word and i == 0))
            open_word = False
        open_word = bool(txt) and not txt.endswith(" ")

    def _join(ws: List[Tuple[str, bool, bool]]) -> str:
        s_ = ""
        for i_, (t_, _a, g_) in enumerate(ws):
            s_ += t_ if (g_ and i_) else ((" " if i_ else "") + t_)
        return s_

    lines: List[List[Tuple[str, bool, bool]]] = []
    cur: List[Tuple[str, bool, bool]] = []
    cur_txt = ""
    for w_, acc, glue in words:
        if glue and cur:
            # A glued piece is never put on a line of its own - a line that
            # starts with a comma is the same defect wearing a hat - but it is
            # still MEASURED.
            #
            # The first version of this just appended and skipped the width
            # test, on the assumption that a glued piece is a stray comma. It
            # is not always: `**the drug works**—unimaginably` glues a whole
            # word, and that headline ran 490px past the right edge of a
            # 1080px canvas and was drawn off the image. fit_runs only
            # binary-searches on HEIGHT, so nothing downstream catches an
            # overlong line.
            #
            # So when it does not fit, the previous word and everything glued
            # to it move down to the next line together, as the one unit they
            # render as.
            trial = cur_txt + w_
            if text_w(d, trial, f, tracking) <= max_w or len(cur) == 1:
                cur.append((w_, acc, True))
                cur_txt = trial
                continue
            atom: List[Tuple[str, bool, bool]] = [cur.pop()]
            while cur and atom[0][2]:
                atom.insert(0, cur.pop())
            lines.append(cur)
            cur = atom + [(w_, acc, True)]
            cur_txt = _join(cur)
            continue
        trial = (cur_txt + " " + w_).strip()
        if text_w(d, trial, f, tracking) <= max_w or not cur:
            cur.append((w_, acc, False))
            cur_txt = trial
        else:
            lines.append(cur)
            cur = [(w_, acc, False)]
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
    stroke: int = 0,
    stroke_fill=None,
) -> float:
    space = d.textlength(" ", font=f)
    for line in lines:
        cx = x
        for i, item in enumerate(line):
            # Tolerant of the old 2-tuple shape, so a caller that builds lines
            # by hand does not start drawing punctuation against the margin.
            word, acc, *rest = item
            glue = bool(rest and rest[0])
            if i and not glue:
                cx += space + tracking
            col = accent_fill if acc else fill
            cx = draw_tracked(d, (cx, y), word, f, col, tracking,
                              stroke=stroke, stroke_fill=stroke_fill)
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


# The disclosure, when a post's implication is ours rather than the paper's.
#
# It used to be a three-word stamp - "Our read, not the paper's claim" - on the
# cover and on the implications slide. It now says what it means, once, as a
# forced bullet on the fine-print slide. Three reasons that is better:
#
#   * it tells the reader what to DO about it (go and read the paper), which a
#     stamp cannot
#   * the fine-print slide is where this account already puts everything it is
#     honest about, so a reader looking for the catch finds it in one place
#   * the cover is a photograph with one sentence over it, and every extra
#     line competes with the only thing a scroller actually reads
#
# Defined HERE, beside the code that draws it, and re-exported by draft.py -
# because build_prompt() and audit() both TELL a model this disclosure is
# printed, and the auditor stands down on the implication BECAUSE it exists.
# A promise and its implementation that live in different files drift; this
# one already did once, and was never drawn at all.
DISCLOSURE_CAVEAT = ("Claims are our interpretation of this study, read it "
                     "for yourself at the DOI provided.")

# The old stamp. Kept importable because posts drafted before this change have
# no record of which disclosure they carried, and a future reader of the git
# history should be able to find the string that used to be on those images.
INFERRED_MARK = "Our read, not the paper's claim"


def _inferred(post: Dict) -> bool:
    """Is this post's implication our own extrapolation, not the paper's claim?

    Read straight off the slides rather than through draft.is_inferred().

    The first version imported draft lazily inside a try/except that returned
    False on any failure. That fails OPEN on the one thing this function
    decides: whether the cover prints "OUR READ, NOT THE PAPER'S CLAIM". An
    import error - draft.py reaches config and the Meta credentials - would
    have dropped the marker from the slide that travels, silently, while the
    audit had already stood down precisely because that marker exists. The
    disclosure has to be the thing that is hardest to lose, not the easiest.

    The rule itself is two lines and needs no import, so there is nothing left
    to fail.
    """
    for sl in post.get("slides") or []:
        if isinstance(sl, dict) and str(sl.get("basis") or "") == "inferred":
            return True
    return False


def draw_footer(d, th: Theme, post: Dict, idx: int, total: int, accent: str,
                on_color: bool, stroke: int = 0, stroke_fill=None):
    kw = {"stroke_width": stroke, "stroke_fill": stroke_fill} if stroke else {}
    fg = th.fg
    muted = th.muted if not on_color else "#FFFFFF"
    f = font("sans_med", 24)
    y = H - SAFE - 30
    src = post["study"]["journal"]
    if post["study"].get("is_preprint"):
        src = f"{post['study']['server']} preprint"
    left = f"{src} · {post['study']['pub_date_display']}"
    d.text((SAFE, y), left, font=f,
           fill="#FFFFFF" if stroke else hex_rgba(muted, 0.85 if on_color else 1.0),
           **kw)
    # slide counter
    cf = font("sans_bold", 24)
    ctxt = f"{idx}/{total}"
    cw = d.textlength(ctxt, font=cf)
    d.text((W - SAFE - cw, y), ctxt, font=cf,
           fill=accent if not on_color else "#FFFFFF", **kw)
    # progress rail
    rail_y = H - SAFE + 14
    d.rectangle((SAFE, rail_y, W - SAFE, rail_y + 4), fill=hex_rgba(muted, 0.28))
    seg = (W - SAFE * 2) / total
    d.rectangle((SAFE + seg * (idx - 1), rail_y, SAFE + seg * idx, rail_y + 4),
                fill=accent if not on_color else "#FFFFFF")


def _handle() -> str:
    """Read the handle lazily so tests and samples do not need a full env.

    THE EXCEPT CLAUSE WAS NOT CATCHING ANYTHING.
    ===========================================
    config._req() signals a missing setting with `raise SystemExit(...)`, and
    SystemExit derives from BaseException, NOT from Exception. So this
    function - whose entire docstring is "tests and samples do not need a full
    env" - let the four Meta credentials propagate anyway, and rendering a
    slide demanded the ability to publish.

    That is the same all-or-nothing settings() edge that has broken a
    non-publishing workflow step three separate times (see the placeholder
    comments in ci.yml, apply-edits.yml and publish-on-approve.yml). Here it
    had been "fixed" in a way that read correctly and did nothing: `pytest
    tests/ -q` on a clean checkout failed twenty-one rendering tests on a
    missing META_APP_ID, for a handle with a default.

    Drawing an account handle needs a string. It does not need a credential.
    """
    import os
    env = os.environ.get("HANDLE", "").strip()
    if env:
        return env
    try:
        from config import settings
        return settings().handle
    except (Exception, SystemExit):
        return "@onestudytoday"


def draw_handle(d, th: Theme, accent: str, on_color: bool,
                align: str = "right", y: int = None, size: int = 26,
                stroke: int = 0, stroke_fill=None):
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
        draw_tracked(d, (W - SAFE - w_, y), handle, f, col, tr,
                     stroke=stroke, stroke_fill=stroke_fill)
    else:
        draw_tracked(d, (SAFE, y), handle, f, col, tr,
                     stroke=stroke, stroke_fill=stroke_fill)


# ---------------------------------------------------------------------------
# Background painters
# ---------------------------------------------------------------------------
# Deliberately LIGHT. An earlier version ramped to 0.94 at the bottom, which
# kept the type crisp by erasing the photograph exactly where the frame had
# room to show it. The picture is the point of the feature, so contrast is
# bought at the glyph edge instead - see COVER_TEXT_STROKE below - and the
# scrim only takes the edge off so the outline is not doing all the work.
COVER_SCRIM_TOP = 0.10
COVER_SCRIM_BOTTOM = 0.40
COVER_SCRIM_KNEE = 0.45

# Outline width for cover type over a photograph, in pixels at 1080 wide.
# 6 is enough to survive a busy, light background without reading as a
# cartoon outline at the sizes this headline is set in.
COVER_TEXT_STROKE = 6
COVER_CHROME_STROKE = 3


def cover_photo_canvas(th: Theme, path: str) -> Optional[Image.Image]:
    """The cover background, from a photograph, or None if it cannot be used.

    A GRADIENT scrim, not a flat one, and that is the whole design.

    A uniform scrim strong enough to keep white type legible over an unknown
    photograph is strong enough to erase the photograph - the picture becomes
    texture and the point is lost. A uniform scrim weak enough to show the
    picture cannot guarantee contrast, because the next photo might be a
    snow field. Ramping it means the top of the frame keeps the image and the
    bottom third - where the kicker, headline and footer all live - is close
    to flat brand colour, so contrast does not depend on what the picture
    happens to contain.

    Returns None rather than raising for anything at all: a cover with no
    photograph is exactly what every post looks like today.
    """
    try:
        src = Image.open(path)
        src.load()
        src = src.convert("RGB")
    except Exception:
        return None

    # Cover the frame, centre-cropped - never letterboxed. A band of flat
    # colour above a photo reads as a mistake rather than a choice.
    scale = max(W / src.width, H / src.height)
    src = src.resize((max(1, int(src.width * scale)),
                      max(1, int(src.height * scale))), Image.LANCZOS)
    left, top = (src.width - W) // 2, (src.height - H) // 2
    photo = src.crop((left, top, left + W, top + H))

    flat = Image.new("RGB", (W, H), hex_rgb(th.bg))
    ramp = Image.new("L", (1, H))
    for y in range(H):
        f = y / (H - 1)
        if f < COVER_SCRIM_KNEE:
            a = COVER_SCRIM_TOP
        else:
            k = (f - COVER_SCRIM_KNEE) / (1 - COVER_SCRIM_KNEE)
            a = COVER_SCRIM_TOP + (COVER_SCRIM_BOTTOM - COVER_SCRIM_TOP) * (k ** 1.4)
        ramp.putpixel((0, y), int(a * 255))
    return Image.composite(flat, photo, ramp.resize((W, H)))


def draw_cover_credit(d, th: Theme, text: str, stroke: int = 0,
                      stroke_fill=None) -> None:
    """The licence credit, small, above the footer, right-aligned.

    On the IMAGE rather than only in the caption, because a CC-BY credit has
    to travel with the work: captions get truncated in the feed, and a
    screenshot of the cover carries no caption at all. Small and muted so it
    reads as a credit and not as part of the copy.
    """
    if not text:
        return
    f = font("sans_med", 19)
    w_ = d.textlength(text, font=f)
    d.text((W - SAFE - w_, H - SAFE - 64), text, font=f,
           fill="#FFFFFF" if stroke else hex_rgba(th.muted, 0.62),
           **({"stroke_width": stroke, "stroke_fill": stroke_fill} if stroke else {}))


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
    art = post.get("cover_art") or {}
    img = None
    if art.get("path"):
        img = cover_photo_canvas(th, art["path"])
    on_photo = img is not None
    if img is None:
        img = make_canvas(th, niche, "cover")
    d = ImageDraw.Draw(img)
    # Outline colour is the theme's own background, so the type looks set
    # INTO the design rather than stickered on top of a photo.
    stroke_col = hex_rgb(th.bg)
    hs = COVER_TEXT_STROKE if on_photo else 0
    cs = COVER_CHROME_STROKE if on_photo else 0
    accent = niche["accent"]
    on_color = th.use_niche_bg

    y = SAFE
    label_h = draw_label(d, th, niche, SAFE, y, niche["label"], accent, on_color)
    # handle sits opposite the niche pill, optically centred against it
    draw_handle(d, th, accent, on_color, align="right",
                y=y + max(0, (label_h - 26) // 2) - 2,
                stroke=cs, stroke_fill=stroke_col)
    y += label_h + 26

    if post["study"].get("is_preprint"):
        y += draw_preprint_badge(d, th, SAFE, y) + 26

    # No "our read" marker here, and no kicker.
    #
    # Both used to sit on this slide and both were taken off deliberately. The
    # cover is a photograph with one sentence over it; every line added to it
    # is a line competing with the only thing a scroller will actually read.
    # The kicker ("Nature - 12 human brains") also repeated the footer rail,
    # which already carries the journal and the date on every single page.
    #
    # The disclosure did NOT disappear - it moved to the fine-print slide as a
    # full sentence, which says more than a three-word stamp did. See
    # DISCLOSURE_CAVEAT below and the paragraph draft.audit() sends the
    # fact-checker, which has to keep describing where it actually is.
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
    # CENTRE the headline block in what is left of the frame.
    #
    # It used to be hard bottom-aligned - `H - SAFE - 150 - block_h` - which
    # put a constant 90px under the text and everything else above it. The
    # gap above therefore depended entirely on how long the headline was:
    # measured on real covers, a six-line headline left 316px above and a
    # three-line one left 619px. So no two covers sat the same way, and every
    # one of them read as slightly wrong rather than deliberately low.
    #
    # The top of the available area is `y`, which has already been advanced
    # past the niche pill, the handle and the preprint badge if there is one.
    # The bottom is the footer rail. Centring between those two is stable
    # whatever the headline length and whatever chrome is above it.
    block_h = lh * len(lines)
    bottom = H - SAFE - 100          # the footer rail and its breathing room
    y_head = y + max(0, (bottom - y - block_h) // 2)
    tr = th.head_tracking * (size / 100.0)
    draw_runs(d, SAFE, y_head, lines, f, lh, th.fg,
              accent if not on_color else "#FFFFFF", tr,
              stroke=hs, stroke_fill=stroke_col)

    # The kicker is no longer drawn - see the note further up. The FIELD is
    # kept on the post: the drafting model still writes it, style.py reads it
    # when it checks whether the copy names the study's subject, and the
    # review card shows it. It just is not on the picture any more.

    if on_photo:
        # A credit that cannot be drawn must not be shrugged off.
        #
        # This used to `except Exception: pass`, which meant any failure here
        # published a CC-BY photograph with no attribution - a licence breach,
        # and silent. If the credit cannot be rendered, the PHOTOGRAPH is what
        # gets dropped: falling back to the flat cover costs nothing, and the
        # flat cover is what every post looked like last week.
        from coverart import credit_line
        line = credit_line(art)
        if line:
            try:
                draw_cover_credit(d, th, line, stroke=2, stroke_fill=stroke_col)
            except Exception as e:
                print(f"  ! cover credit could not be drawn ({type(e).__name__}); "
                      f"dropping the photograph rather than publishing it "
                      f"uncredited")
                return render_cover({**post, "cover_art": {}}, th, niche, idx, total)
    draw_footer(d, th, post, idx, total, accent, on_color,
                stroke=cs, stroke_fill=stroke_col)
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

    # No marker here either - the disclosure is one sentence on the fine-print
    # slide now. See DISCLOSURE_CAVEAT.

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

    # The disclosure is APPENDED HERE, not read out of post["caveats"].
    #
    # That is the whole point of it being in the renderer. post["caveats"] is
    # model-written, hand-editable in the queue document, and rewritable by a
    # `revise:` comment - three ways for the one sentence that says "this
    # conclusion is ours" to go missing from an image. Building it in at draw
    # time means the only way to publish an inferred implication without the
    # disclosure is to stop marking it inferred, and lint() already treats
    # that as a guardrail breach.
    #
    # It is also not counted against the 2-4 caveat budget in copy_spec.yaml,
    # because it is not one of the paper's limitations - it is ours.
    items: List[str] = list(post["caveats"])
    if _inferred(post):
        items.append(DISCLOSURE_CAVEAT)
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


def _display_url(raw: str, limit: int = 62) -> str:
    """The web address as a person would read it out.

    Scheme and `www.` dropped, query string and fragment dropped, and the
    middle elided if it is long - a tracking-laden URL set in small type is
    noise, and the point of printing it is that somebody can find the article.

    PARSED, not stripped with a regex. `https://www.reuters.com@evil.example/a`
    has a hostname of evil.example, but chopping the scheme off the front with
    a substitution prints "www.reuters.com@evil.example/a" - and a reader
    glancing at small grey type under a screenshot reads the first thing that
    looks like a domain. clipping.outlet_for() parses properly and would
    already have refused that URL, so this is the second lock on the same
    door; it is here because this function is what a reader actually sees.
    """
    parts = urlsplit(str(raw or "").strip())
    if parts.hostname:
        host = parts.hostname[4:] if parts.hostname.startswith("www.") else parts.hostname
        s = host + (parts.path or "")
    else:
        # No scheme, so nothing to parse - fall back to the raw text rather
        # than printing nothing.
        s = str(raw or "").strip().split("?")[0].split("#")[0]
    s = s.rstrip("/")
    if len(s) <= limit:
        return s
    head, tail = s[: limit - 18], s[-14:]
    return f"{head}.../{tail.lstrip('/')}"


def render_clipping(post: Dict, th: Theme, niche: Dict, idx: int, total: int) -> Image.Image:
    """The article's own headline, photographed, with its address underneath.

    Nothing on this page is set in our typeface except the address. The
    screenshot is the outlet's own page - their masthead type, their column
    width, their byline - because a headline re-typeset in our fonts reads as
    US saying it, which is the exact impression this page exists to avoid.

    So: no eyebrow label, no line of our own commentary, no quotation card. A
    picture of somebody else's page, and a web address so a reader can go and
    check it. If the screenshot is missing this function is never called -
    clipping.get() treats a record without one as no clipping at all.
    """
    img = make_canvas(th, niche, "body")
    d = ImageDraw.Draw(img)
    accent = niche["accent"]
    on_color = th.use_niche_bg
    clip = post.get("clipping") or {}

    box_w = W - SAFE * 2
    url_font = font("sans_med", 25)
    url_text = _display_url(clip.get("url"))
    url_h = 40

    # The shot gets the whole page minus the footer rail and the address line.
    avail_h = H - SAFE * 2 - url_h - 26 - 90
    shot = None
    try:
        shot = Image.open(str(clip.get("shot"))).convert("RGB")
    except Exception:
        shot = None
    if shot is None:
        # Should be unreachable - render_post only calls this when a shot
        # exists - so say so loudly rather than publishing an empty page.
        raise FileNotFoundError(
            f"clipping screenshot missing for {post.get('id')}: "
            f"{clip.get('shot')!r}")

    scale = min(box_w / shot.width, avail_h / shot.height)
    if scale < 1:
        shot = shot.resize((max(1, int(shot.width * scale)),
                            max(1, int(shot.height * scale))),
                           Image.LANCZOS)

    x = SAFE + (box_w - shot.width) // 2
    y = SAFE + max(0, (avail_h - shot.height) // 2)

    # A hairline around the shot, so a white article page does not bleed into
    # a light theme's background and stop reading as a separate object.
    d.rectangle((x - 2, y - 2, x + shot.width + 1, y + shot.height + 1),
                fill=None, outline=hex_rgba(th.muted, 0.45), width=2)
    img.paste(shot, (x, y))

    if url_text:
        draw_tracked(d, (x, y + shot.height + 22), url_text, url_font,
                     hex_rgba(th.muted if not on_color else "#FFFFFF", 0.95),
                     0.8)

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
def _slug(text: str) -> str:
    """An eyebrow, as a safe filename component.

    The eyebrow used to go straight into the path as
    `.lower().replace(" ", "_")`, which is fine for the labels the spec allows
    and not fine for the ones that can actually arrive. postdoc.apply_markdown
    takes the eyebrow verbatim from a Markdown HEADING, so a reviewer renaming
    `## Slide 1 - What they found` to `... - What they found / measured` put a
    slash in a path and render_post died with FileNotFoundError - after the
    edited copy had already been written to the queue. lint() rejects an
    eyebrow outside the spec, but it rejects it as a blocker on a post that
    has to render first to be looked at.
    """
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s[:48] or "slide"


def render_post(post: Dict, theme_key: str, outdir: str, prefix: str = "") -> List[str]:
    th = THEMES[theme_key]
    niche = NICHES[post["niche"]]
    os.makedirs(outdir, exist_ok=True)

    slides = post["slides"]

    # The clipping page, when there is one the reviewer has not excluded. It
    # goes straight after the FINDING - which is the slide after the
    # implications one.
    #
    # It used to go directly after the implications slide, and that was right
    # while the finding came first: the clipping corroborates the implication,
    # and corroboration three slides later is just trivia. Moving the
    # implication in front of the finding made "next to the implication" and
    # "before the evidence" the same position, which is the wrong one - a
    # newspaper agreeing with a claim the reader has not yet seen supported
    # reads as padding it. Claim, evidence, then who else says so.
    #
    # Imported lazily and guarded, so a post rendered in a workflow where
    # clipping.py is unavailable - or one drafted before clippings existed -
    # renders exactly as it did before.
    clip_after = -1
    try:
        from clipping import is_shown
        if is_shown(post):
            from draft import IMPLICATIONS_INDEX, implications_slide
            sl = implications_slide(post)
            at = next((n for n, s in enumerate(slides) if s is sl), None)
            clip_after = (IMPLICATIONS_INDEX if at is None else at) + 1
    except Exception:
        clip_after = -1
    has_clip = 0 <= clip_after < len(slides)

    total = (1 + len(slides) + (1 if has_clip else 0)
             + (1 if post.get("caveats") else 0) + 1)

    paths: List[str] = []
    i = 1
    img = render_cover(post, th, niche, i, total)
    p = os.path.join(outdir, f"{prefix}{i:02d}_cover.png")
    img.save(p, "PNG", optimize=True)
    paths.append(p)

    for n, s in enumerate(slides):
        i += 1
        img = render_body(post, s, th, niche, i, total)
        p = os.path.join(outdir, f"{prefix}{i:02d}_{_slug(s['eyebrow'])}.png")
        img.save(p, "PNG", optimize=True)
        paths.append(p)
        if has_clip and n == clip_after:
            i += 1
            img = render_clipping(post, th, niche, i, total)
            p = os.path.join(outdir, f"{prefix}{i:02d}_clipping.png")
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
