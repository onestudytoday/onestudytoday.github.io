"""
The house-style gate: code-level checks that reject copy which reads as
machine-written, and force a rewrite.

WHY THIS IS CODE AND NOT A LINE IN THE PROMPT
=============================================
The prompt already says "write like a smart friend telling you the interesting
part over coffee, not like a press office". The copy still came out reading as
AI. That is the normal outcome: a model told "do not sound like AI" produces
the same register with more adjectives, because the instruction names a vibe
and nothing measures it.

What does work is the mechanism this repo already has for guardrails - a check
in plain code, whose failures are appended to `lint()` and fed straight back
into `draft_post`'s repair loop. The model does not get to grade its own tone;
it gets a concrete list of constructions it used, and it rewrites. Same shape
as `foreign_reference_flags`, for the same reason.

HOW THE THRESHOLDS WERE SET
===========================
By measuring the account's own 12 published posts, not by taste. That mattered:
a sentence-length-uniformity check was going to ship with a threshold that,
measured against the real corpus (stdev 4.46-6.81), would have flagged nothing
ever. It is still here, but recalibrated as a regression alarm rather than a
nitpick, and documented as such.

Measured hit rates on the 12 published posts at the time of writing:

    em-dash appositives > 2      4/12   bimodal: posts have 0 or 4
    CTA names nothing concrete   4/12   "Science that feeds your curiosity"
    filler phrases               5/12   "swipe through", "follow for", ...
    knowledge-gap cliche > 1     1/12   genuine framing, so only overuse flags
    uniform sentence length      0/12   forward guard only

STYLE IS NOT A GUARDRAIL
========================
These are reported as `STYLE ...`, never `GUARDRAIL ...`, and they are excluded
from the `publishable` calculation in draft.py. A guardrail means "this post
might tell someone a mouse result applies to them". A style flag means "this
sentence is boring". Putting them in the same bucket would train the reviewer
to wave through the bucket, which is exactly how the one that matters gets
missed. Style flags drive the rewrite loop and appear on the review card; they
never block a scientifically sound post.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Phrases that mark copy as generic. Ones that currently appear in the account's
# own posts are marked with their hit count; the rest are forward guards for
# constructions that have not shown up yet but are the usual next arrivals.
# ---------------------------------------------------------------------------
FILLER_PHRASES = [
    "swipe through",            # 2/12
    "follow for",               # 3/12
    "the latest research",      # 1/12
    "the science behind",       # 1/12
    "explained clearly",        # 1/12
    "feeds your curiosity",     # 1/12
    # forward guards
    "dive into", "deep dive", "here's the thing", "it's worth noting",
    "the bottom line", "in a world where", "what if i told you",
    "at the end of the day", "plays a crucial role", "plays a key role",
    "sheds light on", "paves the way", "opens the door",
    "a growing body of", "stay tuned", "let that sink in",
    "the results may surprise you", "you won't believe",
]

# "not just X, but Y" and its relatives. A single one is a normal English
# sentence; it is the reflex that reads as machine-written.
_NOT_JUST = re.compile(
    r"\bnot (?:just|only|merely)\b[^.!?]{0,80}?\bbut\b", re.I)

# The knowledge-gap opener. This is legitimate scientific framing - most papers
# genuinely do open on a gap - so it is capped, not banned.
_KNOWLEDGE_GAP = re.compile(
    r"remains? (?:poorly )?(?:understood|unclear|unknown|elusive)"
    r"|little is known|almost nothing is known|barely know"
    r"|poorly understood|remain(?:s)? a mystery", re.I)

_EM_DASH = "—"

# Thresholds, all calibrated against the 12-post corpus. See module docstring.
MAX_EM_DASHES = 2
MAX_KNOWLEDGE_GAP = 1
MIN_SENTENCES_FOR_RHYTHM_CHECK = 10
# Median absolute deviation of sentence length, NOT standard deviation.
#
# Stdev was tried first and is the wrong statistic here, because it is
# dominated by a couple of outliers: a body of thirteen identical five-word
# sentences still scored 3.57 once the long cover headline and a short caveat
# were mixed in, which sailed past a 3.5 threshold calibrated from real posts.
# MAD ignores the outliers and measures what the check is actually about -
# whether most sentences are the same length.
#
# Measured across the 12 published posts and 5 samples: MAD 2.0-6.0. The
# metronomic fixture scores 0.0. 1.5 sits in the gap with margin on both sides.
MIN_SENTENCE_LENGTH_MAD = 1.5
_CONTENT_WORD = re.compile(r"[a-z]{5,}")

# Words that are shared between a CTA and a headline without the CTA actually
# naming anything - they would satisfy the overlap check while saying nothing.
_CTA_STOPWORDS = {
    "study", "studies", "science", "scientific", "research", "researchers",
    "found", "finding", "findings", "results", "shows", "showed", "reveals",
    "revealed", "suggests", "could", "might", "these", "their", "there",
    "about", "which", "where", "while", "after", "before", "through",
    "people", "human", "humans", "paper", "papers", "curious", "curiosity",
    "latest", "explained", "clearly", "follow", "swipe",
}


def _texts(post: Dict[str, Any]) -> List[str]:
    """Every piece of study-specific copy in the post."""
    out: List[str] = []
    cover = post.get("cover") or {}
    out += [str(cover.get("kicker") or ""), str(cover.get("headline") or "")]
    for s in post.get("slides") or []:
        if not isinstance(s, dict):
            continue
        out += [str(s.get("title") or ""), str(s.get("body") or "")]
    out += [str(c) for c in (post.get("caveats") or [])]
    cta = post.get("cta") or {}
    out += [str(cta.get("headline") or ""), str(cta.get("sub") or "")]
    # The caption is the most-read text on the post after the cover slide, and
    # it is written by the same model in the same breath as the slides, so it
    # picks up the same habits. Excluding it would have left the single
    # longest piece of copy unchecked.
    out.append(str(post.get("caption") or ""))
    return [t for t in out if t.strip()]


def _blob(post: Dict[str, Any]) -> str:
    return "\n".join(_texts(post))


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 3]


def _stem(word: str) -> str:
    """Crudest possible stemmer: enough to see that "fossils" and "fossil" are
    the same word.

    Without it the CTA check compared surface forms and reported that
    "Send this to whoever thinks fossils need fieldwork" named nothing from a
    cover headline that opens "A fossil sat lost in a drawer" - a false
    positive that would have sent a perfectly specific CTA back for a pointless
    rewrite, and burned a repair round doing it.
    """
    for suffix in ("ies", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _content_words(text: str) -> set:
    return {_stem(w) for w in _CONTENT_WORD.findall(text.lower())
            if w not in _CTA_STOPWORDS}


# ---------------------------------------------------------------------------
def style_flags(post: Dict[str, Any], study: Any = None) -> List[str]:
    """Constructions that make this post read as machine-written.

    Never raises: a malformed post is the lint layer's problem, not this one's,
    and a crash here would take down drafting over a tone check.
    """
    try:
        return _style_flags(post, study)
    except Exception as e:                                # pragma: no cover
        return [f"style check could not run: {type(e).__name__}: {e}"]


def _style_flags(post: Dict[str, Any], study: Any = None) -> List[str]:
    flags: List[str] = []
    blob = _blob(post)
    low = blob.lower()

    # 1. Filler phrases.
    for phrase in FILLER_PHRASES:
        if phrase in low:
            flags.append(
                f"filler phrase '{phrase}' - say the specific thing instead")

    # 2. "not just X, but Y".
    hits = _NOT_JUST.findall(blob)
    if hits:
        flags.append(
            f"{len(hits)}x 'not just X but Y' construction - state the claim "
            f"directly")

    # 3. Em-dash appositives.
    n_dash = blob.count(_EM_DASH)
    if n_dash > MAX_EM_DASHES:
        flags.append(
            f"{n_dash} em-dashes (max {MAX_EM_DASHES}) - the "
            f"term—definition—continues habit is the single most "
            f"recognisable tell; define terms in their own sentence instead")

    # 4. Knowledge-gap framing, capped rather than banned.
    n_gap = len(_KNOWLEDGE_GAP.findall(blob))
    if n_gap > MAX_KNOWLEDGE_GAP:
        flags.append(
            f"{n_gap}x 'remains poorly understood'-style framing (max "
            f"{MAX_KNOWLEDGE_GAP}) - open on what they DID, not on the gap")

    # 5. The CTA has to name the actual subject.
    cta = post.get("cta") or {}
    cta_text = f"{cta.get('headline') or ''} {cta.get('sub') or ''}"
    if cta_text.strip():
        source = str((post.get("cover") or {}).get("headline") or "")
        if study is not None:
            source += " " + str(getattr(study, "title", "") or "")
        shared = _content_words(cta_text) & _content_words(source)
        if not shared:
            flags.append(
                "cta names nothing from the study - it would fit any post on "
                "the account, which is what makes it read as filler")

    # 6. Sentence rhythm. Calibrated as a regression alarm: the real corpus
    #    sits at 4.46-6.81, so this fires only if the copy becomes markedly
    #    more metronomic than anything published so far.
    lengths = [len(s.split()) for s in _sentences(blob)]
    if len(lengths) >= MIN_SENTENCES_FOR_RHYTHM_CHECK:
        mid = statistics.median(lengths)
        mad = statistics.median([abs(n - mid) for n in lengths])
        if mad < MIN_SENTENCE_LENGTH_MAD:
            flags.append(
                f"most sentences are the same length (median deviation "
                f"{mad:.1f}, want >{MIN_SENTENCE_LENGTH_MAD}) - vary the "
                f"rhythm; one short sentence after two long ones does most "
                f"of it")

    return flags
