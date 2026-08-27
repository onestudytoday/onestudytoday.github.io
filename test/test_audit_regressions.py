"""
Regression tests from the 24 Aug 2026 full-codebase audit.

Consolidated into one file deliberately: every fix has to be hand-uploaded
through the GitHub web UI, so this is one upload instead of five.

What the audit found, and what each section below pins:

  A. THE REPUBLISH LOOP (critical). Publishing to Instagram is irreversible the
     moment the Graph API accepts it, but the record that it happened was only
     written to the runner's disk and committed several steps later. Any
     failure in between - a second post erroring, the link-in-bio rebuild, a
     push rejected because an approval landed mid-run - threw that record away
     while the carousel stayed live, so the next 15-minute poll published the
     identical carousel again, every 15 minutes, until the daily quota ran out.

  B. _opt() DEFAULTS NEVER APPLIED to anything wired to `${{ vars.X }}`.
     Actions defines the env var with an EMPTY value when the variable is
     undefined or misspelled, so os.environ.get(name, default) found the key
     and skipped the default. ALLOW_PREPRINTS unset therefore meant "reject all
     preprints", silently killing every Thursday.

  C. SAMPLE-SIZE DETECTION was wrong in the two directions that make a small
     study look big - it could not see 1-2 digit counts at all, and it took
     max() across all matches so an unrelated larger number won.

  D. FORCED CAVEATS were detected and then ignored by the approve gate, which
     only counted "GUARDRAIL"-prefixed lint errors. Skeleton placeholder posts
     were approvable too.

  E. TOKEN REFRESH reported success when the secret write-back failed, so the
     weekly job went green while the token quietly aged out.

    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import auth                                       # noqa: E402
import config                                     # noqa: E402
import pipeline                                   # noqa: E402
import publish as publish_mod                      # noqa: E402
import review                                     # noqa: E402
import sources                                    # noqa: E402
from draft import flatten                          # noqa: E402
from sources import Study                          # noqa: E402
from vet import VetReport, check_draft, detect_sample_size  # noqa: E402


# ===========================================================================
# A. The republish loop
# ===========================================================================
def _post(pid="2026-08-24-nature-aaaaaaaa", niche="nature", key="k-aaa"):
    return {"id": pid, "status": "approved", "niche": niche,
            "study": {"key": key, "doi": f"10.1000/{pid}", "title": "t"}}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A scratch queue/published/docs/ledger, wired into every module."""
    queue = tmp_path / "queue"
    published = tmp_path / "published"
    docs = tmp_path / "docs"
    for d in (queue, published, docs):
        d.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "QUEUE", queue)
    monkeypatch.setattr(pipeline, "PUBLISHED", published)
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "OUT", tmp_path / "out")
    monkeypatch.setattr(sources, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(publish_mod, "public_urls",
                        lambda jpegs, pid: [f"https://x.test/{pid}/{j.name}" for j in jpegs])
    return {"queue": queue, "published": published, "docs": docs, "tmp": tmp_path}


def _stage(docs, pid, n=5):
    d = docs / "img" / pid
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"{i:02d}_slide.jpg").write_bytes(b"jpeg")


def test_already_published_sees_the_published_record(wired):
    p = _post()
    (wired["published"] / f"{p['id']}.json").write_text(
        json.dumps({**p, "published": {"media_id": "MEDIA123"}}))
    assert pipeline.already_published(p) == "MEDIA123"


def test_already_published_sees_the_ledger_entry(wired):
    p = _post()
    sources.LEDGER.write_text(json.dumps(
        {"posted": {"k-aaa": {"doi": "d", "media_id": "MEDIA456"}},
         "rejected": {}, "seen": {}}))
    assert pipeline.already_published(p) == "MEDIA456"


def test_a_fresh_post_is_not_seen_as_published(wired):
    assert pipeline.already_published(_post()) is None


