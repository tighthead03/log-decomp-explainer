# Specialty pharmacy volume drivers

### User

I want to to develop an analytical solution to identify what factors caused specialty pharmacy volume to change between two time periods but I also want to know what changed in those periods that caused the changes. The output of this solution also needs to b'Ã© explainable so that I can send it to an executive for them to consume.

### Claude

*[Tool status: Read memory â€” Done]*

Your decomposition and partitioner tell you **where** volume moved and **by how much**. They do not tell you **why**. The trap here is a nice waterfall with factor labels like â€œconversion -4%â€ that leaves the exec asking the obvious next question with nothing to back it up. So treat this as two separate layers and keep them visibly separate in the output.

**Layer 1: Accounting (where and how much)**

- Volume = referrals Ã— conversion Ã— active patients retained Ã— fills per patient Ã— units per fill, cut by drug Ã— payer Ã— referral source Ã— region.
- Use your existing LMDI and recursive partitioner to find the few cells that actually matter.
- Fix new-entrant handling first. Report new and lost cells (new drugs, new payers, LDD wins and losses) as their own bars. Donâ€™t push them through log-mean workarounds.
- Normalise for business days and seasonality. Q1 re-authorisations alone can produce a â€œconversion problemâ€ that isnâ€™t real.

**Layer 2: Causal (what changed)**

1. **Change register.** A structured, dated log of events, each tagged with the dimensions it touches: payer PA or formulary changes, biosimilar launches, LDD access, territory changes, staffing and TAT shifts, process changes. This is the hard part. The maths is easy.
2. **Change-point detection** on each factorâ€™s time series inside the material cells (PELT or BSTS) to date when each factor broke.
3. **Matching.** Line up the break dates and affected dimensions with events in the register. If both line up, the event is a candidate cause.
4. **Quantification.** Where thereâ€™s a natural comparison, use diff-in-diff or synthetic control (for example, the same drug at payers that didnâ€™t change policy). Where there isnâ€™t, label the driver â€œassociatedâ€ and donâ€™t claim causation.
5. **Mechanism.** Drill the factor into workflow reason codes (PA denials, abandonment, MD clarification loops) so â€œconversion fellâ€ becomes â€œdenials at Payer X rose after the 1 March policy changeâ€.

**Layer 3: Exec output**

- One waterfall from Period A to Period B, with a bar labelled **Unexplained**. Showing it builds trust.
- The top 3â€“5 drivers, one line each: what changed, when, impact, evidence tier (Confirmed / Probable / Unattributed), controllable or not, owner, action.
- Use an LLM only to draft the narrative from the structured outputs. Every number comes from the deterministic pipeline.

**Where a sceptic would push back**

- **The identity shapes the story.** Pick factors that map to owners and levers, not ones that are just mathematically convenient.
- **Factors arenâ€™t independent.** Sales pushing marginal referrals will raise referrals and lower conversion together. LMDI will show two drivers when there was one decision. Flag linked factors explicitly.
- **The register is biased toward what got logged.** Youâ€™ll find causes where you wrote things down. Without the Unexplained bar, the exec gets a confident story that isnâ€™t true.
- **Two periods is a thin basis.** Run the factor trends monthly and present the two-period comparison as a summary of that series. Otherwise youâ€™ll mistake noise and timing for drivers.
- **Execs act on controllability, not on the size of a factor.** A -3% payer mix shift you canâ€™t influence matters less than a -1% TAT slip you can fix.

The fastest proof is to pick one past period where you already know the answer, such as a known payer policy change, and check whether the pipeline finds it without being told. If it canâ€™t recover a cause you already know, it wonâ€™t find the ones you donâ€™t. Want me to sketch the change-register schema and the exec one-pager layout next?

---

### User

Develop a custom dataset with all of these features and mock up NCPDP messages so that we can simulate the effectiveness

### Claude

Iâ€™ll build a synthetic specialty pharmacy dataset with known events planted in it, render the NCPDP messages from the same simulation, and add an evaluation harness that scores how well the decomposition and causal layers recover those events.

*[Tool status: Ran 40 commands, updated tasks, created 8 files (created `sim.py`, `decomp.py`, `build_dataset.py`, `render_ncpdp.py`, `parse_ncpdp.py`, `eval_pipeline.py`, `README.md`, `EVALUATION.md`)]*

Iâ€™ve built the dataset, the NCPDP messages and an evaluation harness. It works, but less well than the earlier design implied: it finds what changed reliably, and the size it puts on each driver is about 74% accurate.

**Whatâ€™s in the zip**

- **Dataset:** a simulated specialty pharmacy covering 2023â€“2025, with about 47k referrals, 400k fills, 450k claims, 30k prior authorisations and daily intake staffing data. Volume is measured in 28-day equivalents so tablets and pens are comparable.
- **Planted events:** ten events with known effects, including payer policy changes, a biosimilar launch, a new limited-distribution drug, rep vacancies, a staffing shortage, the refill reminder cascade and a 90-day supply mandate. One is a decoy logged with no real effect, and one is a real prescriber group loss that was never logged.
- **NCPDP messages:** e-prescriptions (NewRx), ePA request and response pairs, and pharmacy claims with reversals. A parser rebuilds the tables from the messages alone and matches them exactly on all 14 reconciliation checks, so the results should carry over to a pipeline fed by real messages.

**Results** (fresh test set built after Iâ€™d finished tuning the rules)

- **Detection:** all 8 logged events were found, the decoy was correctly left out, and the unlogged prescriber loss came top of the â€œno logged event: investigateâ€ list.
- **Sizing:** the stock-flow approach got 74% of event impact right. The simple chain I sketched earlier (referrals Ã— conversion Ã— patients per start Ã— ...) scored -110%, meaning it does worse than giving no answer. It puts every referral change through the whole patient base and produces two large bars that cancel out. Drop it.
- **Size of each driver:** strong for the payer policy, new drug and refill cascade events. The biosimilar switch is overstated by about 2x every time, because the decomposition counts the switch as a loss in one drug and a gain in another. The fix is to treat the molecule family as one unit.
- **Detection limit:** a 25% referral lift on about 150 referrals a month is borderline at monthly grain. The Skyrizi push was usually caught only through its conversion side effect.

**What it takes to work**

- **The change register:** the maths was useless until each logged event said which KPIs it was expected to move. Matching on scope and date alone produced 179 attributed breaks and blamed the decoy.
- **Mechanism-only sizing:** each event has to be sized on the factors behind its mechanism, not on total volume, or concurrent events leak into each otherâ€™s numbers.

I tuned the matching rules while looking at the answers on three simulations, so their 70â€“83% accuracy flatters the method. The 74% figure comes from a fourth simulation built after the rules were frozen, and itâ€™s the honest number.

The natural next step is to build the one-page exec summary on top of the attribution output, with evidence tiers and the unexplained bar kept visible.

#### Artifact: `EVALUATION` (`Document Â· MD`)

