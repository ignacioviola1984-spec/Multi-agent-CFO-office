# AI Finance Portfolio

Finance AI engineering projects: live API integration, a custom MCP
connector for a multi-entity finance system, a multi-agent month-end close
model with reliability controls, and a multi-agent CFO office that runs the
close over a shared, auditable state. The deterministic numbers are also
validated against real public filings: they tie **17 of 17** to dLocal
(NASDAQ: DLO) reported FY2024 and FY2025 figures, statement-level, public data
only. Built by a finance operator with 17 years of experience, now building the
systems.

> **Validated against real public filings:** the deterministic statement-level math
> now ties **17 of 17** figures to dLocal (NASDAQ: DLO) reported FY2024/FY2025
> consolidated numbers (IFRS, USD), regenerated from dLocal public SEC filings and
> diffed against an SEC-derived answer key by a fail-closed, read-only auditor (17
> PASS, 0 FAIL). Reproducible in two commands, with no LLM and no API keys. This
> validates the statement-level math against a real company's reported financials;
> it does not prove the transaction-level agents or multi-entity consolidation,
> which no public company discloses. Public data only; dLocal is not affiliated
> with this project and did not endorse or review it. Full evidence and boundaries:
> [`test-dlocal/AUDIT_EVIDENCE.md`](test-dlocal/AUDIT_EVIDENCE.md).

> **See it run:** [`CASE-STUDY.md`](CASE-STUDY.md) walks a month-end close end to end
> on synthetic data (every figure computed in code). **Work with me:** [`OFFERING.md`](OFFERING.md).

## Agentic architecture

A single CFO orchestrator coordinates the month-end close, the Order-to-Cash
control tower (a sub-orchestration), and the bounded self-improvement loop, all
under one governance layer: deterministic numbers, maker/checker sign-off, an
audit trail, and a hard gate that blocks reporting.

![Agentic architecture: a single CFO orchestrator coordinating the month-end close, the Order-to-Cash control tower (a sub-orchestration), and the bounded self-improvement loop under one governance layer of deterministic numbers, maker/checker sign-off, an audit trail, and a hard gate that blocks reporting.](docs/agentic-architecture.png)

## Projects

### Finance MCP Connector (`finance-mcp/`)
A Model Context Protocol server that exposes the finance system of a
multi-entity SaaS (6 legal entities, 6 currencies) as callable tools:
consolidated P&L, balance sheet, AR aging, and cash position, with
multi-currency consolidation at period-close FX. Ships with a Python MCP
client that drives the server over the protocol, plus input validation and
a deliberately read-only surface. Details: [`finance-mcp/README.md`](finance-mcp/README.md).

**Stack:** Python, MCP (FastMCP), stdio transport, multi-entity consolidation.

### Multi-Agent Close & Reporting Model (`orchestration/`)
The AI Finance Operating Model v2: an orchestrator that coordinates
specialized sub-agents (close review, cash forecast, reporting) and adds a
reliability layer, deterministic checks between stages, a timestamped audit
trail, severity-based escalation, and a human-in-the-loop approval gate
before any figure reaches the board. Details:
[`orchestration/README.md`](orchestration/README.md).

**Stack:** Python, Anthropic API, agent patterns (chaining, routing,
sub-agents), audit trail, human-in-the-loop controls.

### CFO Office — multi-agent finance department over shared state (`cfo-office/`)
The operating model evolved into a full CFO office: **eight specialist agents**
— Controller, Treasury, Administration (Accounts Receivable / Accounts Payable /
Tax), Accounting & Reporting (the close and the three financial statements),
FP&A, Strategic Finance, Internal Controls, and an **independent Audit** — that
communicate through a shared state book (`CFOContext`), coordinated by a CFO
orchestrator. It runs the whole month-end loop — **record → close → report →
analyze → control → audit** — reconciles the agents' numbers with deterministic
cross-checks and consolidates escalations by severity without double-counting.
Governance is **two-tier (maker-checker), the way finance actually works**: each
function is signed off by its own domain expert (the Tax Manager signs tax, the
Treasurer signs treasury, …) as the first line, and the CFO gives a single
**final** consolidated sign-off — not a pseudo-review of every detail a generalist
can't own. Two of the agents are themselves sub-orchestrators (Administration,
Accounting & Reporting), giving a real two-level org. The books reconcile, the three financial statements
articulate, and the Audit agent re-derives the figures independently and issues
an opinion. Details: [`cfo-office/README.md`](cfo-office/README.md).

