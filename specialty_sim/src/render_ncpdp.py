"""
Render mock NCPDP messages from the dataset.

  SCRIPT 2017071 (XML):  NewRx for eRx referrals; PARequest / PAResponse for ePA (initial + re-auth)
  Telecom D.0:           B1 claim request/response and B2 reversal request/response

Readability simplifications (documented in README):
  * Telecom fields are rendered as <2-char field ID><value> separated by '|', one segment per line.
    On the wire D.0 uses 0x1C field, 0x1D group and 0x1E segment separators.
  * Amounts are plain cents, not signed-overpunch. Quantity uses D.0's implied 3 decimals.
  * XML follows SCRIPT 2017071 element naming but is not schema-validated.
All identifiers are fictional (prescriber NPIs start with 9, which is never issued).
"""
import gzip
import json
import os
import sys
from xml.sax.saxutils import escape as x
import pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else "../data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "../ncpdp"
os.makedirs(f"{OUT}/samples", exist_ok=True)

PHARMACY_NPI = "9876543210"
PHARMACY_NCPDP = "9912345"
VENDOR = "SIMRX00001"
ORIGIN = {"eRx": "3", "Portal": "3", "Fax": "4", "Hub": "4"}
SIG = {"Dupixent": "Inject 300 mg subcutaneously every 2 weeks", "Humira": "Inject 40 mg subcutaneously every 2 weeks",
       "Adalimumab biosimilar": "Inject 40 mg subcutaneously every 2 weeks", "Stelara": "Inject 90 mg subcutaneously every 8 weeks",
       "Ustekinumab biosimilar": "Inject 90 mg subcutaneously every 8 weeks", "Skyrizi": "Inject 150 mg subcutaneously every 12 weeks",
       "Rinvoq": "Take 1 tablet by mouth daily", "Cosentyx": "Inject 300 mg subcutaneously every 4 weeks",
       "Taltz": "Inject 80 mg subcutaneously every 4 weeks", "Otezla": "Take 1 tablet by mouth twice daily",
       "Enbrel": "Inject 50 mg subcutaneously weekly", "Velarix (mock LDD)": "Inject 1 pen subcutaneously every 4 weeks"}
ICD = {"Derm": ("L20.9", "Atopic dermatitis, unspecified"), "Rheum": ("M06.9", "Rheumatoid arthritis, unspecified"),
       "GI": ("K50.90", "Crohn's disease, unspecified"), "Allergy": ("J45.50", "Severe persistent asthma, uncomplicated")}

ref = pd.read_parquet(f"{DATA}/fact_referral.parquet")
clm = pd.read_parquet(f"{DATA}/fact_claim.parquet")
pa = pd.read_parquet(f"{DATA}/fact_pa.parquet")
pat = pd.read_parquet(f"{DATA}/dim_patient.parquet").set_index("patient_id")
pres = pd.read_parquet(f"{DATA}/dim_prescriber.parquet").set_index("prescriber_npi")
payer = pd.read_parquet(f"{DATA}/dim_payer.parquet").set_index("payer")
drug = pd.read_parquet(f"{DATA}/dim_drug.parquet").set_index("drug")
ref_all = ref.set_index("referral_id")
fill = pd.read_parquet(f"{DATA}/fact_fill.parquet", columns=["referral_id", "referral_source", "prescriber_npi"]).drop_duplicates("referral_id")
src_of = {**fill.set_index("referral_id").referral_source.to_dict(), **ref_all.referral_source.to_dict()}
npi_of = {**clm.drop_duplicates("referral_id").set_index("referral_id").prescriber_npi.to_dict(), **fill.set_index("referral_id").prescriber_npi.to_dict(), **ref_all.prescriber_npi.to_dict()}


def ymd(t):
    return pd.Timestamp(t).strftime("%Y%m%d")


def iso(t, hh="09:00:00"):
    return pd.Timestamp(t).strftime("%Y-%m-%d") + "T" + hh


