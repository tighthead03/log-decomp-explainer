"""Run the simulator, write the analytic dataset, change register and ground truth."""
import os
import sys
import numpy as np
import pandas as pd
import sim
import decomp

OUT = sys.argv[1] if len(sys.argv) > 1 else "../data"
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else sim.SEED
os.makedirs(OUT, exist_ok=True)
A0, A1 = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31")
B0, B1 = pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")
WIN = pd.Timestamp(sim.REPORT_START)


def prep(out, pres):
    R = sim.label(out["referrals"], pres)
    F = sim.label(out["fills"], pres)
    F["eq28"] = F.days_supply / 28
    return R, F


def write(df, name):
    df.to_parquet(f"{OUT}/{name}.parquet", index=False)
    df.to_csv(f"{OUT}/{name}.csv.gz", index=False, compression="gzip")


pres, cand = sim.build_candidates(SEED)
full = sim.simulate(pres, cand, seed=SEED)

# ------------------------------------------------------------------ dimensions
dim_drug = pd.DataFrame([{k: v for k, v in d.items() if k not in ("spec", "base", "haz")} | {"specialties": "|".join(d["spec"])} for d in sim.DRUGS])
dim_drug = dim_drug.rename(columns={"ds": "standard_days_supply", "units": "units_per_fill", "price": "mock_unit_price_usd"})
dim_payer = pd.DataFrame(sim.PAYERS).rename(columns={"pa": "base_pa_rate_hidden", "appr": "base_pa_approval_hidden"})
dim_payer = dim_payer.drop(columns=["base_pa_rate_hidden", "base_pa_approval_hidden", "share"])
dim_prescriber = pres.drop(columns="weight")

# ------------------------------------------------------------------ facts
ref = sim.label(full["referrals"], pres)
pres_region = dict(zip(pres.prescriber_npi, pres.region))
ref["prescriber_specialty"] = ref.prescriber_npi.map(dict(zip(pres.prescriber_npi, pres.specialty)))
ref["practice"] = ref.prescriber_npi.map(dict(zip(pres.prescriber_npi, pres.practice)))
ref = ref.rename(columns={"date": "referral_date", "start_date": "first_fill_date", "marginal_flag": "_hidden_marginal_flag"})
ref["referral_id"] = "R" + ref.referral_id.astype(str).str.zfill(7)
ref["patient_id"] = "P" + ref.patient_id.astype(str).str.zfill(7)

fill = sim.label(full["fills"], pres)
fill["eq28"] = (fill.days_supply / 28).round(4)
fill["referral_id"] = "R" + fill.referral_id.astype(str).str.zfill(7)
fill["patient_id"] = "P" + fill.patient_id.astype(str).str.zfill(7)
fill = fill.rename(columns={"date": "fill_date"})

clm = sim.label(full["claims"], pres)
clm["referral_id"] = "R" + clm.referral_id.astype(str).str.zfill(7)
clm["patient_id"] = "P" + clm.patient_id.astype(str).str.zfill(7)
clm = clm.rename(columns={"date": "date_of_service"})
clm = clm[clm.date_of_service >= WIN].sort_values(["date_of_service", "patient_id", "fill_number"]).reset_index(drop=True)
clm.insert(0, "claim_id", "C" + pd.Series(np.arange(len(clm))).astype(str).str.zfill(8))
price = dict(zip(dim_drug.drug, dim_drug.mock_unit_price_usd))
seg = dict(zip(dim_payer.payer, dim_payer.segment))
clm["ingredient_cost_submitted"] = (clm.units * clm.drug.map(price)).round(2)
copay = clm.payer.map(seg).map({"Commercial": 0.02, "Medicare": 0.06, "Medicaid": 0.0})
clm["patient_pay_amount"] = np.where(clm.status == "P", (clm.ingredient_cost_submitted * copay).clip(upper=750).round(2), 0)
clm["total_amount_paid"] = np.where(clm.status == "P", (clm.ingredient_cost_submitted * 0.93 - clm.patient_pay_amount).round(2), 0)
REJ = {"75": "Prior Authorization Required", "70": "Product/Service Not Covered", "MR": "Product Not On Formulary",
       "76": "Plan Limitations Exceeded", "79": "Refill Too Soon"}
clm["reject_description"] = clm.reject_code.map(REJ)

pa = sim.label(full["pa"], pres)
pa["referral_id"] = "R" + pa.referral_id.astype(str).str.zfill(7)
pa["patient_id"] = "P" + pa.patient_id.astype(str).str.zfill(7)
pa = pa[pa.request_date >= WIN].reset_index(drop=True)
pa.insert(0, "pa_id", "PA" + pd.Series(np.arange(len(pa))).astype(str).str.zfill(7))

ops = sim.label(full["ops"], pres)
ops = ops[ops.date >= WIN].reset_index(drop=True)
ops["avg_intake_delay"] = ops.avg_intake_delay.round(2)

# patients referenced in window
rng = np.random.default_rng(7)
pats = ref[["patient_id", "payer", "referral_date"]].copy()
pats = pats[pats.patient_id.isin(set(fill.loc[fill.fill_date >= WIN, "patient_id"]) | set(ref.loc[ref.referral_date >= WIN, "patient_id"])
                                 | set(clm.patient_id) | set(pa.patient_id))]
