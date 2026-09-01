"""
Your own link-in-bio page. No Linktree, no monthly fee, no third party
deciding what your links look like.

It builds docs/index.html from data/published/*.json and GitHub Pages serves
it at https://<username>.github.io/<repo>/ - that URL goes in your Instagram
bio. Every post that publishes automatically adds itself, newest first,
grouped by week.

Why not Linktree: it costs money for anything beyond the basics, it puts its
own branding on your page, it can't auto-populate from your posts, and you
don't own the URL. This is free, matches your carousel design exactly, and
updates itself as a side effect of publishing.

It also writes docs/sitemap.xml and docs/robots.txt, and puts schema.org
structured data on the page. This is the account's ONLY channel that keeps
working on old posts - an Instagram post is dead a week after it goes up, but
a page listing every study covered, with the journal, the date and a link to
the paper, is exactly the thing search engines are built to surface. The
markup is what makes that possible, so it is generated here, from the same
data as the page, rather than hand-maintained and left to drift.

    python src/linkinbio.py
"""

from __future__ import annotations

import html
import json
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

from config import DOCS, PUBLISHED
from config import _opt as _opt_setting
from config import handle as handle_setting
from theme import NICHES

def contact_url() -> str:
    """Where a reader goes to reach a human. Never a dead link.

    This used to be the literal string "mailto:you@example.com" - the scaffold
    placeholder, shipped live and never replaced, so the one contact button on
    the page opened a mail client addressed to nobody.

    That is worse than cosmetic now: an accessibility statement whose "report a
    problem" route does not work is not a statement, it is a liability, because
    it documents in writing that you offered a channel and the channel was
    fake. So the fallback is not another placeholder - it is the Instagram
    profile, which is a real inbox this account definitely owns and monitors.

    Set CONTACT_EMAIL to use email instead.
    """
    email = _opt_setting("CONTACT_EMAIL", "").strip()
    if email and "@" in email and not email.lower().endswith("example.com"):
        return f"mailto:{email}"
    return f"https://instagram.com/{handle_setting().lstrip('@')}"


def contact_label() -> str:
    return ("Email us" if contact_url().startswith("mailto:")
            else "Message us on Instagram")


def profile_links() -> List[Dict[str, str]]:
    """The big buttons at the top of the page."""
    return [
        {"label": "Suggest a study", "url": contact_url(),
         "note": "Found something worth covering?"},
        {"label": "How we pick and check studies", "url": "#method",
         "note": "The vetting rules, in plain English"},
    ]


# ---------------------------------------------------------------------------
# Where this page lives. Needed by every SEO tag on it: a canonical link, an
# og:url and a sitemap are all statements of absolute location, and a relative
# one is worse than none (Google treats a wrong canonical as a instruction to
# drop the page).
# ---------------------------------------------------------------------------
DEFAULT_SITE_URL = "https://onestudytoday.github.io/"


def site_url() -> str:
    """Absolute base URL of the published page, with a trailing slash.

    Read through config._opt for the same reason handle() exists: rebuilding
    this page never touches the Graph API, and settings() would demand all
    four Meta credentials to hand back a string (see config.handle() and
    tests/test_copy.py::test_linkinbio_build_does_not_require_instagram_
    credentials). This deliberately does NOT call settings().

    Order of preference:
      1. SITE_URL, if someone sets it explicitly.
      2. PUBLIC_IMAGE_BASE with its trailing "/img" removed - that variable is
         already documented as "https://<user>.github.io/<repo>/img" and is
         set in CI, so the site root falls out of it for free.
      3. DEFAULT_SITE_URL.

    Anything that is not an http(s) URL is discarded rather than emitted: a
    malformed canonical is an actively harmful tag, an unset one is merely a
    missing one.

    NOTE: .github/workflows/scheduled-publish.yml's "Rebuild the link-in-bio
    page" step passes only HANDLE into its env, so in CI this currently lands
    on DEFAULT_SITE_URL. That is correct for @onestudytoday today; adding
    PUBLIC_IMAGE_BASE (or SITE_URL) to that step's env is what makes it follow
    the repo if the Pages URL ever changes.
    """
    base = _opt_setting("SITE_URL", "").strip()
    if not base:
        img = _opt_setting("PUBLIC_IMAGE_BASE", "").strip().rstrip("/")
        if img.lower().endswith("/img"):
            img = img[: -len("/img")]
        base = img
    if urlsplit(base).scheme not in ("http", "https") or not urlsplit(base).netloc:
        base = DEFAULT_SITE_URL
    return base.rstrip("/") + "/"


