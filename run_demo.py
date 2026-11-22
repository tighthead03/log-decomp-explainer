"""
Log Decomp Explainer: run the v2 specialty-pharmacy volume attribution and see the results.

Usage (from the project folder):
    python run_demo.py sample                    # re-run the estimator on the bundled sample world (~1-3 min)
    python run_demo.py world --seed 1065         # simulate a brand-new world, then explain it
    python run_demo.py batch --start 1061 --end 1066 --procs 2   # several worlds + accuracy scorecard

Each run writes to runs/<name>/:
    exec_report.csv        executive attribution table (tiers, 95% intervals, shared ranks, residual)
    truth_comparison.csv   estimate vs the simulator's answer key, per event
    waterfall.png          2024 -> 2025 volume bridge
    estimate_vs_truth.png  each driver's estimate and interval against the true impact
    report.html            all of the above on one page (open in a browser)
"""
import argparse
import base64
import io
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.join(HERE, "specialty_sim", "v2")
sys.path.insert(0, V2)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_argv, sys.argv = sys.argv, sys.argv[:1]   # score.py reads sys.argv[1] at import time
import est2  # noqa: E402
import sim2  # noqa: E402
import exec_report  # noqa: E402
from score import family, unlogged_match  # noqa: E402
import explain_report  # noqa: E402
sys.argv = _argv

RUNS = os.path.join(HERE, "runs")
CALIB = os.path.join(V2, "results", "calibration_factors.csv")
SAMPLE = os.path.join(V2, "sample_world")
INPUTS = ["fact_referral.parquet", "fact_fill.parquet", "dim_prescriber.parquet", "change_register.csv",
          "ground_truth_events.csv", "truth_summary.csv"]


# ----------------------------------------------------------------------------- core steps
def estimate(wdir):
    t = time.time()
    out = est2.run(wdir)
    out.to_csv(os.path.join(wdir, "v2_estimates.csv"), index=False)
    return out, time.time() - t


def calib_factors():
    return pd.read_csv(CALIB, index_col=0)["k"].to_dict() if os.path.exists(CALIB) else {}


def compare_to_truth(wdir):
    """Per-event estimate vs ground truth (only available because this is a simulation)."""
    gt_path = os.path.join(wdir, "ground_truth_events.csv")
    if not os.path.exists(gt_path):
        return None, {}
    gt = pd.read_csv(gt_path)
    v2 = pd.read_csv(os.path.join(wdir, "v2_estimates.csv"))
    k = calib_factors()
    m = gt.merge(v2[["event_id", "unit", "est", "sd", "channels", "est_start"]], on="event_id", how="left")
    # unlogged events: credit the matching discovered "unexplained driver" (same scope, start within 45 days)
    for i, row in m[~m.in_register].iterrows():
        hit = unlogged_match(row, v2)
        if hit is not None:
            m.loc[i, ["est", "sd", "channels", "est_start"]] = [hit.est, hit.sd, hit.channels, hit.est_start]
            m.loc[i, "unit"] = row.event_id
    m[["est", "sd"]] = m[["est", "sd"]].fillna(0.0)
    m["unit"] = m.unit.fillna(m.event_id)
    # grouped (inseparable) events share one estimate -> compare against summed truth
    u = m.groupby("unit", sort=False).agg(event_ids=("event_id", "+".join), types=("type", "+".join),
                                          in_register=("in_register", "all"), true_impact=("true_impact", "sum"),
                                          est=("est", "first"), sd=("sd", "first"), channels=("channels", "first"),
                                          true_start=("true_start", "first"), est_start=("est_start", "first")).reset_index()
    kk = u.channels.map(lambda c: k.get(family(c), 1.0)).fillna(1.0)
    u["lo95"], u["hi95"] = u.est - 1.96 * kk * u.sd, u.est + 1.96 * kk * u.sd
    u["covered"] = (u.lo95 <= u.true_impact) & (u.true_impact <= u.hi95)
    u["abs_error"] = (u.est - u.true_impact).abs()
    u["right_direction"] = np.where(u.true_impact.abs() < 1, np.nan, (np.sign(u.est) == np.sign(u.true_impact)).astype(float))
    real = u[u.in_register & ~u.types.str.fullmatch("decoy")]
    decoy = u[u.types.str.fullmatch("decoy")]
    unlogged = u[~u.in_register]
    nuis = v2[~v2.logged]
    stats = {
        "events in register (real)": len(real),
        "attribution accuracy (logged events)": 1 - real.abs_error.sum() / max(real.true_impact.abs().sum(), 1e-9),
        "95% interval coverage": u[u.in_register].covered.mean(),
        "direction errors (material, >=300 eq28)": int(((real.true_impact.abs() >= 300) & (real.right_direction == 0)).sum()),
        "decoy wrongly claimed": int(((decoy.lo95 > 0) | (decoy.hi95 < 0)).sum()),
        "unlogged events (truth)": ", ".join(f"{r.event_ids} {r.types} {r.true_impact:+.0f}" for r in unlogged.itertuples()) or "none",
        "unexplained drivers found": ", ".join(f"{r.event_id}: {r.label} {r.est:+.0f}" for r in nuis.itertuples()) or "none",
    }
    return u, stats