def patient_xml(p):
    return (f"<Patient><HumanPatient><Identification><PatientAccountNumber>{p.name}</PatientAccountNumber></Identification>"
            f"<Name><LastName>{x(p.last_name)}</LastName><FirstName>{x(p.first_name)}</FirstName></Name>"
            f"<Gender>{p.gender}</Gender><DateOfBirth><Date>{pd.Timestamp(p.date_of_birth).date()}</Date></DateOfBirth>"
            f"</HumanPatient></Patient>")


def prescriber_xml(npi):
    pr = pres.loc[npi]
    return (f"<Prescriber><NonVeterinarian><Identification><NPI>{npi}</NPI></Identification>"
            f"<Specialty>{pr.specialty}</Specialty><PracticeLocation><BusinessName>{x(pr.practice)}</BusinessName></PracticeLocation>"
            f"<Name><LastName>{x(pr.last_name)}</LastName><FirstName>{x(pr.first_name)}</FirstName></Name>"
            f"</NonVeterinarian></Prescriber>")


def header(msg_id, sent, frm, to, rel=None):
    r = f"<RelatesToMessageID>{rel}</RelatesToMessageID>" if rel else ""
    return (f"<Header><To Qualifier=\"P\">{to}</To><From Qualifier=\"C\">{frm}</From><MessageID>{msg_id}</MessageID>{r}"
            f"<SentTime>{sent}</SentTime><SenderSoftware><SenderSoftwareDeveloper>SimEHR</SenderSoftwareDeveloper>"
            f"<SenderSoftwareProduct>MockScript</SenderSoftwareProduct><SenderSoftwareVersionRelease>2017071</SenderSoftwareVersionRelease>"
            f"</SenderSoftware></Header>")


NS = 'xmlns="http://www.ncpdp.org/schema/SCRIPT" DatatypesVersion="2017071" TransportVersion="2017071" TransactionDomain="SCRIPT" TransactionVersion="2017071" StructuresVersion="2017071" ECLVersion="2017071"'

# ----------------------------------------------------------------- NewRx
n_newrx = 0
with gzip.open(f"{OUT}/script_newrx.jsonl.gz", "wt") as fh:
    for r in ref[ref.referral_source == "eRx"].itertuples(index=False):
        p = pat.loc[r.patient_id]
        d = drug.loc[r.drug]
        icd = ICD[r.prescriber_specialty]
        mid = "NRX" + r.referral_id[1:]
        sent = iso(r.referral_date, "10:15:00")
        xml = (f"<Message {NS}>{header(mid, sent, r.prescriber_npi, PHARMACY_NCPDP)}<Body><NewRx>"
               f"<UrgencyIndicatorCode>X</UrgencyIndicatorCode>{patient_xml(p)}"
               f"<Pharmacy><Identification><NCPDPID>{PHARMACY_NCPDP}</NCPDPID><NPI>{PHARMACY_NPI}</NPI></Identification>"
               f"<BusinessName>Sim Specialty Pharmacy</BusinessName></Pharmacy>{prescriber_xml(r.prescriber_npi)}"
               f"<BenefitsCoordination><PayerIdentification><IINNumber>{payer.loc[r.payer].bin}</IINNumber>"
               f"<ProcessorIdentificationNumber>{payer.loc[r.payer].pcn}</ProcessorIdentificationNumber></PayerIdentification>"
               f"<PayerName>{x(r.payer)}</PayerName><CardholderID>{p.cardholder_id}</CardholderID><GroupID>{p.group_id}</GroupID>"
               f"</BenefitsCoordination><MedicationPrescribed><DrugDescription>{x(r.drug)}</DrugDescription>"
               f"<DrugCoded><ProductCode><Code>{r.ndc}</Code><Qualifier>ND</Qualifier></ProductCode></DrugCoded>"
               f"<Quantity><Value>{int(d.units_per_fill)}</Value><CodeListQualifier>38</CodeListQualifier>"
               f"<QuantityUnitOfMeasure><Code>C48542</Code></QuantityUnitOfMeasure></Quantity>"
               f"<DaysSupply>{int(d.standard_days_supply)}</DaysSupply><WrittenDate><Date>{pd.Timestamp(r.referral_date).date()}</Date></WrittenDate>"
               f"<Substitutions>0</Substitutions><NumberOfRefills>11</NumberOfRefills>"
               f"<Diagnosis><ClinicalInformationQualifier>1</ClinicalInformationQualifier><Primary><Code>{icd[0]}</Code>"
               f"<Qualifier>ABF</Qualifier><Description>{x(icd[1])}</Description></Primary></Diagnosis>"
               f"<Sig><SigText>{x(SIG[r.drug])}</SigText></Sig></MedicationPrescribed></NewRx></Body></Message>")
        fh.write(json.dumps({"message_id": mid, "type": "NewRx", "sent_time": sent, "xml": xml}) + "\n")
        if n_newrx == 0:
            open(f"{OUT}/samples/NewRx_example.xml", "w").write(xml)
        n_newrx += 1