def test_publish_refuses_to_repost_and_clears_the_stale_queue_file(wired, monkeypatch):
    """The exact loop: the record survived, the queue file did not get cleaned."""
    p = _post()
    _stage(wired["docs"], p["id"])
    (wired["published"] / f"{p['id']}.json").write_text(
        json.dumps({**p, "published": {"media_id": "MEDIA123"}}))
    qf = wired["queue"] / f"{p['id']}.json"
    qf.write_text(json.dumps(p))

    called = []
    monkeypatch.setattr(publish_mod, "publish",
                        lambda post, urls, live: called.append(post["id"]) or {})

    res = pipeline._publish_one(qf, p, live=True)

    assert called == [], "must not call the Graph API for an already-published post"
    assert res.get("skipped") == "already_published"
    assert not qf.exists(), "the stale queue file must be cleared or it loops forever"


def test_one_failing_post_does_not_abort_the_ones_that_succeeded(wired, monkeypatch):
    good, bad = _post("2026-08-24-nature-aaaaaaaa", key="k-aaa"), _post("2026-08-24-nature-bbbbbbbb", key="k-bbb")
    for p in (good, bad):
        _stage(wired["docs"], p["id"])
        (wired["queue"] / f"{p['id']}.json").write_text(json.dumps(p))

    def fake_publish(post, urls, live):
        if post["id"] == bad["id"]:
            raise publish_mod.PublishError("transient Graph API container error")
        return {"media_id": "MEDIA_GOOD", "mode": "LIVE"}
    monkeypatch.setattr(publish_mod, "publish", fake_publish)

    # The batch still fails loudly - but only AFTER recording what worked.
    with pytest.raises(SystemExit):
        pipeline.publish_approved(live=True)

    assert (wired["published"] / f"{good['id']}.json").exists(), \
        "the successful post's record must be written before the batch fails"
    assert not (wired["queue"] / f"{good['id']}.json").exists(), \
        "the successful post's queue file must be gone"
    led = json.loads(sources.LEDGER.read_text())
    assert "k-aaa" in led["posted"]


def test_the_full_republish_scenario_cannot_happen_twice(wired, monkeypatch):
    """End-to-end repro of the incident this whole fix exists for.

    Run 1: post A publishes, post B fails, the job dies before committing.
    Run 2 (15 minutes later, same repo state on disk): A must NOT go out again.
    """
    a, b = _post("2026-08-24-nature-aaaaaaaa", key="k-aaa"), _post("2026-08-24-nature-bbbbbbbb", key="k-bbb")
    for p in (a, b):
        _stage(wired["docs"], p["id"])
        (wired["queue"] / f"{p['id']}.json").write_text(json.dumps(p))

    sent = []

    def fake_publish(post, urls, live):
        sent.append(post["id"])
        if post["id"] == b["id"]:
            raise publish_mod.PublishError("boom")
        return {"media_id": "MEDIA_A", "mode": "LIVE"}
    monkeypatch.setattr(publish_mod, "publish", fake_publish)

    with pytest.raises(SystemExit):
        pipeline.publish_approved(live=True)
    assert sent == [a["id"], b["id"]]

    # Run 2. B is still queued and still fails; A must be left alone.
    sent.clear()
    with pytest.raises(SystemExit):
        pipeline.publish_approved(live=True)
    assert a["id"] not in sent, "post A was published to Instagram a SECOND time"
    assert sent == [b["id"]]


# ===========================================================================
# B. _opt() and empty-vs-unset
# ===========================================================================
def test_empty_env_var_falls_back_to_the_documented_default(monkeypatch):
    # Actions sets the var to "" when ${{ vars.X }} is undefined/misspelled.
    monkeypatch.setenv("THEME", "")
    assert config._opt("THEME", "neon") == "neon"


def test_allow_preprints_unset_does_not_silently_reject_every_preprint(monkeypatch):
    monkeypatch.setenv("ALLOW_PREPRINTS", "")
    val = config._opt("ALLOW_PREPRINTS", "true").lower() == "true"
    assert val is True, "an empty ALLOW_PREPRINTS must mean the default (allow), not reject"


def test_a_real_value_still_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("THEME", "editorial")
    assert config._opt("THEME", "neon") == "editorial"


