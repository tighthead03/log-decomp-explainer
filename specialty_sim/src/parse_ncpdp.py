"""
Parse the mock NCPDP messages back into analytic tables and reconcile to the fact tables.
This is the ingestion path a production pipeline would use, so if it reconciles, the
evaluation results transfer to message-sourced data.
"""
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
import pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else "../data"
MSG = sys.argv[2] if len(sys.argv) > 2 else "../ncpdp"
NS = {"s": "http://www.ncpdp.org/schema/SCRIPT"}
payer = pd.read_parquet(f"{DATA}/dim_payer.parquet")
bin2payer = dict(zip(payer.bin, payer.payer))
ndc2drug = dict(zip(*pd.read_parquet(f"{DATA}/dim_drug.parquet")[["ndc", "drug"]].values.T))


def fields(seg_line):
    parts = seg_line.split("|")
    return parts[0], {p[:2]: p[2:] for p in parts[1:]}


# ----------------------------------------------------------------- Telecom
rows = []
with gzip.open(f"{MSG}/telecom_d0.jsonl.gz", "rt") as fh:
    for line in fh:
        m = json.loads(line)
        req = m["request"].split("\n")
        h = req[0].split("|")
        rec = dict(claim_id=m["claim_id"], bin=h[0], txn_code=h[2], date_of_service=h[7])
        for s in req[1:]:
            sid, f = fields(s)
            if sid == "AM07":
                rec.update(rx_number=int(f["D2"]), fill_number=int(f["D3"]), ndc=f.get("D7"),
                           units=int(f["E7"]) / 1000 if "E7" in f else None, days_supply=int(f["D5"]) if "D5" in f else None,
                           origin=f.get("DJ"))
            elif sid == "AM03":
                rec["prescriber_npi"] = f["DB"]
            elif sid == "AM04":
                rec["cardholder_id"] = f["C2"]
        for s in m["response"].split("\n")[1:]:
            sid, f = fields(s)
            if sid == "AM21":
                rec["status"] = f["AN"]
                rec["reject_code"] = f.get("FB")
            elif sid == "AM23":
                rec["total_amount_paid"] = int(f["F9"]) / 100
        rows.append(rec)
tel = pd.DataFrame(rows)
tel["payer"] = tel.bin.map(bin2payer)
tel["date_of_service"] = pd.to_datetime(tel.date_of_service)

# dispensed fills = paid B1 not followed by an accepted B2 on the same rx/fill
rev = tel[(tel.txn_code == "B2") & (tel.status == "A")][["rx_number", "fill_number"]].assign(reversed=True)
paid = tel[(tel.txn_code == "B1") & (tel.status == "P")].merge(rev, on=["rx_number", "fill_number"], how="left")
disp = paid[paid.reversed.isna()].copy()
disp["drug"] = disp.ndc.map(ndc2drug)
disp["eq28"] = disp.days_supply / 28
disp["month"] = disp.date_of_service.dt.to_period("M")

# ----------------------------------------------------------------- SCRIPT NewRx
nrx = []
with gzip.open(f"{MSG}/script_newrx.jsonl.gz", "rt") as fh:
    for line in fh:
        m = json.loads(line)
        root = ET.fromstring(m["xml"])
        nrx.append(dict(message_id=m["message_id"], sent=m["sent_time"][:10],
                        ndc=root.find(".//s:MedicationPrescribed/s:DrugCoded/s:ProductCode/s:Code", NS).text,
                        npi=root.find(".//s:Prescriber//s:NPI", NS).text,
                        bin=root.find(".//s:BenefitsCoordination//s:IINNumber", NS).text))
nrx = pd.DataFrame(nrx)
nrx["month"] = pd.to_datetime(nrx.sent).dt.to_period("M")

# ----------------------------------------------------------------- SCRIPT ePA
epa = {}
with gzip.open(f"{MSG}/script_epa.jsonl.gz", "rt") as fh:
    for line in fh:
        m = json.loads(line)
        root = ET.fromstring(m["xml"])
        ref = root.find(".//s:PAReferenceID", NS).text
        if m["type"] == "PAResponse":
            epa.setdefault(ref, {})["outcome"] = "Denied" if root.find(".//s:Denied", NS) is not None else (
                "Approved on appeal" if root.find(".//s:Approved/s:Note", NS) is not None else "Approved")
        else:
            epa.setdefault(ref, {})["requested"] = m["sent_time"][:10]
epa = pd.DataFrame.from_dict(epa, orient="index")

# ----------------------------------------------------------------- reconcile
fill = pd.read_parquet(f"{DATA}/fact_fill.parquet")
fill["month"] = fill.fill_date.dt.to_period("M")
fill["eq28"] = fill.days_supply / 28  # stored column is rounded to 4 dp
clm = pd.read_parquet(f"{DATA}/fact_claim.parquet")
ref = pd.read_parquet(f"{DATA}/fact_referral.parquet")
pa = pd.read_parquet(f"{DATA}/fact_pa.parquet")

checks = []
def check(name, a, b):
    ok = (a == b) if not isinstance(a, float) else abs(a - b) < 1e-6 * max(1, abs(b))
    checks.append(dict(check=name, from_messages=a, from_tables=b, match=bool(ok)))

check("Telecom transactions", len(tel), len(clm))
check("Rejected claims", int((tel.status == "R").sum()), int((clm.status == "R").sum()))
for code in ["75", "70", "MR", "76", "79"]:
    check(f"Reject {code}", int((tel.reject_code == code).sum()), int((clm.reject_code == code).sum()))
check("Dispensed fills", len(disp), len(fill))
check("EQ28 volume", round(disp.eq28.sum(), 3), round(fill.eq28.sum(), 3))
check("Monthly EQ28 max abs diff", float((disp.groupby("month").eq28.sum() - fill.groupby("month").eq28.sum()).abs().max()), 0.0)
check("Fills by payer identical", bool(disp.groupby("payer").size().equals(fill.groupby("payer").size())), True)
check("NewRx messages = eRx referrals", len(nrx), int((ref.referral_source == "eRx").sum()))
check("ePA requests", len(epa), len(pa))
check("ePA denials", int((epa.outcome == "Denied").sum()), int(((pa.pa_outcome == "Denied") & pa.decision_date.notna()).sum()))
out = pd.DataFrame(checks)
print(out.to_string(index=False))
out.to_csv(f"{DATA}/../eval/ncpdp_reconciliation.csv", index=False)
sys.exit(0 if out.match.all() else 1)
