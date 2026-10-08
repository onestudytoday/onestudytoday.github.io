<!-- Edit the text below, commit, and the post updates itself.

     Keep the headings. Anything you add under an unknown heading is ignored.
     The study, the DOI and the vetting report are NOT here on purpose - they
     are not text to rewrite.

     **double asterisks** mark the accent-coloured words on a slide.
-->

# 2026-10-07-physics-5bd7fec6

## Cover

**Kicker:** arXiv preprint, not peer reviewed
**Headline:** **Quantum software can output wrong answers** without crashing, and a new agent spotted bugs human testers missed.

## Slide 1 - What it means for you

**Title:** When quantum code fails silently, bad results flow into research papers and algorithm designs.

**Basis:** stated    <!-- stated = the paper says it; inferred = our read, must stay conditional -->

Most testing tools only catch bugs that make programs crash or disagree with another version. Quantum libraries can produce incorrect outputs that look valid.

The team reports this agent can find bugs that existing methods miss, catching logic errors before they reach production.

## Slide 2 - The result

**Title:** The agent found 40 previously unknown bugs confirmed by developers, including 30 silent bugs.

It reasons about quantum operations using documentation and code constraints as an oracle. On a benchmark of 20 historical silent bugs, it achieved higher relocation counts than existing code agents.

The system generates executable tests through library APIs.

## Slide 3 - How it works

**Title:** The agent loops through API docs and source code, checking whether logic can produce invalid outputs from valid inputs.

It uses quantum semantics and documentation as a source-level oracle, not just execution checks. The approach targets Qiskit and PennyLane libraries.

Guided by quantum-domain reasoning, it identifies potential semantic deviations and validates them by generating tests.

## Caveats

- This is a preprint. It has not been peer reviewed, so no independent expert has checked the work yet.
- The comparison benchmark included only 20 historical bugs, a small test set for evaluating detection performance.
- The system requires access to documentation and source code, so it cannot test closed or poorly documented quantum libraries.

## CTA

**Headline:** Send this to the quantum developer
**Sub:** One real study, every weekday. The full paper is linked in bio.

## Caption

<!-- One line. The link and hashtags are added automatically - do not type them here. -->

Quantum libraries can fail silently, producing wrong answers that flow into research without triggering any alarms or error messages.
