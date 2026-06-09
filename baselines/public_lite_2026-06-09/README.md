# Public-lite baseline: GPT-5.5 vs Claude Sonnet vs Claude Opus

Run date: 2026-06-09

Scope: 3 static public-lite cases in MediaBuyerBench v0.1: Google search term waste, Meta creative fatigue, cross-channel platform CPA conflict.

Important caveat: this first split is too small and too easy. Treat these as smoke-test baselines, not a meaningful leaderboard.

## Aggregate deterministic scores

| Model | Average | Google | Meta | Cross-channel |
|---|---:|---:|---:|---:|
| gpt-5.5 | 95.0 | 85.0 | 100.0 | 100.0 |
| claude-sonnet | 95.0 | 85.0 | 100.0 | 100.0 |
| claude-opus | 95.0 | 85.0 | 100.0 | 100.0 |

## Observations

- All three models passed the current cases. The deterministic scorer gives all three 95.0 average after fixing a negated-forbidden false positive.
- GPT-5.5 was the most balanced and concise. It made clean denominator calculations and gave operator-ready next steps.
- Claude Sonnet was strong and more strategic, especially on Meta audience saturation, but added a few platform heuristics that the fixture did not prove.
- Claude Opus gave the best uncertainty/caveat language and strong measurement framing. It was slightly more verbose but still operational.
- The current scorer mainly checks concept coverage, so it does not separate senior judgment well yet. The benchmark needs more adversarial cases and rubric/judge scoring next.

## Runner settings

- GPT-5.5: `codex exec -m gpt-5.5`, read-only sandbox, no tools requested.
- Claude Sonnet: `claude --print --model sonnet`, tools disallowed.
- Claude Opus: `claude --print --model opus`, tools disallowed.

Raw responses and JSON scores are included under this directory.
