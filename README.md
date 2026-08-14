# MediaBuyerBench

Open benchmark for evaluating AI systems as **senior paid media buyers** across ad platforms.

This first iteration is intentionally small: static case packets, deterministic scoring, and provider/skill scorecards. The public-lite split starts with five Google Ads analyst cases before expanding to cross-channel or interactive platform work.

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
- Public lite split covering retrieval, calculation, diagnosis, recommendation, and safe refusal
- Deterministic assertion scorer for required scope, values, and safety concepts
- Blind LLM-judge rubric for decision quality, pending calibration to the paid-media reviewer
- Provider and skill-level score output
- Local, ignored reviewer-only Google Search drafts; never committed or exposed to a model as tools
- No live ad account access for a model under test
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
mediabuyerbench prompt cases/public_lite/google/cpa_spike_diagnosis_001.json
```

Score a response file:

```bash
mediabuyerbench score \
  --case cases/public_lite/google/cpa_spike_diagnosis_001.json \
  --response examples/responses/google_cpa_spike_diagnosis_001.md
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
- required concepts and deterministic assertions the model should satisfy
- forbidden concepts/recommendations
- skill weights for score breakdown

See `schemas/case.schema.json` and `cases/public_lite/*`.

## Scoring philosophy

This v0 scorer is deliberately simple and inspectable.

- Required concepts award points when the response mentions at least one configured phrase.
- Required assertions check either a required phrase or a reported number within a configured tolerance.
- Forbidden concepts subtract points and are surfaced as hard warnings.
- Provider and skill scores are derived from the matched concepts.

This is not the final evaluation quality ceiling. It is the public skeleton. Next iterations should add:

- LLM judge rubrics for seniority/quality
- multi-turn tool simulation
- sandbox state mutation and approval-gating
- richer platform case packs
- leaderboard artifacts and multiple-seed reporting

## Blind review layer

Deterministic checks are useful for numbers and clear safety errors, but they should not punish an equivalent well-reasoned answer for using different words. `rubrics/google_search_v2.json` defines a blind senior-review rubric for auditable evidence, causal discipline, precondition order, intervention scope, decision rules, and alternatives. It also contains the versioned **Google Search operator arbiter**: the binding decision method distilled from the project's Google Ads/Google Search operator playbooks. The judge sees only the rendered case and candidate response, never the canonical answer or deterministic checks.

Authority is deliberately ordered: **case packet facts and explicit gates → operator arbiter → blind judge application**. The arbiter never supplies missing facts or a hidden expected answer. It requires correct conversion-goal integrity, mature-cohort reasoning, narrow search-term scope, verified geo serviceability, causal incrementality discipline, and a reversible, falsifiable validation plan. This keeps an LLM judge from rewarding a plausible conclusion reached by unsafe or generic methodology.

Render the prompt for any independent LLM judge:

```bash
mediabuyerbench judge-prompt \
  --case cases/private_google_review/example.json \
  --response candidate.md > judge-prompt.txt
```

Save the returned JSON as `judgment.json`, then attach it to the deterministic result:

```bash
mediabuyerbench score \
  --case cases/private_google_review/example.json \
  --response candidate.md \
  --judge-output judgment.json
```

The resulting hybrid score is provisional: calibrate the judge against at least 20 independently reviewer-scored responses before treating it as a release or leaderboard score. A critical error caps the judge score at 49. The judge also reports `methodology_pass`: no critical errors and at least 3/4 on every method gate. Use methodology-pass rate, not the hybrid average, as the primary expert-split metric. Do not tune the judge after seeing a single model’s response; log each reviewer disagreement as a calibration example and apply the revised rubric prospectively.

### Calibrated judge panels

Never rank models from one judge sample. Generate at least three blind judgments per candidate, then aggregate them with a dimension-level median and a majority vote for critical errors:

```bash
mediabuyerbench aggregate-judgments \
  --judgment judge-a.json \
  --judgment judge-b.json \
  --judgment judge-c.json
```

The aggregate exposes the individual score range, every dimension vote, and the critical-error vote count. A single harsh or generous judge cannot decide the result. Each judge must also cite the response excerpt, packet facts, and applicable operator-arbiter rule IDs supporting every dimension score.

To measure whether that panel is aligned with the paid-media reviewer, prepare a JSON file with one `human_judgment` and an odd `judge_judgments` panel for each example, then run:

