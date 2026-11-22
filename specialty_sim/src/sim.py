"""
Synthetic specialty pharmacy simulator with planted, known-cause events.

Every number is synthetic. Names, NPIs (prefix 9, never issued), BINs, NDCs and
payers are fictional. Drug names are used only as recognisable labels.

Design choices that matter for evaluation:
  * Referral candidates are generated once from a structural RNG (independent of
    which events are switched on) and then accepted/rejected by thinning. Each
    referral's journey uses its own RNG seeded by its candidate id. This gives
    common random numbers, so "event off" counterfactual runs differ from the
    actual run almost only through the event itself.
  * Ground truth per event = actual run minus the run with that event disabled.
"""
import datetime as dt
import math
import numpy as np
import pandas as pd

SEED = 20261121
SIM_START = dt.date(2019, 1, 1)      # 4-year warm-up so the active base is at steady state
SIM_END = dt.date(2025, 12, 31)
REPORT_START = dt.date(2023, 1, 1)   # data window: 2023-2025; comparison 2024 vs 2025


def d2i(d):
    return (d - SIM_START).days


NDAYS = d2i(SIM_END) + 1
DATES = np.array([SIM_START + dt.timedelta(i) for i in range(NDAYS)])
MONTH = np.array([d.month for d in DATES])
YEAR = np.array([d.year for d in DATES])
REPORT_I = d2i(REPORT_START)

SEASON = {1: 1.08, 2: .98, 3: 1.04, 4: 1.0, 5: 1.0, 6: .97, 7: .93, 8: .95, 9: 1.02, 10: 1.04, 11: .98, 12: .90}
TREND = 0.04  # underlying annual referral growth

# ----------------------------------------------------------------------------- reference data
DRUGS = [
    # name, ndc, base monthly referrals, days supply, units/fill, unit price USD, specialties, 28d hazard
    dict(drug="Dupixent", ndc="00024591420", base=230, ds=28, units=2, price=1850, spec=("Derm", "Allergy"), haz=.070),
    dict(drug="Humira", ndc="00074055402", base=130, ds=28, units=2, price=3450, spec=("Rheum", "Derm", "GI"), haz=.080),
    dict(drug="Adalimumab biosimilar", ndc="61314045402", base=95, ds=28, units=2, price=1200, spec=("Rheum", "Derm", "GI"), haz=.085),
    dict(drug="Stelara", ndc="57894006103", base=115, ds=56, units=1, price=13800, spec=("GI", "Derm"), haz=.060),
    dict(drug="Ustekinumab biosimilar", ndc="55513020001", base=0, ds=56, units=1, price=5900, spec=("GI", "Derm"), haz=.065),
    dict(drug="Skyrizi", ndc="00074210001", base=120, ds=84, units=1, price=20500, spec=("Derm", "GI"), haz=.055),
    dict(drug="Rinvoq", ndc="00074230630", base=100, ds=30, units=30, price=210, spec=("Rheum", "Derm", "GI"), haz=.080),
    dict(drug="Cosentyx", ndc="00078063941", base=80, ds=28, units=2, price=3400, spec=("Derm", "Rheum"), haz=.075),
    dict(drug="Taltz", ndc="00002144511", base=50, ds=28, units=1, price=6600, spec=("Derm", "Rheum"), haz=.075),
    dict(drug="Otezla", ndc="59572063106", base=90, ds=30, units=60, price=80, spec=("Derm", "Rheum"), haz=.100),
    dict(drug="Enbrel", ndc="58406003504", base=70, ds=28, units=4, price=1800, spec=("Rheum", "Derm"), haz=.080),
    dict(drug="Velarix (mock LDD)", ndc="99999010001", base=60, ds=28, units=1, price=9800, spec=("Derm",), haz=.070),
]
DRUG_IX = {d["drug"]: i for i, d in enumerate(DRUGS)}

PAYERS = [
    # name, segment, bin, pcn, share, P(PA required), P(PA approved)
    dict(payer="Keystone Rx", segment="Commercial", bin="990101", pcn="KSRX", share=.26, pa=.40, appr=.82),
    dict(payer="Northstar Health", segment="Commercial", bin="990202", pcn="NSHLTH", share=.22, pa=.35, appr=.84),
    dict(payer="Meridian Care", segment="Commercial", bin="990303", pcn="MERCARE", share=.15, pa=.30, appr=.85),
    dict(payer="Atlas Part D", segment="Medicare", bin="990404", pcn="ATLASD", share=.15, pa=.45, appr=.80),
    dict(payer="Harbor Medicaid", segment="Medicaid", bin="990505", pcn="HARBORMCD", share=.10, pa=.50, appr=.72),
    dict(payer="Summit Health Plan", segment="Commercial", bin="990606", pcn="SUMMIT", share=.12, pa=.30, appr=.85),
]
PAYER_IX = {p["payer"]: i for i, p in enumerate(PAYERS)}
REGIONS = ["Northeast", "South", "Midwest", "West"]
REGION_W = [.25, .30, .22, .23]
SOURCES = ["eRx", "Fax", "Portal", "Hub"]
SOURCE_W = [.55, .25, .12, .08]
SPECIALTIES = ["Derm", "Rheum", "GI", "Allergy"]
SPEC_W = [.40, .25, .20, .15]