def _safe_url(raw: Any, schemes: tuple = ("http", "https")) -> str:
    """A link target we are willing to put in an href, or "".

    Study URLs come from public research databases, i.e. from strangers.
    html.escape() stops them breaking out of the attribute but says nothing
    about the SCHEME, and `javascript:...` in an href is script execution on
    the one page the account's Instagram bio points at. Allow-list instead.
    """
    u = str(raw or "").strip()
    if not u:
        return ""
    if u.startswith("#"):
        return u if "#" in schemes else ""
    scheme = urlsplit(u).scheme.lower()
    if scheme in schemes and (scheme == "mailto" or urlsplit(u).netloc):
        return u
    return ""


def _xml_escape(s: str) -> str:
    """XML text/attribute escaping. html.escape covers & < > " ' and every one
    of those replacements is a legal XML entity or numeric reference."""
    return html.escape(str(s), quote=True)


def _iso_date(raw: Any) -> str:
    """`raw` as a yyyy-mm-dd string, or "" if it is not a date.

    Machine-readable dates (<time datetime>, sitemap lastmod, datePublished)
    have to be real dates; feeding a parser a database's free-text date field
    is how you get a sitemap that Search Console rejects wholesale.
    """
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        return ""


def _week_key(iso: str) -> str:
    d = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_label(key: str) -> str:
    y, w = key.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    sunday = date.fromisocalendar(int(y), int(w), 7)
    if monday.month == sunday.month:
        return f"{monday.strftime('%b %-d')}–{sunday.strftime('%-d, %Y')}"
    return f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}"


# ---------------------------------------------------------------------------
# Styles.
#
# Font sizes are in rem, not px. Browser ZOOM scales px fine, so px is not a
# 1.4.4 failure on its own - but a reader who has raised their browser's
# default font size (the setting people with low vision actually use, because
# it follows them across every site) gets nothing from a px value. rem honours
# it. The rem numbers below are the old px values / 16 and render identically
# at default settings.
#
# The palette is unchanged: every text pair on this page already clears WCAG
# 1.4.3 AA at 4.5:1 (the weakest is the muted grey at 5.58:1 on the card
# background, and the lowest-contrast niche accent, psych purple, at 4.96:1).
# Contrast was never the problem here, so nothing is "fixed" that was not
# broken - recolouring a passing palette would only have cost the design.
# ---------------------------------------------------------------------------
CSS = """
:root{--bg:#0B0B0F;--fg:#fff;--muted:#8A8A99;--line:#23232E;--focus:#FBBF24}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:1rem/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;
-webkit-font-smoothing:antialiased}
/* Base link colour. Without this, any <a> not covered by a more specific rule
   below falls back to the user agent's default link blue (#0000EE), which
   against this #0B0B0F background is about 2:1 - a real, serious contrast
   failure, and one that is easy to miss precisely because the styled links
   (.btn, .item, footer, .prose) all look fine. It was caught by running
   axe-core over the built pages, not by reading the stylesheet.
   Underline is left on deliberately: a link inside a paragraph of muted text
   must not rely on colour alone to be identifiable (WCAG 1.4.1). */
a{color:var(--fg)}
.wrap{max-width:620px;margin:0 auto;padding:3.25rem 1.375rem 5.625rem}
.brand{font-size:1.875rem;font-weight:800;letter-spacing:-1px;margin:0}
.tag{color:var(--muted);margin:.625rem 0 2.125rem;font-size:.9375rem}
.btn{display:block;border:1px solid var(--line);border-radius:14px;padding:1.0625rem 1.25rem;
margin-bottom:.75rem;text-decoration:none;color:#fff;transition:.15s;background:#101016}
.btn:hover{border-color:#3a3a4a;transform:translateY(-1px)}
.btn b{display:block;font-size:1rem}
.btn span{color:var(--muted);font-size:.84375rem}
h2{font-size:.75rem;letter-spacing:2px;text-transform:uppercase;color:var(--muted);
margin:2.625rem 0 .875rem;font-weight:800}
.list{list-style:none;margin:0;padding:0}
.item{display:flex;gap:.875rem;padding:.9375rem 0;border-bottom:1px solid var(--line);
text-decoration:none;color:#fff}
.item:hover .t{text-decoration:underline}
.day{flex:0 0 74px;font-size:.65625rem;font-weight:800;letter-spacing:1.4px;padding-top:3px}
.t{font-size:.96875rem;line-height:1.42}
.src{color:var(--muted);font-size:.8125rem;margin-top:4px}
.badge{display:inline-block;font-size:.625rem;font-weight:800;letter-spacing:1px;
border:1px solid #FBBF24;color:#FBBF24;border-radius:5px;padding:1px 6px;margin-left:6px}
.method{border:1px solid var(--line);border-radius:14px;padding:1.375rem;margin-top:2.75rem;
background:#101016}
.method h2,.method h3{margin:0 0 .75rem;font-size:.9375rem;letter-spacing:0;
text-transform:none;color:var(--fg)}
.method li{color:#c9c9d4;font-size:.90625rem;margin-bottom:9px}
footer{color:var(--muted);font-size:.78125rem;margin-top:2.75rem;text-align:center}
footer a{color:var(--muted)}
.legal{margin:0 0 .75rem;padding:0;list-style:none}
.legal li{display:inline;white-space:nowrap}
.legal li+li::before{content:" · ";white-space:pre}
.prose{color:#c9c9d4;font-size:.9375rem}
.prose h2{font-size:.9375rem;letter-spacing:0;text-transform:none;color:var(--fg);
margin:2rem 0 .5rem}
.prose a{color:#fff}
.note{color:var(--muted);font-size:.78125rem;margin-top:1.5rem}

/* --- WCAG 2.4.7 Focus Visible / 2.4.11 Focus Appearance ------------------
   There was no focus style at all, so a keyboard user's position on the page
   was whatever the browser drew by default - which on a #0B0B0F background is
   frequently a thin dark ring nobody can see. Every interactive element on
   this page is an <a>, so one rule covers all of them. :focus-visible rather
   than :focus so a mouse click does not leave a ring behind. */
a:focus-visible{outline:3px solid var(--focus);outline-offset:3px;border-radius:4px}

/* Skip link: off-screen until focused, then pinned top-left. */
.skip{position:absolute;left:-9999px;top:0;background:var(--focus);color:#0B0B0F;
padding:.75rem 1rem;font-weight:800;text-decoration:none;border-radius:0 0 8px 0;z-index:10}
.skip:focus{left:0}

/* 1.4.11-friendly: bump the decorative borders when the reader has asked the
   OS for more contrast. */
@media (prefers-contrast:more){
  :root{--line:#6a6a80;--muted:#b9b9c8}
}

/* 2.3.3: the only motion here is the button lift, but honour the setting. */
@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important;scroll-behavior:auto!important}
  .btn:hover{transform:none}
}
"""

