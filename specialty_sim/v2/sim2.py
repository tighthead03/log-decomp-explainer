"""
Randomised-world simulator (v2).

Each world draws its own set of events (type, scope, size, timing, ramp) from a library,
plus a messy change register (date errors, one decoy, one unlogged event). Ground truth per
event = 2025 eq28 actual minus 2025 eq28 with only that event switched off (common random numbers).
"""
import datetime as dt
import math
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import sim as v1  # reference data (payers, seasonality, names)

SIM_START = dt.date(2020, 1, 1)
SIM_END = dt.date(2025, 12, 31)
DATA_START = dt.date(2023, 1, 1)
D0 = pd.Timestamp(SIM_START)


def d2i(d):
    return (d - SIM_START).days


NDAYS = d2i(SIM_END) + 1
_dates = pd.date_range(SIM_START, SIM_END, freq="D")
MONTH, YEAR = _dates.month.values, _dates.year.values
Y2025 = d2i(dt.date(2025, 1, 1))
DATA_I = d2i(DATA_START)

DRUGS = [dict(d) for d in v1.DRUGS] + [
    dict(drug="Etanercept biosimilar", ndc="99999020001", base=0, ds=28, units=4, price=900, spec=("Rheum", "Derm"), haz=.085),
    dict(drug="Corvanta (mock LDD)", ndc="99999030001", base=50, ds=30, units=30, price=310, spec=("Rheum", "GI"), haz=.080),
]
DRUG_IX = {d["drug"]: i for i, d in enumerate(DRUGS)}
BIOSIM_OF = {"Stelara": "Ustekinumab biosimilar", "Enbrel": "Etanercept biosimilar"}
FAMILY = {"Stelara": "ustekinumab", "Ustekinumab biosimilar": "ustekinumab", "Enbrel": "etanercept", "Etanercept biosimilar": "etanercept",
          "Humira": "adalimumab", "Adalimumab biosimilar": "adalimumab"}
NEW_DRUGS = ["Velarix (mock LDD)", "Corvanta (mock LDD)"]
ESTABLISHED = [d["drug"] for d in DRUGS if d["base"] > 0 and d["drug"] not in NEW_DRUGS]
PAYERS = v1.PAYERS
PAYER_NAMES = [p["payer"] for p in PAYERS]
REGIONS, SOURCES = v1.REGIONS, v1.SOURCES
GROUPS = [("Lakeside Dermatology Associates", "Midwest", "Derm"), ("Riverbend Rheumatology", "South", "Rheum"),
          ("Harborview GI Partners", "West", "GI")]

# Carrier / Account / Group hierarchy beneath each Payer (with baseline gross OOP and copay card availability)
PLANS = {
    "Keystone Rx": [
        dict(carrier="CAR-KSRX", account="ACC-100", group="GRP-ABC1234", share=0.45, gross_oop=90.0, card_prob=0.78, max_assist=75.0),
        dict(carrier="CAR-KSRX", account="ACC-100", group="GRP-KSRX200", share=0.35, gross_oop=75.0, card_prob=0.80, max_assist=65.0),
        dict(carrier="CAR-KSRX", account="ACC-210", group="GRP-KSRX310", share=0.20, gross_oop=85.0, card_prob=0.75, max_assist=70.0),
    ],
    "Northstar Health": [
        dict(carrier="CAR-NSH", account="ACC-300", group="GRP-NSH101", share=0.50, gross_oop=85.0, card_prob=0.80, max_assist=70.0),
        dict(carrier="CAR-NSH", account="ACC-300", group="GRP-NSH202", share=0.50, gross_oop=70.0, card_prob=0.82, max_assist=60.0),
    ],
    "Meridian Care": [
        dict(carrier="CAR-MER", account="ACC-400", group="GRP-MER100", share=0.60, gross_oop=80.0, card_prob=0.78, max_assist=65.0),
        dict(carrier="CAR-MER", account="ACC-410", group="GRP-MER200", share=0.40, gross_oop=75.0, card_prob=0.80, max_assist=65.0),
    ],
    "Atlas Part D": [
        dict(carrier="CAR-ATLD", account="ACC-500", group="GRP-PARTD1", share=0.65, gross_oop=55.0, card_prob=0.00, max_assist=0.0),
        dict(carrier="CAR-ATLD", account="ACC-500", group="GRP-PARTD2", share=0.35, gross_oop=45.0, card_prob=0.00, max_assist=0.0),
    ],
    "Harbor Medicaid": [
        dict(carrier="CAR-HMCD", account="ACC-600", group="GRP-MCD001", share=1.00, gross_oop=5.0, card_prob=0.00, max_assist=0.0),
    ],
    "Summit Health Plan": [
        dict(carrier="CAR-SUM", account="ACC-700", group="GRP-SUM100", share=0.55, gross_oop=80.0, card_prob=0.76, max_assist=65.0),
        dict(carrier="CAR-SUM", account="ACC-720", group="GRP-SUM200", share=0.45, gross_oop=70.0, card_prob=0.80, max_assist=60.0),
    ],
}


