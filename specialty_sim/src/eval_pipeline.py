"""
Blind evaluation of the two-layer explanation pipeline against planted ground truth.

The pipeline only sees what an analyst would have: the fact tables and the change register.
ground_truth_events.csv is opened only in the final scoring step.

Layer 1  LMDI under two identities
         simple chain:  V = R * S/R * A/S * F/A * V/F
         stock-flow:    V = [R * S/R * Fn/S * Vn/Fn]  (new patients this period)
                          + [Ap * C/Ap * Fc/C * Vc/Fc] (carried-in base; Ap = prior-period actives)
Layer 2  monthly metric series per slice -> year-on-year differencing -> PELT change points
         -> match breaks to register events (scope + timing) -> unmatched breaks = 'Unexplained'
         -> event impact = difference-in-differences on the LMDI factor contributions in scope
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import ruptures as rpt

warnings.filterwarnings("ignore")
DATA = sys.argv[1] if len(sys.argv) > 1 else "../data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "../eval"
os.makedirs(OUT, exist_ok=True)
CELL = ["drug", "payer", "region"]
P_A = (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"))
P_B = (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31"))
P_PRE = (pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31"))

ref = pd.read_parquet(f"{DATA}/fact_referral.parquet")
fill = pd.read_parquet(f"{DATA}/fact_fill.parquet")
pres = pd.read_parquet(f"{DATA}/dim_prescriber.parquet")
register = pd.read_csv(f"{DATA}/change_register.csv", parse_dates=["start_date", "end_date"])
fill["practice"] = fill.prescriber_npi.map(dict(zip(pres.prescriber_npi, pres.practice)))


# =========================================================================== Layer 1
def components(p, prev):
    r = ref[(ref.referral_date >= p[0]) & (ref.referral_date <= p[1])]
    f = fill[(fill.fill_date >= p[0]) & (fill.fill_date <= p[1])]
    fp = fill[(fill.fill_date >= prev[0]) & (fill.fill_date <= prev[1])]
    new_pts = set(f.loc[f.fill_number == 0, "patient_id"])
    fn, fc = f[f.patient_id.isin(new_pts)], f[~f.patient_id.isin(new_pts)]
    g = lambda d: d.groupby(CELL)
    return pd.concat([
        g(r).size().rename("R"), g(f[f.fill_number == 0]).size().rename("S"),
        g(f).patient_id.nunique().rename("A"), g(f).size().rename("F"), g(f).eq28.sum().rename("V"),
        g(fn).size().rename("Fn"), g(fn).eq28.sum().rename("Vn"),
        g(fp).patient_id.nunique().rename("Ap"), g(fc).patient_id.nunique().rename("C"),
        g(fc).size().rename("Fc"), g(fc).eq28.sum().rename("Vc"),
    ], axis=1).fillna(0)


# factor: (term volume column, numerator, denominator, exec group)
SPECS = {
    "simple_chain": {
        "Referrals": ("V", "R", None, "Referrals"), "Conversion": ("V", "S", "R", "Conversion"),
        "Patient base": ("V", "A", "S", "Patient base / retention"), "Fill frequency": ("V", "F", "A", "Fill frequency"),
        "Days supply mix": ("V", "V", "F", "Days supply mix")},
    "stock_flow": {
        "Referrals": ("Vn", "R", None, "Referrals"), "Conversion": ("Vn", "S", "R", "Conversion"),
        "New-patient fill frequency": ("Vn", "Fn", "S", "Fill frequency"), "New-patient days supply": ("Vn", "Vn", "Fn", "Days supply mix"),
        "Opening patient base": ("Vc", "Ap", None, "Opening patient base"), "Retention": ("Vc", "C", "Ap", "Patient base / retention"),
        "Base fill frequency": ("Vc", "Fc", "C", "Fill frequency"), "Base days supply": ("Vc", "Vc", "Fc", "Days supply mix")},
}
METRIC_FACTORS = {
    "simple_chain": {"referrals": ["Referrals"], "conversion": ["Conversion"], "pa_required_rate": ["Conversion"],
                     "tat_median": ["Conversion"], "lapse_rate": ["Patient base", "Fill frequency"], "fills_per_active": ["Fill frequency"],
                     "eq28_per_fill": ["Days supply mix"]},
    "stock_flow": {"referrals": ["Referrals"], "conversion": ["Conversion"], "pa_required_rate": ["Conversion"],
                   "tat_median": ["Conversion"], "lapse_rate": ["Retention", "New-patient fill frequency", "Base fill frequency"],
                   "fills_per_active": ["New-patient fill frequency", "Base fill frequency"],
                   "eq28_per_fill": ["New-patient days supply", "Base days supply"]},
}


def logmean(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.isclose(a, b), a, (a - b) / (np.log(a) - np.log(b)))


def ratio(df, num, den, sfx):
    n = df[num + sfx]
    return n if den is None else n / df[den + sfx]


def lmdi(c0, c1, spec):
    df = c0.join(c1, how="outer", lsuffix="0", rsuffix="1").fillna(0)
    terms = sorted({v[0] for v in spec.values()})
    out = pd.DataFrame(index=df.index)
    out["V0"], out["V1"] = df.V0, df.V1
    resid = np.zeros(len(df))
    for t in terms:
        cols = {t} | {c for f, (tt, n, d, _) in spec.items() if tt == t for c in (n, d) if c}
        ok = np.logical_and.reduce([(df[c + s] > 0).values for c in cols for s in ("0", "1")])
        w = logmean(df[t + "1"].clip(lower=1e-9), df[t + "0"].clip(lower=1e-9))
        out[f"w_{t}"], out[f"ok_{t}"] = w, ok
        for f, (tt, n, d, _) in spec.items():
            if tt != t:
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                lr = np.log(ratio(df, n, d, "1") / ratio(df, n, d, "0")).values
            out[f"lr_{f}"] = np.where(ok, lr, 0.0)
            out[f] = np.where(ok, w * lr, 0.0)
        resid += np.where(ok, 0.0, (df[t + "1"] - df[t + "0"]).values)
    out["Residual"] = resid
    out["bucket"] = np.where(df.V0 == 0, "New entrant", np.where(df.V1 == 0, "Lost", np.where(np.abs(resid) > 0, "Sparse cell", "Decomposed")))
    return out, df


def exec_bars(res, spec):
    bars = {}
    for f, (_, _, _, grp) in spec.items():
        bars[grp] = bars.get(grp, 0) + res[f].sum()
    bars["New entrants"] = res.loc[res.bucket == "New entrant", "Residual"].sum()
    bars["Lost / sparse cells"] = res.loc[res.bucket != "New entrant", "Residual"].sum()
    return pd.Series(bars)


c_pre, c_a, c_b = components(P_PRE, (P_PRE[0] - pd.DateOffset(years=1), P_PRE[0] - pd.Timedelta(days=1))), components(P_A, P_PRE), components(P_B, P_A)
L1 = {}
for name, spec in SPECS.items():
    res, comp = lmdi(c_a, c_b, spec)
    L1[name] = (res, comp)
    exec_bars(res, spec).round(0).to_csv(f"{OUT}/waterfall_{name}.csv", header=["eq28_contribution"])
V_A, V_B = c_a.V.sum(), c_b.V.sum()


# =========================================================================== Layer 2: series
def month(s):
    return s.dt.to_period("M")


ref["m"] = month(ref.referral_date)
fill["m"] = month(fill.fill_date)
fill = fill.sort_values(["patient_id", "fill_date"])
fill["next_fill"] = fill.groupby("patient_id").fill_date.shift(-1)
horizon = fill.fill_date + pd.to_timedelta(fill.days_supply + 60, unit="D")
fill["lapse"] = np.where(horizon > pd.Timestamp("2025-12-31"), np.nan,
                         (fill.next_fill.isna() | (fill.next_fill > horizon)).astype(float))
practice_region = pres.groupby("practice").region.agg(lambda s: s.mode().iat[0]).to_dict()
top_practices = ref.practice.value_counts().head(25).index

SLICES = [{}] + [{d: v} for d in ["drug", "payer", "region", "referral_source"] for v in sorted(ref[d].unique())] \
         + [{"drug": a, "payer": b} for a, b in ref.groupby(["drug", "payer"]).size().loc[lambda s: s > 600].index] \
         + [{"practice": p} for p in top_practices]
MONTHS = pd.period_range("2023-01", "2025-12", freq="M")


def sel(df, s):
    m = np.ones(len(df), bool)
    for k, v in s.items():
        m &= (df[k] == v).values
    return df[m]


def series(s):
    r, f = sel(ref, s), sel(fill, s)
    gr, gf = r.groupby("m"), f.groupby("m")
    out = pd.DataFrame(index=MONTHS)
    out["referrals"] = gr.size()
    out["conversion"] = gr.first_fill_date.apply(lambda x: x.notna().mean())
    out["pa_required_rate"] = gr.first_reject_code.apply(lambda x: (x == "75").mean())
    out["tat_median"] = r[r.first_fill_date.notna()].groupby("m").tat_days.mean()   # mean: integer medians step artificially
    out["lapse_rate"] = gf.lapse.mean()
    out["fills_per_active"] = gf.size() / gf.patient_id.nunique()
    out["eq28_per_fill"] = gf.eq28.mean()
    out.loc[pd.Period("2025-11"):, ["conversion", "tat_median"]] = np.nan   # cohorts still maturing
    n_ref, n_fill = gr.size().reindex(MONTHS).mean(), gf.size().reindex(MONTHS).mean()
    return out, n_ref, n_fill


def detect(x, is_count):
    """YoY-difference the series, run PELT, return (month, effect, relative effect) for each break."""
    x = x.astype(float)
    y = (np.log1p(x.fillna(0)) - np.log1p(x.shift(12).fillna(0))) if is_count else (x - x.shift(12))
    y = y.loc["2024-01":].dropna()
    if len(y) < 10:
        return []
    sig = np.median(np.abs(np.diff(y.values))) / 0.6745 / np.sqrt(2) + 1e-9
    algo = rpt.Pelt(model="l2", min_size=2, jump=1).fit(y.values.reshape(-1, 1))
    bks = algo.predict(pen=3 * sig ** 2 * np.log(len(y)))[:-1]
    base = x.loc["2023-01":"2023-12"].mean()
    outs = []
    for b in bks:
        pre, post = y.values[max(0, b - 6):b], y.values[b:b + 6]
        eff = post.mean() - pre.mean()
        z = eff / (sig * np.sqrt(1 / len(pre) + 1 / len(post)))
        rel = eff if is_count else eff / base
        if abs(z) >= 3:
            outs.append((y.index[b], eff, rel))
    return outs


breaks = []
for s in SLICES:
    ser, n_ref, n_fill = series(s)
    for metric in ser.columns:
        need = n_fill if metric in ("lapse_rate", "fills_per_active", "eq28_per_fill") else n_ref
        if need < (40 if metric != "referrals" else 25):
            continue
        for mth, eff, rel in detect(ser[metric], metric == "referrals"):
            if abs(rel) < 0.08:
                continue
            breaks.append(dict(slice=" & ".join(f"{k}={v}" for k, v in s.items()) or "All", dims=s, metric=metric,
                               break_month=mth, effect=eff, relative_effect=rel))
B = pd.DataFrame(breaks)


# =========================================================================== Layer 2: roll-up
def ancestors(d):
    d = dict(d)
    if "practice" in d:
        return [{}, {"region": practice_region.get(d["practice"])}]
    keys = list(d)
    out = [{}]
    if len(keys) == 2:
        out += [{keys[0]: d[keys[0]]}, {keys[1]: d[keys[1]]}]
    return out if keys else []


B["rolled_into"] = None
B["mi"] = B.break_month.apply(lambda p: p.year * 12 + p.month)
for i, b in B.iterrows():
    for a in ancestors(b.dims):
        par = B[(B.metric == b.metric) & B.dims.apply(lambda x: x == a)]
        par = par[(par.mi - b.mi).abs() <= 3]
        par = par[np.sign(par.effect) == np.sign(b.effect)]
        par = par[par.relative_effect.abs() * 1.5 > abs(b.relative_effect)]
        if len(par):
            B.at[i, "rolled_into"] = par.slice.iat[0]
            break

# new entrants are found directly in Layer 1, not by change points
res_sf = L1["stock_flow"][0]
for drug in res_sf[res_sf.bucket == "New entrant"].index.get_level_values("drug").unique():
    first = fill.loc[fill.drug == drug, "fill_date"].min().to_period("M")
    B.loc[len(B)] = dict(slice=f"drug={drug}", dims={"drug": drug}, metric="new_entrant", break_month=first,
                         effect=res_sf.loc[res_sf.index.get_level_values("drug") == drug, "Residual"].sum(), relative_effect=np.nan, rolled_into=None,
                         mi=first.year * 12 + first.month)


# =========================================================================== Layer 2: matching
def scope(ev):
    sc = {}
    for d, col in [("drug", "scope_drug"), ("payer", "scope_payer"), ("region", "scope_region"), ("referral_source", "scope_source")]:
        if pd.notna(ev[col]):
            sc[d] = set(str(ev[col]).split("|"))
    return sc


def match_quality(dims, sc):
    """3 exact scope, 2 slice narrower than scope, 1 slice broader than scope, None incompatible."""
    d = dict(dims)
    narrower = "practice" in d
    if narrower:
        d = {"region": practice_region.get(d["practice"])}
    for k, vals in sc.items():
        if k in d and d[k] not in vals:
            return None
    hits = sum(1 for k in sc if k in d)
    extras = sum(1 for k in d if k not in sc)
    if not sc:
        return 3 if not d else 2
    if hits == len(sc) and extras == 0 and not narrower:
        return 3
    if hits == len(sc):
        return 2
    return 1 if hits > 0 else None     # a slice must share at least one scope dimension to count as evidence


assign = []
for i, b in B.iterrows():
    best = None
    if b.rolled_into is None:
        for _, ev in register.iterrows():
            start = ev.start_date.to_period("M")
            end = ev.end_date.to_period("M") if pd.notna(ev.end_date) else None
            lags = [(b.break_month - start).n] + ([(b.break_month - (end + 1)).n] if end is not None else [])
            lag = min(lags, key=abs)
            if not (-1 <= lag <= 2):
                continue
            sc = scope(ev)
            q = match_quality(b.dims, sc)
            if q is None:
                continue
            expected = b.metric in str(ev.expected_kpis).split("|")
            if not sc and not (q == 3 and expected):
                continue                      # global events: only the top-level series, only expected KPIs
            if sc and not expected and q != 3:
                continue                      # side effects only accepted at exact scope
            key = (q + (1 if expected else 0), expected, -abs(lag))   # exact-scope side effect beats a weak overlapping match
            if best is None or key > best[0]:
                best = (key, ev.event_id, lag, q, expected)
    if best:
        assign.append(dict(event_id=best[1], lag_months=best[2], match_quality={3: "exact", 2: "narrower slice", 1: "broader slice", 0.5: "overlapping slice"}[best[3]],
                           kpi_expected=best[4]))
    else:
        assign.append(dict(event_id="ROLLED UP" if b.rolled_into else "UNEXPLAINED", lag_months=None, match_quality=None, kpi_expected=None))
B = pd.concat([B.reset_index(drop=True), pd.DataFrame(assign)], axis=1)
B["period"] = np.where(B.break_month < pd.Period("2025-01"), "2024 (no events: false positive)", "2025")
B.drop(columns="dims").sort_values(["event_id", "break_month"]).to_csv(f"{OUT}/break_log.csv", index=False)


# =========================================================================== Layer 2: impact attribution
def in_scope(idx, sc):
    m = np.ones(len(idx), bool)
    for k, vals in sc.items():
        m &= idx.get_level_values(k).isin(vals) if k in CELL else False
    return m


MECHANISM = {   # metric -> exec factor groups that carry that mechanism (coupled factors)
    "referrals": {"Referrals", "Conversion"}, "conversion": {"Referrals", "Conversion"}, "pa_required_rate": {"Referrals", "Conversion"},
    "tat_median": {"Referrals", "Conversion"}, "new_entrant": {"Referrals", "Conversion"},
    "lapse_rate": {"Patient base / retention", "Fill frequency"}, "fills_per_active": {"Patient base / retention", "Fill frequency"},
    "eq28_per_fill": {"Days supply mix", "Fill frequency"},
}


def attribute(identity):
    res, comp = L1[identity]
    spec = SPECS[identity]
    b25 = B[(B.period == "2025") & B.event_id.str.startswith("E") & (B.kpi_expected == True)]   # size only on hypothesised KPIs
    reg = register.set_index("event_id")
    est, detail, used = {}, [], {f: 0.0 for f in spec}
    order = sorted(reg.index, key=lambda e: len(scope(reg.loc[e])) == 0)       # scoped events first
    for eid in order:
        mets = set(b25.loc[b25.event_id == eid, "metric"])
        if not mets:
            continue
        sc = scope(reg.loc[eid])
        val = 0.0
        if sc and set(sc) <= set(CELL):
            msk = in_scope(res.index, sc)
            newm = msk & (res.bucket == "New entrant").values
            val += res.loc[newm, "Residual"].sum()
            detail.append((eid, "New entrant", res.loc[newm, "Residual"].sum()))
            ctrl = (res.index.get_level_values("drug").isin(sc["drug"]) & ~msk) if ("drug" in sc and "payer" in sc) else ~msk
            c = comp[ctrl]
            groups = set().union(*[MECHANISM[m] for m in mets])
            for f, (t, n, d, grp) in spec.items():      # DiD on the event's mechanism factors only
                if grp not in groups:
                    continue
                num1, num0 = c[n + "1"].sum(), c[n + "0"].sum()
                den1, den0 = (c[d + "1"].sum(), c[d + "0"].sum()) if d else (1, 1)
                lr_ctrl = np.log((num1 / den1) / (num0 / den0))
                okm = msk & res[f"ok_{t}"].values
                contrib = (res.loc[okm, f"w_{t}"] * (res.loc[okm, f"lr_{f}"] - lr_ctrl)).sum()
                val += contrib
                used[f] += contrib
                detail.append((eid, grp, contrib))
            tier = "Confirmed (scoped DiD)"
        elif not sc:
            groups = set().union(*[MECHANISM[m] for m in mets])
            factors = [f for f, v in spec.items() if v[3] in groups and any(f in METRIC_FACTORS[identity].get(m, []) for m in mets) or
                       (v[3] in groups and v[3] == "Fill frequency")]
            for f in factors:
                rem = res[f].sum() - used[f]
                val += rem
                used[f] += rem
                detail.append((eid, spec[f][3], rem))
            tier = "Probable (no control group)"
        else:
            tier = "Detected, not sized (scope not in cell grain)"
        est[eid] = (val, tier)
    return est, pd.DataFrame(detail, columns=["event_id", "exec_factor", "eq28"]), used


gt = pd.read_csv(f"{DATA}/ground_truth_events.csv")          # <- first time ground truth is opened
rows = []
ATT = {k: attribute(k) for k in SPECS}
est = {k: {e: v[0] for e, v in ATT[k][0].items()} for k in SPECS}
for _, g in gt.iterrows():
    eb = B[(B.event_id == g.event_id) & (B.period == "2025")]
    row = dict(event_id=g.event_id, event_name=g.event_name, in_register=g.in_register, true_mechanism=g.true_mechanism,
               true_impact_eq28=g.true_impact_eq28_2025)
    if g.event_id == "E10":
        u = B[(B.event_id == "UNEXPLAINED") & (B.metric == "referrals") & (B.break_month >= pd.Period("2025-08")) & (B.break_month <= pd.Period("2025-11"))
              & B.slice.str.contains("Lakeside|region=Midwest|drug=")]
        row.update(detected=len(u) > 0, best_slice="; ".join(u.slice.unique()[:3]), metrics="referrals" if len(u) else "",
                   outcome="Surfaced as UNEXPLAINED break" if len(u) else "Missed")
        for k in SPECS:
            row[f"est_{k}"] = 0.0
    else:
        best = eb.sort_values("match_quality", key=lambda s: s.map({"exact": 0, "narrower slice": 1, "broader slice": 2})).head(1)
        row.update(detected=len(eb) > 0, best_slice=best.slice.iat[0] if len(best) else "", metrics=", ".join(sorted(eb.metric.unique())),
                   outcome=("False attribution (decoy)" if len(eb) else "Correctly not attributed") if g.event_id == "E09"
                   else ("Detected (" + best.match_quality.iat[0] + ")" if len(eb) else "Missed"))
        for k in SPECS:
            row[f"est_{k}"] = round(est[k].get(g.event_id, 0.0), 1)
    rows.append(row)
S = pd.DataFrame(rows)
for k in SPECS:
    S[f"abs_err_{k}"] = (S[f"est_{k}"] - S.true_impact_eq28).abs().round(1)
S.to_csv(f"{OUT}/scorecard.csv", index=False)

# =========================================================================== summary
logged_real = S[S.in_register & (S.event_id != "E09")]
summary = {
    "volume_2024_eq28": round(V_A), "volume_2025_eq28": round(V_B), "change_eq28": round(V_B - V_A),
    "logged_events_detected": f"{int(logged_real.detected.sum())}/{len(logged_real)}",
    "decoy_falsely_attributed": bool(S.loc[S.event_id == "E09", "detected"].iat[0]),
    "unlogged_event_surfaced": bool(S.loc[S.event_id == "E10", "detected"].iat[0]),
    "breaks_2025_after_rollup": int(((B.period == "2025") & (B.event_id != "ROLLED UP")).sum()),
    "breaks_2025_matched": int(((B.period == "2025") & B.event_id.str.startswith("E")).sum()),
    "breaks_2025_unexplained": int(((B.period == "2025") & (B.event_id == "UNEXPLAINED")).sum()),
    "false_positive_breaks_2024": int(((B.period != "2025") & (B.event_id != "ROLLED UP")).sum()),
}
for k in SPECS:
    summary[f"attribution_accuracy_{k}"] = round(1 - S[f"abs_err_{k}"].sum() / S.true_impact_eq28.abs().sum(), 3)
pd.Series(summary).to_csv(f"{OUT}/summary.csv", header=["value"])
print(pd.Series(summary).to_string())
print()
print(S[["event_id", "outcome", "metrics", "true_impact_eq28", "est_simple_chain", "est_stock_flow"]].to_string(index=False))
for k in SPECS:
    print(f"\nWaterfall ({k}): 2024 {V_A:,.0f} -> 2025 {V_B:,.0f}")
    print(exec_bars(L1[k][0], SPECS[k]).round(0).to_string())

# =========================================================================== exec-ready outputs
est_sf, det_sf, _ = ATT["stock_flow"]
reg = register.set_index("event_id")
rows = []
for eid, (val, tier) in sorted(est_sf.items(), key=lambda kv: -abs(kv[1][0])):
    ev = reg.loc[eid]
    mets = B[(B.event_id == eid) & (B.period == "2025")]
    rows.append(dict(driver=ev.event_name, event_id=eid, owner=ev.owner, start=ev.start_date.date(), eq28_impact=round(val),
                     pct_of_2024=round(val / V_A * 100, 1), evidence=tier,
                     what_moved=", ".join(sorted(mets.metric.unique())),
                     side_effects=", ".join(sorted(mets.loc[mets.kpi_expected == False, "metric"].unique()))))
explained = sum(v[0] for v in est_sf.values())
rows.append(dict(driver="Underlying trend and unexplained", event_id="", owner="Analytics", start=None,
                 eq28_impact=round(V_B - V_A - explained), pct_of_2024=round((V_B - V_A - explained) / V_A * 100, 1),
                 evidence="Residual", what_moved="", side_effects=""))
EX = pd.DataFrame(rows)
EX.to_csv(f"{OUT}/exec_attribution.csv", index=False)

# unexplained breaks clustered by metric and month (+-1): these are the 'go investigate' items
U = B[(B.period == "2025") & (B.event_id == "UNEXPLAINED")].sort_values("mi").copy()
M = B[(B.period == "2025") & B.event_id.str.startswith("E")]
clusters, cid = [], 0
for metric, g in U.groupby("metric"):
    last = None
    for _, r in g.iterrows():
        if last is None or r.mi - last > 1:
            cid += 1
        last = r.mi
        clusters.append(dict(cluster=cid, metric=metric, break_month=str(r.break_month), slice=r.slice, dims=r.dims, relative_effect=round(r.relative_effect, 3)))
C = pd.DataFrame(clusters)
if len(C):
    C = C.reindex(C.relative_effect.abs().sort_values(ascending=False).index)
    summ = C.groupby("cluster").agg(metric=("metric", "first"), months=("break_month", lambda s: f"{s.min()}..{s.max()}"),
                                     n_slices=("slice", "size"), strongest_slices=("slice", lambda s: "; ".join(s.head(4))),
                                     max_abs_effect=("relative_effect", lambda s: s.abs().max()), top_dims=("dims", "first"))
    def note(r):
        lo = pd.Period(r.months.split("..")[0]).ordinal
        near = M[(M.metric == r.metric) & ((M.break_month.apply(lambda p: p.ordinal) - lo).abs() <= 2)]
        near = near[near.event_id.apply(lambda e: (lambda q: q is not None and q >= 1)(match_quality(r.top_dims, scope(reg.loc[e]))))]
        return f"possibly local variation of {', '.join(sorted(near.event_id.unique()))}" if len(near) else "no logged event: investigate"
    summ["triage"] = summ.apply(note, axis=1)
    summ = summ.drop(columns="top_dims").sort_values(["triage", "max_abs_effect"], ascending=[True, False])
    summ.to_csv(f"{OUT}/unexplained_clusters.csv")
    print("\nUnexplained clusters (top 5):")
    print(summ[["metric", "months", "n_slices", "strongest_slices", "triage"]].head(6).to_string())
print("\nExec attribution (stock-flow):")
print(EX[["driver", "eq28_impact", "pct_of_2024", "evidence"]].to_string(index=False))
