import sys, os, json
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import est2
def go(w):
    try:
        est2.run(w).to_csv(f"{w}/v2_estimates.csv", index=False); return w, "ok"
    except Exception as ex:
        import traceback; return w, traceback.format_exc()[-600:]
if __name__ == "__main__":
    root = sys.argv[1]
    ws = sorted(f"{root}/{d}" for d in os.listdir(root) if os.path.exists(f"{root}/{d}/ground_truth_events.csv") and os.path.exists(f"{root}/{d}/v1_estimates.json"))
    if len(sys.argv) > 2:
        ws = [w for w in ws if any(w.endswith(f"/w{s}") for s in sys.argv[2:])]
    with Pool(2) as p:
        for w, st in p.imap_unordered(go, ws):
            if st != "ok": print(w, st, flush=True)
    print("done", len(ws))
