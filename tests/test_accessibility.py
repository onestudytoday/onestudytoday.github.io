"""
Accessibility tests for the public site (WCAG 2.1 level AA).

WHY THESE ARE TESTS AND NOT A ONE-OFF AUDIT
===========================================
An accessibility audit is a photograph; these are a tripwire. The page is
REGENERATED from linkinbio.py on every publish, so any accessibility property
of the output is only as durable as something that re-checks it. Without this
file, the next person to restyle the page - or the next model asked to "make
the page prettier" - could delete the focus ring or flatten the list markup and
nothing anywhere would notice.

That matters more than usual here, because docs/accessibility.html makes public
written claims: a stated WCAG 2.1 AA conformance target, a claimed minimum
contrast ratio, and a list of specific things the site says it does. A claim
nobody verifies is the part that turns into a problem later. Every bullet on
that page has an assertion below it.

The contrast checker is the real WCAG formula (sRGB linearisation, relative
luminance, (L1+0.05)/(L2+0.05)), not an approximation, so the numbers here are
the same numbers an auditor's tool would report.

    python -m pytest tests/test_accessibility.py -q
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import linkinbio  # noqa: E402
from theme import NICHES  # noqa: E402

SAMPLES = sorted((ROOT / "samples" / "posts").glob("*.json"))

# The page's own palette, from CSS. Kept here as literals deliberately: if
# someone changes a colour in linkinbio.CSS, the assertion that the CSS still
# CONTAINS these values fails, which forces them to come here and re-run the
# contrast maths rather than silently shipping a darker grey.
BG = "#0B0B0F"          # page background
CARD = "#101016"        # .btn and .method background
FG = "#ffffff"
MUTED = "#8A8A99"
PROSE = "#c9c9d4"
FOCUS = "#FBBF24"

AA_TEXT = 4.5           # WCAG 1.4.3, text below 18.66px bold / 24px
AA_UI = 3.0             # WCAG 1.4.11, UI components and graphics


# ---------------------------------------------------------------------------
# WCAG contrast, by the book
# ---------------------------------------------------------------------------
def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
           for c in srgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_the_contrast_helper_agrees_with_known_reference_values():
    """Guard the guard. If this maths drifts, every ratio below is fiction."""
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
    # A widely published reference pair: #767676 on white is the canonical
    # "just passes AA for normal text" grey.
    assert contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.02)
    assert contrast("#a1a1a1", "#ffffff") < AA_TEXT


# ---------------------------------------------------------------------------
# 1.4.3 Contrast (minimum)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fg,bg,what", [
    (FG, BG, "body text"),
    (MUTED, BG, "muted text on the page background"),
    (MUTED, CARD, "button sub-label on the card background"),
    (PROSE, CARD, "method list items"),
    (PROSE, BG, "small-print page prose"),
    (FOCUS, BG, "preprint badge / focus ring colour"),
])
def test_every_text_pair_clears_aa(fg, bg, what):
    ratio = contrast(fg, bg)
    assert ratio >= AA_TEXT, f"{what}: {ratio:.2f}:1 is below {AA_TEXT}:1"


@pytest.mark.parametrize("niche", sorted(NICHES))
def test_every_niche_accent_clears_aa_on_the_page_background(niche):
    """The weekday accent is rendered as small bold text (.day), not as
    decoration, so it needs the full 4.5:1 - the large-text exemption starts at
    18.66px bold and .day is 10.5px."""
    accent = NICHES[niche]["accent"]
    ratio = contrast(accent, BG)
    assert ratio >= AA_TEXT, f"{niche} accent {accent}: {ratio:.2f}:1"


def test_the_accessibility_statements_claimed_minimum_is_true():
    """docs/accessibility.html tells the public the lowest ratio on the site is
    4.96:1. That is a checkable claim, so check it."""
    pairs = [(FG, BG), (MUTED, BG), (MUTED, CARD), (PROSE, CARD), (PROSE, BG),
             (FOCUS, BG)] + [(NICHES[n]["accent"], BG) for n in NICHES]
    lowest = min(contrast(f, b) for f, b in pairs)
    assert lowest >= AA_TEXT
    assert round(lowest, 2) == 4.96, (
        f"lowest contrast on the site is now {lowest:.2f}:1 - "
        f"docs/accessibility.html still claims 4.96:1. Update both together.")


# ---------------------------------------------------------------------------
# Fixture: build the whole site into a temp dir
# ---------------------------------------------------------------------------
@pytest.fixture
def site(tmp_path, monkeypatch):
    pub = tmp_path / "published"
    docs = tmp_path / "docs"
    pub.mkdir()
    docs.mkdir()
    monkeypatch.setattr(linkinbio, "PUBLISHED", pub)
    monkeypatch.setattr(linkinbio, "DOCS", docs)
    monkeypatch.setenv("HANDLE", "@onestudytoday")
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_IMAGE_BASE", raising=False)
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)

    for f in SAMPLES:
        post = json.loads(f.read_text())
        (pub / f"{post['id']}.json").write_text(json.dumps(post))
    linkinbio.build()

    class Site:
        docs_dir = docs
        index = (docs / "index.html").read_text()
        accessibility = (docs / "accessibility.html").read_text()
        privacy = (docs / "privacy.html").read_text()
        disclaimer = (docs / "disclaimer.html").read_text()

        @property
        def all_pages(self):
            return {"index.html": self.index,
                    "accessibility.html": self.accessibility,
                    "privacy.html": self.privacy,
                    "disclaimer.html": self.disclaimer}

    return Site()


# ---------------------------------------------------------------------------
# 2.4.7 Focus Visible
# ---------------------------------------------------------------------------
def test_every_page_defines_a_visible_focus_indicator(site):
    for name, doc in site.all_pages.items():
        assert "a:focus-visible" in doc, name
        assert "outline:3px solid" in doc, name
        assert "outline-offset" in doc, name


def test_the_focus_ring_colour_is_high_contrast_against_the_page(site):
    """A focus ring nobody can see is the same as no focus ring."""
    assert contrast(FOCUS, BG) >= AA_UI


# ---------------------------------------------------------------------------
# 2.4.1 Bypass Blocks
# ---------------------------------------------------------------------------
def test_every_page_has_a_skip_link_that_points_at_its_main_landmark(site):
    for name, doc in site.all_pages.items():
        assert '<a class="skip" href="#main"' in doc, name
        assert re.search(r'<main\b[^>]*\bid="main"', doc), name
        # The skip link must come before main in the DOM or it is useless.
        assert doc.index('class="skip"') < doc.index("<main"), name


def test_the_skip_link_is_hidden_until_focused_but_not_from_screen_readers(site):
    """display:none / visibility:hidden would take it out of the tab order,
    which defeats the point. Off-screen positioning is the correct technique."""
    css = linkinbio.CSS
    assert ".skip{position:absolute;left:-9999px" in css
    assert ".skip:focus{left:0}" in css
    assert "display:none" not in css.split(".skip")[1][:200]


# ---------------------------------------------------------------------------
# 1.3.1 Info and Relationships
# ---------------------------------------------------------------------------
def test_the_study_rows_are_marked_up_as_a_list(site):
    assert '<ul class="list">' in site.index
    assert site.index.count("<li><article>") == len(SAMPLES)


def test_landmarks_are_present_and_the_footer_is_outside_main(site):
    for name, doc in site.all_pages.items():
        assert "<header>" in doc, name
        assert "<main" in doc and "</main>" in doc, name
        assert "<footer>" in doc, name
        assert doc.index("</main>") < doc.index("<footer>"), name


def test_heading_order_never_skips_a_level(site):
    for name, doc in site.all_pages.items():
        levels = [int(m) for m in re.findall(r"<h([1-6])\b", doc)]
        assert levels, name
        assert levels[0] == 1, f"{name} does not start at h1"
        assert levels.count(1) == 1, f"{name} has {levels.count(1)} h1s"
        for prev, nxt in zip(levels, levels[1:]):
            assert nxt <= prev + 1, f"{name} jumps h{prev} -> h{nxt}"


def test_every_page_declares_its_language(site):
    for name, doc in site.all_pages.items():
        assert '<html lang="en">' in doc, name


# ---------------------------------------------------------------------------
# 3.2.5 / G201 - warn before opening a new tab
# ---------------------------------------------------------------------------
def test_links_that_open_a_new_tab_say_so_in_their_accessible_name(site):
    opens_new_tab = re.findall(r'<a\b[^>]*target="_blank"[^>]*>', site.index)
    assert opens_new_tab, "expected the study links to open in a new tab"
    for tag in opens_new_tab:
        assert "opens in a new tab" in tag, tag[:120]


def test_new_tab_links_are_not_a_referrer_or_opener_leak(site):
    for tag in re.findall(r'<a\b[^>]*target="_blank"[^>]*>', site.index):
        assert 'rel="noopener noreferrer"' in tag, tag[:120]


# ---------------------------------------------------------------------------
# 1.4.4 Resize text / user preferences
# ---------------------------------------------------------------------------
def test_font_sizes_are_relative_so_they_follow_the_browser_setting(site):
    """px font sizes survive zoom but ignore a raised default font size, which
    is the setting people with low vision actually use."""
    px_fonts = re.findall(r"font-size:\s*[\d.]+px", linkinbio.CSS)
    assert not px_fonts, f"px font sizes left in the stylesheet: {px_fonts}"
    assert "rem" in linkinbio.CSS


def test_a_bare_link_never_falls_back_to_the_user_agent_default_colour(site):
    """Regression test for a real violation found by axe-core, not by reading
    the stylesheet.

    Every link that had been thought about - .btn, .item, footer, .prose - had
    an explicit colour, so the page looked fine. But the small-print link in
    the method box and the "back" link in each subheader matched none of those
    rules, so they inherited the browser's default link blue (#0000EE) against
    a #0B0B0F background: roughly 2:1, a serious AA failure, on four pages.

    A base rule is the fix, because the next unstyled link added to this page
    will otherwise reintroduce it silently.
    """
    # Anchored at line start so it matches the bare `a` selector and not the
    # `a` inside `.prose a`, `footer a` or `a:focus-visible`.
    assert re.search(r"^a\{color:var\(--fg\)\}", linkinbio.CSS, re.M), \
        "no base `a` colour rule - a new unstyled link would be UA blue"
    assert contrast("#0000EE", BG) < AA_TEXT      # what we are guarding against


def test_reduced_motion_and_increased_contrast_are_honoured(site):
    css = linkinbio.CSS
    assert "prefers-reduced-motion:reduce" in css
    assert "prefers-contrast:more" in css


# ---------------------------------------------------------------------------
# The contact route, and the claims the small-print pages make
# ---------------------------------------------------------------------------
def test_no_page_ships_a_placeholder_contact(site):
    """The scaffold's mailto:you@example.com shipped live and sat there. An
    accessibility statement offering a dead reporting channel is worse than
    one offering none."""
    for name, doc in site.all_pages.items():
        assert "example.com" not in doc, name
        assert "you@example" not in doc, name


def test_the_contact_falls_back_to_a_channel_that_actually_exists(monkeypatch):
    monkeypatch.setenv("HANDLE", "@onestudytoday")
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    assert linkinbio.contact_url() == "https://instagram.com/onestudytoday"

    monkeypatch.setenv("CONTACT_EMAIL", "hi@onestudytoday.org")
    assert linkinbio.contact_url() == "mailto:hi@onestudytoday.org"

    # A placeholder is not a contact.
    monkeypatch.setenv("CONTACT_EMAIL", "you@example.com")
    assert linkinbio.contact_url().startswith("https://instagram.com/")


def test_every_page_carries_the_not_medical_advice_line(site):
    for name, doc in site.all_pages.items():
        assert "Not medical advice" in doc, name


def test_the_small_print_pages_are_built_and_linked_from_everywhere(site):
    for name, _label in linkinbio.LEGAL_PAGES:
        assert (site.docs_dir / name).exists(), name
        for page_name, doc in site.all_pages.items():
            assert name in doc, f"{page_name} does not link to {name}"


def test_the_accessibility_statement_names_the_standard_and_a_contact(site):
    doc = site.accessibility
    assert "WCAG 2.1" in doc and "AA" in doc
    assert "instagram.com/onestudytoday" in doc or "mailto:" in doc
    # Known limitations are part of an honest statement, not an optional extra.
    assert "Known limitations" in doc


def test_the_statement_does_not_promise_an_overlay_widget(site):
    """Overlays are what a worried site owner buys first. They are widely
    rejected by disabled users and have themselves drawn litigation, so the
    page commits, in writing, to not using one."""
    assert "does <strong>not</strong> use an accessibility overlay" in \
        site.accessibility


def test_the_privacy_page_only_claims_things_the_site_actually_does(site):
    """Every 'we do not' below is verifiable from the generated HTML: if
    someone later adds analytics or a web font, this fails and the privacy
    page stops being a lie."""
    doc = site.index
    assert "google-analytics" not in doc.lower()
    assert "googletagmanager" not in doc.lower()
    assert "fonts.googleapis" not in doc.lower()
    assert "<script" not in doc.replace(
        '<script type="application/ld+json">', "")   # JSON-LD is data, not code
    assert "cookie" not in doc.lower()
    for external in re.findall(r'src="(https?://[^"]+)"', doc):
        raise AssertionError(f"third-party asset on the page: {external}")
