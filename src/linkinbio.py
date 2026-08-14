"""
Your own link-in-bio page. No Linktree, no monthly fee, no third party
deciding what your links look like.

It builds docs/index.html from data/published/*.json and GitHub Pages serves
it at https://<username>.github.io/<repo>/ - that URL goes in your Instagram
bio. Every post that publishes automatically adds itself, newest first,
grouped by week.

Why not Linktree: it costs money for anything beyond the basics, it puts its
own branding on your page, it can't auto-populate from your posts, and you
don't own the URL. This is free, matches your carousel design exactly, and
updates itself as a side effect of publishing.

    python src/linkinbio.py
"""

from __future__ import annotations

import html
import json
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from config import DOCS, PUBLISHED, settings
from theme import NICHES

PROFILE_LINKS: List[Dict[str, str]] = [
    # Edit these. They render as the big buttons at the top.
    {"label": "Suggest a study", "url": "mailto:you@example.com",
     "note": "Found something worth covering?"},
    {"label": "How we pick and check studies", "url": "#method",
     "note": "The vetting rules, in plain English"},
]


def _week_key(iso: str) -> str:
    d = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_label(key: str) -> str:
    y, w = key.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    sunday = date.fromisocalendar(int(y), int(w), 7)
    if monday.month == sunday.month:
        return f"{monday.strftime('%b %-d')}–{sunday.strftime('%-d, %Y')}"
    return f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}"


CSS = """
:root{--bg:#0B0B0F;--fg:#fff;--muted:#8A8A99;--line:#23232E}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:620px;margin:0 auto;padding:52px 22px 90px}
.brand{font-size:30px;font-weight:800;letter-spacing:-1px;margin:0}
.tag{color:var(--muted);margin:10px 0 34px;font-size:15px}
.btn{display:block;border:1px solid var(--line);border-radius:14px;padding:17px 20px;
margin-bottom:12px;text-decoration:none;color:#fff;transition:.15s;background:#101016}
.btn:hover{border-color:#3a3a4a;transform:translateY(-1px)}
.btn b{display:block;font-size:16px}
.btn span{color:var(--muted);font-size:13.5px}
h2{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);
margin:42px 0 14px;font-weight:800}
.item{display:flex;gap:14px;padding:15px 0;border-bottom:1px solid var(--line);
text-decoration:none;color:#fff}
.item:hover .t{text-decoration:underline}
.day{flex:0 0 74px;font-size:10.5px;font-weight:800;letter-spacing:1.4px;padding-top:3px}
.t{font-size:15.5px;line-height:1.42}
.src{color:var(--muted);font-size:13px;margin-top:4px}
.badge{display:inline-block;font-size:10px;font-weight:800;letter-spacing:1px;
border:1px solid #FBBF24;color:#FBBF24;border-radius:5px;padding:1px 6px;margin-left:6px}
.method{border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:44px;
background:#101016}
.method h3{margin:0 0 12px;font-size:15px}
.method li{color:#c9c9d4;font-size:14.5px;margin-bottom:9px}
footer{color:var(--muted);font-size:12.5px;margin-top:44px;text-align:center}
"""

METHOD = """
<div class="method" id="method">
  <h3>How a study gets on this page</h3>
  <ul>
    <li>It has to have been published in the last 14 days.</li>
    <li>It gets checked against retraction records before anything is written.</li>
    <li>Predatory publishers are auto-rejected from a blocklist.</li>
    <li>If it is a preprint, the post says so on the cover slide. Always.</li>
    <li>If the design is observational, the copy is not allowed to say "causes".</li>
    <li>If it was done in mice, the post says mice.</li>
    <li>Sample size, funding conflicts and relative-vs-absolute risk are checked
        automatically and turned into the fine-print slide.</li>
    <li>A human reads every post before it goes live.</li>
  </ul>
</div>
"""


def build() -> str:
    s = settings()
    posts: List[Dict[str, Any]] = []
    for f in PUBLISHED.glob("*.json"):
        try:
            posts.append(json.loads(f.read_text()))
        except Exception:
            continue
    posts.sort(key=lambda p: p["study"].get("pub_date", ""), reverse=True)

    weeks: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for p in posts:
        weeks.setdefault(_week_key(p["study"]["pub_date"]), []).append(p)

    buttons = "".join(
        f'<a class="btn" href="{html.escape(b["url"])}"><b>{html.escape(b["label"])}</b>'
        f'<span>{html.escape(b.get("note", ""))}</span></a>'
        for b in PROFILE_LINKS)

    sections = []
    for wk, items in list(weeks.items())[:8]:
        rows = []
        for p in items:
            st = p["study"]
            n = NICHES.get(p.get("niche", "wildcard"), NICHES["wildcard"])
            pre = '<span class="badge">PREPRINT</span>' if st.get("is_preprint") else ""
            rows.append(
                f'<a class="item" href="{html.escape(st.get("url") or "#")}" target="_blank" '
                f'rel="noopener">'
                f'<div class="day" style="color:{n["accent"]}">{n["short"]}</div>'
                f'<div><div class="t">{html.escape(st["title"])}{pre}</div>'
                f'<div class="src">{html.escape(st["journal"])} · '
                f'{html.escape(st["pub_date_display"])}</div></div></a>')
        sections.append(f'<h2>{_week_label(wk)}</h2>{"".join(rows)}')

    if not sections:
        sections = ['<h2>This week</h2><div class="src">First posts land soon.</div>']

    handle = s.handle
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(handle)} · every study we cover</title>
<meta name="description" content="Direct links to every scientific study covered on {html.escape(handle)}.">
<style>{CSS}</style></head><body><div class="wrap">
<h1 class="brand">{html.escape(handle)}</h1>
<p class="tag">One real study, every weekday. Here is every paper we have covered,
with a direct link to the original. No paywalled summaries, no telephone game.</p>
{buttons}
{"".join(sections)}
{METHOD}
<footer>Updated automatically when a post publishes ·
{datetime.utcnow().strftime('%d %b %Y')}</footer>
</div></body></html>"""

    out = DOCS / "index.html"
    out.write_text(doc)
    return str(out)


if __name__ == "__main__":
    print(build())
