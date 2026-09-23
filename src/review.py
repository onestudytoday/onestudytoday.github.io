"""
The human review gate. Nothing reaches Instagram without passing through here.

Runs a small local web app - no framework, standard library only - that shows
you every queued post exactly as it will appear: the rendered slides, the
vetting report card, the assembled caption with its character count, and the
QA result from the drafting audit.

You can edit copy in the browser, re-render, and approve or kill. Approving is
the ONLY thing that sets status="approved", and publish.py refuses to run on
anything else.

    python src/review.py serve         # then open http://localhost:8765
    python src/review.py list
    python src/review.py approve <post-id>
    python src/review.py reject <post-id> "reason"
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from caption import build_caption, caption_stats
from config import OUT, QUEUE, ROOT
from render import contact_sheet, render_post

PORT = 8765


# ---------------------------------------------------------------------------
def queued() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(QUEUE.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def load(post_id: str) -> Optional[Dict[str, Any]]:
    p = QUEUE / f"{post_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def save(post: Dict[str, Any]) -> None:
    (QUEUE / f"{post['id']}.json").write_text(json.dumps(post, indent=2))


def rerender(post: Dict[str, Any]) -> List[str]:
    """Re-render a post's slides after an edit - and RESTAGE them.

    Re-rendering used to write only into out/posts/<id>/, which .gitignore
    excludes as working scratch. The slides that actually publish are the
    JPEGs in docs/img/<id>/, staged once at draft time and committed. So
    fixing an overstated headline or a missing caveat here corrected the
    review card and the caption while Instagram still received the ORIGINAL
    slides - the edit was silently discarded at exactly the moment it
    mattered most, and the published carousel showed the old cover next to
    the new caption.

    Restaging is therefore part of re-rendering, not a separate step someone
    has to remember. Stale JPEGs from a previous render are removed first, so
    an edit that changes the slide count cannot leave an orphaned slide (a
    deleted caveats slide, say) still sitting there to be published.
    """
    from config import DOCS, settings
    from publish import to_jpeg
    d = OUT / "posts" / post["id"]
    paths = render_post(post, settings().theme, str(d))
    contact_sheet(paths, str(OUT / "posts" / f"SHEET_{post['id']}.png"))

    staged_dir = DOCS / "img" / post["id"]
    if staged_dir.exists():
        for old in staged_dir.glob("*.jpg"):
            old.unlink()
    to_jpeg(paths, staged_dir)

    # The Reel is built from these same JPEGs, ONCE, at draft time - and
    # _publish_one() sends the Reel in PREFERENCE to the carousel when the
    # niche wants one and the file exists. So re-rendering the slides and
    # leaving reel.mp4 alone republishes the pre-edit slides as a video:
    # excluding a clipping would take the page off the carousel and leave it
    # in the Reel, and a corrected number or a restored caveat would go out
    # uncorrected. Same class of bug as the one this function's docstring is
    # about, one layer further down.
    #
    # Rebuilt if it can be, and DELETED if it cannot. Deleting is the safe
    # failure: no Reel means the carousel publishes, and the carousel is
    # always current. A stale Reel is the only outcome that is actually wrong.
    stale = staged_dir / "reel.mp4"
    if stale.exists():
        stale.unlink()
        post.pop("reel", None)
        try:
            from reel import build_reel
            info = build_reel(post["id"], images=[Path(p) for p in paths])
            post["reel"] = {"path": info["path"], "duration": info["duration"],
                            "bytes": info["bytes"]}
            post["reel_status"] = {"built": True,
                                   "duration": info["duration"],
                                   "bytes": info["bytes"],
                                   "reason": "rebuilt after an edit"}
        except Exception as e:
            print(f"  ! the Reel could not be rebuilt after this edit "
                  f"({type(e).__name__}: {e}); it has been removed, so this "
                  f"post publishes as a carousel with the corrected slides")
            post["reel_status"] = {
                "built": False,
                "reason": f"removed on re-render, rebuild failed "
                          f"({type(e).__name__})"}
    return paths


# ---------------------------------------------------------------------------
def set_status(post_id: str, status: str, note: str = "") -> Dict[str, Any]:
    post = load(post_id)
    if not post:
        raise SystemExit(f"No queued post with id {post_id}")
    blockers = blocking_reasons(post)
    if status == "approved" and blockers:
        raise SystemExit(
            "Cannot approve - unresolved blockers:\n  - " + "\n  - ".join(blockers)
            + "\n\nFix the copy, or use `force-approve` if you have read the paper "
              "yourself and disagree with the checker.")
    post["status"] = status
    post.setdefault("review", {})[status] = {"note": note}
    if status == "rejected":
        # The ledger entry below is what "kill ... never source that study
        # again" (issue.py, docs/RUNBOOK.md) actually depends on - without
        # it, killing a post from the GitHub comment gate (the only way this
        # happens in production) never touched the ledger, so the same study
        # was free to be sourced and drafted all over again on the next run.
        from sources import load_ledger, save_ledger, study_key
        led = load_ledger()
        led.setdefault("rejected", {})[study_key(post)] = {
            "title": post["study"].get("title", "")[:160],
            "doi": post["study"].get("doi", ""),
            "reason": note or "killed by reviewer"}
        save_ledger(led)
        # The ledger entry is the thing that actually keeps this study out of
        # future sourcing - once it's written, this file has nothing left to
        # do. Leaving it behind in data/queue/ forever, just with a status
        # field flipped, was the root of a real production bug: a workflow
        # step used to pick "whichever queue file sorts first" to decide
        # which post to open a review issue for, and a killed post with an
        # early date prefix could permanently win that pick over whatever was
        # actually freshly drafted. Deleting it here removes that failure
        # mode at the source instead of only at the one call site that
        # tripped over it (.github/workflows/daily-draft.yml now also reads
        # the id from its own run's log rather than the directory, so this
        # isn't the only fix - but a queue file that no longer exists can't
        # be picked by ANY future bug of that shape, which a live file with a
        # status flag can).
        p = QUEUE / f"{post_id}.json"
        if p.exists():
            p.unlink()
        # And the editable document beside it - see pipeline._drop_doc().
        doc = QUEUE / f"{post_id}.md"
        if doc.exists():
            doc.unlink()
        return post
    save(post)
    return post


def blocking_reasons(post: Dict[str, Any]) -> List[str]:
    """Everything that must be cleared before this can go live."""
    out: List[str] = []
    qa = post.get("qa", {}) or {}
    vet = post.get("vet", {}) or {}

    if vet.get("verdict") == "REJECT":
        out.append("Vetting engine verdict is REJECT.")
    for e in qa.get("lint_errors", []) or []:
        if e.startswith("GUARDRAIL"):
            out.append(f"Guardrail violation: {e}")
        elif e.startswith("required caveat not represented"):
            # The vetting engine decides certain caveats MUST appear on the
            # fine-print slide - "done in mice, not humans", "found a pattern,
            # not a cause", "only 24 people took part", the industry-funding
            # disclosure. draft.lint() already notices when the drafting model
            # dropped one, but it emits a plain unprefixed error, and this
            # function only ever counted the "GUARDRAIL"-prefixed ones. So
            # every forced caveat except the preprint one (handled separately
            # below) was detected and then ignored: a post missing its
            # mandated caveat had zero blockers and a plain `approve` from
            # your phone published it. These are the specific promises the
            # README makes about this account, so they block.
            out.append(f"Missing forced caveat: {e}")
    for c in qa.get("blocking_claims", []) or []:
        out.append(f"Unsupported claim: \"{c.get('claim','')[:90]}\" - {c.get('problem','')}")
    for n in qa.get("unverified_numbers", []) or []:
        out.append(f"Number not found in the abstract: {n.get('number')}")

    st = post.get("study", {})
    if st.get("is_preprint"):
        cav = " ".join(post.get("caveats", [])).lower()
        if "preprint" not in cav and "peer review" not in cav:
            out.append("Preprint with no preprint caveat on the fine-print slide.")
    if not st.get("doi") and not st.get("url"):
        out.append("No link to the original study.")
    if not post.get("caveats"):
        out.append("No caveats slide.")

    # A skeleton() post is the hand-authoring aid pipeline.run() falls back to
    # when ANTHROPIC_API_KEY is missing - every field is literal instruction
    # text ("**WRITE THE HOOK.** One sentence."). It is not a draft, and it
    # passed every check here because none of them look at whether the copy
    # was actually written. An unnoticed missing/expired API key would have
    # opened a normal-looking review issue whose approval published template
    # text to Instagram.
    from draft import flatten as _flatten
    body = _flatten(post)
    if "WRITE THE HOOK" in body or body.count("WRITE ") >= 3:
        out.append("This is an unwritten skeleton draft, not finished copy - "
                   "the drafting step did not run (check ANTHROPIC_API_KEY).")
    return out


# ---------------------------------------------------------------------------
CSS = """
:root{--bg:#0B0B0F;--fg:#fff;--muted:#8A8A99;--line:#23232E;--ok:#22C55E;
--warn:#FBBF24;--bad:#F43F5E;--accent:#A855F7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
header{padding:22px 30px;border-bottom:1px solid var(--line);display:flex;
gap:18px;align-items:baseline;position:sticky;top:0;background:var(--bg);z-index:9}
h1{font-size:19px;margin:0;letter-spacing:-.3px}
.sub{color:var(--muted);font-size:13px}
main{padding:26px 30px;max-width:1180px}
.card{border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:26px}
.row{display:flex;gap:22px;flex-wrap:wrap}
.slides{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px}
.slides img{height:330px;border-radius:10px;border:1px solid var(--line)}
.pill{display:inline-block;padding:4px 11px;border-radius:99px;font-size:11px;
font-weight:700;letter-spacing:.9px;text-transform:uppercase}
.pass{background:var(--ok);color:#04150b}.hold{background:var(--warn);color:#231a02}
.rej{background:var(--bad);color:#fff}
.flag{padding:9px 12px;border-left:3px solid var(--line);margin:7px 0;
background:#131319;border-radius:0 8px 8px 0;font-size:13.5px}
.flag.hard{border-color:var(--bad)}.flag.warn{border-color:var(--warn)}
.flag.note{border-color:var(--muted)}
.block{border:1px solid var(--bad);background:#2a0d15;border-radius:10px;
padding:14px 16px;margin:12px 0}
textarea{width:100%;background:#131319;color:#fff;border:1px solid var(--line);
border-radius:9px;padding:12px;font:14px/1.5 ui-monospace,monospace;resize:vertical}
label{display:block;font-size:11px;letter-spacing:1px;color:var(--muted);
text-transform:uppercase;margin:16px 0 6px;font-weight:700}
button{border:0;border-radius:9px;padding:11px 20px;font-weight:700;font-size:14px;
cursor:pointer;margin-right:9px}
.approve{background:var(--ok);color:#04150b}.kill{background:var(--bad);color:#fff}
.savebtn{background:var(--accent);color:#fff}
.meta{color:var(--muted);font-size:13px}
a{color:var(--accent)}
kbd{background:#1b1b23;padding:2px 7px;border-radius:5px;font-size:12px;
border:1px solid var(--line)}
.empty{color:var(--muted);padding:60px 0;text-align:center}
"""


def _flag_html(vet: Dict[str, Any]) -> str:
    out = []
    for f in vet.get("flags", []) or []:
        sev = f.get("severity", "note")
        out.append(f'<div class="flag {sev}"><b>{html.escape(f.get("code",""))}</b> — '
                   f'{html.escape(f.get("message",""))}</div>')
    return "".join(out) or '<div class="meta">No flags raised.</div>'


def _post_html(post: Dict[str, Any]) -> str:
    pid = post["id"]
    vet = post.get("vet", {}) or {}
    qa = post.get("qa", {}) or {}
    verdict = vet.get("verdict", "?")
    cls = {"PASS": "pass", "HOLD": "hold", "REJECT": "rej"}.get(verdict, "hold")
    blockers = blocking_reasons(post)
    cap = build_caption(post)
    cs = caption_stats(cap)

    imgs = sorted((OUT / "posts" / pid).glob("*.png"))
    slides = "".join(f'<img src="/img/{pid}/{p.name}" alt="">' for p in imgs) \
        or '<div class="meta">Not rendered yet.</div>'

    blk = ""
    if blockers:
        blk = ('<div class="block"><b>BLOCKED — cannot approve until these clear</b><ul>'
               + "".join(f"<li>{html.escape(b)}</li>" for b in blockers) + "</ul></div>")

    st = post["study"]
    pre = ' <span class="pill hold">PREPRINT</span>' if st.get("is_preprint") else ""

    return f"""
<div class="card" id="{html.escape(pid)}">
  <div class="row" style="justify-content:space-between;align-items:baseline">
    <div>
      <span class="pill {cls}">{verdict} · {vet.get('score','?')}/100</span>{pre}
      <span class="meta"> &nbsp;{html.escape(post.get('niche',''))} · {html.escape(pid)}</span>
    </div>
    <div class="meta">{html.escape(st.get('journal',''))} ·
      {html.escape(st.get('pub_date_display',''))} ·
      <a href="{html.escape(st.get('url','#'))}" target="_blank">open paper</a></div>
  </div>

  <h2 style="font-size:22px;margin:16px 0 4px">
    {html.escape(post['cover']['headline'].replace('**',''))}</h2>
  <div class="meta">{html.escape(st.get('title','')[:170])}</div>

  <div class="slides" style="margin-top:16px">{slides}</div>

  {blk}

  <div class="row" style="margin-top:6px">
    <div style="flex:1;min-width:340px">
      <label>Vetting report — design: {html.escape(str(vet.get('design')))} ·
        subjects: {html.escape(str(vet.get('subjects')))} ·
        n: {html.escape(str(vet.get('sample_size')))}</label>
      {_flag_html(vet)}
    </div>
    <div style="flex:1;min-width:340px">
      <label>Draft QA — repairs: {qa.get('repair_rounds', 0)} ·
        publishable: {qa.get('publishable')}</label>
      <div class="flag note">Lint errors: {len(qa.get('lint_errors', []) or [])}</div>
      <div class="flag note">Blocking claims: {len(qa.get('blocking_claims', []) or [])}</div>
      <div class="flag note">Unverified numbers: {len(qa.get('unverified_numbers', []) or [])}</div>
    </div>
  </div>

  <form method="POST" action="/save">
    <input type="hidden" name="id" value="{html.escape(pid)}">
    <label>Cover headline — wrap one phrase in **double asterisks**</label>
    <textarea name="headline" rows="2">{html.escape(post['cover']['headline'])}</textarea>
    <label>Caveats — one per line, these are non-negotiable</label>
    <textarea name="caveats" rows="4">{html.escape(chr(10).join(post.get('caveats', [])))}</textarea>
    <label>Caption body — assembled caption is {cs['chars']} chars
      ({cs['chars_remaining']} left of 2200), {cs['hashtags']} hashtags</label>
    <textarea name="caption" rows="7">{html.escape(post.get('caption', ''))}</textarea>
    <div style="margin-top:14px">
      <button class="savebtn" type="submit">Save &amp; re-render</button>
    </div>
  </form>

  <details style="margin-top:16px">
    <summary class="meta" style="cursor:pointer">Preview the full assembled caption</summary>
    <pre style="white-space:pre-wrap;background:#131319;padding:16px;border-radius:9px;
      font-size:13px;margin-top:10px">{html.escape(cap)}</pre>
  </details>

  <div style="margin-top:18px">
    <form method="POST" action="/approve" style="display:inline">
      <input type="hidden" name="id" value="{html.escape(pid)}">
      <button class="approve" {'disabled style=opacity:.35' if blockers else ''}
        type="submit">Approve for publishing</button>
    </form>
    <form method="POST" action="/reject" style="display:inline">
      <input type="hidden" name="id" value="{html.escape(pid)}">
      <button class="kill" type="submit">Kill this one</button>
    </form>
    <span class="meta">&nbsp; status: <b>{html.escape(post.get('status', '?'))}</b></span>
  </div>
</div>"""


def page() -> str:
    posts = queued()
    body = "".join(_post_html(p) for p in posts) or \
        '<div class="empty">Queue is empty. Run <kbd>python src/pipeline.py run</kbd>.</div>'
    n_ok = sum(1 for p in posts if p.get("status") == "approved")
    return f"""<!doctype html><meta charset="utf-8">
<title>One Study Today review</title><style>{CSS}</style>
<header><h1>One Study Today review queue</h1>
<span class="sub">{len(posts)} queued · {n_ok} approved · nothing publishes without approval</span>
</header><main>{body}</main>"""


# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code=200, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/img/"):
            rel = urllib.parse.unquote(self.path[len("/img/"):])
            f = (OUT / "posts" / rel).resolve()
            if not str(f).startswith(str((OUT / "posts").resolve())) or not f.exists():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, f.read_bytes(), "image/png")
        return self._send(200, page().encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())
        pid = form.get("id", [""])[0]
        post = load(pid)
        if not post:
            return self._send(404, b"unknown post", "text/plain")

        if self.path == "/save":
            post["cover"]["headline"] = form.get("headline", [""])[0].strip()
            post["caveats"] = [l.strip() for l in form.get("caveats", [""])[0].splitlines()
                               if l.strip()]
            post["caption"] = form.get("caption", [""])[0].strip()
            post["status"] = "needs_review"
            save(post)
            rerender(post)
        elif self.path == "/approve":
            if not blocking_reasons(post):
                post["status"] = "approved"
                save(post)
        elif self.path == "/reject":
            set_status(pid, "rejected")

        self.send_response(303)
        self.send_header("Location", f"/#{pid}")
        self.end_headers()


def serve(port: int = PORT):
    print(f"\n  Review queue: http://localhost:{port}\n"
          f"  {len(queued())} posts waiting. Ctrl-C to stop.\n")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    sub.add_parser("list")
    for c in ("approve", "force-approve"):
        s = sub.add_parser(c)
        s.add_argument("post_id")
    r = sub.add_parser("reject")
    r.add_argument("post_id")
    r.add_argument("note", nargs="?", default="")
    rv = sub.add_parser("revise")
    rv.add_argument("post_id")
    rv.add_argument("instruction")
    rv.add_argument("--force", action="store_true",
                    help="apply the rewrite even if the checks reject it. The "
                         "post is then BLOCKED and needs force-approve.")
    sub.add_parser("revert").add_argument("post_id")
    sub.add_parser("apply-doc").add_argument("post_id")
    for c in ("exclude-clipping", "include-clipping"):
        sub.add_parser(c).add_argument("post_id")
    ri = sub.add_parser("reimage")
    ri.add_argument("post_id")
    ri.add_argument("query", nargs="?", default="",
                    help="what to search for instead. Omitted: try the next "
                         "candidate for the automatic search term.")
    a = ap.parse_args()

    if a.cmd == "serve":
        return serve(a.port)
    if a.cmd == "list":
        for p in queued():
            b = blocking_reasons(p)
            print(f"{p.get('status','?'):14} {p['id']:44} "
                  f"{p.get('vet',{}).get('verdict','?'):7} "
                  f"blockers={len(b)}")
            for x in b:
                print(f"    ! {x}")
        return
    if a.cmd == "approve":
        set_status(a.post_id, "approved")
        print(f"{a.post_id} approved.")
        return
    if a.cmd == "force-approve":
        p = load(a.post_id)
        if not p:
            # Reached from the GitHub comment gate. Fail with a sentence a
            # human can act on rather than a TypeError on None.
            raise SystemExit(f"No queued post with id {a.post_id}")
        p["status"] = "approved"
        p.setdefault("review", {})["forced"] = True
        save(p)
        print(f"{a.post_id} FORCE approved - blockers overridden by a human.")
        return

    # ---- pick a different cover photograph ------------------------------
    #
    # `revise` could never do this. It rewrites COPY - it calls a model,
    # gets new text back, re-runs the checks and re-renders - and it does not
    # touch cover_art at all. So "revise: change the background picture"
    # produced a perfectly successful revision in which the picture did not
    # change, over and over, with nothing reporting that the request had not
    # been understood. This is the command that actually does it.
    if a.cmd == "reimage":
        from config import DOCS
        from coverart import fetch_for
        from reel import check_post_id
        p = load(a.post_id)
        if not p:
            raise SystemExit(f"No queued post with id {a.post_id}")
        # The same guard every other write into docs/img/ uses: post["id"]
        # becomes a directory name here.
        check_post_id(a.post_id)

        # EVERY image already tried, not just the current one. The search is
        # deterministic - same query, same candidates, same scoring - so
        # without this the second request returns the first answer again and
        # the reviewer concludes the command is broken.
        tried = [str(u) for u in (p.get("cover_art_tried") or []) if u]
        cur = (p.get("cover_art") or {}).get("url")
        if cur and cur not in tried:
            tried.append(str(cur))

        art = fetch_for(p, DOCS / "img" / a.post_id,
                        query=a.query or None, exclude=tried)
        if not art:
            raise SystemExit(
                "No usable image found" + (f" for {a.query!r}" if a.query else "")
                + ". Nothing changed - the post keeps the picture it has.\n"
                  "Try `reimage: <something concrete and photographable>`, or "
                  "leave it: the flat background is what every post used to "
                  "look like.")
        p["cover_art"] = art
        p["cover_art_tried"] = tried + [str(art.get("url"))]
        # Bump the cache-buster BEFORE re-rendering. GitHub proxies these
        # images and caches on the full URL, so a new picture at the same
        # path would keep showing as the old one on the review card - the
        # change would have happened and looked like it had not.
        p["render_seq"] = int(p.get("render_seq") or 0) + 1
        rerender(p)
        save(p)
        print(f"{a.post_id}: new cover image from {art.get('source')} "
              f"<- {art.get('query')!r} ({art.get('licence')}). "
              f"{len(tried) + 1} tried so far. Slides re-rendered.")
        return

    # ---- show or hide the clipping page ---------------------------------
    #
    # A flag, not a delete. `include clipping` puts the same article back,
    # rather than sending the pipeline out to find another one that may no
    # longer be there - and it re-renders, because the slides are what
    # publish. The status is deliberately NOT changed: showing or hiding a
    # quotation from somebody else does not touch a word of our own copy, so
    # an approved post stays approved.
    if a.cmd in ("exclude-clipping", "include-clipping"):
        from clipping import ClippingError, set_excluded
        p = load(a.post_id)
        if not p:
            raise SystemExit(f"No queued post with id {a.post_id}")
        try:
            p = set_excluded(p, a.cmd == "exclude-clipping")
        except ClippingError as e:
            raise SystemExit(str(e))
        save(p)
        rerender(p)
        md_path = QUEUE / f"{a.post_id}.md"
        if md_path.exists():
            # Keep the editable document honest about what the post now says.
            from postdoc import to_markdown
            md_path.write_text(to_markdown(p))
        print(f"{a.post_id}: clipping "
              f"{'excluded' if a.cmd == 'exclude-clipping' else 'included'}. "
              f"Slides re-rendered.")
        return

    # ---- apply an edited document ---------------------------------------
    if a.cmd == "apply-doc":
        from postdoc import apply_markdown, recheck, to_markdown
        p = load(a.post_id)
        if not p:
            raise SystemExit(f"No queued post with id {a.post_id}")
        md_path = QUEUE / f"{a.post_id}.md"
        if not md_path.exists():
            raise SystemExit(f"No document at {md_path}")
        was = p
        before = json.dumps(p, sort_keys=True)
        p = recheck(apply_markdown(p, md_path.read_text()), before=was)
        if json.dumps(p, sort_keys=True) == before:
            print(f"{a.post_id}: the document matches the post already.")
            return
        # RENDER FIRST, SAVE SECOND.
        #
        # The other order looks harmless and is not. rerender() can fail on
        # hand-edited copy in ways lint() never sees - a slide heading renamed
        # in the document becomes the eyebrow, and the eyebrow becomes a
        # filename - and this command runs in a workflow step that tolerates a
        # non-zero exit so a refused edit can be reported rather than going
        # red. Saving first therefore left the NEW copy in the queue file, the
        # OLD images in docs/img, and a comment on the issue saying "your
        # edits are in - the card shows the re-rendered slides". The card
        # would show the new words over the old pictures, and publishing would
        # send the old carousel with the new caption. That is exactly the
        # failure rerender()'s own docstring was written about.
        #
        # Rendering first means a failure here leaves the post, the images and
        # the document all as they were, and the error reaches the issue.
        rerender(p)
        save(p)
        # Rewrite the document FROM the post, so the file always reflects what
        # was actually stored. Without this, a heading the parser ignored
        # would sit in the file looking like it had taken effect.
        md_path.write_text(to_markdown(p))
        blockers = blocking_reasons(p)
        print(f"{a.post_id}: your edits are in. Slides re-rendered.")
        if blockers:
            print("This post is now BLOCKED and needs `force approve`:")
            for b in blockers:
                print(f"  - {b}")
        return

    # ---- revise / revert ------------------------------------------------
    # Both re-render, because the slides are what actually publish. Skipping
    # that is the mistake rerender()'s docstring was written about: the copy
    # changes, the committed JPEGs do not, and Instagram receives the old
    # carousel next to the new caption.
    if a.cmd in ("revise", "revert"):
        from draft import ReviseError, revert_post, revise_post
        from secrets_guard import redact, safe_error
        p = load(a.post_id)
        if not p:
            raise SystemExit(f"No queued post with id {a.post_id}")
        try:
            p = (revise_post(p, a.instruction, force=getattr(a, "force", False))
                 if a.cmd == "revise" else revert_post(p))
        except ReviseError as e:
            # A refused revision is a normal outcome, not a crash: the post is
            # left exactly as it was. Exit non-zero so the workflow surfaces
            # the reason on the issue instead of reporting success.
            raise SystemExit(f"REVISION REFUSED - {redact(e)}")
        except Exception as e:
            # Anything else - an SDK error, a timeout - is reported through
            # safe_error() because this output is tee'd to a log that the
            # workflow pastes into a PUBLIC issue comment. An unwrapped
            # exception from an HTTP client can quote the request it was
            # making, and secrets_guard exists precisely because a quoted
            # request can carry a credential. Actions' own secret masking does
            # not help here: it covers the log stream, not bytes a process
            # writes to a file, and not a REST payload.
            raise SystemExit(f"REVISION FAILED - {safe_error(e)}")
        save(p)
        rerender(p)
        # Rewrite the editable document from the revised post.
        #
        # Without this the .md beside the post still holds the PRE-revision
        # copy: a reviewer who revises and then opens the document to fix one
        # word would commit the old sentences back over the new ones, and the
        # apply would look like it worked. The two representations of a post
        # have to move together or the one nobody re-read wins.
        md_path = QUEUE / f"{a.post_id}.md"
        if md_path.exists():
            from postdoc import to_markdown
            md_path.write_text(to_markdown(p))
        n = len(p.get("revisions") or [])
        print(f"{a.post_id} {'revised' if a.cmd == 'revise' else 'reverted'} "
              f"({n} revision{'' if n == 1 else 's'} on record). Slides re-rendered.")
        forced = (p.get("qa") or {}).get("forced_revision")
        if forced:
            print("FORCED past the checks. This post is now BLOCKED and needs "
                  "`force approve` to publish. What failed:")
            for r in forced:
                print(f"  - {r}")
        return
    if a.cmd == "reject":
        set_status(a.post_id, "rejected", a.note)
        print(f"{a.post_id} rejected.")


if __name__ == "__main__":
    _main()
