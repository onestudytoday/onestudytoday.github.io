"""
"Which weekday is it" tests. GitHub Actions runners are UTC; the account's
documented operating timezone is America/Chicago (docs/GROWTH.md, config.py's
TZ setting). Before 21 Aug 2026, todays_niche() used naive date.today(), i.e.
UTC - so any manual trigger after ~7pm Central was already reading as the
next calendar day and silently drafting the wrong weekday's niche.

    python -m pytest tests/ -q
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline import todays_niche  # noqa: E402


def test_late_evening_central_is_still_the_same_day():
    # 20 Aug 2026, 19:56 America/Chicago -> already 21 Aug in UTC.
    # This is the exact mixup that produced two wildcard drafts in a row.
    chicago_evening = datetime(2026, 8, 20, 19, 56, tzinfo=ZoneInfo("America/Chicago"))
    utc_equivalent = chicago_evening.astimezone(ZoneInfo("UTC"))
    assert chicago_evening.date().isoformat() == "2026-08-20"   # Thursday
    assert utc_equivalent.date().isoformat() == "2026-08-21"    # already Friday in UTC

    assert todays_niche(d=chicago_evening.date()) == "physics"   # Thursday
    assert todays_niche(d=utc_equivalent.date()) == "wildcard"   # Friday - the bug


def test_todays_niche_uses_the_configured_timezone_by_default():
    # Without forcing `d`, todays_niche() should resolve "today" through the
    # given tz rather than the process's local/UTC clock.
    now_chicago = datetime.now(ZoneInfo("America/Chicago"))
    expected = {0: "nature", 1: "psych", 2: "health",
                3: "physics", 4: "wildcard"}.get(now_chicago.weekday())
    assert todays_niche(tz="America/Chicago") == expected


def test_weekend_is_still_none():
    saturday = datetime(2026, 8, 22)  # a Saturday
    assert todays_niche(d=saturday.date()) is None
