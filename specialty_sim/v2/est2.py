"""
v2 estimator: structural counterfactual attribution with calibrated intervals.

Pipeline (sees only fact_referral, fact_fill, dim_prescriber, change_register):
  1. Response models fitted jointly over 2023-2025 with every logged event as a regressor
       referrals   Poisson GLM, monthly cells drug(-family) x payer x region x source (+ flagged practices)
       conversion  logistic GLM, one row per referral
       persistence discrete-time cloglog hazard per fill, offset log(days_supply/28)  -> lapse
       refill gap  Poisson GLM on gap days
       days supply observed share of 3x refills by payer and month
     Event timing and ramp are profiled around the logged date (+-4 weeks, ramp 0/45/90 days) and
     accepted only if they beat the logged date by a likelihood margin.
  2. Discovery: residual step scans on practices, regions, payers and drugs. Strong unexplained steps
     become nuisance regressors (so they cannot contaminate logged events) and are sized as
     'unexplained drivers'.
  3. Counterfactual volume per event (one at a time, all other events left on), via a renewal
     calculation of expected 2025 eq28 per referral / patient episode under 'event on' and 'event off'.
  4. Intervals: parametric draws of model coefficients + process (flip) variance.
"""
import math
import os
import sys
import warnings
import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, os.path.dirname(__file__))
import sim2

warnings.filterwarnings("ignore")
END = pd.Timestamp("2025-12-31")
STD_DS = {d["drug"]: d["ds"] for d in sim2.DRUGS}
B_DRAWS = 300
SHIFTS = [-28, -14, 0, 14, 28]
RAMPS = [0, 45, 90]
PROFILE_MARGIN = 3.0       # log-likelihood gain needed to move away from the logged date / step


# =============================================================================== lightweight GLM (IRLS)
class GLMResult:
    def __init__(self, params, cov, llf, fitted):
        self.params, self._cov, self.llf, self.fittedvalues = params, cov, llf, fitted

    def cov_params(self):
        return self._cov


def glm(y, X, family, n=None, offset=None, start=None, maxiter=60, tol=1e-9):
    """IRLS for poisson(log), logit and cloglog binomial. y is a count (poisson) or a proportion (binomial, weights n)."""
    cols = list(X.columns)
    A = X.values.astype(float)
    y = np.asarray(y, float)
    n = np.ones(len(y)) if n is None else np.asarray(n, float)
    off = np.zeros(len(y)) if offset is None else np.asarray(offset, float)
    p = A.shape[1]
    beta = np.zeros(p) if start is None else np.nan_to_num(np.asarray(start, float))
    if start is None:
        if family == "poisson":
            beta[cols.index("const")] = math.log(max(y.mean(), 1e-6)) if "const" in cols else 0
        else:
            m0 = np.clip((y * n).sum() / n.sum(), 1e-4, 1 - 1e-4)
            beta[cols.index("const")] = (math.log(m0 / (1 - m0)) if family == "logit" else math.log(-math.log(1 - m0))) if "const" in cols else 0
    ll_old = -np.inf
    for _ in range(maxiter):
        eta = np.clip(A @ beta + off, -30, 30)
        if family == "poisson":
            mu = np.exp(eta); d = mu; var = mu; w = n * d * d / var
        elif family == "logit":
            mu = 1 / (1 + np.exp(-eta)); d = mu * (1 - mu); var = d; w = n * d
        else:
            ee = np.exp(eta); mu = 1 - np.exp(-ee); d = ee * np.exp(-ee); var = mu * (1 - mu)
            mu = np.clip(mu, 1e-10, 1 - 1e-10); var = np.clip(var, 1e-10, None); w = n * d * d / var
        z = eta - off + (y - mu) / np.where(d == 0, 1e-12, d)
        XtW = A.T * w
        H = XtW @ A + 1e-8 * np.eye(p)
        beta_new = np.linalg.solve(H, XtW @ z)
        eta = np.clip(A @ beta_new + off, -30, 30)
        if family == "poisson":
            mu = np.exp(eta); ll = float((y * eta - mu).sum())
        else:
            mu = 1 / (1 + np.exp(-eta)) if family == "logit" else 1 - np.exp(-np.exp(eta))
            mu = np.clip(mu, 1e-12, 1 - 1e-12); ll = float((n * (y * np.log(mu) + (1 - y) * np.log(1 - mu))).sum())
        beta = beta_new
        if abs(ll - ll_old) < tol * (abs(ll) + 1):
            break
        ll_old = ll
    eta = A @ beta + off
    if family == "poisson":
        mu = np.exp(eta); w = n * mu
    elif family == "logit":
        mu = 1 / (1 + np.exp(-eta)); w = n * mu * (1 - mu)
    else:
        ee = np.exp(eta); mu = 1 - np.exp(-ee); d = ee * np.exp(-ee); w = n * d * d / np.clip(mu * (1 - mu), 1e-10, None)
    cov = np.linalg.pinv((A.T * w) @ A)
    return GLMResult(pd.Series(beta, cols), pd.DataFrame(cov, cols, cols), ll, mu)


def days(ts):
    return ((pd.to_datetime(ts) - sim2.D0) / pd.Timedelta(days=1)).astype(float)


def strength(day, start, end, ramp):
    day = np.asarray(day, float)
    s = np.where(day >= start, 1.0, 0.0) if ramp <= 0 else np.clip((day - start + 1) / ramp, 0, 1)
    if end is not None:
        s = np.where(day > end, 0.0, s)
    return s


