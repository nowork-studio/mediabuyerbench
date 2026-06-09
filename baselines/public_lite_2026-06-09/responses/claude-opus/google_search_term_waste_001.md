## Top diagnosis
The broad-match expansion added two weeks ago is the cause. It's pulling in high-volume, low-intent queries (jobs, freebie, price-shopper) that spend and click but don't convert. Under Maximize Conversions, the bidder chased that cheaper traffic, so clicks rose but qualified bookings fell. This is a **traffic-quality** problem, not a bidding or landing-page problem.

## Evidence and denominators
- **Conversion rate (conv ÷ clicks) collapsed:** previous 20/410 = **4.9%**, current 14/515 = **2.7%** — nearly halved. CPA = spend ÷ conversions, so $1,420 ÷ 14 = **$101** vs $59. The rising CTR (6.2%→7.1%) is a red flag here, not a win — broader, looser matches click more but qualify less.
- **Wasted spend is concentrated and identifiable.** Three queries are clearly off-intent with **0 conversions on 175 clicks**:
  - `dog boarding jobs` — $144 (job seekers)
  - `free dog sitting` — $96 (freebie seekers)
  - `cheap dog kennel seattle` — $205 (price shoppers, won't pay for qualified boarding)
  - That's **$445, ~31% of spend, zero return.**
- **The intended query works:** `dog boarding near me` drove 7 of 14 conversions at ~$44 CPA — at or below target.
- Caveat on the denominator: 14 conversions is a thin sample, and booking-request conversions can lag a few days, so the current count may be understated. Treat the *direction* (junk traffic up, CVR down) as solid; the exact CPA may improve slightly with late conversions.

## Smallest safe next actions
1. **Add negative keywords now:** `jobs`, `careers`, `salary`, `hiring`, `free`, `volunteer`, and review `cheap`/`affordable` (price-shopper intent that doesn't qualify). This stops ~31% of bleed immediately and is fully reversible.
2. **Add negatives for off-service queries** — e.g. `cat boarding` if you don't board cats (otherwise route to a relevant ad group).
3. **Keep, don't cut, the winner** — `dog boarding near me` is profitable; protect its budget.
4. Mine the full search-terms report (not just top 5) for the same patterns and negative them.

## What NOT to do yet
- Don't kill broad match outright or rip out the expansion — tighten it with negatives first; you may keep the incremental qualified volume it does find.
- Don't switch bid strategy or impose a hard tCPA cap right now — the account has thin conversion signal (14), and changing strategy resets learning and can starve volume.
- Don't slash total budget reactively — the problem is *where* spend goes, not how much.
- Don't judge the week as final until conversion lag clears (give it 3–7 days).

## What to measure after
- **Conversion rate (conv ÷ clicks)** — the primary signal; should recover toward ~5%.
- **CPA** back toward $65, and qualified-booking volume holding or rising.
- **Share of spend on relevant queries** — confirm junk queries dropped to ~0.
- **Search-terms report weekly** — broad match drifts; expect to add negatives continuously.
- **Impression share / lost-to-budget** — make sure negatives didn't over-restrict and choke the winning queries.
