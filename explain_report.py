"""
Plain-English executive report: what changed, why, when, how sure.

build_html(wdir, outdir, header, T, u, stats) -> writes report.html (+ PNGs) to outdir.
"""
import base64
import io
import os
import re

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

GREEN, RED, AMBER, GREY, NAVY = "#2e8b57", "#c0392b", "#d98c2b", "#9aa5b1", "#34495e"

CONFIDENCE = {
    "Confirmed": ("High confidence", "#2e8b57"),
    "Probable": ("Likely", "#d98c2b"),
    "Joint: cannot be separated": ("Combined effect", "#6c5ce7"),
    "Unexplained driver: investigate": ("Not in change log: investigate", "#c0392b"),
}


# ----------------------------------------------------------------------------- plain-English wording
def _list(v):
    return [] if not isinstance(v, str) or not v else v.split("|")


def where_text(r):
    parts = []
    if _list(r.get("scope_drug")):
        parts.append(" / ".join(_list(r["scope_drug"])))
    if _list(r.get("scope_payer")):
        parts.append(" / ".join(_list(r["scope_payer"])) + " patients")
    if _list(r.get("scope_region")):
        parts.append(" / ".join(_list(r["scope_region"])) + " region")
    if _list(r.get("scope_source")):
        parts.append(" / ".join(_list(r["scope_source"])) + " referrals")
    return ", ".join(parts)


def describe(etype, reg_row, est):
    """Return (headline, why-it-moved-volume) for a logged event."""
    up = est >= 0
    w = where_text(reg_row) if reg_row is not None else ""
    at = f" ({w})" if w else ""
    drug = " / ".join(_list(reg_row.get("scope_drug"))) if reg_row is not None else ""
    payer = " / ".join(_list(reg_row.get("scope_payer"))) if reg_row is not None else ""
    t = {
        "referral_mult": (f"Referrals {'increased' if up else 'fell'}{at}",
                          f"{'More' if up else 'Fewer'} new patients were referred to us, so {'more' if up else 'fewer'} patients started therapy."),
        "push": (f"Sales / marketing push{at}",
                 f"Field activity {'generated extra referrals' if up else 'did not lift referrals'}; incremental referrals tend to convert slightly worse."),
        "persistence": (f"Patient retention {'improved' if up else 'worsened'}{at}",
                        f"Patients {'stayed on therapy longer and refilled sooner' if up else 'dropped off therapy sooner or refilled later'}."),
        "intake_delay": (f"Intake processing delays{at}",
                         "Slower intake (longer referral-to-first-fill turnaround) made some patients abandon before their first fill and pushed others' start dates later."),
        "pa_policy": (f"Prior-authorisation tightened{at}",
                      f"{payer or 'The payer'} required PA more often and approved fewer, so fewer referrals for {drug or 'the drug'} turned into patients."),
        "ds_mandate": (f"90-day supply mandate{at}",
                       "Refills moved from 30-day to 90-day supplies, so each fill carries more therapy and volume is pulled forward."),
        "biosimilar": (f"Biosimilar switch{at}",
                       "Patients moved between the originator and its biosimilar. Switching within the molecule nets out; only patients lost to (or won from) competitors show here."),
        "new_drug": (f"New drug added{at}",
                     f"We began dispensing {drug or 'a new drug'} (e.g. a limited-distribution win), adding new patients."),
        "copay_shock": (f"Copay card restriction & out-of-pocket spike{at}",
                        f"Patients at {payer or 'the payer'} lost copay-card coverage on {drug or 'the drug'} and net out-of-pocket costs jumped +$50/fill, increasing first-fill abandonment and mid-year refill drop-off."),
    }.get(etype, (f"{str(etype).replace('_', ' ').capitalize()}{at}", ""))
    return t


def describe_unexplained(label):
    m = re.match(r"Unexplained:\s*(\w+)=(.+?)\s+(\w+)\s+([+-]?\d+%)\s+from\s+(\S+)", str(label))
    if not m:
        return str(label).replace("Unexplained: ", ""), "A step change the data shows clearly but no logged event explains."
    dim, val, kpi, pct, when = m.groups()
    dimtxt = {"practice": "", "region": " region", "payer": " patients", "drug": "", "source": " referrals"}.get(dim, "")
    up = not pct.startswith("-")
    when_txt = pd.Timestamp(when).strftime("%b %Y") if re.match(r"\d{4}-\d{2}", when) else when
    head = f"{kpi.capitalize()} from {val}{dimtxt} {'rose' if up else 'fell'} {pct.lstrip('+-')} (from {when_txt})"
    return head, "Found in the data but missing from the change log. Someone should find out what happened here."