**Stack:** Python, Anthropic API, shared-state multi-agent coordination, two-level
orchestration, record-to-report (reconciliations + articulating financial
statements), budget-vs-actual variance, internal controls, independent audit,
cross-agent reconciliation, audit trail, **maker-checker review per function +
final CFO sign-off**.

> **Could this run in production, or is it a vision?** An honest, CFO-grade
> assessment — what's already production-grade, where the real gap is, and how it
> deploys today as a governed co-pilot: [`PRODUCTION-READINESS.md`](PRODUCTION-READINESS.md).

### Revenue Operations / Order-to-Cash Control Tower (`cfo-office/o2c/`)
A sub-orchestrator under the CFO Office that owns Revenue Operations and
Order-to-Cash end to end: **CRM → customer master → contracts → orders → billing →
invoices → revenue recognition → AR → collections → cash application → bank → GL /
reporting**, across multiple entities, regions, and currencies (USD, EUR, GBP, BRL,
MXN, ARS). It runs on **15 coherent datasets** with seeded exceptions, **10
deterministic maker agents** each signed off by a domain-expert checker, and **25
controls (15 hard + 10 soft)**. Finance numbers are computed in code; agents
diagnose, prioritize, explain, route, and draft, but never invent a figure. **Hard
controls block reporting** when CRM, billing, revenue recognition, cash
application, AR, or deferred revenue do not tie out, and the orchestrator exits
non-zero in CI. It needs no API key and runs from one command. It ships **two
periods on identical controls**: a problematic month (`2026-05`) that is blocked
with an adverse audit opinion, and a clean month (`2026-06`) where the source data
ties out and reporting is released - no thresholds relaxed, only the data differs.
The datasets are synthetic and illustrative (deterministic, with a known
seeded-exception ground truth), so the controls and tests have a verifiable answer
key. Details: [`cfo-office/o2c/README.md`](cfo-office/o2c/README.md) and the
[interview script](cfo-office/o2c/INTERVIEW_SCRIPT.md).

```
python run_o2c_control_tower.py            # single period
python run_o2c_control_tower.py --compare  # blocked 2026-05 vs clean 2026-06
```

**Stack:** Python, pandas, deterministic O2C engine, agentic workflow
orchestration, billing/revenue/cash/credit controls, collections risk scoring,
maker-checker HITL, audit trail, metrics framework, executive reporting.

### Finance Document Intelligence / RAG (`document-intelligence/`)
Semantic search, retrieval-augmented generation, and structured extraction
over finance documents (vendor contracts, expense policy): embeds and
chunks the documents, answers questions with source citations, and extracts
key contract terms into a table. Includes the judgment of when RAG helps and
when full context is better. Details:
[`document-intelligence/README.md`](document-intelligence/README.md).

**Stack:** Python, sentence-transformers / PyTorch, embeddings & cosine
similarity, RAG, structured extraction, Anthropic API.

### Evals, Guardrails & Reliability (`evals/`)
An evaluation harness that measures whether the agents are trustworthy:
regression on consolidated numbers, accuracy of contract extraction against a
known ground truth, and a grounding guardrail that checks the RAG refuses
out-of-scope questions instead of inventing. Exits non-zero on failure, so it
works as a regression test. Details: [`evals/README.md`](evals/README.md).

**Stack:** Python, evaluation harness, regression testing, grounding
guardrails. This is the reliability layer over the other projects.