def month_strength(month_start_days, start, end, ramp):
    """Mean strength over the days of each month (vector of month-start day indices)."""
    out = np.zeros(len(month_start_days))
    for i, m0 in enumerate(month_start_days):
        d = np.arange(m0, m0 + 31)
        d = d[(sim2.D0 + pd.to_timedelta(d, "D")).month == (sim2.D0 + pd.Timedelta(days=int(m0))).month]
        out[i] = strength(d, start, end, ramp).mean()
    return out


def scope_mask(df, scope, drugkey=None):
    m = np.ones(len(df), bool)
    for k, vals in scope.items():
        col = {"drug": "drug", "payer": "payer", "region": "region", "source": "referral_source", "practice": "practice"}[k]
        if k == "drug" and drugkey is not None:
            vals = {drugkey.get(v, v) for v in vals}
            col = "drugkey"
        if col not in df:
            m &= False
            continue
        m &= df[col].isin(vals).values
    return m


class Event:
    def __init__(self, eid, scope, start, end, kpis, logged=True, label=None):
        self.eid, self.scope, self.start, self.end, self.kpis, self.logged = eid, scope, start, end, kpis, logged
        self.ramp, self.label = 0, label or eid
        ch = set()
        for k in kpis:
            if k == "referrals":
                ch |= {"ref", "conv"}
            elif k in ("conversion", "pa_required_rate", "tat"):
                ch.add("conv")
            elif k in ("lapse_rate", "fills_per_active"):
                ch.add("pers")
            elif k == "net_oop":
                ch |= {"conv", "pers"}
            elif k == "eq28_per_fill":
                ch.add("ds")
            elif k == "new_entrant":
                ch.add("new")
        if "ds" in ch and "lapse_rate" not in kpis:
            ch.discard("pers")      # fewer fills per patient is mechanical when days supply changes
        self.channels = ch


def load_events(reg):
    evs = []
    for r in reg.itertuples(index=False):
        sc = {}
        for k, c in (("drug", "scope_drug"), ("payer", "scope_payer"), ("region", "scope_region"), ("source", "scope_source")):
            v = getattr(r, c)
            if isinstance(v, str) and v:
                sc[k] = set(v.split("|"))
        end = None if pd.isna(r.end_date) else float(days(pd.Series([r.end_date])).iat[0])
        evs.append(Event(r.event_id, sc, float(days(pd.Series([r.start_date])).iat[0]), end, set(str(r.expected_kpis).split("|"))))
    return evs


