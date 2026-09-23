"""
The post as a plain document you can edit, and read back.

WHY THIS EXISTS
===============
Reviewing happens on a phone, and until now the only way to change a word was
to describe the change to a model - `revise: tighten slide 3` - and hope it
did what you meant. That is a good tool for "make this better" and a terrible
one for "the second sentence should say four hours, not five".

So every drafted post also gets a Markdown file next to it. Open it in
GitHub's web editor, fix the sentence, commit. A workflow reads it back into
the post, re-renders the slides and updates the review card. No model call, no
interpretation - what you typed is what gets rendered.

WHAT IS AND IS NOT EDITABLE
===========================
The document carries the COPY: kicker, headline, each slide, the caveats, the
CTA and the caption hook. It does not carry the study, the vetting report, the
id or the QA results, because none of those are yours to rewrite - editing a
DOI or a verdict in a text file and having the pipeline believe it is exactly
the hole this repo spends most of its code closing.

Unknown headings are ignored rather than rejected. A document that has been
reformatted, had a note added at the bottom, or been partially mangled by a
mobile editor should still apply the parts that are recognisable.

YOUR EDITS ARE STILL CHECKED
============================
`apply_markdown()` returns the post; it does NOT decide it is publishable.
The caller re-runs lint() and the invented-number check and records the
result, so deleting "only 40 people took part" from the caveats leaves the
post blocked with that reason on the card rather than quietly publishing.

That is not distrust of your typing. It is that the forced caveats are
promises the README makes on the account's behalf, and a promise that can be
removed by accident in a text editor on a train is not a promise.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

HEADER = """<!-- Edit the text below, commit, and the post updates itself.

     Keep the headings. Anything you add under an unknown heading is ignored.
     The study, the DOI and the vetting report are NOT here on purpose - they
     are not text to rewrite.

     **double asterisks** mark the accent-coloured words on a slide.
