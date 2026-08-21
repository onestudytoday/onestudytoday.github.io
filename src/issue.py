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


def build(post: Dict[str, Any], image_base: str) -> str:
    st = post["study"]
    vet = post.get("vet", {}) or {}
    qa = post.get("qa", {}) or {}
    blockers = blocking_reasons(post)
    cap = build_caption(post)
    cs = caption_stats(cap)

    imgs = "\n".join(
        f'<img src="{image_base}/{post["id"]}/{Path(p).stem}.jpg" width="230">'
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
