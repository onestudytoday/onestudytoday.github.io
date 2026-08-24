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

from config import DOCS, OUT, PUBLISHED, QUEUE, settings
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
# Approving a post (the GitHub comment gate) only ever sets status="approved".
# It used to publish in the same breath, which meant the post went out at
# whatever time you happened to review it - usually right at the 6am draft
# slot, not when your audience is actually around. These two functions are
# what decouple "you decided yes" from "it actually goes out":
#
#   publish_approved()   publishes every approved post right now, regardless
#                         of time. The manual override - what
#                         `workflow_dispatch` on scheduled-publish.yml runs,
#                         and what "python src/pipeline.py publish-approved
#                         --live" always did.
#   publish_scheduled()  publishes an approved post only once its niche's
#                         peak-engagement time (docs/GROWTH.md) has arrived in
#                         the configured timezone. This is what the frequent
#                         cron in scheduled-publish.yml runs. It is timezone-
#                         aware the same way todays_niche() is, so it is not
#                         vulnerable to the UTC-vs-Chicago mixup that hit the
#                         drafting side.
#
# Both funnel through _publish_one() so there is exactly one place that knows
# how to actually turn an approved post into a live Instagram post.
# ---------------------------------------------------------------------------
PUBLISH_TIMES = {
    # America/Chicago wall-clock time of day, from docs/GROWTH.md's weekly
    # rhythm table. Wildcard uses Friday's slot regardless of which niche it
    # was actually sourced from.
    "nature": "07:00", "psych": "07:00", "health": "12:00",
    "physics": "09:00", "wildcard": "09:00",
}


def _publish_one(f: Path, post: Dict[str, Any], live: bool) -> Dict[str, Any]:
    from publish import public_urls, publish, stage_images

    # docs/img/<id>/*.jpg is what daily-draft.yml's "Stage slide JPEGs for
    # GitHub Pages" step already produced and committed, right after
    # rendering, in that same run. It is the ONLY copy of the slides that
    # survives to reach a publish run days later on a different runner:
    # out/posts/ is gitignored working-file scratch (see .gitignore -
    # "Rendered PNGs are working files"), regenerated fresh each time
    # `pipeline.py run` renders, and gone from every checkout after that.
    # Checking out/posts/<id>/ here - as this used to, unconditionally -
    # meant every post silently "had no rendered slides" on any publish run
    # that did not happen to share a runner with the draft that made it,
    # which in practice is every scheduled-publish.yml run there has ever
    # been. Prefer the already-staged JPEGs; only fall back to converting
    # fresh PNGs for the same-session local flow (`pipeline.py run` followed
    # immediately by `publish-approved` in one shell).
    staged = sorted((DOCS / "img" / post["id"]).glob("*.jpg"))
    if staged:
        urls = public_urls(staged, post["id"])
    else:
        pngs = sorted(str(p) for p in (OUT / "posts" / post["id"]).glob("*.png"))
        if not pngs:
            print(f"  {post['id']}: no rendered slides, skipping")
            return {}
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
    return res


def publish_approved(live: bool = False) -> List[Dict[str, Any]]:
    """Publish every approved post right now, ignoring peak-time gating."""
    n, results = 0, []
    for f in sorted(QUEUE.glob("*.json")):
        post = json.loads(f.read_text())
        if post.get("status") != "approved":
            continue
        results.append(_publish_one(f, post, live))
        n += 1
    if not n:
        print("Nothing approved is waiting.")
    return results


def publish_scheduled(live: bool = True, tz: str = "America/Chicago",
                      _now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Publish approved posts whose niche's peak-time slot has arrived.

    Meant to run often (every ~15 minutes, see scheduled-publish.yml) rather
    than at one exact cron time, specifically so it never depends on GitHub's
    UTC cron surviving a daylight-saving transition. A post approved well
    before its slot just waits; one approved late is published on the very
    next poll instead of being silently skipped.
    """
    now = _now or datetime.now(ZoneInfo(tz))
    n, results = 0, []
    for f in sorted(QUEUE.glob("*.json")):
        post = json.loads(f.read_text())
        if post.get("status") != "approved":
            continue
        target = PUBLISH_TIMES.get(post.get("niche", ""))
        if target:
            th, tm = (int(x) for x in target.split(":"))
            target_dt = now.replace(hour=th, minute=tm, second=0, microsecond=0)
            if now < target_dt:
                continue   # not this post's turn yet
        results.append(_publish_one(f, post, live))
        n += 1
    if not n:
        print("Nothing is both approved and past its scheduled slot yet.")
    return results


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

    ps = sub.add_parser("publish-scheduled")
    ps.add_argument("--live", action="store_true")

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
    elif a.cmd == "publish-scheduled":
        publish_scheduled(a.live)
    elif a.cmd == "linkinbio":
        from linkinbio import build
        print(build())


if __name__ == "__main__":
    _main()