# ============================================================================ world sampling
def sample_world(seed):
    rng = np.random.default_rng(seed + 7919)
    ev = []
    used = set()

    def start_day():
        return d2i(dt.date(2025, 2, 1)) + int(rng.integers(0, 257))  # Feb 1 .. mid Oct

    def ramp():
        return int(rng.integers(30, 91)) if rng.random() < 0.25 else 0

    def add(t, scope, params, in_register=True, start=None, end=None, rmp=None):
        s = start_day() if start is None else start
        ev.append(dict(type=t, scope=scope, params=params, start=s, end=(NDAYS - 1 if end is None else end),
                       ramp=ramp() if rmp is None else rmp, in_register=in_register))

    # always: one unlogged event and one decoy
    if rng.random() < 0.7:
        g = GROUPS[rng.integers(len(GROUPS))]
        add("prescriber_loss", {"practice": {g[0]}}, {"mult": 0.0}, in_register=False, rmp=0)
    else:
        add("referral_mult", {"region": {str(rng.choice(REGIONS))}}, {"mult": float(rng.uniform(0.7, 0.85))}, in_register=False)
    types = ["pa_policy", "referral_mult", "biosimilar", "new_drug", "intake_delay", "persistence", "ds_mandate", "push", "pa_policy", "referral_mult"]
    rng.shuffle(types)
    singles = {"biosimilar", "new_drug", "intake_delay", "persistence", "ds_mandate"}
    n_target = 6
    for t in types:
        if len(ev) >= n_target + 1:
            break
        if t in singles and t in used:
            continue
        used.add(t)
        if t == "pa_policy":
            d, p = str(rng.choice(ESTABLISHED)), str(rng.choice(PAYER_NAMES))
            if rng.random() < 0.75:
                prm = {"pa_new": float(rng.uniform(.75, .9)), "appr_new": float(rng.uniform(.5, .65))}
            else:
                prm = {"pa_new": float(rng.uniform(.08, .18)), "appr_new": .92}
            add(t, {"drug": {d}, "payer": {p}}, prm)
        elif t == "referral_mult":
            m = float(rng.uniform(0.7, 0.85)) if rng.random() < 0.5 else float(rng.uniform(1.15, 1.3))
            sc = {"region": {str(rng.choice(REGIONS))}} if rng.random() < 0.6 else {"payer": {str(rng.choice(PAYER_NAMES))}}
            add(t, sc, {"mult": m})
        elif t == "biosimilar":
            o = str(rng.choice(list(BIOSIM_OF)))
            add(t, {"drug": {o, BIOSIM_OF[o]}}, {"originator": o, "switch": float(rng.uniform(.35, .7)), "capture": float(rng.uniform(.6, .95))}, rmp=0)
        elif t == "new_drug":
            add(t, {"drug": {str(rng.choice(NEW_DRUGS))}}, {}, rmp=int(rng.integers(60, 121)))
        elif t == "intake_delay":
            s = start_day()
            sc = {} if rng.random() < 0.7 else {"source": {str(rng.choice(["Fax", "eRx"]))}}
            add(t, sc, {"extra": float(rng.uniform(3, 7))}, start=s, end=min(NDAYS - 1, s + int(rng.integers(60, 121))), rmp=0)
        elif t == "persistence":
            sc = {} if rng.random() < 0.6 else {"payer": {str(rng.choice(PAYER_NAMES))}}
            add(t, sc, {"haz_mult": float(rng.uniform(.65, .85)), "gap": float(rng.uniform(1.5, 3.5))})
        elif t == "ds_mandate":
            add(t, {"payer": {str(rng.choice(PAYER_NAMES))}}, {}, rmp=0)
        elif t == "push":
            d = str(rng.choice([x for x in ESTABLISHED if DRUGS[DRUG_IX[x]]["base"] >= 80]))
            add(t, {"drug": {d}}, {"mult": float(rng.uniform(1.2, 1.5)), "marginal_nc": float(rng.uniform(.3, .5))})
    # always include a copay / out-of-pocket accumulator shock on a commercial payer x drug
    comm_payers = ["Keystone Rx", "Northstar Health", "Meridian Care", "Summit Health Plan"]
    cp = "Keystone Rx" if seed % 2 == 1 else str(rng.choice(comm_payers))
    cd = "Dupixent" if seed % 2 == 1 else str(rng.choice(["Dupixent", "Humira", "Skyrizi", "Rinvoq"]))
    c_grp = PLANS[cp][0]["group"]
    add("copay_shock", {"drug": {cd}, "payer": {cp}},
        {"card_prob_new": 0.12, "oop_delta": 50.0, "target_group": c_grp},
        start=d2i(dt.date(2025, 3, 15)) if seed % 2 == 1 else None, rmp=0)
    # decoy: plausible scope, no effect
    dsc = [{"source": {str(rng.choice(SOURCES))}}, {"payer": {str(rng.choice(PAYER_NAMES))}}, {"region": {str(rng.choice(REGIONS))}}][rng.integers(3)]
    add("decoy", dsc, {})
    for i, e in enumerate(ev):
        e["eid"] = f"E{i + 1:02d}"
        err = int(np.clip(round(rng.normal(0, 10)), -30, 30))
        e["register_start"] = e["start"] + err
        e["register_end"] = e["end"] + (err if e["end"] < NDAYS - 1 else 0)
        e["expected_kpis"] = {
            "pa_policy": "conversion|pa_required_rate", "referral_mult": "referrals", "biosimilar": "referrals|new_entrant",
            "new_drug": "new_entrant", "intake_delay": "tat|conversion", "persistence": "lapse_rate|fills_per_active",
            "ds_mandate": "eq28_per_fill|fills_per_active", "push": "referrals" if rng.random() < 0.5 else "referrals|conversion",
            "copay_shock": "conversion|lapse_rate|net_oop",
            "decoy": str(rng.choice(["conversion|tat", "referrals", "lapse_rate"])), "prescriber_loss": "referrals"}[e["type"]]
    return ev