```markdown
# Evaluation: can the two-layer pipeline explain what changed?

Volume: 161.3k â†’ 185.6k eq28 (+15.1%), 2024 vs 2025. Tested on 4 simulated worlds (3 development seeds, 1 untouched holdout). Ten planted events: 8 logged, 1 decoy, 1 unlogged.

## Headline

| Test | Dev seeds (3) | Holdout |
|---|---|---|
| Logged events detected | 23/24 | 8/8 |
| Decoy falsely attributed | 0/3 | No |
| Unlogged event surfaced as top "investigate" item | 3/3 | Yes |
| Impact attribution accuracy, **stock-flow identity** | 70â€“83% | **74%** |
| Impact attribution accuracy, simple chain identity | âˆ’62% to âˆ’88% | âˆ’110% |
| False-positive breaks in 2024 (no events) | 2â€“11 | 11 |

Attribution accuracy = 1 âˆ’ Î£|estimated âˆ’ true| / Î£|true| across events. Below zero means you'd be better off saying nothing.

**Caveat:** I developed the matching rules while looking at dev-seed ground truth, so the dev numbers are flattering. Seed 303 was built after the rules were frozen and is the honest number.

## What the simulation proves

**1. Identity choice decides the story, and the simple chain is wrong.**
`V = R Ã— S/R Ã— A/S Ã— F/A Ã— V/F` pushes every referral change through the whole patient base. On the same data it reports Referrals âˆ’17.7k and Patient base +18.8k, two large offsetting bars driven by one artefact. The stock-flow identity separates new patients (`R Ã— S/R Ã— Fn/S Ã— Vn/Fn`) from the carried-in base (`Ap Ã— C/Ap Ã— Fc/C Ã— Vc/Fc`). It reports Referrals âˆ’5.3k and adds an Opening patient base bar (+5.7k), which carries 2024's momentum into 2025. Event-level sizing under the simple chain overstated the cascade at +39.7k against a true +17.0k. **Use stock-flow.**

**2. Factor bars are not causes.**
Even under stock-flow, Days supply mix shows +13.8k. Most of that is Northstar's 90-day mandate, offset by a fill-frequency drop inside the same bar set. The true net impact is +3.6k. Show executives the event view. Keep the factor view for analysts.

**3. Matching on scope and date alone is useless.**
The first version attributed 179 breaks, including to the decoy. Four rules fixed it:
- Roll child-slice breaks into a parent break (same metric and sign, within Â±3 months) unless the child effect is â‰¥1.5Ã— larger.
- Global events only match the top-level series and only on their expected KPIs.
- A slice must share a scope dimension with the event to count as evidence.
- Breaks on unexpected KPIs ("side effects") are labelled but never used for sizing, except at exact scope.

**4. Size an event on its mechanism factors only.**
A full-volume DiD leaks concurrent events with uneven exposure. Stelara and Skyrizi already use 56/84-day supplies, so the 90-day mandate doesn't touch them, and they showed a false âˆ’1.7k to âˆ’2.1k "days supply" hit against a control group that did move. So referrals and conversion events are sized on Referrals + Conversion, and persistence events on Retention + Fill frequency.

**5. The change register is the real product.**
Expected KPIs per event are what make matching work. They also create the bias you'd expect: whatever you didn't hypothesise doesn't get sized (see E08).

## Per-event sizing (estimate Ã· truth, stock-flow)

| Event | Dev seeds | Holdout | Read |
|---|---|---|---|
| E01 Keystone PA on Dupixent | 0.76â€“1.00 | 1.14 | Good. Clean scope, clean control (same drug, other payers) |
| E02 Biosimilar interchange | 1.7â€“2.9 | 2.00 | **Systematic 2Ã— overstatement.** Substitution shows as gross loss in Stelara cells and gross gain in a new cell. Fix: decompose at molecule-family grain (ustekinumab as one cell) so the switch nets out and only leakage shows |
| E03 LDD win | 1.00 | 1.00 | Exact; new entrants are handled in Layer 1 directly |
| E04 South vacancies | 0.71â€“1.09 | 1.34 | Noisy. The control (other regions) is contaminated by the unlogged Lakeside loss in the Midwest |
| E05 Staffing shortage | 0.85â€“1.83 | 1.66 | Overstated. A global event has no control group, so it absorbs every unsized conversion change. Correctly tiered "Probable" |
| E06 Touch cascade | 1.06â€“1.19 | 1.11 | Good despite no control group, because nothing else moves persistence |
| E07 90-day mandate | 1.30â€“1.33 | 1.31 | Consistent +30%. Calibratable |
| E08 Skyrizi push | 0â€“1.49 | 0 | **Detection limit.** A +25% lift on ~150 referrals/month sits at zâ‰ˆ3. Usually caught only through its linked conversion drop, which isn't sized because the register didn't expect it |
| E09 Decoy | Not attributed | Not attributed | Pass |
| E10 Lakeside (unlogged) | Surfaced | Surfaced | Top "no logged event: investigate" cluster every run, practice-level slice leading |

## What this means for the production build

1. **Stock-flow identity, eq28 volume, drug Ã— payer Ã— region cells**, with biosimilar/interchange molecules rolled into family cells.
2. **The register needs `expected_kpis` and `scope`**, an owner, and a monthly review so it gets filled in. Linked metrics (referrals â‡’ conversion) belong in the expected list for sales pushes.
3. **Evidence tiers in the exec output**: Confirmed (scoped DiD), Probable (global, no control), Unexplained. In every run the residual bar was 2â€“4% of 2024 volume. Show it.
4. **Unexplained clusters, triaged**: "possibly local variation of E0x" vs "no logged event: investigate". That's how the Lakeside loss gets found, at practice grain. Add prescriber/practice slices as standard.
5. **Detection floor**: effects below ~20% on slices under ~150 events/month won't be found monthly. Use weekly grain or longer windows for small slices, and say so to executives rather than implying full coverage.
6. **Keep this harness as a regression test.** Any pipeline change must pass the holdout seeds before it ships.

## Example exec attribution (dev seed, stock-flow)

| Driver | eq28 | % of 2024 | Evidence |
|---|---|---|---|
| Refill reminder touch cascade | +18,027 | +11.2% | Probable |
| Northstar 90-day mandate | +4,806 | +3.0% | Confirmed |
| Intake staffing shortage | âˆ’1,633 | âˆ’1.0% | Probable |
| South territory vacancies | âˆ’1,573 | âˆ’1.0% | Confirmed |
| Velarix LDD win | +1,472 | +0.9% | Confirmed |
| Ustekinumab biosimilar interchange | âˆ’1,277 | âˆ’0.8% | Confirmed (known 2Ã— bias) |
| Skyrizi marketing push | +1,267 | +0.8% | Confirmed |
| Keystone Dupixent PA policy | âˆ’565 | âˆ’0.4% | Confirmed |
| Underlying trend and unexplained | +3,771 | +2.3% | Residual. Includes âˆ’1.0k from Lakeside, flagged to investigate |

Files: `eval/scorecard.csv`, `eval/multi_seed_scorecard.csv`, `eval/break_log.csv`, `eval/unexplained_clusters.csv`, `eval/exec_attribution.csv`, `eval/waterfall_*.csv`, `eval/ncpdp_reconciliation.csv`.
```

*(Also attached: `Specialty volume sim` (`ZIP` â€” No preview available))*

---

### User

how can we improve this result?

### Claude

The fastest gains come from fixing how you size events, but fix the test first. Right now itâ€™s partly flattering and partly unfair.

**1. Make the test honest**

- **Every seed has the same 10 events.** Only the noise changes, so a holdout seed proves robustness to noise, not to new kinds of events, and I tuned the rules on these events. Randomise the event types, timing, size and scope on each seed. Add the messy cases real data has: gradual ramps, register dates off by a few weeks, overlapping scopes, and late logging.
- **The accuracy score mixes two separate failures.** The unlogged prescriber loss counts as error, but no pipeline can size an event nobody logged. Score register coverage separately from pipeline accuracy. On the holdout, leaving that event out of the score lifts accuracy from 74% to about 77%.
- **The refill cascade dominates the score.** It accounts for about 55% of all true impact. Also report per-event accuracy and whether the direction is right, so small events arenâ€™t hidden.

**2. Replace â€œsubtract whatâ€™s leftâ€ with a counterfactual model (biggest gain)**
On the holdout, about 70% of the error comes from four drivers: the refill cascade (+11%), the staffing shortage (+66%), the 90-day mandate (+31%) and the South vacancies (+34%). That error comes from sizing events one at a time and giving global events whatever is left over. Instead:

- Fit a conversion model at referral level (payer, drug, month and event indicators) and a discrete-time persistence model at patient level with time-varying event flags. If you already have an attrition hazard model, thatâ€™s the second half.
- Size each event the way the ground truth does: switch its indicator off, rerun the volume calculation, and take the difference. Overlapping events are then estimated jointly instead of in sequence. Use Shapley allocation where they interact.
- Keep the stock-flow decomposition as the executive waterfall, but reconcile it to the model rather than using it to size events.

This also fixes the staffing shortage, which currently has no control group and soaks up every conversion change nobody else claims.

**3. Use two levels of evidence for detection**
Events in the register are written-down hypotheses. Test them before looking at the data: one-sided, at weekly grain, with their linked KPIs included (a sales push should list conversion as well as referrals). Keep the strict penalty, plus false-discovery-rate control across slices, for unexplained breaks only. That should recover the Skyrizi push without letting more false positives into discovery mode. It may also cut the current 30â€“40 unexplained breaks.

**4. Use better control groups**

- Match controls on drug mix, or use synthetic control, instead of â€œall other cellsâ€. The 90-day mandateâ€™s +31% bias is mostly that Northstarâ€™s drug mix differs from everyone elseâ€™s.
- Remove cells with unexplained breaks from the control pool. The Lakeside loss in the Midwest is contaminating the South vacancy estimate.

**5. Fix the grain for switching events**
Treat Stelara and its biosimilar as one ustekinumab family. The switch then nets out and only the leakage to competitors shows, which removes the systematic 2x overstatement.

**What to expect:** steps 2 and 5 should get the holdout into the high 80s or low 90s. Treat that as a guess until itâ€™s tested. If accuracy then drops on randomised events, the tuning was fitted to these ten scenarios, and step 1 is what tells you.

Want me to build steps 1 and 2 next? Step 2 is the bigger change.

---

### User

what approach is this using?

### Claude

The current pipeline combines four standard methods, one per stage:

1. **Decomposition: additive LMDI-I index decomposition.** It splits the volume change into factor contributions using a stock-flow identity: new patients (referrals Ã— conversion Ã— fills per start Ã— days supply) plus the carried-in base (opening patients Ã— retention Ã— fills per patient Ã— days supply). This is the energy-economics method, applied to a patient funnel.
2. **Detection: change-point detection on year-on-year differences.** Each month is compared with the same month last year to remove seasonality. PELT (Pruned Exact Linear Time, via the `ruptures` library) then finds where that gap shifts, using an L2 cost and a penalty scaled to the noise. Breaks are kept only if they pass z â‰¥ 3 and an 8% effect threshold.
3. **Attribution: rule-based event matching.** Each break is matched to the change register on scope (drug, payer, region), timing (one month before to two months after the event) and expected KPIs. Child-slice breaks roll up into their parent. Whatever is left becomes clusters flagged as unexplained.
4. **Sizing: difference-in-differences on the LMDI factor contributions.** For events limited to a drug, payer or region, it compares the affected cells with a control group on the factors behind the eventâ€™s mechanism. For global events, which have no control group, it uses whatever change is left after the scoped events are subtracted.

Steps 1 and 4 together amount to an **accounting decomposition with quasi-experimental adjustments**. It is transparent and reconciles exactly to total volume. Its weakness is that it sizes events one at a time and relies on control groups, which is where the error comes from.

The upgrade I proposed (step 2) is **structural counterfactual simulation**. You fit causal response models (logistic regression for conversion, a discrete-time hazard model for persistence), switch each event off, and re-simulate volume. Where events overlap, Shapley values split the shared effect between them. Thatâ€™s the same method used to create the ground truth, which is why it should score better. It also means the evaluation is partly marking its own homework, and thatâ€™s another reason to randomise the events first.

---

### User

where does the accuracy fall down?

### Claude

On the holdout seed, the pipeline gets the direction right for every event it sizes. All of the error is in magnitude, and it falls into four failure modes.

**Figure: Where the 26% error comes from (holdout seed, absolute eq28 error)**

| Category | Absolute error (eq28) |
| --- | --- |
| Touch cascade | 1,746 |
| Staffing shortage | 1,267 |
| 90-day mandate | 1,141 |
| Lakeside (unlogged) | 936 |
| Skyrizi push | 921 |
| Biosimilar switch | 771 |
| South vacancies | 667 |
| Keystone PA | 111 |

The holdout error totals 7.6k eq28 against 28.5k of true impact.

1. **Global events with no control group (40% of the error).**
   - The touch cascade is off by +11%, but itâ€™s a 16k event, so thatâ€™s still the largest absolute miss.
   - The staffing shortage is off by +66%. Global events get whatever change is left after the scoped events are subtracted, so the shortage soaks up every conversion movement nobody else claims, noise included.
2. **Mismatched control groups (24%).**
   - The 90-day mandate is consistently +31%. Northstarâ€™s drug mix differs from the â€œall other payersâ€ comparison group.
   - The South vacancies are +34%. Their comparison group includes the Midwest, which is dragged down by the unlogged Lakeside loss, so one undetected event distorts another eventâ€™s estimate.
3. **Events never sized (25%).**
   - Lakeside was never logged, so it canâ€™t be sized. Thatâ€™s a gap in the change register, not in the method.
   - The Skyrizi push was missed. A 25% lift on about 150 referrals a month is below what the monthly test can detect, and its conversion side effect is labelled but not sized.
4. **Wrong grain (10%).** The biosimilar switch is overstated about 2x. It shows up as a gross loss in Stelara and a gross gain in the biosimilar, not as the small net leakage it really is.

**Why this matters for an exec.** The percentages hide where it hurts. Large drivers are sized well in relative terms, but the medium ones (500 to 2,000 eq28) are 30% to 100% off. Thatâ€™s enough to reorder the ranking: the pipeline puts staffing (âˆ’3.2k) clearly above the South vacancies (âˆ’2.6k), when the true figures are almost identical (âˆ’1.9k each). An exec acting on the ranking would prioritise the wrong fix.

**What to work on first.** Global-event sizing is the biggest bucket and the one the counterfactual model fixes directly. Improving the control groups is the cheapest gain, and grouping the biosimilar with Stelara is a one-line change. Detection misses are a register and test-design problem, not a modelling one.

---

### User

I need this as accurate as possible any errors can not be tollerated

### Claude

Zero error isnâ€™t achievable, and Iâ€™d be misleading you if I said otherwise. What you can achieve is that no number reaching the executive is wrong in a way that matters, and that every number says how sure it is.

**Why zero is off the table**

- **Noise.** The Keystone PA effect is about 0.4% of annual volume, smaller than month-to-month noise. Even in a simulation with a perfect answer key, any estimator has a minimum error.
- **Real data is harder.** Thereâ€™s no answer key, the change register will be incomplete, and some confounders are never recorded. The Lakeside loss canâ€™t be sized by any model if nobody logs it.
- **Promising zero is how errors reach executives.** A pipeline that has to look certain will present guesses as facts.

**What â€œerrors canâ€™t be toleratedâ€ should mean in practice**

1. **The accounting is exact.** The waterfall reconciles to the total volume and the NCPDP parse matches the tables exactly. Both already hold.
2. **No wrong directions.** No driver reported as up when it was down. This already holds for every event the pipeline sizes.
3. **No wrong rankings among material drivers.** This currently fails: staffing and the South vacancies come out in the wrong order.
4. **Every figure carries an interval, and the intervals are calibrated.** A 95% interval should contain the true impact at least 95% of the time across many simulated worlds. This is the real accuracy guarantee.
5. **No overclaiming.** Anything below the detection floor, or with an interval that crosses zero, is reported as â€œnot distinguishableâ€, never as a number.

