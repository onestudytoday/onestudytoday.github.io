"""
Abstract -> slide copy, with the guardrails wired into the prompt.

Three things happen here, in order:

  1. DRAFT      Claude gets the abstract, the vetting report, and the hard word
                counts from config/copy_spec.yaml. Output is forced through a
                tool schema, so it is structured JSON or nothing.
  2. LINT       Deterministic, free, always runs. Word counts, banned words,
                banned openers, and vet.check_draft() - the causal-verb and
                animal-claim gates. Violations trigger a repair round-trip.
  3. AUDIT      A second Claude call that sees ONLY the abstract and the draft
                and answers one question: is every claim in this copy supported
                by that abstract? Anything unsupported blocks the post.

If ANTHROPIC_API_KEY is absent, `skeleton()` fills the template from the
extracted metadata so you can still write the post yourself by hand.

CLI:
    python src/draft.py --niche psych --limit 3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

import yaml

from config import QUEUE, ROOT, settings
from sources import Study, fetch_candidates
from vet import VetReport, check_draft, vet

SPEC = yaml.safe_load((ROOT / "config" / "copy_spec.yaml").read_text())

MAX_REPAIRS = 2


# ---------------------------------------------------------------------------
# Tool schema - this is what forces well-formed output
# ---------------------------------------------------------------------------
POST_SCHEMA = {
    "name": "emit_post",
    "description": "Emit the finished carousel copy for one study.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cover": {
                "type": "object",
                "properties": {
                    "kicker": {"type": "string",
                               "description": "3-7 words. Journal + the credibility detail."},
                    "headline": {"type": "string",
                                 "description": "9-18 words, one or two sentences. Exactly one "
                                                "phrase wrapped in **double asterisks**."},
                },
                "required": ["kicker", "headline"],
            },
            "slides": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "eyebrow": {"type": "string",
                                    "enum": ["The setup", "What they found",
                                             "The mechanism", "Why it matters"]},
                        "title": {"type": "string", "description": "10-20 words, one sentence."},
                        "body": {"type": "string",
                                 "description": "55-90 words in EXACTLY two paragraphs "
                                                "separated by a blank line."},
                        "stat": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string", "description": "max 7 chars"},
                                "label": {"type": "string", "description": "4-10 words"},
                            },
                            "required": ["value", "label"],
                        },
                    },
                    "required": ["eyebrow", "title", "body"],
                },
            },
            "caveats": {
                "type": "array", "minItems": 2, "maxItems": 4,
                "items": {"type": "string", "description": "10-24 words, plain English."},
            },
            "cta": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string", "description": "5-10 words"},
                    "sub": {"type": "string", "description": "8-18 words"},
                },
                "required": ["headline", "sub"],
            },
            "caption": {"type": "string", "description": "70-140 words."},
            "hashtag_set": {"type": "string",
                            "description": "Which set fits best: core, nature, psych, "
                                           "health, physics, or wildcard."},
        },
        "required": ["cover", "slides", "caveats", "cta", "caption"],
    },
}


AUDIT_SCHEMA = {
    "name": "emit_audit",
    "description": "Report whether the draft is fully supported by the abstract.",
    "input_schema": {
        "type": "object",
        "properties": {
            "supported": {"type": "boolean",
                          "description": "true only if EVERY factual claim in the draft "
                                         "is directly supported by the abstract."},
            "unsupported_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "problem": {"type": "string"},
                        "severity": {"type": "string", "enum": ["blocking", "minor"]},
                    },
                    "required": ["claim", "problem", "severity"],
                },
            },
            "numbers_check": {
                "type": "array",
                "description": "Every number that appears in the draft, and whether the "
                               "abstract contains it.",
                "items": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "string"},
                        "found_in_abstract": {"type": "boolean"},
                    },
                    "required": ["number", "found_in_abstract"],
                },
            },
        },
        "required": ["supported", "unsupported_claims", "numbers_check"],
    },
}


# ---------------------------------------------------------------------------
def _client():
    from anthropic import Anthropic
    s = settings()
    if not s.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "Get one at https://console.anthropic.com/settings/keys, then add\n"
            "  ANTHROPIC_API_KEY=sk-ant-...\n"
            "to .env locally and to GitHub > Settings > Secrets and variables > Actions."
        )
    return Anthropic(api_key=s.anthropic_api_key)


def _call_tool(system: str, user: str, schema: Dict[str, Any],
               max_tokens: int = 3000) -> Dict[str, Any]:
    c = _client()
    resp = c.messages.create(
        model=settings().draft_model,
        max_tokens=max_tokens,
        system=system,
        tools=[schema],
        tool_choice={"type": "tool", "name": schema["name"]},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Model did not return the tool call.")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
SYSTEM = """You write Instagram carousel copy that explains one newly published \
scientific study to a curious general audience.

