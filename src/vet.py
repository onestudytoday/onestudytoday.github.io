"""
The vetting engine. This is the part that keeps the account honest.

These are not suggestions in a checklist a human might skip. They are gates in
the pipeline. A study that trips a HARD rule never reaches the drafting step,
and required caveats are injected into the drafting prompt AND rendered onto
the fine-print slide - a post physically cannot ship without them.

Three verdicts:
    REJECT  killed automatically, logged, never shown
    HOLD    shown to you with a written warning, needs an explicit override
    PASS    proceeds to drafting (still needs your approval before publishing)

Run against a niche to see the report cards:
    python src/vet.py psych
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import yaml

from config import ROOT
from sources import (Study, crossref_retraction_check, enrich_from_crossref,
                     fetch_candidates, load_niches, preprint_published_version)

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------
CAUSAL_VERBS = [
    "causes", "caused", "causing", "leads to", "led to", "results in",
    "resulted in", "triggers", "triggered", "makes you", "prevents",
    "prevented", "cures", "cured", "protects against", "reduces your risk",
    "boosts", "improves your", "drives", "induces", "produces",
]

OBSERVATIONAL_MARKERS = [
    "observational", "cross-sectional", "cohort", "case-control", "survey",
    "questionnaire", "self-report", "self-reported", "correlational",
    "association", "associated with", "linked to", "prospective cohort",
    "retrospective", "registry", "biobank", "mendelian randomization",
    "mendelian randomisation", "ecological study",
]

CAUSAL_DESIGN_MARKERS = [
    "randomized controlled trial", "randomised controlled trial",
    "randomly assigned", "randomised to", "randomized to", "double-blind",
    "placebo-controlled", "crossover trial", "experimental manipulation",
    "we manipulated", "intervention group", "knockout", "optogenetic",
    "lesion", "causal inference from randomization",
]

NON_HUMAN_MARKERS = {
    "mice": "mice", "mouse": "mice", "murine": "mice", "rats": "rats",
    "rat ": "rats", "zebrafish": "zebrafish", "drosophila": "fruit flies",
    "c. elegans": "nematode worms", "macaque": "macaques",
    "non-human primate": "non-human primates", "porcine": "pigs",
    "in vitro": "cells in a dish", "cell line": "cell lines",
    "organoid": "lab-grown organoids", "cultured cells": "cultured cells",
    "xenograft": "xenografts", "in silico": "computer simulation",
    "canine": "dogs", "bovine": "cattle",
}

HYPE_WORDS = [
    "breakthrough", "revolutionary", "miracle", "game-changer", "game changer",
    "proves", "proven", "definitive proof", "cure for", "the cure", "unprecedented",
    "paradigm shift", "holy grail", "silver bullet", "first ever",
]

RELATIVE_ONLY = ["relative risk", "odds ratio", "hazard ratio", "% reduction",
                 "percent reduction", "fold increase", "times more likely"]

ABSOLUTE_MARKERS = ["absolute risk", "number needed to treat", "per 1,000",
                    "per 100,000", "percentage point", "absolute difference"]

INDUSTRY_FUNDER_HINTS = [
    "pharmaceutical", "pharma", "inc.", "llc", "ltd", "gmbh", "corporation",
    "novartis", "pfizer", "merck", "astrazeneca", "gsk", "glaxosmithkline",
    "sanofi", "roche", "bayer", "abbvie", "eli lilly", "boehringer",
    "nestle", "nestlé", "coca-cola", "pepsico", "unilever", "danone",
    "philip morris", "juul", "meta platforms", "google llc",
]

MECHANISTIC_ONLY = ["mechanism", "pathway", "in vitro", "transcriptomic",
                    "proteomic", "signalling", "signaling"]


# ---------------------------------------------------------------------------
@dataclass
class Flag:
    code: str
    severity: str          # hard | warn | note
    message: str
    caveat: Optional[str] = None       # forced onto the fine-print slide
    draft_rule: Optional[str] = None   # injected into the drafting prompt


@dataclass
class VetReport:
    key: str
    verdict: str = "PASS"          # PASS | HOLD | REJECT
    score: int = 0                 # 0-100 credibility
    flags: List[Flag] = field(default_factory=list)
    design: str = "unclear"
    sample_size: Optional[int] = None
    subjects: str = "unclear"      # human | non-human | mixed | unclear
    journal_tier: Optional[int] = None
    required_caveats: List[str] = field(default_factory=list)
    required_badges: List[str] = field(default_factory=list)
    draft_rules: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add(self, f: Flag) -> None:
        self.flags.append(f)
        if f.caveat and f.caveat not in self.required_caveats:
            self.required_caveats.append(f.caveat)
        if f.draft_rule and f.draft_rule not in self.draft_rules:
            self.draft_rules.append(f.draft_rule)
        if f.severity == "hard":
            self.verdict = "REJECT"
        elif f.severity == "warn" and self.verdict != "REJECT":
            self.verdict = "HOLD"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["flags"] = [asdict(f) for f in self.flags]
        return d

    def human(self) -> str:
        icons = {"hard": "REJECT", "warn": "  WARN", "note": "  note"}
        lines = [f"  verdict {self.verdict}   score {self.score}/100   "
                 f"design={self.design}  n={self.sample_size}  subjects={self.subjects}"]
        for f in self.flags:
            lines.append(f"    [{icons[f.severity]}] {f.code}: {f.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
def _txt(s: Study) -> str:
    return f"{s.title}. {s.abstract}".lower()


def detect_design(s: Study) -> str:
    t = _txt(s)
    pt = " ".join(s.pub_types).lower()
    if "meta-analysis" in t or "meta-analysis" in pt or "systematic review" in pt:
        return "meta-analysis"
    if any(m in t for m in ("randomized controlled trial", "randomised controlled trial",
                            "randomly assigned", "placebo-controlled", "double-blind")) \
       or "randomized controlled trial" in pt:
        return "randomised trial"
    if any(m in t for m in ("we manipulated", "participants were assigned",
                            "experimental condition", "optogenetic", "knockout",
                            "we induced", "we stimulated")):
        return "experiment"
    if "mendelian randomization" in t or "mendelian randomisation" in t:
        return "mendelian randomisation"
    if any(m in t for m in ("prospective cohort", "cohort study", "biobank", "registry")):
        return "cohort (observational)"
    if "case-control" in t:
        return "case-control (observational)"
    if any(m in t for m in ("cross-sectional", "survey", "questionnaire")):
        return "cross-sectional (observational)"
    if any(m in t for m in ("modelling study", "modeling study", "simulation", "in silico")):
        return "modelling"
    if any(m in t for m in MECHANISTIC_ONLY):
        return "mechanistic / lab"
    return "unclear"


def detect_subjects(s: Study) -> Tuple[str, List[str]]:
    t = _txt(s)
    found = sorted({label for k, label in NON_HUMAN_MARKERS.items() if k in t})
    human = any(w in t for w in ("participants", "patients", "volunteers", "adults",
                                 "children", "human subjects", "men and women",
                                 "in humans", "individuals aged"))
    if found and human:
        return "mixed", found
    if found:
        return "non-human", found
    if human:
        return "human", []
    return "unclear", []


N_PATTERNS = [
    r"\bn\s*=\s*([\d][\d,\s]{0,10}\d|\d)",
    r"\b([\d][\d,]{2,})\s+(?:participants|patients|adults|children|individuals|subjects|volunteers|respondents|people)\b",
    r"\b(?:recruited|enrolled|included|analys\w+|studied|followed)\s+([\d][\d,]{1,})\b",
]


def detect_sample_size(s: Study) -> Optional[int]:
    t = f"{s.title} {s.abstract}"
    best = None
    for pat in N_PATTERNS:
        for m in re.finditer(pat, t, re.I):
            raw = re.sub(r"[,\s]", "", m.group(1))
            if not raw.isdigit():
                continue
            v = int(raw)
            if 1 <= v <= 100_000_000:
                best = v if best is None else max(best, v)
    return best


def journal_tier(s: Study, niche_cfg: Dict[str, Any]) -> Optional[int]:
    j = (s.journal or "").lower().strip()
    if not j:
        return None
    for tier, names in (niche_cfg.get("journals") or {}).items():
        for n in names:
            nl = n.lower()
            if j == nl or nl in j or j in nl:
                return int(tier)
    return None


# ---------------------------------------------------------------------------
def vet(s: Study, niche: Optional[str] = None, allow_preprints: bool = True,
        recency_days: int = 14, deep: bool = True) -> VetReport:
    """Run every gate. `deep=True` makes the extra Crossref/retraction calls."""
    cfg = load_niches()
    niche = niche or s.niche or "nature"
    ncfg = cfg["niches"].get(niche, {})
    rep = VetReport(key=s.key)
    score = 50

    if deep:
        s = enrich_from_crossref(s)
        s.retraction_notices += crossref_retraction_check(s.doi)

    text = _txt(s)

    # ---------------------------------------------------------------- HARD
    if s.retraction_notices:
        rep.add(Flag("RETRACTED", "hard",
                     "Retraction / withdrawal / expression-of-concern found: "
                     + "; ".join(sorted(set(s.retraction_notices)))))

    pub_blob = f"{s.publisher} {s.journal}".lower()
    for bad in cfg.get("blocklist_publishers", []):
        if bad.lower() in pub_blob:
            rep.add(Flag("PREDATORY_PUBLISHER", "hard",
                         f"Publisher matches the blocklist: {bad}"))

    if not s.doi and not s.url:
        rep.add(Flag("NO_STABLE_LINK", "hard",
                     "No DOI and no stable URL - readers could not verify it."))

    if s.age_days is not None and s.age_days > recency_days:
        rep.add(Flag("STALE", "hard",
                     f"Published {s.age_days} days ago, window is {recency_days}."))

    if len(s.abstract) < 400:
        rep.add(Flag("THIN_ABSTRACT", "hard",
                     f"Abstract is only {len(s.abstract)} chars - not enough to "
                     f"summarise without inventing detail."))

    # ------------------------------------------------------------ PREPRINT
    if s.is_preprint:
        pub = preprint_published_version(s.doi) if deep else None
        if pub:
            rep.notes.append(
                f"This preprint has since been published: {pub['published_doi']}. "
                f"Use the published version instead.")
            score += 5
        sev = "warn" if allow_preprints else "hard"
        rep.add(Flag(
            "PREPRINT", sev,
            f"Not peer reviewed ({s.server or 'preprint'}).",
            caveat=("This is a preprint. It has not been peer reviewed, so no "
                    "independent expert has checked the work yet."),
            draft_rule=("This is a PREPRINT. The copy must not state findings as "
                        "settled fact. Use hedged framing such as 'the team reports' "
                        "or 'in this early paper'."),
        ))
        rep.required_badges.append("PREPRINT")
        score -= 12

    # -------------------------------------------------------------- DESIGN
    rep.design = detect_design(s)
    is_observational = "observational" in rep.design or rep.design in (
        "cross-sectional (observational)", "mendelian randomisation")

    causal_lang = [v for v in CAUSAL_VERBS if v in text]
    obs_markers = [m for m in OBSERVATIONAL_MARKERS if m in text]
    causal_design = [m for m in CAUSAL_DESIGN_MARKERS if m in text]

    if is_observational or (obs_markers and not causal_design):
        rep.add(Flag(
            "CORRELATIONAL", "warn" if is_observational else "note",
            f"Design reads as observational ({rep.design}); "
            f"markers: {', '.join(obs_markers[:4]) or 'n/a'}",
            caveat=("This study found a pattern, not a cause. Something else could "
                    "explain the link."),
            draft_rule=("This study is OBSERVATIONAL. Causal verbs are banned in every "
                        "slide: no 'causes', 'leads to', 'prevents', 'boosts', 'makes'. "
                        "Use 'is linked to', 'goes together with', 'people who X also Y'."),
        ))
        score -= 6
        if causal_lang:
            rep.add(Flag(
                "CAUSAL_LANGUAGE_IN_OBSERVATIONAL", "warn",
                "The paper's own text uses causal verbs "
                f"({', '.join(causal_lang[:4])}) despite an observational design. "
                "The authors are overreaching - do not repeat it."))
            score -= 8
    elif rep.design in ("randomised trial", "experiment", "meta-analysis"):
        score += 12

    # ------------------------------------------------------------ SUBJECTS
    rep.subjects, species = detect_subjects(s)
    if rep.subjects == "non-human":
        pretty = " and ".join(species) or "non-human subjects"
        rep.add(Flag(
            "NON_HUMAN", "warn",
            f"Study subjects are {pretty}, not people.",
            caveat=f"This was done in {pretty} — not humans. Most findings in "
                   f"{pretty} do not carry over to people.",
            draft_rule=(f"Subjects were {pretty}, NOT humans. The cover slide must not "
                        f"imply a human result. Name the species explicitly by slide 2."),
        ))
        rep.required_badges.append("NOT_IN_HUMANS")
        score -= 4
    elif rep.subjects == "human":
        score += 6

    # --------------------------------------------------------- SAMPLE SIZE
    rep.sample_size = detect_sample_size(s)
    n = rep.sample_size
    if rep.subjects in ("human", "mixed") and n is not None:
        if n < 30:
            rep.add(Flag(
                "TINY_SAMPLE", "warn",
                f"Only {n} human participants detected.",
                caveat=f"Only {n} people took part. That is small enough that the "
                       f"result could shift a lot in a bigger study.",
                draft_rule=f"Sample size is only {n}. State the number on the slide "
                           f"and do not generalise to 'people' broadly."))
            score -= 10
        elif n < 100:
            rep.add(Flag("SMALL_SAMPLE", "note", f"{n} participants - modest.",
                         caveat=f"Sample size was {n} — worth replicating before anyone "
                                f"changes what they do."))
            score -= 2
        elif n >= 10000:
            score += 8
        elif n >= 1000:
            score += 4

    # ------------------------------------------------------- JOURNAL / SRC
    rep.journal_tier = journal_tier(s, ncfg)
    if rep.journal_tier == 1:
        score += 18
    elif rep.journal_tier == 2:
        score += 12
    elif rep.journal_tier == 3:
        score += 6
    elif not s.is_preprint:
        rep.add(Flag("UNLISTED_JOURNAL", "note",
                     f"'{s.journal}' is not on the niche whitelist - not "
                     f"disqualifying, just unverified by us."))

    for w in cfg.get("watchlist_journals", []):
        if w.lower() in (s.journal or "").lower():
            rep.add(Flag("WATCHLIST_JOURNAL", "warn",
                         f"'{s.journal}' is on the low-rigour watchlist.",
                         draft_rule="Be extra conservative; this venue has a mixed record."))
            score -= 10

    # ------------------------------------------------------------- FUNDING
    fund_blob = " ".join(s.funders).lower()
    hits = [h for h in INDUSTRY_FUNDER_HINTS if h in fund_blob]
    if hits:
        rep.add(Flag(
            "INDUSTRY_FUNDING", "warn",
            f"Funder list contains commercial entities: {', '.join(sorted(set(hits))[:3])}",
            caveat="This study was funded in part by industry, which is worth knowing "
                   "when you read the result."))
        score -= 6

    # ---------------------------------------------------------------- HYPE
    hype = [h for h in HYPE_WORDS if h in text]
    if hype:
        rep.add(Flag("HYPE_LANGUAGE", "note",
                     f"Paper text contains hype words: {', '.join(hype[:4])}",
                     draft_rule="Do not reuse the paper's promotional wording."))

    # ------------------------------------------------- RELATIVE VS ABSOLUTE
    rel = [r for r in RELATIVE_ONLY if r in text]
    absol = [a for a in ABSOLUTE_MARKERS if a in text]
    if rel and not absol:
        rep.add(Flag(
            "RELATIVE_RISK_ONLY", "warn",
            f"Reports relative effects ({', '.join(rel[:2])}) with no absolute numbers.",
            caveat="The paper reports a relative change. Without the baseline rate, a "
                   "big-sounding percentage can be a very small real-world difference.",
            draft_rule="A relative effect with no absolute baseline must never be stated "
                       "as a bare percentage on the cover slide."))
        score -= 5

    # --------------------------------------------------------------- BONUS
    if s.open_access:
        score += 4
        rep.notes.append("Open access - readers can actually read the full paper.")
    if s.citations > 5:
        score += 2

    rep.score = max(0, min(100, score))

    if rep.verdict == "PASS" and rep.score < 45:
        rep.verdict = "HOLD"
        rep.add(Flag("LOW_SCORE", "note",
                     f"Credibility score {rep.score} is below the auto-pass line of 45."))

    return rep


# ---------------------------------------------------------------------------
# Post-draft verification: does the written copy overclaim vs the abstract?
# ---------------------------------------------------------------------------
CLAIM_CHECK_PATTERNS = {
    "causal_verb_on_observational": (
        lambda draft, rep: rep.design.endswith("(observational)")
        and any(v in draft.lower() for v in CAUSAL_VERBS),
        "Draft uses a causal verb but the study design is observational."),
    "human_claim_on_animal": (
        lambda draft, rep: rep.subjects == "non-human"
        and any(w in draft.lower() for w in
                (" you ", "your ", "people ", "humans ", "we can now"))
        and not any(w in draft.lower() for w in
                    ("mice", "rats", "zebrafish", "cells", "worms", "flies",
                     "macaques", "pigs", "dogs", "organoid")),
        "Draft speaks to humans but the study was not done in humans."),
    "hype": (
        lambda draft, rep: any(h in draft.lower() for h in HYPE_WORDS),
        "Draft contains promotional hype wording."),
    "cure_claim": (
        lambda draft, rep: re.search(r"\bcure[sd]?\b", draft.lower()) is not None,
        "Draft claims a cure."),
    "proof_claim": (
        lambda draft, rep: re.search(r"\bprov(e|es|en|ed)\b", draft.lower()) is not None,
        "Draft claims something is proven."),
}


def check_draft(draft_text: str, rep: VetReport) -> List[str]:
    """Returns a list of violations. Empty list means the draft is clean."""
    out = []
    for code, (fn, msg) in CLAIM_CHECK_PATTERNS.items():
        try:
            if fn(draft_text, rep):
                out.append(f"{code}: {msg}")
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser(description="Vet candidate studies for a niche")
    ap.add_argument("niche", choices=["nature", "psych", "health", "physics"])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--no-deep", action="store_true",
                    help="skip Crossref/retraction lookups (faster, less safe)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    studies = fetch_candidates(a.niche, a.days)
    rows = []
    for s in studies:
        r = vet(s, a.niche, recency_days=a.days, deep=not a.no_deep)
        rows.append((s, r))

    if a.json:
        print(json.dumps([{"study": s.to_dict(), "vet": r.to_dict()}
                          for s, r in rows], indent=2))
        return

    order = {"PASS": 0, "HOLD": 1, "REJECT": 2}
    rows.sort(key=lambda x: (order[x[1].verdict], -x[1].score))
    for s, r in rows:
        print(f"\n{s.title[:110]}")
        print(f"  {s.journal} · {s.pub_date} · {s.doi_display}")
        print(r.human())
    counts = {}
    for _, r in rows:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    print(f"\n{'-'*70}\n{counts}")


if __name__ == "__main__":
    _main()
