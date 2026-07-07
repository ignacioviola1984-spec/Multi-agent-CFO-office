# Production readiness — an honest assessment

> Could the CFO AI Office actually run at a real company, in production — or is
> it a vision of the future? Written by a finance operator, with the same
> skepticism a CFO or auditor would bring. No hype.

## Verdict

**It is not science fiction, and it is not plug-and-play.** What this repo
implements is a *correct reference architecture*: the patterns it uses already
run in production at leading finance teams today. The distance to a real
deployment is **known engineering plus scoping the AI's authority** — not a
research breakthrough.

The right mental model is a **co-pilot for the close**, not an autonomous CFO.

The core accuracy claim is now backed by reproducible evidence, not only
asserted. The deterministic numbers have been audited against a real public
company: feeding dLocal's (NASDAQ: DLO) reported FY2024 and FY2025 consolidated
figures through the workflow reproduces 17 statement-level numbers that tie to
the filings exactly (**17 PASS, 0 FAIL**). They have also been stress-tested
against adversarial synthetic data, four month-end datasets with roughly 30
seeded errors each. Both are statement-level checks, not a validation of the
entire operating model on real transaction-level data.

## What has actually been tested (the evidence)

So the claims above are not just asserted, here is what was actually run and what
each test proves (and, as importantly, what it does not). The boundaries are
deliberate: this is statement-level and analytical only, on public data only. The
full evidence and scope notes are in `test-dlocal/AUDIT_EVIDENCE.md`.

| Test | What it proves | What it does not prove |
|---|---|---|
| **Real-company reconciliation (dLocal, NASDAQ: DLO).** A dependency-free workflow regenerates 17 statement-level figures from dLocal public input CSVs; a read-only auditor diffs them against an SEC-derived answer key. Result: 17 PASS, 0 FAIL. | The deterministic numbers tie to a real public company's reported FY2024/FY2025 consolidated financials (IFRS, USD): P&L subtotals, Adjusted EBITDA, balance-sheet section totals, closing cash, margins, and year-over-year growth. Accuracy moves from asserted to checked. Reproducible in two commands, no LLM, no API keys. | Statement-level only. No public filer discloses transaction-level subledgers, so the transaction-level AR/AP/tax agents and the multi-entity / multi-currency consolidation are not validated here. |
| **Adversarial synthetic stress test.** The model was run cold against four synthetic month-end datasets with roughly 30 seeded errors each. | Detection is strong: the large majority of seeded traps are caught via planted-ID and flag-column scans. | The recurring gap is quantifying and classifying the adjustments (amounts, P&L vs balance sheet, where credit losses sit), which still needed correction against ground truth. The data is still synthetic. |
| **Local eval harness.** 33/33 pass (Numbers 22/22, Extraction 9/9, Grounding 2/2). | The grounding and number-handling checks pass locally on each run. | This is a local harness; it is not third-party or external verification. |
| **Dual-model AI-assisted review.** A second model (Codex) independently reviewed the repo, the test design, the local eval evidence, and the claim boundaries, external to the model-output generation path. | An independent second set of eyes, external to the preparer/output path, checked the design and the wording of the claims. | This is not a formal external or statutory audit, a certification, an assurance opinion, or a substitute for a human auditor. |

dLocal is not affiliated with this project and did not endorse, sponsor, or review
it. No non-public, internal, or confidential data was used; the exercise is
illustrative.

## What is already production-grade (the design choices)

These are not demo conveniences; they are how serious finance-AI must be built:

| Choice in this repo | Why it holds up in production |
|---|---|
| **Numbers computed in code; the LLM only narrates** | No hallucinated figures. Every number is deterministic and traceable. This is the core safety property, and it is now checked against reality: the deterministic math ties 17 of 17 statement-level figures to dLocal's reported FY2024/FY2025 filings (17 PASS, 0 FAIL). |
| **Read-only by default; the first write path is governed** | The safe posture is still the default. The one write capability (payment initiation, `payments/`) is deliberately fenced: agents are **propose-only**, a deterministic engine (no LLM) validates, a **registered human distinct from the proposer** approves, auto-execution is off behind a code-level flag, and settlement goes through a `PaymentRail` interface whose only implementation here is a **local SandboxRail** (a real bank/wallet rail is a not-implemented stub). No LLM is in the validation, approval, or execution path. The AI still never moves money on its own. |
| **Audit trail (append-only, timestamped)** | A governance and SOX requirement, present by design. |
| **Deterministic cross-checks / reconciliations as gates** | Real controls that catch inconsistency before it reaches the board. |
| **Single source of truth for every figure** | Agents cannot disagree on a number by construction. |
| **MCP as the data layer** | This is literally the production integration path — ERPs (NetSuite, SAP, QuickBooks) are exposing MCP. "Point at production data" is a connector swap in principle. |