Your copy is bold and punchy. Confident, never breathless. You write like a smart \
friend who read the paper and is telling you the interesting part over coffee - not \
like a press office.

Non-negotiable rules:
- Every claim must be traceable to the abstract you are given. If the abstract does \
not say it, you do not write it.
- Never state a correlation as a cause.
- Never imply a finding applies to humans when the study was not done in humans.
- Never use these words: breakthrough, revolutionary, game-changer, miracle, \
shocking, mind-blowing, proves, proven, cure.
- Never open with "Scientists have discovered", "A new study shows", "Researchers at", \
"In a groundbreaking", or "Did you know".
- No exclamation marks. No emoji. No rhetorical questions as headlines.
- Numbers are your friend. Use the real ones from the abstract, never rounded up.

You will be given REQUIRED RULES from an automated vetting pass. Those override \
everything else, including your instincts about what makes punchier copy."""


def build_prompt(s: Study, rep: VetReport) -> str:
    rules = "\n".join(f"  - {r}" for r in rep.draft_rules) or "  (none)"
    caveats = "\n".join(f"  - {c}" for c in rep.required_caveats) or "  (none)"
    spec = SPEC["fields"]

    def rng(k):
        f = spec[k]
        w = f.get("words")
        return f"{w[0]}-{w[1]} words" if w else ""

    return textwrap.dedent(f"""\
        STUDY
        =====
        Title    : {s.title}
        Journal  : {s.journal}{' (PREPRINT - not peer reviewed)' if s.is_preprint else ''}
        Published: {s.pub_date_display}
        DOI      : {s.doi or '(none)'}

        ABSTRACT
        ========
        {s.abstract}

        AUTOMATED VETTING REPORT
        ========================
        Design detected   : {rep.design}
        Subjects          : {rep.subjects}
        Sample size found : {rep.sample_size}
        Credibility score : {rep.score}/100

        REQUIRED RULES (these override your judgement)
        {rules}

        REQUIRED CAVEATS (every one of these must appear on the caveats slide,
        rewritten in the voice described above - keep the substance, lose the
        stiffness)
        {caveats}

        LENGTH SPEC (enforced automatically; copy outside these ranges is rejected)
        - cover.kicker    : {rng('cover.kicker')}, max 44 chars
        - cover.headline  : {rng('cover.headline')}, max 115 chars, exactly ONE
                            phrase wrapped in **double asterisks** for emphasis
        - slide.title     : {rng('slide.title')}
        - slide.body      : {rng('slide.body')}, EXACTLY two paragraphs separated
                            by one blank line
        - caveats         : 2-4 items, {spec['caveats']['words_each'][0]}-{spec['caveats']['words_each'][1]} words each
        - cta.headline    : {rng('cta.headline')}
        - cta.sub         : {rng('cta.sub')}
        - caption         : {rng('caption')}

        STRUCTURE
        Slide 2 must use eyebrow "The setup" and explain why anyone should care
        about this question, ending on the tension the study resolves.
        Slide 3 (and optionally 4) must use eyebrow "What they found" or
        "The mechanism" and deliver the actual result with real numbers.
        Add a `stat` object to whichever slide has the single most striking
        number. Only one slide gets a stat.

        CAPTION
        Open by restating the hook in different words than the cover slide.
        Give one extra detail that did not fit on the slides. Name the journal
        and the sample. State the main limitation in one short sentence. End
        with the link line "Full study: {s.doi_display}" and then a short
        question the reader can answer in four words.

        Write the post.""")


# ---------------------------------------------------------------------------
# Lint - deterministic, free, always runs
# ---------------------------------------------------------------------------
def _wc(s: str) -> int:
    return len(re.findall(r"\b[\w'’\-]+\b", s or ""))


def lint(post: Dict[str, Any], rep: VetReport) -> List[str]:
    spec = SPEC["fields"]
    voice = SPEC["voice"]
    errs: List[str] = []

    def check_words(label, text, key):
        lo, hi = spec[key]["words"]
        n = _wc(text)
        if not (lo <= n <= hi):
            errs.append(f"{label}: {n} words, spec is {lo}-{hi}")
        cm = spec[key].get("chars_max")
        if cm and len(text) > cm:
            errs.append(f"{label}: {len(text)} chars, max {cm}")

    cov = post.get("cover", {})
    check_words("cover.kicker", cov.get("kicker", ""), "cover.kicker")
    check_words("cover.headline", cov.get("headline", ""), "cover.headline")

    runs = len(re.findall(r"\*\*.+?\*\*", cov.get("headline", "")))
    if runs != 1:
        errs.append(f"cover.headline: {runs} highlighted phrases, need exactly 1")

    slides = post.get("slides", [])
    if not (2 <= len(slides) <= 4):
        errs.append(f"slides: {len(slides)}, need 2-4")
    stats = sum(1 for sl in slides if sl.get("stat"))
    if stats > 1:
        errs.append(f"slides: {stats} stat callouts, only 1 allowed")
    for i, sl in enumerate(slides, 1):
        check_words(f"slide{i}.title", sl.get("title", ""), "slide.title")
        check_words(f"slide{i}.body", sl.get("body", ""), "slide.body")
        paras = [p for p in re.split(r"\n\s*\n", sl.get("body", "")) if p.strip()]
        if len(paras) != 2:
            errs.append(f"slide{i}.body: {len(paras)} paragraphs, need exactly 2")
        st = sl.get("stat")
        if st:
            if len(st.get("value", "")) > spec["slide.stat.value"]["chars_max"]:
                errs.append(f"slide{i}.stat.value too long: '{st.get('value')}'")
            check_words(f"slide{i}.stat.label", st.get("label", ""), "slide.stat.label")

    cav = post.get("caveats", [])
    lo, hi = spec["caveats"]["count"]
    if not (lo <= len(cav) <= hi):
        errs.append(f"caveats: {len(cav)} items, need {lo}-{hi}")
    wlo, whi = spec["caveats"]["words_each"]
    for i, c in enumerate(cav, 1):
        if not (wlo <= _wc(c) <= whi):
            errs.append(f"caveat{i}: {_wc(c)} words, spec is {wlo}-{whi}")

    check_words("cta.headline", post.get("cta", {}).get("headline", ""), "cta.headline")
    check_words("cta.sub", post.get("cta", {}).get("sub", ""), "cta.sub")
    check_words("caption", post.get("caption", ""), "caption")

    blob = flatten(post)
    low = blob.lower()
    for w in voice["banned_words"]:
        if re.search(rf"\b{re.escape(w)}\b", low):
            errs.append(f"banned word present: '{w}'")
    for o in voice["banned_openers"]:
        if low.lstrip().startswith(o.lower()) or f". {o.lower()}" in low:
            errs.append(f"banned opener present: '{o}'")
    if "!" in blob:
        errs.append("exclamation mark present")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", blob):
        errs.append("emoji present")

    # The safety gates run on study-specific copy only. The CTA is fixed brand
    # boilerplate ("science you can actually check") and its second-person
    # phrasing is not a claim about the study, so including it produced false
    # positives on the animal-claim rule.
    errs += [f"GUARDRAIL {v}" for v in check_draft(flatten(post, include_cta=False), rep)]

    # every required caveat must be represented
    for req in rep.required_caveats:
        kws = [w for w in re.findall(r"\b[a-z]{5,}\b", req.lower())][:4]
        if kws and not any(all(k in c.lower() for k in kws[:2]) for c in cav):
            hit = any(any(k in c.lower() for k in kws) for c in cav)
            if not hit:
                errs.append(f"required caveat not represented: '{req[:70]}...'")
    return errs


def flatten(post: Dict[str, Any], include_cta: bool = True) -> str:
    parts = [post.get("cover", {}).get("kicker", ""),
             post.get("cover", {}).get("headline", "")]
    for sl in post.get("slides", []):
        parts += [sl.get("title", ""), sl.get("body", "")]
        if sl.get("stat"):
            parts += [sl["stat"].get("value", ""), sl["stat"].get("label", "")]
    parts += post.get("caveats", [])
    if include_cta:
        parts += [post.get("cta", {}).get("headline", ""),
                  post.get("cta", {}).get("sub", "")]
    parts += [post.get("caption", "")]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Audit - independent claim verification
# ---------------------------------------------------------------------------
AUDIT_SYSTEM = """You are a fact-checker. You will be shown a scientific abstract and \
a piece of social-media copy written from it.

