# Orchestration patterns

Agent orchestration patterns from Anthropic's "Building Effective Agents",
applied to the Lumen finance data. Reuses the `finance-mcp` server functions
as the source of truth for every number.

## `patterns.py`

- **Prompt chaining** — a fixed pipeline: real P&L numbers → key
  observations → executive summary. Each step feeds the next.
- **Routing** — a cheap classifier sends each question to the right
  specialist (P&L, cash, or AR aging), which pulls real data.

Run it (needs `ANTHROPIC_API_KEY` in the repo-root `.env`):

```bash
python patterns.py
```

## Design principle

Numbers are computed by code (deterministic). The model only observes,
routes, and writes prose. It never invents a figure.

## A real lesson from this build

In one run the model labeled "overdue receivables" as ">30 days past due"
(USD 604,582, 53% of the book). The figures were exact, but the framing
understated reality: the 1-30 bucket is already overdue, so ~97% of the
book is past due, not 53%. The math was right; the interpretation could
mislead. This is why a reliability layer (evals/guardrails) and a
human-in-the-loop reviewer matter in finance: a correct number can still
produce a wrong conclusion.
