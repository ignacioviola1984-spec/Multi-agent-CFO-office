"""
app.py - CFO AI Office: month-end close with live human approval gates.

Runs the multi-agent CFO office over a saved run of the deterministic engine
(demo_snapshot.json, synthetic company Lumen Inc.). Every number is computed by
code; the agents write the commentary. The close advances stage by stage and
PAUSES at every maker-checker sign-off: the named domain expert (played by the
person at the console) must approve before the next stage starts. Rejection
sends the work back for rework; a second rejection blocks the close, by design.

Run locally:  python -m streamlit run app.py
Deploy:       Streamlit Community Cloud, main file = cfo-demo/app.py (no secrets needed).
"""

import json
import os
import time
from datetime import datetime

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(page_title="CFO AI Office", page_icon="📊", layout="wide")


# --------------------------------------------------------------------------
# Data: a real run of the deterministic engine.
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

OM_STAGES = {s["id"]: s for s in A.get("Operating Model", {}).get("stages", [])}

# The close as explicit stages. Each stage = the agents' work + one sign-off
# gate per function, owned by that function's domain expert (maker-checker).
STAGES = [
    dict(id=1, icon="🧾", name="Controllership review", short="Controllership",
         functions=["Controller"],
         working="Controller agent is reviewing the close…"),
    dict(id=2, icon="💵", name="Treasury & liquidity", short="Treasury",
         functions=["Treasury"],
         working="Treasury agent is building the liquidity view…"),
    dict(id=3, icon="🗂️", name="Working capital & tax", short="Working capital",
         functions=["Accounts Receivable", "Accounts Payable", "Tax"],
         working="AR, AP and Tax agents are working the subledgers…"),
    dict(id=4, icon="📒", name="Close & financial statements", short="Close & statements",
         functions=["Accounting & Close", "Financial Reporting"],
         working="Accounting & Reporting agents are closing the books and drafting the statements…"),
    dict(id=5, icon="📈", name="Planning & analysis (FP&A)", short="FP&A",
         functions=["FP&A"],
         working="FP&A agent is running variances and the forecast…"),
    dict(id=6, icon="🎯", name="Strategic finance", short="Strategic finance",
         functions=["Strategic Finance"],
         working="Strategic Finance agent is assessing growth quality…"),
    dict(id=7, icon="🛡️", name="Internal controls", short="Internal controls",
         functions=["Internal Controls"],
         working="Internal Controls agent is executing the control register…"),
    dict(id=8, icon="🔎", name="Independent audit", short="Audit",
         functions=["Audit"],
         working="Audit agent is re-deriving the figures independently…"),
]
CFO_STAGE_ID = 9
CFO_WORKING = "CFO agent is consolidating the close…"
ALL_FUNCTIONS = [fn for s in STAGES for fn in s["functions"]]
N_GATES = len(ALL_FUNCTIONS) + 1  # 11 first-line sign-offs + the CFO's final one
MAX_ATTEMPTS = 2  # one rework cycle; a second rejection blocks the close
WORK_SECONDS = 1.4  # how long each stage's processing indicator is shown


def reviewer(fn):
    return A[fn]["review"]["reviewer"]


def money(x):
    return f"${x:,.0f}"


def clean(text):
    """Trim a dangling incomplete final sentence (agent commentary can cut at a
    token limit), then escape '$' so Streamlit does not render $...$ as LaTeX."""
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


# --------------------------------------------------------------------------
# Session state: one entry per gate, kept across Streamlit reruns.
# --------------------------------------------------------------------------

def init_state():
    ss = st.session_state
    ss.setdefault("started", False)
    ss.setdefault("gates", {})          # function -> {status, ts, attempts}
    ss.setdefault("cfo_gate", {"status": "pending", "ts": None, "attempts": 0})
    ss.setdefault("worked", [])         # stage ids whose processing pass ran
    ss.setdefault("signoff_log", [])    # live approval log for this session
    ss.setdefault("t_start", None)
    ss.setdefault("t_end", None)

init_state()


def gate(fn):
    return st.session_state.gates.get(fn, {"status": "pending", "ts": None, "attempts": 0})


def approved(fn):
    return gate(fn)["status"] == "approved"


def stage_done(s):
    return all(approved(fn) for fn in s["functions"])


def log_signoff(role, item, action):
    st.session_state.signoff_log.append(
        {"ts": datetime.now().strftime("%H:%M:%S"), "role": role,
         "item": item, "action": action})