def test_explicitly_turning_preprints_off_is_still_honoured(monkeypatch):
    # The fix must not make the setting unturnoffable - an explicit "false"
    # still has to mean false.
    monkeypatch.setenv("ALLOW_PREPRINTS", "false")
    assert config._opt("ALLOW_PREPRINTS", "true") == "false"
    assert (config._opt("ALLOW_PREPRINTS", "true").lower() == "true") is False


# ===========================================================================
# C. Sample-size detection
# ===========================================================================
def _study(abstract):
    return Study(source="europepmc", ext_id="MED:1", title="", abstract=abstract,
                 journal="Nature", pub_date="2026-08-20", doi="10.1/x")


@pytest.mark.parametrize("abstract,expected", [
    ("A total of 18 patients completed the protocol.", 18),
    ("We enrolled 24 participants in a crossover trial.", 24),
    ("Twenty-six adults (n=26) took part.", 26),
    ("A cohort of 45,231 adults was followed for 10 years.", 45231),
    ("No participant count is stated anywhere.", None),
])
def test_small_participant_counts_are_visible(abstract, expected):
    # The old regex needed 3+ characters in the number, so the entire
    # "human sample under 30" guardrail was blind to the studies it exists for.
    assert detect_sample_size(_study(abstract)) == expected


def test_an_unrelated_larger_number_cannot_displace_the_participant_count():
    # Old behaviour: max() across all matches -> 12400, no TINY_SAMPLE flag,
    # a large-sample credibility bonus, and "sample size: 12400" fed to the
    # drafting model as fact about a 26-person study.
    s = _study("We enrolled 26 participants; we analysed 12400 brain images.")
    assert detect_sample_size(s) == 26


def test_participant_count_wins_even_when_stated_after_the_larger_number():
    s = _study("We analysed 12400 brain images from 26 participants.")
    assert detect_sample_size(s) == 26


@pytest.mark.parametrize("abstract,expected", [
    # A subgroup stated alongside the total must not become "the" sample size.
    ("Follow-up was available for 24 participants at 6 months out of the "
     "310 participants randomised.", 310),
    ("Discovery cohort (n=28) and replication cohort (n=1,204).", 1204),
    ("A pilot of 15 and a confirmatory trial of 612 participants.", 612),
])
def test_a_subgroup_count_does_not_shrink_the_study(abstract, expected):
    # Guards the OVER-correction. Taking the first match instead of the
    # largest within a pattern called a 310-person trial a 24-person one -
    # and because a missing forced caveat now BLOCKS approval, that would have
    # made "only 24 people took part" a mandatory sentence on a post about a
    # 310-person randomised trial. Priority across patterns, max within one.
    assert detect_sample_size(_study(abstract)) == expected


@pytest.mark.parametrize("abstract", [
    "Between 2017 and 2019, patients were screened for eligibility.",
    "Doses were given (n = 20, 22, and 24) across arms.",
])
def test_numbers_that_are_not_sample_sizes_are_not_read_as_one(abstract):
    # A year read as a participant count, or a comma-separated list spanned
    # into one number ("20, 22" -> 2022), both inflate n and suppress the
    # small-sample guardrail.
    assert detect_sample_size(_study(abstract)) is None


# ===========================================================================
# C2. The claim checks that were silently disabled
# ===========================================================================
def _animal_post():
    return {"cover": {"kicker": "k",
                      "headline": "Great news for people who want better sleep"},
            "slides": [{"eyebrow": "The setup", "title": "t", "body": "b"}],
            "caveats": ["This study was done in mice, not humans. "
                        "It may not apply to people."],
            "cta": {"headline": "h", "sub": "s"}, "caption": "c"}


def test_the_forced_species_caveat_no_longer_disables_the_animal_claim_gate():
    # human_claim_on_animal fires only when NO species word appears in the text
    # it is given. The pipeline FORCES a caveat naming the species onto the
    # fine-print slide - so feeding it the whole post meant the mandated caveat
    # supplied the species word itself and switched the gate off. The better a
    # post complied with the caveat rule, the more completely the check that
    # protects its cover slide was disabled.
    rep = VetReport(key="x")
    rep.subjects = "non-human"
    post = _animal_post()
    errs = check_draft(
        flatten(post, include_cta=False), rep,
        claim_text=flatten(post, include_cta=False, include_caveats=False))
    assert any("human_claim_on_animal" in e for e in errs)


