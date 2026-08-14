"""
The five worked sample posts, written by hand to the exact spec in
config/copy_spec.yaml, then validated by the same lint() the automated
pipeline uses. If a sample fails lint, this script fails loudly.

Every study here is real, published, and linked.

    python samples/build_samples.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from draft import lint                      # noqa: E402
from render import contact_sheet, render_post  # noqa: E402
from vet import VetReport                   # noqa: E402


def V(**kw) -> VetReport:
    r = VetReport(key=kw.pop("key", "x"))
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# ===========================================================================
# MONDAY - NATURE & ENVIRONMENT
# ===========================================================================
P1 = {
    "id": "2026-07-29-nature-iron",
    "niche": "nature",
    "study": {
        "title": "Climate benefit and ecological cost trade-offs for ocean iron fertilization",
        "journal": "Nature",
        "pub_date": "2026-07-29",
        "pub_date_display": "Jul 29, 2026",
        "doi": "10.1038/s41586-026-10795-y",
        "doi_display": "doi.org/10.1038/s41586-026-10795-y",
        "url": "https://www.nature.com/articles/s41586-026-10795-y",
        "is_preprint": False, "server": None, "n": None,
    },
    "cover": {
        "kicker": "Nature · 60-year ocean model",
        "headline": "Dumping iron in the ocean does pull carbon down. "
                    "**Then the ocean burps half of it back.**",
    },
    "slides": [
        {
            "eyebrow": "The setup",
            "title": "Iron is the missing nutrient across a third of the ocean. Add it, and plankton bloom.",
            "body": "Plankton pull carbon dioxide out of the air, then sink when they die, "
                    "dragging that carbon to the deep sea. Huge stretches of ocean are "
                    "starved of iron, so the plankton never get going. Sprinkle iron in, "
                    "and you get a bloom.\n\n"
                    "That trick has been pitched as climate engineering for thirty years. "
                    "Nobody had run the numbers across the whole ocean, over decades, "
                    "while tracking what it does to everything living down there.",
        },
        {
            "eyebrow": "What they found",
            "title": "Sixty years of fertilising ten ocean regions bought between 1.1 and 5.3 ppm of CO2.",
            "body": "For scale, humans add roughly 2.5 ppm to the atmosphere every single "
                    "year. So six decades of continuously seeding the ocean buys back "
                    "somewhere between five months and two years of emissions.\n\n"
                    "Worse, more than half of the captured carbon leaked back out within "
                    "decades of stopping. The Southern Ocean performed best. The equatorial "
                    "Pacific paid the steepest ecological price for the least return.",
            "stat": {"value": "5.3 ppm", "label": "best case, after 60 years of seeding"},
        },
    ],
    "caveats": [
        "This is a computer model, not an experiment. No iron was actually dumped in any ocean.",
        "Equatorial seeding shrank zooplankton and widened low-oxygen dead zones. The food web pays.",
        "Over half the captured carbon came back out. The ocean is a leaky bank, not a vault.",
    ],
    "cta": {
        "headline": "One real study. Every weekday.",
        "sub": "Follow for science you can actually check. Every paper linked in bio.",
    },
    "caption": (
        "Seeding the ocean with iron is one of the oldest ideas in climate engineering, "
        "and this is the most complete audit of it yet.\n\n"
        "A team ran a process-rich ocean biogeochemical model across ten ocean biomes for "
        "sixty simulated years, tracking both the carbon and the ecosystem. Net removal "
        "came out between 1.1 and 5.3 ppm of atmospheric CO2. Humanity currently adds "
        "about 2.5 ppm a year. More than half of what got captured was re-emitted within "
        "decades of switching the iron off.\n\n"
        "The ecological bill is the part that rarely makes headlines: less energy reaching "
        "higher animals, expanding oxygen-minimum zones, less zooplankton.\n\n"
        "Big caveat: this is a model, not a field trial.\n\n"
        "Full study: doi.org/10.1038/s41586-026-10795-y\n\n"
        "Worth it, or not worth it?"
    ),
    "hashtag_set": "nature",
    "vet": V(design="modelling", subjects="unclear", sample_size=None,
             journal_tier=1, score=76, verdict="PASS").to_dict(),
}

# ===========================================================================
# TUESDAY - PSYCHOLOGY & NEUROSCIENCE
# ===========================================================================
P2 = {
    "id": "2026-08-05-psych-dbs",
    "niche": "psych",
    "study": {
        "title": "Deep brain stimulation reshapes memory cell assemblies and drives distinct "
                 "transcriptional programs in human cortex",
        "journal": "Nature",
        "pub_date": "2026-08-05",
        "pub_date_display": "Aug 5, 2026",
        "doi": "10.1038/s41586-026-10879-9",
        "doi_display": "doi.org/10.1038/s41586-026-10879-9",
        "url": "https://www.nature.com/articles/s41586-026-10879-9",
        "is_preprint": False, "server": None, "n": 12,
    },
    "cover": {
        "kicker": "Nature · 12 human brains",
        "headline": "Zapping the brain doesn't just fire neurons. "
                    "**It changes which genes they switch on.**",
    },
    "slides": [
        {
            "eyebrow": "The setup",
            "title": "We put electrodes in thousands of human brains and still cannot say why it helps.",
            "body": "Deep brain stimulation already treats Parkinson's, epilepsy and severe "
                    "depression. The working theory has stayed crude: electricity goes in, "
                    "symptoms get better, nobody can point to what changed inside the "
                    "cell.\n\n"
                    "Getting the answer means reading living human brain tissue, which you "
                    "cannot ethically do on a volunteer. So a team waited for patients "
                    "already having epilepsy surgery and asked them.",
        },
        {
            "eyebrow": "What they found",
            "title": "Stimulation tightened the neuron gangs that hold a memory, and woke the support cells.",
            "body": "They mapped 19 cell assemblies, the clusters of neurons that fire "
                    "together to encode one memory. After stimulation those clusters fired "
                    "in tighter lockstep than before.\n\n"
                    "Then the surprise. The electricity switched on separate gene programs "
                    "in neurons and in astrocytes, the support cells long written off as "
                    "brain glue. Both were running plasticity machinery, the molecular "
                    "toolkit for rewiring a connection.",
            "stat": {"value": "19", "label": "memory cell assemblies mapped and tracked"},
        },
    ],
    "caveats": [
        "Only twelve people, all with epilepsy, which is a brain already behaving unusually.",
        "They sampled temporal cortex. Clinical stimulation targets deeper structures this study never touched.",
        "This captures hours of stimulation. Real patients get years, and nobody knows what that does.",
    ],
    "cta": {
        "headline": "One real study. Every weekday.",
        "sub": "Follow for science you can actually check. Every paper linked in bio.",
    },
    "caption": (
        "We have been putting electrodes into human brains for decades without a solid "
        "molecular account of why it works. This paper goes and looks.\n\n"
        "Twelve patients undergoing epilepsy surgery had a region of cortex stimulated, and "
        "the team then read the genetic activity of that exact tissue, with eight more "
        "patients supplying validation data. Stimulation strengthened 19 memory-encoding "
        "cell assemblies and triggered distinct transcriptional programs in neurons and in "
        "astrocytes.\n\n"
        "That second half matters. Astrocytes were treated as passive scaffolding for most "
        "of the last century, and here they are, running plasticity genes.\n\n"
        "Limits worth holding onto: twelve people, all with epilepsy, temporal cortex only, "
        "and only the first few hours of stimulation.\n\n"
        "Full study: doi.org/10.1038/s41586-026-10879-9\n\n"
        "Surprised, or not surprised?"
    ),
    "hashtag_set": "psych",
    "vet": V(design="experiment", subjects="human", sample_size=12, journal_tier=1,
             score=71, verdict="HOLD",
             required_caveats=["Only 12 people took part."]).to_dict(),
}

# ===========================================================================
# WEDNESDAY - HEALTH & MEDICINE
# ===========================================================================
P3 = {
    "id": "2026-08-10-health-glp1",
    "niche": "health",
    "study": {
        "title": "Aleniglipron, a daily oral small-molecule GLP-1 receptor agonist, in adults "
                 "with obesity: a phase 2b randomised, double-blind, placebo-controlled trial",
        "journal": "Nature Medicine",
        "pub_date": "2026-08-10",
        "pub_date_display": "Aug 10, 2026",
        "doi": "10.1038/s41591-026-04476-6",
        "doi_display": "doi.org/10.1038/s41591-026-04476-6",
        "url": "https://doi.org/10.1038/s41591-026-04476-6",
        "is_preprint": False, "server": None, "n": 230,
    },
    "cover": {
        "kicker": "Nature Medicine · 230 adults",
        "headline": "A GLP-1 you swallow took off 12.1% of body weight. "
                    "**Placebo managed 0.5%.**",
    },
    "slides": [
        {
            "eyebrow": "The setup",
            "title": "The weight-loss drugs that work are injections. That is a real barrier for many people.",
            "body": "Semaglutide and tirzepatide changed obesity medicine, and both arrive as "
                    "a weekly needle. Needles mean cold chain, prescriber training, and a "
                    "hard no from a lot of patients.\n\n"
                    "A pill would dodge all of it. The problem is that the peptide drugs "
                    "fall apart in the gut. So chemists have been hunting for a small "
                    "molecule that hits the same receptor and survives being swallowed.",
        },
        {
            "eyebrow": "What they found",
            "title": "Across 38 US sites, the top dose beat placebo by roughly twelve percentage points.",
            "body": "230 adults, average age 50, were randomly assigned to one of three "
                    "doses or placebo for 36 weeks. Weight change came in at 9.0% on 45 mg, "
                    "10.7% on 90 mg and 12.1% on 120 mg, against 0.5% on placebo.\n\n"
                    "Gut side effects were mild to moderate and got less frequent as the "
                    "trial ran on. About one in ten participants stopped early. No cases of "
                    "drug-induced liver injury were reported.",
            "stat": {"value": "12.1%", "label": "body weight lost on the top dose"},
        },
    ],
    "caveats": [
        "The trial was funded by Structure Therapeutics, the company that owns the drug.",
        "Phase 2b means 230 people over 36 weeks. Rare harms only show up in far larger trials.",
        "Around one in ten quit before the end, so tolerability is not a solved problem.",
        "Weight regain after stopping was not tested here. For every other GLP-1, it happens.",
    ],
    "cta": {
        "headline": "One real study. Every weekday.",
        "sub": "Follow for science you can actually check. Every paper linked in bio.",
    },
    "caption": (
        "The interesting number here is not 12.1%. It is the 0.5% next to it.\n\n"
        "This was a phase 2b randomised, double-blind, placebo-controlled trial of "
        "aleniglipron, a daily oral small-molecule GLP-1 receptor agonist, in 230 adults "
        "with obesity across 38 US medical centres. At 36 weeks: 9.0% weight loss on 45 mg, "
        "10.7% on 90 mg, 12.1% on 120 mg, and 0.5% on placebo. Gastrointestinal side effects "
        "were mild to moderate and eased over time. The discontinuation rate was 10.4%.\n\n"
        "Two things to hold onto. The trial was funded by the company that owns the drug. "
        "And phase 2b is a signal, not a verdict.\n\n"
        "Full study: doi.org/10.1038/s41591-026-04476-6\n\n"
        "Pill or needle?"
    ),
    "hashtag_set": "health",
    "vet": V(design="randomised trial", subjects="human", sample_size=230, journal_tier=1,
             score=74, verdict="HOLD",
             required_caveats=["Funded in part by industry."]).to_dict(),
}

# ===========================================================================
# THURSDAY - PHYSICS & SPACE
# ===========================================================================
P4 = {
    "id": "2026-08-12-physics-bhstar",
    "niche": "physics",
    "study": {
        "title": "A black hole star at cosmic dawn: MoM-BH*-1 and the nature of little red dots",
        "journal": "Nature",
        "pub_date": "2026-08-12",
        "pub_date_display": "Aug 12, 2026",
        "doi": "10.1038/s41586-026-10846-4",
        "doi_display": "doi.org/10.1038/s41586-026-10846-4",
        "url": "https://doi.org/10.1038/s41586-026-10846-4",
        "is_preprint": False, "server": None, "n": None,
    },
    "cover": {
        "kicker": "Nature · JWST, cosmic dawn",
        "headline": "Solar-system sized, outshining every star we know of. "
                    "**And it is not a star.**",
    },
    "slides": [
        {
            "eyebrow": "The setup",
            "title": "JWST kept finding tiny red dots in the early universe that nobody could explain.",
            "body": "Since Webb switched on, its deep images have been littered with "
                    "compact, very red points of light from the universe's first billion "
                    "years. The obvious reading was dust, which reddens light the same way "
                    "smoke reddens a sunset.\n\n"
                    "That reading had a problem. Make them dusty galaxies and you need more "
                    "stars, forming faster, than the early universe had time to build. The "
                    "dots were breaking the timeline.",
        },
        {
            "eyebrow": "What they found",
            "title": "The reddening is not dust. It is a cocoon of hydrogen gas wrapped around a black hole.",
            "body": "Spectroscopy killed the dust idea outright. At specific wavelengths the "
                    "light simply vanished, which dust does not do and dense hydrogen gas "
                    "does. So the team modelled what sits inside the cocoon.\n\n"
                    "The best fit is a black hole feeding hard, buried in gas so thick the "
                    "whole object mimics a single enormous star. Nuclear fusion cannot "
                    "generate that much power. Something falling into a black hole can.",
            "stat": {"value": "660 M", "label": "years after the Big Bang, this existed"},
        },
    ],
    "caveats": [
        "This is one object. A category of cosmic thing does not get established on a single example.",
        "The interpretation leans on model fitting, so a different model could still fit the same light.",
        "Mass estimates for objects this far away carry wide error bars that headlines tend to drop.",
    ],
    "cta": {
        "headline": "One real study. Every weekday.",
        "sub": "Follow for science you can actually check. Every paper linked in bio.",
    },
    "caption": (
        "The little red dots have been the most annoying thing in astronomy for three "
        "years. Webb sees them everywhere in the early universe and nothing explained them "
        "cleanly.\n\n"
        "This paper describes MoM-BH*-1, seen as it was roughly 660 million years after the "
        "Big Bang. Spectroscopy rules out dust as the source of its redness, because the "
        "light disappears at wavelengths where dust would let it through and dense hydrogen "
        "would not. Modelling points to an accreting black hole wrapped in a thick gas "
        "envelope, radiating far more than fusion could ever manage from an object that "
        "size.\n\n"
        "Hold it loosely. This is one object, and the case rests on model fitting.\n\n"
        "Full study: doi.org/10.1038/s41586-026-10846-4\n\n"
        "Star, or something new?"
    ),
    "hashtag_set": "physics",
    "vet": V(design="observation + modelling", subjects="not applicable", sample_size=None,
             journal_tier=1, score=78, verdict="PASS").to_dict(),
}

# ===========================================================================
# FRIDAY - WILDCARD
# ===========================================================================
P5 = {
    "id": "2026-08-10-wildcard-fossil",
    "niche": "wildcard",
    "study": {
        "title": "A new archosauriform from the Middle Triassic of southern Brazil and the "
                 "early diversification of Archosauria",
        "journal": "Scientific Reports",
        "pub_date": "2026-08-10",
        "pub_date_display": "Aug 10, 2026",
        "doi": "10.1038/s41598-026-53740-9",
        "doi_display": "doi.org/10.1038/s41598-026-53740-9",
        "url": "https://doi.org/10.1038/s41598-026-53740-9",
        "is_preprint": False, "server": None, "n": None,
    },
    "cover": {
        "kicker": "Scientific Reports · Brazil",
        "headline": "A fossil sat lost in a drawer for twenty years. "
                    "**It was a species nobody had named.**",
    },
    "slides": [
        {
            "eyebrow": "The setup",
            "title": "Dinosaurs and crocodiles share a grandparent, and that grandparent is barely known.",
            "body": "Before either group existed there was a sprawl of reptiles called "
                    "archosauriforms, quietly experimenting with body plans in the Triassic. "
                    "One of those experiments led to everything from sparrows to "
                    "saltwater crocodiles.\n\n"
                    "The fossil record for that stretch is thin. So when a specimen "
                    "collected in southern Brazil went missing for two decades, a real "
                    "piece of the family tree went with it. It resurfaced in 2022.",
        },
        {
            "eyebrow": "What they found",
            "title": "The bones belong to an unnamed reptile with legs held halfway under its body.",
            "body": "The team named it Silescelida acristata and placed it close to "
                    "Euparkeriidae, on the branch running towards dinosaurs and crocodiles "
                    "rather than inside either.\n\n"
                    "Its legs sat in a semi-erect posture, tucked further under the body "
                    "than a sprawling lizard but not fully upright. That halfway stance is "
                    "the anatomical shift that eventually let archosaurs move efficiently "
                    "enough to take over.",
            "stat": {"value": "240 M", "label": "years old, from the Middle Triassic"},
        },
    ],
    "caveats": [
        "The specimen is fragmentary. Most of what survives is limb bones, not a full skeleton.",
        "Where a species sits on the family tree often shifts once more fossils turn up.",
        "Scientific Reports is a broad-scope journal, so this got less specialist scrutiny than a Nature paper.",
    ],
    "cta": {
        "headline": "One real study. Every weekday.",
        "sub": "Follow for science you can actually check. Every paper linked in bio.",
    },
    "caption": (
        "Somebody dug this up in southern Brazil, and then it was accidentally lost for more "
        "than twenty years. It came back in 2022 and turned out to be a species nobody had "
        "described.\n\n"
        "Silescelida acristata is a Middle Triassic archosauriform, roughly 240 million "
        "years old, sitting near Euparkeriidae on the lineage that runs towards both "
        "dinosaurs and crocodiles. The detail that matters is the legs: a semi-erect "
        "posture, tucked further under the body than a sprawling lizard, which is the shift "
        "that made archosaur locomotion efficient.\n\n"
        "Fair warning, the specimen is fragmentary and mostly limb bones, and family-tree "
        "placements like this move around as new material appears.\n\n"
        "Full study: doi.org/10.1038/s41598-026-53740-9\n\n"
        "Team dinosaur, or team crocodile?"
    ),
    "hashtag_set": "wildcard",
    "vet": V(design="descriptive (fossil)", subjects="not applicable", sample_size=None,
             journal_tier=None, score=58, verdict="HOLD").to_dict(),
}

POSTS = [P1, P2, P3, P4, P5]


def main():
    outdir = ROOT / "out" / "samples"
    outdir.mkdir(parents=True, exist_ok=True)
    (ROOT / "samples" / "posts").mkdir(parents=True, exist_ok=True)

    failures = 0
    for p in POSTS:
        rep = VetReport(key=p["id"])
        for k, v in p["vet"].items():
            if k != "flags" and hasattr(rep, k):
                setattr(rep, k, v)
        errs = lint(p, rep)
        status = "OK  " if not errs else "FAIL"
        print(f"{status} {p['id']}")
        for e in errs:
            print(f"       - {e}")
            failures += 1

        (ROOT / "samples" / "posts" / f"{p['id']}.json").write_text(json.dumps(p, indent=2))
        paths = render_post(p, "neon", str(outdir / p["id"]))
        contact_sheet(paths, str(outdir / f"SHEET_{p['id']}.png"), cols=5, scale=0.38)

    print(f"\n{len(POSTS)} samples, {failures} lint violations")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