# ============================================================================ structure
def build_prescribers(rng, n=1200):
    rows = []
    for i in range(n):
        rows.append(dict(prescriber_npi=v1.luhn_npi("9" + f"{10000000 + i:08d}"), specialty=rng.choice(v1.SPECIALTIES, p=v1.SPEC_W),
                         region=rng.choice(REGIONS, p=v1.REGION_W), practice=f"Practice {i // 4 + 1:04d}", weight=float(rng.lognormal(0, 0.9))))
    df = pd.DataFrame(rows)
    taken = set()
    for name, reg, spec in GROUPS:
        ix = [i for i in df.index[df.specialty == spec] if i not in taken][:30]
        taken |= set(ix)
        df.loc[ix, ["region", "practice"]] = [reg, name]
        df.loc[ix, "weight"] *= 2.2
    return df


def _match(scope, drug=None, payer=None, region=None, source=None, practice=None):
    for k, v in (("drug", drug), ("payer", payer), ("region", region), ("source", source), ("practice", practice)):
        if k in scope and v not in scope[k]:
            return False
    return True


def build_candidates(seed, events):
    rng = np.random.default_rng(seed)
    pres = build_prescribers(rng)
    pw = pres.weight.values
    elig = {}
    for d in DRUGS:
        ix = np.where(np.isin(pres.specialty.values, d["spec"]))[0]
        elig[d["drug"]] = (ix, pw[ix] / pw[ix].sum())
    base_payer = np.array([p["share"] for p in PAYERS])
    mix = {}
    for r in REGIONS:
        w = base_payer.copy()
        if r == "South":
            w[v1.PAYER_IX["Harbor Medicaid"]] *= 1.6
        if r == "Northeast":
            w[v1.PAYER_IX["Northstar Health"]] *= 1.3
        mix[r] = w / w.sum()
    over = {}
    for d in DRUGS:
        o = 1.0
        for e in events:
            if e["type"] in ("push", "referral_mult") and e["params"]["mult"] > 1 and _match({k: v for k, v in e["scope"].items() if k == "drug"}, drug=d["drug"]):
                o *= e["params"]["mult"]
        over[d["drug"]] = o
    recs = []
    region_of = pres.region.values
    for di, d in enumerate(DRUGS):
        if d["base"] == 0:
            continue
        for day in range(NDAYS):
            if d["drug"] in NEW_DRUGS and day < Y2025:
                continue
            lam = d["base"] / 30.4 * v1.SEASON[MONTH[day]] * (1 + v1.TREND) ** (day / 365.25) * over[d["drug"]]
            n = rng.poisson(lam)
            if n == 0:
                continue
            ix, p = elig[d["drug"]]
            pr = rng.choice(ix, size=n, p=p)
            u = rng.random((n, 4))
            for j in range(n):
                reg = region_of[pr[j]]
                recs.append((day, di, pr[j], reg, int(rng.choice(len(PAYERS), p=mix[reg])), int(rng.choice(4, p=v1.SOURCE_W)), *u[j]))
    cand = pd.DataFrame(recs, columns=["day", "drug_i", "pres_i", "region", "payer_i", "source_i", "u_drug", "u_pres", "u_sw", "u_sw2"])
    cand = cand.sort_values(["day", "drug_i", "pres_i"], kind="stable").reset_index(drop=True)
    cand["cand_id"] = np.arange(len(cand))
    return pres, cand, over


