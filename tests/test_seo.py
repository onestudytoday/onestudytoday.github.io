"""
SEO / structured-data tests for the link-in-bio page.

The page at docs/index.html is the only channel this account has that keeps
working on old posts, so search indexing is the point of the markup these
tests cover. It is also the one page that renders UNTRUSTED third-party text -
study titles and journal names come straight out of public research databases -
into HTML *and* into a <script> block, on a URL the account's own Instagram bio
points at. The escaping tests below are written so that they fail loudly if the
escaping is ever removed, not just so they pass today.

    python -m pytest tests/test_seo.py -q
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import linkinbio  # noqa: E402

SAMPLES = sorted((ROOT / "samples" / "posts").glob("*.json"))

SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# A title that tries every escape hatch at once: it closes the JSON-LD script
# element, opens a tag with an event handler, and carries quotes and an
# ampersand for good measure.
HOSTILE_TITLE = 'Sleep </script><img src=x onerror="alert(1)"> & "more" <b>x</b>'
HOSTILE_JOURNAL = 'Journal of </SCRIPT> Bad Ideas'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def site(tmp_path, monkeypatch):
    """Build the page into a temp dir. Returns a helper with the outputs."""
    pub = tmp_path / "published"
    docs = tmp_path / "docs"
    pub.mkdir()
    docs.mkdir()
    monkeypatch.setattr(linkinbio, "PUBLISHED", pub)
    monkeypatch.setattr(linkinbio, "DOCS", docs)
    monkeypatch.setenv("HANDLE", "@onestudytoday")
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_IMAGE_BASE", raising=False)

    class Site:
        published = pub
        docs_dir = docs

        def add(self, post, name=None):
            (pub / (name or f"{post['id']}.json")).write_text(json.dumps(post))

        def add_samples(self):
            for f in SAMPLES:
                (pub / f.name).write_text(f.read_text())

        def build(self):
            self.html = Path(linkinbio.build()).read_text()
            self.sitemap = (docs / "sitemap.xml").read_text()
            self.robots = (docs / "robots.txt").read_text()
            return self

        @property
        def jsonld(self):
            return json.loads(extract_jsonld(self.html))

    return Site()


def extract_jsonld(doc: str) -> str:
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', doc, re.S)
    assert m, "no JSON-LD block in the page"
    return m.group(1)


def hostile_post(post_id="hostile", url="https://example.org/study"):
    return {
        "id": post_id,
        "niche": "psych",
        "study": {
            "title": HOSTILE_TITLE,
            "journal": HOSTILE_JOURNAL,
            "pub_date": "2026-08-12",
            "pub_date_display": "Aug 12, 2026",
            "doi": "",
            "url": url,
            "is_preprint": False,
        },
    }


# ---------------------------------------------------------------------------
# 1. The JSON-LD is valid, and says what it should say
# ---------------------------------------------------------------------------
def test_jsonld_parses_and_models_a_collectionpage_of_scholarly_articles(site):
    site.add_samples()
    site.build()
    data = site.jsonld

    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "CollectionPage"
    assert data["url"].startswith("https://")

    lst = data["mainEntity"]
    assert lst["@type"] == "ItemList"
    assert lst["numberOfItems"] == len(lst["itemListElement"]) == len(SAMPLES)

    positions = [e["position"] for e in lst["itemListElement"]]
    assert positions == list(range(1, len(SAMPLES) + 1))

    for entry in lst["itemListElement"]:
        assert entry["@type"] == "ListItem"
        art = entry["item"]
        assert art["@type"] == "ScholarlyArticle"
        assert art["name"] and art["headline"] == art["name"]
        assert art["url"].startswith("http")
        # yyyy-mm-dd, i.e. actually machine readable
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", art["datePublished"])
        assert art["isPartOf"]["@type"] == "Periodical"
        assert art["isPartOf"]["name"]
        assert art["publisher"]["@type"] == "Organization"


def test_jsonld_titles_match_the_source_posts_exactly(site):
    site.add_samples()
    site.build()
    from_page = {e["item"]["name"] for e in site.jsonld["mainEntity"]["itemListElement"]}
    from_disk = {json.loads(f.read_text())["study"]["title"] for f in SAMPLES}
    assert from_page == from_disk


def test_preprints_are_marked_and_not_invented_as_a_type(site):
    p = hostile_post("preprint-post")
    p["study"]["title"] = "A perfectly ordinary preprint"
    p["study"]["journal"] = "bioRxiv"
    p["study"]["is_preprint"] = True
    site.add(p)
    site.build()
    art = site.jsonld["mainEntity"]["itemListElement"][0]["item"]
    assert art["@type"] == "ScholarlyArticle"          # no invented Preprint type
    assert art["creativeWorkStatus"] == "Preprint"


# ---------------------------------------------------------------------------
# 2. A </script> in a study title cannot break out of the JSON-LD block
# ---------------------------------------------------------------------------
def test_script_close_in_a_title_cannot_break_out_of_the_jsonld_block(site):
    site.add(hostile_post())
    site.build()
    doc = site.html

    # There is exactly one script element on the page. If the title's
    # "</script>" survived into the output as literal markup there would be
    # two closing tags and the rest of the title would be live HTML.
    assert doc.count("<script") == 1
    assert doc.count("</script>") == 1
    assert doc.count("</SCRIPT>") == 0

    # The block still parses, and round-trips the ORIGINAL title byte for
    # byte - the escaping must be a JSON-level \u escape, not a mangling.
    art = site.jsonld["mainEntity"]["itemListElement"][0]["item"]
    assert art["name"] == HOSTILE_TITLE
    assert art["isPartOf"]["name"] == HOSTILE_JOURNAL

    # Nothing anywhere in the page renders the payload as markup: the script
    # block contains no raw angle brackets at all, and the body only ever has
    # the entity-escaped form.
    assert "<img src=x" not in doc
    inner = extract_jsonld(doc)
    assert "<" not in inner and ">" not in inner
    body = doc.split("</script>", 1)[1]
    assert 'onerror="alert(1)"' not in body
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in body


def test_json_ld_script_helper_escapes_angle_brackets_and_ampersands():
    # Unit-level guard on the helper itself, so this fails even if build()
    # stops feeding it hostile data for some other reason.
    payload = {"name": HOSTILE_TITLE, "note": "a & b"}
    block = linkinbio._json_ld_script(payload)
    inner = block[len('<script type="application/ld+json">'):-len("</script>")]
    assert "<" not in inner and ">" not in inner and "&" not in inner
    assert json.loads(inner) == payload


# ---------------------------------------------------------------------------
# 3. Every interpolated value is HTML-escaped
# ---------------------------------------------------------------------------
def test_titles_and_journals_are_html_escaped_in_the_body(site):
    site.add(hostile_post())
    site.build()
    doc = site.html
    body = doc.split("</script>", 1)[1]      # everything after the JSON-LD

    # Raw, unescaped forms must not appear in the rendered body at all.
    assert HOSTILE_TITLE not in body
    assert HOSTILE_JOURNAL not in body
    assert "<b>x</b>" not in body

    # The escaped forms must - i.e. the text is still displayed, just inert.
    assert "&lt;/script&gt;&lt;img src=x" in body
    assert "&amp;" in body
    assert "Journal of &lt;/SCRIPT&gt; Bad Ideas" in body


def test_aria_labels_are_escaped_too(site):
    site.add(hostile_post())
    site.build()
    m = re.search(r'aria-label="([^"]*)"[^>]*>\s*<div class="day"', site.html)
    assert m, "study anchors should carry a descriptive aria-label"
    label = m.group(1)
    assert "&lt;" in label and "&quot;" in label
    assert '"' not in label.replace("&quot;", "")   # no attribute breakout


def test_a_javascript_url_never_reaches_an_href(site):
    site.add(hostile_post(url="javascript:alert(document.domain)"))
    site.build()
    assert "javascript:" not in site.html
    assert 'class="item" href="#"' in site.html


# ---------------------------------------------------------------------------
# 4. sitemap.xml / robots.txt
# ---------------------------------------------------------------------------
def test_sitemap_is_well_formed_xml_with_the_newest_post_as_lastmod(site):
    site.add_samples()
    site.build()

    root = ET.fromstring(site.sitemap)            # raises if malformed
    assert root.tag == f"{SM_NS}urlset"
    urls = root.findall(f"{SM_NS}url")

    # The site root, first, carrying the newest study's date as lastmod.
    loc = urls[0].findtext(f"{SM_NS}loc")
    assert loc == linkinbio.site_url()
    assert loc.startswith("https://") and loc.endswith("/")

    newest = max(json.loads(f.read_text())["study"]["pub_date"] for f in SAMPLES)
    assert urls[0].findtext(f"{SM_NS}lastmod") == newest

    # Plus the small-print pages, which are ours. Still NOT the study links -
    # those point at publishers we do not own, and listing someone else's URL
    # in our sitemap is both wrong and ignored.
    listed = {u.findtext(f"{SM_NS}loc") for u in urls}
    for name, _label in linkinbio.LEGAL_PAGES:
        assert linkinbio.site_url() + name in listed, name
    assert len(urls) == 1 + len(linkinbio.LEGAL_PAGES)
    for u in urls:
        assert u.findtext(f"{SM_NS}loc").startswith(linkinbio.site_url())


def test_robots_allows_everything_and_points_at_the_sitemap(site):
    site.add_samples()
    site.build()
    base = linkinbio.site_url()
    assert "User-agent: *" in site.robots
    assert "Allow: /" in site.robots
    assert "Disallow: /" not in site.robots
    assert f"Sitemap: {base}sitemap.xml" in site.robots


def test_sitemap_and_robots_are_written_by_running_build(site):
    site.add_samples()
    site.build()
    assert (site.docs_dir / "sitemap.xml").exists()
    assert (site.docs_dir / "robots.txt").exists()
    assert (site.docs_dir / "index.html").exists()


# ---------------------------------------------------------------------------
# 5. Zero published posts
# ---------------------------------------------------------------------------
def test_zero_posts_still_produces_valid_json_ld_and_a_valid_sitemap(site):
    site.build()                                  # nothing added

    data = site.jsonld                            # raises if not valid JSON
    lst = data["mainEntity"]
    assert lst["@type"] == "ItemList"
    assert lst["itemListElement"] == []
    assert lst["numberOfItems"] == 0

    root = ET.fromstring(site.sitemap)
    lastmod = root.find(f"{SM_NS}url").findtext(f"{SM_NS}lastmod")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod)

    # And the human-facing empty state is untouched.
    assert "First posts land soon." in site.html
    assert site.html.count("<h1") == 1


def test_a_post_with_a_broken_date_does_not_produce_a_bogus_datepublished(site):
    p = hostile_post("bad-date")
    p["study"]["title"] = "Fine title"
    p["study"]["journal"] = "Nature"
    p["study"]["pub_date"] = "2026-08-12"          # week grouping needs a real one
    p["study"]["pub_date_display"] = "not a date"
    site.add(p)
    site.build()
    art = site.jsonld["mainEntity"]["itemListElement"][0]["item"]
    assert art["datePublished"] == "2026-08-12"
    assert linkinbio._iso_date("sometime in spring") == ""


# ---------------------------------------------------------------------------
# 6. Head metadata
# ---------------------------------------------------------------------------
def test_head_carries_canonical_og_twitter_robots_and_theme_color(site):
    site.add_samples()
    site.build()
    doc = site.html
    base = linkinbio.site_url()

    assert f'<link rel="canonical" href="{base}">' in doc
    assert '<meta name="robots" content="index,follow' in doc
    assert '<meta name="theme-color" content="#0B0B0F">' in doc
    for prop in ("og:title", "og:description", "og:type", "og:url",
                 "og:image", "og:site_name"):
        assert f'property="{prop}"' in doc, f"missing {prop}"
    assert '<meta name="twitter:card" content="summary_large_image">' in doc
    for name in ("twitter:title", "twitter:description", "twitter:image"):
        assert f'name="{name}"' in doc, f"missing {name}"

    # Exactly one description meta, not the old one plus a new one.
    assert doc.count('<meta name="description"') == 1
    assert doc.count(f'<meta property="og:url" content="{base}">') == 1


def test_semantic_html_landmarks(site):
    site.add_samples()
    site.build()
    doc = site.html
    assert doc.count("<h1") == 1
    # A main landmark, and the skip link's target lands on it. This used to
    # assert the literal string '<main class="wrap">'; the landmark now carries
    # the id instead and the layout div carries the class, so the assertion is
    # written against the STRUCTURE it always meant rather than that one
    # spelling of it.
    assert re.search(r'<main\b[^>]*\bid="main"', doc)
    assert "</main>" in doc
    assert '<a class="skip" href="#main"' in doc
    # Footer is a sibling of main, not inside it: site-wide small print does
    # not belong in the page's main landmark.
    assert doc.index("</main>") < doc.index("<footer>")
    assert doc.count("<article>") == len(SAMPLES)
    assert re.search(r'<time datetime="\d{4}-\d{2}-\d{2}">', doc)
    # The load-bearing CSS classes are all still on the page.
    for cls in ("wrap", "brand", "tag", "btn", "item", "day", "t", "src", "method"):
        assert f'class="{cls}"' in doc or f'class="{cls} ' in doc, cls


# ---------------------------------------------------------------------------
# 7. Base URL derivation
# ---------------------------------------------------------------------------
def test_site_url_is_derived_from_public_image_base_without_settings(monkeypatch):
    # Same contract as config.handle(): rebuilding this page must never need
    # the Meta credentials (see tests/test_copy.py).
    for name in ("META_APP_ID", "META_APP_SECRET", "IG_ACCESS_TOKEN",
                 "IG_BUSINESS_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SITE_URL", raising=False)

    monkeypatch.setenv("PUBLIC_IMAGE_BASE", "https://someone.github.io/sciscroll/img")
    assert linkinbio.site_url() == "https://someone.github.io/sciscroll/"

    monkeypatch.setenv("SITE_URL", "https://explicit.example/")
    assert linkinbio.site_url() == "https://explicit.example/"

    # Junk is discarded rather than emitted as a canonical.
    monkeypatch.setenv("SITE_URL", "not a url")
    monkeypatch.setenv("PUBLIC_IMAGE_BASE", "")
    assert linkinbio.site_url() == linkinbio.DEFAULT_SITE_URL

    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_IMAGE_BASE", raising=False)
    assert linkinbio.site_url() == linkinbio.DEFAULT_SITE_URL


# ---------------------------------------------------------------------------
# 8. Build determinism / no-op churn
#
# The page is rebuilt on every scheduled-publish run. Anything in it that
# varies with WALL-CLOCK rather than with content turns every rebuild into a
# real diff, which gets committed, which triggers CI on push, which inflates a
# history that a fetch-depth:0 clone re-downloads on the next poll.
#
# That is not hypothetical: the footer carried utcnow().date(), so the first
# rebuild after each UTC midnight rewrote the date on index.html and all three
# small-print pages and committed it. 365 commits a year saying nothing
# happened. Caught by reading the actual commit history, not the code.
# ---------------------------------------------------------------------------
def test_rebuilding_across_a_utc_midnight_produces_identical_bytes(site, monkeypatch):
    """Two scheduled-publish runs on either side of UTC midnight, no new post
    in between, must produce byte-identical pages.

    Building twice in the same second proves almost nothing - that version of
    this test passed even with the wall-clock bug in place. The day boundary
    is the thing that actually triggered it, so the day boundary is what gets
    simulated here.
    """
    published = {
        "id": "2026-06-01-nature-deadbeef",
        "niche": "nature",
        "study": {"title": "A study", "journal": "Nature",
                  "pub_date": "2026-06-01", "pub_date_display": "Jun 1, 2026",
                  "doi": "10.1000/x", "url": "https://example.org/x",
                  "is_preprint": False},
        "published": {"media_id": "1", "kind": "CAROUSEL",
                      "at": "2026-06-02T10:00:00Z"},
    }
    site.add(published)

    def _freeze(when):
        class _FakeDatetime(datetime):
            @classmethod
            def utcnow(cls):
                return when
        monkeypatch.setattr(linkinbio, "datetime", _FakeDatetime)

    _freeze(datetime(2026, 6, 10, 23, 59, 0))
    site.build()
    before = {p.name: p.read_bytes()
              for p in sorted(site.docs_dir.glob("*.html"))}
    assert before, "expected some pages to be built"

    _freeze(datetime(2026, 6, 11, 0, 1, 0))      # same repo, next UTC day
    site.build()
    after = {p.name: p.read_bytes()
             for p in sorted(site.docs_dir.glob("*.html"))}

    assert before.keys() == after.keys()
    for name in before:
        assert before[name] == after[name], (
            f"{name} changed across UTC midnight with no new post - "
            f"this is the junk-commit-per-day bug coming back")


def test_the_footer_date_is_the_last_publish_not_todays_date(site):
    """The footer says "Updated automatically when a post publishes". It must
    therefore show when a post last published - not when the page last got
    regenerated, which is a different thing and changes every day."""
    old = {
        "id": "2026-06-01-nature-deadbeef",
        "niche": "nature",
        "study": {"title": "An older study", "journal": "Nature",
                  "pub_date": "2026-06-01", "pub_date_display": "Jun 1, 2026",
                  "doi": "10.1000/old", "url": "https://example.org/old",
                  "is_preprint": False},
        "published": {"media_id": "1", "kind": "CAROUSEL",
                      "at": "2026-06-02T10:00:00Z"},
    }
    site.add(old)
    site.build()

    today = datetime.utcnow().date().isoformat()
    assert linkinbio.last_publish_date([old]).isoformat() == "2026-06-02"
    # The publish date appears in the footer; today's date does not.
    assert 'datetime="2026-06-02"' in site.html
    if today != "2026-06-02":
        assert f'<time datetime="{today}">' not in site.html, \
            "footer is still keyed to wall-clock time"


def test_with_nothing_published_the_footer_falls_back_to_today(site):
    """No publish history to be stale against, so today is the honest answer -
    and an empty date would be worse than a fresh one."""
    site.build()
    today = datetime.utcnow().date()
    assert linkinbio.last_publish_date([]) == today
    assert f'<time datetime="{today.isoformat()}">' in site.html