# ----------------------------------------------------------------------------- planted events
# in_register=False -> the event happened but nobody logged it (tests register-completeness bias)
EVENTS = {
    "E01": dict(name="Keystone Rx step-therapy PA policy on Dupixent", start=dt.date(2025, 3, 1), end=SIM_END,
                drug="Dupixent", payer="Keystone Rx", region=None, source=None, factor="conversion", direction="-",
                in_register=True, category="Payer policy"),
    "E02": dict(name="Ustekinumab biosimilar launch / Stelara interchange", start=dt.date(2025, 2, 1), end=SIM_END,
                drug="Stelara|Ustekinumab biosimilar", payer=None, region=None, source=None, factor="referrals / new entrant",
                direction="- Stelara, + biosimilar", in_register=True, category="Market"),
    "E03": dict(name="Limited-distribution win: Velarix", start=dt.date(2025, 5, 1), end=SIM_END,
                drug="Velarix (mock LDD)", payer=None, region=None, source=None, factor="new entrant", direction="+",
                in_register=True, category="Access"),
    "E04": dict(name="South territory rep vacancies", start=dt.date(2025, 6, 1), end=SIM_END,
                drug=None, payer=None, region="South", source=None, factor="referrals", direction="-",
                in_register=True, category="Sales"),
    "E05": dict(name="Intake staffing shortage", start=dt.date(2025, 8, 1), end=dt.date(2025, 10, 31),
                drug=None, payer=None, region=None, source=None, factor="conversion", direction="-",
                in_register=True, category="Operations"),
    "E06": dict(name="Refill reminder touch cascade launch", start=dt.date(2025, 4, 1), end=SIM_END,
                drug=None, payer=None, region=None, source=None, factor="persistence / fill frequency", direction="+",
                in_register=True, category="Process"),
    "E07": dict(name="Northstar Health 90-day supply mandate", start=dt.date(2025, 7, 1), end=SIM_END,
                drug=None, payer="Northstar Health", region=None, source=None, factor="units per fill + / fills per patient -",
                direction="mixed", in_register=True, category="Payer policy"),
    "E08": dict(name="Skyrizi field marketing push", start=dt.date(2025, 4, 1), end=SIM_END,
                drug="Skyrizi", payer=None, region=None, source=None, factor="referrals + / conversion -",
                direction="linked", in_register=True, category="Sales"),
    "E09": dict(name="Fax OCR intake system go-live", start=dt.date(2025, 5, 12), end=SIM_END,
                drug=None, payer=None, region=None, source="Fax", factor="none (decoy)", direction="0",
                in_register=True, category="Operations"),
    "E10": dict(name="Lakeside Dermatology Associates moved to competitor", start=dt.date(2025, 9, 15), end=SIM_END,
                drug=None, payer=None, region="Midwest", source=None, factor="referrals", direction="-",
                in_register=False, category="Prescriber"),
}
EV_START = {k: d2i(v["start"]) for k, v in EVENTS.items()}
EV_END = {k: d2i(v["end"]) for k, v in EVENTS.items()}


# ----------------------------------------------------------------------------- structural data (fixed across runs)
FIRST = ["Alex", "Jordan", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn", "Drew", "Robin",
         "Cameron", "Dana", "Elliot", "Frances", "Gale", "Harper", "Jesse", "Kerry", "Logan", "Marion", "Noel", "Pat"]
LAST = ["Ashford", "Brennan", "Castell", "Delaney", "Everly", "Fenwick", "Garrow", "Hollis", "Ingram", "Jessop",
        "Kearney", "Lindqvist", "Marlow", "Norland", "Okafor", "Pryce", "Quill", "Rowan", "Sutter", "Talbot",
        "Underwood", "Vance", "Whitlock", "Yardley"]


