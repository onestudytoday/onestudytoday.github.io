"""
Study sourcing.

Pulls candidate papers per weekday niche from four public APIs, normalises
everything into one `Study` shape, and drops anything already used.

  Europe PMC   life sciences, medicine, psychology, ecology. Also indexes
               preprints (SRC:PPR) and, critically, carries retraction
               notices in commentCorrectionList - which is how the guardrail
               in vet.py catches retracted papers.
  arXiv        physics, astro, quantum. The only good source for Thursday.
  Crossref     publisher metadata, license, funders, retraction relations.
  bioRxiv API  preprint posting dates and published-journal linkage.

None of these need an API key. Europe PMC and Crossref both ask for a contact
email in the user agent, which we send - it gets you into the polite pool and
much better rate limits.

CLI:
    python src/sources.py psych --days 14
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
import yaml

from config import CACHE, DATA, ROOT

CONTACT = "onestudytoday-bot@example.com"   # change to your email; improves rate limits
UA = f"One Study Today/1.0 (Instagram science summaries; mailto:{CONTACT})"
TIMEOUT = 45

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF = "https://api.crossref.org/works"
ARXIV = "https://export.arxiv.org/api/query"
BIORXIV = "https://api.biorxiv.org/details"

PREPRINT_SERVERS = {
    "biorxiv": "bioRxiv", "medrxiv": "medRxiv", "arxiv": "arXiv",
    "psyarxiv": "PsyArXiv", "ssrn": "SSRN", "research square": "Research Square",
    "chemrxiv": "ChemRxiv", "osf": "OSF Preprints",
}


# ---------------------------------------------------------------------------
@dataclass
class Study:
    source: str                 # europepmc | arxiv | crossref
    ext_id: str
    title: str
    abstract: str
    journal: str
    publisher: str = ""
    authors: List[str] = field(default_factory=list)
    doi: str = ""
    url: str = ""
    pub_date: str = ""          # ISO yyyy-mm-dd
    is_preprint: bool = False
    server: str = ""            # preprint server display name, if any
    pub_types: List[str] = field(default_factory=list)
    citations: int = 0
    open_access: bool = False
    retraction_notices: List[str] = field(default_factory=list)
    funders: List[str] = field(default_factory=list)
    license: str = ""
    niche: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        base = (self.doi or f"{self.source}:{self.ext_id}").lower().strip()
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def age_days(self) -> Optional[int]:
        if not self.pub_date:
            return None
        try:
            d = datetime.strptime(self.pub_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        return (date.today() - d).days

    @property
    def pub_date_display(self) -> str:
        try:
            return datetime.strptime(self.pub_date[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
        except Exception:
            return self.pub_date or ""

    @property
    def doi_display(self) -> str:
        return f"doi.org/{self.doi}" if self.doi else (self.url or "")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["key"] = self.key
        d["age_days"] = self.age_days
        d["pub_date_display"] = self.pub_date_display
        d["doi_display"] = self.doi_display
        return d


# ---------------------------------------------------------------------------
def _get(url: str, params: Dict[str, Any], cache_key: str = "", ttl: int = 3600) -> Any:
    """GET with a tiny on-disk cache so re-runs during review cost nothing."""
    cp: Optional[Path] = None
    if cache_key:
        cp = CACHE / f"{hashlib.sha1(cache_key.encode()).hexdigest()[:20]}.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime) < ttl:
            return json.loads(cp.read_text())
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    payload = r.json() if "json" in ct else r.text
    if cp is not None:
        cp.write_text(json.dumps(payload) if "json" in ct else json.dumps({"_text": payload}))
    return payload


def _cached_text(url: str, params: Dict[str, Any], cache_key: str, ttl: int = 3600) -> str:
    p = _get(url, params, cache_key, ttl)
    if isinstance(p, dict) and "_text" in p:
        return p["_text"]
    return p if isinstance(p, str) else json.dumps(p)


# ---------------------------------------------------------------------------
# Europe PMC
# ---------------------------------------------------------------------------
def europepmc_search(topic_query: str, days: int, limit: int = 60,
                     include_preprints: bool = True) -> List[Study]:
    since = (date.today() - timedelta(days=days)).isoformat()
    until = date.today().isoformat()
    srcs = "(SRC:MED OR SRC:PMC" + (" OR SRC:PPR" if include_preprints else "") + ")"
    q = (
        f"({topic_query.strip()}) AND {srcs} "
        f"AND (FIRST_PDATE:[{since} TO {until}]) "
        f"AND (HAS_ABSTRACT:Y) AND (LANG:eng)"
    )
    params = {"query": q, "format": "json", "resultType": "core",
              "pageSize": min(limit, 100), "sort": "P_PDATE_D desc"}
    data = _get(EPMC, params, cache_key=f"epmc:{q}:{limit}")
    out: List[Study] = []
    for r in data.get("resultList", {}).get("result", []):
        out.append(_epmc_to_study(r))
    return out


def _epmc_to_study(r: Dict[str, Any]) -> Study:
    ji = r.get("journalInfo", {}) or {}
    journal = (ji.get("journal", {}) or {}).get("title", "") or r.get("bookOrReportDetails", {}).get("publisher", "")
    src = (r.get("source") or "").upper()
    is_pp = src == "PPR"
    server = ""
    if is_pp:
        blob = f"{journal} {r.get('publisher','')} {r.get('doi','')}".lower()
        for k, v in PREPRINT_SERVERS.items():
            if k in blob:
                server = v
                break
        server = server or "preprint server"

    notices = []
    for cc in (r.get("commentCorrectionList", {}) or {}).get("commentCorrection", []) or []:
        t = (cc.get("type") or "").lower()
        if "retraction" in t or "withdraw" in t or "expression of concern" in t:
            notices.append(cc.get("type", ""))

    pub_date = (r.get("firstPublicationDate")
                or ji.get("printPublicationDate")
                or (f"{r.get('pubYear','')}-01-01" if r.get("pubYear") else ""))

    doi = (r.get("doi") or "").lower()
    url = (f"https://doi.org/{doi}" if doi
           else f"https://europepmc.org/article/{src}/{r.get('id','')}")

    authors = [a.strip() for a in (r.get("authorString", "") or "").split(",") if a.strip()]

    return Study(
        source="europepmc",
        ext_id=f"{src}:{r.get('id','')}",
        title=(r.get("title") or "").strip().rstrip("."),
        abstract=_clean(r.get("abstractText", "") or ""),
        journal=journal or ("preprint" if is_pp else ""),
        publisher=r.get("publisher", "") or "",
        authors=authors,
        doi=doi,
        url=url,
        pub_date=pub_date[:10] if pub_date else "",
        is_preprint=is_pp,
        server=server,
        pub_types=[p for p in (r.get("pubTypeList", {}) or {}).get("pubType", []) or []],
        citations=int(r.get("citedByCount", 0) or 0),
        open_access=(r.get("isOpenAccess") == "Y"),
        retraction_notices=notices,
        raw=r,
    )


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_search(categories: List[str], days: int, limit: int = 60) -> List[Study]:
    if not categories:
        return []
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y%m%d%H%M")
    until = datetime.utcnow().strftime("%Y%m%d%H%M")
    cat_q = "+OR+".join(f"cat:{c}" for c in categories)
    search = f"({cat_q})+AND+submittedDate:[{since}+TO+{until}]"
    # arXiv wants the query pre-encoded; requests would re-encode the +
    url = (f"{ARXIV}?search_query={search}&start=0&max_results={limit}"
           f"&sortBy=submittedDate&sortOrder=descending")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return _parse_arxiv(r.text)


def _parse_arxiv(xml_text: str) -> List[Study]:
    root = ET.fromstring(xml_text)
    out: List[Study] = []
    for e in root.findall("a:entry", ATOM):
        def t(p):
            n = e.find(p, ATOM)
            return (n.text or "").strip() if n is not None else ""
        aid = t("a:id")
        doi_node = e.find("arxiv:doi", ATOM)
        jref = e.find("arxiv:journal_ref", ATOM)
        cats = [c.attrib.get("term", "") for c in e.findall("a:category", ATOM)]
        authors = [a.find("a:name", ATOM).text for a in e.findall("a:author", ATOM)
                   if a.find("a:name", ATOM) is not None]
        published = t("a:published")[:10]
        has_journal = jref is not None and (jref.text or "").strip()
        out.append(Study(
            source="arxiv",
            ext_id=aid.rsplit("/", 1)[-1],
            title=re.sub(r"\s+", " ", t("a:title")),
            abstract=_clean(t("a:summary")),
            journal=(jref.text.strip() if has_journal else "arXiv"),
            publisher="arXiv",
            authors=authors,
            doi=(doi_node.text.strip().lower() if doi_node is not None else ""),
            url=aid,
            pub_date=published,
            is_preprint=not bool(has_journal),
            server="arXiv" if not has_journal else "",
            pub_types=cats,
            open_access=True,
            raw={"categories": cats},
        ))
    return out


# ---------------------------------------------------------------------------
# Crossref - metadata enrichment + retraction relations
# ---------------------------------------------------------------------------
def crossref_lookup(doi: str) -> Dict[str, Any]:
    if not doi:
        return {}
    try:
        d = _get(f"{CROSSREF}/{doi}", {"mailto": CONTACT},
                 cache_key=f"cr:{doi}", ttl=86400)
        return d.get("message", {})
    except Exception:
        return {}


def enrich_from_crossref(s: Study) -> Study:
    m = crossref_lookup(s.doi)
    if not m:
        return s
    s.publisher = s.publisher or m.get("publisher", "")
    s.license = ";".join(sorted({l.get("URL", "") for l in m.get("license", []) or []}))
    s.funders = [f.get("name", "") for f in m.get("funder", []) or [] if f.get("name")]
    if not s.journal:
        ct = m.get("container-title") or []
        s.journal = ct[0] if ct else ""
    # retraction / concern relations
    for u in m.get("update-to", []) or []:
        lbl = (u.get("label") or u.get("type") or "").lower()
        if any(w in lbl for w in ("retract", "withdraw", "concern")):
            s.retraction_notices.append(u.get("label") or u.get("type"))
    if (m.get("type") or "") in ("retraction",):
        s.retraction_notices.append("crossref type=retraction")
    return s


def crossref_retraction_check(doi: str) -> List[str]:
    """Second, independent retraction probe. Cheap insurance."""
    if not doi:
        return []
    notices: List[str] = []
    try:
        d = _get(EPMC, {"query": f'REF_DOI:"{doi}" AND PUB_TYPE:"Retraction of Publication"',
                        "format": "json", "pageSize": 5},
                 cache_key=f"epmcretr:{doi}", ttl=86400)
        n = int(d.get("hitCount", 0) or 0)
        if n:
            notices.append(f"Europe PMC lists {n} retraction notice(s) citing this DOI")
    except Exception:
        pass
    return notices


# ---------------------------------------------------------------------------
# bioRxiv / medRxiv - has this preprint since been published?
# ---------------------------------------------------------------------------
def preprint_published_version(doi: str) -> Optional[Dict[str, Any]]:
    if not doi:
        return None
    for server in ("biorxiv", "medrxiv"):
        try:
            d = _get(f"{BIORXIV}/{server}/{doi}", {}, cache_key=f"bx:{server}:{doi}", ttl=86400)
            coll = d.get("collection") or []
            if coll and coll[-1].get("published") not in (None, "NA"):
                return {"server": server, "published_doi": coll[-1]["published"]}
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace(" ", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Ledger - never post the same study twice
# ---------------------------------------------------------------------------
LEDGER = DATA / "ledger.json"


def load_ledger() -> Dict[str, Any]:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"posted": {}, "rejected": {}, "seen": {}}


def save_ledger(led: Dict[str, Any]) -> None:
    LEDGER.write_text(json.dumps(led, indent=2))


def mark_seen(led: Dict[str, Any], s: Study) -> None:
    led.setdefault("seen", {})[s.key] = {
        "doi": s.doi, "title": s.title[:160], "at": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
def load_niches() -> Dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / "niches.yaml").read_text())


def fetch_candidates(niche: str, days: Optional[int] = None,
                     include_preprints: bool = True) -> List[Study]:
    cfg = load_niches()
    defaults = cfg["defaults"]
    n = cfg["niches"][niche]
    days = days or defaults["recency_days"]
    limit = defaults["per_source_limit"]

    studies: List[Study] = []
    # Each source call goes over the network to a service we do not control.
    # A single timeout or 5xx from Europe PMC or arXiv used to crash the whole
    # weekday job with an uncaught exception - "Draft today's post" would go
    # red with nothing to show for it, even though the other source was fine.
    # Isolate them: log a warning and carry on with whatever succeeded.
    if n.get("europepmc_query"):
        try:
            studies += europepmc_search(n["europepmc_query"], days, limit, include_preprints)
        except Exception as e:
            print(f"  ! Europe PMC fetch failed, continuing without it: {e}")
    if n.get("arxiv_categories"):
        try:
            studies += arxiv_search(n["arxiv_categories"], days, limit)
        except Exception as e:
            print(f"  ! arXiv fetch failed, continuing without it: {e}")

    # de-dupe within the batch
    seen, uniq = set(), []
    for s in studies:
        if s.key in seen:
            continue
        seen.add(s.key)
        s.niche = niche
        uniq.append(s)

    # drop anything we have already used or already rejected
    led = load_ledger()
    used = set(led.get("posted", {})) | set(led.get("rejected", {}))
    uniq = [s for s in uniq if s.key not in used]

    # abstract must be substantial enough to summarise honestly
    uniq = [s for s in uniq if len(s.abstract) >= defaults["min_abstract_chars"]]

    # topic exclusions
    ex = [e.lower() for e in n.get("exclude_terms", [])]
    uniq = [s for s in uniq
            if not any(e in (s.title + " " + " ".join(s.pub_types)).lower() for e in ex)]

    uniq.sort(key=lambda s: (s.pub_date or ""), reverse=True)
    return uniq[: defaults["max_candidates"]]


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser(description="Fetch candidate studies for a niche")
    ap.add_argument("niche", choices=["nature", "psych", "health", "physics"])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--no-preprints", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    studies = fetch_candidates(a.niche, a.days, include_preprints=not a.no_preprints)
    if a.json:
        print(json.dumps([s.to_dict() for s in studies], indent=2))
        return
    print(f"{len(studies)} candidates for {a.niche}\n")
    for i, s in enumerate(studies, 1):
        tag = f"[{s.server} PREPRINT]" if s.is_preprint else f"[{s.journal}]"
        print(f"{i:2}. {tag} {s.pub_date}  ({s.age_days}d old)")
        print(f"    {s.title[:120]}")
        print(f"    {s.doi_display}\n")


if __name__ == "__main__":
    _main()
