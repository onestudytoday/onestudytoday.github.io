"""
Builds the GitHub Issue body used as the mobile review card.

When the daily workflow drafts a post it opens an issue containing the rendered
slides, the vetting report, the blockers, and the full caption. You review it
from the GitHub app on your phone and comment `approve` to publish, or `kill`
to bin it. That is the entire human step.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

from caption import build_caption, caption_stats
from config import settings
from review import blocking_reasons

SEV_ICON = {"hard": "🛑", "warn": "⚠️", "note": "·"}

# The last line of this issue body is a machine-readable marker:
#     <!-- onestudytoday-post-id: 2026-08-21-psych-1a2b3c4d -->
# publish-on-approve.yml reads it to work out which post you just approved.
#
# Several pieces of this body are NOT written by us: the paper's title comes
# straight out of a public research database, and the headline and caption come
# out of a language model that was fed that paper's abstract. Either could
# contain the marker text - by accident or because someone published a paper
# designed to put it there - and an extra marker higher up the body is an
# attempt to make the publish workflow act on something other than this post.
#
# So: the marker phrase is broken up wherever it appears in text we did not
# write, and HTML comment delimiters in that text are neutered too. The
# workflow independently refuses anything that is not shaped like a real post
# id, so this is the belt to that pair of braces.
_MARKER = "onestudytoday-post-id"
_COMMENT_OPEN = re.compile(r"<!--+")
_COMMENT_CLOSE = re.compile(r"--+>")


def _defang(text: Any) -> str:
    """Strip anything in third-party text that could impersonate our marker."""
    t = str(text or "")
    t = t.replace(_MARKER, "onestudytoday post id")
    t = _COMMENT_OPEN.sub("&lt;!--", t)
    t = _COMMENT_CLOSE.sub("--&gt;", t)
    return t


def _reel_line(post: Dict[str, Any]) -> str:
    """One line on the review card saying whether this post has a Reel.

    Reels shipped on 31 Aug and silently built nothing for weeks: the skip and
    the failure paths both only printed to a workflow log. Putting the answer
    on the card is what turns "Reels are on" into something checkable from a
    phone rather than something taken on trust.
    """
    st = post.get("reel_status")
    if not isinstance(st, dict):
        # Drafted before reel_status existed, or by skeleton().
        return "yes" if post.get("reel") else "unknown (drafted before this was recorded)"
    if st.get("built"):
        mb = (st.get("bytes") or 0) / 1e6
        return f"**yes** - {st.get('duration')}s, {mb:.1f}MB"
    return f"no - {_defang(st.get('reason', 'unknown'))}"


def _traction_line(post: Dict[str, Any]) -> str:
    """Whether a general audience had already picked this paper out.

    Worth a row of its own because it changes how you read the rest of the
    card: "4,200 people upvoted this" is a reason to look harder at whether
    the copy is overstating, since the studies that travel are
    disproportionately the ones whose headline outruns their data.
    """
    tr = ((post.get("study") or {}).get("traction")) or {}
    if not isinstance(tr, dict) or not tr:
        return "found by topic search"
    src = _defang(tr.get("source", "?"))
    score = tr.get("score")
    try:
        score = f"{float(score):.0f}"
    except Exception:
        score = "?"
    return f"picked up on **{src}** (traction {score}/20)"


def _revision_line(post: Dict[str, Any]) -> str:
    """What has been asked of this draft since it was written.

    Defanged like everything else on the card: the instruction is your own
    text, but it arrives from a GitHub comment and lands in a public issue
    body, so it goes through the same marker-stripping as the study title.
    """
    history = post.get("revisions") or []
    if not isinstance(history, list) or not history:
        return "none - this is the original draft"
    last = history[-1] if isinstance(history[-1], dict) else {}
    # Collapsed to one line before it goes in a table cell. _defang() neuters
    # the post-id marker but leaves newlines and pipes alone, and either one
    # ends the row early and drops the rest of the instruction into the issue
    # body as free markdown. Confusing rather than dangerous - but the whole
    # point of this card is that it reads accurately at a glance on a phone.
    asked = " ".join(_defang(last.get("instruction", "?")).split()).replace("|", "/")
    if len(asked) > 120:
        asked = asked[:117] + "..."
    return f"**{len(history)}** - latest: _{asked}_"


def build(post: Dict[str, Any], image_base: str) -> str:
    st = post["study"]
    vet = post.get("vet", {}) or {}
    qa = post.get("qa", {}) or {}
    blockers = blocking_reasons(post)
    cap = build_caption(post)
    cs = caption_stats(cap)

    # ?v=<n> is a cache-buster, and it is load-bearing for `revise`.
    #
    # GitHub does not hotlink these images: it rewrites every <img> through
    # its own camo proxy, which caches aggressively and keys on the full URL.
    # A revision re-renders the slides and overwrites docs/img/<id>/*.jpg at
    # the SAME paths, so without a changing query string the issue would keep
    # showing the pre-revision slides indefinitely - the copy would be fixed,
    # the pictures would not, and the feature would look broken while working
    # perfectly. Bumping n on each revision gives camo a URL it has not seen.
    #
    # A query string rather than versioned filenames on purpose: reel.py and
    # publish.py both glob that directory, and renaming files to bust a cache
    # would leave them picking up whichever copy sorted first.
    # MONOTONIC, not a revision count. `revert` shortens the history, so a
    # count would go 0,1,0,1 across revise/revert/revise - and the second
    # revision's slides would be requested at ?v=1, a URL the proxy already
    # cached with the FIRST revision's images. The card would then show one
    # draft's words over another draft's pictures, which is the worst possible
    # failure for a review step whose entire job is that you looked at it.
    ver = int(post.get("render_seq") or len(post.get("revisions") or []))
    q = f"?v={ver}" if ver else ""
    imgs = "\n".join(
        f'<img src="{image_base}/{post["id"]}/{Path(p).stem}.jpg{q}" width="230">'
        for p in sorted((Path("out/posts") / post["id"]).glob("*.png")))

    flags = "\n".join(
        f"- {SEV_ICON.get(f.get('severity'), '·')} **{_defang(f.get('code'))}** — "
        f"{_defang(f.get('message'))}"
        for f in vet.get("flags", []) or []) or "- No flags raised."

    blk = ""
    if blockers:
        blk = ("\n> [!CAUTION]\n> **This post is blocked and cannot publish.**\n"
               + "\n".join(f"> - {_defang(b)}" for b in blockers) + "\n")

    pre = "\n> [!WARNING]\n> **This is a PREPRINT.** The badge is forced onto the cover slide.\n" \
        if st.get("is_preprint") else ""

    return f"""## {_defang(post['cover']['headline'].replace('**', ''))}