# ----------------------------------------------------------------- ePA (PARequest / PAResponse)
n_pa = 0
with gzip.open(f"{OUT}/script_epa.jsonl.gz", "wt") as fh:
    for r in pa.itertuples(index=False):
        p = pat.loc[r.patient_id]
        npi = npi_of[r.referral_id]
        qid = "PAR" + r.pa_id[2:]
        rid = "PAS" + r.pa_id[2:]
        pending = pd.isna(r.decision_date)
        req_t = iso(r.request_date, "11:00:00")
        res_t = None if pending else iso(r.decision_date, "15:30:00")
        reason = "Initial therapy" if r.pa_context == "initial" else "Annual continuation of therapy"
        req = (f"<Message {NS}>{header(qid, req_t, npi, payer.loc[r.payer].bin)}<Body><PARequest>"
               f"<PAReferenceID>{r.pa_id}</PAReferenceID>{patient_xml(p)}{prescriber_xml(npi)}"
               f"<MedicationPrescribed><DrugDescription>{x(r.drug)}</DrugDescription><DrugCoded><ProductCode><Code>{r.ndc}</Code>"
               f"<Qualifier>ND</Qualifier></ProductCode></DrugCoded></MedicationPrescribed>"
               f"<PAReason>{reason}</PAReason><PayerName>{x(r.payer)}</PayerName></PARequest></Body></Message>")
        if pending:
            fh.write(json.dumps({"message_id": qid, "type": "PARequest", "sent_time": req_t, "referral_id": r.referral_id, "pa_context": r.pa_context, "xml": req}) + "\n")
            n_pa += 1
            continue
        if r.pa_outcome == "Denied":
            status = f"<Denied><DenialReason><ReasonCode>CC</ReasonCode><Description>{x(r.denial_reason or 'Criteria not met')}</Description></DenialReason></Denied>"
        else:
            status = (f"<Approved><AuthorizationNumber>AUTH{r.pa_id[2:]}</AuthorizationNumber><AuthorizationPeriod>"
                      f"<StartDate><Date>{pd.Timestamp(r.decision_date).date()}</Date></StartDate>"
                      f"<EndDate><Date>{(pd.Timestamp(r.decision_date) + pd.Timedelta(days=365)).date()}</Date></EndDate></AuthorizationPeriod>"
                      f"{'<Note>Approved on appeal</Note>' if r.pa_outcome == 'Approved on appeal' else ''}</Approved>")
        res = (f"<Message {NS}>{header(rid, res_t, payer.loc[r.payer].bin, npi, rel=qid)}<Body><PAResponse>"
               f"<PAReferenceID>{r.pa_id}</PAReferenceID>{patient_xml(p)}<Response>{status}</Response></PAResponse></Body></Message>")
        fh.write(json.dumps({"message_id": qid, "type": "PARequest", "sent_time": req_t, "referral_id": r.referral_id, "pa_context": r.pa_context, "xml": req}) + "\n")
        fh.write(json.dumps({"message_id": rid, "type": "PAResponse", "sent_time": res_t, "relates_to": qid, "xml": res}) + "\n")
        kind = "denied" if r.pa_outcome == "Denied" else "approved"
        if not os.path.exists(f"{OUT}/samples/PAResponse_{kind}_example.xml"):
            open(f"{OUT}/samples/PARequest_example.xml", "w").write(req)
            open(f"{OUT}/samples/PAResponse_{kind}_example.xml", "w").write(res)
        n_pa += 1

