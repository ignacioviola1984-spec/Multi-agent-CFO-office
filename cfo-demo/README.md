# CFO AI Office — month-end close with live approval gates

A **no-API-key, zero-cost** web app of the multi-agent CFO office. The numbers
come from a saved run of the deterministic engine (`demo_snapshot.json`); the
interactive widgets recompute live from those same numbers — no AI calls, so it
can never run up an API bill.

Shows the full month-end loop — **record → close → report → analyze → control →
audit** — across eight specialist agents (Controller, Treasury, Administration
[AR/AP/Tax], Accounting & Reporting [close + the three financial statements],
FP&A, Strategic Finance, Internal Controls, and an independent Audit), run as an
explicit **staged operating model** with **two-tier human-in-the-loop control
(maker-checker)**: every function is signed off by its own domain expert (the
first line), and the CFO gives a single **final consolidated sign-off** on top.

**The approval gates are live.** The close advances stage by stage and pauses
at every sign-off: the person at the console acts as each domain expert (the 11
first-line gates) and as the CFO (the final gate). Each panel shows exactly what
is being approved — amounts, reconciliations, exceptions — plus the stage's
deterministic control. Rejecting sends the work back for one rework cycle; a
second rejection **blocks the whole close** (no board pack is built on
unapproved work). Every decision is time-stamped in the session's sign-off log.
A restart control reruns the close from the top. Data is **synthetic** (a
fictional SaaS company, Lumen Inc.) — no live company data.

For the technical audience, the full source (the agents, the deterministic
engine, the eval harness) is in the repo root. This folder is just the
shop window.

## Deploy on Streamlit Community Cloud (free, ~5 clicks)

1. The repo is already on GitHub.
2. Go to **share.streamlit.io** → sign in with GitHub.
3. **Create app** → **Deploy from a repo**:
   - Repository: `ignacioviola1984-spec/ai-finance-engineering`
   - Branch: `main`
   - **Main file path: `cfo-demo/app.py`**
4. Click **Deploy**. In ~1 minute you get a public URL like
   `https://<something>.streamlit.app` to send to anyone.

**No secrets / no API key needed** — this demo runs entirely on the saved
snapshot. (That's deliberate: a public link with a live key could be drained.)

## Run locally

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

## Refreshing the snapshot (optional)

The demo reads `demo_snapshot.json`. To refresh it with a new live run:

```bash
python ../cfo-office/cfo_orchestrator.py     # needs a valid ANTHROPIC_API_KEY in ../.env
cp ../cfo-office/cfo_state.json demo_snapshot.json
```
