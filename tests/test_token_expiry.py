"""
The token that died with the weekly refresh job green.

WHAT HAPPENED (7 Oct 2026): publishing failed with
    OAuthException 190 / subcode 463
    "Session has expired on Wednesday, 07-Oct-26 11:09:56 PDT"

The job that exists to prevent exactly this had run every Sunday and reported
"Token healthy" each time. Instagram-Login tokens are inspected through
graph.instagram.com/me, which does not report an expiry, and the probe filled
in `expires_at = 0` - the same value Facebook uses for a Page token that
genuinely never expires. So days_left was inf, inf is never <= the 20-day
threshold, and the refresh never ran. The alert is wired to `if: failure()`
and nothing ever failed.

Three things had to be true at once, and all three are now tested:
  * "unknown" must not read as "never"
  * an unknown expiry must trigger a refresh rather than a shrug
  * a refresh that leaves the token still dying must FAIL, so the alert fires

    python -m pytest tests/test_token_expiry.py -q
"""

import math
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import auth  # noqa: E402


def _info(expires_at, valid=True, kind="IG_USER"):
    return auth.TokenInfo(valid=valid, kind=kind, app_id="", expires_at=expires_at,
                          data_access_expires_at=0, scopes=[], raw={})


def _needs_refresh(info):
    """The condition ensure() applies."""
    return (not info.expiry_known) or (info.days_left <= auth.REFRESH_THRESHOLD_DAYS)


# ---------------------------------------------------------------------------
# "We do not know" is not "it never expires"
# ---------------------------------------------------------------------------
def test_an_unreported_expiry_is_not_treated_as_immortal():
    assert _info(None).expiry_known is False
    assert _info(None).never_expires is False


def test_a_page_token_with_no_expiry_still_reads_as_never():
    """Facebook really does return 0 for a Page token. That one is genuine."""
    assert _info(0).expiry_known is True
    assert _info(0).never_expires is True
    assert _info(0).days_left == math.inf


def test_an_unknown_expiry_compares_false_in_both_directions():
    """NaN on purpose. An unknown expiry must not slip through a "plenty of
    time left" test, whichever way a future caller writes the comparison."""
    d = _info(None).days_left
    assert math.isnan(d)
    assert not (d > auth.REFRESH_THRESHOLD_DAYS)
    assert not (d < auth.REFRESH_THRESHOLD_DAYS)


def test_the_instagram_login_probe_reports_unknown_not_never(monkeypatch):
    """THE line that caused it: the probe hardcoded expires_at=0."""
    class _R:
        status_code = 200
        @staticmethod
        def json():
            return {"id": "1784...", "username": "onestudytoday"}
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: _R())
    info = auth._probe_instagram_login("tok")
    assert info is not None
    assert info.expiry_known is False, "an IG-Login token still claims immortality"


# ---------------------------------------------------------------------------
# What ensure() decides
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("expires_at,should_refresh,why", [
    (None, True, "unknown expiry - the case that broke"),
    (0, False, "a genuine never-expiring Page token"),
    (int(time.time() + 45 * 86400), False, "45 days left"),
    (int(time.time() + 19 * 86400), True, "inside the 20-day threshold"),
    (int(time.time() - 86400), True, "already expired"),
])
def test_when_a_refresh_is_attempted(expires_at, should_refresh, why):
    assert _needs_refresh(_info(expires_at)) is should_refresh, why


def test_an_unknown_expiry_refreshes_every_run(monkeypatch):
    """Refreshing is idempotent and this runs weekly, so the cost of
    refreshing a token that did not need it is one API call. The cost of not
    refreshing one that did is the account going silent."""
    calls = []
    monkeypatch.setattr(auth, "inspect",
                        lambda tok=None: _info(None if not calls else
                                               int(time.time() + 59 * 86400)))
    monkeypatch.setattr(auth, "refresh",
                        lambda: calls.append(1) or {"access_token": "new",
                                                    "expires_in": 5184000,
                                                    "path": "ig_refresh"})
    monkeypatch.setattr(auth, "verify", lambda t: {"id": "1"})
    monkeypatch.setattr(auth, "persist", lambda t: {"github_secret": True})
    monkeypatch.setattr(auth, "_opt", lambda *a, **k: "")
    out = auth.ensure()
    assert calls, "an unknown expiry did not trigger a refresh"
    assert out["refreshed"] is True


# ---------------------------------------------------------------------------
# A refresh that does not actually help must FAIL, or the alert cannot fire
# ---------------------------------------------------------------------------
def test_a_refresh_that_leaves_the_token_dying_is_an_error(monkeypatch):
    dying = int(time.time() + 3 * 86400)
    monkeypatch.setattr(auth, "inspect", lambda tok=None: _info(dying))
    monkeypatch.setattr(auth, "refresh",
                        lambda: {"access_token": "new", "expires_in": 259200,
                                 "path": "ig_refresh"})
    monkeypatch.setattr(auth, "verify", lambda t: {"id": "1"})
    monkeypatch.setattr(auth, "persist", lambda t: {"github_secret": True})
    monkeypatch.setattr(auth, "_opt", lambda *a, **k: "")
    with pytest.raises(auth.AuthError) as e:
        auth.ensure()
    assert "still expires" in str(e.value)


def test_a_good_refresh_does_not_raise(monkeypatch):
    monkeypatch.setattr(auth, "inspect",
                        lambda tok=None: _info(int(time.time() + 59 * 86400)))
    monkeypatch.setattr(auth, "refresh",
                        lambda: {"access_token": "new", "expires_in": 5184000,
                                 "path": "ig_refresh"})
    monkeypatch.setattr(auth, "verify", lambda t: {"id": "1"})
    monkeypatch.setattr(auth, "persist", lambda t: {"github_secret": True})
    monkeypatch.setattr(auth, "_opt", lambda *a, **k: "")
    auth.ensure(force=True)       # must not raise


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def test_the_status_output_never_says_never_for_an_unknown_expiry():
    text = _info(None).human()
    assert "never" not in text.lower() or "UNKNOWN" in text
    assert "UNKNOWN" in text
