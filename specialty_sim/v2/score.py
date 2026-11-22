"""
Score v2 (with calibrated intervals) and v1 across worlds.

Calibration: one inflation factor k on interval width, chosen on the calibration worlds so that
95% intervals cover the truth at least 95% of the time; then frozen and applied to the holdout worlds.
"""
import json
import os
import sys
import numpy as np
import pandas as pd

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/worlds"
CAL = range(1001, 1031)
HOLD = range(1031, 1061)
FRESH = range(1061, 1091)   # generated after the estimator was frozen
MAT = 300          # materiality threshold, eq28 (about 0.2% of annual volume)
CLEAR = 1.25       # truths at least 25% apart define a 'clear' ranking pair


def load(seed):
    w = f"{ROOT}/w{seed}"
    if not os.path.exists(f"{w}/v2_estimates.csv"):
        return None
    gt = pd.read_csv(f"{w}/ground_truth_events.csv")
    v2 = pd.read_csv(f"{w}/v2_estimates.csv")
    v1 = json.load(open(f"{w}/v1_estimates.json"))
    ts = pd.read_csv(f"{w}/truth_summary.csv", index_col=0).value
    return gt, v2, v1, ts


def unlogged_match(gt_row, v2):
    sc = eval(gt_row.scope)
    t0 = pd.Timestamp(gt_row.true_start)
    for r in v2[~v2.logged].itertuples():
        s2 = eval(r.scope)
        same = all(k in s2 and set(v) & set(s2[k]) for k, v in sc.items())
        if same and abs((pd.Timestamp(r.est_start) - t0).days) <= 45:
            return r
    return None


