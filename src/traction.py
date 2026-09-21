"""
Studies that a general audience has ALREADY found interesting.

WHY THIS EXISTS, AND HOW IT DIFFERS FROM interest.py
====================================================
`interest.py` predicts. It asks a heuristic and then a model "would a curious
non-scientist stop scrolling for this?", which is a good question asked of
something that has never seen the account's audience.

This module measures instead. r/science has millions of subscribers who are
overwhelmingly not scientists, and every post there carries a number attached
to the only question that matters: did people care. Hacker News is the same
signal from a different crowd. EurekAlert and ScienceDaily are that judgement
made earlier still, by press officers and science editors whose entire job is
picking which paper the public will read.

None of these are better than a model at judging whether a study can be told
HONESTLY - that is interest.py's real contribution and this does not replace
it. What they are better at is knowing what lands, because they are made of
people who already decided.

TWO USES, AND THE SECOND ONE IS THE POINT
=========================================
1. As a SIGNAL - `traction_score(study)` boosts a candidate the normal
   pipeline already found. Cheap, safe, marginal.

2. As a DISCOVERY CHANNEL - `discover(niche)` starts from what got traction
   this week, resolves it to a DOI and a real record, and hands that to the
   ordinary vetting path. This is the bigger change: the pipeline stops
   asking "which of this week's papers is most interesting?" and starts
   asking "of the papers that demonstrably interested people, which ones can
   we tell honestly?"

WHAT THIS MODULE IS NOT ALLOWED TO DO
=====================================
Everything here is a SUGGESTION of what to look at. A study arriving through
this path meets `vet()` exactly as one from Europe PMC does: same recency
rule, same journal standards, same retraction check, same caveat
requirements, same human approval. Popularity is the least trustworthy signal
in the building - the things that go viral on r/science skew to the
overstated - so it is wired to open the door, never to skip a check behind it.

Pinned by test_traction_cannot_bypass_vetting.

FAILING CLOSED
==============
Every fetch here is a third-party service that can be down, rate-limited, or
blocked by whatever egress the runner has. All of them are optional: a
failure returns NOTHING from that source and says so, and the pipeline
proceeds on its ordinary sources. What must never happen is a failure that
silently returns a WORSE answer that looks like a good one - the pattern the
24 Aug audit flagged, where a Crossref timeout made every paper look
un-retracted.

Instagram is deliberately absent from the source list. Accounts posting
popular study content would be an excellent signal and there is no lawful,
reliable way to read them automatically: the site blocks unauthenticated
access and scraping other people's accounts is against its terms. Everything
below is a public, documented, free API or feed meant to be read by programs.
"""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

UA = ("onestudytoday/1.0 (+https://onestudytoday.github.io; "
      "one peer-reviewed study a day)")
TIMEOUT = 20

# ---------------------------------------------------------------------------
# Sources. Each is (name, enabled-by-default) and each can fail on its own.
# ---------------------------------------------------------------------------
REDDIT_SUBS = ["science", "EverythingScience", "psychology", "health"]
REDDIT_URL = "https://www.reddit.com/r/{sub}/top.json"
HN_URL = "https://hn.algolia.com/api/v1/search_by_date"
CROSSREF_EVENTS = "https://api.eventdata.crossref.org/v1/events"
RSS_FEEDS = {
    "eurekalert": "https://www.eurekalert.org/rss/technology_engineering.xml",
    "sciencedaily": "https://www.sciencedaily.com/rss/top/science.xml",
}

# A DOI as it appears in a URL or a body of text. Deliberately strict about
# the prefix and permissive about the suffix, which is what the spec says.
# The trailing-punctuation strip matters: a DOI at the end of a sentence in a
# reddit selftext arrives as "10.1038/s41586-024-07123-7." and resolves to
# nothing with the full stop attached.
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
_DOI_TRAILING = ".,;:)]}>\"'"

# Scores are deliberately coarse. This is a nudge in a ranking, not a
# measurement, and pretending otherwise by tuning to two decimal places would
# be false precision on a sample of one week's posts.
REDDIT_SCORE_CAP = 20.0
HN_SCORE_CAP = 10.0
PRESS_SCORE = 6.0
EVENTS_CAP = 8.0


@dataclass
class TractionHit:
    """One piece of evidence that people cared about a paper."""
    doi: str = ""
    title: str = ""
    url: str = ""
    source: str = ""          # reddit:science | hn | eurekalert | crossref
    score: float = 0.0        # upvotes, points, or a flat value for a feed
    comments: int = 0
    when: str = ""            # ISO date, best effort

    def normalised(self) -> float:
        """A 0-ish..20-ish number, comparable across sources."""
        if self.source.startswith("reddit"):
            return min(self.score / 250.0, 1.0) * REDDIT_SCORE_CAP
        if self.source == "hn":
            return min(self.score / 100.0, 1.0) * HN_SCORE_CAP
        if self.source == "crossref":
            return min(self.score / 10.0, 1.0) * EVENTS_CAP
        return PRESS_SCORE


