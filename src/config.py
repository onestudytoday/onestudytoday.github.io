"""
Central config. Reads .env locally, environment variables in CI.

Nothing else in the codebase reads os.environ directly, so there is exactly one
place to look when a credential goes missing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
DOCS = ROOT / "docs"
QUEUE = DATA / "queue"
PUBLISHED = DATA / "published"
CACHE = DATA / "cache"

for _p in (DATA, OUT, DOCS, QUEUE, PUBLISHED, CACHE, DOCS / "img"):
    _p.mkdir(parents=True, exist_ok=True)

_ENV_LINE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$")


def load_dotenv(path: Optional[Path] = None) -> None:
    """Load .env into os.environ without clobbering real env vars (CI wins)."""
    p = path or (ROOT / ".env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


load_dotenv()


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(
            f"\nMissing required setting: {name}\n"
            f"Add it to {ROOT / '.env'} (local) or to GitHub > Settings > "
            f"Secrets and variables > Actions (CI).\n"
        )
    return v


def _opt(name: str, default: str = "") -> str:
    """An optional setting, falling back to `default`.

    An env var that exists but is EMPTY counts as unset. That is not
    hypothetical tidiness: GitHub Actions defines the variable with an empty
    value whenever `${{ vars.SOMETHING }}` is undefined or misspelled, so
    `os.environ.get(name, default)` finds the key, skips the default, and
    hands back "". Every caller then silently took a wrong value instead of
    the documented one - and the worst of them flips a guardrail the wrong
    way: `ALLOW_PREPRINTS` unset made `_opt(...).lower() == "true"` False,
    which turns preprints into an automatic REJECT and silently kills every
    Thursday (physics is almost entirely preprints), while the runbook says
    an empty Thursday is normal. Same class of bug for THEME (falls back to
    an unstyled render), HANDLE and GRAPH_VERSION.
    """
    v = os.environ.get(name, "").strip()
    return v if v else default.strip()


def handle() -> str:
    """The @handle, for display purposes only.

    Deliberately does NOT go through settings() / _req(), because settings()
    demands all four Meta/Instagram credentials even though this value never
    touches the Graph API. linkinbio.py used to call settings() just to reach
    s.handle - the exact "required a credential, then never used it" bug
    caption.build_caption() had (see tests/test_copy.py) - and the CI step
    that rebuilds the link-in-bio page (scheduled-publish.yml) only ever
    passes HANDLE into its env, not the Meta secrets, so that call raised
    "Missing required setting: META_APP_ID" and failed the job on every
    single run, whether or not anything was even queued to publish.
    """
    return _opt("HANDLE", "@onestudytoday")


def anthropic_key() -> str:
    """The Anthropic API key, or "" if unset.

    Standalone for the same reason as handle() above: interest.py is imported
    by sources.py, which runs in contexts that have no Meta credentials at
    all (`python src/sources.py <niche>` locally, and any future workflow
    step that only sources candidates). Reaching this through settings()
    would demand all four Graph API secrets to decide whether we can rank
    candidates - the same "required a credential, then never used it" shape
    that broke linkinbio.build() on every CI run.

    Returns "" rather than raising: interest ranking is an ENHANCEMENT. With
    no key the pipeline still sources, vets, drafts and publishes exactly as
    it did before, just without the model's opinion on what is interesting.
    """
    return _opt("ANTHROPIC_API_KEY")


def draft_model() -> str:
    """The model id used for drafting and interest scoring. See anthropic_key()."""
    return _opt("DRAFT_MODEL", "claude-sonnet-4-5")


@dataclass(frozen=True)
class Settings:
    # --- Meta / Instagram
    meta_app_id: str
    meta_app_secret: str
    ig_access_token: str
    ig_business_account_id: str
    graph_version: str

    # --- Drafting
    anthropic_api_key: str
    draft_model: str

    # --- Publishing / hosting
    public_image_base: str      # e.g. https://<user>.github.io/<repo>/img
    github_repo: str            # "owner/repo", used for secret write-back
    github_pat: str             # fine-grained PAT with Secrets: write

    # --- Behaviour
    theme: str
    handle: str
    recency_days: int
    allow_preprints: bool
    timezone: str

    @property
    def graph(self) -> str:
        return f"https://graph.facebook.com/{self.graph_version}"


def settings() -> Settings:
    return Settings(
        meta_app_id=_req("META_APP_ID"),
        meta_app_secret=_req("META_APP_SECRET"),
        ig_access_token=_req("IG_ACCESS_TOKEN"),
        ig_business_account_id=_req("IG_BUSINESS_ACCOUNT_ID"),
        graph_version=_opt("GRAPH_VERSION", "v23.0"),
        anthropic_api_key=_opt("ANTHROPIC_API_KEY"),
        draft_model=_opt("DRAFT_MODEL", "claude-sonnet-4-5"),
        public_image_base=_opt("PUBLIC_IMAGE_BASE"),
        github_repo=_opt("GITHUB_REPOSITORY"),
        github_pat=_opt("GH_PAT"),
        theme=_opt("THEME", "neon"),
        handle=_opt("HANDLE", "@onestudytoday"),
        # NOTE: config/niches.yaml `defaults.recency_days` is the authoritative
        # publication window - it is what sources.fetch_candidates and vet()
        # actually read. This field is a legacy override that nothing currently
        # consumes; the fallback is kept in step with the YAML so that if
        # something ever does start reading it, it does not silently reimpose
        # a fortnight-long window that vet() would then enforce as STALE.
        recency_days=int(_opt("RECENCY_DAYS", "75")),
        allow_preprints=_opt("ALLOW_PREPRINTS", "true").lower() == "true",
        timezone=_opt("TZ", "America/Chicago"),
    )
