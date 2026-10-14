"""
Is the paper actually about the niche?

THE BUG THESE EXIST FOR, observed on three consecutive Tuesdays: the psych
slot drafted three papers from Bioactive Materials - a ferroptotic tumour
nanogel, collagen hydrogel yarns, and an eDNA/H2S biofilm biohybrid. The
slide said PSYCHOLOGY & NEUROSCIENCE over a cancer nanomedicine paper.

Two causes, stacked. `europepmc_query` listed bare terms with no field
prefix, and Europe PMC searches the FULL TEXT of open-access records for an
unqualified term - so "memory" matched shape-memory polymers and "attention"
matched "has attracted attention". And nothing downstream re-checked the
topic: the journal tiers in niches.yaml are read by vet.py for credibility
SCORING, never for filtering.

    python -m pytest tests/test_topic_relevance.py -q
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sources  # noqa: E402

NICHES = yaml.safe_load((ROOT / "config" / "niches.yaml").read_text())["niches"]
PSYCH = NICHES["psych"]


class _S:
    def __init__(self, title, abstract=""):
        self.title, self.abstract = title, abstract


# ---------------------------------------------------------------------------
# The three that actually shipped
# ---------------------------------------------------------------------------
SHIPPED_OFF_TOPIC = [
    ("An endoplasmic reticulum-enriched nanogel couples ferroptotic tumour "
     "damage with macrophage reprogramming",
     "A pH/redox-sensitive SPIONS@P-CpG-DOX nanogel was constructed to elicit "
     "an antitumour immune cascade and build long-term systemic immune memory "
     "in 4T1 TNBC models."),
    ("A designable twist-densification route to bioactive collagen hydrogel "
     "yarns approaching tendon-level strength",
     "Collagen yarns were produced by twist densification, showing shape "
     "memory behaviour under cyclic mechanical load."),
    ("Dismantling the eDNA-mediated H2S barrier with biohybrids against "
     "refractory biofilm infection",
     "A biohybrid was designed to degrade extracellular DNA and dismantle the "
     "hydrogen sulfide barrier of established bacterial biofilms."),
]


@pytest.mark.parametrize("title,abstract", SHIPPED_OFF_TOPIC,
                         ids=["nanogel", "collagen-yarn", "biofilm"])
def test_the_papers_that_shipped_as_psych_are_now_refused(title, abstract):
    assert sources.on_topic(_S(title, abstract), PSYCH) is False


def test_immune_memory_is_not_psychological_memory():
    """The one term the nanogel paper did hit. "long-term systemic immune
    memory" is immunology; counting it is how the paper got in."""
    strong, weak = sources.topic_hits(
        "build long-term systemic immune memory in 4T1 models", PSYCH)
    assert strong == [] and weak == []


@pytest.mark.parametrize("phrase", [
    "shape memory polymer", "shape-memory alloy", "immunological memory",
    "memory T cells", "memory B cells", "machine learning model",
    "deep learning segmentation", "self-attention layer",
    "an attention mechanism", "has attracted attention",
])
def test_everyday_words_in_their_other_senses_do_not_count(phrase):
    strong, weak = sources.topic_hits(phrase, PSYCH)
    assert (strong, weak) == ([], []), f"{phrase!r} counted as psych"


# ---------------------------------------------------------------------------
# Real psych must still get through - a filter that starves the niche is
# worse than the bug, because Tuesday would silently produce nothing.
# ---------------------------------------------------------------------------
REAL_PSYCH = [
    ("Sleep spindles predict overnight memory consolidation in older adults",
     "We recorded overnight polysomnography in 84 adults and tested memory "
     "for word pairs before and after sleep."),
    ("Dopamine release tracks reward prediction error in human striatum",
     "Subsecond dopamine release was measured during a gambling task."),
    ("Loneliness and cognitive decline in a nine-year cohort",
     "Self-reported loneliness was associated with faster decline in "
     "episodic memory."),
    ("Attention is captured by emotionally salient faces",
     "Visual attention was drawn to fearful faces, scaling with self-reported "
     "anxiety."),
    ("Prefrontal cortex activity during decision making under uncertainty",
     "Neuroimaging during a two-armed bandit task revealed prefrontal "
     "responses scaling with outcome uncertainty."),
]


