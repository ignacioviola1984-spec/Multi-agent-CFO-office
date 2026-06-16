"""
app.py - CFO AI Office, public demo (HR-friendly).

Replays a REAL, saved run of the multi-agent CFO office so anyone can explore
it instantly, with no API key and no cost. The narratives come from the saved
run (demo_snapshot.json); the interactive widgets recompute from those same
numbers, live and free. Source: github.com/ignacioviola1984-spec/ai-finance-engineering

Run locally:  python -m streamlit run app.py
Deploy:       Streamlit Community Cloud, main file = cfo-demo/app.py (no secrets needed).
"""

import json
import os

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(page_title="CFO AI Office — Live Demo", page_icon="📊", layout="wide")


# --------------------------------------------------------------------------
# Data: the saved real run.
# --------------------------------------------------------------------------

@st.cache_data
def load_snapshot():
    with open(os.path.join(HERE, "demo_snapshot.json"), encoding="utf-8") as f:
        return json.load(f)

DATA = load_snapshot()
A = DATA["agents"]
PERIOD = "May 2026"

# Top-level functions reporting to the CFO (Administration and Accounting &
# Reporting each consolidate their own sub-agents, so their escalations are
# already rolled up — no double-counting).
TOP_LEVEL = ["Controller", "Treasury", "Administration", "Accounting & Reporting",
             "FP&A", "Strategic Finance", "Internal Controls", "Audit"]


def money(x):
    return f"${x:,.0f}"


def clean(text):
    """Trim a dangling incomplete final sentence (snapshots can cut at a token
    limit), then escape '$' so Streamlit does not render $...$ as LaTeX math."""
    text = (text or "").strip()
    if text and text[-1] not in ".!?\")*":
        cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if cut > 0:
            text = text[: cut + 1]
    return text.replace("$", "\\$")


def sev_badge(sev):
    color = {"CRITICAL": "#C0392B", "HIGH": "#D97706"}.get(sev, "#4A6FA5")
    return (f"<span style='background:{color};color:#fff;padding:2px 8px;"
            f"border-radius:6px;font-size:0.72rem;font-weight:700'>{sev}</span>")


def stmt_table(rows):
    """Render a financial statement as a 2-column table (line, amount)."""
    st.table([{"": label, " ": val} for label, val in rows])


