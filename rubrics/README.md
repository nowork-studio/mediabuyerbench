# Rubric criteria library

A reusable, source-backed catalog of audit criteria for scoring media-buyer
decisions. Each entry is a single checkable rule drawn from an authoritative
PPC / paid-social source (the ad platforms themselves, Adalysis, Optmyzr,
WordStream, Brad Geddes, and named practitioners).

Case authors can attach consistent, defensible criteria to cases by id, and a
deterministic rubric scorer (`mediabuyerbench/rubric.py`) reads them. A future
LLM-judge scorer can reuse the same vocabulary for the `judge`/`hybrid` items.

- `criteria_library.json` — the catalog (73 criteria).
- `criteria.schema.json` — JSON Schema for the catalog format.

See `cases/public_lite/google/search_term_waste_001.json` for a worked example
case with a `rubric` block, and run it with:

```bash
mediabuyerbench score \
  --case cases/public_lite/google/search_term_waste_001.json \
  --response examples/responses/google_search_term_waste_001.md \
  --rubric
```

## Why this exists

In paid media there is **no outcome ground truth** — outcomes are stochastic
and counterfactual, so you cannot grade on "did ROAS go up." What you *can*
grade is **decision quality given the data**: what a senior buyer would flag and
do. This library is that decision standard, sourced from recognized authorities
so it is defensible rather than one person's opinion.

## Anatomy of a criterion

```json
{
  "id": "g.kw.zero_conversion_waste",
  "provider": "google_ads",
  "category": "wasted_spend",
  "title": "Keyword spending with no conversions",
  "type": "programmatic",
  "authority": "expert",
  "condition": "Keyword with 0 conversions and significant click volume over the window.",
  "default_threshold": {"conversions_max": 0, "clicks_min": 150},
  "window_days": 30,
  "tunable": true,
  "source": {"name": "Adalysis — keywords with poor conversions", "url": "https://docs.adalysis.com/..."}
}
```

### `type` — how the criterion is scored
- `programmatic` — objective check computable from the case data (no LLM). 60 of 70.
- `judge` — subjective call needing an LLM-judge / human rubric. 4 of 70.
- `hybrid` — programmatic trigger, judged severity or relevance. 6 of 70.

### `authority` — how much to trust the threshold
- `official` — published by the ad platform (Google/Meta/X docs). Hard numbers.
- `expert` — published by a recognized tool/practitioner (Adalysis, Optmyzr,
  Geddes, WordStream, etc.).
- `convention` — a widely-used industry number with **no** authoritative
  published cutoff (e.g. QS < 7, Lost IS > 10%, frequency > 3). Treat as
  tunable and lower-confidence. Keep these visibly separate so the benchmark's
  "official" claims stay clean.

### Other fields
- `default_threshold` — the default numbers, as a small object. `null` for
  pure-judge criteria.
- `window_days` — the lookback the check applies over (`null` if not time-bound).
- `tunable` — whether the threshold is expected to be adjusted per case.
- `caution` — an evidence-backed exception to the rule (see Ad Strength below).
- `judge_guidance` — what an LLM-judge should assess, for `judge`/`hybrid` items.

## How to attach criteria to a case

The existing `expected` block (`required_concepts` / `forbidden_concepts`) is
unchanged and still drives the v0 concept scorer. A case may *additionally*
carry a `rubric` array that references criteria by `id`:

```json
"expected": {
  "required_concepts": [ ... ],
  "forbidden_concepts": [ ... ],
  "rubric": [
    {
      "criterion": "g.kw.zero_conversion_waste",
      "expected": "flag",
      "weight": 3,
      "skills": ["diagnosis", "google_ads"],
      "threshold_override": {"clicks_min": 25},
      "data_check": {
        "block": "Search terms, current period",
        "where": {"conversions": {"==": 0}, "clicks": {">=": 25}},
        "select": "query"
      },
      "detect": ["broad match", "irrelevant", "search term waste"]
    }
  ]
}
```

- `criterion` — the library `id` (provenance, type, authority, source).
- `expected` — what a correct answer does: `flag` / `act` / `respect`
  (positive) or `avoid` (guardrail; violation triggers `penalty`).
- `weight` / `skills` — fold into a 0-100 rubric score + per-skill breakdown,
  mirroring the concept scorer.
- `threshold_override` — per-case tweak to the criterion's `default_threshold`.
  Case data is small, so click/spend floors usually need lowering.
- `data_check` — derives the **ground truth** from the case's own `data`: it
  selects the `select` field from every row in the named `block` matching all
  `where` conditions (operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`).
  For the example above this returns the actually-wasteful search terms.
- `detect` — phrases that drive the headline score (same deterministic
  semantics as the concept scorer). When `detect` is omitted, scoring falls
  back to coverage of the `data_check` findings.

How scoring works (`mediabuyerbench/rubric.py`):

- **Ground truth** is computed from `data_check` — there is no outcome data, so
  correctness comes from how the case was constructed.
- **Headline score** is detect-phrase driven (so an abbreviated-but-correct
  answer is not punished), reported per item with full source traceability.
- **Completeness** is reported as `findings_covered / findings` — e.g. did the
  answer enumerate every wasteful term, or only address the theme?
- `judge`/`hybrid` items currently score deterministically via `detect`; they
  are tagged so a future LLM-judge can take them over without changing cases.

## Important: read these caveats before scoring with this

1. **Never use an aggregate score as ground truth.** Google's Optimization
   Score and WordStream's grade both reward *adopting suggestions*, not profit.
   Use the individual underlying checks. (See `xc.principle.no_aggregate_score_as_truth`.)
2. **Senior judgment beats checkbox-following.** The clearest example is
   `g.as.rsa_ad_strength`: an Optmyzr study of 1M+ ads found "Average" RSAs had
   the *best* CPA/ROAS, beating "Excellent." A benchmark that rewards "make Ad
   Strength Excellent" rewards the wrong answer. That criterion carries a
   `caution`, and the better signal is `g.as.fully_pinned_rsa`.
3. **Thresholds are defaults, not laws.** Adalysis exposes every threshold as
   editable and Meta states its 50-conversions rule is "not a hard rule."
   Prefer band-based scoring over hard pass/fail on borderline metrics.

## Coverage (v0.1)

73 criteria: 51 Google Ads, 10 Meta, 6 X, 3 cross-channel, 3 general.
By type: 60 programmatic, 7 hybrid, 6 judge.
By authority: 24 official, 44 expert, 5 convention.

This is a starting set focused on the highest-value, most-defensible checks. It
is not exhaustive — add criteria as cases need them, always with a `source`.

## Primary sources

- Adalysis prebuilt alert index — https://docs.adalysis.com/tools/audit/prebuilt-alert-list/
- Google Ads API recommendation types — https://developers.google.com/google-ads/api/docs/recommendations
- Optmyzr PPC audit playbook — https://www.optmyzr.com/guide/ppc-audit/
- WordStream Google Ads Performance Grader — https://www.wordstream.com/google-adwords
- Meta learning phase — https://www.facebook.com/business/help/112167992830700
- X Ads API — https://docs.x.com/x-ads-api/campaign-management
