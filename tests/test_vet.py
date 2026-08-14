"""
Guardrail tests. These run offline against hand-built fixtures, so you can
prove the safety rules work without touching a single API.

    python -m pytest tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sources import Study                      # noqa: E402
from vet import check_draft, vet               # noqa: E402


def mk(**kw) -> Study:
    base = dict(
        source="europepmc", ext_id="MED:1", title="A study of things",
        abstract="x" * 900, journal="Nature", pub_date="2026-08-10",
        doi="10.1000/abc", niche="psych",
    )
    base.update(kw)
    return Study(**base)


# ---------------------------------------------------------------- hard gates
def test_retraction_is_hard_reject():
    s = mk(retraction_notices=["Retraction of Publication"])
    r = vet(s, "psych", deep=False)
    assert r.verdict == "REJECT"
    assert any(f.code == "RETRACTED" for f in r.flags)


def test_predatory_publisher_is_hard_reject():
    s = mk(publisher="OMICS International", journal="Journal of Everything")
    r = vet(s, "psych", deep=False)
    assert r.verdict == "REJECT"
    assert any(f.code == "PREDATORY_PUBLISHER" for f in r.flags)


def test_no_doi_and_no_url_is_hard_reject():
    s = mk(doi="", url="")
    r = vet(s, "psych", deep=False)
    assert r.verdict == "REJECT"


def test_stale_study_is_hard_reject():
    s = mk(pub_date="2019-01-01")
    r = vet(s, "psych", deep=False, recency_days=14)
    assert r.verdict == "REJECT"


def test_thin_abstract_is_hard_reject():
    s = mk(abstract="Too short.")
    r = vet(s, "psych", deep=False)
    assert r.verdict == "REJECT"


# ------------------------------------------------------------ preprint gate
def test_preprint_forces_badge_and_caveat():
    s = mk(is_preprint=True, server="bioRxiv", journal="bioRxiv")
    r = vet(s, "psych", deep=False, allow_preprints=True)
    assert r.verdict == "HOLD"
    assert "PREPRINT" in r.required_badges
    assert any("not been peer reviewed" in c for c in r.required_caveats)
    assert any("PREPRINT" in d for d in r.draft_rules)


def test_preprint_hard_rejected_in_strict_mode():
    s = mk(is_preprint=True, server="bioRxiv")
    r = vet(s, "psych", deep=False, allow_preprints=False)
    assert r.verdict == "REJECT"


# -------------------------------------------------- correlation vs causation
OBS_ABSTRACT = (
    "In this prospective cohort study we followed 42,000 adults from the UK Biobank "
    "for nine years. Higher coffee consumption was associated with a lower incidence "
    "of type 2 diabetes. Participants completed a food frequency questionnaire at "
    "baseline. This observational design cannot establish causality, though the "
    "association persisted after adjustment for smoking, body mass index and "
    "physical activity. " + "Further detail. " * 40
)


def test_observational_design_forces_correlation_caveat():
    s = mk(abstract=OBS_ABSTRACT, title="Coffee intake and diabetes risk in 42,000 adults")
    r = vet(s, "health", deep=False)
    assert "observational" in r.design
    assert any(f.code == "CORRELATIONAL" for f in r.flags)
    assert any("pattern, not a cause" in c for c in r.required_caveats)
    assert any("Causal verbs are banned" in d for d in r.draft_rules)


def test_draft_check_catches_causal_verb_on_observational_study():
    s = mk(abstract=OBS_ABSTRACT, title="Coffee intake and diabetes risk")
    r = vet(s, "health", deep=False)
    bad = check_draft("Drinking coffee causes a big drop in diabetes risk.", r)
    assert any("causal_verb_on_observational" in v for v in bad)
    good = check_draft("People who drank more coffee were less likely to develop diabetes.", r)
    assert good == []


# --------------------------------------------------------------- animal gate
MOUSE_ABSTRACT = (
    "We administered the compound to mice and measured hippocampal neurogenesis. "
    "Treated mice showed improved performance in the Morris water maze. "
    "We then repeated the experiment in rats. " + "Method detail. " * 60
)


def test_animal_study_forces_species_caveat():
    s = mk(abstract=MOUSE_ABSTRACT, title="Compound X improves memory in mice")
    r = vet(s, "psych", deep=False)
    assert r.subjects == "non-human"
    assert "NOT_IN_HUMANS" in r.required_badges
    assert any("not humans" in c.lower() or "not humans" in c for c in r.required_caveats)


def test_draft_check_catches_human_claim_on_animal_study():
    s = mk(abstract=MOUSE_ABSTRACT, title="Compound X improves memory in mice")
    r = vet(s, "psych", deep=False)
    bad = check_draft("This could sharpen your memory — people who take it remember more.", r)
    assert any("human_claim_on_animal" in v for v in bad)
    ok = check_draft("Mice given the compound remembered the maze better.", r)
    assert ok == []


# ---------------------------------------------------------------- sample size
def test_tiny_human_sample_is_flagged_with_number():
    ab = ("We recruited 18 participants for this within-subjects experiment. "
          "Participants were randomly assigned to condition order. " + "Detail. " * 90)
    s = mk(abstract=ab, title="A small experiment in 18 participants")
    r = vet(s, "psych", deep=False)
    assert r.sample_size == 18
    assert any(f.code == "TINY_SAMPLE" for f in r.flags)
    assert any("18 people" in c for c in r.required_caveats)


def test_large_sample_boosts_score():
    big = ("This prospective cohort analysed n = 412000 adults. " + "Detail. " * 90)
    small = ("This prospective cohort analysed n = 40 adults. " + "Detail. " * 90)
    rb = vet(mk(abstract=big), "health", deep=False)
    rs = vet(mk(abstract=small), "health", deep=False)
    assert rb.score > rs.score


# --------------------------------------------------------- relative risk gate
def test_relative_only_effect_is_flagged():
    ab = ("Treatment reduced events with a hazard ratio of 0.62, a 38% reduction "
          "in the primary endpoint. " + "Detail. " * 90)
    s = mk(abstract=ab)
    r = vet(s, "health", deep=False)
    assert any(f.code == "RELATIVE_RISK_ONLY" for f in r.flags)


def test_absolute_numbers_clear_the_relative_flag():
    ab = ("Treatment reduced events with a hazard ratio of 0.62; the absolute risk "
          "difference was 1.2 percentage points. " + "Detail. " * 90)
    r = vet(mk(abstract=ab), "health", deep=False)
    assert not any(f.code == "RELATIVE_RISK_ONLY" for f in r.flags)


# ------------------------------------------------------------------- scoring
def test_flagship_journal_scores_above_unlisted():
    good = vet(mk(journal="Nature"), "psych", deep=False)
    meh = vet(mk(journal="Journal of Unlisted Studies"), "psych", deep=False)
    assert good.score > meh.score


def test_watchlist_journal_loses_points_and_holds():
    r = vet(mk(journal="Medical Hypotheses"), "psych", deep=False)
    assert any(f.code == "WATCHLIST_JOURNAL" for f in r.flags)
    assert r.verdict in ("HOLD", "REJECT")


def test_industry_funding_is_flagged():
    s = mk(funders=["Novartis Pharmaceuticals Corporation"])
    r = vet(s, "health", deep=False)
    assert any(f.code == "INDUSTRY_FUNDING" for f in r.flags)


def test_hype_and_proof_claims_are_caught_in_draft():
    r = vet(mk(), "psych", deep=False)
    bad = check_draft("A revolutionary breakthrough that proves the theory.", r)
    assert any("hype" in v for v in bad)
    assert any("proof_claim" in v for v in bad)


def test_clean_rct_passes():
    ab = ("In this double-blind placebo-controlled randomised controlled trial, "
          "n = 2400 adults were randomly assigned to treatment or placebo. The "
          "absolute risk difference was 3.1 percentage points. " + "Detail. " * 80)
    s = mk(abstract=ab, journal="The Lancet", niche="health")
    r = vet(s, "health", deep=False)
    assert r.verdict == "PASS"
    assert r.design == "randomised trial"
