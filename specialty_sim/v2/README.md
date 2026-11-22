# v2: counterfactual attribution with calibrated intervals

v2 replaces the v1 LMDI + DiD sizing with structural counterfactual models. It is tested on randomised simulated worlds rather than one fixed scenario.

## Files

| File | Purpose |
|---|---|
| `sim2.py` | Randomised world generator. Each seed draws its own events, register errors, one decoy and one unlogged event. Ground truth per event = actual 2025 eq28 minus the run with only that event switched off (common random numbers) |
| `est2.py` | The v2 estimator (below). Sees only fact tables and the change register |
| `harness.py` | Builds worlds, runs v2 and the unchanged v1 pipeline on each |
| `rerun_est.py` | Re-runs the estimator on existing worlds |
| `score.py` | Calibrates interval width on calibration worlds, scores all splits against the pass criteria |
| `exec_report.py` | Executive table: tiers, calibrated intervals, shared ranks, residual reconciled to the YoY change |
| `results/` | Per-world, per-unit and per-event-type scores, calibration factors, summary |
| `sample_world/` | One fresh-holdout world: data, register, ground truth, v2/v1 estimates, exec report |

## Estimator

1. **Response models**, fitted jointly over 2023–2025 with every logged event as a regressor:
   - **Referrals:** Poisson GLM on monthly cells (drug or molecule family × payer × region, with source and flagged practices added when needed).
   - **Conversion:** logistic regression per referral. TAT is modelled separately (OLS), because a delay costs therapy days even when the patient converts.
   - **Persistence:** discrete-time cloglog hazard per fill with offset log(days_supply/28), plus a Poisson model for the refill gap.
   - **Days supply:** the observed share of 3x refills by payer and month.
2. **Timing:** start date and ramp are profiled around the logged date (±4 weeks; ramp 0/45/90 days). They are accepted only if they beat the logged date by 3 log-likelihood points. The end date of temporary events moves with the start.
3. **Discovery:** residual step scans on practices, regions, payers and drugs, adding the most specific finding first, one per pass. Each finding becomes a nuisance regressor, so an unlogged event can't contaminate logged ones, and is sized as an "unexplained driver".
4. **Collinearity groups:** events whose regressors correlate above 0.8 are sized jointly and reported as "cannot be separated".
5. **Counterfactual volume:** each event (or group) is switched off with every other event left on. Expected 2025 eq28 is recomputed with a fill-by-fill renewal path, where days supply, hazard and gap follow the calendar date. The referral channel uses expected counts per cell, so a practice that drops to zero is still sized.
6. **Intervals:** coefficient draws plus process (flip) variance, then a width factor per channel family calibrated on the calibration worlds and frozen.

Biosimilars launched mid-period are merged with their originator into a molecule family, so interchange nets out and only leakage is sized.

## Run

```bash
python3 harness.py /tmp/worlds 1001 1031     # build + estimate (2 processes)
python3 score.py /tmp/worlds                 # calibrate on 1001-1030, score all splits
python3 exec_report.py /tmp/worlds/w1065     # exec table for one world
```
Requires pandas, numpy, pyarrow. (statsmodels isn't used: its GLM leaked ~600 MB per fit on this version, so `est2.py` has its own IRLS.)
