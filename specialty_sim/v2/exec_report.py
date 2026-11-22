"""
Exec-ready attribution table from v2 estimates.

Tiers
  Confirmed           logged event, sized alone, calibrated 99.9% interval excludes zero (no false confirmations in 90 test worlds)
  Probable            95% interval excludes zero but 99.9% does not
  Joint               logged events whose effects the data cannot separate; reported as one figure
  Not distinguishable interval includes zero; reported as 'no measurable effect', never as a number
  Unexplained driver  discovered step change with no logged cause; sized, flagged to investigate
Residual = year-on-year change minus everything above (underlying trend, unmodelled effects).
Drivers are ranked by magnitude; ranks are shared where intervals overlap.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import family

HERE = os.path.dirname(os.path.abspath(__file__))


def report(world_dir, calib_path=f"{HERE}/results/calibration_factors.csv"):
    v2 = pd.read_csv(f"{world_dir}/v2_estimates.csv")
    reg = pd.read_csv(f"{world_dir}/change_register.csv")
    fill = pd.read_parquet(f"{world_dir}/fact_fill.parquet")
    k = pd.read_csv(calib_path, index_col=0)["k"].to_dict() if os.path.exists(calib_path) else {}
    v24 = fill.loc[fill.fill_date.dt.year == 2024, "eq28"].sum()
    v25 = fill.loc[fill.fill_date.dt.year == 2025, "eq28"].sum()
    names = dict(zip(reg.event_id, reg.event_type_logged.str.replace("_", " ") + " (" +
                     reg[["scope_drug", "scope_payer", "scope_region", "scope_source"]].apply(lambda r: ", ".join(str(x) for x in r if isinstance(x, str)) or "all", axis=1) + ")"))
    rows = []
    for unit, g in v2.groupby("unit", sort=False):
        r = g.iloc[0]
        kk = k.get(family(r.channels), 1.0)
        lo, hi = r.est - 1.96 * kk * r.sd, r.est + 1.96 * kk * r.sd
        strong = abs(r.est) > 3.29 * kk * r.sd if r.sd > 0 else abs(r.est) > 0
        ids = unit.split("+")
        logged = g.logged.all()
        label = " + ".join(names.get(i, r.label) for i in ids) if logged else r.label
        if not logged:
            tier = "Unexplained driver: investigate" if (lo > 0 or hi < 0) else "Unexplained (not distinguishable)"
        elif len(ids) > 1:
            tier = "Joint: cannot be separated" + ("" if (lo > 0 or hi < 0) else " (not distinguishable)")
        elif strong:
            tier = "Confirmed"
        elif lo > 0 or hi < 0:
            tier = "Probable"
        else:
            tier = "Not distinguishable from zero"
        rows.append(dict(driver=label, events=unit, tier=tier, eq28=r.est, lo95=lo, hi95=hi, pct_of_2024=r.est / v24 * 100))
    T = pd.DataFrame(rows)
    sized = T[~T.tier.str.contains("Not distinguishable|not distinguishable")]
    T.loc[T.tier.str.contains("istinguishable"), ["eq28", "pct_of_2024"]] = 0.0
    T = T.reindex(T.eq28.abs().sort_values(ascending=False).index)
    # shared ranks where magnitude intervals overlap
    mag = [((0 if r.lo95 <= 0 <= r.hi95 else min(abs(r.lo95), abs(r.hi95))), max(abs(r.lo95), abs(r.hi95))) for r in T.itertuples()]
    rank, cur, anchor_lo, n_ranked = [], 0, None, 0
    for i, (lo_m, hi_m) in enumerate(mag):
        if T.iloc[i].eq28 == 0:
            rank.append("-")
            continue
        n_ranked += 1
        if anchor_lo is None or hi_m < anchor_lo:      # clearly smaller than the head of the current tie group
            cur, anchor_lo = n_ranked, lo_m
        rank.append(str(cur))
    T.insert(0, "rank", rank)
    resid = (v25 - v24) - sized.eq28.sum()
    T = pd.concat([T, pd.DataFrame([dict(rank="-", driver="Underlying trend and everything not modelled", events="", tier="Residual",
                                         eq28=resid, lo95=np.nan, hi95=np.nan, pct_of_2024=resid / v24 * 100)])], ignore_index=True)
    header = dict(volume_2024=round(v24), volume_2025=round(v25), change=round(v25 - v24), change_pct=round((v25 / v24 - 1) * 100, 1))
    return header, T


if __name__ == "__main__":
    h, T = report(sys.argv[1])
    print(h)
    pd.set_option("display.width", 220)
    print(T.round(1).to_string(index=False))
