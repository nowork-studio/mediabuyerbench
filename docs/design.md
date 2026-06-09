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

Future versions should add:

1. Expert rubric scoring for seniority and prioritization.
2. LLM judge scoring with fixed judge prompts and model versions.
3. Interactive tools for ads, CRM, ecommerce, creative, and landing-page data.
4. Sandbox mutations with approval-gated state changes.
5. Leaderboard submission artifacts.

## Case quality bar

A case should encode a senior paid-media decision, not trivia about platform APIs. Good cases include traps: misleading platform CPA, weak tracking, creative fatigue, lead-quality gaps, low-margin ROAS, and budget changes that should not happen yet.