# ----------------------------------------------------------------- Telecom D.0
pay_bin = payer.bin.to_dict()
pay_pcn = payer.pcn.to_dict()
n_tel = 0
samples = {}
with gzip.open(f"{OUT}/telecom_d0.jsonl.gz", "wt") as fh:
    for c in clm.itertuples(index=False):
        p = pat.loc[c.patient_id]
        dos = ymd(c.date_of_service)
        hdr = f"{pay_bin[c.payer]}|D0|{c.txn_code}|{pay_pcn[c.payer]:<10}|1|01|{PHARMACY_NPI:<15}|{dos}|{VENDOR}"
        ins = f"AM04|C2{p.cardholder_id}|C1{p.group_id}|C301|C61"
        clmseg = f"AM07|EM1|D2{c.rx_number:012d}|D3{c.fill_number:02d}"
        if c.txn_code == "B1":
            ptseg = f"AM01|C4{ymd(p.date_of_birth)}|C5{'2' if p.gender == 'F' else '1'}|CA{p.first_name}|CB{p.last_name}"
            clmseg += (f"|E103|D7{c.ndc}|E7{int(c.units * 1000)}|D5{int(c.days_supply)}|D61|D80"
                       f"|DE{ymd(c.date_of_service)}|DF11|DJ{ORIGIN.get(src_of.get(c.referral_id, ''), '0')}")
            prs = f"AM03|EZ01|DB{c.prescriber_npi}"
            prc = f"AM11|D9{int(round(c.ingredient_cost_submitted * 100))}|DC1500|DU{int(round(c.ingredient_cost_submitted * 100)) + 1500}"
            request = "\n".join([hdr, ins, ptseg, clmseg, prs, prc])
        else:
            request = "\n".join([hdr, ins, clmseg])
        rhdr = f"D0|{c.txn_code}|1|A|01|{PHARMACY_NPI:<15}|{dos}"
        if c.status == "R":
            stat = f"AM21|ANR|FA1|FB{c.reject_code}"
            msg = f"AM20|F4{c.reject_description}"
            response = "\n".join([rhdr, msg, stat, f"AM22|EM1|D2{c.rx_number:012d}"])
        elif c.status == "P":
            stat = f"AM21|ANP|F3{c.claim_id}"
            pr = f"AM23|F5{int(round(c.patient_pay_amount * 100))}|F6{int(round(c.ingredient_cost_submitted * 93))}|F9{int(round(c.total_amount_paid * 100))}"
            response = "\n".join([rhdr, stat, f"AM22|EM1|D2{c.rx_number:012d}", pr])
        else:  # accepted reversal
            response = "\n".join([rhdr, "AM21|ANA", f"AM22|EM1|D2{c.rx_number:012d}"])
        fh.write(json.dumps({"claim_id": c.claim_id, "transmitted": iso(c.date_of_service, "14:00:00"), "request": request, "response": response}) + "\n")
        key = (c.txn_code, c.status, c.reject_code)
        if key not in samples:
            samples[key] = f"--- REQUEST ---\n{request}\n--- RESPONSE ---\n{response}\n"
        n_tel += 1

with open(f"{OUT}/samples/telecom_d0_examples.txt", "w") as fh:
    for k, v in samples.items():
        fh.write(f"===== {k[0]} status={k[1]} reject={k[2]} =====\n{v}\n")
print(f"NewRx {n_newrx:,}  ePA pairs {n_pa:,}  Telecom transactions {n_tel:,}")
