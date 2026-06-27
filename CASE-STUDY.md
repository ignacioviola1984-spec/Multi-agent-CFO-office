# Case study — the CFO AI Office runs a month-end close

A walk-through of one saved run of the multi-agent CFO office, end to end,
on a synthetic SaaS company (**Lumen Inc.**) closing **May 2026**. Every figure
below was computed by code and produced by the system — nothing here is mocked up
for the page. The deterministic math is separately reconciled to a real public
company (dLocal, NASDAQ: DLO) from its public SEC filings: see
[Real-data validation](#real-data-validation-dlocal-nasdaq-dlo) below. The
interactive version is in [`cfo-demo/`](cfo-demo/); the operating model is in
[`OPERATING-MODEL.md`](OPERATING-MODEL.md) and
[`diagrams/07_operating_model_hitl_gates.svg`](diagrams/07_operating_model_hitl_gates.svg).

> **This is not a chatbot for finance.** AI agents draft, code-based controls
> validate, finance leaders approve, and the CFO owns the final consolidated sign-off.

## How it runs

The close runs as an explicit **8-stage operating model** with **two-tier
human-in-the-loop control (maker-checker)**. Each stage = an agent does the work
(the maker) → a deterministic control in code must hold (a hard gate) → the domain
expert signs off (the checker). A control failure blocks immediately; a sign-off
rejection reworks and then blocks the whole close. Only when all eight stages pass
does the CFO give a single **final consolidated sign-off**.

`record → close → report → analyze → control → audit → consolidate → CFO sign-off`

## What the system produced (real output from the run)

**The close is clean and tied out.** The AR and AP subledgers reconcile to the
general ledger with zero exceptions, the three financial statements articulate, and
the cash-flow statement foots to the actual change in cash.

**An independent audit agent re-derived the numbers** from the raw ledger and
subledger (not via the close) and issued an **unqualified opinion** — 6 procedures
re-performed, 0 exceptions.

**The controls have teeth — they caught something.** Of 5 internal-control checks,
4 passed and **1 raised an exception**: six disbursements at or above the
USD 25,000 authorization threshold, **USD 217,269** in total, flagged for documented
authorization review. That is the difference between a control that runs and a
control that *works*.

**Nine risk flags, consolidated by severity, each owned by exactly one agent** (no
double-counting):

| Severity | Flag |
|---|---|
| HIGH | Operating loss of **USD 756,823** — cost structure needs review (widened USD 86,655 MoM, USD 38,938 vs budget) |
| HIGH | Runway **9.9 months** (< 12): tight room to maneuver |
| HIGH | **97% of receivables overdue** — USD 1,109,564 across 54 invoices: collections risk |
| HIGH | **USD 416,764 in overdue payables** (33 bills, DPO 50d): supplier/operational risk |
| HIGH | **USD 118,496 in overdue tax** across 6 jurisdictions: compliance/penalty risk |
| HIGH | G&A overspend **+USD 20,935 (+6.0%)** vs plan |
| HIGH | **Burn multiple 11.6x** (benchmark ≤ 2): low capital efficiency |
| HIGH | Growth alone won't reach breakeven: a **61pp operating-margin gap** needs structural margin, not more volume |
| HIGH | Large disbursements pending authorization review (the USD 217,269 above) |

**The CFO agent auto-drafted the board pack** from the eight agents' inputs — every
number traceable to code. Excerpt:

> *"The May 2026 close is technically clean: financials articulate fully across all
> three statements, subledgers tie to GL with zero exceptions, and an unqualified
> audit opinion has been issued… operating loss of USD 756,823, widening USD 86,655
> month-on-month… a burn multiple of 11.6x against a ≤2.0x benchmark confirms the
> business is spending USD 11.60 for every USD 1.00 of net new ARR… this is the most
> urgent strategic problem on the agenda."*

…plus three prioritized actions (collections sprint on the overdue AR, a G&A/S&M
freeze and review, and enforcing the authorization control on large disbursements).

**Every step is logged:** a 68-event audit trail records each stage transition and
each sign-off (who, what they decided, any correction note, and the timestamp).

## What is deterministic vs. what the AI does

| Done by **code** (deterministic, auditable) | Done by the **AI agents** (Claude) |
|---|---|
| Every figure: P&L, balance sheet, cash flow, ratios, agings, variances | Reasoning over the numbers and writing the commentary |
| The reconciliations and the statement articulation | Drafting the board pack and the recommended actions |
| The control checks and the escalation rules | Explaining variances and anomalies in plain English |
| The cross-checks that reconcile the agents | — *the AI never produces a number on its own* |

## What it blocks (the governance value)

- A board pack built on an **un-reconciled** close — blocked by the close control.
- A close where the **statements don't foot** — blocked.
- An **integrity-control failure** (trial balance, FX, cutoff, duplicates) — blocks the close.
- An **adverse audit opinion** — blocks the close.
- A function reaching the CFO **without its domain expert's sign-off** — blocked.

## Reliability

An evaluation harness ([`evals/`](evals/)) runs as a regression test and exits
non-zero on failure: it **passes locally, 33/33 checks**, 22 on the consolidated
numbers, 9 on contract-extraction accuracy, and 2 grounding guardrails that confirm
the system **refuses** out-of-scope questions instead of inventing answers. (Local
eval, not third-party or externally verified.)

## Real-data validation: dLocal (NASDAQ: DLO)

The Lumen run above is synthetic. To check the deterministic math against reality,
the model was reconciled to a real public company using **only its public SEC
filings**: **dLocal** (NASDAQ: DLO), reported FY2024 and FY2025 consolidated numbers
(IFRS, USD).

A deterministic, dependency-free workflow regenerates **17 statement-level figures**
from dLocal public input CSVs, and a separate read-only auditor diffs them against an
SEC-derived answer key. **Result: 17 PASS, 0 FAIL, exit 0.** The 17 figures are
statement-level: P&L subtotals (gross profit, operating profit, profit before tax,
net income FY2025, net income FY2024), Adjusted EBITDA, balance-sheet section totals
(total assets FY2025 and FY2024, total liabilities, total equity), closing cash,
margins (gross, net, Adjusted EBITDA), and year-over-year growth (revenue, gross
profit, net income).

**Reproduce it in two commands**, with no LLM and no API keys:

```bash
python test-dlocal/run_dlocal_test.py
python test-dlocal/audit_dlocal_test.py
```

It is pure Python standard library, deterministic (identical bytes on re-run). The
auditor is read-only and **fails closed**: a wrong USD value fails, missing/extra/
duplicate keys fail, a non-numeric value fails. Full evidence and boundaries are in
[`test-dlocal/AUDIT_EVIDENCE.md`](test-dlocal/AUDIT_EVIDENCE.md).

**Dual-model review.** External to the model-output generation path, a second model
(Codex) independently reviewed the repo, the test design, the local eval evidence,
and the claim boundaries. "External" here means external to the preparer/model-output
generation path. It is **not** a formal external or statutory audit, a certification,
an assurance opinion, or a substitute for a human auditor.

**Scope boundary (read it, do not skip it).** This validates the deterministic
statement-level math against a real public company reported financials: the accuracy
and determinism claim moves from asserted to checked against reality. It does **not**
prove the entire operating model on real transaction-level data. No public company
discloses transaction-level subledgers, so the transaction-level AR/AP/tax agents and
the multi-entity, multi-currency consolidation shown above are **not** validated on
real data. Public data only, built solely from dLocal public SEC filings. dLocal is
not affiliated with this project and did not endorse, sponsor, or review it. No
non-public, internal, or confidential data was used. The exercise is illustrative.

## Three independent angles of assurance

Taken together, the demo stands on three independent checks plus a fail-closed,
read-only auditor:

| Angle | What it tests | Evidence |
|---|---|---|
| **Adversarial synthetic traps** | Detection | Run cold against four synthetic month-end datasets with roughly 30 seeded errors each; the large majority of seeded traps are caught via planted-ID and flag-column scans |
| **Real public-company reconciliation** | Accuracy | dLocal: 17 of 17 statement-level figures tie to reported financials |
| **Independent second-model review** | Dual-model | Codex review external to the model-output generation path |

On the synthetic stress tests, detection is strong; the recurring gap is quantifying
and classifying the adjustments (amounts, P&L-vs-balance-sheet, where credit losses
sit), which still needed correction against ground truth. That is exactly why a human
checker stays in the loop.

## Illustrative impact

*Illustrative, not measured on a client.* A month-end close and board pack of this
scope — pulling the numbers, reconciling subledgers, building three articulating
statements, running variance and controls, drafting commentary — typically absorbs
**many hours across several finance roles**. Here the agents draft it in minutes and
the experts spend their time **reviewing and deciding**, not assembling. The point
isn't to remove the humans — it's to move them from preparation to judgment, with a
full audit trail behind every step. Real time-savings are measured per client in a
pilot.

## Honest limitations

- The figures in this run are **synthetic** (a fictional SaaS, Lumen Inc.); the
  architecture is built to point at production data. The deterministic
  statement-level math is separately checked against a real public company (dLocal,
  17 of 17), but the transaction-level subledgers and multi-entity consolidation
  shown here are **not** validated on real data, because no public company discloses
  transaction-level data.
- The public demo **auto-approves** the human sign-offs (no reviewer is at the
  console when the snapshot is generated) and labels them as such — the
  maker-checker workflow, audit trail and block-on-reject are real; only the
  keystroke is simulated. Auto-approved is not a human sign-off.

---

Built by **Ignacio Viola** — 17 years in senior finance, now building the systems.
