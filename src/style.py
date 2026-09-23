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
from typing import Any, Dict, List, Optional

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


def _voice() -> Dict[str, Any]:
    """config/copy_spec.yaml's `voice` block, or {} if it cannot be read.

    Read from the spec rather than hardcoded here, because the drafting prompt
    quotes the same numbers back to the model. Two copies of a threshold is
    one edit away from a checker that enforces something the prompt never
    asked for - which is how this repo ended up with a `fixed_values` list
    that no line of code had read for months.
    """
    global _VOICE_CACHE
    if _VOICE_CACHE is None:
        try:
            import yaml
            from config import ROOT
            spec = yaml.safe_load(
                (ROOT / "config" / "copy_spec.yaml").read_text())
            _VOICE_CACHE = dict((spec or {}).get("voice") or {})
        except Exception:
            _VOICE_CACHE = {}
    return _VOICE_CACHE


_VOICE_CACHE: Optional[Dict[str, Any]] = None

# Defaults, used only if the spec cannot be read. See the calibration note
# above syllables() for where these numbers come from.
MAX_READING_GRADE = 12.0        # a high-school senior
MAX_LONG_WORD_PCT = 7.0

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


# ---------------------------------------------------------------------------
# Reading ease
#
# WHY THIS IS MEASURED IN CODE RATHER THAN ASKED FOR IN THE PROMPT
# ================================================================
# "Write simply" has been in the drafting prompt from the beginning. Measured
# across everything this account has actually published and queued, the copy
# scores a MEDIAN Flesch reading ease of 35.7 - which is roughly a university
# textbook - and one in twelve words has four or more syllables. Asking has
# not worked, because the model is summarising a paper and the paper's own
# vocabulary is right there.
#
# So it is a number now. lint() feeds these flags back into the repair loop the
# same way it feeds back a word count, and the model gets told which words to
# replace rather than told to be simpler.
#
# CALIBRATION, against this repo's own corpus (17 published, 19 queued, 5
# samples): shipped posts run 14-58 ease and 1.4-17.3% long words. The five
# samples rewritten to the current word budgets run 60-76 ease and 1.4-5.9%
# long words - so the thresholds below are set where the good copy already
# sits, not at an aspirational number nothing can reach.
#
# STYLE-prefixed, deliberately. A dense sentence is worth another drafting
# round; it is not worth blocking a scientifically sound post over, and a
# paper about nucleocapsid phosphoprotein signalling has a floor this cannot
# argue with.
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def syllables(word: str) -> int:
    """Rough English syllable count. Good enough to compare against itself."""
    w = re.sub(r"[^a-z]", "", str(word).lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def reading_ease(text: str) -> Optional[float]:
    """Flesch reading ease. Higher is plainer; 60+ is ordinary English."""
    sents = [s for s in _SENT_RE.split(text or "") if s.strip()]
    words = _WORD_RE.findall(text or "")
    if not sents or not words:
        return None
    syl = sum(syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / len(sents)) - 84.6 * (syl / len(words))


def reading_grade(text: str) -> Optional[float]:
    """Flesch-Kincaid grade level: the US school year this reads at.

    Reported instead of reading ease because it names a PERSON. "Grade 12" is
    a high-school senior and anyone can picture one; "reading ease 55" is the
    same measurement upside down and means nothing without a table. When the
    target is "a general audience, not a graduate", the units should be the
    ones the target is expressed in.
    """
    sents = [s for s in _SENT_RE.split(text or "") if s.strip()]
    words = _WORD_RE.findall(text or "")
    if not sents or not words:
        return None
    syl = sum(syllables(w) for w in words)
    return 0.39 * (len(words) / len(sents)) + 11.8 * (syl / len(words)) - 15.59


def long_words(text: str, min_syllables: int = 4) -> List[str]:
    """The words doing the damage, so the repair round can be told which."""
    seen: List[str] = []
    for w in _WORD_RE.findall(text or ""):
        if syllables(w) >= min_syllables and w.lower() not in seen:
            seen.append(w.lower())
    return seen


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
        # The cover headline is no longer the finding.
        #
        # When it was, comparing the CTA against it was a fair proxy for "does
        # this CTA name the study's subject". Now the cover carries the
        # IMPLICATION and the finding sits on slide 1, so a CTA that names the
        # finding precisely - "whoever says weight-loss pills need needles" -
        # could share no word with the cover and be flagged as filler. The
        # repair loop would then rewrite the one CTA on the post that was
        # already doing its job.
        #
        # So the source is the whole of this post's subject matter: the cover,
        # the slide titles, and the paper's own title. It stays a real check
        # because _content_words drops the stopwords a generic CTA is made of -
        # "send this to a friend" still shares nothing with any of it.
        parts = [str((post.get("cover") or {}).get("headline") or "")]
        parts += [str(sl.get("title") or "") for sl in (post.get("slides") or [])
                  if isinstance(sl, dict)]
        if study is not None:
            parts.append(str(getattr(study, "title", "") or ""))
        source = " ".join(parts)
        shared = _content_words(cta_text) & _content_words(source)
        if not shared:
            flags.append(
                "cta names nothing from the study - it would fit any post on "
                "the account, which is what makes it read as filler")

    # 6. Reading ease, and the words costing it.
    #
    #    Two numbers rather than one, because they fail differently: a post
    #    can be dense from long SENTENCES or from long WORDS, and the rewrite
    #    for each is different. Naming the offending words matters more than
    #    the score - "replace transcriptional, supplementation" is actionable
    #    and "be simpler" is not.
    voice = _voice()
    grade_max = float(voice.get("reading_grade_max", MAX_READING_GRADE))
    long_max = float(voice.get("long_word_max_pct", MAX_LONG_WORD_PCT))

    grade = reading_grade(blob)
    if grade is not None and grade > grade_max:
        flags.append(
            f"reads at US grade {grade:.0f}; the target is grade "
            f"{grade_max:.0f}, a high-school senior. Shorter sentences and "
            f"everyday words. This is the single biggest reason a scroller "
            f"passes a science post by")
    words = _WORD_RE.findall(blob)
    if words:
        hard = long_words(blob)
        pct = 100.0 * sum(1 for w in words if syllables(w) >= 4) / len(words)
        if pct > long_max:
            flags.append(
                f"{pct:.0f}% of words are four syllables or more (max "
                f"{long_max:.0f}%) - replace or define: "
                f"{', '.join(hard[:8])}")

    # 7. Sentence rhythm. Calibrated as a regression alarm: the real corpus
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
