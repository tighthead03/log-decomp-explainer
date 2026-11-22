"""
Granular Pocket & Mechanism Drill-Down (No Change Log Required).

Answers "WHERE did we lose/gain volume, and WHY?" directly from transaction tables:
  1. Top-down LMDI decomposition across Region x Drug x Payer pockets (2024 vs 2025):
       Delta eq28 = Delta Referrals + Delta Conversion + Delta Retention/Refills + Delta Days Supply
  2. Automatic transaction drill-down inside every material pocket:
       - Prescriber NPI churn: lost NPIs (and their prior avg referrals/NPI) vs gained NPIs (and their new avg referrals/NPI) vs retained NPIs
       - Carrier / Account / Group & Reject Code 75 / 70 / 76 / MR breakdown: which exact plan group spiked on Reject 75 (PA Required) or PA denials
       - Copay Card & Net Out-of-Pocket (OOP) impact: change in % of patients with secondary copay card assistance, $/fill net OOP increase, first-fill copay abandonment, and mid-year refill drop-off
       - Turnaround time (TAT): days added from referral to first fill and resulting TAT abandonment
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.join(HERE, "specialty_sim", "v2")
sys.path.insert(0, V2)
import sim2  # noqa: E402


def log_mean(a, b):
    a, b = float(a), float(b)
    if a <= 0 or b <= 0:
        return (a + b) / 2.0
    if abs(a - b) < 1e-9:
        return a
    return (b - a) / (np.log(b) - np.log(a))


def ensure_granular_columns(ref, fill, seed=1065):
    """If running on a legacy world without carrier/account/group or copay columns, enrich deterministically."""
    ref, fill = ref.copy(), fill.copy()
    if "group_id" in ref.columns and "net_oop" in ref.columns and "net_oop" in fill.columns:
        return ref, fill

    rng = np.random.default_rng(seed)
    # Assign each referral / patient to a carrier/account/group under their payer
    pats = pd.concat([ref[["payer", "referral_id"]].rename(columns={"referral_id": "pid"}),
                      fill[["payer", "patient_id"]].rename(columns={"patient_id": "pid"})]).drop_duplicates("pid")
    u_plan = rng.random(len(pats))
    u_card = rng.random(len(pats))
    car_map, acc_map, grp_map, goop_map, cprob_map, maxa_map, ucard_map = {}, {}, {}, {}, {}, {}, {}
    for (pid, payer), up, uc in zip(pats[["pid", "payer"]].itertuples(index=False), u_plan, u_card):
        plist = sim2.PLANS.get(payer, [dict(carrier="CAR-00", account="ACC-00", group="GRP-000", share=1.0, gross_oop=75.0, card_prob=0.75, max_assist=60.0)])
        pshares = np.cumsum([pl["share"] for pl in plist])
        ix = int(np.searchsorted(pshares, min(0.999999, up)))
        pl = plist[ix]
        car_map[pid], acc_map[pid], grp_map[pid] = pl["carrier"], pl["account"], pl["group"]
        goop_map[pid], cprob_map[pid], maxa_map[pid], ucard_map[pid] = float(pl["gross_oop"]), float(pl["card_prob"]), float(pl["max_assist"]), float(uc)

    for df, idcol, dcol in ((ref, "referral_id", "referral_date"), (fill, "patient_id", "fill_date")):
        ids = df[idcol].values
        df["carrier_id"] = [car_map[i] for i in ids]
        df["account_id"] = [acc_map[i] for i in ids]
        df["group_id"] = [grp_map[i] for i in ids]
        goop = np.array([goop_map[i] for i in ids])
        cprob = np.array([cprob_map[i] for i in ids])
        maxa = np.array([maxa_map[i] for i in ids])
        uc = np.array([ucard_map[i] for i in ids])
        is_2025_shock = (df[dcol].dt.year == 2025).values & (df["group_id"].isin(["GRP-ABC1234", "GRP-NSH101"])).values & (df["drug"].isin(["Dupixent", "Stelara", "Humira"])).values
        eff_cprob = np.where(is_2025_shock, 0.14, cprob)
        eff_maxa = np.where(is_2025_shock, np.maximum(0.0, maxa - 50.0), maxa)
        has_card = (eff_cprob > 0) & (uc < eff_cprob)
        assist = np.where(has_card, np.minimum(goop - 10.0, eff_maxa),
                          np.where(is_2025_shock & (eff_maxa > 0) & (uc < cprob), np.minimum(goop - 15.0, eff_maxa), 0.0))
        assist = np.maximum(0.0, np.round(assist, 2))
        df["gross_oop"] = goop
        df["copay_card_used"] = has_card.astype(int)
        df["copay_assistance_amt"] = assist
        df["net_oop"] = np.round(np.maximum(0.0, goop - assist), 2)
    return ref, fill


def analyze_pockets(wdir, top_n=8):
    """
    Decompose 2024 -> 2025 volume change across (region, drug, payer) pockets and drill into
    prescriber NPIs, carrier/account/group Reject 75 rates, and copay card / net OOP changes.
    """
    ref = pd.read_parquet(os.path.join(wdir, "fact_referral.parquet"))
    fill = pd.read_parquet(os.path.join(wdir, "fact_fill.parquet"))
    ref, fill = ensure_granular_columns(ref, fill)

    ref["year"] = ref.referral_date.dt.year
    fill["year"] = fill.fill_date.dt.year
    r24, r25 = ref[ref.year == 2024], ref[ref.year == 2025]
    f24, f25 = fill[fill.year == 2024], fill[fill.year == 2025]

    keys = ["region", "drug", "payer"]
    # Funnel metrics per pocket in 2024 and 2025
    def funnel(r_df, f_df):
        ra = r_df.groupby(keys).agg(referrals=("referral_id", "size"), starts=("converted", "sum")).reset_index()
        fa = f_df.groupby(keys).agg(fills=("days_supply", "size"), eq28=("eq28", "sum"),
                                    active_pts=("patient_id", "nunique")).reset_index()
        m = pd.merge(ra, fa, on=keys, how="outer").fillna(0.0)
        return m

    m24, m25 = funnel(r24, f24), funnel(r25, f25)
    M = pd.merge(m24, m25, on=keys, how="outer", suffixes=("_24", "_25")).fillna(0.0)
    M["delta_eq28"] = M.eq28_25 - M.eq28_24

    # 4-factor LMDI per pocket: eq28 = Referrals * (Starts/Referrals) * (Fills/Starts) * (eq28/Fills)
    d_ref, d_conv, d_pers, d_ds = [], [], [], []
    for row in M.itertuples(index=False):
        v0, v1 = row.eq28_24, row.eq28_25
        if v0 == 0 or v1 == 0 or row.referrals_24 == 0 or row.referrals_25 == 0 or row.starts_24 == 0 or row.starts_25 == 0 or row.fills_24 == 0 or row.fills_25 == 0:
            # New or lost cell: assign proportionally or to referrals/new entrant
            d_ref.append(v1 - v0); d_conv.append(0.0); d_pers.append(0.0); d_ds.append(0.0)
            continue
        L = log_mean(v0, v1)
        c0, c1 = row.starts_24 / row.referrals_24, row.starts_25 / row.referrals_25
        p0, p1 = row.fills_24 / row.starts_24, row.fills_25 / row.starts_25
        s0, s1 = row.eq28_24 / row.fills_24, row.eq28_25 / row.fills_25
        dr = L * np.log(row.referrals_25 / row.referrals_24)
        dc = L * np.log(max(c1, 1e-6) / max(c0, 1e-6))
        dp = L * np.log(max(p1, 1e-6) / max(p0, 1e-6))
        ds = L * np.log(max(s1, 1e-6) / max(s0, 1e-6))
        tot = dr + dc + dp + ds
        scale = (v1 - v0) / tot if abs(tot) > 1e-9 else 1.0
        d_ref.append(dr * scale); d_conv.append(dc * scale); d_pers.append(dp * scale); d_ds.append(ds * scale)

    M["lmdi_referrals"] = d_ref
    M["lmdi_conversion"] = d_conv
    M["lmdi_persistence"] = d_pers
    M["lmdi_days_supply"] = d_ds

    # Pick top losses, top gains, plus any pocket with a major Reject 75 spike or Copay/OOP shock
    oop_ref = r25.groupby(keys).net_oop.mean() - r24.groupby(keys).net_oop.mean()
    r75_ref = (r25.assign(r75=r25.first_reject_code == "75").groupby(keys).r75.mean() -
               r24.assign(r75=r24.first_reject_code == "75").groupby(keys).r75.mean())
    M = M.merge(oop_ref.rename("d_oop").reset_index(), on=keys, how="left").merge(
        r75_ref.rename("d_r75").reset_index(), on=keys, how="left").fillna({"d_oop": 0.0, "d_r75": 0.0})
    losses = M.sort_values("delta_eq28").head(top_n // 2)
    gains = M.sort_values("delta_eq28", ascending=False).head(max(2, top_n // 3))
    shocks = M[(M.d_oop >= 10.0) | (M.d_r75 >= 0.12)].sort_values("delta_eq28").head(2)
    picks = pd.concat([losses, shocks, gains]).drop_duplicates(subset=keys).sort_values("delta_eq28")

    drills = []
    for row in picks.itertuples(index=False):
        reg, drg, pay = row.region, row.drug, row.payer
        sr24 = r24[(r24.region == reg) & (r24.drug == drg) & (r24.payer == pay)]
        sr25 = r25[(r25.region == reg) & (r25.drug == drg) & (r25.payer == pay)]
        sf24 = f24[(f24.region == reg) & (f24.drug == drg) & (f24.payer == pay)]
        sf25 = f25[(f25.region == reg) & (f25.drug == drg) & (f25.payer == pay)]

        # 1. Prescriber NPI cohort math
        npi24 = sr24.groupby("prescriber_npi").size()
        npi25 = sr25.groupby("prescriber_npi").size()
        set24, set25 = set(npi24.index), set(npi25.index)
        lost_npis = set24 - set25
        gained_npis = set25 - set24
        kept_npis = set24 & set25
        lost_cnt = len(lost_npis)
        gained_cnt = len(gained_npis)
        kept_cnt = len(kept_npis)
        lost_rx = int(npi24.loc[list(lost_npis)].sum()) if lost_cnt else 0
        gained_rx = int(npi25.loc[list(gained_npis)].sum()) if gained_cnt else 0
        kept_rx24 = int(npi24.loc[list(kept_npis)].sum()) if kept_cnt else 0
        kept_rx25 = int(npi25.loc[list(kept_npis)].sum()) if kept_cnt else 0
        lost_avg = lost_rx / lost_cnt if lost_cnt else 0.0
        gained_avg = gained_rx / gained_cnt if gained_cnt else 0.0

        # Top practice movement inside this slice
        p24 = sr24.groupby("practice").size()
        p25 = sr25.groupby("practice").size()
        pdiff = (p25.reindex(p24.index.union(p25.index), fill_value=0) - p24.reindex(p24.index.union(p25.index), fill_value=0)).sort_values()
        top_prac_loss = (pdiff.index[0], int(pdiff.iloc[0])) if len(pdiff) and pdiff.iloc[0] < -3 else None

        # 2. Carrier / Account / Group & Reject Code 75 drill-down
        def plan_stats(sr):
            if len(sr) == 0:
                return pd.DataFrame()
            return sr.groupby(["carrier_id", "account_id", "group_id"]).agg(
                refs=("referral_id", "size"),
                conv=("converted", "mean"),
                rej75=("first_reject_code", lambda s: (s == "75").mean()),
                rej70_mr=("first_reject_code", lambda s: s.isin(["70", "MR"]).mean()),
                tat=("tat_days", "mean"),
                card_pct=("copay_card_used", "mean"),
                net_oop=("net_oop", "mean"),
            ).reset_index()

        pl24, pl25 = plan_stats(sr24), plan_stats(sr25)
        lead_plan_text = ""
        rej75_24 = (sr24.first_reject_code == "75").mean() if len(sr24) else 0.0
        rej75_25 = (sr25.first_reject_code == "75").mean() if len(sr25) else 0.0
        conv24 = sr24.converted.mean() if len(sr24) else 0.0
        conv25 = sr25.converted.mean() if len(sr25) else 0.0
        tat24 = sr24.tat_days.mean() if len(sr24) else 0.0
        tat25 = sr25.tat_days.mean() if len(sr25) else 0.0

        if len(pl24) and len(pl25):
            pm = pd.merge(pl24, pl25, on=["carrier_id", "account_id", "group_id"], suffixes=("_24", "_25"))
            if len(pm):
                pm["d_rej75"] = pm.rej75_25 - pm.rej75_24
                pm["d_oop"] = pm.net_oop_25 - pm.net_oop_24
                pm["d_conv"] = pm.conv_25 - pm.conv_24
                worst = pm.sort_values("d_conv").iloc[0]
                lead_plan_text = (f"{worst.carrier_id} / {worst.account_id} / {worst.group_id} "
                                  f"(Reject 75: {worst.rej75_24:.0%}→{worst.rej75_25:.0%}, "
                                  f"Conversion: {worst.conv_24:.0%}→{worst.conv_25:.0%}, "
                                  f"Copay card: {worst.card_pct_24:.0%}→{worst.card_pct_25:.0%}, "
                                  f"Net OOP: ${worst.net_oop_24:.0f}→${worst.net_oop_25:.0f}/fill)")

        # 3. Copay Card & Net OOP across fills and referrals
        card24 = sf24.copay_card_used.mean() if len(sf24) else (sr24.copay_card_used.mean() if len(sr24) else 0.0)
        card25 = sf25.copay_card_used.mean() if len(sf25) else (sr25.copay_card_used.mean() if len(sr25) else 0.0)
        oop24 = sf24.net_oop.mean() if len(sf24) else (sr24.net_oop.mean() if len(sr24) else 0.0)
        oop25 = sf25.net_oop.mean() if len(sf25) else (sr25.net_oop.mean() if len(sr25) else 0.0)
        ds24 = sf24.days_supply.mean() if len(sf24) else 0.0
        ds25 = sf25.days_supply.mean() if len(sf25) else 0.0

        # Build plain-English bullet narrative for this pocket
        bullets = []
        if abs(row.lmdi_referrals) >= 15 or lost_cnt > 0 or gained_cnt > 0:
            prac_note = f" (led by {top_prac_loss[0]}: {top_prac_loss[1]:+d} referrals)" if top_prac_loss else ""
            bullets.append(
                f"<b>Prescriber churn ({row.lmdi_referrals:+,.0f} eq28 from referrals):</b> "
                f"Lost <b>{lost_cnt}</b> prescribing NPIs ({-lost_rx:+d} referrals, avg <b>{lost_avg:.1f}</b> Rx/NPI) and "
                f"gained <b>{gained_cnt}</b> NPIs (+{gained_rx} referrals, avg <b>{gained_avg:.1f}</b> Rx/NPI); "
                f"{kept_cnt} retained NPIs moved from {kept_rx24} to {kept_rx25} referrals ({kept_rx25 - kept_rx24:+d}){prac_note}."
            )
        if abs(row.lmdi_conversion) >= 10 or abs(rej75_25 - rej75_24) >= 0.03 or abs(tat25 - tat24) >= 0.5:
            bullets.append(
                f"<b>Reject 75 (PA Required) & intake conversion ({row.lmdi_conversion:+,.0f} eq28):</b> "
                f"First-fill conversion moved from <b>{conv24:.0%}</b> to <b>{conv25:.0%}</b> "
                f"(Reject 75 rate <b>{rej75_24:.0%}→{rej75_25:.0%}</b>, avg TAT <b>{tat24:.1f}→{tat25:.1f} days</b>)."
                + (f" Plan breakdown: <code>{lead_plan_text}</code>." if lead_plan_text else "")
            )
        if abs(oop25 - oop24) >= 2.0 or abs(card25 - card24) >= 0.04 or abs(row.lmdi_persistence) >= 15:
            bullets.append(
                f"<b>Copay card access, Net OOP & retention ({row.lmdi_persistence:+,.0f} eq28 from persistence):</b> "
                f"Copay card coverage moved from <b>{card24:.0%}</b> to <b>{card25:.0%}</b> of fills and patient net OOP moved by "
                f"<b>${oop25 - oop24:+.0f}/fill</b> (${oop24:.0f}→${oop25:.0f}/fill); "
                f"fills per new start changed from <b>{row.fills_24 / max(row.starts_24, 1):.2f}</b> to <b>{row.fills_25 / max(row.starts_25, 1):.2f}</b>."
            )
        if abs(row.lmdi_days_supply) >= 15 or abs(ds25 - ds24) >= 2.0:
            bullets.append(
                f"<b>Days supply shift ({row.lmdi_days_supply:+,.0f} eq28):</b> "
                f"Average days supply per fill moved from <b>{ds24:.1f}</b> to <b>{ds25:.1f} days</b>."
            )

        drills.append(dict(
            region=reg, drug=drg, payer=pay,
            eq28_24=round(row.eq28_24, 1), eq28_25=round(row.eq28_25, 1), delta_eq28=round(row.delta_eq28, 1),
            lmdi_referrals=round(row.lmdi_referrals, 1), lmdi_conversion=round(row.lmdi_conversion, 1),
            lmdi_persistence=round(row.lmdi_persistence, 1), lmdi_days_supply=round(row.lmdi_days_supply, 1),
            lost_npis=lost_cnt, lost_npi_avg_rx=round(lost_avg, 2),
            gained_npis=gained_cnt, gained_npi_avg_rx=round(gained_avg, 2),
            retained_npis=kept_cnt, retained_rx_delta=kept_rx25 - kept_rx24,
            rej75_24=round(rej75_24, 3), rej75_25=round(rej75_25, 3),
            conv_24=round(conv24, 3), conv_25=round(conv25, 3),
            tat_24=round(tat24, 1), tat_25=round(tat25, 1),
            copay_card_24=round(card24, 3), copay_card_25=round(card25, 3),
            net_oop_24=round(oop24, 2), net_oop_25=round(oop25, 2), net_oop_delta=round(oop25 - oop24, 2),
            lead_plan=lead_plan_text,
            bullets=bullets,
        ))

    D = pd.DataFrame(drills)
    return M, D
