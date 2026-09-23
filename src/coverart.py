"""
A photograph behind the cover headline, chosen from the study's own subject.

WHAT THIS IS FOR
================
The covers are typographically good and visually identical. On a grid, twelve
flat cards read as one flat card. A picture of the thing the study is actually
about - intestines, a petri dish, a telescope - is the cheapest way to make a
post look like a specific post rather than a template, and the cover is the
only slide most people ever see.

THE THREE SOURCES, IN ORDER, AND WHY THAT ORDER
===============================================
    1. Wikimedia Commons   no key. The best source for SCIENTIFIC imagery -
                           anatomical plates, labelled diagrams, real
                           specimens - which is usually what a study is about.
                           Quality and framing vary wildly.
    2. Pexels              needs a free key. Proper stock photography: well
                           lit, well composed, no attribution required. Much
                           better when the subject is an everyday object or a
                           person rather than a diagram.
    3. Openverse           no key. A broad aggregator, used last because its
                           results overlap the first two and its metadata is
                           the least consistent.

Each is tried in turn and the first usable result wins, so a missing Pexels
key costs nothing but one fallback step.

ATTRIBUTION IS NOT OPTIONAL
===========================
Wikimedia and Openverse return CC-BY and CC-BY-SA images, which legally
require credit. That credit has to travel with the published IMAGE, not sit
in a JSON file nobody reads and not only in the caption - captions are
truncated in the feed, and a screenshot of the cover carries no caption at
all. So `credit_line()` produces the text and render.draw_cover_credit()
prints it small, bottom-right, onto the slide itself. Where
the licence cannot be determined, the image is DISCARDED rather than used
uncredited: an unattributed CC-BY image on a public account is a licence
breach, and "we could not tell" is not a defence.

Public domain and CC0 are preferred in the ranking for exactly this reason -
they are the results that need no credit at all.

WHY A HUMAN STILL SEES IT
=========================
Wikimedia Commons contains real surgical photography, cadaver dissections and
clinical wound images, correctly filed under the same anatomical terms a
study about the gut would produce. An automated image search on medical
vocabulary WILL eventually return one. Two things stop that reaching the
grid: `safe_query()` rewrites the queries most likely to surface them, and
the chosen image is shown on the review card, where `revise: different image`
re-picks it. Neither alone is enough; the second is the one that actually
holds.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

UA = ("onestudytoday/1.0 (+https://onestudytoday.github.io; "
      "one peer-reviewed study a day)")
TIMEOUT = 20
MAX_BYTES = 12 * 1024 * 1024        # bounds the wire transfer
MAX_PIXELS = 40_000_000             # ...and this bounds the DECODE
DOWNLOAD_BUDGET_S = 60              # wall clock, not per-read
MIN_PIXELS = 900          # anything smaller looks soft blown up to 1080 wide

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_API = "https://api.pexels.com/v1/search"
OPENVERSE_API = "https://api.openverse.org/v1/images/"

PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "").strip()

# Licences we can actually publish under, and whether they need a credit.
# Anything not listed here is refused - see the module docstring.
_FREE_NO_CREDIT = ("cc0", "publicdomain", "pdm", "public domain", "no restrictions")
_FREE_WITH_CREDIT = ("by", "by-sa", "cc-by", "cc-by-sa", "attribution")


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------
# Terms that reliably surface clinical or post-mortem photography on Commons.
# The replacement is not a refusal - it is the same subject asked for in a way
# that returns a diagram instead of an operating theatre.
_GRAPHIC = {
    "surgery": "anatomical diagram", "surgical": "anatomical diagram",
    "operation": "anatomical diagram", "cadaver": "anatomical illustration",
    "autopsy": "anatomical illustration", "dissection": "anatomical illustration",
    "corpse": "anatomical illustration", "wound": "medical illustration",
    "lesion": "medical illustration", "tumour": "cell illustration",
    "tumor": "cell illustration", "biopsy": "microscope slide",
    "amputation": "medical illustration", "trauma": "medical illustration",
    "injury": "medical illustration", "blood": "laboratory",
    "gore": "medical illustration", "necrosis": "cell illustration",
    "ulcer": "medical illustration", "abscess": "medical illustration",
}

# Words that are never a useful image query on their own.
_STOP = {
    "study", "studies", "research", "trial", "randomised", "randomized",
    "patients", "participants", "effects", "effect", "association",
    "associated", "analysis", "results", "evidence", "review", "using",
    "based", "among", "during", "after", "before", "novel", "significant",
    "potential", "role", "impact", "outcomes", "data", "model", "models",
    "human", "adults", "children", "mice", "rats", "via", "with", "from",
    "their", "these", "This", "that", "into", "inside", "produce", "produced",
    "modified", "engineered", "increase", "increased", "decrease", "reduced",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")


def safe_query(q: str) -> str:
    """Rewrite a query away from clinical-graphic imagery.

    Substitution, not blocking: a study about ulcers still gets a picture, it
    just gets an illustration rather than a photograph of one.
    """
    out = str(q or "")
    for bad, good in _GRAPHIC.items():
        out = re.sub(rf"\b{re.escape(bad)}\w*\b", good, out, flags=re.I)
    return " ".join(out.split())


def subjects_for(post: Dict[str, Any], limit: int = 3) -> List[str]:
    """What to search for, best first.

    Prefers `cover.image_subjects` - concrete, photographable nouns the
    drafting model emits alongside the copy, because it has read the abstract
    and knows that "engineered gut bacteria produce 5-HTP" is a picture of an
    intestine and not a picture of the word "engineered".

    Falls back to keywords from the title so that posts drafted before that
    field existed, and skeleton() drafts, still get a cover.
    """
    cover = post.get("cover") or {}

    # THE COVER HEADLINE COMES FIRST, and it is ONE WORD.
    #
    # `image_subjects` is a phrase the drafting model emits, and in practice
    # it drifts towards the abstract of the study rather than a picture of it.
    # A paper on citrate-based hydrogels produced a stock photograph of a
    # person at a College of Engineering, because "engineering" was in the
    # subject and Commons has a great many photographs of engineers. The
    # headline is what the reader is looking at while the picture is behind
    # it, so the picture should be of the thing the headline names -
    # "hydrogels" gets a photograph of a gel.
    #
    # One word, not a phrase: an image search narrows fast, and a two-word
    # query on a specific noun ("citrate hydrogels") usually returns nothing
    # at all, which falls through to a broader, worse query.
    subs: List[str] = []
    head = headline_keyword(cover.get("headline"))
    if head:
        subs.append(head)

    subs += [str(s).strip() for s in (cover.get("image_subjects") or [])
             if str(s).strip()]
    title = str((post.get("study") or {}).get("title") or "")
    words = [w for w in _WORD.findall(title)
             if w.lower() not in {s.lower() for s in _STOP}]
    # Pairs read far better as image queries than single words:
    # "gut bacteria" finds what "gut" and "bacteria" separately do not.
    subs += [" ".join(words[i:i + 2]) for i in range(0, min(len(words), 6), 2)]

    out: List[str] = []
    for s in subs:
        q = safe_query(s)
        if q and q.lower() not in {o.lower() for o in out}:
            out.append(q)
    return out[:limit]


# Words that are in every science headline and name nothing photographable.
# Kept apart from _STOP, which is tuned for pulling nouns out of a paper
# title; this one is about what makes a picture.
_UNPHOTOGRAPHABLE = {
    "could", "might", "makes", "made", "make", "means", "matter", "matters",
    "study", "studies", "research", "people", "person", "human", "humans",
    "first", "found", "finding", "findings", "result", "results", "years",
    "year", "shows", "showed", "change", "changes", "changed", "better",
    "worse", "higher", "lower", "faster", "slower", "risk", "risks", "link",
    "linked", "links", "effect", "effects", "evidence", "data", "number",
    "numbers", "percent", "times", "level", "levels", "group", "groups",
    "using", "used", "after", "before", "without", "within", "between",
    "about", "their", "there", "these", "those", "which", "while", "where",
    "engineering", "engineered", "modular", "platform", "platforms",
    "function", "functions", "approach", "method", "methods", "system",
    "systems", "based", "novel", "potential", "significant",
    "decades", "decade", "months", "month", "weeks", "week", "hours",
    "minutes", "cheapest", "biggest", "largest", "smallest",
}


def headline_keyword(headline: Any) -> str:
    """The one photographable noun in the cover headline.

    Longest wins, which is a crude specificity proxy and a good one here:
    in "Citrate-based hydrogels are being engineered as modular wound-healing
    platforms", the longest word that is not scaffolding is "hydrogels", and
    that is exactly the picture wanted. Accent markup is stripped first, and
    a plural is reduced to its singular because image libraries file things
    under the singular.
    """
    text = str(headline or "").replace("**", "")
    stop = {s.lower() for s in _STOP}
    best, best_score = "", -99.0
    for w in _WORD.findall(text):
        lw = w.lower().strip("-")
        if len(lw) < 5 or lw in _UNPHOTOGRAPHABLE or lw in stop:
            continue
        # Score, rather than simply take the longest. Longest alone picked
        # "citrate-based" over "hydrogels" and "self-feeding" over "implants"
        # - adjectives, which no image library files anything under.
        score = float(len(lw))
        if "-" in lw:
            score -= 8          # compound modifiers: wound-healing, self-feeding
        if lw.endswith(("ed", "ing", "est", "ly", "ous", "ive", "able", "al",
                        "ic", "ical")):
            score -= 6          # adjective and participle endings
        if lw.endswith(("ion", "ions", "ment", "ments")):
            score += 2          # noun endings that outweigh the "-al"-ish hit
        if lw.endswith("s") and not lw.endswith("ss"):
            score += 3          # a plural is almost always the concrete noun
        if score > best_score:
            best, best_score = lw, score
    if best.endswith("ies") and len(best) > 4:
        best = best[:-3] + "y"
    elif best.endswith("ses") or best.endswith("xes"):
        best = best[:-2]
    elif best.endswith("s") and not best.endswith("ss"):
        best = best[:-1]
    return safe_query(best)


# ---------------------------------------------------------------------------
# Licensing
# ---------------------------------------------------------------------------
def classify_licence(raw: str) -> Tuple[bool, bool]:
    """(usable, needs_credit) for a licence string."""
    s = str(raw or "").strip().lower()
    if not s:
        return (False, True)
    if any(k in s for k in _FREE_NO_CREDIT):
        return (True, False)
    if any(k in s for k in _FREE_WITH_CREDIT):
        return (True, True)
    return (False, True)


def _one_line(s: Any, limit: int = 60) -> str:
    """Collapse third-party metadata to a single bounded line.

    Commons `Artist` is user-editable HTML and routinely holds <br>-separated
    author lists; Openverse `creator` and Pexels `photographer` are free text.
    Two things go wrong unbounded. A newline makes PIL's textlength() raise
    ValueError - which render_cover CATCHES, so the credit silently vanishes
    and a CC-BY image publishes uncredited, the exact outcome this module
    says must never happen. And an over-long string renders off-frame, or
    puts attacker-chosen text on the account's own cover.
    """
    return " ".join(str(s or "").split())[:limit]


def credit_line(art: Dict[str, Any]) -> str:
    """The text that must appear with the published image, or "" if none is
    required. One line, bounded - it is printed onto the slide."""
    if not art or not art.get("needs_credit"):
        return ""
    who = _one_line(art.get("author")) or "Unknown"
    lic = _one_line(art.get("licence"), 30).upper()
    src = _one_line(art.get("source"), 40)
    return _one_line(f"Cover image: {who} / {lic} via {src}", 120)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def _get(url: str, params: Dict[str, Any],
         headers: Optional[Dict[str, str]] = None) -> Any:
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def search_commons(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Wikimedia Commons. No key. Best for diagrams and real specimens."""
    try:
        data = _get(COMMONS_API, {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
            "gsrlimit": limit, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": 1600,
        })
    except Exception as e:
        print(f"  ! Commons search failed for {query!r}: {type(e).__name__}")
        return []
    out = []
    for page in ((data or {}).get("query") or {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = str((meta.get("LicenseShortName") or {}).get("value") or "")
        author = re.sub(r"<[^>]+>", "", str((meta.get("Artist") or {}).get("value") or ""))
        usable, credit = classify_licence(lic)
        if not usable:
            continue
        if min(int(info.get("width") or 0), int(info.get("height") or 0)) < MIN_PIXELS:
            continue
        title = page.get("title", "")
        if not looks_english(title, author):
            # A file captioned in another language is a picture captioned in
            # another language. See the note above looks_english().
            continue
        out.append({"url": info.get("thumburl") or info.get("url"),
                    "licence": lic, "author": author.strip()[:80] or "Wikimedia Commons",
                    "needs_credit": credit, "source": "Wikimedia Commons",
                    "page": title})
    return out


def search_pexels(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Pexels. Needs a free key; skipped entirely without one."""
    if not PEXELS_KEY:
        return []
    try:
        data = _get(PEXELS_API, {"query": query, "per_page": limit,
                                 "orientation": "portrait"},
                    headers={"Authorization": PEXELS_KEY})
    except Exception as e:
        print(f"  ! Pexels search failed for {query!r}: {type(e).__name__}")
        return []
    out = []
    for p in (data or {}).get("photos") or []:
        src = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if not src:
            continue
        out.append({"url": src, "licence": "Pexels", "needs_credit": False,
                    "author": str(p.get("photographer") or ""),
                    "source": "Pexels", "page": p.get("url", "")})
    return out


def search_openverse(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Openverse. No key. Last because its results overlap the other two."""
    try:
        data = _get(OPENVERSE_API, {"q": query, "page_size": limit,
                                    "license_type": "commercial,modification"})
    except Exception as e:
        print(f"  ! Openverse search failed for {query!r}: {type(e).__name__}")
        return []
    out = []
    for r in (data or {}).get("results") or []:
        usable, credit = classify_licence(r.get("license") or "")
        if not usable or not r.get("url"):
            continue
        if not looks_english(r.get("title"), r.get("creator")):
            continue
        out.append({"url": r["url"], "licence": str(r.get("license") or "").upper(),
                    "author": str(r.get("creator") or ""), "needs_credit": credit,
                    "source": "Openverse",
                    "page": str(r.get("title") or "")[:120]
                            or r.get("foreign_landing_url", "")})
    return out


SOURCES = (("wikimedia", search_commons),
           ("pexels", search_pexels),
           ("openverse", search_openverse))


# ---------------------------------------------------------------------------
# Language, and why it is checked on the filename
#
# A post about dental implants shipped with a diagram captioned in ARMENIAN
# across the whole cover. Wikimedia Commons is a global archive: a search for
# an English term matches files described in any language, and a labelled
# diagram carries its labels in whatever language its author drew it in.
#
# There is no cheap way to read the text INSIDE an image, but the file's own
# title and description are right there and are written in the same language
# as the labels essentially every time. So: if the metadata is not in a Latin
# script, the picture is not either, and it is refused.
#
# Explicit script ranges rather than "is it ASCII". Scientific filenames are
# full of accented Latin (Müller, Ångström) and of Greek letters used as
# symbols (α-synuclein, µm) - all of them fine, all of them non-ASCII. These
# are the scripts that mean the file is captioned in another LANGUAGE.
# ---------------------------------------------------------------------------
_NON_LATIN = (
    (0x0400, 0x052F),    # Cyrillic
    (0x0530, 0x058F),    # Armenian  <- the one that shipped
    (0x0590, 0x05FF),    # Hebrew
    (0x0600, 0x06FF),    # Arabic
    (0x0700, 0x074F),    # Syriac
    (0x0900, 0x097F),    # Devanagari
    (0x0E00, 0x0E7F),    # Thai
    (0x10A0, 0x10FF),    # Georgian
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x3040, 0x30FF),    # Hiragana, Katakana
    (0x3400, 0x9FFF),    # CJK
    (0xAC00, 0xD7AF),    # Hangul syllables
)

# Titles that describe a drawing rather than a photograph. A diagram is the
# thing that carries text, and text behind a headline is noise whatever
# language it is in.
_DIAGRAMMATIC = ("diagram", "schema", "scheme", "chart", "graph", "infographic",
                 "flowchart", "map of", "illustration", "drawing", "sketch",
                 "logo", "icon", "poster", "screenshot", "table", "plot of",
                 "timeline", "figure ")


def looks_english(*texts: Any) -> bool:
    """False if any of this file's own metadata is in a non-Latin script."""
    for text in texts:
        for ch in str(text or ""):
            cp = ord(ch)
            for lo, hi in _NON_LATIN:
                if lo <= cp <= hi:
                    return False
    return True


def _score(cand: Dict[str, Any]) -> float:
    """Rank the usable candidates. Higher is better.

    Credit is the original axis - an image needing none is simply less to get
    wrong. The rest is about what makes a readable COVER: a headline is set
    over this picture, so a photograph beats a labelled diagram, and a
    diagram's labels are the text that fights the headline.
    """
    score = 0.0 if cand.get("needs_credit") else 1.0
    title = f"{cand.get('page', '')} {cand.get('author', '')}".lower()
    if any(w in title for w in _DIAGRAMMATIC):
        score -= 2.0
    url = str(cand.get("url") or "").lower().split("?")[0]
    if url.endswith((".jpg", ".jpeg")):
        score += 0.5       # photographs are filed as jpg, diagrams as png/svg
    elif url.endswith(".svg"):
        score -= 1.0
    return score


def find_image(post: Dict[str, Any],
               order: Sequence[str] = ("wikimedia", "pexels", "openverse"),
               query: Optional[str] = None,
               exclude: Sequence[str] = (),
               ) -> Optional[Dict[str, Any]]:
    """The first usable image for this post, or None.

    None is a perfectly good outcome: the cover falls back to the flat
    background it has today, which is what every post looks like now.

    `query` overrides the automatic search terms, so a reviewer who can see
    the picture and knows it is wrong can say what to look for instead.

    `exclude` is the URLs already tried. Without it, asking for a different
    picture re-runs the same deterministic search, scores the same candidates
    the same way, and hands back the SAME image - which looks exactly like
    the request being ignored.
    """
    subs = [safe_query(query)] if query else subjects_for(post)
    subs = [s for s in subs if s]
    if not subs:
        return None
    skip = {str(u) for u in exclude if u}
    by_name = dict(SOURCES)
    for name in order:
        fn = by_name.get(name)
        if fn is None:
            continue
        for q in subs:
            try:
                cands = fn(q)
            except Exception as e:
                print(f"  ! {name} raised on {q!r}: {type(e).__name__}")
                continue
            cands = [c for c in cands
                     if c.get("url") and str(c["url"]) not in skip]
            if not cands:
                continue
            best = sorted(cands, key=_score, reverse=True)[0]
            best["query"] = q
            print(f"  cover image: {name} <- {q!r} ({best.get('licence')})")
            return best
    print("  no cover image found; using the flat background")
    return None


def download(art: Dict[str, Any]) -> Optional[bytes]:
    """Fetch the chosen image, refusing anything that is not a public host.

    Routed through fetch_guard for the same reason friday-reel.yml is: these
    URLs come from a third-party search result, they are followed by an
    automated job, and whatever comes back is committed to a public repo.
    """
    url = str((art or {}).get("url") or "")
    if not url:
        return None
    try:
        from fetch_guard import resolve
        url = resolve(url)
    except Exception as e:
        print(f"  ! refusing cover image url: {e}")
        return None
    try:
        # allow_redirects=False, and this is the whole point of the guard
        # above. resolve() vets each hop with HEAD; a host is free to answer
        # HEAD 200 and GET 302, and requests follows redirects by default -
        # so the URL that was checked and the URL that was fetched could be
        # different hosts. Openverse hands back URLs on arbitrary third-party
        # providers, which means an attacker picks that host. friday-reel.yml
        # passes --max-redirs 0 for the same reason.
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                         stream=True, allow_redirects=False)
        if r.status_code != 200:
            print(f"  ! cover image fetch returned {r.status_code}, skipping it")
            return None

        # A wall-clock budget, because `timeout` is a per-read timeout, not a
        # deadline: a server dribbling a byte every 19 seconds keeps this loop
        # alive until the 12MB cap, which is effectively forever, and the
        # weekday post never happens.
        deadline = time.monotonic() + DOWNLOAD_BUDGET_S
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            if time.monotonic() > deadline:
                print("  ! cover image download is too slow, abandoning it")
                return None
            buf.write(chunk)
            if buf.tell() > MAX_BYTES:
                print("  ! cover image is too large, skipping it")
                return None
        return buf.getvalue()
    except Exception as e:
        print(f"  ! cover image download failed: {type(e).__name__}")
        return None


def fetch_for(post: Dict[str, Any], dest_dir: Any,
              query: Optional[str] = None,
              exclude: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    """Find, download and save a cover image. Returns its record, or None.

    `query` and `exclude` are passed straight through to find_image() so the
    review step can ask for a different picture, or for a picture of
    something specific. See its docstring.
    """
    from pathlib import Path
    art = find_image(post, query=query, exclude=exclude)
    if not art:
        return None
    data = download(art)
    if not data:
        return None
    try:
        from PIL import Image
        # An ALLOW-LIST of decoders. Without `formats=`, PIL will dispatch on
        # content to any plugin it has - including EPS, whose load() shells
        # out to Ghostscript. PostScript is a programming language and these
        # bytes come from a URL a third-party search result chose.
        #
        # MAX_IMAGE_PIXELS bounds the DECODED size. MAX_BYTES only bounds the
        # wire transfer, and a ~1MB PNG can decode to hundreds of megabytes.
        # MAX_IMAGE_PIXELS is a PROCESS-WIDE setting, so it is set and put
        # back. Leaving it lowered leaked out of here and into every other
        # decode in the same run - the reel builder composes 1080x1920 frames
        # and started raising DecompressionBombError because a test had
        # tightened this for its own purposes. A global mutated as a side
        # effect is not a safety measure, it is a different bug.
        prev_cap = Image.MAX_IMAGE_PIXELS
        try:
            Image.MAX_IMAGE_PIXELS = MAX_PIXELS
            im = Image.open(io.BytesIO(data), formats=("JPEG", "PNG", "WEBP"))
            # Dimensions BEFORE load(): the header is cheap, the decode is
            # not, and checking afterwards throws away the chance to refuse.
            if min(im.size) < MIN_PIXELS:
                print(f"  ! cover image is only {im.size[0]}x{im.size[1]}, skipping")
                return None
            if im.size[0] * im.size[1] > MAX_PIXELS:
                print(f"  ! cover image decodes to {im.size[0]}x{im.size[1]}, skipping")
                return None
            im.load()
        finally:
            Image.MAX_IMAGE_PIXELS = prev_cap
        # A SUBFOLDER, and this is not tidiness.
        #
        # docs/img/<id>/*.jpg is globbed in three places - pipeline's publish
        # step, reel.slide_paths(), review.rerender() - and each treats every
        # jpg it finds as a SLIDE. A cover_bg.jpg sitting beside them would be
        # published as an extra carousel panel, muxed into the Reel, and
        # deleted by the next re-render. All three globs are non-recursive, so
        # one directory down is invisible to them while staying committed and
        # served, which is what a later `revise` needs to re-render.
        d = Path(dest_dir) / "bg"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "cover.jpg"
        im.convert("RGB").save(path, "JPEG", quality=88, optimize=True)
    except Exception as e:
        print(f"  ! cover image could not be decoded: {type(e).__name__}")
        return None
    art["path"] = str(path)
    return art


if __name__ == "__main__":                              # pragma: no cover
    import json
    post = json.loads(open(sys.argv[1]).read())
    print(json.dumps(find_image(post), indent=2))
