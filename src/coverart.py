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
    subs = [str(s).strip() for s in (cover.get("image_subjects") or [])
            if str(s).strip()]
    if not subs:
        title = str((post.get("study") or {}).get("title") or "")
        words = [w for w in _WORD.findall(title)
                 if w.lower() not in {s.lower() for s in _STOP}]
        # Pairs read far better as image queries than single words:
        # "gut bacteria" finds what "gut" and "bacteria" separately do not.
        subs = [" ".join(words[i:i + 2]) for i in range(0, min(len(words), 6), 2)]
    return [safe_query(s) for s in subs[:limit] if safe_query(s)]


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
        out.append({"url": info.get("thumburl") or info.get("url"),
                    "licence": lic, "author": author.strip()[:80] or "Wikimedia Commons",
                    "needs_credit": credit, "source": "Wikimedia Commons",
                    "page": page.get("title", "")})
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
        out.append({"url": r["url"], "licence": str(r.get("license") or "").upper(),
                    "author": str(r.get("creator") or ""), "needs_credit": credit,
                    "source": "Openverse", "page": r.get("foreign_landing_url", "")})
    return out


SOURCES = (("wikimedia", search_commons),
           ("pexels", search_pexels),
           ("openverse", search_openverse))


def _score(cand: Dict[str, Any]) -> float:
    """Prefer images that need no credit - they are simply less to get wrong."""
    return 0.0 if cand.get("needs_credit") else 1.0


def find_image(post: Dict[str, Any],
               order: Sequence[str] = ("wikimedia", "pexels", "openverse")
               ) -> Optional[Dict[str, Any]]:
    """The first usable image for this post, or None.

    None is a perfectly good outcome: the cover falls back to the flat
    background it has today, which is what every post looks like now.
    """
    subs = subjects_for(post)
    if not subs:
        return None
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
            cands = [c for c in cands if c.get("url")]
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


def fetch_for(post: Dict[str, Any], dest_dir: Any) -> Optional[Dict[str, Any]]:
    """Find, download and save a cover image. Returns its record, or None."""
    from pathlib import Path
    art = find_image(post)
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