def _vet_study(title, abstract, niche="health"):
    from vet import vet as _vet
    s = Study(source="europepmc", ext_id="MED:1", title=title,
              abstract=abstract + " " + "x" * 600, journal="Nature",
              pub_date="2026-08-20", doi="10.1/x")
    return _vet(s, niche, allow_preprints=True, recency_days=14)


def test_vet_actually_arms_the_causal_flag_for_a_non_observational_string():
    # THE POINT of the causal_language_banned field: the "causal verbs are
    # banned in every slide" rule is injected for designs that do NOT end in
    # "(observational)" - mendelian randomisation above all - which used to
    # get the prompt instruction and no code enforcement whatsoever.
    #
    # This asserts against vet() itself, not a hand-set flag. An earlier
    # version of this test set rep.causal_language_banned = True by hand and
    # therefore still passed with the line that sets it deleted from vet.py -
    # pinning nothing at all.
    rep = _vet_study("Mendelian randomisation of coffee and sleep",
                     "A mendelian randomisation study of coffee intake and "
                     "sleep quality in a large cohort.")
    assert rep.causal_language_banned is True
    errs = check_draft("Coffee causes better sleep in adults.", rep)
    assert any("causal_verb_on_observational" in e for e in errs)


def test_a_meta_analysis_of_rcts_is_not_causally_gagged():
    # The flag must NOT be armed by the wider "observational markers present"
    # half of that branch. Words like "association" and "cohort" appear in
    # abstracts of designs that are not observational at all, and a
    # meta-analysis of randomised trials is the most causally authoritative
    # thing this pipeline can source. Blocking "reduces" there would be wrong
    # on the science AND would report "the study design is observational"
    # about an RCT - and would make `force approve` a daily reflex.
    rep = _vet_study("Meta-analysis of fourteen randomised trials",
                     "A meta-analysis of fourteen randomised controlled trials "
                     "examined the association between interval training and "
                     "HbA1c across a large cohort of adults.")
    assert rep.causal_language_banned is False
    errs = check_draft("Interval training reduces your risk of complications.", rep)
    assert not any("causal_verb_on_observational" in e for e in errs)


@pytest.mark.parametrize("species,copy_text", [
    # The species list used to be ten hard-coded PLURAL words, so removing the
    # caveats from the checked text turned legitimate copy into a blocker:
    # "a mouse model" does not contain "mice", and cattle, cell lines and
    # non-human primates had no entry at all.
    (["mice"], "This could help people. The mouse brain changed in a mouse model."),
    (["cattle"], "This matters for people. The cattle showed less inflammation."),
    (["cell lines"], "What this means for you: the cell lines responded strongly."),
    (["pigs"], "Good news for people. Each pig cleared the virus faster."),
])
def test_copy_that_does_name_its_species_is_not_blocked(species, copy_text):
    rep = VetReport(key="x")
    rep.subjects = "non-human"
    rep.species = species
    errs = check_draft(copy_text, rep, claim_text=copy_text)
    assert not any("human_claim_on_animal" in e for e in errs)


def test_copy_that_names_no_species_at_all_is_still_blocked():
    rep = VetReport(key="x")
    rep.subjects = "non-human"
    rep.species = ["cattle"]
    txt = "What this means for you and your family."
    errs = check_draft(txt, rep, claim_text=txt)
    assert any("human_claim_on_animal" in e for e in errs)


def test_vet_records_the_species_it_detected():
    rep = _vet_study("Porcine deltacoronavirus nucleocapsid protein",
                     "Porcine deltacoronavirus infection was studied in "
                     "porcine cell cultures to determine the mechanism.")
    assert rep.subjects == "non-human"
    assert rep.species, "the animal-claim gate depends on this being populated"


def test_flatten_can_exclude_caveats():
    post = _animal_post()
    assert "mice" in flatten(post)
    assert "mice" not in flatten(post, include_caveats=False)


