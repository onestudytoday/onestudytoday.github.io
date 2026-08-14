"""
Builds the GitHub Issue body used as the mobile review card.

When the daily workflow drafts a post it opens an issue containing the rendered
slides, the vetting report, the blockers, and the full caption. You review it
from the GitHub app on your phone and comment `approve` to publish, or `kill`
to bin it. That is the entire human step.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from caption import build_caption, caption_stats
from config import settings
from review import blocking_reasons

SEV_ICON = {"hard": "🛑", "warn": "⚠️", "note": "·"}


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
        f"- {SEV_ICON.get(f.get('severity'), '·')} **{f.get('code')}** — {f.get('message')}"
        for f in vet.get("flags", []) or []) or "- No flags raised."

    blk = ""
    if blockers:
        blk = ("\n> [!CAUTION]\n> **This post is blocked and cannot publish.**\n"
               + "\n".join(f"> - {b}" for b in blockers) + "\n")

    pre = "\n> [!WARNING]\n> **This is a PREPRINT.** The badge is forced onto the cover slide.\n" \
        if st.get("is_preprint") else ""

    return f"""## {post['cover']['headline'].replace('**', '')}

**{st['journal']}** · {st['pub_date_display']} · [read the paper]({st.get('url', '')})
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
{cap}
```
</details>

<details><summary>Original abstract check — study title</summary>

{st['title']}

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