**How to get there**

- **Counterfactual models.** Fit a conversion model and a persistence hazard model, switch each event off, re-simulate volume, and use Shapley values to split overlapping effects. This removes most of the 40% of error coming from global events.
- **Bootstrap confidence intervals** on every event estimate, resampling patients and referrals.
- **Better control groups.** Synthetic or drug-mix-matched controls, with contaminated cells removed from the pool.
- **Biosimilar grouping.** Treat Stelara and its biosimilar as one molecule family so only the leakage shows.
- **Two-level testing.** Pre-registered tests for logged events, and false-discovery-rate control for unexplained breaks.
- **A harder test.** At least 50 simulated worlds, each with randomised event types, sizes, timing and messy registers. Pass only if there are zero direction errors, zero ranking errors among material drivers, 95% interval coverage, and the decoy is never attributed.
- **Process controls.** Monthly register review, sign-off from the event owner before a â€œProbableâ€ driver goes to an executive, and the harness run as a regression test on every change.

A realistic target is point estimates in the high 80s to low 90s for material drivers, with honest intervals and nothing overclaimed. In production, the biggest remaining risk will be how complete the change register is, not the model.

Should I build this version: the randomised harness, the counterfactual models, and calibrated intervals, scored against those pass criteria?

---

### User

yes build this approach

### Claude

Iâ€™ll build this as a randomised test across many simulated worlds, with a new counterfactual estimator and calibrated intervals, then score it against the strict pass criteria and compare it with the current pipeline on the same worlds.

*[Tool status: Ran 13 commands, updated tasks, created 2 files]*

Both pipelines run on the same worlds. Launching 60 randomised worlds in the background (seeds 1001â€“1030 for calibration, 1031â€“1060 as untouched holdout) while I write the scoring.

*[Tool status: Ran 18 commands, updated tasks, created a file]*

The 90-day mandate in world 1049 is now accurate (4,482 against 4,367), and persistence in 1054 is closer. Worlds 1031 and 1043 are still off, so checking whatâ€™s specific to them.

*[Tool status: Ran 4 commands]*

While the fresh worlds run, Iâ€™ll build the exec-facing output: evidence tiers, calibrated intervals, joint groups, unexplained drivers, and a residual that reconciles to the year-on-year change.

*[Tool status: Ran 11 commands, created 3 files, updated tasks]*

v2 meets three of the four strict criteria outright on 30 fresh test worlds, generated after I froze the code. The fourth (interval coverage) passes on average but fails for one event type, the 90-day supply mandate.

| Fresh test worlds | v2 | v1 |
| --- | --- | --- |
| Wrong-direction drivers | **0** | 7 across all 90 worlds |
| Clear rankings shown in the wrong order | **0 of 158 pairs** | 22 |
| 95% intervals containing the true value | **97%** | no intervals |
| Decoys labelled â€œConfirmedâ€ | **0** (0 of 84 across all worlds) | 3 |
| Accuracy of driver sizes | **91.5%** | 78.3% |
| Unlogged events surfaced | **30 / 30**, sized within about 12% | not sized |

**How it differs from v1**

- **One model per mechanism:** referrals, conversion, turnaround time, persistence, refill gap and days supply, fitted jointly. Each driver is switched off in turn and the yearâ€™s volume recomputed with everything else left on.
- **Corrects the register:** logged start dates, end dates and ramp-ups are adjusted from the data.
- **Finds unlogged events:** unexplained changes, down to individual practices, are detected and sized.
- **Admits limits:** events the data canâ€™t tell apart are reported as one combined figure.

**Rules that keep errors out of the exec report**

- **â€œConfirmedâ€** needs 99.9% confidence. A null event clears 95% about 5% of the time by design, so 95% canâ€™t guarantee zero false claims. At 99.9% there were none, and 97.6% of material volume was still confirmed.
- **â€œProbableâ€** covers drivers between 95% and 99.9% confidence.
- **â€œNot distinguishableâ€** drivers show as zero, never as a number.
- **Close drivers share a rank** when their intervals overlap.
- **A residual line** reconciles everything to the year-on-year change.

**What still isnâ€™t good enough**

- **90-day supply mandates:** intervals contain the truth only 84% of the time, below the 95% target. Treat those ranges as too narrow for now.
- **Small drivers:** biosimilar switches (around 300 units of volume) are sized to about 72% accuracy, and payer prior-auth changes (around 80) to about 83%.
- **Late-year events:** changes that start after September have only a few months of data, so their size is partly extrapolated.
- **A logged hypothesis can take credit for a coincident unlogged event** with the same scope and timing. That happened once in 90 worlds.
- **Production accuracy will be lower than 91.5%.** The simulator is cleaner than real data, and the models share its structure. Treat 91.5% as a ceiling.

Before trusting this on real data, run it on two or three past periods where you already know what happened, such as a payer policy change you remember. If it canâ€™t recover those, it isnâ€™t ready for executives, however well it scores here.

#### Artifact: `EVALUATION v2` (`Document Â· MD`)

```markdown
# v2 evaluation: counterfactual attribution vs v1, 90 randomised worlds

Each world draws its own events (8 per world, from 9 types with random scope, size, timing and ramps), logs them with date errors of up to Â±30 days, adds one decoy (logged, no effect) and one unlogged event. Truth per event = actual 2025 eq28 minus a re-run with only that event switched off.

Three splits:
- **Calibration (1001â€“1030):** used to set one interval-width factor per channel family.
- **Holdout used in debugging (1031â€“1060):** I looked at four of these while fixing the renewal maths, so they are no longer clean.
- **Fresh holdout (1061â€“1090):** generated after the estimator was frozen (md5 `61f55630â€¦`). **These are the honest numbers.**

## Results against the pass criteria

| Criterion | v2 fresh holdout | v1 fresh holdout | Pass? |
|---|---|---|---|
| Direction errors on claimed drivers | **0** (0 in all 90 worlds) | 0 (7 across all splits) | âœ… |
| Hard ranking flips among material drivers | **0 / 158 pairs** (0 in all 90 worlds) | 22 / 158 | âœ… |
| 95% interval coverage of truth | **97.1%** (94.6â€“97.1% across splits) | no intervals | âœ… |
| Decoy confirmed (99.9% "Confirmed" tier) | **0 / 29** (0 / 84 across all splits) | 3 attributed | âœ… |
| Decoy at the 95% "Probable" tier | 1 / 29 | | expected at 95% |
| Point accuracy, pooled | **91.5%** | 78.3% | |
| Median world accuracy | 91.2% | 76.7% | |
| Unlogged event surfaced as unexplained driver | **30 / 30**, median size error 12% | not sized | |
| Material events (â‰¥300 eq28) left "not distinguishable" | 4 / 116 at 95%; 13 / 116 at 99.9% (97.6% of material volume still confirmed) | 13 missed | |
| Event pairs reported jointly ("cannot be separated") | 2 | n/a | |

Accuracy = 1 âˆ’ Î£|estimate âˆ’ truth| / Î£|truth| over logged real drivers. v1 swings from 60% to 78% between splits, so its accuracy depends heavily on which events happen. v2 holds at 88â€“92%.

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
| Biosimilar interchange | 72.1% | 100% | Small net effects (~300 eq28). v1 was âˆ’100% here |

## What made the difference (each fix was measured)

1. **Counterfactual sizing instead of residual subtraction.** Global events no longer absorb unclaimed change. This is the largest gain, at a cost of roughly 2x in compute.
2. **Fill-by-fill renewal path.** The "all refills 30-day or all 90-day" approximation missed the switch-month pull-forward. One world went from 2.9k to 4.5k against a true 4.4k.
3. **TAT channel.** Intake delays went from 74% to 93% of truth.
4. **End dates move with profiled starts.** Temporary events kept the logging error at their end date.
5. **Collinearity groups.** A decoy logged with the same scope as a real event took +702 while the real one took âˆ’273. Now they are reported jointly at +481 against a true +337.
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
```

