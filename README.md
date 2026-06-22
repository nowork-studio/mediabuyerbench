# MediaBuyerBench

Open benchmark for evaluating AI systems as **senior paid media buyers** across ad platforms.

This first iteration is intentionally small: static case packets, deterministic scoring, and provider/skill scorecards. The goal is to make it easy to add high-quality cases before adding interactive ad-platform simulators.

## What it evaluates

MediaBuyerBench scores whether a model can make commercially sound paid-media decisions under imperfect data:

- diagnose the true bottleneck
- reason from business economics, not vanity metrics
- handle attribution and tracking skepticism
- understand platform-specific mechanics
- recommend small, safe interventions
- avoid reckless spend or destructive changes
- communicate like a senior operator

## Current scope

- Static benchmark cases in JSON
- Public lite split with Google, Meta, and cross-channel examples
- Deterministic keyword/concept scorer
- Provider and skill-level score output
- No private/hidden split yet
- No live ad account access
- No mutating tools

## Install

```bash
git clone https://github.com/notfair/mediabuyerbench.git
cd mediabuyerbench
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

No runtime dependencies are required for the first version.

## Quick start

List cases:

```bash
mediabuyerbench list
```

Print a prompt for a case:

```bash
mediabuyerbench prompt cases/public_lite/google/search_term_waste_001.json
```

Score a response file:

```bash
mediabuyerbench score \
  --case cases/public_lite/google/search_term_waste_001.json \
  --response examples/responses/google_search_term_waste_001.md
```

Run all sample responses:

```bash
mediabuyerbench run-samples
```

Run tests:

```bash
python -m unittest discover -s tests
```

## Case format

Each case includes:

- business context
- user prompt
- provider/category/difficulty metadata
- compact synthetic data tables
- required concepts the model should identify
- forbidden concepts/recommendations
- skill weights for score breakdown

See `schemas/case.schema.json` and `cases/public_lite/*`.

## Scoring philosophy

This v0 scorer is deliberately simple and inspectable.

- Required concepts award points when the response mentions at least one configured phrase.
- Forbidden concepts subtract points and are surfaced as hard warnings.
- Provider and skill scores are derived from the matched concepts.

This is not the final evaluation quality ceiling. It is the public skeleton. Next iterations should add:

- LLM judge rubrics for seniority/quality
- multi-turn tool simulation
- sandbox state mutation and approval-gating
- richer platform case packs
- leaderboard artifacts and multiple-seed reporting

## Contributing cases

A good case should contain a real media-buyer trap:

- platform CPA lies
- tracking is broken
- CTR moves opposite to profit
- audience fatigue vs offer problem
- cheap traffic with weak downstream quality
- high CPL but high sales quality
- budget cap vs rank/relevance constraint
- margin/inventory issue hidden behind ROAS

Keep public cases synthetic. Do not include customer IDs, domains, exact spend, private search terms, or real account exports.

## License

MIT
