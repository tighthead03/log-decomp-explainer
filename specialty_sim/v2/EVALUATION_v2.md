# v2 evaluation: counterfactual attribution vs v1, 90 randomised worlds

Each world draws its own events (8 per world, from 9 types with random scope, size, timing and ramps), logs them with date errors of up to ±30 days, adds one decoy (logged, no effect) and one unlogged event. Truth per event = actual 2025 eq28 minus a re-run with only that event switched off.

Three splits:
- **Calibration (1001–1030):** used to set one interval-width factor per channel family.
- **Holdout used in debugging (1031–1060):** I looked at four of these while fixing the renewal maths, so they are no longer clean.
- **Fresh holdout (1061–1090):** generated after the estimator was frozen (md5 `61f55630…`). **These are the honest numbers.**

## Results against the pass criteria

| Criterion | v2 fresh holdout | v1 fresh holdout | Pass? |
|---|---|---|---|
| Direction errors on claimed drivers | **0** (0 in all 90 worlds) | 0 (7 across all splits) | ✅ |
| Hard ranking flips among material drivers | **0 / 158 pairs** (0 in all 90 worlds) | 22 / 158 | ✅ |
| 95% interval coverage of truth | **97.1%** (94.6–97.1% across splits) | no intervals | ✅ |
| Decoy confirmed (99.9% "Confirmed" tier) | **0 / 29** (0 / 84 across all splits) | 3 attributed | ✅ |
| Decoy at the 95% "Probable" tier | 1 / 29 | | expected at 95% |
| Point accuracy, pooled | **91.5%** | 78.3% | |
| Median world accuracy | 91.2% | 76.7% | |
| Unlogged event surfaced as unexplained driver | **30 / 30**, median size error 12% | not sized | |
| Material events (≥300 eq28) left "not distinguishable" | 4 / 116 at 95%; 13 / 116 at 99.9% (97.6% of material volume still confirmed) | 13 missed | |
| Event pairs reported jointly ("cannot be separated") | 2 | n/a | |

Accuracy = 1 − Σ|estimate − truth| / Σ|truth| over logged real drivers. v1 swings from 60% to 78% between splits, so its accuracy depends heavily on which events happen. v2 holds at 88–92%.

## Accuracy and coverage by event type (fresh holdout, drivers sized alone)

| Type | Accuracy v2 | Coverage | Note |
|---|---|---|---|
| New drug / LDD | 100% | 100% | Observed directly |
| Persistence programme | 93.5% | 100% | Late-year starts are the weak spot (few observable months) |
| Intake delay | 90.8% | 88% | Needed the TAT channel: delays cost therapy days, not only conversions |
| 90-day mandate | 89.5% | 84% | **Below target coverage.** The expected-path approximation in the switch month |
| Referral shift (region/payer) | 87.6% | 100% | |
| Sales push (linked factors) | 84.8% | 100% | |
| PA policy | 82.6% | 100% | Small drivers (mean ~80 eq28); relative error inflates |
| Biosimilar interchange | 72.1% | 100% | Small net effects (~300 eq28). v1 was −100% here |

## What made the difference (each fix was measured)

1. **Counterfactual sizing instead of residual subtraction.** Global events no longer absorb unclaimed change. This is the largest gain, at a cost of roughly 2x in compute.
2. **Fill-by-fill renewal path.** The "all refills 30-day or all 90-day" approximation missed the switch-month pull-forward. One world went from 2.9k to 4.5k against a true 4.4k.
3. **TAT channel.** Intake delays went from 74% to 93% of truth.
4. **End dates move with profiled starts.** Temporary events kept the logging error at their end date.
5. **Collinearity groups.** A decoy logged with the same scope as a real event took +702 while the real one took −273. Now they are reported jointly at +481 against a true +337.
6. **Mechanical fill frequency.** A 90-day mandate expects fewer fills per patient. Treating that as a persistence effect created a spurious coefficient.
7. **Expected referral counts per cell.** A practice that drops to zero has no observed referrals left to "add back".
8. **Per-channel calibration.** Persistence/days-supply intervals needed 2.57x widening; conversion needed 1.02x. A single global factor would over-widen some and under-cover others.

## Remaining limits

- **The 90-day mandate's interval coverage is 84%,** under the 95% target. Treat mandate intervals as optimistic until the switch-month model is improved.
- **Late-year events** have few observable months, so persistence effects starting after September are extrapolated.
- **An unlogged event can coincide with a logged one of the same scope and timing.** The data can't separate them, and the logged label wins. That happened once in 90 worlds.
- **The simulator is still friendlier than production.** Event shapes are steps or linear ramps, there are no data-quality defects, and the estimator's model structure is close to the simulator's. Expect real-world accuracy below 91% and treat this as an upper bound to validate against known historical events.
- **Truth is defined one event at a time.** Events interact: in one world, the sum of single impacts was 2.4k against 2.9k jointly. The exec residual absorbs the interaction.

## Exec output rules (`exec_report.py`)

- **Confirmed:** calibrated 99.9% interval excludes zero. No false confirmation in 90 worlds.
- **Probable:** 95% excludes zero, 99.9% does not.
- **Joint:** cannot be separated; one combined figure.
- **Not distinguishable:** shown as zero, never as a number.
- **Unexplained driver: investigate:** discovered step with no logged cause, sized.
- **Ranks are shared where intervals overlap.** The residual reconciles to the year-on-year change exactly.