@pytest.mark.parametrize("title,abstract", REAL_PSYCH,
                         ids=["sleep-memory", "dopamine", "loneliness",
                              "attention-faces", "prefrontal"])
def test_genuine_psych_papers_still_pass(title, abstract):
    assert sources.on_topic(_S(title, abstract), PSYCH) is True


def test_one_distinctive_term_is_enough():
    assert sources.on_topic(_S("Dopamine and effort", "dopamine signalling"),
                            PSYCH) is True


def test_one_everyday_term_alone_is_not():
    assert sources.on_topic(_S("A memory alloy", "we studied memory"),
                            PSYCH) is False


def test_two_everyday_terms_are_no_longer_enough():
    """Inverted on 14 Oct. Two ordinary words used to qualify a paper; that
    is what admitted a metacarpal-fracture radiograph study to the psych
    slot on "bias" and "learning". A real sleep-and-memory paper is still
    found, but by a phrase that means the mind - see the cases above."""
    assert sources.on_topic(
        _S("Sleep and memory", "sleep improved memory retention"), PSYCH) is False
    assert sources.on_topic(
        _S("Sleep and memory consolidation",
           "sleep improved memory consolidation"), PSYCH) is True


# ---------------------------------------------------------------------------
# Only the title and abstract count. That is the entire fix.
# ---------------------------------------------------------------------------
def test_the_full_text_is_not_consulted():
    """Europe PMC matched these papers on their full text. on_topic() is
    given the title and the abstract and nothing else, so a term buried in a
    methods section cannot carry a paper into the wrong day."""
    s = _S("A biomaterials paper", "An abstract with no psychology in it.")
    s.full_text = "dopamine prefrontal cortex neuroimaging"   # ignored
    assert sources.on_topic(s, PSYCH) is False


# ---------------------------------------------------------------------------
# The query itself
# ---------------------------------------------------------------------------
def test_the_search_is_scoped_to_title_and_abstract():
    q = sources.scoped_query('(memory OR "decision making" OR dopamine)')
    assert 'TITLE:"memory"' in q and 'ABSTRACT:"memory"' in q
    assert 'TITLE:"decision making"' in q
    assert "TITLE:\"dopamine\"" in q
    assert "OR" in q


def test_an_empty_query_scopes_to_nothing_rather_than_to_everything():
    assert sources.scoped_query("") == ""
    assert sources.scoped_query("   ") == ""


def test_a_niche_with_no_topic_query_is_not_filtered():
    """physics and the Friday wildcard source from arXiv categories, which
    are already topical. Filtering them on an empty term list would drop
    every candidate."""
    for name in ("physics", "wildcard"):
        cfg = NICHES[name]
        if cfg.get("europepmc_query"):
            continue
        assert sources.on_topic(_S("Anything at all", "..."), cfg) is True


# THE RULE CHANGED ON 14 OCT, and this is what replaced the one above.
#
# Every niche used to be allowed a list of `weak_terms` - words that are also
# ordinary English - on the understanding that TWO of them meant a paper was
# on topic. nature and psych both had one, and the rule was too generous in
# a corpus that is overwhelmingly biomedical: "tissue evolution" plus
# "conservation of the matrix" put a bone-scaffold paper in the Monday nature
# slot, and "interobserver bias" plus "learning curve" put a hand-radiograph
# paper in the Tuesday psych slot. Both are sitting in the queue.
#
# The deeper problem was that those same ordinary words were in the SEARCH,
# so Europe PMC spent all sixty result slots on biomedical papers and
# on_topic() then correctly deleted nearly all of them. On 13 Oct nature
# reported "51 candidate(s) dropped as off-topic" and zero survivors.
#
# So the ordinary words are gone from both queries, and with them the need
# for weak_terms at all: every term a niche searches for now means one thing,
# and a paper has to carry one of them.
def test_no_niche_searches_for_an_ordinary_english_word():
    """If this fails, a niche's terms have drifted back towards words that
    mean something else in a biomedical abstract - which is what emptied the
    nature and psych slots for a week."""
    ordinary = {"memory", "attention", "learning", "sleep", "emotion",
                "perception", "bias", "habit", "motivation", "ecosystem",
                "evolution", "migration", "conservation", "microbiome",
                "growth", "development", "energy", "control", "stress"}
    for name, cfg in NICHES.items():
        terms = set(sources.topic_terms(cfg))
        drifted = terms & ordinary
        assert not drifted, (
            f"{name} searches for {sorted(drifted)} - in Europe PMC's corpus "
            f"those match biomedical papers, which then fill the slot")


