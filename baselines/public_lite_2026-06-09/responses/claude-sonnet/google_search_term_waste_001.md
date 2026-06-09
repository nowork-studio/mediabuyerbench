## Diagnosis: Broad match is serving irrelevant intent, burning ~37% of spend on zero-conversion queries

The timing is the tell. Broad match expansion was added two weeks ago; the "current" period is exactly those two weeks. CPA went from $59 to $101 — 55% over target — while conversions dropped from 20 to 14 despite spending $240 more.

---

## Evidence

**The search terms table tells the story directly:**

| Query | Spend | Conversions | CPA | Intent |
|---|---|---|---|---|
| dog boarding near me | $310 | 7 | $44 | ✅ Commercial |
| cheap dog kennel seattle | $205 | 0 | ∞ | ⚠️ Price-sensitive |
| dog boarding jobs | $144 | 0 | ∞ | ❌ Employment |
| free dog sitting | $96 | 0 | ∞ | ❌ Non-commercial |
| cat boarding seattle | $82 | 0 | ∞ | ❌ Wrong species |

Wasted spend across the four zero-conversion terms: **$527 / $1,420 = 37% of budget**.

CTR ticking up from 6.2% to 7.1% looks positive but is misleading — broad match is surfacing curiosity clicks on mismatched queries. High CTR with low conversion rate signals traffic quality degradation, not improvement.

The one term that works ("dog boarding near me") has a $44 CPA — well under target. The bidding strategy and creative are not the problem.

---

## Recommended next actions (smallest safe moves)

**1. Add negative keywords immediately — these are near-zero-risk:**
- `jobs` (phrase match — catches "dog boarding jobs", "kennel jobs", etc.)
- `free` (phrase match)
- `employment`, `hiring`, `career`, `salary`, `volunteer` (phrase)
- `cat`, `cats` (phrase — unless you board cats)

Do this in the shared negative keyword list, not just at campaign level, so it propagates to any future campaigns.

**2. Review the full search terms report (not just these five)** — these are the visible top spenders. Scroll to the long tail. Look for: pet sitting vs. boarding (DIY intent), breed-specific grooming terms, veterinary queries, anything with "how to."

**3. Add "cheap" to the monitoring list, not immediately negated.** "Cheap dog kennel seattle" had 72 clicks and 0 conversions, which is a strong signal to negative it — but it's conceivable some of those are price-sensitive buyers who might convert on a deal. Check if any bookings that came in this period mentioned price. If not, negative it in week 2.

---

## What NOT to do yet

- **Don't switch off broad match.** "Dog boarding near me" is delivering at $44 CPA. The issue is match quality, not the match type itself.
- **Don't cut budget or lower tCPA targets.** That would squeeze the good traffic before you've removed the bad. Fix the input first.
- **Don't restructure campaigns or switch bidding strategies.** You have enough conversion signal and a working core term. Don't reset the learning period.

---

## What to measure after the change

- **Search terms report, daily for 7 days** — confirm the negatives are holding and watch for new irrelevant patterns surfacing
- **CPA on the surviving traffic** — it should drop quickly once $527 of wasteful spend is stopped
- **Impression share on "dog boarding near me"** — verify the negatives didn't accidentally block anything adjacent to that term
- **Conversion volume** — if conversions drop further after negatives, the problem is upstream (landing page, bid, etc.) and not traffic quality

**Expected outcome:** Removing the four zero-conversion terms saves ~$527/period. If conversion volume holds at 14, CPA drops from $101 to roughly $63 — just under target. If the remaining traffic is cleaner, conversion rate should improve further.
