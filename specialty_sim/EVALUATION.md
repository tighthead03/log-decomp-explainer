# Evaluation: can the two-layer pipeline explain what changed?

Volume: 161.3k → 185.6k eq28 (+15.1%), 2024 vs 2025. Tested on 4 simulated worlds (3 development seeds, 1 untouched holdout). Ten planted events: 8 logged, 1 decoy, 1 unlogged.

## Headline

| Test | Dev seeds (3) | Holdout |
|---|---|---|
| Logged events detected | 23/24 | 8/8 |
| Decoy falsely attributed | 0/3 | No |
| Unlogged event surfaced as top "investigate" item | 3/3 | Yes |
| Impact attribution accuracy, **stock-flow identity** | 70–83% | **74%** |
| Impact attribution accuracy, simple chain identity | −62% to −88% | −110% |
| False-positive breaks in 2024 (no events) | 2–11 | 11 |

Attribution accuracy = 1 − Σ|estimated − true| / Σ|true| across events. Below zero means you'd be better off saying nothing.

**Caveat:** I developed the matching rules while looking at dev-seed ground truth, so the dev numbers are flattering. Seed 303 was built after the rules were frozen and is the honest number.

## What the simulation proves

**1. Identity choice decides the story, and the simple chain is wrong.**
`V = R × S/R × A/S × F/A × V/F` pushes every referral change through the whole patient base. On the same data it reports Referrals −17.7k and Patient base +18.8k, two large offsetting bars driven by one artefact. The stock-flow identity separates new patients (`R × S/R × Fn/S × Vn/Fn`) from the carried-in base (`Ap × C/Ap × Fc/C × Vc/Fc`). It reports Referrals −5.3k and adds an Opening patient base bar (+5.7k), which carries 2024's momentum into 2025. Event-level sizing under the simple chain overstated the cascade at +39.7k against a true +17.0k. **Use stock-flow.**

**2. Factor bars are not causes.**
Even under stock-flow, Days supply mix shows +13.8k. Most of that is Northstar's 90-day mandate, offset by a fill-frequency drop inside the same bar set. The true net impact is +3.6k. Show executives the event view. Keep the factor view for analysts.

**3. Matching on scope and date alone is useless.**
The first version attributed 179 breaks, including to the decoy. Four rules fixed it:
- Roll child-slice breaks into a parent break (same metric and sign, within ±3 months) unless the child effect is ≥1.5× larger.
- Global events only match the top-level series and only on their expected KPIs.
- A slice must share a scope dimension with the event to count as evidence.
- Breaks on unexpected KPIs ("side effects") are labelled but never used for sizing, except at exact scope.

**4. Size an event on its mechanism factors only.**
A full-volume DiD leaks concurrent events with uneven exposure. Stelara and Skyrizi already use 56/84-day supplies, so the 90-day mandate doesn't touch them, and they showed a false −1.7k to −2.1k "days supply" hit against a control group that did move. So referrals and conversion events are sized on Referrals + Conversion, and persistence events on Retention + Fill frequency.

**5. The change register is the real product.**
Expected KPIs per event are what make matching work. They also create the bias you'd expect: whatever you didn't hypothesise doesn't get sized (see E08).

## Per-event sizing (estimate ÷ truth, stock-flow)

| Event | Dev seeds | Holdout | Read |
|---|---|---|---|
| E01 Keystone PA on Dupixent | 0.76–1.00 | 1.14 | Good. Clean scope, clean control (same drug, other payers) |
| E02 Biosimilar interchange | 1.7–2.9 | 2.00 | **Systematic 2× overstatement.** Substitution shows as gross loss in Stelara cells and gross gain in a new cell. Fix: decompose at molecule-family grain (ustekinumab as one cell) so the switch nets out and only leakage shows |
| E03 LDD win | 1.00 | 1.00 | Exact; new entrants are handled in Layer 1 directly |
| E04 South vacancies | 0.71–1.09 | 1.34 | Noisy. The control (other regions) is contaminated by the unlogged Lakeside loss in the Midwest |
| E05 Staffing shortage | 0.85–1.83 | 1.66 | Overstated. A global event has no control group, so it absorbs every unsized conversion change. Correctly tiered "Probable" |
| E06 Touch cascade | 1.06–1.19 | 1.11 | Good despite no control group, because nothing else moves persistence |
| E07 90-day mandate | 1.30–1.33 | 1.31 | Consistent +30%. Calibratable |
| E08 Skyrizi push | 0–1.49 | 0 | **Detection limit.** A +25% lift on ~150 referrals/month sits at z≈3. Usually caught only through its linked conversion drop, which isn't sized because the register didn't expect it |
| E09 Decoy | Not attributed | Not attributed | Pass |
| E10 Lakeside (unlogged) | Surfaced | Surfaced | Top "no logged event: investigate" cluster every run, practice-level slice leading |

## What this means for the production build

1. **Stock-flow identity, eq28 volume, drug × payer × region cells**, with biosimilar/interchange molecules rolled into family cells.
2. **The register needs `expected_kpis` and `scope`**, an owner, and a monthly review so it gets filled in. Linked metrics (referrals ⇒ conversion) belong in the expected list for sales pushes.
3. **Evidence tiers in the exec output**: Confirmed (scoped DiD), Probable (global, no control), Unexplained. In every run the residual bar was 2–4% of 2024 volume. Show it.
4. **Unexplained clusters, triaged**: "possibly local variation of E0x" vs "no logged event: investigate". That's how the Lakeside loss gets found, at practice grain. Add prescriber/practice slices as standard.
5. **Detection floor**: effects below ~20% on slices under ~150 events/month won't be found monthly. Use weekly grain or longer windows for small slices, and say so to executives rather than implying full coverage.
6. **Keep this harness as a regression test.** Any pipeline change must pass the holdout seeds before it ships.

## Example exec attribution (dev seed, stock-flow)

| Driver | eq28 | % of 2024 | Evidence |
|---|---|---|---|
| Refill reminder touch cascade | +18,027 | +11.2% | Probable |
| Northstar 90-day mandate | +4,806 | +3.0% | Confirmed |
| Intake staffing shortage | −1,633 | −1.0% | Probable |
| South territory vacancies | −1,573 | −1.0% | Confirmed |
| Velarix LDD win | +1,472 | +0.9% | Confirmed |
| Ustekinumab biosimilar interchange | −1,277 | −0.8% | Confirmed (known 2× bias) |
| Skyrizi marketing push | +1,267 | +0.8% | Confirmed |
| Keystone Dupixent PA policy | −565 | −0.4% | Confirmed |
| Underlying trend and unexplained | +3,771 | +2.3% | Residual. Includes −1.0k from Lakeside, flagged to investigate |

Files: `eval/scorecard.csv`, `eval/multi_seed_scorecard.csv`, `eval/break_log.csv`, `eval/unexplained_clusters.csv`, `eval/exec_attribution.csv`, `eval/waterfall_*.csv`, `eval/ncpdp_reconciliation.csv`.