def decide(fn, approve):
    """Record the reviewer's decision on a first-line gate and rerun."""
    g = dict(gate(fn))
    if approve:
        g["status"], g["ts"] = "approved", datetime.now().strftime("%H:%M:%S")
        log_signoff(reviewer(fn), fn, "Approved")
    else:
        g["attempts"] += 1
        if g["attempts"] >= MAX_ATTEMPTS:
            g["status"] = "blocked"
            log_signoff(reviewer(fn), fn, "Sign-off declined again — close blocked at this stage")
        else:
            g["status"] = "rework"
            log_signoff(reviewer(fn), fn, "Revision requested — returned to the agent for rework")
    st.session_state.gates[fn] = g
    st.rerun()


def decide_cfo(approve):
    g = st.session_state.cfo_gate
    if approve:
        g["status"], g["ts"] = "approved", datetime.now().strftime("%H:%M:%S")
        st.session_state.t_end = time.time()
        log_signoff("CFO", "Consolidated board pack", "Approved — board pack released")
    else:
        g["attempts"] += 1
        if g["attempts"] >= MAX_ATTEMPTS:
            g["status"] = "blocked"
            log_signoff("CFO", "Consolidated board pack", "Held again — close stopped before release")
        else:
            g["status"] = "rework"
            log_signoff("CFO", "Consolidated board pack", "Held — returned to the functions with comments")
    st.rerun()


def restart(key):
    if st.button("↺ Restart the close", key=key):
        st.session_state.clear()
        st.rerun()


def blocked_stage():
    """The stage (or CFO pseudo-stage) whose gate is blocked, if any."""
    for s in STAGES:
        if any(gate(fn)["status"] == "blocked" for fn in s["functions"]):
            return s
    if st.session_state.cfo_gate["status"] == "blocked":
        return dict(id=CFO_STAGE_ID, name="CFO final sign-off", icon="👔")
    return None


