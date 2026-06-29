# Design notes

MediaBuyerBench starts as a static benchmark because case quality matters more than agent infrastructure in v0.

## Borrowed patterns

- SWE-bench: reproducible package and later verified splits.
- τ-bench: future domain policies, tools, tasks, and simulated users.
- WebArena: self-contained task configs with explicit eval blocks.
- OSWorld: frozen environment snapshots for future interactive modes.
- HELM: multi-axis reporting instead of one opaque score.
- MLE-bench: future leaderboard artifacts, comparable settings, multiple seeds.

## v0 scoring

The first scorer is deterministic concept matching. It is intentionally limited but transparent. It helps contributors see what the case is testing.

## Rubric layer

`rubrics/criteria_library.json` is a source-backed catalog of audit criteria
(threshold, machine-checkability, source authority). Cases attach criteria via
`expected.rubric`, and `mediabuyerbench/rubric.py` scores them deterministically:
ground truth is derived from each case's own `data` via `data_check`, and each
positive item earns half from coverage of those findings and half from the
`detect` diagnosis (so the data check is a real input, not decoration).
Guardrail items penalize any non-negated forbidden phrase. The matching is
substring-based — transparent but gameable — so `judge`/`hybrid` criteria are
tagged for a pinned LLM judge to take over later. See `rubrics/README.md`.

Future versions should add:

1. Expert rubric scoring for seniority and prioritization.
2. LLM judge scoring with fixed judge prompts and model versions.
3. Interactive tools for ads, CRM, ecommerce, creative, and landing-page data.
4. Sandbox mutations with approval-gated state changes.
5. Leaderboard submission artifacts.

## Case quality bar

A case should encode a senior paid-media decision, not trivia about platform APIs. Good cases include traps: misleading platform CPA, weak tracking, creative fatigue, lead-quality gaps, low-margin ROAS, and budget changes that should not happen yet.