# ============================================================================ simulation
def strength(e, day):
    if day < e["start"] or day > e["end"]:
        return 0.0
    return 1.0 if e["ramp"] <= 0 else min(1.0, (day - e["start"] + 1) / e["ramp"])


def simulate(pres, cand, over, events, enabled=None, seed=0, detail=True):
    E = [e for e in events if enabled is None or e["eid"] in enabled]
    ref_ev = [e for e in E if e["type"] in ("referral_mult", "prescriber_loss", "push")]
    bios = {e["params"]["originator"]: e for e in E if e["type"] == "biosimilar"}
    newd = {next(iter(e["scope"]["drug"])): e for e in E if e["type"] == "new_drug"}
    pa_ev = [e for e in E if e["type"] == "pa_policy"]
    delay_ev = [e for e in E if e["type"] == "intake_delay"]
    pers_ev = [e for e in E if e["type"] == "persistence"]
    ds_ev = [e for e in E if e["type"] == "ds_mandate"]
    copay_ev = [e for e in E if e["type"] == "copay_shock"]
    practice = pres.practice.values
    referrals, fills = [], []
    eq2025 = 0.0

    for row in cand.itertuples(index=False):
        day, di = row.day, row.drug_i
        d = DRUGS[di]
        payer = PAYERS[row.payer_i]["payer"]
        src = SOURCES[row.source_i]
        prac = practice[row.pres_i]
        # --- referral thinning
        if d["drug"] in NEW_DRUGS:
            e = newd.get(d["drug"])
            if e is None or row.u_drug >= strength(e, day):
                continue
            m = m_np = over[d["drug"]]
        else:
            m_np, push_mult, marginal_nc = 1.0, 1.0, None
            for e in ref_ev:
                if _match(e["scope"], d["drug"], payer, row.region, src, prac):
                    f = strength(e, day)
                    if f > 0:
                        mm = 1 + (e["params"]["mult"] - 1) * f
                        if e["type"] == "push":
                            push_mult *= mm
                            marginal_nc = e["params"]["marginal_nc"]
                        else:
                            m_np *= mm
            m = m_np * push_mult
        o = over[d["drug"]]
        if row.u_drug >= m / o:
            continue
        marginal = d["drug"] not in NEW_DRUGS and row.u_drug >= m_np / o and m > m_np
        if d["drug"] in bios:
            e = bios[d["drug"]]
            f = strength(e, day)
            if f > 0 and row.u_sw < e["params"]["switch"] * f:
                if row.u_sw2 < e["params"]["capture"]:
                    di = DRUG_IX[BIOSIM_OF[d["drug"]]]
                    d = DRUGS[di]
                else:
                    continue
        p = PAYERS[row.payer_i]
        rr = np.random.default_rng([seed, int(row.cand_id)])
        u = rr.random(16)
        uf = rr.random((120, 7))
        # --- Carrier / Account / Group & Patient Out-of-Pocket (OOP) / Copay Card ---
        plist = PLANS[payer]
        pshares = np.cumsum([pl["share"] for pl in plist])
        plan_ix = int(np.searchsorted(pshares, min(0.999999, row.u_pres)))
        plan = plist[plan_ix]
        gross_oop = float(plan["gross_oop"])
        card_prob = float(plan["card_prob"])
        max_assist = float(plan["max_assist"])
        oop_shock_f = 0.0
        for e in copay_ev:
            if _match(e["scope"], d["drug"], payer, row.region, src, prac):
                tgrp = e["params"].get("target_group")
                if tgrp is None or plan["group"] == tgrp:
                    f_cp = strength(e, day)
                    if f_cp > 0:
                        oop_shock_f = max(oop_shock_f, f_cp)
                        card_prob = card_prob * (1 - f_cp) + e["params"].get("card_prob_new", 0.12) * f_cp
                        max_assist = max(0.0, max_assist - e["params"].get("oop_delta", 50.0) * f_cp)
        has_card = (card_prob > 0) and (u[13] < card_prob)
        assist_amt = min(gross_oop - 10.0, max_assist) if has_card else (min(gross_oop - 15.0, max_assist) if (oop_shock_f > 0 and max_assist > 0 and u[13] < float(plan["card_prob"])) else 0.0)
        assist_amt = max(0.0, round(assist_amt, 2))
        net_oop = round(max(0.0, gross_oop - assist_amt), 2)
        base_net_oop = max(0.0, gross_oop - (min(gross_oop - 10.0, float(plan["max_assist"])) if float(plan["card_prob"]) > 0 else 0.0))
        oop_excess = max(0.0, net_oop - base_net_oop)

        jan = MONTH[day] == 1
        delay = 1 + (-math.log(u[0]) - math.log(u[1])) * 0.75 + (1 if jan else 0)
        for e in delay_ev:
            if _match(e["scope"], source=src):
                delay += e["params"]["extra"] * strength(e, day)
        delay = int(math.ceil(delay))
        pa_p, appr = p["pa"], p["appr"]
        for e in pa_ev:
            if _match(e["scope"], d["drug"], payer):
                f = strength(e, day)
                pa_p += (e["params"]["pa_new"] - pa_p) * f
                appr += (e["params"]["appr_new"] - appr) * f
        if jan:
            pa_p += .05
        nc_p, lim_p = .05, .03
        if marginal:
            nc_p, pa_p = marginal_nc, max(pa_p, .6)
        code = None
        if u[2] < nc_p:
            code = "70" if u[5] < .5 else "MR"
        elif u[3] < lim_p:
            code = "76"
        elif u[4] < pa_p:
            code = "75"
        extra, lost = 0, None
        if code == "75":
            pa_tat = int(math.ceil((-math.log(u[6]) - math.log(u[7])) * 1.5)) + (1 if jan else 0)
            if u[8] < appr:
                extra = pa_tat
            elif u[9] < .35 and u[10] < .5:
                extra = pa_tat + 7
            else:
                extra, lost = pa_tat, "PA denied"
        elif code in ("70", "MR"):
            extra, lost = (6, None) if u[6] < .25 else (0, "Not covered")
        elif code == "76":
            extra, lost = (2, None) if u[6] < .6 else (0, "Plan limits")
        tat = delay + extra
        if lost is None:
            if u[11] < min(.6, .04 + .025 * max(0, tat - 4)):
                lost = "Abandoned (TAT)"
            elif u[12] < min(.45, .05 + .0030 * oop_excess):
                lost = "Abandoned (Copay/OOP)" if oop_excess > 15 else "Patient declined"
        start = day + tat if lost is None else None
        if start is not None and start >= NDAYS:
            start, lost = None, "Pending at period end"
        if detail and day >= DATA_I:
            referrals.append((int(row.cand_id), day, di, row.pres_i, row.payer_i, plan_ix, row.source_i, row.region,
                              delay, code, tat, start, lost, gross_oop, int(has_card), assist_amt, net_oop))
        if start is None:
            continue
        dday, k = start, 0
        while dday < NDAYS and k < 120:
            ds, eqf = d["ds"], d["ds"] / 28
            if k >= 1 and d["ds"] <= 30:
                for e in ds_ev:
                    if _match(e["scope"], payer=payer) and uf[k, 6] < strength(e, dday):
                        ds, eqf = ds * 3, eqf * 3
            # recompute fill-level copay card and net OOP at fill date dday
            f_card_prob, f_max_assist, f_shock = float(plan["card_prob"]), float(plan["max_assist"]), 0.0
            for e in copay_ev:
                if _match(e["scope"], d["drug"], payer, row.region, src, prac):
                    tgrp = e["params"].get("target_group")
                    if tgrp is None or plan["group"] == tgrp:
                        fc = strength(e, dday)
                        if fc > 0:
                            f_shock = max(f_shock, fc)
                            f_card_prob = f_card_prob * (1 - fc) + e["params"].get("card_prob_new", 0.12) * fc
                            f_max_assist = max(0.0, f_max_assist - e["params"].get("oop_delta", 50.0) * fc)
            f_has_card = (f_card_prob > 0) and (u[13] < f_card_prob)
            f_assist = min(gross_oop - 10.0, f_max_assist) if f_has_card else (min(gross_oop - 15.0, f_max_assist) if (f_shock > 0 and f_max_assist > 0 and u[13] < float(plan["card_prob"])) else 0.0)
            f_assist = max(0.0, round(f_assist, 2))
            f_net_oop = round(max(0.0, gross_oop - f_assist), 2)
            f_oop_excess = max(0.0, f_net_oop - base_net_oop)
            if dday >= Y2025:
                eq2025 += eqf
            if detail and dday >= DATA_I:
                fills.append((int(row.cand_id), k, dday, di, row.payer_i, plan_ix, row.region, row.source_i, row.pres_i,
                              ds, gross_oop, int(f_has_card), f_assist, f_net_oop))
            hm, gap_mean = 1.0, 5.0
            for e in pers_ev:
                if _match(e["scope"], payer=payer):
                    f = strength(e, dday)
                    hm *= 1 + (e["params"]["haz_mult"] - 1) * f
                    gap_mean += (e["params"]["gap"] - 5.0) * f
            if f_oop_excess > 0:
                hm *= 1.0 + 0.0085 * f_oop_excess
            h28 = min(0.65, d["haz"] * hm)
            if uf[k, 0] < 1 - (1 - h28) ** (ds / 28):
                break
            nd = dday + ds + int(round(-math.log(uf[k, 1]) * gap_mean))
            if nd < NDAYS and YEAR[nd] > YEAR[dday] and uf[k, 2] < .35:
                if uf[k, 3] < .12:
                    break
                nd += 2 + int(uf[k, 5] * 6)
            dday = nd
            k += 1
    if not detail:
        return eq2025
    R = pd.DataFrame(referrals, columns=["referral_id", "day", "drug_i", "pres_i", "payer_i", "plan_i", "source_i", "region", "intake_delay_days",
                                         "first_reject_code", "tat_days", "start_day", "lost_reason", "gross_oop", "copay_card_used", "copay_assistance_amt", "net_oop"])
    F = pd.DataFrame(fills, columns=["patient_id", "fill_number", "day", "drug_i", "payer_i", "plan_i", "region", "source_i", "pres_i", "days_supply",
                                     "gross_oop", "copay_card_used", "copay_assistance_amt", "net_oop"])
    return R, F, eq2025


