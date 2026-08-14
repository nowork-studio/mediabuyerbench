# Design notes

MediaBuyerBench starts as a static benchmark because case quality matters more than agent infrastructure in v0.

## Borrowed patterns

- SWE-bench: reproducible package and later verified splits.
- τ-bench: future domain policies, tools, tasks, and simulated users.
- WebArena: self-contained task configs with explicit eval blocks.
- OSWorld: frozen environment snapshots for future interactive modes.
- HELM: multi-axis reporting instead of one opaque score.
- MLE-bench: future leaderboard artifacts, comparable settings, multiple seeds.

## Public-lite v1 scoring

The original three-case cross-platform smoke suite was deliberately broad and saturated: all sampled models scored 95. The replacement suite is five static Google Ads analyst cases, grouped by evaluation shape:

1. Retrieval and scope
2. Calculation
3. Diagnosis
4. Recommendation
5. Safe refusal when the decision lacks comparable evidence

Each case has two deterministic layers:

- **Concept checks** for required diagnoses, actions, and safety language.
- **Assertions** for scope phrases and required numeric values, with per-value tolerance.

This is intentionally inspectable rather than a claim that free-form strategic judgment can be reduced to string matching. Scores are split by skill so an answer that is fluent but mis-scoped cannot hide behind a single aggregate score.

Future versions should add:

1. Calibrated expert rubric scoring for prioritization, trade-offs, and uncertainty.
2. LLM-judge scoring with fixed judge prompts and model versions, calibrated against experts.
3. Meta and TikTok cases only after defining a common conversion and attribution contract.
4. Interactive read-tool simulation and trajectory checks.
5. Sandbox mutations with approval-gated state changes.
6. Leaderboard submission artifacts and repeated-run reliability reporting.

## Case quality bar

A case should encode a senior paid-media decision, not trivia about platform APIs. Good cases include traps: misleading platform CPA, weak tracking, creative fatigue, lead-quality gaps, low-margin ROAS, and budget changes that should not happen yet. A case must also name its scope, conversion denominator, and deterministic evidence requirements.

### Evidence and causal calibration

Case facts should distinguish three things: what the packet directly measures, the most supported working hypothesis, and what would require more evidence to establish as causal. Do not score an answer for claiming a causal mechanism that the packet cannot prove. Likewise, only require an intervention when its operational preconditions are in the fixture. For example, a cat-boarding negative keyword is safe only if the business-services field says cat boarding is not offered.

Selected extracts are deliberately insufficient evidence for account-wide changes. Cases that show only a subset of search terms should reward the response for reviewing the complete search-terms report and match types; cases that invite broader targeting or bid changes should require a lead-quality or CRM check when that information is absent.

### Operator arbiter

The Google Search rubric embeds a versioned operator arbiter rather than relying on generic "senior reviewer" language. It is a compact, inspectable normalization of the project's Google Ads/Google Search operating skill: conversion-goal integrity, mature cohort analysis, narrow negative-keyword control, verified geo serviceability, incrementality-aware budget allocation, and reversible validation. In a judgment prompt the ordering is fixed: **case facts and explicit gates first, then arbiter method, then blind model judgment**. The arbiter is not a canonical answer and cannot fill factual gaps in a packet.

### Source-grounded expert referee

The public synthetic suite and any private certification suite use the same stronger two-pass evaluator. The expert referee receives a versioned source pack that records the permitted Google Ads mechanics and external operator guidance, then creates a reference decision from the case before seeing any candidate answer. The review pass sees the frozen reference and a single anonymous candidate response. It must cite the candidate excerpt, packet facts, reference alignment, and source-pack IDs for every dimension.

This avoids two failure modes of a generic LLM judge: rewarding an answer merely because it sounds senior, and treating an AI-generated canonical answer as unquestionable truth. The source pack cannot establish advertiser-specific facts; the packet still controls those. The paid-media reviewer approves the initial hard-case suite and any substantive new version, while the source-grounded referee makes evaluations scalable and auditable between those checkpoints. Customer-derived hard cases remain private until independently de-identified and approved for release.