# =============================================================================== estimator
class Estimator:
    def __init__(self, ref, fill, pres, reg):
        self.ref, self.fill, self.pres = ref.copy(), fill.copy(), pres
        self.events = load_events(reg)
        y24 = ref[(ref.referral_date >= "2024-01-01") & (ref.referral_date <= "2024-12-31")].drug.value_counts()
        self.new_drugs = [d for d in ref.drug.unique() if y24.get(d, 0) == 0]
        self.drugkey = {}
        for o, b in sim2.BIOSIM_OF.items():
            if b in self.new_drugs:
                self.drugkey[o] = self.drugkey[b] = sim2.FAMILY[o]
        self.new_drugs = [d for d in self.new_drugs if d not in self.drugkey]
        for df in (self.ref, self.fill):
            df["drugkey"] = df.drug.map(lambda d: self.drugkey.get(d, d))
        self.ref["day"] = days(self.ref.referral_date)
        self.fill["day"] = days(self.fill.fill_date)
        self.flagged = []      # nuisance practices
        self.nuisance = []

    # ------------------------------------------------------------------ referral model
    def _ref_frame(self):
        r = self.ref[~self.ref.drug.isin(self.new_drugs)].copy()
        r["pgroup"] = np.where(r.practice.isin(self.flagged), r.practice, "other")
        r["m"] = r.referral_date.dt.to_period("M")
        use_src = any("source" in e.scope for e in self.events + self.nuisance if "ref" in e.channels)
        if not use_src:
            r["referral_source"] = "all"
        keys = ["drugkey", "payer", "region", "referral_source", "pgroup"]
        cnt = r.groupby(keys + ["m"]).size().rename("n")
        combos = r[keys].drop_duplicates()
        months = pd.period_range("2023-01", "2025-12", freq="M")
        grid = combos.assign(_k=1).merge(pd.DataFrame({"m": months, "_k": 1}), on="_k").drop(columns="_k")
        grid = grid.merge(cnt.reset_index(), on=keys + ["m"], how="left").fillna({"n": 0})
        grid["m0"] = days(grid.m.dt.to_timestamp())
        grid["moy"] = grid.m.dt.month
        grid["t"] = (grid.m0 - grid.m0.min()) / 365.25
        self.R = grid.rename(columns={"referral_source": "referral_source"})
        X = pd.get_dummies(grid[["drugkey", "payer", "region", "referral_source", "pgroup"]].astype(str), drop_first=True)
        X = X.join(pd.get_dummies(grid.payer + "|" + grid.region, prefix="pr", drop_first=True))
        X = X.join(pd.get_dummies(grid.drugkey + "|" + grid.region, prefix="dr", drop_first=True))
        X = X.join(pd.get_dummies(grid.moy.astype(str), prefix="moy", drop_first=True))
        X["t"] = grid.t
        X.insert(0, "const", 1.0)
        self.RX = X.astype(float)
        self._ms_cache = {}

    def _ref_col(self, ev, start=None, ramp=None):
        end = ev.end if (start is None or ev.end is None) else ev.end + (start - ev.start)
        start = ev.start if start is None else start
        ramp = ev.ramp if ramp is None else ramp
        sc = {k: v for k, v in ev.scope.items()}
        if "practice" in sc:
            m = self.R.pgroup.isin(sc["practice"]).values
        else:
            m = scope_mask(self.R, sc, self.drugkey)
        ms = self._month_s(start, end, ramp)
        return m * self.R.m0.map(ms).values

    def _month_s(self, start, end, ramp):
        key = (start, end, ramp)
        if key not in self._ms_cache:
            m0s = np.sort(self.R.m0.unique())
            self._ms_cache[key] = dict(zip(m0s, month_strength(m0s, start, end, ramp)))
        return self._ms_cache[key]

    def _fit_poisson(self, cols, start_params=None):
        X = self.RX.copy()
        for k, v in cols.items():
            X[k] = v
        keep = X.columns[(X != 0).any()]
        X = X[keep]
        return glm(self.R.n.values, X, "poisson", start=start_params.reindex(keep).fillna(0).values if start_params is not None else None)

    def fit_referrals(self, profile=True):
        self._ref_frame()
        evs = [e for e in self.events + self.nuisance if "ref" in e.channels]
        cols = {f"ev_{e.eid}": self._ref_col(e) for e in evs}
        res = self._fit_poisson(cols)
        if profile:
            for e in evs:
                if not e.logged:
                    continue
                base_ll, best = res.llf, (res.llf, e.start, e.ramp)
                for sh in SHIFTS:
                    for rp in RAMPS:
                        if sh == 0 and rp == 0:
                            continue
                        c2 = dict(cols)
                        c2[f"ev_{e.eid}"] = self._ref_col(e, e.start + sh, rp)
                        r2 = self._fit_poisson(c2, start_params=res.params)
                        if r2.llf > best[0]:
                            best = (r2.llf, e.start + sh, rp)
                if best[0] - base_ll > PROFILE_MARGIN:
                    if e.end is not None:
                        e.end += best[1] - e.start
                    e.start, e.ramp = best[1], best[2]
                    cols[f"ev_{e.eid}"] = self._ref_col(e)
                    res = self._fit_poisson(cols, start_params=res.params)
        self.ref_res, self.ref_cols = res, cols
        return res

    # ------------------------------------------------------------------ discovery of unexplained steps
    def discover(self, lr_threshold=30.0):
        """Scan residual step changes in 2025 on practices, regions, payers, drugs. Returns new nuisance events."""
        R = self.R.assign(mu=np.asarray(self.ref_res.fittedvalues))
        found = []
        months = sorted(R.m.unique())
        m25 = [m for m in months if m.year == 2025 and m.month >= 2]
        def scan(o, e, rel_min):
            best = None
            for sm_ in m25:
                pre, post = [m for m in months if m < sm_], [m for m in months if m >= sm_]
                O1, E1, O2, E2 = o[pre].sum(), e[pre].sum(), o[post].sum(), e[post].sum()
                if E2 < 30:
                    continue
                r1, r2 = O1 / E1, O2 / E2
                r0 = (O1 + O2) / (E1 + E2)
                ll = lambda O, E, r: O * np.log(max(r, 1e-9)) - r * E
                lr = 2 * (ll(O1, E1, r1) + ll(O2, E2, r2) - ll(O1, E1, r0) - ll(O2, E2, r0))
                rel = r2 / r1 - 1
                if abs(rel) >= rel_min and (best is None or lr > best[0]):
                    best = (lr, sm_, rel)
            return best
        for dim in ["region", "payer", "drugkey"]:
            g = R.groupby([dim, "m"])[["n", "mu"]].sum()
            for v in R[dim].unique():
                b = scan(g.loc[v].n, g.loc[v].mu, 0.10)
                if b and b[0] > lr_threshold:
                    found.append(({"drug" if dim == "drugkey" else dim: {v}}, b))
        # practices: expected = pre-2025 share of each drug x region cell applied to that cell's monthly referrals
        r = self.ref[~self.ref.drug.isin(self.new_drugs)].assign(m=lambda x: x.referral_date.dt.to_period("M"))
        pre = r[r.referral_date < "2025-01-01"]
        cell = r.groupby(["drugkey", "region", "m"]).size().unstack(fill_value=0).reindex(columns=months, fill_value=0)
        pre_cell = pre.groupby(["drugkey", "region"]).size()
        for p in r.practice.value_counts().head(30).index:
            rp = r[r.practice == p]
            o = rp.groupby("m").size().reindex(months, fill_value=0)
            share = (pre[pre.practice == p].groupby(["drugkey", "region"]).size() / pre_cell).dropna()
            e = (cell.loc[share.index].mul(share, axis=0)).sum()
            b = scan(o, e, 0.30)
            if b and b[0] > lr_threshold:
                found.append(({"practice": {p}}, b))
        new = []
        existing = {str(sorted((k, tuple(sorted(v))) for k, v in n.scope.items())) for n in self.nuisance}
        found = sorted(found, key=lambda x: (0 if "practice" in x[0] else 1, -x[1][0]))   # most specific first, one per pass
        for sc, (lr, mth, rel) in found:
            key = str(sorted((k, tuple(sorted(v))) for k, v in sc.items()))
            if key in existing:
                continue
            # skip if a logged event with compatible scope starts within 45 days (already modelled)
            st = float(days(pd.Series([mth.to_timestamp()])).iat[0])
            if any(e.logged and "ref" in e.channels and abs(e.start - st) <= 45 and all(k in e.scope and e.scope[k] & v for k, v in sc.items() if k != "practice")
                   and "practice" not in sc for e in self.events):
                continue
            ev = Event(f"U{len(self.nuisance) + len(new) + 1:02d}", sc, st, None, {"referrals"}, logged=False,
                       label=f"Unexplained: {list(sc)[0]}={next(iter(list(sc.values())[0]))} referrals {rel:+.0%} from {mth}")
            ev.lr = lr
            new.append(ev)
            existing.add(key)
            if "practice" in sc:
                self.flagged.append(next(iter(sc["practice"])))
            break
        self.nuisance += new
        return new

    # ------------------------------------------------------------------ conversion model
    def fit_conversion(self, profile=True):
        r = self.ref[self.ref.referral_date <= "2025-11-30"].copy()
        X = pd.get_dummies(r[["payer", "drug", "region", "referral_source"]].astype(str), drop_first=True)
        X = X.join(pd.get_dummies(r.referral_date.dt.month.astype(str), prefix="moy", drop_first=True))
        X["t"] = (r.day - r.day.min()) / 365.25
        X.insert(0, "const", 1.0)
        X = X.astype(float)
        self.CXcols = list(X.columns)
        y = r.converted.astype(float).values
        evs = [e for e in self.events + self.nuisance if "conv" in e.channels]
        def col(e, start=None, ramp=None):
            end = e.end if (start is None or e.end is None) else e.end + (start - e.start)
            return scope_mask(r, e.scope) * strength(r.day.values, e.start if start is None else start, end, e.ramp if ramp is None else ramp)
        def fit(cols, sp=None):
            Z = X.copy()
            for k, v in cols.items():
                Z[k] = v
            Z = Z.loc[:, (Z != 0).any()]
            return glm(y, Z, "logit", start=sp.reindex(Z.columns).fillna(0).values if sp is not None else None)
        cols = {f"ev_{e.eid}": col(e) for e in evs}
        res = fit(cols)
        if profile:
            for e in evs:
                if not e.logged or "ref" in e.channels:     # referral events keep referral-model timing
                    continue
                base, best = res.llf, (res.llf, e.start, e.ramp)
                for sh in SHIFTS:
                    for rp in RAMPS:
                        if sh == 0 and rp == 0:
                            continue
                        c2 = dict(cols)
                        c2[f"ev_{e.eid}"] = col(e, e.start + sh, rp)
                        r2 = fit(c2, res.params)
                        if r2.llf > best[0]:
                            best = (r2.llf, e.start + sh, rp)
                if best[0] - base > PROFILE_MARGIN:
                    if e.end is not None:
                        e.end += best[1] - e.start
                    e.start, e.ramp = best[1], best[2]
                    cols[f"ev_{e.eid}"] = col(e)
                    res = fit(cols, res.params)
        self.conv_res, self.conv_X = res, X
        # TAT model (OLS, converted referrals): a delay costs therapy days even when the patient converts
        Z = X.copy()
        for k, v_ in cols.items():
            Z[k] = col(self._ev(k[3:]))
        Z = Z.loc[:, (Z != 0).any()]
        ok = r.converted.values & r.tat_days.notna().values
        A, yt = Z.values[ok], r.tat_days.values[ok].astype(float)
        beta, *_ = np.linalg.lstsq(A, yt, rcond=None)
        resid = yt - A @ beta
        cov = np.linalg.pinv(A.T @ A) * resid.var()
        self.tat_beta = pd.Series(beta, Z.columns)
        self.tat_cov = pd.DataFrame(cov, Z.columns, Z.columns)
        self.tat_base = float(np.median(r.loc[(r.referral_date < "2025-01-01") & r.converted, "tat_days"]))
        self.conv_cols = cols
        return res

    def conv_design(self, df):
        X = pd.get_dummies(df[["payer", "drug", "region", "referral_source"]].astype(str), drop_first=False)
        X = X.join(pd.get_dummies(df.referral_date.dt.month.astype(str), prefix="moy"))
        X["t"] = (df.day - self.ref.day.min()) / 365.25
        X["const"] = 1.0
        params = self.conv_res.params
        X = X.reindex(columns=[c for c in params.index if not c.startswith("ev_")], fill_value=0).astype(float)
        for c in [c for c in params.index if c.startswith("ev_")]:
            e = self._ev(c[3:])
            X[c] = scope_mask(df, e.scope) * strength(df.day.values, e.start, e.end, e.ramp)
        return X[params.index]

    # ------------------------------------------------------------------ persistence: hazard, gap, days supply
    def fit_persistence(self, profile=True):
        f = self.fill.sort_values(["patient_id", "fill_date"]).copy()
        f["next_day"] = f.groupby("patient_id").day.shift(-1)
        f["horizon"] = f.day + f.days_supply + 60
        f = f[f.horizon <= days(pd.Series([END])).iat[0]].copy()
        horizon = f.horizon
        f["lapse"] = (f.next_day.isna() | (f.next_day > horizon)).astype(float)
        f["gap"] = np.where(f.lapse == 0, (f.next_day - f.day - f.days_supply).clip(lower=0), np.nan)
        f["m"] = f.fill_date.dt.to_period("M")
        f["first"] = (f.fill_number == 0).astype(int)
        self.P = f
        evs = [e for e in self.events + self.nuisance if "pers" in e.channels]
        def agg(cols):
            g = f.assign(**cols)
            keys = ["drug", "payer", "m", "days_supply", "first"]
            a = g.groupby(keys).agg(n=("lapse", "size"), y=("lapse", "sum"), gsum=("gap", "sum"), gn=("gap", "count"),
                                    **{k: (k, "mean") for k in cols}).reset_index()
            return a
        def design(a, cols):
            X = pd.get_dummies(a[["drug", "payer"]].astype(str), drop_first=True)
            X = X.join(pd.get_dummies(a.m.dt.month.astype(str), prefix="moy", drop_first=True))
            X["first"] = a["first"]
            X["t"] = (a.m.dt.year - 2023) + (a.m.dt.month - 1) / 12
            X.insert(0, "const", 1.0)
            for k in cols:
                X[k] = a[k]
            return X.astype(float)
        def col(e, start=None, ramp=None):
            end = e.end if (start is None or e.end is None) else e.end + (start - e.start)
            return scope_mask(f, e.scope) * strength(f.day.values, e.start if start is None else start, end, e.ramp if ramp is None else ramp)
        def fit(cols):
            a = agg(cols)
            X = design(a, cols)
            X = X.loc[:, (X != 0).any()]
            hz = glm(a.y / a.n, X, "cloglog", n=a.n, offset=np.log(a.days_supply / 28))
            ga = a[a.gn > 0]
            Xg = X.loc[ga.index]
            Xg = Xg.loc[:, (Xg != 0).any()]
            gp = glm(ga.gsum, Xg, "poisson", offset=np.log(ga.gn))
            return hz, gp, a
        cols = {f"ev_{e.eid}": col(e) for e in evs}
        hz, gp, a = fit(cols)
        if profile:
            for e in evs:
                if not e.logged:
                    continue
                base, best = hz.llf + gp.llf, (hz.llf + gp.llf, e.start, e.ramp)
                for sh in SHIFTS:
                    for rp in RAMPS:
                        if sh == 0 and rp == 0:
                            continue
                        c2 = dict(cols)
                        c2[f"ev_{e.eid}"] = col(e, e.start + sh, rp)
                        h2, g2, _ = fit(c2)
                        if h2.llf + g2.llf > best[0]:
                            best = (h2.llf + g2.llf, e.start + sh, rp)
                if best[0] - base > PROFILE_MARGIN:
                    if e.end is not None:
                        e.end += best[1] - e.start
                    e.start, e.ramp = best[1], best[2]
                    cols[f"ev_{e.eid}"] = col(e)
                    hz, gp, a = fit(cols)
        self.hz_res, self.gap_res = hz, gp
        # days-supply share of 3x refills by payer and month (observed)
        rf = self.fill[(self.fill.fill_number >= 1) & (self.fill.drug.map(STD_DS) <= 30)].copy()
        rf["x3"] = (rf.days_supply == 3 * rf.drug.map(STD_DS)).astype(float)
        rf["m"] = rf.fill_date.dt.to_period("M")
        self.q = rf.groupby(["payer", "m"]).x3.agg(["mean", "size"])
        return hz, gp

    def _ev(self, eid):
        return next(e for e in self.events + self.nuisance if e.eid == eid)

    def pers_design(self, drug, payer, day, first, which):
        """Design rows for hazard/gap at 2025 conditions; month-of-year averaged over Feb-Nov."""
        res = self.hz_res if which == "hz" else self.gap_res
        params = res.params
        n = len(drug)
        X = pd.DataFrame(0.0, index=range(n), columns=params.index)
        X["const"] = 1.0
        for c in params.index:
            if c.startswith("drug_"):
                X[c] = (np.asarray(drug) == c[5:]).astype(float)
            elif c.startswith("payer_"):
                X[c] = (np.asarray(payer) == c[6:]).astype(float)
            elif c.startswith("moy_"):
                mo = int(c[4:])
                X[c] = (1.0 / 10) if 2 <= mo <= 11 else 0.0
        if "first" in X:
            X["first"] = np.asarray(first, float)
        X["t"] = 2.5 if "t" in X else 0
        tmp = pd.DataFrame({"drug": drug, "payer": payer})
        for c in [c for c in params.index if c.startswith("ev_")]:
            e = self._ev(c[3:])
            # mean strength over the remaining year from each episode start
            END_D = int(days(pd.Series([END])).iat[0])
            cs = np.concatenate([[0.0], np.cumsum(strength(np.arange(0, END_D + 1), e.start, e.end, e.ramp))])
            d0 = np.clip(np.asarray(day, float).astype(int), 0, END_D)
            s = (cs[END_D + 1] - cs[d0]) / (END_D - d0 + 1)
            X[c] = scope_mask(tmp, e.scope) * s
        return X

    # ------------------------------------------------------------------ renewal volume
    @staticmethod
    def renewal(T, ds_first, ds_ref, lam, g):
        """Expected eq28 dispensed within T days: first fill + refills while surviving."""
        T = np.asarray(T, float)
        S_first = np.exp(-lam * ds_first)
        S = np.exp(-lam * ds_ref)
        room = T - ds_first - g
        K = np.where(room >= 0, np.floor(room / (ds_ref + g)) + 1, 0)
        geom = np.where(np.abs(1 - S) < 1e-12, K, (1 - S ** K) / np.maximum(1 - S, 1e-12))
        return ds_first / 28 + S_first * (ds_ref / 28) * geom

    def q_day(self, payer, day):
        """Observed share of 3x refills for the payer in the calendar month of each day."""
        if not hasattr(self, "_qm"):
            self._qm = {(p, m.ordinal): v for (p, m), v in self.q["mean"].items()}
        mo = (sim2.D0 + pd.to_timedelta(np.clip(np.asarray(day, float), 0, None), "D")).to_period("M")
        return np.array([self._qm.get((p, m.ordinal), 0.0) for p, m in zip(payer, mo)])

    def step_renewal(self, t0, dsf, std, payer, eta_h, eta_g, q0=None, mod_h=None, mod_g=None):
        """Expected eq28 dispensed by year end, stepping fill by fill: each refill's days supply, hazard and gap
        follow the calendar date. q0 (scalar or array) replaces the observed 3x share (used for 'mandate off')."""
        END_D = float(days(pd.Series([END])).iat[0])
        t = np.asarray(t0, float).copy()
        ds_k = np.asarray(dsf, float).copy()
        std = np.asarray(std, float)
        small = std <= 30
        S = np.ones(len(t))
        E = np.where(t <= END_D, ds_k / 28, 0.0)
        for _ in range(40):
            alive = t <= END_D
            if not alive.any():
                break
            mh = mod_h(t) if mod_h is not None else 0.0
            mg = mod_g(t) if mod_g is not None else 0.0
            lam = np.exp(eta_h + mh) / 28
            g = np.exp(eta_g + mg)
            S = S * np.exp(-lam * ds_k)
            t = t + ds_k + g
            q = (self.q_day(payer, t) if q0 is None else (q0 if np.ndim(q0) else np.full(len(t), q0)))
            ds_k = np.where(small, std * (1 + 2 * q), std)
            E = E + np.where(t <= END_D, S * ds_k / 28, 0.0)
        return E

    def q_share(self, payer, day):
        """Observed share of 3x refills for the payer, averaged from the given month to year end."""
        if not hasattr(self, "_qtab"):
            tab = {}
            for p in self.q.index.get_level_values(0).unique():
                ser = self.q.loc[p]["mean"].sort_index()
                suffix = ser[::-1].expanding().mean()[::-1]
                tab[p] = {m.ordinal: v for m, v in suffix.items()}
                tab[p]["_last"] = ser.iloc[-1]
            self._qtab = tab
        mo = (sim2.D0 + pd.to_timedelta(np.asarray(day, float), "D")).to_period("M")
        ords = np.array([m.ordinal for m in mo])
        return np.array([self._qtab.get(p, {}).get(o, self._qtab.get(p, {}).get("_last", 0.0)) for p, o in zip(payer, ords)])

    # ------------------------------------------------------------------ collinearity groups
    def units(self, thr=0.8):
        """Events whose regressors are near-collinear in any shared model are sized jointly."""
        evs = self.events + self.nuisance
        cols = {}
        for name, mats in (("ref", self.ref_cols), ("conv", self.conv_cols)):
            for k, v_ in mats.items():
                cols.setdefault(k, {})[name] = np.asarray(v_, float)
        parent = {e.eid: e.eid for e in evs}
        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for i, a_ in enumerate(evs):
            for b_ in evs[i + 1:]:
                for name in ("ref", "conv"):
                    xa, xb = cols.get(f"ev_{a_.eid}", {}).get(name), cols.get(f"ev_{b_.eid}", {}).get(name)
                    if xa is None or xb is None:
                        continue
                    if (xa != 0).sum() < 10 or (xb != 0).sum() < 10 or xa.std() == 0 or xb.std() == 0:
                        continue
                    if abs(np.corrcoef(xa, xb)[0, 1]) > thr:
                        parent[find(b_.eid)] = find(a_.eid)
        groups = {}
        for e in evs:
            groups.setdefault(find(e.eid), []).append(e)
        return list(groups.values())

    # ------------------------------------------------------------------ attribution
    def attribute(self, rng=None):
        rng = rng or np.random.default_rng(0)
        END_D = float(days(pd.Series([END])).iat[0])
        out = {}
        br, Cr = self.ref_res.params, self.ref_res.cov_params()
        bc, Cc = self.conv_res.params, self.conv_res.cov_params()
        draws_c = rng.multivariate_normal(bc.values, Cc.values, size=B_DRAWS, method="eigh")
        bh, Ch = self.hz_res.params, self.hz_res.cov_params()
        bg, Cg = self.gap_res.params, self.gap_res.cov_params()
        dh = rng.multivariate_normal(bh.values, Ch.values, size=B_DRAWS // 3, method="eigh")
        dg = rng.multivariate_normal(bg.values, Cg.values, size=B_DRAWS // 3, method="eigh")
        tat = self.tat_base
        def vol_new(drug, payer, start_day):
            eh = self.pers_design(drug, payer, start_day, np.zeros(len(drug)), "hz").values @ bh.values
            eg = self.pers_design(drug, payer, start_day, np.zeros(len(drug)), "gap").values @ bg.values
            ds = pd.Series(drug).map(STD_DS).values.astype(float)
            return self.step_renewal(start_day, ds, ds, np.asarray(payer), eh, eg)

        # observed 2025 referrals (conversion-only units)
        r = self.ref[(self.ref.referral_date >= "2025-01-01") & ~self.ref.drug.isin(self.new_drugs)].copy().reset_index(drop=True)
        Xc = self.conv_design(r)
        Xt = Xc.reindex(columns=self.tat_beta.index, fill_value=0.0)
        Xt["const"] = 1.0
        shift_all = sum(Xt[c].values * self.tat_beta[c] for c in self.tat_beta.index if c.startswith("ev_")) if any(c.startswith("ev_") for c in self.tat_beta.index) else np.zeros(len(r))
        start_on = r.day.values + tat + shift_all
        v_on_r = vol_new(r.drug.values, r.payer.values, start_on)
        eta_c = Xc.values @ bc.values

        # cell-month frame (referral units)
        G = self.R[self.R.m0 >= days(pd.Series([pd.Timestamp("2025-01-01")])).iat[0]].copy()
        Xg = self.RX.loc[G.index].copy()
        for c in [c for c in br.index if c.startswith("ev_")]:
            Xg[c] = np.asarray(self.ref_cols.get(c, np.zeros(len(self.R))))[G.index]
        Xg = Xg.reindex(columns=br.index, fill_value=0.0)
        eta_r = Xg.values @ br.values
        rep = {v_: k for k, v_ in self.drugkey.items() if k in sim2.BIOSIM_OF}
        G["drug"] = G.drugkey.map(lambda k: rep.get(k, k))
        G["day"] = G.m0 + 15
        v_g = vol_new(G.drug.values, G.payer.values, G.day.values + tat)
        src_w = self.ref[self.ref.referral_date >= "2025-01-01"].referral_source.value_counts(normalize=True)
        srcs = list(src_w.index) if (G.referral_source == "all").all() else [None]
        Gd_list = []
        for sname in srcs:
            Gd = G.assign(referral_date=sim2.D0 + pd.to_timedelta(G.day, "D"), referral_source=sname if sname is not None else G.referral_source)
            Gd_list.append((src_w[sname] if sname is not None else 1.0, self.conv_design(Gd)))
        def conv_cells(beta, drop=()):
            p = np.zeros(len(G))
            for w_, Xd in Gd_list:
                eta = Xd.values @ beta
                for c in drop:
                    if c in Xd:
                        eta = eta - beta[list(Xd.columns).index(c)] * Xd[c].values
                p += w_ / (1 + np.exp(-eta))
            return p
        p_on_g = conv_cells(bc.values)

        f = self.fill.sort_values("day")
        f25 = self.fill[self.fill.fill_date >= "2025-01-01"]
        for unit in self.units():
            ids = [e.eid for e in unit]
            cols = [f"ev_{e}" for e in ids]
            ch = set().union(*[e.channels for e in unit])
            est, sims, var_proc, used = 0.0, np.zeros(1), 0.0, []
            sims_parts = []
            # --- referral channel (cell level, expected counts)
            rc = [c for c in cols if c in br.index and any(f"ev_{e.eid}" == c and "ref" in e.channels for e in unit)]
            if rc:
                x = Xg[rc].values
                inn = (x != 0).any(axis=1)
                if inn.any():
                    cc = [c for c in cols if c in bc.index]
                    p_off = conv_cells(bc.values, drop=cc) if cc else p_on_g
                    bG = br[rc].values
                    CG = Cr.loc[rc, rc].values
                    finite = np.all(np.isfinite(CG)) and np.all(np.sqrt(np.clip(np.diag(CG), 0, None)) <= 3)
                    def d_ref(bv, p_on, p_off_):
                        return np.exp(eta_r[inn]) * p_on[inn] - np.exp(eta_r[inn] - x[inn] @ bv) * p_off_[inn]
                    dd = d_ref(bG, p_on_g, p_off)
                    est += float((dd * v_g[inn]).sum())
                    bdraws = rng.multivariate_normal(bG, CG, size=100, method="eigh") if finite else np.tile(bG, (100, 1))
                    s_ref = np.array([float((d_ref(bd, p_on_g, p_off) * v_g[inn]).sum()) for bd in bdraws])
                    if cc:
                        jj = [list(bc.index).index(c) for c in cc]
                        s_c = []
                        for b in range(0, B_DRAWS, 6):
                            bb = bc.values.copy(); bb[jj] = draws_c[b][jj]
                            s_c.append(float((d_ref(bG, conv_cells(bb), conv_cells(bb, drop=cc)) * v_g[inn]).sum()))
                        sims_parts.append(np.std(s_ref) ** 2 + np.std(s_c) ** 2)
                    else:
                        sims_parts.append(np.std(s_ref) ** 2)
                    var_proc += float((np.abs(dd) * v_g[inn] ** 2).sum())
                    used.append("ref/conv")
            # --- conversion-only (observed referrals) incl. TAT delay
            cc_only = [c for c in cols if c in bc.index and c not in rc]
            if cc_only:
                xc = Xc[cc_only].values
                inn = (xc != 0).any(axis=1)
                if inn.any():
                    jj = [list(bc.index).index(c) for c in cc_only]
                    tj = [c for c in cc_only if c in self.tat_beta.index]
                    xt = Xt[tj].values[inn] if tj else np.zeros((inn.sum(), 0))
                    def d_conv(beta, tb):
                        p_on = 1 / (1 + np.exp(-(Xc.values[inn] @ beta)))
                        p_off = 1 / (1 + np.exp(-(Xc.values[inn] @ beta - xc[inn] @ beta[jj])))
                        start_off = start_on[inn] - (xt @ tb if len(tj) else 0.0)
                        v_off = vol_new(r.drug.values[inn], r.payer.values[inn], start_off) if len(tj) else v_on_r[inn]
                        return p_on * v_on_r[inn] - p_off * v_off, p_on - p_off
                    tb0 = self.tat_beta[tj].values if tj else np.zeros(0)
                    dd, dp = d_conv(bc.values, tb0)
                    est += float(dd.sum())
                    tdraws = rng.multivariate_normal(tb0, self.tat_cov.loc[tj, tj].values, size=60, method="eigh") if tj else np.zeros((60, 0))
                    s_c = [float(d_conv(draws_c[b], tdraws[b % 60])[0].sum()) for b in range(0, B_DRAWS, 5)]
                    sims_parts.append(np.std(s_c) ** 2)
                    var_proc += float((v_on_r[inn] ** 2 * np.abs(dp)).sum())
                    used.append("conv+tat" if tj else "conv")
            # --- persistence / days supply
            if {"pers", "ds"} & ch:
                pe = [e for e in unit if {"pers", "ds"} & e.channels]
                start = min(e.start for e in pe)
                if any("ds" in e.channels for e in pe):
                    start -= 45          # days-supply share is observed directly, so a wider window is harmless
                sc = {}
                for e in pe:
                    for k, v_ in e.scope.items():
                        sc.setdefault(k, set()).update(v_)
                ff = f[(f.day >= start) & (f.day <= END_D)]
                ff = ff[scope_mask(ff, {k: v_ for k, v_ in sc.items() if k != "practice"})]
                ep = ff.groupby("patient_id").first().reset_index()
                if len(ep):
                    T = END_D - ep.day.values
                    std = ep.drug.map(STD_DS).values.astype(float)
                    XH = self.pers_design(ep.drug.values, ep.payer.values, ep.day.values, np.zeros(len(ep)), "hz")
                    XG = self.pers_design(ep.drug.values, ep.payer.values, ep.day.values, np.zeros(len(ep)), "gap")
                    pc = [c for c in cols if c in XH.columns]
                    pg = [c for c in cols if c in XG.columns]
                    offd = any("ds" in e.channels for e in pe)
                    q_on = np.where(std <= 30, self.q_share(ep.payer.values, ep.day.values), 0.0)
                    pre = self.q.loc[(self.q.index.get_level_values("m").to_timestamp() < sim2.D0 + pd.Timedelta(days=start))]
                    q0 = pre.groupby(level="payer")["mean"].mean()
                    q_off = np.where(std <= 30, ep.payer.map(q0).fillna(0).values, 0.0) if offd else q_on
                    dsf_on = ep.days_supply.values.astype(float)
                    dsf_off = std if offd else dsf_on
                    masks = {c: np.asarray(XH[c].values > 0, float) for c in pc}
                    evmap = {f"ev_{e.eid}": e for e in pe}
                    END_I = int(END_D)
                    daily = {c: strength(np.arange(0, END_I + 400), evmap[c].start, evmap[c].end, evmap[c].ramp) for c in set(pc) | set(pg) if c in evmap}
                    pay = ep.payer.values
                    def vol(bhv, bgv, off):
                        eh, eg = XH.values @ bhv, XG.values @ bgv
                        # remove the mean-strength terms, then add the exact time path (on) or nothing (off)
                        for c in pc:
                            eh = eh - bhv[list(XH.columns).index(c)] * XH[c].values
                        for c in pg:
                            eg = eg - bgv[list(XG.columns).index(c)] * XG[c].values
                        mh = mg = None
                        if not off:
                            mh = lambda t: sum(bhv[list(XH.columns).index(c)] * (XH[c].values > 0) * daily[c][np.clip(t.astype(int), 0, END_I + 399)] for c in pc if c in daily) if pc else 0.0
                            mg = lambda t: sum(bgv[list(XG.columns).index(c)] * (XG[c].values > 0) * daily[c][np.clip(t.astype(int), 0, END_I + 399)] for c in pg if c in daily) if pg else 0.0
                        if off and offd:
                            return self.step_renewal(ep.day.values, dsf_off, std, pay, eh, eg, q0=q_off, mod_h=mh, mod_g=mg)
                        return self.step_renewal(ep.day.values, dsf_on, std, pay, eh, eg, mod_h=mh, mod_g=mg)
                    on, off = vol(bh.values, bg.values, False), vol(bh.values, bg.values, True)
                    est += float((on - off).sum())
                    s_p = [float((vol(dh[b], dg[b], False) - vol(dh[b], dg[b], True)).sum()) for b in range(len(dh))]
                    sims_parts.append(np.std(s_p) ** 2)
                    var_proc += float((np.abs(on - off) * on).sum())
                    used.append("pers/ds")
            # --- new entrants
            nd = [d for e in unit if "new" in e.channels and "drug" in e.scope for d in e.scope["drug"] if d in self.new_drugs]
            if nd:
                xv = f25[f25.drug.isin(nd)].eq28
                est += float(xv.sum())
                var_proc += float((xv ** 2).sum())
                used.append("new")
            if used:
                rec = dict(est=est, sd_param=math.sqrt(sum(sims_parts)) if sims_parts else 0.0, var_proc=var_proc,
                           channels="+".join(used), unit="+".join(ids))
                for e in ids:
                    out[e] = rec
        return out


def run(world_dir, calib=1.0, discover=True):
    ref = pd.read_parquet(f"{world_dir}/fact_referral.parquet")
    fill = pd.read_parquet(f"{world_dir}/fact_fill.parquet")
    pres = pd.read_parquet(f"{world_dir}/dim_prescriber.parquet")
    reg = pd.read_csv(f"{world_dir}/change_register.csv", parse_dates=["start_date", "end_date"])
    E = Estimator(ref, fill, pres, reg)
    E.fit_referrals(profile=True)
    if discover:
        for _ in range(4):
            if not E.discover():
                break
            E.fit_referrals(profile=False)
    E.fit_conversion(profile=True)
    E.fit_persistence(profile=True)
    att = E.attribute()
    rows = []
    for ev in E.events + E.nuisance:
        a = att.get(ev.eid)
        if a is None:
            a = dict(est=0.0, sd_param=0.0, var_proc=0.0, channels="none")
        sd = math.sqrt(a["sd_param"] ** 2 + a["var_proc"])
        rows.append(dict(event_id=ev.eid, unit=a.get("unit", ev.eid), logged=ev.logged, label=ev.label, channels=a["channels"], est=a["est"], sd=sd,
                         est_start=(sim2.D0 + pd.Timedelta(days=ev.start)).date(), est_ramp=ev.ramp,
                         scope=str({k: sorted(v) for k, v in ev.scope.items()})))
    out = pd.DataFrame(rows)
    out["lo"] = out.est - 1.96 * calib * out.sd
    out["hi"] = out.est + 1.96 * calib * out.sd
    out["claimed"] = (out.lo > 0) | (out.hi < 0)
    return out