def clean_doi(raw: str) -> str:
    """Normalise a DOI found in the wild. Empty string if it is not one."""
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = _DOI.search(s)
    if not m:
        return ""
    out = m.group(1).rstrip(_DOI_TRAILING)
    # A DOI cannot sensibly be this long; something has run together.
    return out.lower() if len(out) <= 200 else ""


def dois_in(text: str) -> List[str]:
    """Every distinct DOI mentioned in a blob of text, in order."""
    out, seen = [], set()
    for m in _DOI.finditer(str(text or "")):
        d = clean_doi(m.group(1))
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    r = requests.get(url, params=params or {},
                     headers={"User-Agent": UA, "Accept": "application/json"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
def reddit_hits(subs: Optional[Sequence[str]] = None, window: str = "week",
                limit: int = 50) -> List[TractionHit]:
    """Top posts from the science subreddits, as traction evidence.

    Reads the public .json view, which needs no key and no account. Only the
    submission's own fields are read - never comments, never user data.

    Most posts link to a news write-up rather than the paper, so the DOI is
    often absent; those still count as a TITLE-matched signal even when they
    cannot seed discovery. That asymmetry is deliberate: a title match is
    good enough to nudge a ranking, and not good enough to source a study
    from, because the wrong paper would be drafted with total confidence.
    """
    out: List[TractionHit] = []
    for sub in (subs or REDDIT_SUBS):
        try:
            data = _get_json(REDDIT_URL.format(sub=sub),
                             {"t": window, "limit": limit})
        except Exception as e:
            print(f"  ! reddit r/{sub} unavailable, skipping it: {e}")
            continue
        for child in ((data or {}).get("data") or {}).get("children") or []:
            # Every layer is type-checked rather than trusted. This is a JSON
            # shape from a third party that can change without notice, and
            # `(child or {}).get(...)` throws on a row that is a number rather
            # than returning nothing - which is how a single odd row in a
            # listing takes down the whole weekday draft.
            if not isinstance(child, dict):
                continue
            d = child.get("data")
            if not isinstance(d, dict) or d.get("stickied"):
                continue
            blob = " ".join(str(d.get(k) or "") for k in
                            ("url", "url_overridden_by_dest", "selftext", "title"))
            found = dois_in(blob)
            out.append(TractionHit(
                doi=found[0] if found else "",
                title=str(d.get("title") or "")[:400],
                url=str(d.get("url") or ""),
                source=f"reddit:{sub}",
                score=float(d.get("score") or 0),
                comments=int(d.get("num_comments") or 0),
                when=_iso(d.get("created_utc")),
            ))
    return out


def _iso(epoch: Any) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Hacker News
# ---------------------------------------------------------------------------
def hn_hits(days: int = 7, limit: int = 60,
            query: str = "study OR research OR paper") -> List[TractionHit]:
    since = int(time.time()) - days * 86400
    try:
        data = _get_json(HN_URL, {"query": query, "tags": "story",
                                  "numericFilters": f"created_at_i>{since},points>20",
                                  "hitsPerPage": limit})
    except Exception as e:
        print(f"  ! Hacker News unavailable, skipping it: {e}")
        return []
    out = []
    for h in (data or {}).get("hits") or []:
        blob = f"{h.get('url') or ''} {h.get('story_text') or ''} {h.get('title') or ''}"
        found = dois_in(blob)
        out.append(TractionHit(
            doi=found[0] if found else "",
            title=str(h.get("title") or "")[:400],
            url=str(h.get("url") or ""),
            source="hn",
            score=float(h.get("points") or 0),
            comments=int(h.get("num_comments") or 0),
            when=str(h.get("created_at") or "")[:10],
        ))
    return out


# ---------------------------------------------------------------------------
# Press-release feeds
# ---------------------------------------------------------------------------
def rss_hits(feeds: Optional[Dict[str, str]] = None) -> List[TractionHit]:
    """Studies a press office decided the public would want.

    Weaker evidence than an upvote count - it is one editor's guess rather
    than many readers' behaviour - but it is early, free, and biased towards
    exactly the lay-interesting end of the literature.
    """
    out: List[TractionHit] = []
    for name, url in (feeds or RSS_FEEDS).items():
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"  ! {name} feed unavailable, skipping it: {e}")
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "")
            found = dois_in(f"{link} {desc}")
            if not title:
                continue
            out.append(TractionHit(
                doi=found[0] if found else "", title=title[:400], url=link,
                source=name, score=0.0,
                when=(item.findtext("pubDate") or "")[:16]))
    return out