*(Also attached: `Specialty volume sim v2` (`ZIP` â€” No preview available))*

---

### User

How can I build this in python in Databricks

### Claude

The fastest route is to keep the estimator as plain pandas/numpy running on the driver, put Spark around it for data prep, parallel testing and publishing, and package it all as a Databricks Asset Bundle. The heavy computation is already done on aggregated cells, so it doesnâ€™t need Spark.

**1. Package the code**

- Put the code in a Git folder as a small package, e.g. `volattr/` containing `estimator.py` (from `est2.py`), `renewal.py`, `report.py`, `sim.py` (from `sim2.py`) and `harness.py`. Build it as a wheel and attach it to the job cluster.
- Remove whatâ€™s hard-coded:
  - The period dates and the year-end constant become job parameters.
  - Drug days supply, molecule families and biosimilar pairs come from a dimension table, not from `sim2`.
  - The calibration factors are read from a table.
- Dependencies are just numpy, pandas and pyarrow. v2 doesnâ€™t use ruptures or statsmodels.

**2. Data layer (Unity Catalog, medallion)**

- **Silver:** `fact_referral`, `fact_fill`, `dim_prescriber`, `dim_drug`, built to the column contract the estimator expects. The critical fields are `eq28`, `fill_number` (0 = start), `first_fill_date`, `tat_days`, `practice`, and drug-family keys. You almost certainly have parsed claims and e-prescriptions already, so this is a mapping exercise. You wonâ€™t need to parse raw NCPDP.
- **`change_register`:** a governed Delta table with check constraints on the scope values and the `expected_kpis` vocabulary, plus an owner column. The quality of this table limits accuracy more than the model does, so give owners an easy way to enter events: a Databricks App, or a form that writes to the table.
- **Gold:** `attribution_results` (run_id, unit, tier, estimate, lo95, hi95, rank), `unexplained_drivers`, `residual`, `model_diagnostics`.

**3. Workflow (one job, run monthly)**

1. **Build silver**, with data-quality expectations (Lakeflow Declarative Pipelines or simple asserts).
2. **Validate the register:** dates, scopes, and no duplicate events with the same scope and dates.
3. **Estimate.** Pull the needed columns with `spark.table(...).toPandas()`, run on the driver, and write gold. Log the code version, calibration factors, model coefficients and the exec table to MLflow so every number can be traced to a run.
4. **Regression gate.** Run 30 or more simulated worlds in parallel across workers and fail the job if any pass criterion breaks. This stops an untested change reaching an executive.
5. **Publish** to an AI/BI dashboard, with the exec table also sent as a PDF if needed.

The gate parallelises cleanly because each world is independent:

```python
seeds = spark.range(1061, 1091).withColumnRenamed("id", "seed")
results = (seeds.repartition(30)
    .groupBy("seed")
    .applyInPandas(run_and_score_world, schema=SCORE_SCHEMA))
# fail the job if any world breaks the criteria
assert results.filter("dir_err > 0 OR hard_flips > 0").count() == 0
```

**4. Scaling to real volumes**
Real specialty data will be one to two orders of magnitude larger than the simulation.

- **Referrals and persistence:** these models already run on aggregated cells, so they scale fine.
- **Conversion:** this logistic model has one row per referral. Above a few million rows, aggregate to design cells with counts; the IRLS solver already accepts weights.
- **Fills:** aggregate in Spark before calling `toPandas()`, or use a memory-optimised driver (64 GB or more).
- **Profiling:** this means hundreds of model refits. If the run gets slow, run the per-event searches in parallel across workers (one event per task), like the harness.

**5. Before executives see it**

- **Backtest** on 2â€“3 past events with known answers.
- **Recalibrate** the interval widths on simulations that reflect your real event types.
- **Keep PHI in silver** under Unity Catalog permissions. Gold contains only aggregates.

I can refactor the zip into a ready-to-deploy Asset Bundle: the package, three notebooks (silver build, estimate, regression gate), job YAML and a column-contract spec for your silver tables. Want me to?

---

### User

Give me the step by step to build this in databricks

### Claude

Build it in this order: prove it on the synthetic data first, then swap in real tables. The key changes are steps 2 and 4.

**1. Set up the workspace**

- **Compute:** a cluster on DBR 15.4 LTS ML or later, in dedicated (single-user) access mode, with a 64 GB+ memory-optimised driver and 2â€“4 workers. The workers are only for the regression gate.
- **Unity Catalog:** create the schemas.

```sql
CREATE SCHEMA IF NOT EXISTS specialty.volattr_silver;
CREATE SCHEMA IF NOT EXISTS specialty.volattr_gold;
CREATE SCHEMA IF NOT EXISTS specialty.volattr_sim;
CREATE VOLUME IF NOT EXISTS specialty.volattr_sim.files;
```

- **Code:** create a Git folder (Workspace â†’ Create â†’ Git folder) and commit the zipâ€™s `src/` and `v2/` folders.

**2. Turn the code into a package**
Restructure as `volattr/` containing `estimator.py` (from `est2.py`), `sim.py` (from `sim2.py`), `report.py`, `harness.py` and `config.py`. Three edits:

- **Remove the `sim2` dependency from the estimator.** `D0`, `STD_DS`, `FAMILY` and `BIOSIM_OF` should come from `dim_drug` and job parameters.
- **Parameterise the dates.** Replace `END = "2025-12-31"` and the 2023/2024/2025 literals with `period_start`, `period_end` and `history_start`.
- **Add a frame-based entry point** next to `run(world_dir)`:

```python
def run_frames(ref, fill, pres, reg, calib):
    E = Estimator(ref, fill, pres, reg)
    E.fit_referrals(profile=True)
    for _ in range(4):
        if not E.discover(): break
        E.fit_referrals(profile=False)
    E.fit_conversion(profile=True)
    E.fit_persistence(profile=True)
    return to_frame(E, E.attribute(), calib)
```

Build a wheel (`python -m build`) and put it in a volume, or point `%pip install -e` at the Git folder while developing.

**3. Prove it on the synthetic data**
Upload `v2/sample_world/` to the volume, load it into `volattr_sim` tables, and run the estimate notebook from step 7 against those tables. You should reproduce the sample `exec_report.csv` to within the random draws. Donâ€™t touch real data until this matches.

**4. Build the silver tables from your real data**
This is the main work. The estimator expects this contract:

| Table | Required columns |
| --- | --- |
| `fact_referral` | referral_id, patient_id, referral_date, drug, payer, region, referral_source, prescriber_npi, practice, converted, first_fill_date, tat_days, first_reject_code |
| `fact_fill` | patient_id, fill_number, fill_date, drug, payer, region, referral_source, prescriber_npi, practice, days_supply, eq28 |
| `dim_drug` | drug, ndc, std_days_supply, molecule_family, biosimilar_of |
| `dim_prescriber` | prescriber_npi, practice, region, specialty |

Getting `fill_number` right matters most. Define a start as the first fill after at least 180 days without that drug, not as the first row in your extract, or old patients will look like new starts.