### Bounded Self-Improvement (`self-improvement/`)
A layer that lets the AI **propose better parameter values and nothing else**:
parameter calibration under hard limits, not an agent that rewrites itself. A
challenger proposes a candidate **deterministically** (statistical calibration
over an outcomes window; the LLM only writes the human-readable rationale, never
picks the number), and a gate promotes it only if every condition holds: within
the registry's `[min, max]`, step cap, and cooldown; the deterministic eval suite
passes with **no regression**; a backtest shows the metric does not get worse; and
the parameter's registered human **owner** approves (maker-checker). The bounds
and the auto-adopt flag live in **code** (the `REGISTRY` and `AUTO_ADOPT_ENABLED`),
not in the mutable champion store, so the loop cannot widen a bound, change a step,
or enable auto-adopt; the default posture is propose-only. Only four scalar values
are ever calibrated and **no formula in `finance_core` can be touched**. Every
prior champion is kept (one-step rollback) and every decision lands in an
append-only audit trail. The bound tests prove the limits, including that the
system cannot change its own bounds or flip the auto-adopt flag. Details:
[`self-improvement/README.md`](self-improvement/README.md).

**Stack:** Python, deterministic calibration, champion/challenger over a bounded
registry, eval-gated promotion, maker-checker approval, append-only audit trail,
rollback.

### Web App / Live Demo (`webapp/`)
A Streamlit app that puts a usable interface over three of the projects so a
non-technical person can operate them: the FX agent, the operating model
(with the human-in-the-loop approval as a button), and document intelligence
(RAG + extraction). Run with `streamlit run app.py`. Details:
[`webapp/README.md`](webapp/README.md).

**Stack:** Python, Streamlit, reuses the project code via imports.

### API Integration (`api-integration/`)
Connecting finance workflows to live external data: a direct FX API client,
a multi-currency rates and conversion tool against official ECB data, and a
natural-language agent that calls an FX API as a tool. Details:
[`api-integration/README.md`](api-integration/README.md).

**Stack:** Python, `requests`, REST/JSON, Anthropic tool use, error handling.

## Diagrams (`diagrams/`)
Architecture diagrams for the agent tool-use flow, the MCP protocol, the
SDK's role, and the operating model. Index: [`diagrams/README.md`](diagrams/README.md).

## Design principle

In finance the number has to be right. Across every project, figures are
computed deterministically in code; the model routes, reasons, and writes
prose, but never produces a number on its own. Controls and a human approve
at the critical points.

That accuracy is checked three independent ways. (1) Real public-company
reconciliation: the deterministic statement-level numbers tie 17 of 17 to
dLocal reported FY2024/FY2025 financials, so the accuracy claim moves from
asserted to checked against reality (statement-level only; see the callout
above and [`test-dlocal/AUDIT_EVIDENCE.md`](test-dlocal/AUDIT_EVIDENCE.md)).
(2) Adversarial synthetic traps: run cold against four synthetic month-end
datasets with roughly 30 seeded errors each, the model catches the large
majority of the planted traps via planted-ID and flag-column scans; the
recurring gap is quantifying and classifying the adjustments (amounts,
P&L vs balance sheet, where credit losses sit), which still needed correction
against ground truth, which is exactly why a human checker stays in the loop.
(3) Independent second-model review: Codex independently reviewed the repo,
the test design, the local eval evidence, and the claim boundaries, external
to the model-output generation path. That is not a formal external or statutory
audit, a certification, or an assurance opinion, and is not a substitute for a
human auditor. The local eval harness passes 33/33 locally (Numbers 22/22,
Extraction 9/9, Grounding 2/2).

## Requirements

- Python 3
- `pip install requests anthropic python-dotenv mcp`
- An Anthropic API key in a local `.env` as `ANTHROPIC_API_KEY` (never committed)

## About

17 years in senior finance, now building AI systems for finance operations.
These projects run on synthetic data modeled on a multi-entity SaaS; the
architecture and accounting logic are built to point at production data. The
deterministic statement-level math has additionally been reconciled against a
real public company (dLocal, 17 of 17 figures from its public SEC filings); the
transaction-level agents and multi-entity consolidation remain on synthetic data,
since no public company discloses transaction-level detail. The exercise is
illustrative and uses public data only.

Available for **finance-transformation roles and advisory engagements** —
see [`OFFERING.md`](OFFERING.md).

## License

Source-available under the **PolyForm Noncommercial License 1.0.0** (see
[`LICENSE`](LICENSE)): you may read, run, study and evaluate the code for any
**noncommercial** purpose. **Commercial use and production implementations require
a separate license** — get in touch.