# ----------------------------------------------------------------------------- charts / report
def fig_png(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return b.getvalue()


def waterfall(header, T):
    rows = T[(T.eq28 != 0)].copy()
    labels = ["2024 volume"] + [d[:45] for d in rows.driver] + ["2025 volume"]
    vals = rows.eq28.tolist()
    fig, ax = plt.subplots(figsize=(11, 0.45 * len(labels) + 1.5))
    run = header["volume_2024"]
    base = header["volume_2024"]
    lo = min(header["volume_2024"], header["volume_2025"]) * 0.97
    ax.barh(0, base - lo, left=lo, color="#5b6b7f")
    for i, (v, tier) in enumerate(zip(vals, rows.tier), start=1):
        col = "#9aa5b1" if tier == "Residual" else ("#d98c2b" if "Unexplained" in tier else ("#2e8b57" if v > 0 else "#c0392b"))
        ax.barh(i, v, left=run, color=col)
        ax.text(run + v + (60 if v >= 0 else -60), i, f"{v:+,.0f}", va="center", ha="left" if v >= 0 else "right", fontsize=8)
        run += v
    ax.barh(len(labels) - 1, header["volume_2025"] - lo, left=lo, color="#5b6b7f")
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(left=lo)
    ax.set_xlabel("eq28 (28-day equivalent fills)")
    ax.set_title(f"Volume bridge 2024 → 2025: {header['volume_2024']:,} → {header['volume_2025']:,} ({header['change_pct']:+}%)")
    ax.grid(axis="x", alpha=0.3)
    return fig


def est_vs_truth(u):
    u = u.reindex(u.true_impact.abs().sort_values().index)
    labels = [f"{r.event_ids} {r.types}" + ("" if r.in_register else " (unlogged)") for r in u.itertuples()]
    fig, ax = plt.subplots(figsize=(10, 0.45 * len(u) + 1.5))
    y = np.arange(len(u))
    ax.errorbar(u.est, y, xerr=[u.est - u.lo95, u.hi95 - u.est], fmt="o", color="#1f5f9e", capsize=3, label="v2 estimate (95% interval)")
    ax.scatter(u.true_impact, y, marker="x", color="#c0392b", s=60, zorder=3, label="true impact (answer key)")
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_yticks(y, labels, fontsize=8)
    ax.set_xlabel("impact on 2025 eq28")
    ax.set_title("Estimated vs true impact per event")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    return fig


def write_outputs(wdir, outdir, runtime=None):
    os.makedirs(outdir, exist_ok=True)
    header, T = exec_report.report(wdir, CALIB)
    header = {k: (float(v) if isinstance(v, np.floating) else v) for k, v in header.items()}
    T.to_csv(os.path.join(outdir, "exec_report.csv"), index=False)
    u, stats = compare_to_truth(wdir)
    imgs = {"waterfall.png": fig_png(waterfall(header, T))}
    if u is not None:
        u.to_csv(os.path.join(outdir, "truth_comparison.csv"), index=False)
        imgs["estimate_vs_truth.png"] = fig_png(est_vs_truth(u))
    for name, b in imgs.items():
        open(os.path.join(outdir, name), "wb").write(b)

    # console
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 60)
    print("\n=== Volume ===")
    print(f"2024: {header['volume_2024']:,}   2025: {header['volume_2025']:,}   change: {header['change']:+,} ({header['change_pct']:+}%)")
    print("\n=== Executive attribution ===")
    print(T[["rank", "driver", "tier", "eq28", "lo95", "hi95", "pct_of_2024"]].round(1).to_string(index=False))
    if stats:
        print("\n=== Against the answer key (simulation only) ===")
        for k_, v in stats.items():
            print(f"  {k_:42s} {v:.1%}" if isinstance(v, float) else f"  {k_:42s} {v}")

    # html (plain-English executive layout with granular pocket drill-down and technical appendix)
    explain_report.build_html(wdir, outdir, header, T, u=u, stats=stats, runtime=runtime, tech_imgs=imgs)
    pcsv = os.path.join(outdir, "granular_pocket_drilldown.csv")
    if os.path.exists(pcsv):
        P = pd.read_csv(pcsv)
        print("\n=== Granular Pocket Drill-Down (Region x Drug x Payer: NPIs, Reject 75, Plan Group, Copay/OOP) ===")
        cols_p = ["region", "drug", "payer", "delta_eq28", "lost_npis", "lost_npi_avg_rx", "gained_npis", "gained_npi_avg_rx",
                  "rej75_24", "rej75_25", "copay_card_24", "copay_card_25", "net_oop_24", "net_oop_25", "lead_plan"]
        print(P[cols_p].to_string(index=False))
    print(f"\nOutputs: {outdir}")
    return header, T, stats