```python
from pyspark.sql import functions as F, Window as W
f = spark.table("rx.paid_dispenses_net_of_reversals").join(spark.table("specialty.volattr_silver.dim_drug"), "ndc")
w = W.partitionBy("patient_id", "molecule_family").orderBy("fill_date")
f = (f.withColumn("prev", F.lag("fill_date").over(w))
      .withColumn("is_start", F.col("prev").isNull() | (F.datediff("fill_date", "prev") > 180))
      .withColumn("episode", F.sum(F.col("is_start").cast("int")).over(w))
      .withColumn("fill_number", F.row_number().over(W.partitionBy("patient_id", "molecule_family", "episode").orderBy("fill_date")) - 1)
      .withColumn("eq28", F.col("days_supply") / 28))
f.write.mode("overwrite").saveAsTable("specialty.volattr_silver.fact_fill")
```

For `fact_referral`, join each referral to that patientâ€™s first `fill_number = 0` within 120 days. Set `converted`, `first_fill_date` and `tat_days` from that join. The first intake claim rejection gives you `first_reject_code`.

**5. Create the change register**

```sql
CREATE TABLE specialty.volattr_gold.change_register (
  event_id STRING NOT NULL, event_type_logged STRING, start_date DATE NOT NULL, end_date DATE,
  scope_drug STRING, scope_payer STRING, scope_region STRING, scope_source STRING,
  expected_kpis STRING NOT NULL, owner STRING NOT NULL, logged_at TIMESTAMP);
ALTER TABLE specialty.volattr_gold.change_register ADD CONSTRAINT kpi_vocab CHECK (
  expected_kpis RLIKE '^(referrals|conversion|pa_required_rate|tat|lapse_rate|fills_per_active|eq28_per_fill|new_entrant)(\\|(referrals|conversion|pa_required_rate|tat|lapse_rate|fills_per_active|eq28_per_fill|new_entrant))*$');
```

Backfill the past 18 months with the event owners. Missing events end up in the residual, or as unexplained drivers.

**6. Store the calibration factors**
Load `v2/results/calibration_factors.csv` into `volattr_gold.calibration_factors`. These are temporary until step 10.

**7. Estimate notebook**

```python
dbutils.widgets.text("period_end", "2025-12-31")
import mlflow
from volattr import estimator, report
S = "specialty.volattr_silver"
cols_f = ["patient_id","fill_number","fill_date","drug","payer","region","referral_source","prescriber_npi","practice","days_supply","eq28"]
ref  = spark.table(f"{S}.fact_referral").toPandas()
fill = spark.table(f"{S}.fact_fill").select(*cols_f).toPandas()
pres = spark.table(f"{S}.dim_prescriber").toPandas()
reg  = spark.table("specialty.volattr_gold.change_register").toPandas()
calib = spark.table("specialty.volattr_gold.calibration_factors").toPandas().set_index("family")["k"].to_dict()

with mlflow.start_run(run_name="attribution") as run:
    est = estimator.run_frames(ref, fill, pres, reg, calib)
    hdr, table = report.build(est, reg, fill, calib)
    mlflow.log_params({"period_end": dbutils.widgets.get("period_end"), **{f"k_{k}": v for k, v in calib.items()}})
    mlflow.log_table(table, "exec_report.json")
    table["run_id"] = run.info.run_id
    spark.createDataFrame(table).write.mode("append").saveAsTable("specialty.volattr_gold.attribution_results")
```

**8. Regression-gate notebook**

```python
from volattr.harness import run_and_score_world, SCORE_SCHEMA
res = (spark.range(1061, 1091).withColumnRenamed("id", "seed").repartition(30)
       .groupBy("seed").applyInPandas(run_and_score_world, SCORE_SCHEMA))
res.write.mode("overwrite").saveAsTable("specialty.volattr_sim.gate_results")
bad = res.filter("dir_err > 0 OR hard_flips > 0 OR decoy_confirmed > 0").count()
cov = res.selectExpr("sum(covered)/sum(n_cov)").first()[0]
assert bad == 0 and cov >= 0.94, f"Gate failed: {bad} worlds, coverage {cov:.3f}"
```

`run_and_score_world` wraps `sim.make_world`, then the estimator, then `score.score_world`, and returns one row per world.

**9. Wire up the job**
In Workflows, create one job with four tasks: silver build â†’ register checks â†’ regression gate â†’ estimate. Have estimate depend on the gate, so a failing change never publishes. Schedule it monthly. Once itâ€™s stable, export it with `databricks bundle generate job` so it lives in Git.

**10. Before executives see it**

- **Recalibrate:** rerun the harness with event sizes and types that match your real history, and refresh `calibration_factors`.
- **Backtest:** run two or three past periods with known causes. It must recover them in the right direction, in the right order, and inside the intervals.
- **Dashboard:** build an AI/BI dashboard on `attribution_results` showing tiers, intervals, shared ranks and the residual.
- **Access:** PHI stays in silver with Unity Catalog grants; give executives access to gold only.

Expect step 4 and the register backfill to take most of the effort. The modelling is already built.

I can produce the refactored `volattr` package, the three notebooks and the bundle YAML as files, ready to commit to your Git folder. Want me to?

---

### User

give me the exact prompt i need to provide genie to replicate this in databricks. I can upload datasets to databricks, tell me what files

### Claude

Two clarifications before the prompts.

- **Genie spaces only answer questions with SQL over tables.** They canâ€™t build Python packages, notebooks or jobs. To build this you need the **Databricks Assistant in agent mode**, opened from inside a notebook. Use a Genie space at the end, for executives to query the results. Your workspace may label these differently.
- **Donâ€™t ask the assistant to rewrite the estimator from a description.** Itâ€™s about 900 lines of tested maths, and a rewrite would lose the fixes that got it to 91%. Upload the code and have it refactor and wire up.

### Files to upload (from `specialty_volume_sim_v2.zip`)

**Code: Workspace folder `/Workspace/Users/<you>/volattr_src/`**

- `src/sim.py` (`sim2.py` imports its reference data)
- `v2/sim2.py`, `v2/est2.py`, `v2/score.py`, `v2/exec_report.py`, `v2/harness.py`
- `v2/README.md`, `v2/EVALUATION_v2.md` (context for the assistant)

**Data: Volume `/Volumes/<catalog>/volattr_sim/files/`**

- From `v2/sample_world/`: `fact_referral.parquet`, `fact_fill.parquet`, `dim_prescriber.parquet`, `change_register.csv`, `ground_truth_events.csv`, `truth_summary.csv`, `v2_estimates.csv`, `exec_report.csv`
- `v2/results/calibration_factors.csv`

Create the volume first: `CREATE VOLUME <catalog>.volattr_sim.files`, after creating the schema.

### Prompts (run one at a time, check each result before the next)

**Prompt 1: tables**

> Create Unity Catalog schemas `<catalog>.volattr_silver`, `<catalog>.volattr_gold` and `<catalog>.volattr_sim`. Load every parquet and CSV file in `/Volumes/<catalog>/volattr_sim/files/` into Delta tables in `<catalog>.volattr_sim` with the same base names, parsing date columns as DATE. Load `calibration_factors.csv` into `<catalog>.volattr_gold.calibration_factors` with columns `family` (STRING) and `k` (DOUBLE). Then read `/Workspace/Users/<you>/volattr_src/sim2.py` and `sim.py` and create `<catalog>.volattr_sim.dim_drug` from the `DRUGS` list, with columns drug, ndc, std_days_supply (the `ds` field), units_per_fill, molecule_family (from `FAMILY`, otherwise the drug name) and biosimilar_of (reverse of `BIOSIM_OF`). Show row counts for every table.

**Prompt 2: package**