def score_world(seed, k):
    d = load(seed)
    if d is None:
        return None, None
    gt, v2, v1, ts = d
    m = gt.merge(v2, on="event_id", how="left", suffixes=("", "_est"))
    m["est"] = m.est.fillna(0.0)
    m["sd"] = m.sd.fillna(0.0)
    kk = m.channels.map(lambda c: k.get(family(c), 1.0)).fillna(1.0)
    m["lo"], m["hi"] = m.est - 1.96 * kk * m.sd, m.est + 1.96 * kk * m.sd
    m["claimed"] = (m.lo > 0) | (m.hi < 0)
    m["v1_est"] = m.event_id.map(lambda e: v1.get(e, 0.0)).astype(float)
    m["seed"] = seed
    lg = m[m.in_register]
    U = units_of(lg)
    kk = U.channels.map(lambda c: k.get(family(c), 1.0)).fillna(1.0)
    U["lo"], U["hi"] = U.est - 1.96 * kk * U.sd, U.est + 1.96 * kk * U.sd
    U["claimed"] = (U.lo > 0) | (U.hi < 0)
    U["v1_est"] = U.unit.map(lambda un: sum(v1.get(e, 0.0) for e in str(un).split("+")))
    logged = U[~((U.n == 1) & U.has_decoy)]            # real units (a decoy inside a group is part of that unit)
    decoy = U[(U.n == 1) & U.has_decoy]
    unl = m[~m.in_register]
    rec = dict(seed=seed, n_events=int((lg.type != "decoy").sum()), n_units=len(logged), grouped_units=int((logged.n > 1).sum()),
               decoy_in_group=int(((U.n > 1) & U.has_decoy).sum()))
    t, e2, e1 = logged.true_impact.values, logged.est.values, logged.v1_est.values
    rec["acc_v2"] = 1 - np.abs(e2 - t).sum() / np.abs(t).sum()
    rec["acc_v1"] = 1 - np.abs(e1 - t).sum() / np.abs(t).sum()
    rec["abs_err_v2"], rec["abs_err_v1"], rec["abs_truth"] = np.abs(e2 - t).sum(), np.abs(e1 - t).sum(), np.abs(t).sum()
    rec["dir_err_v2"] = int(((logged.claimed) & (np.sign(logged.est) != np.sign(logged.true_impact)) & (logged.true_impact != 0)).sum())
    rec["dir_err_v1"] = int(((logged.v1_est != 0) & (np.sign(logged.v1_est) != np.sign(logged.true_impact)) & (logged.true_impact.abs() >= MAT)).sum())
    rec["missed_material_v2"] = int(((logged.true_impact.abs() >= MAT) & ~logged.claimed).sum())
    rec["missed_material_v1"] = int(((logged.true_impact.abs() >= MAT) & (logged.v1_est == 0)).sum())
    rec["covered"] = int(((logged.lo <= logged.true_impact) & (logged.true_impact <= logged.hi)).sum()) + int(((decoy.lo <= 0) & (decoy.hi >= 0)).sum())
    rec["n_cov"] = len(logged) + len(decoy)
    rec["decoy_claimed_v2"] = int(decoy.claimed.sum())
    rec["decoy_attrib_v1"] = int((m[m.type == "decoy"].v1_est.abs() > 0).sum())
    m.attrs["units"] = U.assign(seed=seed)
    # ranking among material events
    mat = logged[logged.true_impact.abs() >= MAT]
    hard = soft = flips1 = pairs = 0
    rows = list(mat.itertuples())
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            ta, tb = abs(a.true_impact), abs(b.true_impact)
            if max(ta, tb) < CLEAR * min(ta, tb):
                continue
            pairs += 1
            big, small = (a, b) if ta > tb else (b, a)
            if abs(big.est) < abs(small.est):
                # presented as ordered only if the magnitude intervals do not overlap
                mag = lambda r: ((0.0 if r.lo <= 0 <= r.hi else min(abs(r.lo), abs(r.hi))), max(abs(r.lo), abs(r.hi)))
                lo_small, _ = mag(small)
                _, hi_big = mag(big)
                if hi_big < lo_small:
                    hard += 1
                else:
                    soft += 1
            if abs(big.v1_est) < abs(small.v1_est):
                flips1 += 1
    rec.update(rank_pairs=pairs, rank_hard_flips_v2=hard, rank_soft_flips_v2=soft, rank_flips_v1=flips1)
    # unlogged
    if len(unl):
        u = unl.iloc[0]
        hit = unlogged_match(u, v2)
        rec["unlogged_type"] = u.type
        rec["unlogged_surfaced"] = hit is not None
        rec["unlogged_true"] = u.true_impact
        rec["unlogged_est"] = hit.est if hit is not None else 0.0
    rec["interaction"] = ts["all_events_impact"] - ts["sum_single_impacts"]
    rec["n_nuisance"] = int((~v2.logged).sum())
    return rec, m


def family(ch):
    ch = str(ch)
    if "pers" in ch:
        return "pers/ds"
    if "ref" in ch:
        return "ref/conv"
    if "conv" in ch:
        return "conv"
    return "new"


def units_of(m):
    """Collapse grouped events into units: truth summed, estimate shared."""
    g = m.groupby("unit", dropna=False)
    u = g.agg(true_impact=("true_impact", "sum"), est=("est", "first"), sd=("sd", "first"), channels=("channels", "first"),
              n=("event_id", "size"), types=("type", lambda x: "+".join(sorted(map(str, x)))),
              all_logged=("in_register", "all"), has_decoy=("type", lambda x: (x == "decoy").any())).reset_index()
    return u


def calibrate():
    zs = {}
    for s in CAL:
        d = load(s)
        if d is None:
            continue
        gt, v2, _, _ = d
        m = gt.merge(v2, on="event_id", suffixes=("", "_est"))
        m = m[m.in_register]
        u = units_of(m)
        u = u[u.sd > 0]
        for fam, z in zip(u.channels.map(family), ((u.est - u.true_impact).abs() / u.sd)):
            zs.setdefault(fam, []).append(z)
    k = {fam: max(1.0, float(np.quantile(v, 0.95)) / 1.96) for fam, v in zs.items()}
    return k, zs