def luhn_npi(base9):
    """NPI check digit (Luhn over '80840' + 9 digits)."""
    s = "80840" + base9
    total = 0
    for i, ch in enumerate(reversed(s)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return base9 + str((10 - total % 10) % 10)


def build_prescribers(rng, n=1200):
    rows = []
    for i in range(n):
        spec = rng.choice(SPECIALTIES, p=SPEC_W)
        region = rng.choice(REGIONS, p=REGION_W)
        rows.append(dict(prescriber_npi=luhn_npi("9" + f"{10000000 + i:08d}"), specialty=spec, region=region,
                         practice=f"Practice {i // 4 + 1:04d}", weight=float(rng.lognormal(0, 0.9))))
    df = pd.DataFrame(rows)
    # Lakeside Dermatology Associates: 35 high-volume Midwest derm prescribers
    lake = df.index[(df.specialty == "Derm")][:35]
    df.loc[lake, ["region", "practice"]] = ["Midwest", "Lakeside Dermatology Associates"]
    df.loc[lake, "weight"] = df.loc[lake, "weight"] * 2.2
    df["first_name"] = [FIRST[i % len(FIRST)] for i in range(n)]
    df["last_name"] = [LAST[(i * 7) % len(LAST)] for i in range(n)]
    return df


def build_candidates(seed=SEED):
    """All potential referrals with their pre-drawn uniforms. Independent of event toggles."""
    rng = np.random.default_rng(seed)
    pres = build_prescribers(rng)
    pres_spec = pres.specialty.values
    pres_w = pres.weight.values
    elig = {}
    for d in DRUGS:
        ix = np.where(np.isin(pres_spec, d["spec"]))[0]
        elig[d["drug"]] = (ix, pres_w[ix] / pres_w[ix].sum())

    region_of = pres.region.values
    base_payer = np.array([p["share"] for p in PAYERS])
    payer_mix = {}
    for r in REGIONS:
        w = base_payer.copy()
        if r == "South":
            w[PAYER_IX["Harbor Medicaid"]] *= 1.6
        if r == "Northeast":
            w[PAYER_IX["Northstar Health"]] *= 1.3
        payer_mix[r] = w / w.sum()

    recs = []
    for di, d in enumerate(DRUGS):
        name = d["drug"]
        if name == "Ustekinumab biosimilar":
            continue  # its referrals come from the Stelara candidate stream
        over = 1.25 if name == "Skyrizi" else 1.0
        base = d["base"]
        for day in range(NDAYS):
            if name.startswith("Velarix") and day < d2i(dt.date(2025, 1, 1)):
                continue
            yrs = (day / 365.25)
            lam = base / 30.4 * SEASON[MONTH[day]] * (1 + TREND) ** yrs * over
            n = rng.poisson(lam)
            if n == 0:
                continue
            ix, p = elig[name]
            pr = rng.choice(ix, size=n, p=p)
            u = rng.random((n, 4))
            for j in range(n):
                reg = region_of[pr[j]]
                recs.append((day, di, pr[j], reg,
                             int(rng.choice(len(PAYERS), p=payer_mix[reg])),
                             int(rng.choice(4, p=SOURCE_W)),
                             u[j, 0], u[j, 1], u[j, 2], u[j, 3]))
    cand = pd.DataFrame(recs, columns=["day", "drug_i", "pres_i", "region", "payer_i", "source_i", "u_drug", "u_pres", "u_sw", "u_sw2"])
    cand = cand.sort_values(["day", "drug_i", "pres_i"], kind="stable").reset_index(drop=True)
    cand["cand_id"] = np.arange(len(cand))
    return pres, cand


# ----------------------------------------------------------------------------- simulation
def _on(enabled, eid, day):
    return eid in enabled and EV_START[eid] <= day <= EV_END[eid]


def simulate(pres, cand, enabled=frozenset(EVENTS), seed=SEED, detail=True):
    lake = set(np.where(pres.practice.values == "Lakeside Dermatology Associates")[0])
    sky_i, stel_i, bios_i, vel_i = DRUG_IX["Skyrizi"], DRUG_IX["Stelara"], DRUG_IX["Ustekinumab biosimilar"], DRUG_IX["Velarix (mock LDD)"]
    vel_start = EV_START["E03"]

    referrals, claims, pas, fills = [], [], [], []
    ops = np.zeros((NDAYS, 3))  # referrals, sum intake delay, fte
    ops[:, 2] = 24
    if "E05" in enabled:
        ops[EV_START["E05"]:EV_END["E05"] + 1, 2] = 16

    for row in cand.itertuples(index=False):
        day, di = row.day, row.drug_i
        marginal = False
        # --- thinning: drug-level
        if di == sky_i:
            if row.u_drug < 0.8:
                pass
            elif _on(enabled, "E08", day):
                marginal = True
            else:
                continue
        elif di == vel_i:
            if not _on(enabled, "E03", day):
                continue
            ramp = min(1.0, ((YEAR[day] - 2025) * 12 + MONTH[day] - 5 + 1) / 3)
            if row.u_drug >= ramp:
                continue
        elif di == stel_i and _on(enabled, "E02", day):
            if row.u_sw < 0.55:          # 55% of Stelara demand switches away
                if row.u_sw2 < 0.818:    # ...most of it to the biosimilar we dispense
                    di = bios_i
                else:
                    continue             # lost to competitor
        # --- thinning: prescriber-level
        if row.region == "South" and _on(enabled, "E04", day) and row.u_pres >= 0.78:
            continue
        if row.pres_i in lake and _on(enabled, "E10", day):
            continue

        d = DRUGS[di]
        p = PAYERS[row.payer_i]
        rr = np.random.default_rng([seed, int(row.cand_id)])
        u = rr.random(16)
        uf = rr.random((120, 6))
        jan = MONTH[day] == 1

        # --- intake
        delay = 1 + (-math.log(u[0]) - math.log(u[1])) * 0.75 + (1 if jan else 0)
        if _on(enabled, "E05", day):
            delay += 5
        delay = int(math.ceil(delay))
        ops[day, 0] += 1
        ops[day, 1] += delay

        pa_p, appr = p["pa"], p["appr"]
        if di == DRUG_IX["Dupixent"] and p["payer"] == "Keystone Rx" and _on(enabled, "E01", day):
            pa_p, appr = .85, .58
        if jan:
            pa_p += .05
        nc_p, lim_p = .05, .03
        if marginal:
            nc_p, pa_p = .45, max(pa_p, .60)

        check_day = day + delay
        code = None
        if u[2] < nc_p:
            code = "70" if u[5] < .5 else "MR"
        elif u[3] < lim_p:
            code = "76"
        elif u[4] < pa_p:
            code = "75"
        extra, lost_reason, pa_out = 0, None, None
        if code == "75":
            pa_tat = int(math.ceil((-math.log(u[6]) - math.log(u[7])) * 1.5)) + (1 if jan else 0)
            if u[8] < appr:
                pa_out = "Approved"
                extra = pa_tat
            elif u[9] < .35 and u[10] < .5:
                pa_out = "Approved on appeal"
                extra = pa_tat + 7
            else:
                pa_out = "Denied"
                extra = pa_tat
                lost_reason = "PA denied"
        elif code in ("70", "MR"):
            if u[6] < .25:
                extra = 6
            else:
                lost_reason = "Not covered"
        elif code == "76":
            if u[6] < .6:
                extra = 2
            else:
                lost_reason = "Plan limits"
        tat = delay + extra
        if lost_reason is None:
            if u[11] < min(.6, .04 + .025 * max(0, tat - 4)):
                lost_reason = "Abandoned (TAT)"
            elif u[12] < .05:
                lost_reason = "Patient declined"
        converted = lost_reason is None
        start = day + tat if converted else None
        if start is not None and start > NDAYS - 1:
            converted, start, lost_reason = False, None, "Pending at period end"

        ref_id = int(row.cand_id)
        pat_id = ref_id  # one patient per referral in this sim
        if not detail and day < REPORT_I and not converted:
            continue
        referrals.append((ref_id, pat_id, day, di, row.pres_i, row.payer_i, row.source_i, row.region, marginal,
                          delay, code, pa_out, tat if converted or lost_reason in ("Abandoned (TAT)", "Patient declined") else None,
                          converted, lost_reason, start))
        rxn = 100000000 + ref_id
        if code is not None:
            claims.append((ref_id, pat_id, rxn, 0, check_day, di, row.payer_i, "B1", "R", code, "intake",
                           d["ds"], d["units"], row.pres_i))
        if code == "75":
            pas.append((ref_id, pat_id, "initial", check_day, check_day + (extra if pa_out != "Approved on appeal" else extra - 7),
                        pa_out, "Step therapy not met" if pa_out == "Denied" else None, di, row.payer_i))
        if code is None and not converted and lost_reason != "Pending at period end" and check_day < NDAYS:
            claims.append((ref_id, pat_id, rxn, 0, check_day, di, row.payer_i, "B1", "P", None, "intake", d["ds"], d["units"], row.pres_i))
            claims.append((ref_id, pat_id, rxn, 0, min(check_day + 2, NDAYS - 1), di, row.payer_i, "B2", "A", None, "reversal", d["ds"], d["units"], row.pres_i))
        if not converted:
            continue

        # --- dispensing and persistence
        dday, k = start, 0
        disc_reason = "Active at period end"
        while dday < NDAYS and k < 120:
            ds, units = d["ds"], d["units"]
            if k >= 1 and p["payer"] == "Northstar Health" and _on(enabled, "E07", dday) and ds <= 30:
                ds, units = ds * 3, units * 3
            if k >= 1 and uf[k, 4] < .03 and dday - 2 >= 0:
                claims.append((ref_id, pat_id, rxn, k, dday - 2, di, row.payer_i, "B1", "R", "79", "refill too soon", ds, units, row.pres_i))
            claims.append((ref_id, pat_id, rxn, k, dday, di, row.payer_i, "B1", "P", None, "intake" if k == 0 else "refill", ds, units, row.pres_i))
            fills.append((ref_id, pat_id, k, dday, di, row.payer_i, row.region, row.source_i, row.pres_i, ds, units))
            h28 = d["haz"] * (0.7 if _on(enabled, "E06", dday) else 1.0)
            if uf[k, 0] < 1 - (1 - h28) ** (ds / 28):
                disc_reason = "Discontinued"
                break
            gap = -math.log(uf[k, 1]) * (2 if _on(enabled, "E06", dday) else 5)
            nd = dday + ds + int(round(gap))
            if nd < NDAYS and YEAR[nd] > YEAR[dday] and uf[k, 2] < .35:   # annual re-authorisation
                claims.append((ref_id, pat_id, rxn, k + 1, nd, di, row.payer_i, "B1", "R", "75", "reauth", ds, units, row.pres_i))
                if uf[k, 3] < .12:
                    pas.append((ref_id, pat_id, "reauth", nd, nd + 3, "Denied", "Continuation criteria not met", di, row.payer_i))
                    disc_reason = "Reauth denied"
                    break
                wait = 2 + int(uf[k, 5] * 6)
                pas.append((ref_id, pat_id, "reauth", nd, nd + wait, "Approved", None, di, row.payer_i))
                nd += wait
            dday = nd
            k += 1

    R = pd.DataFrame(referrals, columns=["referral_id", "patient_id", "day", "drug_i", "pres_i", "payer_i", "source_i", "region",
                                         "marginal_flag", "intake_delay_days", "first_reject_code", "pa_outcome", "tat_days",
                                         "converted", "lost_reason", "start_day"])
    F = pd.DataFrame(fills, columns=["referral_id", "patient_id", "fill_number", "day", "drug_i", "payer_i", "region", "source_i",
                                     "pres_i", "days_supply", "units"])
    C = pd.DataFrame(claims, columns=["referral_id", "patient_id", "rx_number", "fill_number", "day", "drug_i", "payer_i", "txn_code",
                                      "status", "reject_code", "claim_context", "days_supply", "units", "pres_i"])
    P = pd.DataFrame(pas, columns=["referral_id", "patient_id", "pa_context", "request_day", "decision_day", "pa_outcome",
                                   "denial_reason", "drug_i", "payer_i"])
    O = pd.DataFrame({"day": np.arange(NDAYS), "referrals": ops[:, 0], "avg_intake_delay": np.where(ops[:, 0] > 0, ops[:, 1] / np.maximum(ops[:, 0], 1), np.nan),
                      "intake_fte": ops[:, 2]})
    return dict(referrals=R, fills=F, claims=C, pa=P, ops=O)


def label(df, pres):
    """Replace integer keys with readable labels and dates."""
    out = df.copy()
    for c in [c for c in out.columns if c.endswith("day")]:
        name = "date" if c == "day" else c.replace("_day", "_date")
        out[name] = pd.to_datetime(pd.Series(out[c]).map(lambda x: DATES[int(x)] if pd.notna(x) and int(x) < NDAYS else pd.NaT))
        out = out.drop(columns=c)
    if "drug_i" in out:
        out["drug"] = out.drug_i.map(lambda i: DRUGS[i]["drug"])
        out["ndc"] = out.drug_i.map(lambda i: DRUGS[i]["ndc"])
        out = out.drop(columns="drug_i")
    if "payer_i" in out:
        out["payer"] = out.payer_i.map(lambda i: PAYERS[i]["payer"])
        out = out.drop(columns="payer_i")
    if "source_i" in out:
        out["referral_source"] = out.source_i.map(lambda i: SOURCES[i])
        out = out.drop(columns="source_i")
    if "pres_i" in out:
        out["prescriber_npi"] = out.pres_i.map(lambda i: pres.prescriber_npi.iat[i])
        out = out.drop(columns="pres_i")
    return out
