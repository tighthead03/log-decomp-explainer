# Log Decomp Explainer

Explains **why specialty pharmacy volume changed** between two periods (2024 → 2025) by combining:
1. **Granular Pocket & Transaction Drill-Down ([granular_drilldown.py](granular_drilldown.py))** — requires **no change log**. Automatically decomposes every `Region × Drug × Payer` pocket via LMDI and drills into:
   - **Prescriber NPI churn:** Exact count of lost NPIs (and their prior average Rx/NPI), gained NPIs (and their new average Rx/NPI), and retained same-store NPIs.
   - **Carrier / Account / Group & Claim Reject 75:** Identifies the exact plan group (e.g. `CAR-KSRX / ACC-100 / GRP-ABC1234`) where Reject 75 (PA Required), Reject 70/MR (Not Covered), or intake turnaround time (`tat_days`) spiked.
   - **Copay Card & Net Out-of-Pocket (OOP) impact:** Tracks the drop in secondary copay-card coverage (`%`), the `$/fill` jump in patient net OOP (e.g. `+$50/fill`), and the resulting first-fill abandonment and mid-year refill drop-off.
2. **Structural Counterfactual Attribution ([specialty_sim/v2/est2.py](specialty_sim/v2/est2.py))** — fits 6 funnel mechanism GLMs (referrals, conversion, TAT, persistence hazard, refill gap, days supply), discovers unlogged step-changes automatically, and replays 2025 with each event switched OFF one at a time to size its full-year `eq28` volume impact with calibrated 95% confidence intervals.

For a complete technical walkthrough of the math and code architecture, see **[HOW_IT_WORKS.md](HOW_IT_WORKS.md)**.

## One-time setup

The Python environment lives outside Google Drive at `%USERPROFILE%\.venvs\log-decomp-explainer`:

```powershell
uv venv --python 3.12 $env:USERPROFILE\.venvs\log-decomp-explainer
uv pip install --python $env:USERPROFILE\.venvs\log-decomp-explainer\Scripts\python.exe -r requirements.txt
```

## Run it

From the project folder (or use the **Run and Debug** panel):

```powershell
$py = "$env:USERPROFILE\.venvs\log-decomp-explainer\Scripts\python.exe"

& $py run_demo.py sample                          # bundled sample world, ~2 min
& $py run_demo.py world --seed 2005               # simulate a NEW world (with Carrier/Account/Group + Copay/OOP shock) and explain it
& $py run_demo.py batch --start 1061 --end 1064   # several worlds + accuracy scorecard
```

Each run writes to `runs/<name>/`. Open **`report.html`** in a browser to see:

| File | What it shows |
|---|---|
| `report.html` | Plain-English executive summary, numbered driver cards, granular `Region × Drug × Payer` pocket drill-downs (NPIs, Reject 75 by Carrier/Account/Group, Copay card & Net OOP), volume bridge, timeline, and collapsible technical appendix |
| `exec_report.csv` | Executive attribution table: drivers, evidence tier, 95% interval, shared ranks, residual |
| `granular_pocket_drilldown.csv` | Pocket-level LMDI decomposition + NPI churn + Reject 75 + Plan Group + Copay/OOP metrics |
| `bridge.png`, `monthly.png` | 2024 → 2025 volume bridge and month-by-month timeline |
| `truth_comparison.csv`, `estimate_vs_truth.png` | Each estimate against the simulator's true impact |
