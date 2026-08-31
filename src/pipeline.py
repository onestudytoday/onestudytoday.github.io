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
from config import _opt as _opt
from draft import draft_post, skeleton
from render import contact_sheet, render_post
from sources import Study, fetch_candidates, load_ledger, mark_seen, save_ledger, study_key
from vet import default_recency_days, vet
from publish import PublishError
from secrets_guard import safe_error
from reel import DEFAULT_BG, ReelError, build_reel, reel_public_url


# ---------------------------------------------------------------------------
# Which weekdays go out as a Reel instead of a carousel.
#
# Not "both": a Reel and a carousel of the same study on the same day is
# duplicate content on the profile, and - per Meta's own docs - Reels publish
# through the same /media_publish edge and consume the same 100-per-24h quota,
# so it costs two of them to say one thing.
#
# The default is the Friday wildcard, because docs/LAUNCH.md already ranks
# "The Friday Reel" as the #2 source of the first hundred followers and the
# only format that reaches non-followers in volume at a standing start.
#
# Set REEL_NICHES to a comma-separated list to change it
# ("nature,psych,health,physics,wildcard" makes every day a Reel).
#
# To turn Reels OFF, set it to the literal "off" (or "none") - NOT to an empty
# string. That is not a quirk worth hiding: config._opt deliberately treats an
# empty env var as unset, because GitHub Actions sets `${{ vars.X }}` to "" for
# any variable that is undefined or misspelled, and every caller that took ""
# at face value silently got the wrong behaviour (the ALLOW_PREPRINTS bug that
# killed every Thursday). Under those semantics an empty REEL_NICHES means
# "unset", which means the default, which means Reels stay ON - so an explicit
# sentinel is the only honest way to express "off".
_REELS_OFF = {"off", "none", "no", "false", "0"}


def reel_niches() -> set:
    raw = _opt("REEL_NICHES", "wildcard").strip()
    if raw.lower() in _REELS_OFF:
        return set()
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


def wants_reel(niche: Optional[str]) -> bool:
    return bool(niche) and niche.lower() in reel_niches()

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
def run(niche: Optional[str] = None, days: Optional[int] = None, limit: int = 1,
        use_api: bool = True, dry_source: bool = False) -> List[Dict[str, Any]]:
    # None, not a literal. The publication window is defined once, in
    # config/niches.yaml, and vet() rejects anything past it as STALE. When
    # this signature carried its own default of 14, widening the window in the
    # YAML changed which studies were FETCHED but not which were ACCEPTED:
    # the daily draft would source three months of candidates and then hard-
    # reject every one over a fortnight old, for a completely silent empty run.
    if days is None:
        days = default_recency_days()
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

        # Build the Reel HERE, at draft time, not at publish time. Instagram
        # fetches video_url with its own crawler, so the file must already be
        # committed and already served by Pages before publishing names that
        # URL. See build_reel()'s docstring. Never fatal: a post with no Reel
        # still publishes perfectly well as a carousel.
        if wants_reel(niche):
            try:
                info = build_reel(post["id"], bg=post.get("theme", {}).get("bg")
                                  or DEFAULT_BG, images=[Path(p) for p in paths])
                post["reel"] = {"path": info["path"], "duration": info["duration"],
                                "bytes": info["bytes"]}
                print(f"           -> reel {info['duration']}s "
                      f"{info['bytes'] / 1e6:.1f}MB")
            except Exception as e:
                print(f"           ! reel build failed, will publish as a "
                      f"carousel instead: {e}")

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


def already_published(post: Dict[str, Any]) -> Optional[str]:
    """The media_id if this post has already gone live, else None.

    THE reason this exists: publishing to Instagram is irreversible the
    instant the Graph API accepts it, but the only record that it happened is
    a `git push` that runs several steps LATER in scheduled-publish.yml. Every
    one of these ordinary events discards that record while the carousel stays
    live:

      * a second approved post in the same batch fails to publish (the
        exception used to abort the whole run, see publish_scheduled);
      * the link-in-bio rebuild fails;
      * `git push` is rejected as non-fast-forward because publish-on-approve
        pushed an approval while this job was running.

    In each case the next 15-minute poll checks out a repo where the post is
    still status="approved" with its queue file intact - and, before this
    guard, cheerfully published the identical carousel again. And again, every
    15 minutes, until the daily quota ran out.

    So the publish path now asks "did I already do this?" before it acts, and
    answers from the two records that outlive a single runner: the committed
    data/published/<id>.json, and the ledger's "posted" map.
    """
    p = PUBLISHED / f"{post['id']}.json"
    if p.exists():
        try:
            prev = json.loads(p.read_text())
            return (prev.get("published") or {}).get("media_id") or "unknown"
        except Exception:
            return "unknown"      # unreadable but present: still do not repost
    try:
        led = load_ledger()
    except Exception:
        return None
    entry = (led.get("posted") or {}).get(study_key(post))
    if isinstance(entry, dict):
        return entry.get("media_id") or "unknown"
    return None