# ===========================================================================
# D. The approve gate
# ===========================================================================
def _reviewable(**over):
    p = {"id": "2026-08-24-health-cccccccc", "status": "needs_review",
         "study": {"doi": "10.1/x", "title": "t"},
         "cover": {"kicker": "k", "headline": "A real finding, plainly stated"},
         "slides": [{"eyebrow": "The setup", "title": "t", "body": "b"}],
         "caveats": ["Only 24 people took part, so this may not generalise."],
         "cta": {"headline": "h", "sub": "s"}, "caption": "c",
         "qa": {"lint_errors": [], "blocking_claims": [], "unverified_numbers": []},
         "vet": {"verdict": "PASS"}}
    p.update(over)
    return p


def test_a_missing_forced_caveat_now_blocks_approval():
    # draft.lint() emits this as a PLAIN error; blocking_reasons() only counted
    # "GUARDRAIL"-prefixed ones, so every forced caveat except the preprint one
    # was detected and then ignored, and a plain `approve` published it.
    post = _reviewable(qa={
        "lint_errors": ["required caveat not represented: 'This study was done "
                        "in mice, not humans...'"],
        "blocking_claims": [], "unverified_numbers": []})
    reasons = review.blocking_reasons(post)
    assert any("forced caveat" in r.lower() for r in reasons)


def test_a_clean_post_is_still_approvable():
    assert review.blocking_reasons(_reviewable()) == []


def test_a_hold_verdict_is_still_approvable_by_design():
    # HOLD is the NORMAL state for this account - preprints, observational
    # designs, non-human subjects and small samples all set it, which is most
    # posts (the first post ever published was HOLD). Making it blocking would
    # mean force-approving daily, which would train the override into a reflex
    # and destroy its meaning. Documented as advisory; pinned so nobody
    # "fixes" it into a blocker later without reading this.
    assert review.blocking_reasons(_reviewable(vet={"verdict": "HOLD"})) == []


def test_an_unwritten_skeleton_draft_cannot_be_approved():
    # skeleton() is the fallback when ANTHROPIC_API_KEY is missing. Every field
    # is literal instruction text, and nothing in the gate looked at whether
    # the copy had actually been written - so an unnoticed missing API key
    # would have opened a normal-looking review issue whose approval published
    # template text.
    post = _reviewable(
        cover={"kicker": "k", "headline": "**WRITE THE HOOK.** One sentence."},
        slides=[{"eyebrow": "The setup", "title": "WRITE: why does this matter?",
                 "body": "WRITE 55-90 words in two paragraphs."}],
        caption="WRITE 70-140 words. End with the study link.")
    assert any("skeleton" in r.lower() for r in review.blocking_reasons(post))


# ===========================================================================
# E. Token refresh must fail loudly
# ===========================================================================
def _fake_token_info(days_left=5.0, valid=True):
    """A real TokenInfo (ensure() calls asdict() on it) inside the refresh window."""
    import time as _time
    return auth.TokenInfo(
        valid=valid, kind="USER", app_id="123",
        expires_at=int(_time.time() + days_left * 86400),
        data_access_expires_at=int(_time.time() + days_left * 86400),
        scopes=["instagram_basic"], raw={})


def test_ensure_raises_when_the_github_secret_writeback_failed(monkeypatch):
    # persist() swallows every write-back exception and returns
    # github_secret=False. ensure() used to return normally, so the workflow
    # went green every Sunday while IG_ACCESS_TOKEN still held the OLD token -
    # and the alert issue, wired to `if: failure()`, could never fire.
    monkeypatch.setenv("GITHUB_REPOSITORY", "onestudytoday/onestudytoday.github.io")
    monkeypatch.setattr(auth, "inspect", lambda tok=None: _fake_token_info())
    monkeypatch.setattr(auth, "refresh", lambda tok=None: {"access_token": "NEW", "path": "test"})
    monkeypatch.setattr(auth, "verify", lambda tok=None: {"ok": True})
    monkeypatch.setattr(auth, "persist", lambda tok: {"dotenv": True, "github_secret": False})

    with pytest.raises(auth.AuthError) as e:
        auth.ensure()
    assert "GH_PAT" in str(e.value)