def current_stage():
    return next((s for s in STAGES if not stage_done(s)), None)


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
.stamp { color:#0F6E56; font-size:0.82rem; font-weight:600; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 10px 0; }
.chip { border-radius:999px; padding:4px 12px; font-size:0.78rem; font-weight:600;
        border:1px solid rgba(127,127,127,0.25); white-space:nowrap; }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Pipeline status strip.
# --------------------------------------------------------------------------

CHIP_STYLE = {
    "done":     ("#0F6E56", "rgba(15,110,86,0.12)",  "✓"),
    "awaiting": ("#B45309", "rgba(217,119,6,0.16)",  "⏸"),
    "working":  ("#1D4ED8", "rgba(29,78,216,0.12)",  "⋯"),
    "pending":  ("#6B7280", "rgba(127,127,127,0.08)", "·"),
    "blocked":  ("#B91C1C", "rgba(192,57,43,0.12)",  "⛔"),
}
CHIP_LABEL = {"done": "approved", "awaiting": "awaiting approval",
              "working": "in process", "pending": "pending", "blocked": "blocked"}


def pipeline_strip():
    blk = blocked_stage()
    cur = current_stage()
    chips = []

    def add(icon, short, state):
        color, bg, mark = CHIP_STYLE[state]
        chips.append(f"<span class='chip' style='color:{color};background:{bg}'>"
                     f"{mark} {icon} {short} · {CHIP_LABEL[state]}</span>")

    for s in STAGES:
        if blk and s["id"] == blk["id"]:
            state = "blocked"
        elif stage_done(s):
            state = "done"
        elif cur and s["id"] == cur["id"]:
            state = "awaiting" if s["id"] in st.session_state.worked else "working"
        else:
            state = "pending"
        add(s["icon"], s["short"], state)

    cfo_g = st.session_state.cfo_gate
    if cfo_g["status"] == "approved":
        cfo_state = "done"
    elif cfo_g["status"] == "blocked":
        cfo_state = "blocked"
    elif cur is None:
        cfo_state = "awaiting" if CFO_STAGE_ID in st.session_state.worked else "working"
    else:
        cfo_state = "pending"
    add("👔", "CFO sign-off", cfo_state)

    n_ok = sum(1 for fn in ALL_FUNCTIONS if approved(fn)) + (cfo_g["status"] == "approved")
    st.markdown("<div class='chips'>" + "".join(chips) + "</div>", unsafe_allow_html=True)
    st.caption(f"Sign-offs completed: {n_ok} of {N_GATES} · every gate below is approved "
               "live in this session — the close does not advance without it.")


# --------------------------------------------------------------------------
# What each reviewer is approving (amounts, entries, exceptions) — all values
# come from the same snapshot the sections render, so the numbers the
# Accounting Manager approves are the ones the Controller and CFO see.
# --------------------------------------------------------------------------

def gate_summary(fn):
    ctrl, trez = A["Controller"], A["Treasury"]
    ar, ap, tax = A["Accounts Receivable"], A["Accounts Payable"], A["Tax"]
    close, rep = A["Accounting & Close"], A["Financial Reporting"]
    fpa, strat = A["FP&A"], A["Strategic Finance"]
    ctrls, aud = A["Internal Controls"], A["Audit"]

    if fn == "Controller":
        return [("Revenue, " + PERIOD, money(ctrl["pnl"]["revenue"])),
                ("Operating income", f"{money(ctrl['pnl']['operating_income'])} "
                                     f"({ctrl['op_margin_pct']:.1f}% margin)"),
                ("Gross margin", f"{ctrl['gross_margin_pct']:.1f}%"),
                ("Receivables overdue", f"{ctrl['ar']['overdue_pct']:.0f}% of total AR")]
    if fn == "Treasury":
        f13 = trez.get("forecast", {})
        rows = [("Cash", money(trez["cash"])),
                ("Monthly burn", money(trez["burn"])),
                ("Runway", f"{trez['runway']:.1f} months")]
        if f13:
            rows.append(("13-week ending cash",
                         f"{money(f13['ending_cash'])} "
                         f"({'stays positive' if not f13.get('week_cash_negative') else 'goes negative'})"))
        return rows
    if fn == "Accounts Receivable":
        m = ar["metrics"]
        return [("Total accounts receivable", money(m["total"])),
                ("Overdue", f"{money(m['overdue'])} ({m['overdue_pct']:.0f}% of AR, "
                            f"{m['n_overdue']} invoices)"),
                ("DSO", f"{m['dso']:.0f} days")]
    if fn == "Accounts Payable":
        m = ap["metrics"]
        return [("Open payables", money(m["open_total"])),
                ("Overdue", f"{money(m['overdue'])} ({m['n_overdue']} bills)"),
                ("DPO", f"{m['dpo']:.0f} days"),
                ("Due within 30 days", money(m["upcoming_30d"]))]
    if fn == "Tax":
        m = tax["metrics"]
        return [("Pending tax obligations", f"{money(m['pending_total'])} across "
                                            f"{len(m['by_jurisdiction'])} jurisdictions"),
                ("Overdue", money(m["overdue"])),
                ("Due within 30 days", money(m["upcoming_30d"]))]
    if fn == "Accounting & Close":
        recs = close["reconciliations"]
        rows = [(f"{r['item']} subledger → GL",
                 f"{money(r['subledger'])} vs {money(r['gl'])} · {r['status']}")
                for r in recs["recs"]]
        art = recs["articulation"]
        rows.append((art["item"],
                     f"movement {money(art['re_movement'])} vs net income "
                     f"{money(art['net_income'])} · {art['status']}"))
        return rows
    if fn == "Financial Reporting":
        inc, bs, cf = rep["income_statement"], rep["balance_sheet"], rep["cash_flow"]
        return [("Net income", f"{money(inc['net_income'])} ({inc['net_margin_pct']:.0f}% margin)"),
                ("Total assets", money(bs["total_assets"])),
                ("Balance check (A−L−E)", money(bs["balance_check"])),
                ("Cash flow foots", f"{money(cf['net_change'])} net change = "
                                    f"{money(cf['actual_change'])} actual · ending cash {money(cf['cash_end'])}")]
    if fn == "FP&A":
        f = fpa["forecast"]
        mat = fpa["budget_variance"]["material"]
        mat_txt = "; ".join(f"{r['label']} {r['var']:+,.0f} ({r['var_pct']:+.1f}%, "
                            f"{'unfavorable' if r['flag'] == 'U' else 'favorable'})" for r in mat)
        return [("Next-month revenue (forecast)", money(f["revenue"])),
                ("Next-month operating income (forecast)", money(f["operating_income"])),
                (f"Material variances vs plan ({len(mat)})", mat_txt or "none")]
    if fn == "Strategic Finance":
        m = strat["metrics"]
        return [("ARR run-rate", money(m["run_rate"])),
                ("Rule of 40", f"{m['rule_of_40']:.0f} (≥ 40 is healthy)"),
                ("Burn multiple", f"{m['burn_multiple']:.1f}x (≤ 2 is efficient)"),
                ("Magic number", f"{m['magic_number']:.2f} (> 0.75 is good)")]
    if fn == "Internal Controls":
        s = ctrls["summary"]
        total = s["n_pass"] + s["n_fail"] + s["n_exception"]
        return [("Controls passed", f"{s['n_pass']} of {total} ({s['n_exception']} exception flagged)"),
                ("Integrity failures", str(s["n_fail"])),
                ("Books balanced", "Yes" if s["books_balanced"] else "No"),
                ("Authorization review", f"{s['approval_exceptions']} payments ≥ $25,000 "
                                         f"({money(s['approval_exceptions_total'])}) pending documented review")]
    if fn == "Audit":
        return [("Audit opinion", aud["opinion"].upper()),
                ("Procedures re-performed", str(aud["n_procedures"])),
                ("Exceptions", str(aud["n_exceptions"]))]
    return []


# --------------------------------------------------------------------------
# Gates: stamps, approval panels, rework and block behaviour.
# --------------------------------------------------------------------------

def stamp(fn):
    g = gate(fn)
    if g["status"] == "approved":
        st.markdown(f"<span class='stamp'>&#10003; Signed off · {reviewer(fn)} · "
                    f"{g['ts']}</span>", unsafe_allow_html=True)


def approval_panel(fn, stage):
    g = gate(fn)
    role = reviewer(fn)
    om = OM_STAGES.get(stage["id"], {})
    control = om.get("control", "")
    exceptions = A[fn].get("escalations", [])

    with st.container(border=True):
        st.markdown(f"#### ⏸ Approval required — {role}")
        st.markdown(f"<span class='small'>Stage {stage['id']} · {stage['name']} · work prepared "
                    f"by the <b>{fn}</b> agent. The close is paused here until you decide.</span>",
                    unsafe_allow_html=True)

        if g["status"] == "rework":
            st.warning(f"**Revision requested.** Your comments went back to the {fn} agent (the maker). "
                       "The work was reworked and **resubmitted for your review** — this is "
                       f"attempt {g['attempts'] + 1} of {MAX_ATTEMPTS}. If you decline again, the close "
                       "**blocks at this stage** and cannot reach the CFO: no board pack is built on "
                       "work its reviewer has not approved.")

        st.markdown("**You are approving:**")
        st.table([{"Item": k, "Value": v} for k, v in gate_summary(fn)])

        if control and control != "no code-level control":
            st.markdown(f"<span class='small'>Deterministic control for this stage: "
                        f"<b>{control}</b> — ✓ passed in code before reaching you.</span>",
                        unsafe_allow_html=True)

        if exceptions:
            st.markdown(f"**Exceptions raised by the {fn} agent** (approving accepts them as "
                        "known, owned risks — they stay on the CFO's risk register):")
            for sev, msg in exceptions:
                st.markdown(f"{sev_badge(sev)}&nbsp; {clean(msg)}", unsafe_allow_html=True)
        else:
            st.markdown(f"<span class='small'>No exceptions raised by the {fn} agent.</span>",
                        unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        if c1.button(f"✓ Approve as {role}", key=f"ap_{fn}", type="primary",
                     use_container_width=True):
            decide(fn, True)
        if c2.button("✗ Reject / request revision", key=f"rj_{fn}", use_container_width=True):
            decide(fn, False)
        st.caption("Rejecting returns the work to the agent with your comments; the close stays "
                   "paused at this stage until the sign-off clears.")


def blocked_panel(fn, stage):
    role = reviewer(fn)
    st.error(f"⛔ **Close blocked at stage {stage['id']} — {stage['name']}.** "
             f"{role} declined the sign-off twice, so the operating model stops the entire close "
             "here: the remaining stages do not run, and the CFO gate stays locked. That is the "
             "control working as designed — an unapproved stage can never flow into the board "
             "pack. In production this raises the block to the process owner with the full "
             "rework history in the audit trail.")
    restart(f"restart_blocked_{fn}")


# --------------------------------------------------------------------------
# Stage sections: the agents' work (identical numbers everywhere).
# --------------------------------------------------------------------------

def render_stage_1():
    ctrl = A["Controller"]
    st.markdown("#### 🧾 Stage 1 · Controllership review")
    c = st.columns(4)
    c[0].metric("Revenue", money(ctrl["pnl"]["revenue"]))
    c[1].metric("Operating income", money(ctrl["pnl"]["operating_income"]),
                f"{ctrl['op_margin_pct']:.1f}% margin", delta_color="off")
    c[2].metric("Gross margin", f"{ctrl['gross_margin_pct']:.1f}%")
    c[3].metric("Receivables overdue", f"{ctrl['ar']['overdue_pct']:.0f}%", "of total AR",
                delta_color="off")
    with st.expander("📄 Controller's full analysis"):
        st.markdown(clean(ctrl["narrative"]))
    stamp("Controller")


def render_stage_2():
    trez = A["Treasury"]
    f13 = trez.get("forecast", {})
    st.markdown("#### 💵 Stage 2 · Treasury & liquidity")
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
    stamp("Treasury")


def render_stage_3():
    ar, ap, tax = A["Accounts Receivable"], A["Accounts Payable"], A["Tax"]
    st.markdown("#### 🗂️ Stage 3 · Working capital & tax (AR · AP · Tax)")
    c = st.columns(4)
    c[0].metric("AR overdue", money(ar["metrics"]["overdue"]),
                f"{ar['metrics']['overdue_pct']:.0f}% of AR · DSO {ar['metrics']['dso']:.0f}d",
                delta_color="off")
    c[1].metric("AP overdue", money(ap["metrics"]["overdue"]),
                f"DPO {ap['metrics']['dpo']:.0f}d", delta_color="off")
    c[2].metric("Tax overdue", money(tax["metrics"]["overdue"]),
                f"of {money(tax['metrics']['pending_total'])} pending", delta_color="off")
    c[3].metric("Due within 30 days",
                money(ap["metrics"]["upcoming_30d"] + tax["metrics"]["upcoming_30d"]),
                "AP + tax", delta_color="off")
    with st.expander("📄 Administration — Accounts Receivable, Payable and Tax"):
        st.markdown("**Accounts Receivable** — " + clean(ar["narrative"]))
        st.markdown("**Accounts Payable** — " + clean(ap["narrative"]))
        st.markdown("**Tax** — " + clean(tax["narrative"]))
    stamp("Accounts Receivable")
    stamp("Accounts Payable")
    stamp("Tax")


def render_stage_4():
    close, rep = A["Accounting & Close"], A["Financial Reporting"]
    st.markdown("#### 📒 Stage 4 · Close & the financial statements")
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
    stamp("Accounting & Close")
    stamp("Financial Reporting")


def render_stage_5():
    fpa = A["FP&A"]
    st.markdown("#### 📈 Stage 5 · FP&A — forecast & variances")
    f = fpa["forecast"]
    c = st.columns(4)
    c[0].metric("Next-month revenue (fcst)", money(f["revenue"]))
    c[1].metric("Next-month op income (fcst)", money(f["operating_income"]))
    oi = next(r for r in fpa["budget_variance"]["rows"] if r["label"] == "Operating income")
    c[2].metric("Op income vs budget", money(oi["var"]), f"{oi['var_pct']:.1f}% vs plan",
                delta_color="off")
    c[3].metric("Material lines vs plan", str(len(fpa["budget_variance"]["material"])))
    with st.expander("📄 FP&A — variance vs last month"):
        st.markdown(clean(fpa["variance_expl"]))
    with st.expander("📄 FP&A — variance vs budget"):
        st.markdown(clean(fpa["budget_expl"]))
    stamp("FP&A")


def render_stage_6():
    strat = A["Strategic Finance"]
    m = strat["metrics"]
    st.markdown("#### 🎯 Stage 6 · Strategic finance — growth quality & capital efficiency")
    c = st.columns(4)
    c[0].metric("ARR run-rate", money(m["run_rate"]))
    c[1].metric("Rule of 40", f"{m['rule_of_40']:.0f}", "≥ 40 is healthy", delta_color="off")
    c[2].metric("Burn multiple", f"{m['burn_multiple']:.1f}x", "≤ 2 is efficient", delta_color="off")
    c[3].metric("Magic number", f"{m['magic_number']:.2f}", "> 0.75 is good", delta_color="off")
    with st.expander("📄 Strategic Finance's full analysis"):
        st.markdown(clean(strat["narrative"]))
    stamp("Strategic Finance")


def render_stage_7():
    ctrls = A["Internal Controls"]
    summ = ctrls["summary"]
    st.markdown("#### 🛡️ Stage 7 · Internal controls — assurance")
    c = st.columns(4)
    c[0].metric("Controls passed",
                f"{summ['n_pass']} / {summ['n_pass'] + summ['n_fail'] + summ['n_exception']}")
    c[1].metric("Integrity failures", str(summ["n_fail"]))
    c[2].metric("Books balanced", "Yes" if summ["books_balanced"] else "No")
    c[3].metric("Authorization review", str(summ["approval_exceptions"]),
                f"payments ≥ $25k ({money(summ['approval_exceptions_total'])})", delta_color="off")
    with st.expander("📄 Control register"):
        for ck in ctrls["checks"]:
            mark = "✅" if ck["status"] == "PASS" else "⚠️"
            st.markdown(f"{mark} **{ck['name']}** — {clean(ck['detail'])}")
    stamp("Internal Controls")


def render_stage_8():
    aud = A["Audit"]
    st.markdown("#### 🔎 Stage 8 · Independent audit (third line)")
    st.markdown(f"<div class='opinion'>Audit opinion: <b>{aud['opinion'].upper()}</b> — "
                f"{aud['n_procedures']} procedures re-performed, {aud['n_exceptions']} exception(s).</div>",
                unsafe_allow_html=True)
    with st.expander("📄 Audit procedures (re-derived from the raw ledger & subledger)"):
        for fnd in aud["findings"]:
            mark = "✅" if fnd["ok"] else "⚠️"
            st.markdown(f"{mark} **{fnd['proc']}** — {clean(fnd['detail'])}")
    stamp("Audit")


SECTION = {1: render_stage_1, 2: render_stage_2, 3: render_stage_3, 4: render_stage_4,
           5: render_stage_5, 6: render_stage_6, 7: render_stage_7, 8: render_stage_8}


# --------------------------------------------------------------------------
# Consolidation: first-line summary, risk flags, the CFO gate, board pack,
# and the close-out summary.
# --------------------------------------------------------------------------

def all_escalations():
    esc = []
    for name in TOP_LEVEL:
        esc += A.get(name, {}).get("escalations", [])
    order = {"CRITICAL": 0, "HIGH": 1}
    return sorted(esc, key=lambda e: order.get(e[0], 9))


def render_consolidation():
    st.divider()
    st.markdown("### First line complete — 11 sign-offs by domain experts")
    st.markdown("<span class='small'>Maker-checker, the way finance actually works: the agent does "
                "the work, and the person with real depth in that area validates and signs. A "
                "generalist CFO can't competently approve every operational detail — so each "
                "function is owned by its expert. In production, each gate below is owned by the "
                "corresponding finance lead.</span>", unsafe_allow_html=True)
    st.table([{"Function": fn, "Signed off by (domain expert)": reviewer(fn),
               "Decision": "✓ Approved", "Signed at": gate(fn)["ts"]}
              for fn in ALL_FUNCTIONS])
    st.info("First line: 11/11 functions cleared their domain-expert checker, and the cross-checks "
            "passed — the agents agree on the shared numbers (operating income, burn, revenue/"
            "run-rate, AR, and Reporting's net income & cash).")

    escalations = all_escalations()
    st.markdown(f"**{len(escalations)} risk flags raised** (each owned by one agent, no double-counting):")
    for sev, msg in escalations:
        st.markdown(f"{sev_badge(sev)}&nbsp; {msg}", unsafe_allow_html=True)

    st.divider()
    st.markdown("### 👔 CFO final sign-off")
    g = st.session_state.cfo_gate

    if g["status"] == "blocked":
        st.error("⛔ **Close held at the CFO gate.** The CFO declined the consolidated sign-off "
                 "twice, so nothing is released: the board pack stays unpublished and the close "
                 "remains open with the full decision history in the audit trail. In production "
                 "the material items go back to their owning functions with the CFO's comments.")
        restart("restart_cfo_blocked")
        return

    if g["status"] != "approved":
        inc = A["Financial Reporting"]["income_statement"]
        cf = A["Financial Reporting"]["cash_flow"]
        trez = A["Treasury"]
        with st.container(border=True):
            st.markdown("#### ⏸ Approval required — CFO (final consolidated sign-off)")
            st.markdown("<span class='small'>The first line is complete — every function is signed "
                        "off by its domain expert. The CFO now approves the <b>consolidated board "
                        "pack and the material items</b> — not a re-review of every detail (that's "
                        "what the experts are for). <b>You are the CFO.</b></span>",
                        unsafe_allow_html=True)
            if g["status"] == "rework":
                st.warning(f"**Close held.** Your comments went back to the owning functions; the "
                           "material items were re-examined and the pack was **resubmitted** — "
                           f"attempt {g['attempts'] + 1} of {MAX_ATTEMPTS}. If you hold it again, "
                           "the close stops before release.")
            st.markdown("**You are approving:**")
            st.table([{"Item": k, "Value": v} for k, v in [
                ("Net income, " + PERIOD, f"{money(inc['net_income'])} ({inc['net_margin_pct']:.0f}% margin)"),
                ("Ending cash", money(cf["cash_end"])),
                ("Runway", f"{trez['runway']:.1f} months"),
                ("Operating-model stages", f"{len(STAGES)}/{len(STAGES)} passed their deterministic "
                                           "control and domain-expert sign-off"),
                ("First-line sign-offs", f"{len(ALL_FUNCTIONS)}/{len(ALL_FUNCTIONS)} approved in this session"),
                ("Risk flags on the register", str(len(all_escalations()))),
                ("Effect of approval", "Releases the consolidated board pack"),
            ]])
            c1, c2 = st.columns(2)
            if c1.button("✓ Final sign-off as CFO — release the board pack", key="cfo_ap",
                         type="primary", use_container_width=True):
                decide_cfo(True)
            if c2.button("✗ Hold the close / send back with comments", key="cfo_rj",
                         use_container_width=True):
                decide_cfo(False)
        return

    # Approved: board pack + close-out summary.
    cfo = A["CFO"]
    st.markdown(f"<span class='stamp'>&#10003; Final consolidated sign-off · CFO · {g['ts']}</span>",
                unsafe_allow_html=True)
    st.success("Close complete — the board pack is released.")
    st.markdown("### 📋 Board pack")
    st.markdown(f"<div class='boardpack'>{clean(cfo['board_pack'])}</div>", unsafe_allow_html=True)
    st.markdown("#### Recommended actions")
    st.markdown(clean(cfo["actions"]))
    st.caption("Generated by the CFO agent from the eight agents' inputs — every figure traces "
               "back to code-computed numbers.")
    render_closeout()


def render_closeout():
    st.divider()
    st.markdown("### Close summary — what just happened")
    close, rep = A["Accounting & Close"], A["Financial Reporting"]
    ctrls, aud = A["Internal Controls"]["summary"], A["Audit"]
    n_recs = len(close["reconciliations"]["recs"])
    n_controls = ctrls["n_pass"] + ctrls["n_fail"] + ctrls["n_exception"]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Processed by the AI office**")
        st.markdown(
            f"- {PERIOD} close for Lumen Inc., end to end in {len(STAGES)} controlled stages\n"
            f"- {n_recs} subledger reconciliations tied to the general ledger, plus the retained-"
            "earnings roll-forward\n"
            "- 3 financial statements produced, articulated and footed\n"
            f"- {n_controls} control checks run ({ctrls['n_pass']} passed, "
            f"{ctrls['n_exception']} authorization exception flagged for review)\n"
            f"- Independent audit: **{aud['opinion'].upper()}** opinion, "
            f"{aud['n_procedures']} procedures re-performed\n"
            f"- {len(all_escalations())} risk flags raised and placed on the CFO's register\n"
            f"- Net income {money(rep['income_statement']['net_income'])} · ending cash "
            f"{money(rep['cash_flow']['cash_end'])} — the same numbers at every gate")
    with c2:
        st.markdown("**Human control exercised in this session**")
        n_decisions = len(st.session_state.signoff_log)
        elapsed = ""
        if st.session_state.t_start and st.session_state.t_end:
            secs = int(st.session_state.t_end - st.session_state.t_start)
            elapsed = f"{secs // 60}m {secs % 60:02d}s"
        st.markdown(
            f"- **{N_GATES} sign-off gates**, every one approved live at this console\n"
            f"- {n_decisions} recorded review decisions (including any rework cycles)\n"
            + (f"- {elapsed} from start of the close to board-pack release\n" if elapsed else "")
            + "- The humans decided; the agents did the processing, reconciliation, statements, "
              "controls, audit re-derivation and commentary\n"
            "- Every decision is time-stamped in the sign-off log below")
    st.markdown("**Gates approved in this session**")
    st.table([{"Gate": e["item"], "Approver": e["role"], "Decision": e["action"], "Time": e["ts"]}
              for e in st.session_state.signoff_log])
    restart("restart_done")


# --------------------------------------------------------------------------
# Header.
# --------------------------------------------------------------------------

h1, h2 = st.columns([6, 1])
with h1:
    st.title("📊 CFO AI Office")
with h2:
    if st.session_state.started:
        restart("restart_top")

st.markdown(
    "#### An AI finance operating model that runs the month-end close from raw data to board "
    "pack — and pauses at every human approval gate."
)
st.markdown(
    "<span class='small'>Eight specialist agents — across accounting, treasury, working capital, "
    "planning, controls and audit — do the work, and <b>each function is signed off by its own "
    "domain expert</b> (maker-checker: the Tax Manager signs tax, the Treasurer signs treasury, "
    "and so on). The <b>CFO</b> then gives a single <b>final sign-off</b> on the consolidated "
    "board pack and the material items. Running on a synthetic SaaS company, <b>Lumen Inc.</b>, "
    f"closing <b>{PERIOD}</b> — the architecture points at production data.</span>",
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
        "and the **CFO gives the final consolidated sign-off**.\n"
        "- **The approvals here are live**: the close pauses at every gate until the named role "
        "approves it at this console. Nothing advances on its own. In your company, each gate "
        "would be owned by the corresponding finance lead.\n"
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
# The framing screen, then the gated close.
# --------------------------------------------------------------------------

if not st.session_state.started:
    st.markdown("### ▶️  Run the month-end close — with you in control")
    st.markdown(
        "<span class='small'>The close runs as <b>8 controlled stages</b>. At each stage the agents "
        "do the work, a <b>deterministic control in code</b> must hold, and the close <b>pauses</b> "
        "until the stage's domain expert signs off. In this session <b>you hold every approval</b>: "
        "the 11 first-line sign-offs and the CFO's final one. Approving advances the close; "
        "rejecting sends the work back for rework — and a stage that can't clear its review "
        "blocks the entire close.</span>", unsafe_allow_html=True)
    st.markdown("**Where the humans intervene** — the 12 gates you will own:")
    g1, g2 = st.columns(2)
    half = (len(STAGES) + 1) // 2
    for col, chunk in ((g1, STAGES[:half]), (g2, STAGES[half:])):
        with col:
            for s in chunk:
                names = " · ".join(dict.fromkeys(reviewer(fn) for fn in s["functions"]))
                col.markdown(f"<div class='card'><b>Stage {s['id']} · {s['icon']} {s['name']}</b><br>"
                             f"<span class='small'>Sign-off: {names}"
                             f"{' (' + str(len(s['functions'])) + ' gates)' if len(s['functions']) > 1 else ''}"
                             "</span></div>", unsafe_allow_html=True)
    g2.markdown("<div class='card'><b>Final gate · 👔 CFO</b><br><span class='small'>"
                "Consolidated sign-off — releases the board pack.</span></div>",
                unsafe_allow_html=True)
    if st.button(f"▶ Start the {PERIOD} close for Lumen Inc.", type="primary", key="start_close"):
        st.session_state.started = True
        st.session_state.t_start = time.time()
        st.rerun()

else:
    st.markdown("### The close, stage by stage")
    pipeline_strip()

    halted = False
    for s in STAGES:
        # Processing indicator the first time a stage becomes current.
        if s["id"] not in st.session_state.worked:
            with st.spinner(s["working"]):
                time.sleep(WORK_SECONDS)
            st.session_state.worked.append(s["id"])
            st.rerun()

        SECTION[s["id"]]()

        pending = next((fn for fn in s["functions"] if not approved(fn)), None)
        if pending:
            if gate(pending)["status"] == "blocked":
                blocked_panel(pending, s)
            else:
                approval_panel(pending, s)
            halted = True
            break
        st.divider()

    if not halted:
        if CFO_STAGE_ID not in st.session_state.worked:
            with st.spinner(CFO_WORKING):
                time.sleep(WORK_SECONDS)
            st.session_state.worked.append(CFO_STAGE_ID)
            st.rerun()
        render_consolidation()


# --------------------------------------------------------------------------
# Play widgets (recompute live from the close's numbers).
# --------------------------------------------------------------------------

st.divider()
st.markdown("### 🎛️  Play with the model")
st.markdown("<span class='small'>These recompute live from the close's numbers — straight from "
            "the deterministic engine that drives the decisions.</span>", unsafe_allow_html=True)

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

def is_recorded_approval(e):
    """Engine-run approval events are superseded by this session's live gates."""
    detail = e.get("detail", "")
    return (detail.endswith("(auto)")
            or (e.get("agent") == "CFO" and e.get("status") == "approved"))


st.divider()
with st.expander("🔍 Audit trail — every step is logged (governance)"):
    st.markdown("**Sign-off log — this session** <span class='small'>(live decisions taken at "
                "this console)</span>", unsafe_allow_html=True)
    if st.session_state.signoff_log:
        for e in st.session_state.signoff_log:
            st.markdown(f"<span class='small'><code>{e['ts']}</code> · <b>{e['role']}</b> · "
                        f"{e['item']} — {e['action']}</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span class='small'>No sign-offs yet — the log fills in as you approve "
                    "each gate.</span>", unsafe_allow_html=True)
    st.markdown("**Process log — the engine run** <span class='small'>(agents' work, stage "
                "controls and escalations, as executed)</span>", unsafe_allow_html=True)
    for e in DATA["audit"]:
        if is_recorded_approval(e):
            continue
        st.markdown(f"<span class='small'><code>{e['ts']}</code> · <b>{e['agent']}</b> · "
                    f"{e['status']} — {e['detail']}</span>", unsafe_allow_html=True)

st.divider()
st.markdown(
    "<span class='small'>Built by <b>Ignacio Viola</b> · 17 years in senior finance, now building AI "
    "systems for finance operations · Synthetic data; architecture built to point at production data · "
    "Source: <a href='https://github.com/ignacioviola1984-spec/ai-finance-engineering'>GitHub</a></span>",
    unsafe_allow_html=True)