def fmt(v):
    return f"{v:+,.0f}"


def month(d):
    try:
        return pd.Timestamp(d).strftime("%b %Y")
    except Exception:
        return ""


# ----------------------------------------------------------------------------- build driver list
def build_drivers(wdir, T):
    reg = pd.read_csv(os.path.join(wdir, "change_register.csv"))
    reg_by = {r.event_id: r._asdict() for r in reg.itertuples()}
    v2 = pd.read_csv(os.path.join(wdir, "v2_estimates.csv")).set_index("event_id")
    rows = []
    for r in T.itertuples():
        if r.tier == "Residual":
            continue
        ids = str(r.events).split("+")
        est = float(r.eq28)
        if r.tier.startswith("Unexplained"):
            head, why = describe_unexplained(r.driver)
            start = v2.loc[ids[0], "est_start"] if ids[0] in v2.index else ""
            end = ""
        else:
            parts = [describe(reg_by[i]["event_type_logged"], reg_by[i], est) for i in ids if i in reg_by]
            head = " + ".join(p[0] for p in parts)
            why = " ".join(p[1] for p in parts)
            start = min(str(v2.loc[i, "est_start"]) for i in ids if i in v2.index)
            ends = [reg_by[i].get("end_date") for i in ids if i in reg_by and isinstance(reg_by[i].get("end_date"), str)]
            end = ends[0] if ends else ""
        measurable = "istinguishable" not in r.tier
        conf, col = CONFIDENCE.get(r.tier.replace(" (not distinguishable)", ""), ("No measurable effect", GREY))
        if not measurable:
            conf, col = "No measurable effect", GREY
        rows.append(dict(events=r.events, headline=head, why=why, start=start, end=end, impact=est, lo=r.lo95, hi=r.hi95,
                         tier=r.tier, confidence=conf, conf_color=col, measurable=measurable,
                         unexplained=r.tier.startswith("Unexplained")))
    D = pd.DataFrame(rows)
    D = D.reindex(D.impact.abs().sort_values(ascending=False).index).reset_index(drop=True)
    return D