**{_defang(st['journal'])}** · {_defang(st['pub_date_display'])} · [read the paper]({st.get('url', '')})
`{post['niche']}` · verdict **{vet.get('verdict')}** · credibility **{vet.get('score')}/100**
{pre}{blk}
{imgs}

### Vetting report
design: `{vet.get('design')}` · subjects: `{vet.get('subjects')}` · n: `{vet.get('sample_size')}`

{flags}

### Draft QA
| check | result |
|---|---|
| lint violations | {len(qa.get('lint_errors', []) or [])} |
| repair rounds | {qa.get('repair_rounds', 0)} |
| unsupported claims (blocking) | {len(qa.get('blocking_claims', []) or [])} |
| numbers not found in abstract | {len(qa.get('unverified_numbers', []) or [])} |
| caption length | {cs['chars']} / 2200 |
| hashtags | {cs['hashtags']} |
| format | {qa.get('format', 'explainer')} |
| reel | {_reel_line(post)} |
| revisions | {_revision_line(post)} |
| why this study | {_traction_line(post)} |

<details><summary>Full caption</summary>

```
{_defang(cap)}
```
</details>

<details><summary>Original abstract check — study title</summary>

{_defang(st['title'])}

</details>

---
### To publish
Comment **`approve`** on this issue. The publish workflow will fire, post the
carousel, update the link-in-bio page, and close this issue.

Comment **`kill`** to reject it and add the study to the do-not-use ledger.

Comment **`force approve`** only if you have read the paper yourself and
disagree with a blocker above.

<!-- onestudytoday-post-id: {post['id']} -->
"""


if __name__ == "__main__":
    post = json.loads(Path(sys.argv[1]).read_text())
    base = sys.argv[2] if len(sys.argv) > 2 else settings().public_image_base
    print(build(post, base))
