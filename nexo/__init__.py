"""
Nexo - AI co-pilot for a single insurance broker (productor de seguros) in
Argentina.

Self-contained project built INSIDE the ai-finance-engineering repo. It reuses
the *architecture* of the finance operating model (deterministic core + shared
state + maker/checker HITL + orchestrator), re-implemented from scratch for the
insurance-brokerage domain. Nexo imports nothing from the finance projects.

Design invariant: numbers are deterministic (computed in Python), prose is the
model's. The LLM only writes message text and narrative insights; it never
computes or invents a number, date, client or policy.
"""

__version__ = "0.1.0"
