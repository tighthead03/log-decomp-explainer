"""Multi-world harness: build world, run v2 estimator and v1 baseline, score against ground truth."""
import io, json, math, os, sys, contextlib, traceback
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim2, est2

V1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "eval_pipeline.py")


def v1_estimates(wdir):
    """Run the v1 pipeline (unchanged) on a v2 world; returns {event_id: estimate}."""
    shim = f"{wdir}/v1"
    os.makedirs(shim, exist_ok=True)
    reg = pd.read_csv(f"{wdir}/change_register.csv")
    reg["expected_kpis"] = reg.expected_kpis.str.replace("tat", "tat_median", regex=False)
    reg.to_csv(f"{shim}/change_register.csv", index=False)
    for f in ["fact_referral", "fact_fill", "dim_prescriber"]:
        if not os.path.exists(f"{shim}/{f}.parquet"):
            os.symlink(os.path.abspath(f"{wdir}/{f}.parquet"), f"{shim}/{f}.parquet")
    code = open(V1).read().split("gt = pd.read_csv(")[0]
    ns = {}
    argv = sys.argv
    sys.argv = ["v1", shim, f"{shim}/out"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code, "v1", "exec"), ns)
            est, _, _ = ns["attribute"]("stock_flow")
    finally:
        sys.argv = argv
    return {k: v[0] for k, v in est.items()}


def run_world(seed, root):
    wdir = f"{root}/w{seed}"
    try:
        sim2.make_world(seed, wdir)
        o = est2.run(wdir)
        o.to_csv(f"{wdir}/v2_estimates.csv", index=False)
        try:
            v1 = v1_estimates(wdir)
        except Exception as ex:
            v1 = {"_error": str(ex)[:200]}
        json.dump(v1, open(f"{wdir}/v1_estimates.json", "w"))
        return seed, "ok"
    except Exception:
        return seed, traceback.format_exc()[-800:]


if __name__ == "__main__":
    root, s0, s1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    from multiprocessing import Pool
    with Pool(2) as p:
        for seed, status in p.starmap(run_world, [(s, root) for s in range(s0, s1)]):
            print(seed, status, flush=True)