def test_no_niche_still_leans_on_the_two_everyday_words_rule():
    """weak_terms is dead config now. Leaving it in place would look live."""
    for name, cfg in NICHES.items():
        assert not cfg.get("weak_terms"), (
            f"{name} has weak_terms again - a paper can now qualify without "
            f"one distinctive word, which is how the junk got in")


# ---------------------------------------------------------------------------
# Through fetch_candidates, which is where it has to actually run
# ---------------------------------------------------------------------------
def test_an_off_topic_paper_is_dropped_by_fetch_candidates(monkeypatch):
    """Asserting on_topic() alone passes even when nothing calls it. That is
    how the journal tiers ended up being config that only vet.py read, and
    how three biomaterials papers reached the psych slot."""
    nanogel = sources.Study(
        source="europepmc", ext_id="MED:1",
        title="An endoplasmic reticulum-enriched nanogel couples ferroptotic "
              "tumour damage with macrophage reprogramming",
        abstract="A nanogel was constructed to build long-term systemic "
                 "immune memory in TNBC models. " + "q" * 700,
        journal="Bioactive materials", pub_date="2026-09-17", doi="10.1/bad")
    real = sources.Study(
        source="europepmc", ext_id="MED:2",
        title="Dopamine release tracks reward prediction error",
        abstract="Subsecond dopamine release was measured during a gambling "
                 "task in six patients. " + "q" * 700,
        journal="Neuron", pub_date="2026-09-17", doi="10.1/good")

    monkeypatch.setattr(sources, "europepmc_search",
                        lambda *a, **kw: [nanogel, real])
    monkeypatch.setattr(sources, "arxiv_search", lambda *a, **kw: [])
    monkeypatch.setattr(sources, "load_ledger",
                        lambda: {"posted": {}, "rejected": {}, "seen": {}})
    monkeypatch.setattr(sources, "load_niches", lambda: {
        "defaults": {"recency_days": 75, "per_source_limit": 60,
                     "min_abstract_chars": 500, "max_candidates": 25},
        "niches": {"psych": PSYCH},
    })
    monkeypatch.delenv("ALTMETRIC_API_KEY", raising=False)

    got = sources.fetch_candidates("psych", days=75)
    dois = [s.doi for s in got]
    assert "10.1/bad" not in dois, "the nanogel paper reached the psych slot again"
    assert "10.1/good" in dois, "the real psych paper was dropped too"


def test_a_traction_sourced_paper_is_checked_too(monkeypatch):
    """Traction DOIs never went through the topic query at all - they are
    discovered from what people are reading and looked up by DOI - so the
    query scoping does nothing for them. This filter is the only thing that
    does."""
    off = sources.Study(
        source="traction", ext_id="x",
        title="A twist-densification route to collagen hydrogel yarns",
        abstract="Collagen yarns with shape memory behaviour. " + "q" * 700,
        journal="Bioactive materials", pub_date="2026-09-17", doi="10.1/yarn")
    assert sources.on_topic(off, PSYCH) is False


# ---------------------------------------------------------------------------
# nature has the same problem in a different vocabulary
# ---------------------------------------------------------------------------
NATURE = NICHES["nature"]


def test_a_tumour_ecosystem_is_not_an_ecosystem():
    """How a cancer-nanomedicine paper reached the Monday nature slot: its
    abstract says "remodels the tumor ecosystem" and "the metabolic
    ecosystem", and `ecosystem` was a nature search term."""
    s = _S("Dual metabolic checkpoint blockade via a 3D-printed metalloplatform",
           "The platform remodels the tumor ecosystem for systemic antitumour "
           "immunity, transforming the metabolic ecosystem from pro-tumour to "
           "antitumour.")
    assert sources.on_topic(s, NATURE) is False