# Shown in the method box AND in the footer of every page. Defined here, above
# both, because METHOD is an f-string evaluated at import time.
DISCLAIMER_LINE = ("Summaries of published research, for general information "
                   "only. Not medical advice.")


def _window_phrase() -> str:
    """How the publication window is described on the public page.

    Read from config/niches.yaml rather than written out, because this is a
    PUBLIC CLAIM ABOUT METHODOLOGY on the page the Instagram bio points at.
    It said "the last 14 days" as a hardcoded string, so the moment the
    window was widened the site was stating something untrue about how the
    account works - the one kind of drift this account cannot afford, given
    that the vetting rules are its whole differentiator.
    """
    try:
        import yaml
        from config import ROOT
        days = int(yaml.safe_load(
            (ROOT / "config" / "niches.yaml").read_text())["defaults"]["recency_days"])
    except Exception:
        return "recently"
    if days % 30 == 0:
        return f"the last {days // 30} months"
    if days >= 60:
        return f"roughly the last {round(days / 30)} months"
    return f"the last {days} days"


METHOD = f"""
<section class="method" id="method" aria-labelledby="method-h">
  <h2 id="method-h">How a study gets on this page</h2>
  <ul>
    <li>It has to have been published in {_window_phrase()}.</li>
    <li>It gets checked against retraction records before anything is written.</li>
    <li>Predatory publishers are auto-rejected from a blocklist.</li>
    <li>If it is a preprint, the post says so on the cover slide. Always.</li>
    <li>If the design is observational, the copy is not allowed to say "causes".</li>
    <li>If it was done in mice, the post says mice.</li>
    <li>Sample size, funding conflicts and relative-vs-absolute risk are checked
        automatically and turned into the fine-print slide.</li>
    <li>A human reads every post before it goes live.</li>
  </ul>
  <p class="note">{DISCLAIMER_LINE} <a href="disclaimer.html">How we handle
  corrections and retractions</a>.</p>
</section>
"""


