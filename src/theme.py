"""
Theme + typography system for One Study Today carousels.

Three themes are defined. Each is a complete visual system: palette, font stack,
and layout metrics. Swap the active theme in config/settings.yaml.

Canvas is 1080x1350 (4:5 portrait) - the largest footprint Instagram allows in
feed, which means the most vertical pixels per scroll.
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple

# --------------------------------------------------------------------------
# Canvas
# --------------------------------------------------------------------------
W, H = 1080, 1350
SAFE = 84  # side margin; IG never crops this, and it keeps text off the edge

# --------------------------------------------------------------------------
# Fonts. Vendored into assets/fonts/ (SIL Open Font License) so the GitHub
# Actions runner renders identically to your laptop, with a fallback to the
# system Google-fonts directory if the vendored copy is missing.
# --------------------------------------------------------------------------
from pathlib import Path as _Path

# Fonts are vendored into assets/fonts/ so a GitHub Actions runner renders
# byte-identical output to your laptop. Poppins and Lora are SIL Open Font
# License; see assets/fonts/OFL.txt.
_VENDOR = _Path(__file__).resolve().parent.parent / "assets" / "fonts"
_SYSTEM = _Path("/usr/share/fonts/truetype/google-fonts")


def _font_path(name: str, system_dir: _Path = _SYSTEM) -> str:
    v = _VENDOR / name
    if v.exists():
        return str(v)
    return str(system_dir / name)


FONTS = {
    "sans_black": _font_path("Poppins-Bold.ttf"),
    "sans_bold": _font_path("Poppins-Bold.ttf"),
    "sans_med": _font_path("Poppins-Medium.ttf"),
    "sans_reg": _font_path("Poppins-Regular.ttf"),
    "sans_light": _font_path("Poppins-Light.ttf"),
    "serif": _font_path("Lora-Variable.ttf"),
    "serif_italic": _font_path("Lora-Italic-Variable.ttf"),
    "mono": _font_path("LiberationMono-Bold.ttf",
                       _Path("/usr/share/fonts/truetype/liberation")),
}

# --------------------------------------------------------------------------
# Niche accents - one per weekday. These carry across all themes so the
# weekday rhythm of the profile grid stays legible no matter the theme.
# --------------------------------------------------------------------------
NICHES: Dict[str, Dict[str, str]] = {
    "nature": {
        "label": "NATURE & ENVIRONMENT",
        "short": "NATURE",
        "day": "Monday",
        "accent": "#22C55E",
        "accent_deep": "#0F7A3D",
        "block_bg": "#12703F",
    },
    "psych": {
        "label": "PSYCHOLOGY & NEUROSCIENCE",
        "short": "MIND",
        "day": "Tuesday",
        "accent": "#A855F7",
        "accent_deep": "#6B21A8",
        "block_bg": "#5B1E9E",
    },
    "health": {
        "label": "HEALTH & MEDICINE",
        "short": "HEALTH",
        "day": "Wednesday",
        "accent": "#F43F5E",
        "accent_deep": "#9F1239",
        "block_bg": "#A81F42",
    },
    "physics": {
        "label": "PHYSICS & SPACE",
        "short": "COSMOS",
        "day": "Thursday",
        "accent": "#38BDF8",
        "accent_deep": "#0369A1",
        "block_bg": "#0E5C87",
    },
    "wildcard": {
        "label": "STUDY OF THE WEEK",
        "short": "WILDCARD",
        "day": "Friday",
        "accent": "#FBBF24",
        "accent_deep": "#B45309",
        "block_bg": "#A05A06",
    },
}


@dataclass
class Theme:
    key: str
    name: str
    bg: str
    fg: str
    muted: str
    rule: str
    # typography
    head_font: str
    head_weight: str
    body_font: str
    label_font: str
    head_max: int
    head_min: int
    head_leading: float
    head_tracking: float
    body_size: int
    body_leading: float
    label_size: int
    uppercase_head: bool = False
    # per-theme switches
    use_niche_bg: bool = False       # theme "block": background = niche color
    accent_on_bg: bool = True        # accent text readable on bg
    badge_style: str = "pill"        # pill | bar | rule
    extras: Dict = field(default_factory=dict)


THEMES: Dict[str, Theme] = {
    # ---------------------------------------------------------------- A
    "neon": Theme(
        key="neon",
        name="Bold dark, neon accent",
        bg="#0B0B0F",
        fg="#FFFFFF",
        muted="#8A8A99",
        rule="#23232E",
        head_font="sans_black",
        head_weight="black",
        body_font="sans_reg",
        label_font="sans_bold",
        head_max=104,
        head_min=52,
        head_leading=0.98,
        head_tracking=-2.0,
        body_size=40,
        body_leading=1.42,
        label_size=25,
        uppercase_head=False,
        badge_style="pill",
        extras={"glow": True, "corner_bar": True},
    ),
    # ---------------------------------------------------------------- B
    "block": Theme(
        key="block",
        name="Big color blocks per niche",
        bg="#12703F",  # overridden per-niche
        fg="#FFFFFF",
        muted="#FFFFFF",
        rule="#FFFFFF",
        head_font="sans_black",
        head_weight="black",
        body_font="sans_med",
        label_font="sans_bold",
        head_max=110,
        head_min=54,
        head_leading=0.94,
        head_tracking=-2.4,
        body_size=41,
        body_leading=1.40,
        label_size=26,
        uppercase_head=True,
        use_niche_bg=True,
        accent_on_bg=False,
        badge_style="bar",
        extras={"muted_alpha": 0.72},
    ),
    # ---------------------------------------------------------------- C
    "editorial": Theme(
        key="editorial",
        name="Clean light, editorial",
        bg="#FAF7F2",
        fg="#16130F",
        muted="#6E675E",
        rule="#D8D1C6",
        head_font="serif",
        head_weight="bold",
        body_font="sans_light",
        label_font="sans_bold",
        head_max=92,
        head_min=46,
        head_leading=1.06,
        head_tracking=-1.0,
        body_size=38,
        body_leading=1.52,
        label_size=23,
        uppercase_head=False,
        badge_style="rule",
        extras={"hairline": True, "drop_cap": True},
    ),
}


def hex_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hex_rgba(h: str, a: float) -> Tuple[int, int, int, int]:
    r, g, b = hex_rgb(h)
    return (r, g, b, int(255 * a))


def mix(a: str, b: str, t: float) -> str:
    ar, ag, ab = hex_rgb(a)
    br, bg_, bb = hex_rgb(b)
    return "#%02X%02X%02X" % (
        int(ar + (br - ar) * t),
        int(ag + (bg_ - ag) * t),
        int(ab + (bb - ab) * t),
    )


def relative_luminance(h: str) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)