# ----------------------------------------------------------------------------- commands
def cmd_sample(_):
    wdir = os.path.join(RUNS, "sample_world")
    os.makedirs(wdir, exist_ok=True)
    for f in INPUTS:
        shutil.copy(os.path.join(SAMPLE, f), wdir)
    print("Running v2 estimator on the bundled sample world (this takes a minute or two)...")
    _, rt = estimate(wdir)
    write_outputs(wdir, wdir, rt)


def build_and_estimate(seed):
    wdir = os.path.join(RUNS, f"world_{seed}")
    t = time.time()
    sim2.make_world(seed, wdir)
    sim_t = time.time() - t
    _, rt = estimate(wdir)
    return wdir, sim_t, rt


def cmd_world(a):
    print(f"Simulating world {a.seed} (2023-2025, random events, register errors, a decoy and an unlogged event)...")
    wdir, st, rt = build_and_estimate(a.seed)
    print(f"  simulated in {st:.0f}s, estimated in {rt:.0f}s")
    write_outputs(wdir, wdir, rt)


def _batch_one(seed):
    try:
        wdir, st, rt = build_and_estimate(seed)
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            _, _, stats = write_outputs(wdir, wdir, rt)
        return seed, "ok", stats
    except Exception:
        import traceback
        return seed, traceback.format_exc()[-600:], {}


def cmd_batch(a):
    seeds = list(range(a.start, a.end))
    print(f"Running {len(seeds)} worlds ({seeds[0]}..{seeds[-1]}) on {a.procs} processes...")
    rows = []
    if a.procs > 1:
        from multiprocessing import Pool
        with Pool(a.procs) as p:
            results = p.imap_unordered(_batch_one, seeds)
            for seed, status, stats in results:
                print(f"  world {seed}: {status if status != 'ok' else 'done'}", flush=True)
                rows.append(dict(seed=seed, status=status, **stats))
    else:
        for s in seeds:
            seed, status, stats = _batch_one(s)
            print(f"  world {seed}: {status if status != 'ok' else 'done'}", flush=True)
            rows.append(dict(seed=seed, status=status, **stats))
    R = pd.DataFrame(rows).sort_values("seed")
    os.makedirs(RUNS, exist_ok=True)
    out = os.path.join(RUNS, f"batch_{a.start}_{a.end - 1}.csv")
    R.to_csv(out, index=False)
    ok = R[R.status == "ok"]
    print("\n=== Batch scorecard ===")
    cols = ["seed", "attribution accuracy (logged events)", "95% interval coverage", "direction errors (material, >=300 eq28)", "decoy wrongly claimed"]
    print(ok[cols].round(3).to_string(index=False))
    if len(ok):
        print(f"\nMean accuracy {ok['attribution accuracy (logged events)'].mean():.1%} · mean coverage {ok['95% interval coverage'].mean():.1%} · "
              f"direction errors {int(ok['direction errors (material, >=300 eq28)'].sum())} · decoys claimed {int(ok['decoy wrongly claimed'].sum())}")
    print(f"Per-world reports in runs/world_<seed>/report.html, scorecard: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample", help="re-run the estimator on the bundled sample world")
    w = sub.add_parser("world", help="simulate a new world and explain it")
    w.add_argument("--seed", type=int, default=1065)
    b = sub.add_parser("batch", help="several worlds + scorecard")
    b.add_argument("--start", type=int, default=1061)
    b.add_argument("--end", type=int, default=1064, help="exclusive")
    b.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    {"sample": cmd_sample, "world": cmd_world, "batch": cmd_batch}[a.cmd](a)