> In `/Workspace/Users/<you>/volattr_src/`, refactor the code into a Python package `volattr/` containing `estimator.py` (from est2.py), `sim.py` (from sim2.py, with the reference data from src/sim.py merged in), `score.py`, `report.py` (from exec_report.py), `harness.py` and `config.py`. Requirements:
>
> 1. Keep all modelling logic and the custom numpy `glm()` IRLS function exactly as written. Do not switch to statsmodels; its GLM leaks memory on this data.
> 2. The estimator must not import the simulator. Pass STD_DS, FAMILY, BIOSIM_OF and the date origin in through a config object built from `dim_drug`.
> 3. Replace the hard-coded dates (END = 2025-12-31, and the 2023/2024/2025 literals) with config fields history_start, comparison_start and period_end.
> 4. Add `run_frames(ref, fill, pres, reg, calib, cfg)`, which returns the estimates DataFrame that `run()` currently returns.
> 5. Add `report.build(estimates, reg, fill, calib)`, which returns the header dict and exec table from exec_report.py.
> 6. Remove the v1 baseline from harness.py.
>
> Add a `pyproject.toml` with dependencies numpy, pandas and pyarrow. Show me the diff for each file.

**Prompt 3: estimate notebook, validated on the sample**

> Create a notebook `01_estimate`. Install the package with `%pip install -e /Workspace/Users/<you>/volattr_src`, then restart Python. Use widgets for catalog, schema (default `volattr_sim`) and period_end (default 2025-12-31). Load fact_referral, fact_fill, dim_prescriber, change_register, dim_drug and calibration_factors with `toPandas()`. Run `run_frames` and `report.build`. Log the parameters, calibration factors and the exec table to MLflow, and append the exec table with the MLflow run_id to `<catalog>.volattr_gold.attribution_results`. Finally, validate against the sample: join the estimates to `v2_estimates` on event_id and assert every `est` is within 2% or 25 eq28 of the stored value (whichever is larger). Then compare against `ground_truth_events` and print each eventâ€™s estimate, interval and true impact.

**Prompt 4: regression gate**

> Create a notebook `02_regression_gate`. Write `run_and_score_world(pdf)`. For the seed in the input pandas DataFrame, it builds a world with `volattr.sim.make_world` into a temp directory, runs the estimator, scores it with `volattr.score.score_world` using the calibration factors table, and returns one row: seed, acc, dir_err, hard_flips, decoy_confirmed (decoy |est| > 3.29Â·kÂ·sd), covered, n_cov, unlogged_surfaced. Run it over seeds 1061â€“1090 with `spark.range(...).repartition(30).groupBy("seed").applyInPandas(...)` and save to `<catalog>.volattr_sim.gate_results`. The notebook must raise an exception unless dir_err, hard_flips and decoy_confirmed are all 0 and pooled coverage (sum covered / sum n_cov) is at least 0.94. Print the summary.

**Prompt 5: job and Genie space**

> Create a Databricks job `volattr_monthly` with tasks 02_regression_gate followed by 01_estimate (estimate depends on the gate passing), running on a job cluster with DBR 15.4 LTS ML, a 64 GB driver and 4 workers, scheduled monthly on day 5 at 06:00 Europe/Dublin. Then create an AI/BI dashboard on `<catalog>.volattr_gold.attribution_results` showing the latest run: a table of rank, driver, tier, eq28, lo95, hi95 and pct_of_2024, plus a waterfall from 2024 volume to 2025 volume. Draft instructions for a Genie space over the same table that explain the tiers (Confirmed = 99.9% interval excludes zero, Probable = 95% only, Joint = cannot be separated, Not distinguishable = shown as zero, Unexplained driver = investigate) and say never to present a Not distinguishable driver as a number.

### Tips

- **Check before moving on.** Prompt 3â€™s assertion is your proof that the refactor didnâ€™t break anything. If it fails, have the assistant diff `estimator.py` against `est2.py`, donâ€™t let it â€œfixâ€ the tolerance.
- **Moving to real data.** Point the `schema` widget at `volattr_silver` once your real tables follow the column contract from my previous message. The 180-day new-start rule for `fill_number` is the step to review by hand.
- **Expect corrections.** The assistant may not get prompt 2 right in one pass on files this size. Ask it to do one file at a time if it struggles.

---

### User

High level, what is this code doing, what does each piece do

### Claude

The kit answers one question: **which events changed specialty pharmacy volume, by how much, and how sure are we?** It also checks its own answers against simulated worlds where the true answer is known.

### The big picture

1. **Simulate a pharmacy** where you plant events and know their exact effect.
2. **Estimate those effects blind**, from the data and the change register alone.
3. **Score the estimates** against the planted truth across many worlds.
4. **Report** the results in a form an executive can act on.

In production, step 1 is replaced by your real data. Steps 2 to 4 stay the same, and the simulation becomes the regression test that guards every code change.

### What each piece does

**`sim.py` (v1): reference data and the original simulator.** It defines the drugs, payers, regions, seasonality and fictional names. v2 reuses this reference data.

**`sim2.py`: the world generator.** For each random seed it:

- **Draws about 8 events**, such as a payer prior-auth rule, a referral shift, a biosimilar launch, a new drug, an intake delay, a persistence programme, a 90-day mandate or a sales push. Each gets a random scope, size, start date and ramp.
- **Writes a messy change register:** dates off by up to 30 days, one decoy with no real effect, and one real event that nobody logged.
- **Simulates every patient journey:** referral, then insurance check, then prior auth, then delay or abandonment, then first fill, then refills until they stop.
- **Produces the answer key** by re-running the world with each event switched off. The difference in 2025 volume is that eventâ€™s true impact.

**`est2.py`: the estimator.** This is the core, and it sees only the data and the register.

1. **Fits a model for each mechanism:**
   - referrals (Poisson, monthly by drug, payer and region)
   - conversion (logistic, per referral)
   - turnaround time (linear)
   - persistence (a discrete-time hazard: the chance a patient stops after each fill)
   - refill gaps
   - the share of 90-day refills

   Each logged event is an input to these models.

2. **Corrects the timing.** It tests nearby start dates and ramp lengths, and keeps a change only if it fits clearly better than the logged date.
3. **Discovers unlogged events** by scanning for unexplained step changes in practices, regions, payers and drugs. Each one found is added to the models so it canâ€™t distort other events.
4. **Groups events it canâ€™t separate,** such as two with the same scope and timing, and reports them as one combined figure.
5. **Builds counterfactuals:** it switches off one event at a time and recomputes the yearâ€™s expected volume fill by fill, with every other event left on.
6. **Puts intervals on every estimate,** from model uncertainty plus the randomness of individual patients.

**`harness.py`: the test runner.** It builds many worlds in parallel and runs the v2 estimator and the old v1 pipeline on each, so the two can be compared.

**`score.py`: the referee.**

- **Calibrates:** on the first 30 worlds, it measures how wide intervals must be to contain the truth 95% of the time, separately for each channel.
- **Scores the other worlds** against the pass criteria: zero wrong directions, zero wrong rankings, 95% coverage, no decoys confirmed, and accuracy.

**`exec_report.py`: the output.** It turns estimates into the executive table:

- **Tiers:** Confirmed, Probable, Joint, Not distinguishable, or Unexplained driver.
- **Shared ranks** where intervals overlap.
- **A residual line**, so everything reconciles exactly to the year-on-year volume change.

**v1 files (`decomp.py`, `eval_pipeline.py`, `render_ncpdp.py`, `parse_ncpdp.py`):** the first version, kept as the baseline. They cover the LMDI decomposition, change-point matching, and the mock NCPDP messages with a parser that rebuilds the tables from them.

### How it flows

Data and change register â†’ **estimator** (models â†’ timing â†’ discovery â†’ counterfactuals â†’ intervals) â†’ **exec report**. Separately, simulated worlds â†’ harness â†’ **score**, which sets the interval widths and blocks any code change that introduces errors.

---

### User

Show me what a row in the data looks like

### Claude

