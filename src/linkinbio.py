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

PROFILE_LINKS: List[Dict[str, str]] = [
    # Edit these. They render as the big buttons at the top.
    {"label": "Suggest a study", "url": "mailto:you@example.com",
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


CSS = """
:root{--bg:#0B0B0F;--fg:#fff;--muted:#8A8A99;--line:#23232E}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:620px;margin:0 auto;padding:52px 22px 90px}
.brand{font-size:30px;font-weight:800;letter-spacing:-1px;margin:0}
.tag{color:var(--muted);margin:10px 0 34px;font-size:15px}
.btn{display:block;border:1px solid var(--line);border-radius:14px;padding:17px 20px;
margin-bottom:12px;text-decoration:none;color:#fff;transition:.15s;background:#101016}
.btn:hover{border-color:#3a3a4a;transform:translateY(-1px)}
.btn b{display:block;font-size:16px}
.btn span{color:var(--muted);font-size:13.5px}
h2{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);
margin:42px 0 14px;font-weight:800}
.item{display:flex;gap:14px;padding:15px 0;border-bottom:1px solid var(--line);
text-decoration:none;color:#fff}
.item:hover .t{text-decoration:underline}
.day{flex:0 0 74px;font-size:10.5px;font-weight:800;letter-spacing:1.4px;padding-top:3px}
.t{font-size:15.5px;line-height:1.42}
.src{color:var(--muted);font-size:13px;margin-top:4px}
.badge{display:inline-block;font-size:10px;font-weight:800;letter-spacing:1px;
border:1px solid #FBBF24;color:#FBBF24;border-radius:5px;padding:1px 6px;margin-left:6px}
.method{border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:44px;
background:#101016}
.method h3{margin:0 0 12px;font-size:15px}
.method li{color:#c9c9d4;font-size:14.5px;margin-bottom:9px}
footer{color:var(--muted);font-size:12.5px;margin-top:44px;text-align:center}
"""

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
<div class="method" id="method">
  <h3>How a study gets on this page</h3>
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
</div>
"""


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
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{_xml_escape(base)}</loc>\n"
        f"    <lastmod>{_xml_escape(lastmod)}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )


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
        for b in PROFILE_LINKS)

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
                f'<article>'
                f'<a class="item" href="{html.escape(_safe_url(st.get("url")) or "#")}" '
                f'target="_blank" rel="noopener" '
                f'aria-label="Open the original study: {title} - '
                f'{journal}, {shown_date}">'
                f'<div class="day" style="color:{n["accent"]}">{n["short"]}</div>'
                f'<div><div class="t">{title}{pre}</div>'
                f'<div class="src">{journal} · {date_html}</div></div></a>'
                f'</article>')
        sections.append(
            f'<section><h2>{_week_label(wk)}</h2>{"".join(rows)}</section>')

    if not sections:
        sections = ['<section><h2>This week</h2>'
                    '<div class="src">First posts land soon.</div></section>']

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

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
{head_seo}
<style>{CSS}</style></head><body><main class="wrap">
<h1 class="brand">{html.escape(handle)}</h1>
<p class="tag">One real study, every weekday. Here is every paper we have covered,
with a direct link to the original. No paywalled summaries, no telephone game.</p>
{buttons}
{"".join(sections)}
{METHOD}
<footer>Updated automatically when a post publishes ·
<time datetime="{built.isoformat()}">{built.strftime('%d %b %Y')}</time></footer>
</main></body></html>"""

    out = DOCS / "index.html"
    out.write_text(doc)
    # Written alongside the page, from the same post list, so the three files
    # can never disagree about when the site last changed.
    write_sitemap(posts, base)
    write_robots(base)
    return str(out)


if __name__ == "__main__":
    print(build())
