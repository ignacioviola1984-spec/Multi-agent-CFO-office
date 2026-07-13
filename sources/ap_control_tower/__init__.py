"""
sources/ap_control_tower/ - bounded, read-only adapter that ingests AP Control
Tower's *approved payment proposal* export into the canonical `ap_invoices`
table the rest of the CFO Office already reads.

AP Control Tower is an INDEPENDENT product (its own repository). It exports a
controlled, human-approved payment proposal (CSV/Excel). This package reads that
file and maps each approved row into a canonical OPEN `ap_invoices` record, plus
a separate, module-owned traceability record. It never posts to the ledger,
never executes a payment, and never depends on anything inside AP Control Tower.

Direction is one-way only:  AP Control Tower export -> adapter -> canonical
finance layer -> existing CFO Office consumers.  Nothing here reaches back.

The adapter supports both normal package import
(`from sources.ap_control_tower import adapter`) and the repository's legacy
flat-import style used by standalone demos/tests. This package intentionally
re-exports no operational capability. See `README.md` and `demo.py`.
"""