# ---------------------------------------------------------------------------
# Footer, and the small print pages it links to.
#
# WHY THESE EXIST
# ===============
# Three of them, and they are not the same kind of thing:
#
#   accessibility.html - a dated conformance statement with a WORKING route to
#     report a problem. Under ADA Title III there is still no DOJ regulation
#     binding private sites and no statutory template to copy, so what actually
#     helps is evidence of good faith: a named standard, what was tested, what
#     is known to be imperfect, and a human to tell. It is also the thing that
#     makes the rest of this file honest - a claim on the page that someone can
#     hold us to.
#
#   privacy.html - short, because there is genuinely nothing to disclose. No
#     analytics, no cookies, no fonts or scripts from anyone else's server, no
#     accounts, no forms. Saying that plainly is worth more than a borrowed
#     policy describing trackers this site does not run.
#
#   disclaimer.html - the one that matters most for THIS site specifically.
#     It publishes health studies. "Not medical advice", plus how corrections
#     work, plus what is and is not reproduced from the papers.
#
# Every page carries the footer, so every page carries the disclaimer line and
# a route to all three.
# ---------------------------------------------------------------------------
LEGAL_PAGES = [
    ("accessibility.html", "Accessibility"),
    ("privacy.html", "Privacy"),
    ("disclaimer.html", "Disclaimer & corrections"),
]

def _footer_html(built: date, here: str = "") -> str:
    """The shared footer. `here` is the current page's filename, which is
    rendered as plain text rather than a link to itself (WCAG-adjacent common
    sense: a link that goes nowhere new is noise on a screen reader)."""
    items = []
    for href, label in [("./", "All studies")] + LEGAL_PAGES:
        if href == here:
            items.append(f'<li><span aria-current="page">{label}</span></li>')
        else:
            items.append(f'<li><a href="{href}">{label}</a></li>')
    return (
        '<footer>\n'
        f'<ul class="legal">{"".join(items)}</ul>\n'
        f'<p>{DISCLAIMER_LINE}</p>\n'
        '<p>Updated automatically when a post publishes · '
        f'<time datetime="{built.isoformat()}">{built.strftime("%d %b %Y")}</time></p>\n'
        '</footer>'
    )


def _page(title: str, desc: str, body: str, base: str, here: str,
          built: Optional[date] = None) -> str:
    """A small-print page, sharing the main page's shell.

    Same <html lang>, same skip link, same landmarks, same focus styles and
    same footer as index.html - because an accessibility statement served on an
    inaccessible page is the sort of thing that gets quoted back at you.
    """
    built = built or datetime.utcnow().date()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{html.escape(base + here)}">
<meta name="theme-color" content="#0B0B0F">
<style>{CSS}</style></head><body>
<a class="skip" href="#main">Skip to main content</a>
<div class="wrap">
<header><p class="tag"><a href="./">← {html.escape(handle_setting())}</a></p></header>
<main id="main">
<h1 class="brand">{html.escape(title)}</h1>
<div class="prose">
{body}
</div>
</main>
{_footer_html(built, here)}
</div>
</body></html>"""


def accessibility_html(base: str, built: Optional[date] = None) -> str:
    contact = contact_url()
    body = f"""
<p>We want every reader to be able to use this page, including people using a
screen reader, keyboard-only navigation, browser zoom or a raised default font
size.</p>

<h2>How accessible this page is</h2>
<p>This site aims to conform to <a href="https://www.w3.org/TR/WCAG21/"
rel="noopener noreferrer" target="_blank">WCAG 2.1 level AA</a> (opens in a new
tab), the standard US courts and regulators reference. We believe the site
currently meets it.</p>

<h2>What that means here in practice</h2>
<ul>
<li>Every text and background colour pair on the site is at or above the 4.5:1
    contrast ratio AA requires; the lowest is 4.96:1.</li>
<li>Everything is reachable and operable with a keyboard alone, and the
    element you are on is always shown with a visible amber focus ring.</li>
<li>A "skip to main content" link is the first thing a keyboard or screen
    reader user reaches.</li>
<li>The page uses real landmarks and headings, and the study list is marked up
    as a list, so a screen reader can announce and jump through it.</li>
<li>Links that open a new tab say so in their accessible name.</li>
<li>Text is sized in relative units, so it follows your browser's font size
    setting as well as zoom.</li>
<li>Motion is disabled if your system asks for reduced motion, and borders
    strengthen if it asks for higher contrast.</li>
<li>Nothing on the site depends on colour alone to convey meaning.</li>
</ul>

<h2>Known limitations</h2>
<p>Every study links out to a journal or repository we do not control. Once you
follow one of those links you are on someone else's site, and we cannot make
any promise about how accessible it is.</p>
<p>Study titles and journal names are reproduced exactly as the publisher
recorded them. If a publisher's own title contains an unexplained abbreviation
or a formatting oddity, it will appear here that way, because silently
rewriting a paper's title would be worse.</p>