# ---------------------------------------------------------------------------
# Crossref Event Data
# ---------------------------------------------------------------------------
def crossref_event_hits(doi: str) -> float:
    """How much public chatter Crossref has recorded about one DOI.

    Free and keyless, unlike Altmetric, which grants keys only to academic
    projects. A previous session ruled this out after it failed from a sandbox
    whose proxy blocks all outbound curl - a conclusion about the sandbox, not
    about the API. It runs on GitHub Actions, which has ordinary egress, so it
    is wired up here and simply contributes nothing if it cannot be reached.
    """
    doi = clean_doi(doi)
    if not doi:
        return 0.0
    try:
        data = _get_json(CROSSREF_EVENTS, {"obj-id": doi, "rows": 0})
        return float(((data or {}).get("message") or {}).get("total-results") or 0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
_WORD = re.compile(r"[a-z]{4,}")
_STOP = {"study", "studies", "research", "researchers", "科学", "shows", "finds",
         "found", "could", "first", "reveals", "according", "scientists",
         "people", "using", "used", "after", "their", "these", "more", "than",
         "with", "from", "that", "this", "have", "been", "says", "may"}


def _title_words(t: str) -> set:
    return {w for w in _WORD.findall(str(t or "").lower()) if w not in _STOP}


def gather(days: int = 7, use_reddit: bool = True, use_hn: bool = True,
           use_rss: bool = True) -> List[TractionHit]:
    """Everything, from whichever sources answer. Never raises."""
    hits: List[TractionHit] = []
    if use_reddit:
        hits += reddit_hits()
    if use_hn:
        hits += hn_hits(days=days)
    if use_rss:
        hits += rss_hits()
    return hits


def traction_score(study: Any, hits: Optional[Sequence[TractionHit]] = None,
                   check_events: bool = False) -> float:
    """How much independent evidence there is that people care about this.

    Matched first on DOI (exact, trustworthy) and then on title overlap
    (fuzzy, used only to add a score, never to identify a paper for
    drafting). A study with no evidence scores 0 and is not penalised - most
    good papers never reach reddit, and this must not turn into "only post
    what is already popular", which would make the account a lagging
    aggregator of other people's picks.
    """
    hits = hits or []
    doi = clean_doi(getattr(study, "doi", "") or "")
    title_words = _title_words(getattr(study, "title", ""))
    best = 0.0
    for h in hits:
        if doi and h.doi and h.doi == doi:
            best = max(best, h.normalised())
            continue
        if title_words and h.title:
            shared = title_words & _title_words(h.title)
            # Four shared content words is a deliberately high bar: news
            # headlines and paper titles share little vocabulary, and a loose
            # match would hand a viral story's score to an unrelated paper.
            if len(shared) >= 4:
                best = max(best, h.normalised() * 0.6)
    if check_events and doi:
        best = max(best, min(crossref_event_hits(doi) / 10.0, 1.0) * EVENTS_CAP)
    return round(best, 2)


def discover_dois(days: int = 7, min_score: float = 3.0,
                  hits: Optional[Sequence[TractionHit]] = None) -> List[Dict[str, Any]]:
    """DOIs worth looking up, best evidence first.

    ONLY returns entries that carry a real DOI. A hit whose DOI could not be
    extracted is dropped rather than guessed at from its headline: resolving
    "Scientists find link between coffee and memory" by search would
    confidently return SOME paper, and the pipeline would then draft a post
    about a study nobody actually upvoted. Wrong-but-confident is the one
    outcome worse than finding nothing.
    """
    hits = list(hits if hits is not None else gather(days=days))
    by_doi: Dict[str, Dict[str, Any]] = {}
    for h in hits:
        if not h.doi:
            continue
        n = h.normalised()
        if n < min_score:
            continue
        cur = by_doi.get(h.doi)
        if cur is None or n > cur["score"]:
            by_doi[h.doi] = {"doi": h.doi, "score": n, "title": h.title,
                             "source": h.source, "url": h.url, "when": h.when}
        else:
            # Corroboration from a second source is worth more than either
            # source alone, but capped so a story on every feed at once
            # cannot dominate the ranking outright.
            cur["score"] = min(cur["score"] + n * 0.25, REDDIT_SCORE_CAP * 1.5)
    return sorted(by_doi.values(), key=lambda d: d["score"], reverse=True)


def _main() -> None:                                   # pragma: no cover
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    hits = gather(days=days)
    print(f"{len(hits)} traction hits, {sum(1 for h in hits if h.doi)} with a DOI\n")
    for d in discover_dois(days=days, hits=hits)[:25]:
        print(f"  {d['score']:5.1f}  {d['source']:22}  {d['doi']}")
        print(f"         {d['title'][:96]}")


if __name__ == "__main__":                             # pragma: no cover
    _main()