if __name__ == "__main__":
    k, zs = calibrate()
    for fam, v in zs.items():
        v = np.array(v)
        print(f"calibration {fam:9s} units {len(v):3d}  uncalibrated coverage {(v <= 1.96).mean():.3f}  k = {k[fam]:.2f}")
    allrec, allev, allu = [], [], []
    for split, seeds in [("calibration", CAL), ("holdout (used in debugging)", HOLD), ("fresh holdout (frozen code)", FRESH)]:
        for s in seeds:
            r, m = score_world(s, k)
            if r is None:
                continue
            r["split"] = split
            m["split"] = split
            allrec.append(r)
            allev.append(m)
            allu.append(m.attrs.pop("units").assign(split=split))
    R, M = pd.DataFrame(allrec), pd.concat(allev)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(out, exist_ok=True)
    R.to_csv(f"{out}/world_scores.csv", index=False)
    M.to_csv(f"{out}/event_scores.csv", index=False)
    def summ(g):
        return pd.Series({
            "worlds": len(g),
            "logged events": int(g.n_events.sum()),
            "units sized (after grouping)": int(g.n_units.sum()),
            "grouped units (not separable)": int(g.grouped_units.sum()),
            "decoys absorbed into a group": int(g.decoy_in_group.sum()),
            "accuracy v2 (pooled)": round(1 - g.abs_err_v2.sum() / g.abs_truth.sum(), 3),
            "accuracy v1 (pooled)": round(1 - g.abs_err_v1.sum() / g.abs_truth.sum(), 3),
            "accuracy v2 (median world)": round(g.acc_v2.median(), 3),
            "accuracy v1 (median world)": round(g.acc_v1.median(), 3),
            "interval coverage v2": round(g.covered.sum() / g.n_cov.sum(), 3),
            "direction errors v2 (claimed)": int(g.dir_err_v2.sum()),
            "direction errors v1 (material)": int(g.dir_err_v1.sum()),
            "material events not claimed v2": int(g.missed_material_v2.sum()),
            "material events missed v1": int(g.missed_material_v1.sum()),
            "decoy claimed v2": int(g.decoy_claimed_v2.sum()),
            "decoy attributed v1": int(g.decoy_attrib_v1.sum()),
            "clear ranking pairs": int(g.rank_pairs.sum()),
            "hard ranking flips v2": int(g.rank_hard_flips_v2.sum()),
            "soft flips v2 (shown as tie)": int(g.rank_soft_flips_v2.sum()),
            "ranking flips v1": int(g.rank_flips_v1.sum()),
            "unlogged surfaced": f"{int(g.unlogged_surfaced.sum())}/{len(g)}",
            "unlogged size error (median abs %)": round((np.abs(g.unlogged_est - g.unlogged_true) / g.unlogged_true.abs())[g.unlogged_surfaced].median() * 100, 1),
        })
    S = R.groupby("split").apply(summ).T
    S.to_csv(f"{out}/summary.csv")
    pd.concat(allu).to_csv(f"{out}/unit_scores.csv", index=False)
    pd.Series({f: round(v, 3) for f, v in k.items()}).to_csv(f"{out}/calibration_factors.csv", header=["k"])
    print(S.to_string())
    # per event type
    UU = pd.concat(allu)
    L = UU[(UU.n == 1) & ~UU.has_decoy].rename(columns={"types": "type", "unit": "event_id"}).copy()
    L["ae2"], L["ae1"] = (L.est - L.true_impact).abs(), (L.v1_est - L.true_impact).abs()
    L["cov"] = (L.lo <= L.true_impact) & (L.true_impact <= L.hi)
    T = L.groupby("type").agg(n=("event_id", "size"), mean_abs_truth=("true_impact", lambda x: x.abs().mean()),
                              acc_v2=("ae2", "sum"), acc_v1=("ae1", "sum"), tsum=("true_impact", lambda x: x.abs().sum()), coverage=("cov", "mean"))
    T["acc_v2"] = 1 - T.acc_v2 / T.tsum
    T["acc_v1"] = 1 - T.acc_v1 / T.tsum
    T = T.drop(columns="tsum").round(3)
    T.to_csv(f"{out}/by_event_type.csv")
    print(T.to_string())
