"""
Keep a permanent record of how each post actually performed.

WHY THIS EXISTS
===============
`publish.insights()` has been able to fetch reach / likes / comments / saved /
shares / profile_visits since the account started. Nothing ever kept the
answer. `pick_friday_source_niche()` calls it, scores a 7-day window, picks a
niche and throws every number away - so the account has been making decisions
from data it immediately forgets, and there is no way to answer any question
that spans more than the last week.

Two of those questions are live right now:

1. **"Is 07:00 actually peak for this account?"** `PUBLISH_TIMES` came out of
   docs/GROWTH.md, which was written before the account had a single follower.
   It is a generic best-practice guess that has never once been checked against
   this audience. It might be right. Nobody knows, and nobody CAN know without
   a history.

2. **"Which post should I put the ad budget behind?"** The plan is to spend a
   month's budget boosting the single best organic performer. That requires
   knowing which post that was - and specifically which one drove PROFILE
   VISITS, not which one collected the most likes.

THE DESIGN
==========
Append-only JSONL, one row per (post, age bucket). Append-only matters: this
file is written by a workflow that runs while other workflows are also
committing, and appended lines merge far more cleanly than a rewritten JSON
object (see scheduled-publish.yml's reconcile script for what the alternative
costs).

Snapshots at +24h and +72h. Instagram keeps surfacing a post for days, so a
single reading taken at an arbitrary moment compares nothing to nothing; two
fixed ages make posts comparable to each other.

`age_hours` is recorded alongside the nominal bucket ON PURPOSE. If the
collector does not run for a while, a post can be 90 hours old the first time
its "24h" snapshot is taken. That row is not wrong, but it is not comparable
either, and analysis has to be able to tell the difference. Recording only the
bucket would have quietly poisoned every comparison later.

Nothing here can affect publishing. It runs in its own workflow, it never
raises, and a total API failure produces zero rows rather than a broken run.

    python src/metrics.py collect     # take any snapshots that are due
    python src/metrics.py report      # what the history says so far
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from config import DATA, PUBLISHED
from secrets_guard import safe_error

LEDGER = DATA / "metrics.jsonl"

# Ages, in hours, at which a post is measured.
SNAPSHOT_HOURS = (24, 72)

# A snapshot taken more than this long after its nominal age is flagged
# `late`, because the collector did not run on time. Still recorded - a late
# reading is better than no reading - but excluded from comparisons.
LATE_TOLERANCE_HOURS = 12

# The operating timezone. Publish-hour analysis is meaningless in UTC because
# the slots the account actually targets are wall-clock Central.
TZ = ZoneInfo("America/Chicago")

# Below this many posts in a group, the report refuses to rank it. The whole
# point of this module is to stop guessing; printing a "winner" chosen from
# two data points would just be guessing with extra steps.
MIN_N_FOR_A_CLAIM = 5

METRIC_FIELDS = ("reach", "likes", "comments", "saved", "shares",
                 "profile_visits")


# ---------------------------------------------------------------------------
# Reading and writing the ledger
# ---------------------------------------------------------------------------
def rows() -> List[Dict[str, Any]]:
    """Every recorded snapshot. A corrupt line is skipped, not fatal."""
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _already_recorded() -> Set[Tuple[str, int]]:
    return {(r.get("post_id"), r.get("bucket_hours"))
            for r in rows()
            if r.get("post_id") and r.get("bucket_hours")}


def append(row: Dict[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Working out what is due
# ---------------------------------------------------------------------------
def _published_at(post: Dict[str, Any]) -> Optional[datetime]:
    at = (post.get("published") or {}).get("at")
    if not at:
        return None
    try:
        return datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def load_published() -> List[Dict[str, Any]]:
    out = []
    for f in sorted(PUBLISHED.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out


def due(posts: List[Dict[str, Any]],
        now: Optional[datetime] = None) -> List[Tuple[Dict[str, Any], int, float]]:
    """(post, bucket, actual_age_hours) for every snapshot not yet taken."""
    now = now or datetime.utcnow()
    done = _already_recorded()
    jobs = []
    for p in posts:
        when = _published_at(p)
        media_id = (p.get("published") or {}).get("media_id")
        if not when or not media_id:
            continue
        age = (now - when).total_seconds() / 3600.0
        for bucket in SNAPSHOT_HOURS:
            if age >= bucket and (p.get("id"), bucket) not in done:
                jobs.append((p, bucket, age))
    return jobs


# ---------------------------------------------------------------------------
def collect(now: Optional[datetime] = None,
            fetch=None) -> Dict[str, Any]:
    """Take every snapshot that is due. Never raises.

    `fetch` is injectable so the tests never touch the network.
    """
    now = now or datetime.utcnow()
    if fetch is None:                                     # pragma: no cover
        from publish import insights as fetch

    posts = load_published()
    jobs = due(posts, now)
    written, failed = [], []

    for post, bucket, age in jobs:
        media_id = (post.get("published") or {}).get("media_id")
        try:
            m = fetch(media_id)
        except Exception as e:
            failed.append({"post_id": post.get("id"), "bucket_hours": bucket,
                           "error": safe_error(e)})
            continue
        if not isinstance(m, dict) or "error" in m:
            # Do NOT write a row. A row of zeroes is indistinguishable from a
            # post that genuinely got no engagement, and it would be permanent.
            failed.append({"post_id": post.get("id"), "bucket_hours": bucket,
                           "error": (m or {}).get("error") if isinstance(m, dict)
                                    else "insights returned a non-dict"})
            continue

        when = _published_at(post)
        local = when.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
        row = {
            "post_id": post.get("id"),
            "media_id": media_id,
            "niche": post.get("niche"),
            "kind": (post.get("published") or {}).get("kind", "CAROUSEL"),
            "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "published_hour_ct": local.hour,
            "published_weekday_ct": local.strftime("%a"),
            "bucket_hours": bucket,
            "age_hours": round(age, 2),
            "late": age > bucket + LATE_TOLERANCE_HOURS,
            "captured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for k in METRIC_FIELDS:
            v = m.get(k)
            row[k] = int(v) if isinstance(v, (int, float)) else None
        append(row)
        written.append(row)

    return {"due": len(jobs), "written": len(written), "failed": failed,
            "rows": written}


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------
def engagement_rate(row: Dict[str, Any]) -> Optional[float]:
    """Saves and shares weighted hardest, matching what Instagram rewards.

    Sends/shares are the top-weighted ranking signal as of 2026, with saves
    and comments behind them; likes are close to noise. Same weighting
    `pick_friday_source_niche()` already uses, kept identical on purpose so
    the two never disagree about what "good" means.
    """
    reach = row.get("reach")
    if not reach:
        return None
    n = lambda k: row.get(k) or 0          # noqa: E731
    return ((n("saved") * 3 + n("shares") * 3 + n("comments") * 2 + n("likes"))
            / reach)


def _median(xs: List[float]) -> float:
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def summarise(bucket: int = 72, key: str = "published_hour_ct",
              include_late: bool = False) -> Dict[Any, Dict[str, Any]]:
    """Group the history by `key` and report n / median engagement."""
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows():
        if r.get("bucket_hours") != bucket:
            continue
        if r.get("late") and not include_late:
            continue
        groups.setdefault(r.get(key), []).append(r)

    out = {}
    for k, rs in groups.items():
        rates = [e for e in (engagement_rate(r) for r in rs) if e is not None]
        out[k] = {
            "n": len(rs),
            "median_engagement": round(_median(rates), 4) if rates else None,
            "median_reach": _median([r["reach"] for r in rs
                                     if r.get("reach")]) if rs else None,
            "enough_data": len(rs) >= MIN_N_FOR_A_CLAIM,
        }
    return out


def best_post_to_boost(bucket: int = 72,
                       include_late: bool = False) -> Optional[Dict[str, Any]]:
    """The post to put the ad budget behind.

    Ranked on PROFILE VISITS, not likes and not the engagement rate. The
    purpose of boosting is to convert strangers into followers, so the post
    worth paying to show more people is the one that already proved it makes
    people tap through to the profile. The most-liked post is frequently not
    that post.

    Late rows are excluded by the same rule summarise() uses. They were
    originally included here, which meant the report could print "nothing
    measured at this age yet" for the hour breakdown and then, two lines
    later, confidently name a post to spend money on - chosen from exactly
    the readings it had just declared unusable. Either late data is
    comparable or it is not; it cannot be both.
    """
    best, best_v = None, -1
    for r in rows():
        if r.get("bucket_hours") != bucket:
            continue
        if r.get("late") and not include_late:
            continue
        v = r.get("profile_visits")
        if v is None:
            continue
        if v > best_v:
            best, best_v = r, v
    return best


def report(bucket: int = 72) -> str:
    rs = rows()
    if not rs:
        return ("No metrics recorded yet.\n"
                "Snapshots are taken at +24h and +72h after a post publishes, "
                "so the first rows appear a day after the next publish.")

    at_bucket = [r for r in rs if r.get("bucket_hours") == bucket]
    late_only = bool(at_bucket) and all(r.get("late") for r in at_bucket)

    lines = [f"{len(rs)} snapshots recorded, "
             f"{len({r['post_id'] for r in rs})} posts.", ""]
    if late_only:
        lines.append(
            f"All {len(at_bucket)} readings at +{bucket}h were taken more than "
            f"{LATE_TOLERANCE_HOURS}h late, so they are not comparable with "
            f"each other and nothing below is ranked from them. This means the "
            f"collector was not running when it should have been.")
        lines.append("")

    for key, label in (("published_hour_ct", "publish hour (Central)"),
                       ("niche", "niche")):
        lines.append(f"--- {label}, at +{bucket}h ---")
        s = summarise(bucket, key)
        if not s:
            lines.append("  nothing comparable measured at this age yet")
            lines.append("")
            continue
        # Numeric keys (the publish hour) sort numerically; string keys (the
        # niche) sort alphabetically. Sorting everything as a string put 12
        # before 7, which reads as a bug the first time you see it.
        def _order(kv):
            k = kv[0]
            return (0, k, "") if isinstance(k, (int, float)) else (1, 0, str(k))

        for k, v in sorted(s.items(), key=_order):
            flag = "" if v["enough_data"] else \
                f"   (n<{MIN_N_FOR_A_CLAIM}: not enough to conclude anything)"
            lines.append(f"  {str(k):>10}  n={v['n']:<3} "
                         f"median engagement={v['median_engagement']}{flag}")
        if not any(v["enough_data"] for v in s.values()):
            lines.append(f"  -> Every group is under {MIN_N_FOR_A_CLAIM} posts. "
                         f"Do not read a winner into this yet.")
        lines.append("")

    b = best_post_to_boost(bucket)
    if b:
        lines.append("--- best candidate to put ad budget behind ---")
        lines.append(f"  {b['post_id']}  profile_visits={b['profile_visits']} "
                     f"reach={b.get('reach')}")
        lines.append("  (ranked on profile visits, not likes: the point of "
                     "boosting is follower conversion)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def _main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "report"
    if cmd == "collect":
        out = collect()
        printable = {k: v for k, v in out.items() if k != "rows"}
        print(json.dumps(printable, indent=2))
        for r in out["rows"]:
            print(f"  recorded {r['post_id']} @ +{r['bucket_hours']}h: "
                  f"reach={r['reach']} saved={r['saved']} shares={r['shares']} "
                  f"profile_visits={r['profile_visits']}")
        # Deliberately exit 0 even with failures. This job must never be the
        # reason a red X appears next to the repo; the failures are printed,
        # and a persistent one shows up as a gap in the history.
        return 0
    if cmd == "report":
        print(report())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(_main(sys.argv))