# ----------------------------------------------------------------------------- charts
def _png(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return b.getvalue()


def bridge_chart(header, D, resid):
    M = D[D.measurable].copy()
    labels = ["2024 volume", "Underlying growth\n(not tied to a specific event)"] + \
             [f"{i + 1}. {h if len(h) <= 48 else h[:46] + '…'}" for i, h in enumerate(M.headline)] + ["2025 volume"]
    vals = [resid] + M.impact.tolist()
    cols = [GREY] + [AMBER if u else (GREEN if v > 0 else RED) for u, v in zip(M.unexplained, M.impact)]
    v24, v25 = header["volume_2024"], header["volume_2025"]
    run, path = v24, []
    for v in vals:
        path.append((run, v))
        run += v
    lo = min([v24, v25] + [a for a, _ in path] + [a + b for a, b in path]) * 0.985
    hi = max([v24, v25] + [a for a, _ in path] + [a + b for a, b in path]) * 1.01
    fig, ax = plt.subplots(figsize=(11, 0.55 * len(labels) + 1.2))
    ax.barh(0, v24 - lo, left=lo, color=NAVY)
    ax.text(v24, 0, f"  {v24:,}", va="center", fontsize=9, fontweight="bold")
    for i, ((left, v), c) in enumerate(zip(path, cols), start=1):
        ax.barh(i, v, left=left, color=c)
        ax.text(left + v, i, f"  {fmt(v)}  " if v >= 0 else f"{fmt(v)}  ", va="center", ha="left" if v >= 0 else "right", fontsize=9)
        if i < len(path):
            ax.plot([left + v, left + v], [i + 0.4, i + 0.6], color="#888", lw=0.8)
    n = len(labels) - 1
    ax.barh(n, v25 - lo, left=lo, color=NAVY)
    ax.text(v25, n, f"  {v25:,}", va="center", fontsize=9, fontweight="bold")
    ax.set_yticks(range(len(labels)), labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(lo, hi + (hi - lo) * 0.08)
    ax.set_xlabel("Volume (28-day equivalent fills)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", alpha=0.25)
    ax.set_title(f"How we got from {v24:,} to {v25:,} ({header['change_pct']:+}%)", fontsize=12, loc="left")
    return fig


def monthly_chart(wdir, D):
    fill = pd.read_parquet(os.path.join(wdir, "fact_fill.parquet"), columns=["fill_date", "eq28"])
    fill["y"], fill["m"] = fill.fill_date.dt.year, fill.fill_date.dt.month
    g = fill[fill.y.isin([2024, 2025])].groupby(["y", "m"]).eq28.sum().unstack(0)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(g.index, g[2024], color=GREY, lw=2, marker="o", ms=4, label="2024")
    ax.plot(g.index, g[2025], color=NAVY, lw=2.5, marker="o", ms=4, label="2025")
    ax.fill_between(g.index, g[2024], g[2025], where=g[2025] >= g[2024], color=GREEN, alpha=0.12, interpolate=True)
    ax.fill_between(g.index, g[2024], g[2025], where=g[2025] < g[2024], color=RED, alpha=0.12, interpolate=True)
    M = D[D.measurable].reset_index(drop=True)
    ymin, ymax = np.nanmin(g.values), np.nanmax(g.values)
    span = ymax - ymin
    ax.set_ylim(ymin - 0.08 * span, ymax + 0.30 * span)
    used = {}
    for i, r in M.iterrows():
        try:
            s = pd.Timestamp(r.start)
        except Exception:
            continue
        if s.year != 2025:
            continue
        x = s.month + (s.day - 1) / 31
        k = used.get(round(x), 0)
        used[round(x)] = k + 1
        c = AMBER if r.unexplained else (GREEN if r.impact > 0 else RED)
        ax.axvline(x, color=c, ls="--", lw=1, alpha=0.7)
        ax.annotate(str(i + 1), (x, ymax + (0.09 + 0.13 * k) * span), ha="center", va="center", fontsize=9, fontweight="bold", color="white",
                    bbox=dict(boxstyle="circle,pad=0.25", fc=c, ec="none"))
    ax.set_xticks(range(1, 13), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_ylabel("Monthly volume (28-day equivalents)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.25)
    ax.legend(loc="lower right", frameon=False)
    ax.set_title("Month by month: 2025 vs 2024 (numbered markers = when each driver started)", fontsize=12, loc="left", pad=12)
    return fig


# ----------------------------------------------------------------------------- html
CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;margin:0;background:#f5f6f8;color:#1f2933}
.wrap{max-width:1100px;margin:0 auto;padding:32px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:36px 0 12px} .sub{color:#5f6b7a;margin-bottom:24px}
.card{background:#fff;border-radius:10px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:16px}
.headline{font-size:17px;line-height:1.6}
.kpis{display:flex;gap:16px;margin:16px 0}
.kpi{flex:1;background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.kpi .v{font-size:24px;font-weight:600} .kpi .l{color:#5f6b7a;font-size:13px}
.driver{display:flex;gap:16px;align-items:flex-start;border-left:5px solid #ccc}
.num{flex:0 0 30px;height:30px;border-radius:50%;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center}
.dbody{flex:1} .dhead{font-size:16px;font-weight:600} .why{color:#3e4c59;margin:4px 0 6px}
.meta{font-size:13px;color:#5f6b7a} .imp{flex:0 0 150px;text-align:right}
.imp .v{font-size:22px;font-weight:700} .pill{display:inline-block;padding:2px 10px;border-radius:12px;color:#fff;font-size:12px;margin-top:4px}
img{max-width:100%} ul.plain{margin:6px 0;padding-left:20px} ul.plain li{margin:4px 0}
details{margin-top:12px} summary{cursor:pointer;font-weight:600;font-size:16px;padding:6px 0}
table.t{border-collapse:collapse;font-size:12.5px;margin:8px 0 20px;background:#fff} .t th,.t td{padding:4px 9px;border-bottom:1px solid #e3e6ea;text-align:left}
.t th{background:#eef1f5} .note{font-size:13px;color:#5f6b7a}
"""


def build_html(wdir, outdir, header, T, u=None, stats=None, runtime=None, tech_imgs=None):
    import granular_drilldown
    D = build_drivers(wdir, T)
    _, P = granular_drilldown.analyze_pockets(wdir, top_n=6)
    P.drop(columns=["bullets"]).to_csv(os.path.join(outdir, "granular_pocket_drilldown.csv"), index=False)
    resid = float(T.loc[T.tier == "Residual", "eq28"].iloc[0])
    M = D[D.measurable].reset_index(drop=True)
    NM = D[~D.measurable]
    imgs = {"bridge.png": _png(bridge_chart(header, D, resid)), "monthly.png": _png(monthly_chart(wdir, D))}
    for n, b in imgs.items():
        open(os.path.join(outdir, n), "wb").write(b)
    img = lambda b: f'<img src="data:image/png;base64,{base64.b64encode(b).decode()}">'

    chg, pct = header["change"], header["change_pct"]
    pos = M[M.impact > 0].sort_values("impact", ascending=False)
    neg = M[M.impact < 0].sort_values("impact")
    events_net = M.impact.sum()

    def short(r):
        return f"<b>{r.headline[0].lower() + r.headline[1:]}</b> ({fmt(r.impact)}, from {month(r.start)})"
    sent = [f"Volume {'grew' if chg >= 0 else 'fell'} <b>{abs(pct)}%</b> ({fmt(chg)}) from 2024 to 2025."]
    if len(pos):
        sent.append("The biggest boost came from " + short(pos.iloc[0]) +
                    (", followed by " + ", ".join(short(r) for r in pos.iloc[1:3].itertuples()) if len(pos) > 1 else "") + ".")
    if len(neg):
        sent.append("The biggest drags were " + " and ".join(short(r) for r in neg.iloc[:2].itertuples()) + ".")
    sent.append(f"Taken together, the specific events below account for <b>{fmt(events_net)}</b>. "
                f"The remaining <b>{fmt(resid)}</b> is underlying growth that isn't tied to any one event "
                f"(market growth and momentum from patients already on therapy).")
    unexp = M[M.unexplained]
    if len(unexp):
        sent.append(f"<b>{len(unexp)} change{'s' if len(unexp) > 1 else ''} found in the data {'are' if len(unexp) > 1 else 'is'} not in the change log</b> and should be investigated.")

    cards = []
    for i, r in M.iterrows():
        col = AMBER if r.unexplained else (GREEN if r.impact > 0 else RED)
        when = f"From {month(r.start)}" + (f" to {month(r.end)}" if r.end else "")
        rng = f"likely between {fmt(r.lo)} and {fmt(r.hi)}" if pd.notna(r.lo) else ""
        cards.append(f"""<div class="card driver" style="border-left-color:{col}">
  <div class="num" style="background:{col}">{i + 1}</div>
  <div class="dbody"><div class="dhead">{r.headline}</div><div class="why">{r.why}</div>
  <div class="meta">{when} · {rng} · {r.impact / header['volume_2024'] * 100:+.1f}% of 2024 volume</div></div>
  <div class="imp"><div class="v" style="color:{col}">{fmt(r.impact)}</div><span class="pill" style="background:{r.conf_color}">{r.confidence}</span></div>
</div>""")
    nm_html = "".join(f"<li><b>{r.headline}</b> (logged {month(r.start)}): no effect we can measure with confidence. Range {fmt(r.lo)} to {fmt(r.hi)} includes zero.</li>"
                      for r in NM.itertuples())

    pocket_cards = []
    for r in P.itertuples():
        col = GREEN if r.delta_eq28 >= 0 else RED
        b_html = "".join(f"<li>{b}</li>" for b in r.bullets)
        pocket_cards.append(f"""<div class="card driver" style="border-left-color:{col}">
  <div class="dbody">
    <div class="dhead">{r.region} Region · {r.drug} · {r.payer}
      <span class="meta" style="font-weight:400">&nbsp;({r.eq28_24:,.0f} → {r.eq28_25:,.0f} eq28)</span></div>
    <div class="meta" style="margin:4px 0 6px">LMDI funnel split: Referrals <b>{fmt(r.lmdi_referrals)}</b> · Conversion <b>{fmt(r.lmdi_conversion)}</b> · Retention <b>{fmt(r.lmdi_persistence)}</b> · Days supply <b>{fmt(r.lmdi_days_supply)}</b></div>
    <ul class="plain">{b_html}</ul>
  </div>
  <div class="imp"><div class="v" style="color:{col}">{fmt(r.delta_eq28)}</div><div class="meta">eq28 change</div></div>
</div>""")

    tech = ""
    if stats:
        stat_rows = "".join(f"<tr><td>{k}</td><td>{(f'{v:.1%}' if isinstance(v, float) else v)}</td></tr>" for k, v in stats.items())
        tech += f"<h3>How accurate was this? (simulation answer key)</h3><table class='t'>{stat_rows}</table>"
        if tech_imgs and "estimate_vs_truth.png" in tech_imgs:
            tech += img(tech_imgs["estimate_vs_truth.png"])
        if u is not None:
            tech += u[["event_ids", "types", "in_register", "true_impact", "est", "lo95", "hi95", "covered", "true_start", "est_start"]].round(1).to_html(index=False, border=0, classes="t", na_rep="")
    reg = pd.read_csv(os.path.join(wdir, "change_register.csv"))
    tech += "<h3>Model output (analyst view)</h3>" + T.round(1).to_html(index=False, border=0, classes="t", na_rep="")
    tech += "<h3>Change log used as input</h3>" + reg.to_html(index=False, border=0, classes="t", na_rep="")
    if runtime:
        tech += f"<p class='note'>Estimator runtime {runtime:.0f}s.</p>"

    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>What drove the volume change</title><style>{CSS}</style></head><body><div class="wrap">
<h1>What drove the change in specialty pharmacy volume?</h1>
<div class="sub">2024 vs 2025 · volume measured in 28-day equivalent fills, so a 90-day fill counts about 3× a 30-day fill</div>
<div class="kpis">
 <div class="kpi"><div class="l">2024 volume</div><div class="v">{header['volume_2024']:,}</div></div>
 <div class="kpi"><div class="l">2025 volume</div><div class="v">{header['volume_2025']:,}</div></div>
 <div class="kpi"><div class="l">Change</div><div class="v" style="color:{GREEN if chg >= 0 else RED}">{fmt(chg)} ({pct:+}%)</div></div>
 <div class="kpi"><div class="l">Explained by specific events</div><div class="v">{fmt(events_net)}</div></div>
</div>
<div class="card headline">{' '.join(sent)}</div>

<h2>The bridge from 2024 to 2025</h2>
<div class="card">{img(imgs['bridge.png'])}
<p class="note">Green = added volume, red = removed volume, amber = not in the change log (investigate), grey = underlying growth.</p></div>

<h2>The drivers, biggest first</h2>
{''.join(cards)}
{f'<div class="card"><b>Logged but no measurable effect</b><ul class="plain">{nm_html}</ul><p class="note">These are reported as zero rather than as a guess.</p></div>' if len(NM) else ''}

<h2>Where we lost or gained volume: pocket &amp; transaction drill-down (no change log needed)</h2>
<div class="sub">Automatically drills into the top Region × Drug × Payer pockets using only raw referral, claim reject, prescriber NPI, and copay/OOP columns</div>
{''.join(pocket_cards)}

<h2>When it happened</h2>
<div class="card">{img(imgs['monthly.png'])}</div>

<h2>How to read the confidence labels</h2>
<div class="card"><ul class="plain">
<li><b>High confidence</b>: the effect is clearly different from zero (99.9% level). In 90 simulated tests this label was never given to a fake event.</li>
<li><b>Likely</b>: probably real (95% level) but not certain.</li>
<li><b>Combined effect</b>: two logged events overlap so closely that the data can't split them; reported as one number.</li>
<li><b>Not in change log: investigate</b>: a clear change in the data that no logged event explains.</li>
<li><b>No measurable effect</b>: logged, but the data can't distinguish its impact from zero.</li>
<li>The "likely between" range is a 95% interval: the true impact falls inside it about 95% of the time.</li>
</ul></div>

<details><summary>Technical appendix (for analysts)</summary><div class="card">{tech}</div></details>
</div></body></html>"""
    open(os.path.join(outdir, "report.html"), "w", encoding="utf-8").write(html)
    return D