def label(R, F, pres):
    names = np.array([d["drug"] for d in DRUGS])
    pn, sn = np.array(PAYER_NAMES), np.array(SOURCES)
    npi, prac, spec = pres.prescriber_npi.values, pres.practice.values, pres.specialty.values
    def plan_cols(payer_i, plan_i):
        car = [PLANS[pn[p]][pl]["carrier"] for p, pl in zip(payer_i, plan_i)]
        acc = [PLANS[pn[p]][pl]["account"] for p, pl in zip(payer_i, plan_i)]
        grp = [PLANS[pn[p]][pl]["group"] for p, pl in zip(payer_i, plan_i)]
        return car, acc, grp
    rcar, racc, rgrp = plan_cols(R.payer_i.values, R.plan_i.values)
    fcar, facc, fgrp = plan_cols(F.payer_i.values, F.plan_i.values)
    R = R.assign(referral_date=D0 + pd.to_timedelta(R.day, "D"),
                 first_fill_date=D0 + pd.to_timedelta(R.start_day, "D"),
                 drug=names[R.drug_i], payer=pn[R.payer_i], carrier_id=rcar, account_id=racc, group_id=rgrp,
                 referral_source=sn[R.source_i],
                 prescriber_npi=npi[R.pres_i], practice=prac[R.pres_i], prescriber_specialty=spec[R.pres_i])
    R["converted"] = R.start_day.notna()
    F = F.assign(fill_date=D0 + pd.to_timedelta(F.day, "D"), drug=names[F.drug_i], payer=pn[F.payer_i],
                 carrier_id=fcar, account_id=facc, group_id=fgrp,
                 referral_source=sn[F.source_i], prescriber_npi=npi[F.pres_i], practice=prac[F.pres_i], eq28=F.days_supply / 28)
    R = R.drop(columns=["day", "start_day", "drug_i", "payer_i", "plan_i", "source_i", "pres_i"])
    F = F.drop(columns=["day", "drug_i", "payer_i", "plan_i", "source_i", "pres_i"])
    return R, F