## The real gap to production (where the work is)

1. **Data integration is ~80% of the project.** The hard part is never the
   agents — it is connecting the ERP, subledgers, bank feeds and warehouse;
   account mappings, intercompany, multi-GAAP, and messy real data. Two things
   are already proven, and one is not. The model has not only been run on pristine
   data: it was stress-tested against four synthetic month-end datasets with
   roughly 30 seeded errors each (sign flips, duplicated journals, 10x FX
   overrides, missing accruals, mis-scoped reversals, maker-equals-checker
   breaks). It detects the large majority of those traps; the recurring gap was
   quantifying and classifying the adjustments against the ground-truth key, which
   is precisely why a human checker stays in the loop. Separately, the
   deterministic statement-level math now ties 17 of 17 figures to a real public
   company (dLocal, NASDAQ: DLO) reported financials. What remains unproven is the
   integration itself: the dLocal pass is statement-level, because no public filer
   discloses transaction-level subledgers, so the transaction-level agents and the
   multi-entity / multi-currency consolidation have not been validated on real
   data. Real data is also messier than any seeded set, so in production most of
   the effort and the risk still live in the data layer and its reliability.
2. **Reliability at scale (AgentOps).** The eval harness here is the right idea,
   but a deployment needs regression suites on the real ledger, drift
   detection, monitoring, alerting and rollback.
3. **Security and segregation of duties.** The scaffolding is now **in the
   system**, not just described: authenticated identity via a provider-agnostic
   OIDC client (`identity/`, with a `LocalDevIdentity` offline provider), RBAC that
   checks the identity holds the role registered as owner, segregation of duties
   enforced in code (maker ≠ checker, approver ≠ proposer), and secrets behind a
   `SecretsProvider` (`config/`) with redaction from logs/audit/snapshots and a
   documented rotation window. The **honest boundary**: the real IdP signature
   verification and the cloud secret manager are stubs — offline dev/demo/CI use
   locally-signed tokens and `.env`, so this proves the enforcement logic and the
   integration seams, not a production IdP/KMS deployment.
4. **Model-output trust.** Even with exact numbers, the narrative can *frame*
   them misleadingly (a real lesson from this repo: an early agent mislabeled
   "overdue"). In production the commentary is treated as a draft for human
   review, heavily constrained.

## The human-in-the-loop model (now two-tier — maker-checker)

An earlier version of this system had **one** human gate, at the CFO. That was
wrong, and it has been corrected — because finance runs on **segregation of
duties**, and a generalist CFO cannot competently approve the entire operational
flow (they "play by ear" on accounting, tax, planning).

The implemented model is **two-tier (maker-checker)**:

- **First line — per-function domain-expert sign-off.** Each function is signed
  off by the human with real depth in that area: the Tax Manager signs tax, the
  Treasurer signs treasury, the Accounting Manager signs the close, the FP&A
  Director signs planning, and so on (`review.py`). The agent is the *maker*; the
  expert is the *checker*. If any function is not signed off, the close is
  **blocked before the CFO** — no board pack is fabricated over un-reviewed work.
- **Second tier — the CFO's final sign-off** on the *consolidated* board pack and
  the material / cross-cutting items only. Not a pseudo-review of every detail.

This is why "one gate per agent" (approval fatigue) and "one gate total" (what
this repo used to claim) are both wrong; the realistic answer is *one expert
sign-off per function + a final CFO gate*. The remaining production refinement is
**materiality- and risk-based routing** (immaterial items auto-clear; only
material items require a human), for which the materiality thresholds already in
the codebase are the seed.

## How it deploys today vs. what is still "vision"

**Deployable now, as a co-pilot:** automate tie-outs and reconciliations, draft
variance commentary and board packs, detect anomalies and control exceptions —
with humans reviewing at risk-based checkpoints. This is already happening at
advanced finance teams.

**Still a vision (5–10 years, and as much a trust/regulatory question as a
technical one):** a lights-out close with no human, and the AI making
*accounting judgments* (estimates, judgmental accruals, edge-case revenue
recognition) without review.

## Bottom line

The value is not "an autonomous CFO." It is a system that knows **where AI adds
value today** (assist, detect, draft) and **where the human is non-negotiable**
(judgment, approval, segregation of duties), built on the controls — determinism,
audit trail, human gates — that let a CFO and an auditor actually trust it. The
core accuracy claim is no longer just asserted: the deterministic statement-level
math has been reconciled, 17 of 17, against a real public company's reported
financials, stress-tested against adversarial synthetic traps, and reviewed by an
independent second model. Those checks are statement-level and on public data
only; they do not prove the whole operating model on real transaction-level data,
and the integration work in the data layer remains the bulk of any deployment. The
honest framing is *"a governed finance co-pilot,"* with a clear, engineering-only
path from here to production.