n = len(pats)
pats["first_name"] = rng.choice(sim.FIRST, n)
pats["last_name"] = rng.choice(sim.LAST, n)
pats["gender"] = rng.choice(["F", "M"], n)
pats["date_of_birth"] = pd.to_datetime("1945-01-01") + pd.to_timedelta(rng.integers(0, 365 * 58, n), unit="D")
pats["cardholder_id"] = "ZX" + pd.Series(rng.integers(10**8, 10**9, n)).astype(str).values
pats["group_id"] = pats.payer.str[:3].str.upper() + "G" + pd.Series(rng.integers(100, 999, n)).astype(str).values
dim_patient = pats.drop(columns="referral_date")

ref_w = ref[ref.referral_date >= WIN].reset_index(drop=True)
fill_w = fill[fill.fill_date >= WIN].reset_index(drop=True)

for df, name in [(dim_drug, "dim_drug"), (dim_payer, "dim_payer"), (dim_prescriber, "dim_prescriber"), (dim_patient, "dim_patient"),
                 (ref_w, "fact_referral"), (fill_w, "fact_fill"), (clm, "fact_claim"), (pa, "fact_pa"), (ops, "ops_intake_daily")]:
    write(df, name)
    print(f"{name:18s} {len(df):>9,d} rows")

# ------------------------------------------------------------------ change register (what the business logged)
reg_rows = []
owners = {"Payer policy": "Payer Relations", "Market": "Trade Relations", "Access": "Trade Relations", "Sales": "Sales Ops",
          "Operations": "Intake Ops", "Process": "Patient Engagement", "Prescriber": "Sales Ops"}
hypothesis = {"E01": "Fewer Dupixent starts at Keystone (more PA, lower approval)",
              "E02": "Stelara volume moves to biosimilar; some leakage to competitors",
              "E03": "New volume from LDD access",
              "E04": "Fewer South referrals while territories are open",
              "E05": "Longer intake TAT; risk of abandonment",
              "E06": "Better refill adherence and persistence",
              "E07": "Fewer, larger fills for Northstar members",
              "E08": "More Skyrizi referrals",
              "E09": "Faster fax intake, expected conversion uplift"}
kpis = {"E01": "conversion|pa_required_rate", "E02": "referrals|new_entrant", "E03": "new_entrant", "E04": "referrals",
        "E05": "tat_median|conversion", "E06": "lapse_rate|fills_per_active", "E07": "eq28_per_fill|fills_per_active",
        "E08": "referrals", "E09": "conversion|tat_median"}
for eid, e in sim.EVENTS.items():
    if not e["in_register"]:
        continue
    reg_rows.append(dict(event_id=eid, expected_kpis=kpis[eid], event_name=e["name"], category=e["category"], start_date=e["start"],
                         end_date=None if e["end"] == sim.SIM_END else e["end"], scope_drug=e["drug"], scope_payer=e["payer"],
                         scope_region=e["region"], scope_source=e["source"], business_hypothesis=hypothesis[eid],
                         owner=owners[e["category"]], logged_by="analyst"))
register = pd.DataFrame(reg_rows)
register.to_csv(f"{OUT}/change_register.csv", index=False)

# ------------------------------------------------------------------ ground truth via counterfactual runs
Rf, Ff = prep(full, pres)
cB_full = decomp.period_components(Rf, Ff, B0, B1)
gt = []
for eid, e in sim.EVENTS.items():
    cf = sim.simulate(pres, cand, enabled=frozenset(set(sim.EVENTS) - {eid}), seed=SEED, detail=False)
    Rc, Fc = prep(cf, pres)
    cB_cf = decomp.period_components(Rc, Fc, B0, B1)
    res = decomp.lmdi(cB_cf, cB_full)
    bars, v0, v1 = decomp.waterfall(res)
    row = dict(event_id=eid, event_name=e["name"], in_register=e["in_register"], start_date=e["start"], end_date=e["end"],
               scope_drug=e["drug"], scope_payer=e["payer"], scope_region=e["region"], scope_source=e["source"],
               true_mechanism=e["factor"], true_direction=e["direction"],
               true_impact_eq28_2025=round(v1 - v0, 1), true_impact_pct_of_2025=round((v1 - v0) / v1 * 100, 2))
    for k, v in bars.items():
        if not k.startswith("check"):
            row[f"true_{k.lower().replace(' ', '_')}"] = round(v, 1)
    gt.append(row)
    print(f"{eid} true 2025 impact {v1 - v0:>9,.0f} EQ28")
gt = pd.DataFrame(gt)
gt.to_csv(f"{OUT}/ground_truth_events.csv", index=False)

# headline
cA = decomp.period_components(Rf, Ff, A0, A1)
bars, v0, v1 = decomp.waterfall(decomp.lmdi(cA, cB_full))
print(f"\n2024 EQ28 {v0:,.0f} -> 2025 {v1:,.0f} ({(v1 / v0 - 1) * 100:+.1f}%)")
print(bars.round(0))