def signoff(name):
    """Green maker-checker stamp: which domain expert signed off this function.
    If the recorded decision was auto-approved (snapshot/CI, no reviewer at the
    console), say so plainly — it is never passed off as a real human sign-off."""
    r = A.get(name, {}).get("review")
    if not r:
        return
    auto = " <span style='color:#92400E;font-weight:600'>(auto-approved in this replay)</span>" \
        if r.get("mode") == "auto" else ""
    st.markdown(f"<span style='color:#0F6E56;font-size:0.82rem;font-weight:600'>"
                f"&#10003; First-line sign-off · {r['reviewer']}</span>{auto}", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Light styling.
# --------------------------------------------------------------------------

st.markdown("""
<style>
.small { color:var(--text-color); opacity:0.9; font-size:0.9rem; }
.card { background:rgba(127,127,127,0.06); border:1px solid rgba(127,127,127,0.18);
        border-radius:12px; padding:14px 16px; margin-bottom:8px; }
.role { font-weight:700; font-size:0.98rem; }
.boardpack { background:rgba(27,42,74,0.06); border-left:4px solid #1B2A4A;
             border-radius:8px; padding:18px 22px; }
.opinion { background:rgba(15,110,86,0.10); border-left:4px solid #0F6E56;
           border-radius:8px; padding:12px 16px; font-weight:600; }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Header.
# --------------------------------------------------------------------------

st.title("📊 CFO AI Office")
st.markdown(
    "#### An AI finance operating model that runs a month-end close workflow from raw data to board pack, with human approval at key points."
)
st.markdown(
    "<span class='small'>Eight specialist agents — across accounting, treasury, working capital, "
    "planning, controls and audit — do the work, and <b>each function is signed off by its own "
    "domain expert</b> (maker-checker: the Tax Manager signs tax, the Treasurer signs treasury, "
    "and so on). The <b>CFO agent</b> then reconciles the numbers and gives a single <b>final "
    "sign-off</b> on the consolidated board pack and the material items. Running on a synthetic "
    f"SaaS company, <b>Lumen Inc.</b>, closing <b>{PERIOD}</b>.</span>",
    unsafe_allow_html=True)

with st.expander("ℹ️  What am I looking at? (30-second version)"):
    st.markdown(
        "- This is a **working multi-agent AI system for corporate finance** — not slides, real software.\n"
        "- It runs the whole loop: **record → close → report → analyze → control → audit**.\n"
        "- Every **number** is computed by code (deterministic, auditable). The AI agents **read the "
        "numbers, reason, and write the commentary** — they never invent a figure. That's the core design rule.\n"
        "- The books **reconcile**, the three financial statements **articulate**, and an **independent "
        "audit agent** re-derives the figures and issues an opinion.\n"
        "- **Two-tier human control (maker-checker):** each function is signed off by the domain "
        "expert who actually has that depth (a generalist CFO can't competently approve everything), "
        "and the **CFO gives the final consolidated sign-off** (you'll do that below).\n"
        "- This page replays a saved run so it is instant and free. Human approvals are simulated in "
        "this public version. In a real deployment, each approval gate would be owned by the relevant "
        "finance lead. The widgets recompute live.\n"
        "- Built by **Ignacio Viola** — 17 years in senior finance, now building the AI systems. "
        "Full source on [GitHub](https://github.com/ignacioviola1984-spec/ai-finance-engineering)."
    )

# The team / org.
st.markdown("##### The team — a two-level finance org")
team_rows = [
    [("🧾 Controller", "Close review: P&L consistency, margins, risk flags."),
     ("💵 Treasury", "Cash, burn, runway, 13-week cash forecast."),
     ("🗂️ Administration", "Supervises Accounts Receivable · Accounts Payable · Tax."),
     ("📒 Accounting & Reporting", "Supervises the Close (reconciliations) and the 3 financial statements.")],
    [("📈 FP&A", "Forecast + variances (vs last month and vs budget)."),
     ("🎯 Strategic Finance", "Growth quality & capital efficiency; path to breakeven."),
     ("🛡️ Internal Controls", "Assurance: trial balance, FX, cutoff, authorizations."),
     ("🔎 Audit", "Independent third line: re-derives the figures, issues an opinion.")],
]
for row in team_rows:
    cols = st.columns(4)
    for c, (role, desc) in zip(cols, row):
        c.markdown(f"<div class='card'><div class='role'>{role}</div>"
                   f"<div class='small'>{desc}</div></div>", unsafe_allow_html=True)
st.markdown("<div class='card' style='text-align:center'><span class='role'>👔 CFO</span> "
            "<span class='small'>— reconciles all eight, consolidates risks, gives the <b>final</b> "
            "consolidated sign-off (after each function's domain-expert sign-off), writes the board "
            "report.</span></div>", unsafe_allow_html=True)

st.divider()


# --------------------------------------------------------------------------
# Run the close.
# --------------------------------------------------------------------------

if "ran" not in st.session_state:
    st.session_state.ran = False
if "approved" not in st.session_state:
    st.session_state.approved = False

if not st.session_state.ran:
    st.markdown("### ▶️  Run the month-end close")
    st.markdown("<span class='small'>Click to have the AI office process Lumen's "
                f"{PERIOD} close, step by step.</span>", unsafe_allow_html=True)
    if st.button("Run the close", type="primary"):
        st.session_state.ran = True
        st.rerun()

if st.session_state.ran:
    ctrl, trez = A["Controller"], A["Treasury"]
    ar, ap, tax = A["Accounts Receivable"], A["Accounts Payable"], A["Tax"]
    close, rep = A["Accounting & Close"], A["Financial Reporting"]
    fpa, strat = A["FP&A"], A["Strategic Finance"]
    ctrls, aud, cfo = A["Internal Controls"], A["Audit"], A["CFO"]

    st.markdown("### 1 · The agents report")

    # Controller
    with st.container():
        st.markdown("#### 🧾 Controller — the close")
        c = st.columns(4)
        c[0].metric("Revenue", money(ctrl["pnl"]["revenue"]))
        c[1].metric("Operating income", money(ctrl["pnl"]["operating_income"]),
                    f"{ctrl['op_margin_pct']:.1f}% margin", delta_color="off")
        c[2].metric("Gross margin", f"{ctrl['gross_margin_pct']:.1f}%")
        c[3].metric("Receivables overdue", f"{ctrl['ar']['overdue_pct']:.0f}%", "of total AR", delta_color="off")
        with st.expander("📄 Controller's full analysis"):
            st.markdown(clean(ctrl["narrative"]))
        signoff("Controller")

    # Treasury
    with st.container():
        st.markdown("#### 💵 Treasury — liquidity")
        f13 = trez.get("forecast", {})
        c = st.columns(4)
        c[0].metric("Cash", money(trez["cash"]))
        c[1].metric("Monthly burn", money(trez["burn"]))
        c[2].metric("Runway", f"{trez['runway']:.1f} months")
        if f13:
            c[3].metric("13-week ending cash", money(f13["ending_cash"]),
                        "stays positive" if not f13.get("week_cash_negative") else "goes negative",
                        delta_color="off")
        with st.expander("📄 Treasury's full analysis"):
            st.markdown(clean(trez["narrative"]))
        signoff("Treasury")

    # Administration → AR / AP / Tax
    with st.container():
        st.markdown("#### 🗂️ Administration — working capital & tax (AR · AP · Tax)")
        c = st.columns(4)
        c[0].metric("AR overdue", money(ar["metrics"]["overdue"]),
                    f"{ar['metrics']['overdue_pct']:.0f}% of AR · DSO {ar['metrics']['dso']:.0f}d", delta_color="off")
        c[1].metric("AP overdue", money(ap["metrics"]["overdue"]),
                    f"DPO {ap['metrics']['dpo']:.0f}d", delta_color="off")
        c[2].metric("Tax overdue", money(tax["metrics"]["overdue"]),
                    f"of {money(tax['metrics']['pending_total'])} pending", delta_color="off")
        c[3].metric("Due within 30 days", money(ap["metrics"]["upcoming_30d"] + tax["metrics"]["upcoming_30d"]),
                    "AP + tax", delta_color="off")
        with st.expander("📄 Administration — Accounts Receivable, Payable and Tax"):
            st.markdown("**Accounts Receivable** — " + clean(ar["narrative"]))
            st.markdown("**Accounts Payable** — " + clean(ap["narrative"]))
            st.markdown("**Tax** — " + clean(tax["narrative"]))
        signoff("Accounts Receivable")
        signoff("Accounts Payable")
        signoff("Tax")

    # Accounting & Reporting → close + the three financial statements
    with st.container():
        st.markdown("#### 📒 Accounting & Reporting — the close & the financial statements")
        recs = close["reconciliations"]
        if recs["all_reconciled"]:
            st.markdown("<div class='opinion'>✅ Close is clean — AR & AP subledgers tie to the general "
                        "ledger, and retained earnings roll forward by net income (the statements "
                        "articulate).</div>", unsafe_allow_html=True)
        inc, bs, cf = rep["income_statement"], rep["balance_sheet"], rep["cash_flow"]
        s1, s2, s3 = st.columns(3)
        with s1:
            st.markdown("**Income statement**")
            stmt_table([
                ("Revenue", money(inc["revenue"])),
                ("Cost of revenue", money(-inc["cogs"])),
                (f"Gross profit ({inc['gross_margin_pct']:.0f}%)", money(inc["gross"])),
                ("Sales & marketing", money(-inc["sm"])),
                ("R&D", money(-inc["rd"])),
                ("G&A", money(-inc["ga"])),
                (f"Net income ({inc['net_margin_pct']:.0f}%)", money(inc["net_income"])),
            ])
        with s2:
            st.markdown("**Balance sheet**")
            stmt_table([
                ("Cash", money(bs["assets"]["cash"])),
                ("Accounts receivable", money(bs["assets"]["accounts_receivable"])),
                ("Fixed assets", money(bs["assets"]["fixed_assets"])),
                ("Total assets", money(bs["total_assets"])),
                ("Liabilities", money(bs["total_liabilities"])),
                ("Equity", money(bs["total_equity"])),
                ("Check (A−L−E)", money(bs["balance_check"])),
            ])
        with s3:
            st.markdown("**Cash flow (indirect)**")
            stmt_table([
                ("Net income", money(cf["net_income"])),
                ("− Increase in AR", money(-cf["d_ar"])),
                ("+ Increase in AP", money(cf["d_ap"])),
                ("+ Increase in deferred", money(cf["d_deferred"])),
                ("Cash from operations", money(cf["cfo"])),
                ("Beginning cash", money(cf["cash_begin"])),
                ("Ending cash", money(cf["cash_end"])),
            ])
        st.caption("The three statements articulate: net income flows into equity, and the cash-flow "
                   "statement foots to the actual change in cash "
                   f"({money(cf['net_change'])} = {money(cf['actual_change'])}).")
        with st.expander("📄 Accounting & Reporting — full commentary"):
            st.markdown(clean(A["Accounting & Reporting"]["narrative"]))
        signoff("Accounting & Close")
        signoff("Financial Reporting")

    # FP&A
    with st.container():
        st.markdown("#### 📈 FP&A — forecast & variances")
        f = fpa["forecast"]
        c = st.columns(4)
        c[0].metric("Next-month revenue (fcst)", money(f["revenue"]))
        c[1].metric("Next-month op income (fcst)", money(f["operating_income"]))
        oi = next(r for r in fpa["budget_variance"]["rows"] if r["label"] == "Operating income")
        c[2].metric("Op income vs budget", money(oi["var"]), f"{oi['var_pct']:.1f}% vs plan", delta_color="off")
        c[3].metric("Material lines vs plan", str(len(fpa["budget_variance"]["material"])))
        with st.expander("📄 FP&A — variance vs last month"):
            st.markdown(clean(fpa["variance_expl"]))
        with st.expander("📄 FP&A — variance vs budget"):
            st.markdown(clean(fpa["budget_expl"]))
        signoff("FP&A")

    # Strategic Finance
    with st.container():
        st.markdown("#### 🎯 Strategic Finance — growth quality & capital efficiency")
        m = strat["metrics"]
        c = st.columns(4)
        c[0].metric("ARR run-rate", money(m["run_rate"]))
        c[1].metric("Rule of 40", f"{m['rule_of_40']:.0f}", "≥ 40 is healthy", delta_color="off")
        c[2].metric("Burn multiple", f"{m['burn_multiple']:.1f}x", "≤ 2 is efficient", delta_color="off")
        c[3].metric("Magic number", f"{m['magic_number']:.2f}", "> 0.75 is good", delta_color="off")
        with st.expander("📄 Strategic Finance's full analysis"):
            st.markdown(clean(strat["narrative"]))
        signoff("Strategic Finance")

    # Internal Controls
    with st.container():
        st.markdown("#### 🛡️ Internal Controls — assurance")
        summ = ctrls["summary"]
        c = st.columns(4)
        c[0].metric("Controls passed", f"{summ['n_pass']} / {summ['n_pass']+summ['n_fail']+summ['n_exception']}")
        c[1].metric("Integrity failures", str(summ["n_fail"]))
        c[2].metric("Books balanced", "Yes" if summ["books_balanced"] else "No")
        c[3].metric("Authorization review", str(summ["approval_exceptions"]),
                    f"payments ≥ $25k ({money(summ['approval_exceptions_total'])})", delta_color="off")
        with st.expander("📄 Control register"):
            for ck in ctrls["checks"]:
                mark = "✅" if ck["status"] == "PASS" else "⚠️"
                st.markdown(f"{mark} **{ck['name']}** — {clean(ck['detail'])}")
        signoff("Internal Controls")

    # Audit
    with st.container():
        st.markdown("#### 🔎 Audit — independent assurance (third line)")
        st.markdown(f"<div class='opinion'>Audit opinion: <b>{aud['opinion'].upper()}</b> — "
                    f"{aud['n_procedures']} procedures re-performed, {aud['n_exceptions']} exception(s).</div>",
                    unsafe_allow_html=True)
        with st.expander("📄 Audit procedures (re-derived from the raw ledger & subledger)"):
            for fnd in aud["findings"]:
                mark = "✅" if fnd["ok"] else "⚠️"
                st.markdown(f"{mark} **{fnd['proc']}** — {clean(fnd['detail'])}")
        signoff("Audit")

    st.divider()

    # --------------------------------------------------------------------
    # The operating model: the close as explicit stages, each with a
    # deterministic control + the domain expert's sign-off (rework -> block).
    # --------------------------------------------------------------------
    om = A.get("Operating Model", {})
    if om.get("stages"):
        st.markdown("### 2 · The operating model — stages, controls & sign-offs")
        st.markdown("<span class='small'>The close isn't one big prompt — it runs as explicit "
                    "<b>stages</b>. Each stage = the agent does the work (maker), a <b>deterministic "
                    "control in code</b> must hold (a hard gate, not the model's opinion), and the "
                    "<b>domain expert signs off</b> (checker). If a stage can't pass it goes to "
                    "<b>rework</b>, and if it still can't, it <b>blocks the whole close</b> — you don't "
                    "build a board pack on an un-controlled stage.</span>", unsafe_allow_html=True)
        stage_rows = []
        for s in om["stages"]:
            reviewers = ", ".join(
                A.get(fn, {}).get("review", {}).get("reviewer", "—") for fn in s["functions"])
            ctrl = s["control"]
            stage_rows.append({
                "Stage": f"{s['id']} · {s['name']}",
                "Deterministic control": "— (no code gate)" if ctrl == "no code-level control" else ctrl,
                "Signed off by (domain expert)": reviewers,
                "Status": ("✓ Passed" if s["status"] == "passed"
                           else f"✗ Blocked ({s.get('reason','')})"),
            })
        st.table(stage_rows)
        if om.get("all_passed"):
            st.success(f"✅ All {len(om['stages'])} stages passed their deterministic control and "
                       "domain-expert sign-off. No stage blocked, so the close can proceed to the CFO.")
        else:
            blocked = next((s for s in om["stages"] if s["status"] == "blocked"), None)
            st.error(f"⛔ Close blocked at stage {blocked['id']} ({blocked['name']}). "
                     "A failed control or missing sign-off stops the whole close — by design.")
        st.divider()

    # --------------------------------------------------------------------
    # CFO consolidation + human gate.
    # --------------------------------------------------------------------
    st.markdown("### 3 · First line — each function signed off by its domain expert")
    st.markdown("<span class='small'>Maker-checker, the way finance actually works: the agent does "
                "the work, and the person with real depth in that area validates and signs. A "
                "generalist CFO can't competently approve every operational detail — so each "
                "function is owned by its expert. (Replayed sign-offs; in production these are "
                "different people.)</span>", unsafe_allow_html=True)
    FIRST_LINE = ["Controller", "Accounting & Close", "Financial Reporting", "Treasury",
                  "Accounts Receivable", "Accounts Payable", "Tax", "FP&A", "Strategic Finance",
                  "Internal Controls", "Audit"]
    fl_rows = []
    n_auto = 0
    for fn in FIRST_LINE:
        r = A.get(fn, {}).get("review")
        if r:
            is_auto = r.get("mode") == "auto"
            n_auto += is_auto
            fl_rows.append({"Function": fn, "Signed off by (domain expert)": r["reviewer"],
                            "Mode": "Auto (replay)" if is_auto else "Human",
                            "Status": "✓ Approved" if r["decision"] == "approved" else "✗ Rejected"})
    st.table(fl_rows)
    n_ok = sum(1 for r in fl_rows if r["Status"].startswith("✓"))
    st.info(f"First line: {n_ok}/{len(fl_rows)} functions cleared their domain-expert checker, and the "
            "cross-checks passed — the agents agree on the shared numbers (operating income, burn, "
            "revenue/run-rate, AR, and Reporting's net income & cash).")
    if n_auto:
        st.caption(f"⚠️ Honest disclosure: {n_auto}/{len(fl_rows)} sign-offs in this public replay were "
                   "**auto-approved** (no reviewer is at the console when the snapshot is generated). The "
                   "maker-checker workflow, audit trail and block-on-reject are real and run interactively; "
                   "what's simulated here is the human keystroke, not the control.")

    escalations = []
    for name in TOP_LEVEL:
        escalations += A.get(name, {}).get("escalations", [])
    order = {"CRITICAL": 0, "HIGH": 1}
    escalations = sorted(escalations, key=lambda e: order.get(e[0], 9))
    st.markdown(f"**{len(escalations)} risk flags raised** (each owned by one agent, no double-counting):")
    for sev, msg in escalations:
        st.markdown(f"{sev_badge(sev)}&nbsp; {msg}", unsafe_allow_html=True)

    st.divider()
    st.markdown("### 4 · 🧑‍⚖️ CFO final sign-off — your call")
    if not st.session_state.approved:
        st.warning("First line is complete — all functions are signed off by their domain experts. "
                   "The CFO now gives the **final consolidated sign-off** on the board pack and the "
                   "material items — *not* a re-review of every detail (that's what the experts are "
                   "for). **You are the CFO.**")
        if st.button("✅ Final sign-off as CFO → release the board pack", type="primary"):
            st.session_state.approved = True
            st.rerun()
    else:
        st.markdown("### 5 · 📋 Board pack")
        st.markdown(f"<div class='boardpack'>{clean(cfo['board_pack'])}</div>", unsafe_allow_html=True)
        st.markdown("#### Recommended actions")
        st.markdown(clean(cfo["actions"]))
        st.caption("Generated by the CFO agent from the eight agents' inputs — every figure traces "
                   "back to code-computed numbers.")


# --------------------------------------------------------------------------
# Play widgets (recompute from snapshot, free).
# --------------------------------------------------------------------------

st.divider()
st.markdown("### 🎛️  Play with the model")
st.markdown("<span class='small'>These recompute instantly from the saved numbers — no AI calls, "
            "showing how the deterministic engine drives the decisions.</span>", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["Materiality threshold", "Growth scenarios"])

with tab1:
    st.markdown("How big a budget variance is worth flagging? Move the threshold and watch which "
                "lines the system escalates (it also requires a \\$20k floor).")
    pct = st.slider("Materiality threshold (% of plan)", 1.0, 15.0, 5.0, 0.5)
    rows = A["FP&A"]["budget_variance"]["rows"]
    flagged = [r for r in rows if abs(r["var_pct"]) >= pct and abs(r["var"]) >= 20000]
    st.markdown(f"**{len(flagged)} line(s) flagged at {pct:.1f}%:**")
    if flagged:
        st.table([{"Line": r["label"], "Variance $": f"{r['var']:+,.0f}",
                   "Variance %": f"{r['var_pct']:+.1f}%",
                   "Fav/Unfav": "Unfavorable" if r["flag"] == "U" else "Favorable"} for r in flagged])
    else:
        st.info("Nothing material at this threshold — the month was in line with plan.")
    st.caption("The default is 5%: at the looser 10% bar, real overruns like the G&A miss would slip through.")

with tab2:
    st.markdown("Growth helps the headline, but does it fix profitability? Margin is held constant on "
                "purpose — to show growth alone doesn't reach breakeven.")
    scn = {s["name"]: s for s in A["Strategic Finance"]["metrics"]["scenarios"]}
    pick = st.radio("Scenario", list(scn.keys()), index=1, horizontal=True)
    s = scn[pick]
    c = st.columns(3)
    c[0].metric("Monthly growth", f"{s['mom_growth']*100:.1f}%")
    c[1].metric("ARR run-rate in 12 months", money(s["run_rate_12m"]))
    c[2].metric("Rule of 40", f"{s['rule_of_40']:.0f}",
                "healthy" if s["rule_of_40"] >= 40 else "below 40", delta_color="off")
    st.caption("Even the high-growth case keeps a negative margin: the real lever is structural margin, "
               "not more volume. That's the Strategic Finance agent's headline.")


# --------------------------------------------------------------------------
# Audit trail + footer.
# --------------------------------------------------------------------------

st.divider()
with st.expander("🔍 Audit trail — every step is logged (governance)"):
    for e in DATA["audit"]:
        st.markdown(f"<span class='small'><code>{e['ts']}</code> · <b>{e['agent']}</b> · "
                    f"{e['status']} — {e['detail']}</span>", unsafe_allow_html=True)

st.divider()
st.markdown(
    "<span class='small'>Built by <b>Ignacio Viola</b> · 17 years in senior finance, now building AI "
    "systems for finance operations · Synthetic data; architecture built to point at production data · "
    "Source: <a href='https://github.com/ignacioviola1984-spec/ai-finance-engineering'>GitHub</a></span>",
    unsafe_allow_html=True)