@pytest.mark.parametrize("phrase", [
    "tumour ecosystem", "tumor ecosystem", "metabolic ecosystem",
    "cell migration", "cellular migration", "migration assay",
    "evolution of resistance",
])
def test_biomedical_senses_of_natures_everyday_words_do_not_count(phrase):
    strong, weak = sources.topic_hits(phrase, NATURE)
    assert (strong, weak) == ([], []), f"{phrase!r} counted as nature"


@pytest.mark.parametrize("title,abstract", [
    ("Pollinator decline across European grasslands",
     "Pollinator abundance fell across 200 grassland sites over twelve years."),
    ("Coral reef bleaching outpaces recovery",
     "Repeat bleaching on the Great Barrier Reef now outpaces coral recovery."),
    ("Ecosystem collapse and species migration in warming seas",
     "Reef ecosystem shifts drove the migration of sixty fish species."),
])
def test_genuine_nature_papers_still_pass(title, abstract):
    assert sources.on_topic(_S(title, abstract), NATURE) is True


def test_the_filter_does_not_simply_reject_everything():
    """A filter that starves a niche is worse than the bug it fixes, because
    the day would silently produce nothing at all."""
    passing = [
        (_S("Dopamine release tracks reward prediction error",
            "Subsecond dopamine release during a gambling task."), PSYCH),
        (_S("Pollinator decline across European grasslands",
            "Pollinator abundance fell across 200 sites."), NATURE),
        (_S("A randomised controlled trial of a shingles vaccine",
            "In this randomized controlled trial the vaccine reduced cases."),
         NICHES["health"]),
    ]
    assert all(sources.on_topic(s, cfg) for s, cfg in passing)


# ---------------------------------------------------------------------------
# THE FOUR PAPERS THAT ACTUALLY GOT DRAFTED, 9-13 OCT
#
# Not invented examples. Each of these is a real post sitting in data/queue,
# drafted into a slot it has nothing to do with, by a pipeline whose topic
# filter was working perfectly - on a catch it should never have been handed.
# Abstracts are paraphrased to the phrasing that carried them through, since
# the queued post records the title and the journal but not the abstract.
# ---------------------------------------------------------------------------
DRAFTED_JUNK = [
    ("nature", "Biomaterials",
     "Magnetic hydrogel encapsulation combined with on-chip processing "
     "enhances straw vitrification of oocytes",
     "Cell migration and tissue evolution within the scaffold, with "
     "conservation of the extracellular matrix ecosystem."),
    ("nature", "Talanta",
     "A flexible hydrogel-based SERS sensor for multi-target detection of "
     "microbial metabolites",
     "Detection of metabolites; cellular migration assays and the evolution "
     "of the signal over time."),
    ("psych", "Journal of hand surgery global online",
     "Oblique Hand Radiograph as a Method to Measure Fifth Metacarpal Neck "
     "Fracture Angulation",
     "Interobserver bias was assessed, along with the learning curve and "
     "the perception of angulation by three raters."),
    ("psych", "International journal of endocrinology and metabolism",
     "Artificial Intelligence-Driven Digital Tools for Diabetes "
     "Self-management in Children: A Scoping Review",
     "Machine learning tools were reviewed; adherence habits, patient "
     "motivation and attention to glucose readings were extracted."),
]


@pytest.mark.parametrize("niche,journal,title,abstract", DRAFTED_JUNK,
                         ids=[f"{n}-{j.split()[0].lower()}"
                              for n, j, _, _ in DRAFTED_JUNK])
def test_the_papers_that_were_really_drafted_are_now_rejected(
        niche, journal, title, abstract):
    assert sources.on_topic(_S(title, abstract), NICHES[niche]) is False, (
        f"{journal} would still be drafted into the {niche} slot")