<h2>How this was checked</h2>
<p>Contrast ratios are computed from the site's own palette rather than
eyeballed. The structural requirements above - the skip link, the landmarks,
the list semantics, the focus styles, the new-tab warnings - are each pinned by
an automated test that runs on every change to the site, so they cannot be
removed by accident later.</p>
<p>This site does <strong>not</strong> use an accessibility overlay or
plug-in widget. Those are widely regarded by disabled users and accessibility
practitioners as unhelpful, and they have themselves attracted litigation.</p>

<h2>Tell us if something is wrong</h2>
<p>If any part of this site is difficult or impossible for you to use, please
tell us and we will fix it. This is a one-person project, so the honest promise
is: we will reply within a week and tell you what we are doing about it.</p>
<p><a href="{html.escape(contact)}">{html.escape(contact_label())}</a></p>
"""
    return _page("Accessibility", "Our accessibility statement, what this site "
                 "conforms to, known limitations, and how to report a problem.",
                 body, base, "accessibility.html", built)


def privacy_html(base: str, built: Optional[date] = None) -> str:
    contact = contact_url()
    body = f"""
<p>The short version: this site collects nothing about you.</p>

<h2>What we do not do</h2>
<ul>
<li>No analytics of any kind. No Google Analytics, no pixel, no counter.</li>
<li>No cookies. The site sets none, for any purpose.</li>
<li>No accounts, no logins, no forms, no newsletter sign-up.</li>
<li>No advertising and no ad networks.</li>
<li>No third-party fonts, scripts or embeds. Everything the page needs is in
    the page itself, so loading it does not tell anyone else you were here.</li>
</ul>

<h2>What unavoidably happens anyway</h2>
<p>This site is hosted on GitHub Pages. Like any web host, GitHub receives the
requests your browser makes in order to send the page back, which includes your
IP address, and it may keep its own server logs. That is between you and
GitHub; we do not receive those logs, and we have no way to identify you.</p>
<p>If you follow a link to a study, you are then on the publisher's site, under
their privacy policy, not ours.</p>

<h2>Your rights</h2>
<p>Rules such as the GDPR and CCPA give you rights to see, correct or delete
personal data a site holds about you. We hold none, so there is nothing for us
to show you or delete. If you email us, we will of course have that email.</p>

<p><a href="{html.escape(contact)}">{html.escape(contact_label())}</a></p>
"""
    return _page("Privacy", "This site sets no cookies, runs no analytics and "
                 "collects no personal data.",
                 body, base, "privacy.html", built)


def disclaimer_html(base: str, built: Optional[date] = None) -> str:
    contact = contact_url()
    body = f"""
<h2>Not medical advice</h2>
<p>This account summarises newly published scientific research, including
health and medical research. It is general information about what a study
reported. It is <strong>not</strong> medical advice, diagnosis or treatment,
and it is not a substitute for a conversation with a qualified clinician who
knows your situation.</p>
<p>Do not start, stop or change any treatment on the basis of a post here.
A single study is one piece of evidence, usually early, often in a small group
of people, and sometimes not in people at all.</p>

<h2>What we claim, and what we do not</h2>
<p>Every post links to the original paper so you can check it yourself. We
summarise; we do not peer-review. A study appearing here is not an endorsement
of its conclusions, and covering a paper does not mean we think it is
correct - only that it was published, passed our vetting checks, and is worth
knowing about.</p>
<p>Where a study is observational, our posts are not permitted to describe it
as showing cause. Where a study is a preprint, the post says so. Where a study
was done in animals, the post says so.</p>

<h2>Corrections</h2>
<p>We will get things wrong. When we do, we want to know quickly and fix it
publicly rather than quietly.</p>
<p>If you spot an error - a misread number, a mischaracterised design, a study
that has since been retracted - tell us and we will correct or delete the post
and say that we did. Retraction status is checked automatically before
anything is written, but that check runs at the time of writing, and papers are
retracted after publication.</p>
<p><a href="{html.escape(contact)}">{html.escape(contact_label())}</a></p>