def register_frame(events):
    rows = []
    for e in events:
        if not e["in_register"]:
            continue
        sc = e["scope"]
        rows.append(dict(event_id=e["eid"], expected_kpis=e["expected_kpis"], event_type_logged=e["type"] if e["type"] != "decoy" else
                         ["pa_policy", "referral_mult", "intake_delay"][int(e["start"]) % 3],
                         start_date=D0 + pd.Timedelta(days=e["register_start"]),
                         end_date=(D0 + pd.Timedelta(days=e["register_end"])) if e["end"] < NDAYS - 1 else pd.NaT,
                         scope_drug="|".join(sorted(sc["drug"])) if "drug" in sc else None,
                         scope_payer="|".join(sorted(sc["payer"])) if "payer" in sc else None,
                         scope_region="|".join(sorted(sc["region"])) if "region" in sc else None,
                         scope_source="|".join(sorted(sc["source"])) if "source" in sc else None))
    return pd.DataFrame(rows)


def make_world(seed, outdir=None, truth=True):
    events = sample_world(seed)
    pres, cand, over = build_candidates(seed, events)
    R, F, eq_all = simulate(pres, cand, over, events, seed=seed)
    R, F = label(R, F, pres)
    reg = register_frame(events)
    gt = []
    if truth:
        ids = [e["eid"] for e in events]
        none = simulate(pres, cand, over, events, enabled=set(), seed=seed, detail=False)
        for e in events:
            cf = simulate(pres, cand, over, events, enabled=set(ids) - {e["eid"]}, seed=seed, detail=False)
            gt.append(dict(event_id=e["eid"], type=e["type"], in_register=e["in_register"], true_start=D0 + pd.Timedelta(days=e["start"]),
                           ramp_days=e["ramp"], scope=str({k: sorted(v) for k, v in e["scope"].items()}), params=str(e["params"]),
                           true_impact=eq_all - cf))
        gt = pd.DataFrame(gt)
        gt.attrs["all_events_impact"] = eq_all - none
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        R.to_parquet(f"{outdir}/fact_referral.parquet", index=False)
        F.to_parquet(f"{outdir}/fact_fill.parquet", index=False)
        pres.drop(columns="weight").to_parquet(f"{outdir}/dim_prescriber.parquet", index=False)
        reg.to_csv(f"{outdir}/change_register.csv", index=False)
        if truth:
            gt.to_csv(f"{outdir}/ground_truth_events.csv", index=False)
            pd.Series({"all_events_impact": gt.attrs["all_events_impact"], "sum_single_impacts": gt.true_impact.sum(),
                       "volume_2025": eq_all}).to_csv(f"{outdir}/truth_summary.csv", header=["value"])
    return R, F, pres, reg, gt, events