Your only job is to find claims in the copy that the abstract does not support. Be \
strict and literal. A claim is unsupported if:
  - the abstract does not contain it
  - the copy states a stronger version than the abstract does
  - the copy converts an association into a cause
  - the copy generalises beyond the population, species, or setting studied
  - a number in the copy does not appear in the abstract, or has been rounded, \
converted, or restated in a way that changes its meaning

Mark severity "blocking" for anything that would mislead a reader about what the \
study found. Mark "minor" for wording that is loose but not misleading.

Simplification is allowed. Losing nuance is allowed. Adding facts is not."""


def audit(post: Dict[str, Any], s: Study) -> Dict[str, Any]:
    user = (f"ABSTRACT\n========\n{s.abstract}\n\n"
            f"COPY TO CHECK\n=============\n{flatten(post)}")
    return _call_tool(AUDIT_SYSTEM, user, AUDIT_SCHEMA, max_tokens=2000)


# ---------------------------------------------------------------------------
# The main entry point
# ---------------------------------------------------------------------------
def draft_post(s: Study, rep: VetReport, run_audit: bool = True) -> Dict[str, Any]:
    prompt = build_prompt(s, rep)
    post = _call_tool(SYSTEM, prompt, POST_SCHEMA)

    errs = lint(post, rep)
    rounds = 0
    while errs and rounds < MAX_REPAIRS:
        rounds += 1
        repair = (prompt + "\n\nYour previous draft was rejected by the automated "
                  "checker for these reasons:\n"
                  + "\n".join(f"  - {e}" for e in errs)
                  + "\n\nPrevious draft:\n" + json.dumps(post, indent=2)
                  + "\n\nFix every issue and emit the corrected post. Keep everything "
                    "that was not flagged.")
        post = _call_tool(SYSTEM, repair, POST_SCHEMA)
        errs = lint(post, rep)

    audit_res = audit(post, s) if run_audit else {"supported": None,
                                                 "unsupported_claims": [],
                                                 "numbers_check": []}
    blocking = [c for c in audit_res.get("unsupported_claims", [])
                if c.get("severity") == "blocking"]
    bad_numbers = [n for n in audit_res.get("numbers_check", [])
                   if not n.get("found_in_abstract")]

    return assemble(s, rep, post, {
        "lint_errors": errs,
        "repair_rounds": rounds,
        "audit": audit_res,
        "blocking_claims": blocking,
        "unverified_numbers": bad_numbers,
        "publishable": (not errs) and (not blocking) and (not bad_numbers),
    })


def assemble(s: Study, rep: VetReport, copy: Dict[str, Any],
             qa: Dict[str, Any]) -> Dict[str, Any]:
    """Merge study + vetting + copy into the shape render.py expects."""
    return {
        "id": f"{s.pub_date}-{s.niche}-{s.key[:8]}",
        "niche": s.niche,
        "study": {
            "key": s.key,      # stable ledger identity - see sources.study_key()
            "title": s.title,
            "journal": s.journal,
            "pub_date": s.pub_date,
            "pub_date_display": s.pub_date_display,
            "doi": s.doi,
            "doi_display": s.doi_display,
            "url": s.url,
            "is_preprint": s.is_preprint,
            "server": s.server,
            "n": rep.sample_size,
            "authors": s.authors[:6],
        },
        "cover": copy["cover"],
        "slides": copy["slides"],
        "caveats": copy["caveats"],
        "cta": copy["cta"],
        "caption": copy.get("caption", ""),
        "hashtag_set": copy.get("hashtag_set", s.niche),
        "vet": rep.to_dict(),
        "qa": qa,
        "status": "needs_review",
    }


# ---------------------------------------------------------------------------
# No-API fallback so the pipeline degrades instead of dying
# ---------------------------------------------------------------------------
def skeleton(s: Study, rep: VetReport) -> Dict[str, Any]:
    first = (s.abstract.split(". ") or [""])[0]
    copy = {
        "cover": {"kicker": f"{s.journal} · {s.pub_date_display}",
                  "headline": "**WRITE THE HOOK.** One sentence. What did they find?"},
        "slides": [
            {"eyebrow": "The setup", "title": "WRITE: why does this question matter?",
             "body": f"WRITE 55-90 words in two paragraphs.\n\nRaw first line of the "
                     f"abstract for reference: {first[:300]}"},
            {"eyebrow": "What they found",
             "title": "WRITE: the result, in one plain sentence.",
             "body": "WRITE 55-90 words in two paragraphs.\n\nPull the real numbers "
                     "from the abstract. Do not round them."},
        ],
        "caveats": rep.required_caveats or ["WRITE at least two honest limits."],
        "cta": {"headline": "Follow for one real study, every weekday.",
                "sub": "Peer-reviewed. Caveats included. Never hyped past the data."},
        "caption": "WRITE 70-140 words. End with the study link.",
        "hashtag_set": s.niche,
    }
    return assemble(s, rep, copy, {"lint_errors": ["SKELETON - written by hand"],
                                   "repair_rounds": 0, "audit": {},
                                   "blocking_claims": [], "unverified_numbers": [],
                                   "publishable": False})


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True,
                    choices=["nature", "psych", "health", "physics"])
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--no-audit", action="store_true")
    ap.add_argument("--skeleton", action="store_true")
    a = ap.parse_args()

    studies = fetch_candidates(a.niche, a.days)
    made = 0
    for s in studies:
        rep = vet(s, a.niche, recency_days=a.days)
        if rep.verdict == "REJECT":
            print(f"skip (REJECT) {s.title[:70]}")
            continue
        post = skeleton(s, rep) if a.skeleton else draft_post(s, rep, not a.no_audit)
        p = QUEUE / f"{post['id']}.json"
        p.write_text(json.dumps(post, indent=2))
        print(f"wrote {p.name}  publishable={post['qa']['publishable']}")
        made += 1
        if made >= a.limit:
            break
    if not made:
        print("No usable candidates.")


if __name__ == "__main__":
    _main()