<h2>Sources and copyright</h2>
<p>Posts are our own plain-language summaries, written from publicly available
metadata and abstracts. We do not reproduce full papers, paywalled text, or
publishers' figures and images. Study titles, journal names, authors and
publication dates are reproduced as factual citation, and every post links to
the publisher's own page for the paper.</p>
<p>If you are a rights holder and believe something here goes beyond that,
contact us and we will take it down while we look at it.</p>
"""
    return _page("Disclaimer &amp; corrections",
                 "Not medical advice: how to read these summaries, how we "
                 "handle corrections and retractions, and how we cite sources.",
                 body, base, "disclaimer.html", built)


def write_legal_pages(base: Optional[str] = None,
                      built: Optional[date] = None) -> List[str]:
    """Write the three small-print pages. Returns their paths."""
    base = base or site_url()
    out = []
    for name, fn in (("accessibility.html", accessibility_html),
                     ("privacy.html", privacy_html),
                     ("disclaimer.html", disclaimer_html)):
        p = DOCS / name
        p.write_text(fn(base, built))
        out.append(str(p))
    return out


# ---------------------------------------------------------------------------
# Structured data
#
# The page is a CollectionPage whose mainEntity is an ItemList of
# ScholarlyArticle entries - the vocabulary Google documents for a page that
# lists scholarly work, and the reason this page can show up as more than a
# blue link. Property names below are all real schema.org properties; nothing
# is invented, because an unrecognised property is silently dropped and an
# invented @type invalidates the whole node.
# ---------------------------------------------------------------------------
def _json_ld_script(data: Dict[str, Any]) -> str:
    """Serialise `data` into a <script type="application/ld+json"> block.

    Study titles and journal names are untrusted third-party text, so they are
    never concatenated in: json.dumps does the quoting, and then "<", ">" and
    "&" are re-encoded as \\uXXXX escapes.

    That second step is the one that matters. JSON quoting alone will happily
    emit a literal `</script>` out of a study title - the HTML tokenizer does
    not care that it is inside a JSON string, it ends the script element right
    there and treats the rest of the title as live markup. `\\u003c` is still
    valid JSON (json.loads gives the original title back verbatim), but the
    tokenizer never sees a `<` at all. ensure_ascii=True additionally escapes
    U+2028/U+2029, which are line terminators to a JS parser.
    """
    blob = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    blob = (blob.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026"))
    return f'<script type="application/ld+json">{blob}</script>'


def _article_node(post: Dict[str, Any]) -> Dict[str, Any]:
    """One ScholarlyArticle node from a published post."""
    st = post.get("study") or {}
    title = str(st.get("title") or "").strip()
    journal = str(st.get("journal") or st.get("server") or "").strip()
    doi = str(st.get("doi") or "").strip()
    url = _safe_url(f"https://doi.org/{doi}" if doi else st.get("url"))

    node: Dict[str, Any] = {"@type": "ScholarlyArticle"}
    if title:
        # name and headline are both valid on CreativeWork and consumers
        # differ on which they read, so carry the same string in both.
        node["name"] = title
        node["headline"] = title
    if url:
        node["url"] = url
    when = _iso_date(st.get("pub_date"))
    if when:
        node["datePublished"] = when
    if journal:
        node["isPartOf"] = {"@type": "Periodical", "name": journal}
        node["publisher"] = {"@type": "Organization", "name": journal}
    if doi:
        node["identifier"] = {"@type": "PropertyValue",
                              "propertyID": "DOI", "value": doi}
    if st.get("is_preprint"):
        # creativeWorkStatus is the schema.org-blessed way to say this; there
        # is no Preprint type in the core vocabulary. The page badges it too,
        # so the structured data and the visible content agree - which is a
        # requirement, not a nicety.
        node["creativeWorkStatus"] = "Preprint"
    authors = [str(a).strip() for a in (st.get("authors") or []) if str(a).strip()]
    if authors:
        node["author"] = [{"@type": "Person", "name": a} for a in authors[:10]]
    return node


def build_jsonld(posts: List[Dict[str, Any]], handle: str, base: str,
                 description: str) -> Dict[str, Any]:
    """The CollectionPage graph for `posts` (the ones actually rendered).

    Only the posts the page shows go in. Structured data that describes items
    a visitor cannot see on the page is a spam signal, so this takes the same
    list the HTML sections were built from rather than everything on disk.
    """
    items = []
    for i, p in enumerate(posts, start=1):
        node = _article_node(p)
        if len(node) > 1:            # something beyond the bare @type
            items.append({"@type": "ListItem", "position": i, "item": node})
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": f"{base}#page",
        "url": base,
        "name": f"{handle} · every study we cover",
        "description": description,
        "inLanguage": "en",
        "isPartOf": {"@type": "WebSite", "@id": f"{base}#website",
                     "url": base, "name": handle},
        "mainEntity": {
            "@type": "ItemList",
            "name": f"Studies covered on {handle}",
            "itemListOrder": "https://schema.org/ItemListOrderDescending",
            "numberOfItems": len(items),
            # Always a list, never None and never omitted: with zero published
            # posts this is [], which is valid JSON and a valid (if empty)
            # ItemList, and the page still parses.
            "itemListElement": items,
        },
    }


# ---------------------------------------------------------------------------
# sitemap.xml / robots.txt
# ---------------------------------------------------------------------------
def _newest_pub_date(posts: List[Dict[str, Any]]) -> str:
    dates = [_iso_date((p.get("study") or {}).get("pub_date")) for p in posts]
    dates = [d for d in dates if d]
    return max(dates) if dates else ""


def sitemap_xml(posts: List[Dict[str, Any]], base: Optional[str] = None) -> str:
    """The sitemap document as a string.

    One URL: the site root. Every study on this page links OUT to a publisher
    we do not own, and listing third-party URLs in our sitemap is both wrong
    and ignored. lastmod is the newest published study's date, so the file
    only claims a change when there actually was one.
    """
    base = base or site_url()
    lastmod = _newest_pub_date(posts) or datetime.utcnow().date().isoformat()
    urls = [
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{_xml_escape(base)}</loc>\n"
        f"    <lastmod>{_xml_escape(lastmod)}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
    ]
    # The small-print pages ARE ours, unlike every study link on the page, so
    # they belong here. Low priority and rarely changing: they should be
    # findable and citable, not competing with the study list for crawl budget.
    for name, _label in LEGAL_PAGES:
        urls.append(
            "  <url>\n"
            f"    <loc>{_xml_escape(base + name)}</loc>\n"
            "    <changefreq>yearly</changefreq>\n"
            "    <priority>0.3</priority>\n"
            "  </url>\n"
        )
    urls.append("</urlset>\n")
    return "".join(urls)


def write_sitemap(posts: Optional[List[Dict[str, Any]]] = None,
                  base: Optional[str] = None) -> str:
    """Write docs/sitemap.xml. Returns the path."""
    if posts is None:
        posts = load_posts()
    out = DOCS / "sitemap.xml"
    out.write_text(sitemap_xml(posts, base))
    return str(out)


def robots_txt(base: Optional[str] = None) -> str:
    base = base or site_url()
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {base}sitemap.xml\n"
    )


def write_robots(base: Optional[str] = None) -> str:
    """Write docs/robots.txt. Returns the path."""
    out = DOCS / "robots.txt"
    out.write_text(robots_txt(base))
    return str(out)


# ---------------------------------------------------------------------------
def load_posts() -> List[Dict[str, Any]]:
    """Every published post, newest first. Unreadable files are skipped."""
    posts: List[Dict[str, Any]] = []
    for f in PUBLISHED.glob("*.json"):
        try:
            posts.append(json.loads(f.read_text()))
        except Exception:
            continue
    # `or ""`, not a .get default. A key that is PRESENT with a null value
    # returns None, and sorting None against a str raises TypeError - taking
    # the whole page, sitemap and robots.txt down over one bad record.
    posts.sort(key=lambda p: ((p.get("study") or {}).get("pub_date") or ""),
               reverse=True)
    return posts


def _og_image(base: str, posts: List[Dict[str, Any]]) -> str:
    """Absolute URL of the share image: the newest post's cover slide.

    Those JPEGs are already staged into docs/img/<post-id>/ for Instagram to
    download, so this costs nothing extra. Before the first post exists there
    is nothing to point at, and the fallback names a file you can drop in by
    hand (docs/img/og-cover.jpg) - a link preview with a missing image is no
    worse than one with no image tag, and the tag has to be there for the
    first share to work the moment the file appears.
    """
    for p in posts:
        pid = str(p.get("id") or "").strip()
        if not pid or "/" in pid or "\\" in pid:
            continue
        try:
            covers = sorted((DOCS / "img" / pid).glob("*_cover.jpg"))
        except OSError:
            covers = []
        if covers:
            return f"{base}img/{quote(pid)}/{quote(covers[0].name)}"
    return f"{base}img/og-cover.jpg"


def build() -> str:
    posts = load_posts()

    weeks: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for p in posts:
        # _iso_date exists precisely because a database date field cannot be
        # trusted - but _week_key re-did the same strptime WITHOUT that guard,
        # so a single published post with an empty or non-ISO pub_date raised
        # ValueError here and permanently broke regeneration of index.html,
        # sitemap.xml and robots.txt. That matters beyond the page: the publish
        # workflow rebuilds the bio page, and "the link-in-bio rebuild fails" is
        # one of the three events already documented as able to discard a
        # publish record. A bad date on one post must cost that post its row,
        # not take the site and the publish path down with it.
        iso = _iso_date((p.get("study") or {}).get("pub_date"))
        if not iso:
            continue
        weeks.setdefault(_week_key(iso), []).append(p)

    buttons = "".join(
        f'<a class="btn" href="{html.escape(_safe_url(b["url"], ("http", "https", "mailto", "#")) or "#")}"'
        f' aria-label="{html.escape(b["label"])}'
        f'{(" - " + html.escape(b["note"])) if b.get("note") else ""}">'
        f'<b>{html.escape(b["label"])}</b>'
        f'<span>{html.escape(b.get("note", ""))}</span></a>'
        for b in profile_links())

    sections = []
    shown: List[Dict[str, Any]] = []      # exactly what the page renders
    for wk, items in list(weeks.items())[:8]:
        rows = []
        for p in items:
            st = p["study"]
            shown.append(p)
            n = NICHES.get(p.get("niche", "wildcard"), NICHES["wildcard"])
            pre = '<span class="badge">PREPRINT</span>' if st.get("is_preprint") else ""
            title = html.escape(st["title"])
            journal = html.escape(st["journal"])
            when = _iso_date(st.get("pub_date"))
            shown_date = html.escape(st["pub_date_display"])
            date_html = (f'<time datetime="{html.escape(when)}">{shown_date}</time>'
                         if when else shown_date)
            rows.append(
                # <li> around each <article>: the rows are a LIST of studies,
                # and marking them as one is what lets a screen reader announce
                # "list, 5 items" and jump between them instead of walking the
                # page word by word. list-style is stripped in CSS, so nothing
                # changes visually.
                f'<li><article>'
                f'<a class="item" href="{html.escape(_safe_url(st.get("url")) or "#")}" '
                f'target="_blank" rel="noopener noreferrer" '
                f'aria-label="Open the original study: {title} - '
                # The new-tab warning belongs IN the accessible name. A sighted
                # mouse user discovers target="_blank" by watching it happen; a
                # screen reader user gets no such cue, and an unannounced
                # context switch is disorienting (WCAG G201).
                f'{journal}, {shown_date} (opens in a new tab)">'
                f'<div class="day" style="color:{n["accent"]}">{n["short"]}</div>'
                f'<div><div class="t">{title}{pre}</div>'
                f'<div class="src">{journal} · {date_html}</div></div></a>'
                f'</article></li>')
        sections.append(
            f'<section><h2>{_week_label(wk)}</h2>'
            f'<ul class="list">{"".join(rows)}</ul></section>')

    if not sections:
        sections = ['<section><h2>This week</h2>'
                    '<p class="src">First posts land soon.</p></section>']

    handle = handle_setting()
    base = site_url()
    title = f"{handle} · every study we cover"
    desc = (f"Every peer-reviewed study covered on {handle} - one a weekday, "
            f"each with a direct link to the original paper, its journal and "
            f"its publication date. Vetted for retractions and preprints.")
    og_image = _og_image(base, posts)
    built = datetime.utcnow().date()

    head_seo = "\n".join([
        f'<link rel="canonical" href="{html.escape(base)}">',
        '<meta name="robots" content="index,follow,max-image-preview:large,'
        'max-snippet:-1">',
        '<meta name="theme-color" content="#0B0B0F">',
        f'<meta property="og:title" content="{html.escape(title)}">',
        f'<meta property="og:description" content="{html.escape(desc)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{html.escape(base)}">',
        f'<meta property="og:image" content="{html.escape(og_image)}">',
        f'<meta property="og:site_name" content="{html.escape(handle)}">',
        '<meta property="og:locale" content="en_US">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{html.escape(title)}">',
        f'<meta name="twitter:description" content="{html.escape(desc)}">',
        f'<meta name="twitter:image" content="{html.escape(og_image)}">',
        f'<meta name="twitter:image:alt" content="Cover slide from {html.escape(handle)}">',
        _json_ld_script(build_jsonld(shown, handle, base, desc)),
    ])

    # Landmark structure. The footer used to sit INSIDE <main>, which puts the
    # site-wide small print inside the page's main landmark and denies it the
    # contentinfo role a screen reader uses to find it. header / main /
    # footer as siblings inside the layout div is the correct shape, and it is
    # what the small-print pages use too.
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
{head_seo}
<style>{CSS}</style></head><body>
<a class="skip" href="#main">Skip to main content</a>
<div class="wrap">
<header>
<h1 class="brand">{html.escape(handle)}</h1>
<p class="tag">One real study, every weekday. Here is every paper we have covered,
with a direct link to the original. No paywalled summaries, no telephone game.</p>
{buttons}
</header>
<main id="main">
{"".join(sections)}
{METHOD}
</main>
{_footer_html(built)}
</div>
</body></html>"""

    out = DOCS / "index.html"
    out.write_text(doc)
    # Written alongside the page, from the same post list, so the files can
    # never disagree about when the site last changed.
    write_sitemap(posts, base)
    write_robots(base)
    write_legal_pages(base, built)
    return str(out)


if __name__ == "__main__":
    print(build())