*[Tool status: Ran a command â€” Pull example rows from sample world tables]*

Here are real rows from the sample world, following one patient through the data.

**Referral (`fact_referral`, one row per referral, 46k rows)**

| Field | Value |
| --- | --- |
| referral_id | 93279 |
| referral_date | 2025-03-01 |
| drug | Dupixent |
| payer | Northstar Health |
| region | Midwest |
| referral_source | Fax |
| prescriber_npi | 9100003323 (fake; real NPIs never start with 9) |
| practice / specialty | Practice 0084 / Derm |
| intake_delay_days | 3 |
| first_reject_code | none (paid first time) |
| converted | True |
| first_fill_date | 2025-03-04 |
| tat_days | 3 |

Same day, a referral that was lost: Dupixent, Atlas Part D, eRx, from Lakeside Dermatology. `first_reject_code` is **75** (prior auth required), `converted` is False and `first_fill_date` is empty.

**Fills for that patient (`fact_fill`, one row per dispensed fill, 370k rows)**

| fill_number | fill_date | days_supply | eq28 |
| --- | --- | --- | --- |
| 0 (start) | 2025-03-04 | 28 | 1.0 |
| 1 | 2025-04-07 | 28 | 1.0 |
| 2 | 2025-05-10 | 28 | 1.0 |
| 3 | 2025-06-12 | 28 | 1.0 |

Every fill also carries the drug, payer, region, source, prescriber and practice, so it can be traced back to the referral. Two things are visible here:

- **Refill gaps:** 34, 33 and 33 days between fills, against a 28-day supply. The 5â€“6 day lag is the gap the persistence model learns, and the one a refill reminder programme shortens.
- **eq28:** days_supply Ã· 28. A 90-day fill would show 3.0, which keeps volume comparable across pack sizes.

**Change register (`change_register`, one row per logged event)**

| event_id | event_type_logged | start_date | scope_region | expected_kpis |
| --- | --- | --- | --- | --- |
| E02 | referral_mult | 2025-02-15 | Northeast | referrals |

The other scope columns (drug, payer, source) are empty, so this event applies to every drug, payer and source in the Northeast. In this world the event really started on 2025-02-19, so the register date is 4 days off, on purpose.

**Estimator output (`v2_estimates`, one row per event)**

| Field | Value |
| --- | --- |
| event_id | E02 |
| channels | ref/conv (sized through referrals and conversion) |
| est | +1,759 eq28 |
| 95% interval | 1,218 to 2,301 |
| est_start / est_ramp | 2025-02-15 / 90 days |
| claimed | True |

The ground truth for E02 is **+1,923**, inside the interval and about 9% below the estimate. Before calibration the interval runs from 1,218 to 2,301; the exec report widens it by the calibrated factor for this channel (1.34x) before tiering.

---

### User

What's the difference between intake delay days and tat

### Claude

**Intake delay** is the pharmacyâ€™s own processing time. **TAT** is the whole journey to the first fill, so it includes intake delay plus any wait on the payer.

- **intake_delay_days:** referral received â†’ first claim submitted to the payer. This covers data entry, document checks and benefits verification. Staffing and workload drive it, so the intake staffing-shortage events move this number.
- **tat_days:** referral received â†’ first fill dispensed. It equals intake delay plus however long it takes to clear whatever the payer returns:
  - Paid first time: nothing extra, so TAT = intake delay.
  - Prior auth required (reject 75): plus the PA decision time, usually a few days, and about a week more if it goes to appeal.
  - Not covered (70 / MR): plus about 6 days if itâ€™s resolved through an exception.
  - Plan limits (76): plus about 2 days.

**In the rows you saw**

- **Referral 93279:** paid first time, so both are 3 days.
- **The lost Dupixent referral:** intake took 3 days, then a prior auth came back denied 2 days later, so `tat_days` is 5. For referrals that donâ€™t convert, the simulation records TAT as time to the decision that lost them.

**Why both are kept**

- **TAT drives outcomes.** The longer the wait, the more patients abandon, and every day of delay is a day of therapy not dispensed that year. The estimator models TAT for this reason.
- **Intake delay shows whose delay it is.** A rise in TAT with flat intake delay points at payers (prior auth, denials). A rise in both points at your own operations. That distinction decides who owns the fix.

**On real data:** the simulation doesnâ€™t model shipping. Decide whether TAT ends at dispense or at delivery, and use one definition everywhere. Also either restrict TAT to converted referrals or label it â€œtime to resolutionâ€. Mixing the two inflates averages when denials rise.

---

### User

Does this take into consideration the availability of copay cards or out of pocket values

### Claude

No, not in any meaningful way.

**Whatâ€™s there now**

- **Conversion:** a flat 5% â€œpatient declinedâ€ step, which is the same for everyone regardless of cost.
- **Claims:** the v1 claims carry mock `patient_pay_amount` values calculated after the fact. They never influence what a patient does.
- **v2:** the simulator has no pricing at all, and the estimator has no out-of-pocket (OOP) or assistance inputs.

**Why itâ€™s a real gap.** In specialty pharmacy, cost to the patient is often a top driver of abandonment and discontinuation. Things the current version would miss or misattribute:

- **January deductible resets.** This is a seasonal OOP spike. Right now the January dip is modelled only as prior-auth re-authorisation.
- **Copay card caps running out mid-year,** and copay accumulator or maximizer programmes. A payer adopting an accumulator could show up as an unexplained persistence drop for that payer.
- **Benefit-design changes,** for example the 2025 Part D redesign that capped annual OOP at $2,000. That would affect Medicare patientsâ€™ persistence and land in the residual or in a wrong event.
- **Manufacturer copay programme launches or cap changes.**
- **Government payers:** manufacturer copay cards generally canâ€™t be used for Medicare or Medicaid, so those patients rely on foundations, and cost affects them differently.

**How to add it**

1. **Data:**
   - Patient pay per claim (field F5 in the Telecom response).
   - Assistance applied, from secondary claims to a copay card processor (its BIN/PCN) or coordination-of-benefits segments.
   - Foundation awards.
   - An accumulator/maximizer flag by plan.
   - From these, derive the net OOP per fill after assistance.
2. **Simulator:** make abandonment and the discontinuation hazard depend on net OOP. Add January deductible resets and card caps that run out at a set spend. Add event types: copay programme change, accumulator adoption, benefit redesign.
3. **Estimator:**
   - Add net OOP as an input to the conversion and persistence models, plus register types for cost events, so they get sized like any other driver.
   - Each register type then gets its own counterfactual. For example, â€œaccumulator offâ€ means re-pricing the affected fills with assistance applied.
4. **Test:** rerun calibration and the fresh holdout with cost events included before trusting the intervals.

**Selection bias to watch.** Assistance goes to the patients with the highest OOP, so a naive model can conclude that assistance causes high costs or poor persistence. Use within-plan changes over time (a card cap being hit, an accumulator starting) to identify the effect, not differences between patients.

Want me to add OOP and copay assistance to the simulator and estimator, and rerun the test?

---

### User

Yes add these factors

### Claude

Iâ€™ll add patient out-of-pocket (OOP) cost and copay assistance to the simulator, extend the estimator to size cost-driven events, and re-run the fresh-holdout test with v2 as the baseline.

*[Tool status: Ran 5 commands, updated tasks]*

The foundation closure is a real bug. Its scope includes the payer, but the scope check only passed the drug, so it never matched. Fixing.

*[Tool status: Ran 23 commands, updated tasks, created a file (including `Created README.md`; note that clicking `README.md` in the shared snapshot returns "Failed to load file content", and no further prose summary appears after this tool block in the shared chat)]*

---

### User

save this conversation as a markdown file

*(End of shared conversation â€” no assistant reply follows this message.)*