@pytest.mark.parametrize("niche,journal,title,abstract", DRAFTED_JUNK,
                         ids=[f"{n}-{j.split()[0].lower()}"
                              for n, j, _, _ in DRAFTED_JUNK])
def test_and_the_search_would_not_have_returned_them_either(
        niche, journal, title, abstract):
    """The filter rejecting them is the second line of defence. The first is
    not asking Europe PMC for them: the scoped query searches title and
    abstract, so if none of the niche's terms appears in either, the record
    never comes back and does not burn one of the sixty result slots."""
    blob = f"{title} {abstract}".lower()
    hit = [t for t in sources.topic_terms(NICHES[niche]) if t in blob]
    assert not hit, f"the {niche} query still matches this on {hit}"


# ---------------------------------------------------------------------------
# THE LOG HAS TO SAY WHICH STEP ATE THEM
#
# The whole of the 13 Oct nature diagnostic was:
#
#     51 candidate(s) dropped as off-topic for 'nature'
#     0 candidates after dedupe and topic filters
#
# Five filters run between the sources and the ranking and only one of them
# printed. "The search found nothing" and "the search found plenty and the
# filter ate it" have opposite fixes - widen the query, or narrow it - and
# the log could not tell them apart. Two days went on guessing.
# ---------------------------------------------------------------------------
def _study(ext_id, title, abstract, journal="Ecology Letters"):
    return sources.Study(source="europepmc", ext_id=ext_id, title=title,
                         abstract=abstract, journal=journal,
                         doi=f"10.1/{ext_id}", pub_date="2026-09-01")


def _run_funnel(monkeypatch, capsys, cands, ledger=None):
    monkeypatch.setattr(sources, "europepmc_search", lambda *a, **k: list(cands))
    monkeypatch.setattr(sources, "traction_candidates", lambda *a, **k: [])
    import traction
    monkeypatch.setattr(traction, "gather", lambda **k: [])
    monkeypatch.setattr(sources, "load_ledger",
                        lambda: ledger or {"posted": {}, "rejected": {}, "seen": {}})
    out = sources.fetch_candidates("nature", 75)
    return out, capsys.readouterr().out


ECO = ("Long-term monitoring shows pollinator biodiversity declining as "
       "habitat is lost to climate change. " * 6)
JUNK = ("Cell migration and tissue evolution in scaffolds, with conservation "
        "of the extracellular matrix. " * 6)


def test_the_log_counts_every_step_not_just_the_topic_filter(monkeypatch, capsys):
    cands = [_study("a", "Pollinator decline", ECO),
             _study("b", "Reef recovery", ECO),
             _study("c", "Bone scaffolds", JUNK, "Bioactive Materials"),
             _study("d", "Short one", "too short", "Nature")]
    led = {"posted": {cands[0].key: {}}, "rejected": {}, "seen": {}}
    out, log = _run_funnel(monkeypatch, capsys, cands, led)
    assert "fetched 4 record(s)" in log
    assert "already in the ledger" in log
    assert "abstract under" in log
    assert "off-topic for 'nature'" in log
    assert [s.ext_id for s in out] == ["b"]


def test_the_ledger_line_says_which_bucket_held_them(monkeypatch, capsys):
    """'used up' and 'piling up unreviewed' are different problems. Twelve
    nature drafts were sitting undecided while the slot reported nothing."""
    cands = [_study(c, "Pollinator decline", ECO) for c in "abc"]
    led = {"posted": {cands[0].key: {}}, "rejected": {cands[1].key: {}},
           "seen": {cands[2].key: {}}}
    _, log = _run_funnel(monkeypatch, capsys, cands, led)
    assert "1 posted" in log and "1 killed" in log
    assert "1 awaiting your review" in log


def test_the_log_names_the_journals_it_threw_out(monkeypatch, capsys):
    """The tell that the QUERY is wrong rather than the filter: the same
    handful of open-access biomedical titles, every day."""
    cands = [_study(c, "Bone scaffolds", JUNK, "Bioactive Materials")
             for c in "abc"]
    _, log = _run_funnel(monkeypatch, capsys, cands)
    assert "Bioactive Materials x3" in log