```bash
mediabuyerbench calibrate-judge --input reviewer-labels.json
```

The report includes per-dimension absolute error, exact/within-one agreement, critical-error false negatives and false positives, and methodology-pass agreement. It is intentionally marked `insufficient_human_labels` until it has at least 20 reviewer-labeled examples. Do not publish a leaderboard until the held-out reviewer labels show stable agreement.

An AI judge adds scalable review, not truth. The paid-media reviewer remains the authority for (1) the correct action, (2) the facts that gate it, and (3) critical-error labels. Keep per-case human scores so judge agreement, disagreement, and score inflation are measurable.

## Source-grounded evaluation and private certification

The public synthetic demonstration suite lives in `suites/google_search_public_demo_v1.json`; its [source pack](source_packs/google_search_operator_sources_v1.json) contains official Google Ads documentation and selected Adalysis operator guidance. It demonstrates the same two-pass evaluation machinery without exposing customer-derived cases.

A private hard certification suite can use the same format and runner. Keep its cases, reference decisions, and any customer-derived source annotations out of the repository until an independently de-identified version is approved.

The final semantic judge is a source-grounded expert referee, not the generic blind rubric. It uses a pinned source pack with official Google Ads documentation and selected Adalysis operator guidance. Evaluation is two-pass:

1. The referee receives the case and source pack only, then creates a cited reference decision with allowed alternatives, gates, safe scope, and a falsifiable validation rule.
2. The referee receives that frozen reference plus one anonymous candidate response, then returns evidence-cited scores and critical errors.

The candidate never sees the reference. Reference decisions should be reused across every compared model for that certification version. A model is not certified from average score alone: methodology-pass rate, critical-error rate, repeated-run spread, cost, and latency all belong in the report.

Render the two prompts directly when reviewing/refining a reference:

```bash
mediabuyerbench expert-reference-prompt \
  --case cases/public_lite/google/cpa_spike_diagnosis_001.json > reference-prompt.txt

mediabuyerbench expert-review-prompt \
  --case cases/public_lite/google/cpa_spike_diagnosis_001.json \
  --response candidate.md \
  --reference reference.json > review-prompt.txt
```

For a repeatable candidate run, the certification runner creates missing references once and then reuses them:

```bash
python scripts/run_google_search_certification.py \
  --response-dir path/to/candidate-responses \
  --candidate-id model-name \
  --referee-model gpt-5.6-sol \
  --reference-dir private/references/google_search_certification_v1 \
  --suite private/google_search_certification_v1.json \
  --source-pack private/google_search_certification_sources_v1.json
```

Generate those candidate responses with the no-tools runner. It records model identity and wall-clock latency but never exposes source-pack or reference-decision files to the candidate:

```bash
python scripts/run_google_search_candidates.py \
  --candidate gpt-5.6-terra \
  --output-dir .runs/google_search_candidates_v1_terra
```

Omit `--suite` and `--source-pack` to run the public synthetic demonstration. Do not change a frozen case, source pack, referee model, or scoring rule in place. Version the suite and rerun all candidates instead.

## Contributing cases

A good case should contain a real media-buyer trap and specify the facts that must be true before a recommendation is safe:

- scope or conversion-definition mismatch
- platform CPA lies
- tracking is broken
- CTR moves opposite to profit
- audience fatigue vs offer problem
- cheap traffic with weak downstream quality
- high CPL but high sales quality
- budget cap vs rank/relevance constraint
- margin/inventory issue hidden behind ROAS
- a prerequisite is explicitly incomplete (for example, CRM identity coverage) but the tempting action is to make the dependent bid-goal change immediately
- two metrics support opposite actions, so the answer must state the decision hierarchy and a counterfactual or experiment
- attribution lag, change history, and reporting window make a before/after conclusion invalid
- a cheap attributed channel is already saturated or has no evidence of incrementality

Cases should separate observed facts, a supported hypothesis, and a proven cause. If an action depends on a business fact, encode that fact in the packet—for example, excluding cat-boarding queries is only safe when the advertiser does not offer cat boarding. When the packet is a selected extract, require review of the complete report before broader changes.

Keep public cases synthetic. Do not include customer IDs, domains, exact spend, private search terms, or real account exports.

## License

MIT
