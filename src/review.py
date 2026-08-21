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
    from config import settings
    d = OUT / "posts" / post["id"]
    paths = render_post(post, settings().theme, str(d))
    contact_sheet(paths, str(OUT / "posts" / f"SHEET_{post['id']}.png"))
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
    save(post)
    if status == "rejected":
        # This is what "kill ... never source that study again" (issue.py,
        # docs/RUNBOOK.md) actually depends on. Without it, killing a post
        # from the GitHub comment gate - the only way this happens in
        # production - never touched the ledger, so the same study was free
        # to be sourced and drafted all over again on the next run.
        from sources import load_ledger, save_ledger, study_key
        led = load_ledger()
        led.setdefault("rejected", {})[study_key(post)] = {
            "title": post["study"].get("title", "")[:160],
            "doi": post["study"].get("doi", ""),
            "reason": note or "killed by reviewer"}
        save_ledger(led)
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
    if a.cmd == "reject":
        set_status(a.post_id, "rejected", a.note)
        print(f"{a.post_id} rejected.")


if __name__ == "__main__":
    _main()