def test_ensure_succeeds_when_the_secret_was_actually_written(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "onestudytoday/onestudytoday.github.io")
    monkeypatch.setattr(auth, "inspect", lambda tok=None: _fake_token_info())
    monkeypatch.setattr(auth, "refresh", lambda tok=None: {"access_token": "NEW", "path": "test"})
    monkeypatch.setattr(auth, "verify", lambda tok=None: {"ok": True})
    monkeypatch.setattr(auth, "persist", lambda tok: {"dotenv": False, "github_secret": True})

    out = auth.ensure()
    assert out["refreshed"] is True


def test_local_run_without_a_repo_still_accepts_a_dotenv_only_write(monkeypatch):
    # On a laptop there is no GITHUB_REPOSITORY and .env IS the real store.
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(auth, "inspect", lambda tok=None: _fake_token_info())
    monkeypatch.setattr(auth, "refresh", lambda tok=None: {"access_token": "NEW", "path": "test"})
    monkeypatch.setattr(auth, "verify", lambda tok=None: {"ok": True})
    monkeypatch.setattr(auth, "persist", lambda tok: {"dotenv": True, "github_secret": False})

    assert auth.ensure()["refreshed"] is True


# ===========================================================================
# F. The workflow guarantees these fixes depend on
# ===========================================================================
def _scheduled_publish():
    import yaml
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "scheduled-publish.yml").read_text())


def test_scheduled_publish_commits_even_when_an_earlier_step_failed():
    steps = _scheduled_publish()["jobs"]["publish"]["steps"]
    commit = next(s for s in steps if s.get("name") == "Commit the result")
    assert commit.get("if") == "always()", \
        "without always(), a post can go live and its record be discarded"
    assert "git merge" in commit["run"], \
        "a push rejected by a concurrent approval must reconcile and retry"


def test_the_push_retry_can_survive_a_ledger_conflict():
    # data/ledger.json is written by every workflow, and while it is small the
    # edits land on adjacent lines and cannot auto-merge. A plain
    # `git pull --rebase` aborts there - losing the publish record and
    # duplicating a live post - so the retry has to resolve it, not give up.
    commit = next(s for s in _scheduled_publish()["jobs"]["publish"]["steps"]
                  if s.get("name") == "Commit the result")
    run = commit["run"]
    assert "reconcile.py" in run
    assert "MERGE_HEAD" in run, "the ledger must be union-merged, not clobbered"
    assert "kept our deletion" in run, \
        "a queue file we deleted because we published it must stay deleted"


def test_scheduled_publish_has_a_failure_alarm():
    # A red run in the Actions tab is not a notification, and this is the one
    # workflow whose silent failure can put the same carousel out twice.
    steps = _scheduled_publish()["jobs"]["publish"]["steps"]
    alert = [s for s in steps if s.get("if") == "failure()"]
    assert alert, "scheduled-publish.yml must open an issue when publishing fails"
    assert "issues.create" in alert[0]["with"]["script"]


def test_commit_step_cannot_mistake_a_failed_commit_for_nothing_to_commit():
    # `git commit ... || echo "nothing to commit"` turns a genuine commit
    # failure into a green step that silently discards the publish record -
    # the same silent-loss class this whole fix exists to prevent.
    run = next(s for s in _scheduled_publish()["jobs"]["publish"]["steps"]
               if s.get("name") == "Commit the result")["run"]
    assert "git diff --cached --quiet" in run, \
        "emptiness must be tested explicitly, not inferred from commit failing"


def test_checkout_is_deep_enough_to_rebase():
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "scheduled-publish.yml").read_text())
    co = next(s for s in wf["jobs"]["publish"]["steps"]
              if str(s.get("uses", "")).startswith("actions/checkout"))
    assert co.get("with", {}).get("fetch-depth") == 0


def test_token_refresh_can_actually_open_its_alert_issue():
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "token-refresh.yml").read_text())
    assert wf["permissions"].get("issues") == "write", \
        "the failure alarm calls issues.create; without this scope it 403s"
