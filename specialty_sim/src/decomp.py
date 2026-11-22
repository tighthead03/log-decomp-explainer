"""
Layer 1: identity-based LMDI decomposition.

Volume identity per cell (drug x payer x region) and period:
    V = R * (S/R) * (A/S) * (F/A) * (V/F)
      R   referrals received
      S/R conversion (new starts / referrals)
      A/S patient-base multiplier (active patients per new start; persistence leverage)
      F/A fills per active patient
      V/F 28-day equivalents per fill (pack size / days-supply mix)
    V = 28-day equivalent fills (days_supply / 28), so tablets and pens are comparable.

Cells with a zero in any component in either period cannot be decomposed with logs.
Cells absent in period A are 'New entrant'; absent in B are 'Lost'; the rest with
zeros are 'Sparse cell'. Each is reported as its own bar, never forced through LMDI.
"""
import numpy as np
import pandas as pd

FACTORS = ["Referrals", "Conversion", "Patient base", "Fill frequency", "Days supply mix"]
CELL = ["drug", "payer", "region"]


def logmean(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    out = np.where(np.isclose(a, b), a, (a - b) / (np.log(a) - np.log(b) + 1e-300))
    return out


def period_components(referrals, fills, start, end, cell=CELL):
    """Aggregate R, S, A, F, V per cell for [start, end]. Inputs are labelled tables with a 'date' column."""
    r = referrals[(referrals.date >= start) & (referrals.date <= end)]
    s = referrals[(referrals.start_date >= start) & (referrals.start_date <= end)]
    f = fills[(fills.date >= start) & (fills.date <= end)]
    out = pd.concat([
        r.groupby(cell).size().rename("R"),
        s.groupby(cell).size().rename("S"),
        f.groupby(cell).patient_id.nunique().rename("A"),
        f.groupby(cell).size().rename("F"),
        f.groupby(cell).eq28.sum().rename("V"),
    ], axis=1).fillna(0)
    return out


def lmdi(c0, c1):
    """Additive LMDI-I between two component tables. Returns per-cell contributions and bucket."""
    df = c0.join(c1, how="outer", lsuffix="0", rsuffix="1").fillna(0)
    comp = ["R", "S", "A", "F", "V"]
    ok = (df[[f"{c}0" for c in comp]].gt(0).all(axis=1)) & (df[[f"{c}1" for c in comp]].gt(0).all(axis=1))
    res = pd.DataFrame(index=df.index)
    res["V0"], res["V1"] = df.V0, df.V1
    res["bucket"] = np.where(ok, "Decomposed", np.where(df.V0 == 0, "New entrant", np.where(df.V1 == 0, "Lost", "Sparse cell")))
    x0 = np.column_stack([df.R0, df.S0 / df.R0.replace(0, np.nan), df.A0 / df.S0.replace(0, np.nan), df.F0 / df.A0.replace(0, np.nan), df.V0 / df.F0.replace(0, np.nan)])
    x1 = np.column_stack([df.R1, df.S1 / df.R1.replace(0, np.nan), df.A1 / df.S1.replace(0, np.nan), df.F1 / df.A1.replace(0, np.nan), df.V1 / df.F1.replace(0, np.nan)])
    w = logmean(df.V1.clip(lower=1e-9), df.V0.clip(lower=1e-9))
    with np.errstate(divide="ignore", invalid="ignore"):
        for j, name in enumerate(FACTORS):
            res[name] = np.where(ok, w * np.log(x1[:, j] / x0[:, j]), 0.0)
    res["Residual"] = np.where(ok, 0.0, df.V1 - df.V0)
    for c in comp:
        res[f"{c}0"], res[f"{c}1"] = df[f"{c}0"], df[f"{c}1"]
    return res


def waterfall(res):
    """Collapse to exec bars: factor bars + new entrant / lost / sparse."""
    bars = {f: res[f].sum() for f in FACTORS}
    for b in ["New entrant", "Lost", "Sparse cell"]:
        bars[b] = res.loc[res.bucket == b, "Residual"].sum()
    total = res.V1.sum() - res.V0.sum()
    bars["check (should be 0)"] = total - sum(bars.values())
    return pd.Series(bars), res.V0.sum(), res.V1.sum()
