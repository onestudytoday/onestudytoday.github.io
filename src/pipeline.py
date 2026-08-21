"""
The orchestrator. One command per day.

    python src/pipeline.py run                # today's niche, end to end
    python src/pipeline.py run --niche psych  # force a niche
    python src/pipeline.py render <post-id>   # re-render after an edit
    python src/pipeline.py publish-approved   # dry run
    python src/pipeline.py publish-approved --live
    python src/pipeline.py linkinbio          # rebuild the bio page

`run` does: source -> vet -> draft -> lint -> audit -> render -> queue.
It stops there. Publishing is a separate, deliberate step.

Friday's niche is chosen by engagement: whichever field's posts performed best
over the previous seven days wins the slot.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import OUT, PUBLISHED, QUEUE, settings
from draft import draft_post, skeleton
from render import contact_sheet, render_post
from sources import Study, fetch_candidates, load_ledger, mark_seen, save_ledger, study_key
from vet import vet

WEEKDAY_NICHE = {0: "nature", 1: "psych", 2: "health", 3: "physics", 4: "wildcard"}


def todays_niche(d: Optional[date] = None, tz: str = "America/Chicago") -> Optional[str]:
    """Which weekday's niche "today" is, in the configured operating
    timezone - NOT the CI runner's UTC clock.

    GitHub Actions runners are UTC. Without this, any manual trigger after
    roughly 7pm Central already reads as tomorrow (UTC has rolled over while
    it's still today locally), so it silently drafted the wrong weekday's
    niche. That's what actually produced the wildcard/wildcard repeat on
    20-21 Aug 2026 - it looked like a re-sourcing bug from the ledger side,
    but the pipeline had genuinely already moved on to "Friday" a few hours
    early each time.
    """
    if d is None:
        try:
            d = datetime.now(ZoneInfo(tz)).date()
        except Exception:
            d = datetime.now(ZoneInfo("America/Chicago")).date()
    return WEEKDAY_NICHE.get(d.weekday())


# ---------------------------------------------------------------------------
def pick_friday_source_niche() -> str:
    """Whichever niche performed best in the last 7 days gets Friday."""
    from publish import insights
    scores: Dict[str, float] = {}
    cutoff = datetime.utcnow() - timedelta(days=7)
    for f in PUBLISHED.glob("*.json"):
        p = json.loads(f.read_text())
        pub = (p.get("published") or {})
        if not pub.get("media_id"):
            continue
        try:
            when = datetime.strptime(pub["at"], "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue
        if when < cutoff:
            continue
        m = insights(pub["media_id"])
        if "error" in m:
            continue
        # saves and shares are the strongest signals for this format
        e = (m.get("saved", 0) * 3 + m.get("shares", 0) * 3
             + m.get("comments", 0) * 2 + m.get("likes", 0)) / max(1, m.get("reach", 1))
        scores[p["niche"]] = scores.get(p["niche"], 0) + e
    if not scores:
        # no data yet - rotate deterministically by ISO week
        order = ["psych", "nature", "health", "physics"]
        return order[date.today().isocalendar()[1] % 4]
    best = max(scores, key=scores.get)
    print(f"  Friday engagement ranking: "
          f"{ {k: round(v, 4) for k, v in sorted(scores.items(), key=lambda x: -x[1])} }")
    return best


# ---------------------------------------------------------------------------
def run(niche: Optional[str] = None, days: int = 14, limit: int = 1,
        use_api: bool = True, dry_source: bool = False) -> List[Dict[str, Any]]:
    s = settings()
    niche = niche or todays_niche(tz=s.timezone or "America/Chicago")
    if not niche:
        print("Weekend. Nothing scheduled.")
        return []

    source_niche = niche
    if niche == "wildcard":
        source_niche = pick_friday_source_niche()
        print(f"Friday wildcard -> sourcing from '{source_niche}'")

    print(f"Sourcing candidates for {source_niche} (last {days} days)...")
    cands = fetch_candidates(source_niche, days,
                             include_preprints=s.allow_preprints)
    print(f"  {len(cands)} candidates after dedupe and topic filters")
    if dry_source:
        for c in cands[:15]:
            print(f"   - [{c.journal}] {c.title[:95]}")
        return []

    led = load_ledger()
    made: List[Dict[str, Any]] = []

    for st in cands:
        rep = vet(st, source_niche, allow_preprints=s.allow_preprints,
                  recency_days=days)
        tag = f"{rep.verdict:6} {rep.score:3}/100"
        if rep.verdict == "REJECT":
            print(f"  {tag}  REJECTED  {st.title[:70]}")
            for f in rep.flags:
                if f.severity == "hard":
                    print(f"           ! {f.code}: {f.message[:110]}")
            led.setdefault("rejected", {})[st.key] = {
                "title": st.title[:160], "doi": st.doi,
                "reasons": [f.code for f in rep.flags if f.severity == "hard"]}
            continue

        print(f"  {tag}  DRAFTING  {st.title[:70]}")
        try:
            post = draft_post(st, rep) if (use_api and s.anthropic_api_key) \
                else skeleton(st, rep)
        except Exception as e:
            print(f"           ! drafting failed: {e}")
            continue

        st.niche = niche              # publish under the weekday's niche colour
        post["niche"] = niche
        post["source_niche"] = source_niche

        d = OUT / "posts" / post["id"]
        paths = render_post(post, s.theme, str(d))
        contact_sheet(paths, str(OUT / "posts" / f"SHEET_{post['id']}.png"))
        (QUEUE / f"{post['id']}.json").write_text(json.dumps(post, indent=2))

        qa = post["qa"]
        print(f"           -> queued {post['id']}  "
              f"lint={len(qa['lint_errors'])} "
              f"blocking={len(qa['blocking_claims'])} "
              f"publishable={qa['publishable']}")
        # Mark it seen so a re-run before you approve/kill this one doesn't
        # pull and draft the same study a second time (see fetch_candidates).
        mark_seen(led, st)
        made.append(post)
        if len(made) >= limit:
            break

    save_ledger(led)
    if not made:
        print("\nNothing made it through. Widen --days or loosen config/niches.yaml.")
    else:
        print(f"\n{len(made)} post(s) queued. Review them:\n"
              f"   python src/review.py serve   ->  http://localhost:8765")
    return made


# ---------------------------------------------------------------------------
def publish_approved(live: bool = False) -> None:
    from publish import publish, stage_images
    from review import load as load_post
    n = 0
    for f in sorted(QUEUE.glob("*.json")):
        post = json.loads(f.read_text())
        if post.get("status") != "approved":
            continue
        pngs = sorted(str(p) for p in (OUT / "posts" / post["id"]).glob("*.png"))
        if not pngs:
            print(f"  {post['id']}: no rendered slides, skipping")
            continue
        urls = stage_images(post, pngs)
        res = publish(post, urls, live=live)
        print(json.dumps(res, indent=2))
        if live and res.get("media_id"):
            post["status"] = "published"
            post["published"] = {
                "media_id": res["media_id"],
                "at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            (PUBLISHED / f"{post['id']}.json").write_text(json.dumps(post, indent=2))
            f.unlink()
            led = load_ledger()
            led.setdefault("posted", {})[study_key(post)] = {
                "doi": post["study"]["doi"], "media_id": res["media_id"]}
            save_ledger(led)
        n += 1
    if not n:
        print("Nothing approved is waiting.")


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--niche", choices=list(set(WEEKDAY_NICHE.values())))
    r.add_argument("--days", type=int, default=14)
    r.add_argument("--limit", type=int, default=1)
    r.add_argument("--no-api", action="store_true", help="use the hand-fill skeleton")
    r.add_argument("--sources-only", action="store_true",
                   help="just list what the sourcing step finds")

    rr = sub.add_parser("render")
    rr.add_argument("post_id")

    pa = sub.add_parser("publish-approved")
    pa.add_argument("--live", action="store_true")

    sub.add_parser("linkinbio")

    a = ap.parse_args()

    if a.cmd == "run":
        run(a.niche, a.days, a.limit, use_api=not a.no_api,
            dry_source=a.sources_only)
    elif a.cmd == "render":
        from review import load as load_post, rerender
        p = load_post(a.post_id)
        if not p:
            raise SystemExit("not in queue")
        print("\n".join(rerender(p)))
    elif a.cmd == "publish-approved":
        publish_approved(a.live)
    elif a.cmd == "linkinbio":
        from linkinbio import build
        print(build())


if __name__ == "__main__":
    _main()