def _publish_one(f: Path, post: Dict[str, Any], live: bool) -> Dict[str, Any]:
    from publish import public_urls, publish, stage_images

    seen = already_published(post)
    if seen:
        print(f"  {post['id']}: already published (media_id {seen}) - "
              f"NOT publishing again")
        # The queue file is the thing that keeps re-offering it. Clearing it
        # is what actually breaks the loop, so do it on the live path even
        # though nothing was sent.
        if live and f.exists():
            f.unlink()
        return {"post_id": post["id"], "skipped": "already_published",
                "media_id": seen}

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
    # Reel or carousel. The Reel decision comes FIRST, before slide resolution,
    # because a Reel does not need the carousel JPEGs at all.
    #
    # It used to come after, and that was a silent permanent stall: run() builds
    # reel.mp4 into docs/img/<id>/, CREATING that directory, while the JPEGs are
    # staged by a separate later workflow step that is skipped whenever
    # out/posts/<id>/ is empty. A post could therefore have a committed Reel and
    # no JPEGs - and the `return {}` below would fire first, every 15 minutes,
    # forever, exiting 0 with no alert and never closing the review issue.
    reel_file = DOCS / "img" / post["id"] / "reel.mp4"
    if wants_reel(post.get("niche")) and reel_file.exists():
        from publish import publish_reel
        video_url = reel_public_url(post["id"], settings().public_image_base)
        res = publish_reel(post, video_url, live=live)
    else:
        staged = sorted((DOCS / "img" / post["id"]).glob("*.jpg"))
        if staged:
            urls = public_urls(staged, post["id"])
        else:
            pngs = sorted(str(p) for p in (OUT / "posts" / post["id"]).glob("*.png"))
            if not pngs:
                # RAISE, do not `return {}`. A bare return made this a success
                # as far as _publish_batch was concerned: exit 0, no ids, no
                # `if: failure()` alert, queue file untouched - so the run went
                # green while the post was permanently unpublishable and
                # retried on every poll. A post stuck approved-and-unshippable
                # has to be loud.
                raise PublishError(
                    f"{post['id']}: no slides in docs/img/{post['id']}/ and no "
                    f"PNGs in out/posts/{post['id']}/ - nothing to publish. The "
                    f"draft run staged nothing, so this post can never publish "
                    f"until it is re-rendered.")
            urls = stage_images(post, pngs)
        if wants_reel(post.get("niche")):
            print(f"  {post['id']}: no reel.mp4 committed, publishing as a carousel")
        res = publish(post, urls, live=live)
    print(json.dumps(res, indent=2))

    # A LIVE result with no media_id means the publish call came back 200 with
    # a body we did not understand. Instagram may well have accepted it. The
    # old code let that fall straight through the `if live and media_id` below,
    # writing no published record and no ledger entry, so the next poll
    # republished it - the exact 24 Aug bug, reachable through a new door.
    if live and res.get("mode") == "LIVE" and not res.get("media_id"):
        raise PublishError(
            f"{post['id']}: publish returned no media_id. The post MAY ALREADY "
            f"BE LIVE on Instagram. Check the account before re-running; if it "
            f"is live, record it by hand in data/published/{post['id']}.json so "
            f"the next scheduled run does not post it a second time.")

    if live and res.get("media_id"):
        post["status"] = "published"
        post["published"] = {
            "media_id": res["media_id"],
            "kind": res.get("kind", "CAROUSEL"),
            "at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
        (PUBLISHED / f"{post['id']}.json").write_text(json.dumps(post, indent=2))
        f.unlink()
        led = load_ledger()
        led.setdefault("posted", {})[study_key(post)] = {
            "doi": post["study"]["doi"], "media_id": res["media_id"]}
        save_ledger(led)

        # Crosspost LAST, and only after every record above is on disk.
        #
        # Ordering is the whole point. By this line the post is irreversibly
        # live on Instagram, and the files that prove it have been written. An
        # exception escaping here would abort the run AFTER that - which is
        # precisely the shape that republished the same carousel every 15
        # minutes before the 24 Aug fixes. crosspost_all() is written never to
        # raise; the try/except is the second lock on the same door.
        try:
            from crosspost import crosspost_all
            cp = crosspost_all(post, live=live)
            for r in cp["posted"]:
                print(f"  crossposted to {r['platform']}")
            for r in cp["skipped"]:
                print(f"  {r['platform']} not configured: {r['reason']}")
            for r in cp["failed"]:
                print(f"  ! {r['platform']} crosspost FAILED: {r.get('error')}")
            res["crosspost"] = cp

            # Persist the outcome. Without this the crosspost result existed
            # only as three print lines: res was already printed above and the
            # published record already written, so nothing machine-readable
            # survived. A crosspost that FAILED was then unrecoverable -
            # already_published() short-circuits every later run, so it would
            # never be retried and its absence would never be noticed.
            #
            # Safe to write here precisely because everything irreversible and
            # everything load-bearing is already on disk: this rewrite can fail
            # without costing the Instagram record.
            post["crosspost"] = {
                "at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "posted": [r["platform"] for r in cp["posted"]],
                "skipped": [r["platform"] for r in cp["skipped"]],
                "failed": {r["platform"]: r.get("error") for r in cp["failed"]},
            }
            (PUBLISHED / f"{post['id']}.json").write_text(json.dumps(post, indent=2))

            # A failed crosspost is not worth failing the run over, but it must
            # not be invisible either - an annotation surfaces in the Actions
            # UI without touching the exit code.
            for r in cp["failed"]:
                print(f"::warning title=Crosspost failed::"
                      f"{r['platform']} for {post['id']}: {r.get('error')}")
        except Exception as e:                                # pragma: no cover
            print(f"  ! crosspost step failed (post is live and recorded): "
                  f"{safe_error(e)}")
    return res


def _publish_batch(jobs: List[tuple], live: bool) -> List[Dict[str, Any]]:
    """Run _publish_one over several posts, isolating each one's failures.

    Without this, a PublishError on the SECOND post propagated out of the
    process after the FIRST was already live on Instagram - killing the
    workflow step, skipping the commit, and losing the only record that post
    one had gone out. One transient Graph API hiccup was therefore enough to
    duplicate a different, perfectly successful post.

    So: every post gets its own try/except, every success is recorded
    immediately, and the process only exits non-zero at the very END, once all
    the bookkeeping is safely on disk for the commit step to pick up. Failing
    loudly still matters - you want the red run - but it must not cost the
    posts that worked.
    """
    results, failures = [], []
    for f, post in jobs:
        try:
            results.append(_publish_one(f, post, live))
        except Exception as e:
            failures.append((post.get("id", "?"), e))
            print(f"  ! {post.get('id', '?')}: publish failed: {e}")
    if failures:
        print(f"\n{len(failures)} post(s) failed to publish:")
        for pid, e in failures:
            print(f"   - {pid}: {type(e).__name__}: {e}")
        print("Posts that succeeded above HAVE been recorded and will be "
              "committed. Nothing will be republished on the next run.")
        raise SystemExit(1)
    return results


def publish_approved(live: bool = False) -> List[Dict[str, Any]]:
    """Publish every approved post right now, ignoring peak-time gating."""
    jobs = []
    for f in sorted(QUEUE.glob("*.json")):
        post = json.loads(f.read_text())
        if post.get("status") != "approved":
            continue
        jobs.append((f, post))
    if not jobs:
        print("Nothing approved is waiting.")
        return []
    return _publish_batch(jobs, live)


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
    jobs = []
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
        jobs.append((f, post))
    if not jobs:
        print("Nothing is both approved and past its scheduled slot yet.")
        return []
    return _publish_batch(jobs, live)


# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--niche", choices=list(set(WEEKDAY_NICHE.values())))
    # No default= here either: None reaches run(), which resolves it from
    # config/niches.yaml. See the comment in run().
    r.add_argument("--days", type=int, default=None,
                   help="Publication window in days (default: "
                        "defaults.recency_days in config/niches.yaml)")
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
