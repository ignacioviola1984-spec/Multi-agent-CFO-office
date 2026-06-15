# Case study — the credit operating model on the REAL LendingClub loan book

The credit operating model run end to end on the **full, real LendingClub dataset**
(`accepted_2007_to_2018Q4.csv`, **2,260,701 loans**; `rejected_2007_to_2018Q4.csv`,
27,648,741 applications). Every figure below was computed in code by
[`orchestration/credit_core.py`](orchestration/credit_core.py); the agents only
narrate. The data is not committed (it is ~3.4 GB, gitignored); this is the record
of the run. Engine + agents: [`OPERATING-MODEL`-style staged model](cfo-office/credit_stages.py).

> Not a sample, not a toy: the real public LendingClub book, ~2.26M loans, read in a
> single streaming pass (~2 minutes, ~80 MB of memory), benchmarked against
> LendingClub's own SEC filings.

## What the model produced (real numbers)

| | |
|---|---|
| Loans / applications | **2,260,701 funded** / 27,648,741 requested (**7.56% approval**) |
| Originations | **$34.0 billion** (avg loan $15,041, WAIR **13.38%**) |
| Charge-off rate (matured) | **19.98%** — **$3.0 billion** of charged-off principal |
| Expected loss (modeled proxy) | **$1.59 billion (16.75%** of the $9.51 bn on-book balance) |
| Delinquency | 3.76% (34,292 loans) |
| Interest income | $5.50 billion (realized yield **16.16%**, take rate 3.63%) |

**PD by grade — the validation that it's real credit analytics, not a demo:**

```
A: 6.04%   B: 13.4%   C: 22.4%   D: 30.4%   E: 38.4%   F: 45.1%   G: 49.67%
```

A clean, monotonic default-probability gradient from A to G, computed on 2.26M real
loans. LGD sits at ~88–90% across grades (low recoveries) — so loss is driven by
default frequency, with little recovery cushion. This is the kind of structural read
a credit risk team actually needs.

## Benchmarked against LendingClub's own SEC filings

The Public Benchmark agent reconciled the computed originations against LendingClub's
**reported 10-K / 8-K loan-origination figures** (SEC, Exhibit 99.1):

| Year | Computed (dataset) | LendingClub reported | Variance |
|---|---|---|---|
| 2016 | $6.40 bn | $8.66 bn | **−26.1%** |
| 2017 | $6.58 bn | $8.99 bn | **−26.7%** |
| 2018 | $7.94 bn | $10.88 bn | **−27.1%** |

The variance is **consistent (~−27%) across all three years** — the signature of a
*systematic* difference, not error. The Variance & Explainability agent reads it as
the public research dataset being a **stable ~73% subset** of total reported
originations (most likely the exclusion of a whole-loan / specialty channel) — a real
reconciliation item, explainable, not noise. Only originations is benchmarked: it is
the metric comparable to the filings (charge-off here is cohort-lifetime, not the
10-K's annual net rate; interest income is loan cash flows, not GAAP net revenue).

## Governance: 9 stages, 9 domain-expert sign-offs, one CFO gate

The close ran as the staged operating model — each stage a maker agent + a
deterministic control + the domain expert's sign-off:

`Source ingestion → Data quality → Source traceability → Loan portfolio →
Credit risk → Revenue & unit economics → Public benchmark → Variance & explainability
→ Model risk` → **CFO final sign-off**.

- **Data quality on real data:** 2 pass, **4 warn, 0 fail** → the hard gate passed.
  The warnings are LendingClub's **33 trailing junk rows** ("Total amount funded in
  policy code…") — 33 of 2.26M, correctly flagged and kept out of the analytics.
- **Escalations to the CFO:** charge-off 19.98% (HIGH), expected loss 16.75% (HIGH),
  the three benchmark variances (MEDIUM), the data-quality warnings (MEDIUM), and the
  proxy-reliance disclosure (MEDIUM). Each owned by a single function.
- **60-event audit trail**; every number reconciled to the engine.

## The board narrative the CFO agent wrote (verbatim excerpt)

> *"Originations for the period totaled $34.0 billion across 2,260,701 loans at a
> weighted-average rate of 13.4%… the board should note a documented reconciliation
> gap against LendingClub's SEC-filed 10-K/8-K originations for 2016–2018, where our
> computed figures trail reported volumes by 26.1% to 27.1% in each year — a gap that
> is consistent in direction… most likely the systematic exclusion of a whole-loan or
> specialty origination channel rather than random noise…*
>
> *The charge-off rate on matured loans stands at 20.0%, representing $3.0 billion in
> charged-off principal… The expected loss of $1,592,681,124 (16.75% of the $9.51
> billion on-book outstanding balance)… is a documented modeled proxy… not a booked,
> GAAP-compliant, or audited provision figure, nor does it satisfy IFRS 9 / CECL
> staging requirements…"*

…plus three prioritized actions (tighten underwriting, quarantine the 33 corrupted
records, reconcile the originations gap and replace proxy losses with disclosed
figures).

## Honest limitations (stated by the model itself)

- Expected loss and unit-economics figures are **documented modeled proxies**
  (PD × LGD; a grade-based origination-fee proxy), not booked/audited results.
- No macroeconomic overlay and no forward-looking IFRS 9 / CECL staging.
- Default/late loans are treated as on-book exposures (outstanding × PD × LGD), which
  the model flags as likely understating near-term risk.

## Why this matters

This is the operating model — built and adversarially reviewed on synthetic data —
pointed at a **real company's full loan book** and **benchmarked against that
company's own SEC filings**, end to end, with deterministic controls and a human
accountable for each function. Built by **Ignacio Viola** — 17 years in senior
finance, now building the systems. See [`OFFERING.md`](OFFERING.md).
