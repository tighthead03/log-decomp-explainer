# Specialty pharmacy volume-change simulation kit

A synthetic specialty pharmacy (2023–2025) with ten planted events of known cause and size, mock NCPDP messages generated from the same simulation, and an evaluation harness that scores how well a two-layer explanation pipeline recovers what happened.

Everything is synthetic. Payers, BINs/PCNs, NDCs, patients, practices and prescribers are fictional. Prescriber NPIs start with 9, a prefix that is never issued. Drug names are used only as recognisable labels. Amounts are mock USD.

## Folder map

| Path | What it is |
|---|---|
| `data/` | Analytic tables (Parquet and CSV.gz), the change register, and ground truth |
| `ncpdp/` | Mock SCRIPT 2017071 and Telecom D.0 messages (JSONL.gz), plus readable samples |
| `eval/` | Evaluation outputs: scorecard, break log, waterfalls, exec attribution, NCPDP reconciliation |
| `src/` | Simulator, dataset builder, NCPDP renderer and parser, evaluation pipeline |
| `EVALUATION.md` | Results and what they mean for the production design |

## Rebuild

```bash
cd src
python3 build_dataset.py ../data [seed]       # ~4 min incl. 10 counterfactual runs
python3 render_ncpdp.py ../data ../ncpdp       # ~1.5 min
python3 parse_ncpdp.py ../data ../ncpdp        # round-trip reconciliation, exits non-zero on mismatch
python3 eval_pipeline.py ../data ../eval       # ~5 s
```
Requires pandas, numpy, pyarrow, ruptures.

## Volume measure

`eq28` = days_supply / 28 per fill (28-day equivalents). Units are useless across drugs (60 Otezla tablets vs 1 Skyrizi pen). Raw fill counts are also misleading: a 90-day mandate cuts fills by two thirds while delivering the same therapy.

## Tables (`data/`)

**fact_referral** (one row per referral, 2023–2025): `referral_id, patient_id, referral_date, drug, ndc, payer, region, referral_source (eRx/Fax/Portal/Hub), prescriber_npi, prescriber_specialty, practice, intake_delay_days, first_reject_code (75/70/MR/76 or null), pa_outcome, tat_days, converted, lost_reason, first_fill_date`. `_hidden_marginal_flag` marks the incremental Skyrizi referrals. It's there for diagnosis only and should be dropped before analysis.

**fact_fill** (one row per dispensed fill): `referral_id, patient_id, fill_number (0 = start), fill_date, drug, ndc, payer, region, referral_source, prescriber_npi, days_supply, units, eq28`.

**fact_claim** (one row per Telecom transaction): `claim_id, rx_number, fill_number, date_of_service, txn_code (B1/B2), status (P/R/A), reject_code, reject_description, claim_context (intake / refill / reauth / refill too soon / reversal), days_supply, units, ingredient_cost_submitted, patient_pay_amount, total_amount_paid` plus keys.

**fact_pa**: `pa_id, referral_id, patient_id, pa_context (initial/reauth), request_date, decision_date, pa_outcome, denial_reason, drug, payer`.

**ops_intake_daily**: `date, referrals, avg_intake_delay, intake_fte`. This is the mechanism data behind the staffing event.

**dim_drug, dim_payer, dim_prescriber, dim_patient**: reference data.

**change_register.csv**: what the business logged, with scope (drug/payer/region/source), the business hypothesis, the KPIs it expects to move, and an owner. It deliberately contains one decoy (E09, no real effect) and omits one real event (E10).

**ground_truth_events.csv**: the answer key. True impact on 2025 eq28 for each event, computed as the actual run minus a run with only that event switched off (common random numbers). Do not let the pipeline read it.

## Planted events

| ID | Event | Start | Mechanism | In register |
|---|---|---|---|---|
| E01 | Keystone Rx step-therapy PA on Dupixent | Mar-25 | PA required 40%→85%, approval 82%→58% | Yes |
| E02 | Ustekinumab biosimilar launch | Feb-25 | 55% of Stelara demand switches; 82% of that comes to us as biosimilar | Yes |
| E03 | LDD win: Velarix | May-25 | New drug, 3-month ramp | Yes |
| E04 | South territory rep vacancies | Jun-25 | South referrals −22% | Yes |
| E05 | Intake staffing shortage | Aug–Oct-25 | Intake delay +5 days, so more abandonment | Yes |
| E06 | Refill reminder touch cascade | Apr-25 | Discontinuation hazard −30%, refill gap 5→2 days | Yes |
| E07 | Northstar 90-day supply mandate | Jul-25 | Refills on ≤30-day drugs move to 84/90 days | Yes |
| E08 | Skyrizi field marketing push | Apr-25 | Referrals +25%, but incremental referrals convert worse (linked factors) | Yes |
| E09 | Fax OCR go-live | May-25 | None (decoy) | Yes |
| E10 | Lakeside Dermatology (35 NPIs) moves to a competitor | Sep-25 | Their referrals go to zero | **No** |

Seasonal confounders built in: referral seasonality, January PA re-authorisations (reject 75, some denials ending therapy), and +4% underlying annual referral growth.

## NCPDP messages (`ncpdp/`)

| File | Content | Count |
|---|---|---|
| `script_newrx.jsonl.gz` | SCRIPT 2017071 NewRx for every eRx referral | 26,080 |
| `script_epa.jsonl.gz` | PARequest / PAResponse pairs (initial and re-auth) | 30,241 pairs |
| `telecom_d0.jsonl.gz` | B1 claim and B2 reversal request/response | 450,067 |
| `samples/` | Pretty examples of each message type and reject code | |

Simplifications, so the messages stay readable and easy to parse:
- Telecom fields are rendered as `<field ID><value>`, separated by `|`, one segment per line (AM01 patient, AM03 prescriber, AM04 insurance, AM07 claim, AM11 pricing, AM20–AM23 response). On the wire, D.0 uses 0x1C/0x1D/0x1E separators. Swap the delimiters and you're close to wire format.
- Amounts are plain cents, not signed overpunch. Quantity (E7) uses D.0's implied 3 decimals.
- XML follows SCRIPT 2017071 element names but has not been validated against the XSD.

`parse_ncpdp.py` rebuilds claims, fills, eRx referrals and ePA outcomes from the messages alone. It reconciles exactly to the fact tables on all 14 checks (`eval/ncpdp_reconciliation.csv`), so evaluation results carry over to a message-sourced pipeline.