-->
"""

_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)
_FIELD = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")


def to_markdown(post: Dict[str, Any]) -> str:
    """Render the editable copy of a post as a document."""
    out: List[str] = [HEADER, f"# {post.get('id', 'post')}", ""]

    cover = post.get("cover") or {}
    out += ["## Cover", "",
            f"**Kicker:** {cover.get('kicker', '')}",
            f"**Headline:** {cover.get('headline', '')}", ""]

    for i, sl in enumerate(post.get("slides") or [], start=1):
        if not isinstance(sl, dict):
            continue
        out += [f"## Slide {i} - {sl.get('eyebrow', '')}", ""]
        out += [f"**Title:** {sl.get('title', '')}", ""]
        # `basis` only exists on the implications slide. Shown so you can see
        # which register the slide is in, and change it if the model got it
        # wrong - marking your own extrapolation as the paper's claim is the
        # one edit here that would actually mislead a reader.
        if sl.get("basis"):
            out += [f"**Basis:** {sl['basis']}    "
                    f"<!-- stated = the paper says it; "
                    f"inferred = our read, must stay conditional -->", ""]
        out += [str(sl.get("body", "")), ""]

    out += ["## Caveats", ""]
    for c in post.get("caveats") or []:
        out.append(f"- {c}")
    out.append("")

    # The clipping, if this post found one.
    #
    # NOTHING here is editable. The slide is a photograph of the article's own
    # headline on the outlet's own page, so there is no text of ours on it to
    # change - and rewriting the quoted headline would mean a slide that puts
    # words in a named masthead's mouth. The one choice is whether to show it.
    #
    # What is printed here is the reviewer's checklist: what the article says,
    # who published it, when, and the address to go and confirm it.
    clip = post.get("clipping")
    if isinstance(clip, dict) and clip.get("headline"):
        out += ["## Clipping", "",
                "<!-- This slide is a SCREENSHOT of the article's own "
                "headline, taken off the outlet's page. Nothing here is "
                "editable; the only choice is whether to show it. Open the "
                "address below and check the article really does say what "
                "the slide implies it says. -->", "",
                f"> {clip.get('headline', '')}", "",
                f"> — {clip.get('outlet', '')}, {clip.get('date', '')}", "",
                f"> {clip.get('url', '')}", "",
                f"**Exclude:** {'yes' if clip.get('excluded') else 'no'}", ""]

    cta = post.get("cta") or {}
    out += ["## CTA", "",
            f"**Headline:** {cta.get('headline', '')}",
            f"**Sub:** {cta.get('sub', '')}", ""]

    out += ["## Caption", "",
            "<!-- One line. The link and hashtags are added automatically -"
            " do not type them here. -->", "",
            str(post.get("caption", "")), ""]
    return "\n".join(out).rstrip() + "\n"


def _sections(md: str) -> Dict[str, str]:
    """Split a document into {heading: body}. Tolerant by design."""
    parts: Dict[str, str] = {}
    marks = list(_SECTION.finditer(md))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md)
        parts[m.group(1).strip()] = md[m.end():end]
    return parts


def _strip_comments(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def _fields(block: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in _strip_comments(block).splitlines():
        m = _FIELD.match(line.strip())
        if m:
            out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def _prose(block: str) -> str:
    """Everything that is not a **Field:** line, as paragraphs."""
    lines = [ln for ln in _strip_comments(block).splitlines()
             if not _FIELD.match(ln.strip())]
    text = "\n".join(lines).strip()
    # Collapse 3+ blank lines to the two the renderer expects between
    # paragraphs; a mobile editor adds them freely.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def apply_markdown(post: Dict[str, Any], md: str) -> Dict[str, Any]:
    """Read an edited document back into a post. Returns a NEW post.

    Never raises on malformed input: an unrecognised or empty section leaves
    that part of the post exactly as it was. A document a phone editor has
    mangled should degrade to "some of your edits applied", not to an error
    and no post.
    """
    out = dict(post)
    secs = _sections(md or "")

    cover = dict(out.get("cover") or {})
    f = _fields(secs.get("Cover", ""))
    if f.get("kicker"):
        cover["kicker"] = f["kicker"]
    if f.get("headline"):
        cover["headline"] = f["headline"]
    out["cover"] = cover

    slides = [dict(s) for s in (out.get("slides") or []) if isinstance(s, dict)]
    for name, block in secs.items():
        m = re.match(r"Slide\s+(\d+)\s*[-–—]?\s*(.*)$", name.strip(), re.I)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if not (0 <= idx < len(slides)):
            continue
        # The eyebrow comes from the HEADING, so renaming the heading renames
        # the slide - but only to a label the spec allows. lint() rejects
        # anything else as a GUARDRAIL error, so a typo here is caught rather
        # than rendered.
        eyebrow = m.group(2).strip()
        if eyebrow:
            slides[idx]["eyebrow"] = eyebrow
        fl = _fields(block)
        if fl.get("title"):
            slides[idx]["title"] = fl["title"]
        if fl.get("basis") in ("stated", "inferred"):
            slides[idx]["basis"] = fl["basis"]
        body = _prose(block)
        if body:
            slides[idx]["body"] = body
    if slides:
        out["slides"] = slides

    cav_block = _strip_comments(secs.get("Caveats", ""))
    caveats = [re.sub(r"^[-*]\s+", "", ln).strip()
               for ln in cav_block.splitlines() if ln.strip().startswith(("-", "*"))]
    if caveats:
        out["caveats"] = caveats

    # The clipping: the ONE thing that is yours to set.
    #
    # Exclude, and nothing else. The headline, outlet, date, url and the
    # screenshot path are all read-only here. They describe somebody else's
    # page, and a document round-trip that let any of them be retyped would
    # let the review card and the slide disagree about what is being shown -
    # which is a fabricated attribution whether or not anyone meant it.
    clip = post.get("clipping")
    if isinstance(clip, dict) and clip.get("headline"):
        f = _fields(secs.get("Clipping", ""))
        clip = dict(clip)
        if "exclude" in f:
            clip["excluded"] = f["exclude"].strip().lower() in (
                "yes", "y", "true", "1", "on")
        out["clipping"] = clip

    cta = dict(out.get("cta") or {})
    f = _fields(secs.get("CTA", ""))
    if f.get("headline"):
        cta["headline"] = f["headline"]
    if f.get("sub"):
        cta["sub"] = f["sub"]
    out["cta"] = cta

    cap = _prose(secs.get("Caption", ""))
    if cap:
        out["caption"] = " ".join(cap.split())

    # Edited copy is not approved copy. Whatever the status was, the words
    # that were approved no longer exist.
    out["status"] = "needs_review"
    out["render_seq"] = int(out.get("render_seq") or 0) + 1
    out["edited_by_hand"] = True
    return out


def basis_loosened(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    """Did this edit relabel our own extrapolation as the paper's claim?

    THE HOLE THIS CLOSES
    ====================
    `basis` is editable in the document on purpose - a model that marked its
    own extrapolation "stated" is exactly the mistake a human should be able
    to correct. But the two directions are not symmetric.

    "stated" -> "inferred" TIGHTENS: it forces conditional language on both the
    slide and the cover, and prints the "our read" marker. Nothing can go wrong.

    "inferred" -> "stated" LOOSENS, and it loosens three things at once with
    one word in a text file:
      * the hedge requirement on the slide stops applying
      * the hedge requirement on the COVER stops applying
      * render.py stops drawing "OUR READ, NOT THE PAPER'S CLAIM" on either

    And the fourth is the one that makes it serious: the stored audit was
    produced by a run that was explicitly TOLD not to report the cover
    headline or the implications slide, because they were flagged
    extrapolations. recheck() does not re-run audit() - it is a model call -
    so flipping the label hands back a passing report over copy that nothing
    ever checked as a claim about the paper.

    So the edit is applied - they are your words - and recorded as a blocker,
    which is what `force approve` exists for.
    """
    def b(p):
        for sl in p.get("slides") or []:
            if isinstance(sl, dict) and sl.get("basis"):
                return str(sl.get("basis"))
        return ""
    return b(before) == "inferred" and b(after) == "stated"


def recheck(post: Dict[str, Any],
            before: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Re-run the code-level checks over hand-edited copy and record them.

    Does NOT refuse the edit - these are your words and you are entitled to
    them. It records what the checks found so the review card shows it and a
    plain `approve` is refused, exactly as a forced revision behaves. The
    forced caveats are promises the README makes on the account's behalf; one
    deleted by accident in a mobile editor must not publish silently.
    """
    from draft import flatten, lint, local_unverified_numbers, study_from_post
    from vet import VetReport

    qa = dict(post.get("qa") or {})
    extra: List[str] = []
    if before is not None and basis_loosened(before, post):
        extra.append(
            "GUARDRAIL this edit relabelled our own extrapolation as the "
            "paper's claim (basis inferred -> stated). That drops the "
            "conditional language requirement on the slide AND the cover, "
            "removes the 'our read' marker from both, and reuses an audit "
            "that was told to skip them. Re-draft, or read the paper "
            "yourself before force-approving.")
    try:
        s = study_from_post(post)
        rep = VetReport.from_dict(post.get("vet") or {})
        errs = lint(post, rep, s)
        bad = local_unverified_numbers(flatten(post), s.abstract)
    except Exception as e:
        qa["lint_errors"] = sorted(set(list(qa.get("lint_errors") or []) + [
            f"GUARDRAIL hand-edited copy could not be re-checked against the "
            f"paper ({type(e).__name__}). Read it yourself before approving."]
            + extra))
        qa["publishable"] = False
        post = dict(post)
        post["qa"] = qa
        return post

    errs = errs + extra
    qa["lint_errors"] = errs
    qa["style_flags"] = [e[len("STYLE "):] for e in errs if e.startswith("STYLE ")]
    qa["unverified_numbers"] = bad
    hard = [e for e in errs if not e.startswith("STYLE ")]
    qa["publishable"] = not (hard or bad or qa.get("blocking_claims"))
    post = dict(post)
    post["qa"] = qa
    return post
