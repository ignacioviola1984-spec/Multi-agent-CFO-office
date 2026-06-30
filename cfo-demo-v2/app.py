"""
app.py - AI Finance Operating Model, public demo v2 (HR-friendly).

Walks one synthetic finance function end to end, the way data actually flows:
  1. ERP            - pull data in from QuickBooks (read-only), land it in a
                      single standard format, validate it, save a sealed copy.
  2. O2C tower      - work the receivables: collections, cash application, DSO,
                      a hard-control gate that blocks reporting when it must.
  3. Month-end close- eight specialist agents turn it into the three statements
                      and a board pack, with maker-checker sign-off.
  4. Evals          - four offline scoreboards proving the numbers hold.
  5. Self-improve   - the system retunes itself, bounded and human-gated.

Every NUMBER is computed by code (deterministic, auditable) - the snapshots in
./snapshots were produced by build_snapshots.py running the real engine offline.
The app only renders them, so it is instant, free, and needs no API key.

Run locally:  python -m streamlit run app.py
Deploy:       Streamlit Community Cloud, main file = cfo-demo-v2/app.py (no secrets).
Source:       github.com/ignacioviola1984-spec/ai-finance-engineering
"""

import json
import os

import streamlit as st
import streamlit.components.v1 as components

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "snapshots")


def scroll_to(target):
    """Reliably scroll the main view. target='top' or an element id.

    Uses an iframe (components.html) because it is the Streamlit primitive that
    actually executes injected JavaScript (st.html does not run scripts). Two
    gotchas handled here:
      - Streamlit reuses an iframe whose HTML is unchanged, so the script never
        re-runs; a per-call nonce forces a fresh iframe each time.
      - Streamlit/the browser re-adjusts the scroll a few frames after the rerun
        renders, so a one-shot scroll gets overwritten; we hold the target for
        ~700ms with a short timer loop to win that race."""
    nonce = st.session_state.get("_scroll_n", 0) + 1
    st.session_state["_scroll_n"] = nonce
    if target == "top":
        step = ("const e=d.querySelector('[data-testid=\"stMain\"]')||d.querySelector('section.main');"
                "if(e)e.scrollTop=0;window.parent.scrollTo(0,0);")
    else:
        step = (f"const el=d.getElementById('{target}');if(el)el.scrollIntoView({{block:'start'}});")
    components.html(
        f"<script>/*{nonce}:{target}*/const d=window.parent.document;let n=0;"
        f"const loop=()=>{{{step}if(n++<42)setTimeout(loop,16);}};loop();</script>", height=0)

st.set_page_config(page_title="AI Finance Operating Model - Live Demo",
                   page_icon="📊", layout="wide", initial_sidebar_state="expanded")


# --------------------------------------------------------------------------
# Data.
# --------------------------------------------------------------------------

@st.cache_data
def load(name):
    with open(os.path.join(SNAP, name), encoding="utf-8") as f:
        return json.load(f)

SOURCES = load("sources.json")
O2C = load("o2c.json")
CLOSE = load("close.json")
EVALS = load("evals.json")
SI = load("selfimprove.json")


# --------------------------------------------------------------------------
# Formatting helpers (ported from v1).
# --------------------------------------------------------------------------

def money(x):
    try:
        return f"${x:,.0f}"
    except (TypeError, ValueError):
        return "-"


def money_m(x):
    """Compact USD for large figures: $18.5M, $327K."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "-"
    a = abs(x)
    if a >= 1_000_000:
        return f"${x/1_000_000:,.1f}M"
    if a >= 1_000:
        return f"${x/1_000:,.0f}K"
    return f"${x:,.0f}"


def clean(text):
    """Trim a dangling incomplete final sentence, then escape '$' so Streamlit
    does not render $...$ as LaTeX math."""
    text = (text or "").strip()
    if text and text[-1] not in ".!?\")*":
        cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if cut > 0:
            text = text[: cut + 1]
    return text.replace("$", "\\$")


def sev_badge(sev):
    color = {"CRITICAL": "#C0392B", "URGENT": "#C0392B", "HIGH": "#D97706",
             "REVIEW": "#D97706", "MEDIUM": "#4A6FA5"}.get(sev, "#4A6FA5")
    return (f"<span style='background:{color};color:#fff;padding:2px 8px;"
            f"border-radius:6px;font-size:0.72rem;font-weight:700'>{sev}</span>")


def status_dot(status):
    color = {"PASS": "#0F6E56", "OK": "#0F6E56", "WARNING": "#D97706",
             "REVIEW": "#D97706", "URGENT": "#C0392B", "FAIL": "#C0392B",
             "CRITICAL": "#C0392B", "EXCEPTION": "#D97706"}.get(status, "#4A6FA5")
    return f"<span style='color:{color};font-weight:700'>&#9679;</span>"


def stmt_table(rows):
    st.table([{"": label, " ": val} for label, val in rows])


def fmt_cell(v):
    """Display a possibly-numeric, possibly-string, possibly-None cell."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{v:,g}" if isinstance(v, float) else f"{v:,}"
    return str(v)


# --------------------------------------------------------------------------
# Styling.
# --------------------------------------------------------------------------

st.markdown("""
<style>
.small { color:var(--text-color); opacity:0.9; font-size:0.9rem; }
.tiny  { color:var(--text-color); opacity:0.7; font-size:0.8rem; }
.card { background:rgba(127,127,127,0.06); border:1px solid rgba(127,127,127,0.18);
        border-radius:12px; padding:14px 16px; margin-bottom:8px; }
.cardgrid { display:grid; gap:10px; margin-bottom:8px; grid-auto-rows:1fr; }
.cardgrid.five { grid-template-columns:repeat(5,1fr); }
.cardgrid.four { grid-template-columns:repeat(4,1fr); }
.gcard { background:rgba(127,127,127,0.06); border:1px solid rgba(127,127,127,0.18);
         border-radius:12px; padding:14px 16px; }
@media (max-width:900px){ .cardgrid.five,.cardgrid.four{ grid-template-columns:repeat(2,1fr); } }
.role { font-weight:700; font-size:0.98rem; }
.boardpack { background:rgba(27,42,74,0.06); border-left:4px solid #1B2A4A;
             border-radius:8px; padding:18px 22px; }
.opinion { background:rgba(15,110,86,0.10); border-left:4px solid #0F6E56;
           border-radius:8px; padding:12px 16px; font-weight:600; }
.blocked { background:rgba(192,57,43,0.10); border-left:4px solid #C0392B;
           border-radius:8px; padding:12px 16px; font-weight:600; }
.step { background:rgba(127,127,127,0.06); border:1px solid rgba(127,127,127,0.18);
        border-radius:10px; padding:10px 14px; margin:4px 0; }
.flow { text-align:center; font-size:0.92rem; }
.pill { display:inline-block; background:rgba(74,111,165,0.15); border-radius:20px;
        padding:3px 12px; margin:2px; font-size:0.82rem; font-weight:600; }
</style>
""", unsafe_allow_html=True)


def honest(note):
    st.caption("⚖️ Honest boundary: " + note)


def section_title(num, title, subtitle):
    st.markdown(f"## {num} · {title}")
    st.markdown(f"<span class='small'>{subtitle}</span>", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Sidebar navigation.
# --------------------------------------------------------------------------

st.sidebar.markdown("### 📊 AI Finance Operating Model")
st.sidebar.markdown("<span class='tiny'>A working multi-agent finance system. "
                    "Follow the data from the ERP to the board pack.</span>",
                    unsafe_allow_html=True)
st.sidebar.divider()

NAV = [
    "🏠  Overview",
    "1 · ERP - data in",
    "2 · O2C control tower",
    "3 · Month-end close",
    "4 · Evals - does it hold?",
    "5 · Self-improvement",
]
choice = st.sidebar.radio("Walk the model", NAV, label_visibility="collapsed")

# Scroll the main view back to the top whenever the station changes (Streamlit
# otherwise keeps the previous scroll position, landing you mid-page).
if st.session_state.get("_last_nav") != choice:
    st.session_state["_last_nav"] = choice
    scroll_to("top")

st.sidebar.divider()
st.sidebar.markdown(
    "<span class='tiny'>Every number is computed by code and verified offline "
    "(see the Evals tab). The AI agents read the numbers and write the "
    "commentary - they never invent a figure.<br><br>"
    "Built by <b>Ignacio Viola</b> · 17 years in senior finance.<br>"
    "<a href='https://github.com/ignacioviola1984-spec/ai-finance-engineering'>Source on GitHub</a></span>",
    unsafe_allow_html=True)


# ==========================================================================
# OVERVIEW
# ==========================================================================

def render_overview():
    st.title("📊 AI Finance Operating Model")
    st.markdown("#### A working multi-agent AI system that runs a finance function "
                "end to end - from the ERP to the board pack - with deterministic "
                "numbers and human control at every gate.")

    st.markdown("<span class='small'>This demo follows one synthetic company's data "
                "through the whole lifecycle. Each station is real software, not slides; "
                "the numbers come from code and are regression-tested. Use the sidebar, "
                "or read the five stations below.</span>", unsafe_allow_html=True)

    st.markdown(
        "<div class='flow card'>"
        "<span class='pill'>1 · ERP data in</span> ➜ "
        "<span class='pill'>2 · O2C control tower</span> ➜ "
        "<span class='pill'>3 · Month-end close</span> ➜ "
        "<span class='pill'>4 · Evals</span> ➜ "
        "<span class='pill'>5 · Self-improvement</span><br>"
        "<span class='tiny'>data comes in → cash gets collected → the books get closed "
        "→ the results get verified → the system gets better</span>"
        "</div>", unsafe_allow_html=True)

    with st.expander("ℹ️  What am I looking at? (30-second version)"):
        st.markdown(
            "- A **multi-agent AI system for corporate finance** - real, running software.\n"
            "- It runs the full lifecycle: **pull from the ERP → work the receivables → "
            "close the books → verify → improve**.\n"
            "- **Every number is computed by code** (deterministic, auditable). The AI agents "
            "read the numbers, reason, and write commentary - they never invent a figure. "
            "That is the core design rule.\n"
            "- The engine reads **one standard format**, so QuickBooks today and "
            "NetSuite/SAP tomorrow plug in with zero engine changes.\n"
            "- **Human control is built in:** read-only ERP access, hard control gates that block "
            "reporting, maker-checker sign-off, an independent audit, and bounded self-improvement "
            "no one can widen.\n"
            "- This page **replays saved runs** so it is instant and free. Human approvals are "
            "simulated in this public version, and that is disclosed honestly wherever it applies.\n"
            "- Built by **Ignacio Viola** - 17 years in senior finance, now building the AI systems. "
            "Full source on [GitHub](https://github.com/ignacioviola1984-spec/ai-finance-engineering)."
        )

    st.divider()
    st.markdown("##### The five stations")
    cards = [
        ("1 · ERP - data in", "Pull from QuickBooks (read-only) into one standard format and "
         f"run {SOURCES['clean']['n_ok']}/{SOURCES['clean']['n_total']} validations."),
        ("2 · O2C control tower", "Collections, cash application, DSO, disputes, credit - "
         "with a hard gate that blocks reporting when controls fail."),
        ("3 · Month-end close", "Eight specialist agents produce the three financial "
         "statements and a board pack, each function signed off by its domain expert."),
        ("4 · Evals - does it hold?", "Four offline scoreboards: 22/22 numbers, 12/12 safety, "
         "48/48 O2C, 17/17 against real audited SEC filings."),
        ("5 · Self-improvement", "The system gets better over time, but only within strict "
         "limits, only with sign-off from the right person, and every change can be undone."),
    ]
    cards_html = "".join(
        f"<div class='gcard'><div class='role'>{title}</div><div class='tiny'>{desc}</div></div>"
        for title, desc in cards)
    st.markdown(f"<div class='cardgrid five'>{cards_html}</div>", unsafe_allow_html=True)

    st.divider()
    st.markdown("##### Why this matters")
    c = st.columns(3)
    c[0].metric("Numbers accuracy", f"{EVALS['numbers']['passed']}/{EVALS['numbers']['total']}",
                "the month-end close", delta_color="off")
    c[1].metric("vs real audited SEC data (dLocal)", f"{EVALS['dlocal']['passed']}/{EVALS['dlocal']['total']}",
                "NASDAQ: DLO FY2024-25", delta_color="off")
    c[2].metric("Control-tower tests", f"{EVALS['o2c_suite']['passed']}/{EVALS['o2c_suite']['total']}",
                f"{EVALS['o2c_blind']['caught']}/{EVALS['o2c_blind']['planted']} planted errors found", delta_color="off")


# ==========================================================================
# STATION 1 - ERP / DATA SOURCES
# ==========================================================================

def render_erp():
    section_title("1", "ERP - data in (any system → one standard format)",
                  "The engine never has to learn each accounting system's own labels. Any system "
                  "is translated into <b>one standard format</b>, checked against a set of rules, "
                  "and saved as a <b>sealed, tamper-evident copy</b> before a single number is "
                  "reported.")

    st.markdown(
        "<div class='flow card'>"
        "<span class='pill'>QuickBooks Online</span> ➜ "
        "<span class='pill'>read-only connection</span> ➜ "
        "<span class='pill'>translate → standard tables</span> ➜ "
        "<span class='pill'>validate</span> ➜ "
        "<span class='pill'>sealed copy</span> ➜ "
        "<span class='pill'>engine</span></div>", unsafe_allow_html=True)

    src = st.radio("Data source", ["QuickBooks Online", "Synthetic (Lumen)"],
                   horizontal=True, key="erp_source")

    if src.startswith("QuickBooks"):
        st.markdown("#### What we pulled from QuickBooks (read-only)")
        st.markdown("<span class='small'>One recorded pull, translated into the standard chart of "
                    "accounts. The connection has <b>no write capability at all</b> - read-only is "
                    "enforced in code, not just by permission.</span>",
                    unsafe_allow_html=True)
        p = SOURCES["pnl"]; bs = SOURCES["balance_sheet"]; tb = SOURCES["trial_balance"]
        c = st.columns(4)
        c[0].metric("Revenue", money(p["revenue"]))
        c[1].metric("Operating income", money(p["operating_income"]))
        c[2].metric("Balance sheet check (A−L−E)", money(bs["check"]),
                    "foots to zero" if abs(bs["check"]) < 1 else "off", delta_color="off")
        c[3].metric("Trial balance", "Balances" if abs(tb["debits"] - tb["credits"]) < 1 else "Off",
                    f"Debits {money(tb['debits'])} = Credits {money(tb['credits'])}", delta_color="off")

        cc = st.columns(2)
        with cc[0]:
            st.markdown("**Standardized balance sheet**")
            st.table([{"Account": r["account"], "USD": money(r["amount_usd"])}
                      for r in SOURCES["preview"]["balance_sheet"]])
        with cc[1]:
            st.markdown("**Standardized chart of accounts** (12 rollup codes)")
            st.table([{"Code": r["code"], "Account": r["account"], "Type": r["type"]}
                      for r in SOURCES["preview"]["chart_of_accounts"]])
    else:
        sc = SOURCES["synthetic_scale"]
        st.markdown("#### The synthetic source (Lumen Inc.) - the consolidation path")
        st.markdown("<span class='small'>Identical standard format, but a multi-entity, "
                    "multi-currency company. This is what proves the swap: the engine code "
                    "does not change between sources.</span>", unsafe_allow_html=True)
        c = st.columns(4)
        c[0].metric("Legal entities", sc["entities"])
        c[1].metric("Currencies", sc["currencies"])
        c[2].metric("FX rate rows", sc["fx_rate_rows"])
        c[3].metric("P&L activity rows", sc["pnl_rows"])
        st.info("Same standard tables, same columns as the QuickBooks output - identical down to "
                "the column headers. Swapping the source touches zero engine code.")

    st.divider()
    st.markdown("#### Automated validations (no AI involved)")
    st.markdown("<span class='small'>Before any number is trusted, the standardized data must pass "
                f"all {SOURCES['clean']['n_total']} checks. These are plain code, not the model's "
                "opinion.</span>", unsafe_allow_html=True)

    tamper = st.radio(
        "Inject a problem and watch a named control fire:",
        ["None - clean data"] + [t["label"] for t in SOURCES["tampers"]],
        index=0, key="erp_tamper")
    if tamper.startswith("None"):
        checks = SOURCES["clean"]["checks"]
        broken = []
    else:
        t = next(t for t in SOURCES["tampers"] if t["label"] == tamper)
        checks = t["checks"]
        broken = t["broken"]
        st.markdown(f"<div class='blocked'>⛔ Tamper applied. The control "
                    f"<b>{', '.join(broken)}</b> caught it - owned by {t['owner']}. "
                    f"Reporting would be blocked.</div>", unsafe_allow_html=True)

    cols = st.columns(2)
    for i, ck in enumerate(checks):
        col = cols[i % 2]
        mark = "✅" if ck["ok"] else "❌"
        col.markdown(f"{mark} **{ck['name']}** - <span class='tiny'>{clean(ck['detail'])}</span>",
                     unsafe_allow_html=True)
    if not broken:
        st.success(f"All {SOURCES['clean']['n_total']} validations pass. The data is safe to report on.")

    st.divider()
    st.markdown("#### Sealed, tamper-evident copy (audit-grade)")
    m = SOURCES["manifest"]
    c = st.columns(4)
    c[0].metric("Source files sealed", m["n_raw_files"])
    c[1].metric("Standard files sealed", m["n_canonical_files"])
    c[2].metric("Validation", "PASS" if m["validation_pass"] else "FAIL")
    c[3].metric("Saved at (UTC)", m["extract_timestamp"][:10])
    st.markdown("<span class='small'>Every pull is saved as a sealed, append-only copy with a "
                "record of what it contains: row counts, period, source, timestamp, and a "
                "<b>digital fingerprint of every file</b>. Re-running on the same input produces "
                "identical fingerprints - reproducible and tamper-evident.</span>",
                unsafe_allow_html=True)
    with st.expander("🔍 Sample digital fingerprints"):
        for k, v in m["sample_hashes"].items():
            st.markdown(f"<span class='tiny'><code>{k.split('/')[-1]}</code> → <code>{v}</code></span>",
                        unsafe_allow_html=True)


# ==========================================================================
# STATION 2 - O2C CONTROL TOWER
# ==========================================================================

def render_o2c():
    section_title("2", "Order-to-Cash control tower",
                  "A sub-orchestration that ingests 15 interlocking tables (CRM → contracts → "
                  "billing → cash), computes every receivables number in code, runs 25 controls, "
                  "and <b>blocks reporting</b> when the hard controls fail. Ten agents diagnose "
                  "and rank the issues; an independent audit agent re-performs the tie-outs.")

    pick = st.radio("Pick a month to run the control tower on:",
                    ["🔴 Broken month (2026-05)", "🟢 Clean month (2026-06)"], horizontal=True,
                    key="o2c_period")
    period = "2026-05" if pick.startswith("🔴") else "2026-06"
    d = O2C[period]
    cs = d["controls_summary"]; s = d["summary"]
    blocked = d["final_status"] == "BLOCKED_HARD_CONTROLS"

    if blocked:
        st.markdown(f"<div class='blocked'>⛔ <b>{d['final_status']}</b> - "
                    f"{cs['hard_failures']} of {cs['hard']} hard controls failed, so the pipeline "
                    f"will not release a report. Independent audit opinion: "
                    f"<b>{d['audit_opinion'].upper()}</b> (score {d['audit_score']}%).</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='opinion'>✅ <b>{d['final_status']}</b> - "
                    f"0 hard-control failures (only {cs['soft_warnings']} soft warnings). "
                    f"Independent audit opinion: <b>{d['audit_opinion'].upper()}</b> "
                    f"(score {d['audit_score']}%).</div>", unsafe_allow_html=True)

    st.markdown("#### The receivables, in the CFO's language")
    c = st.columns(4)
    c[0].metric("DSO (days sales outstanding)", f"{s['dso']:.0f}d", f"best possible {s['best_possible_dso']:.0f}d",
                delta_color="off")
    c[1].metric("Overdue AR", money_m(s["overdue_ar_usd"]), f"of {money_m(s['open_ar_usd'])} open",
                delta_color="off")
    c[2].metric("Unapplied cash", money_m(s["unapplied_cash_usd"]), "cash received, not yet matched",
                delta_color="off")
    c[3].metric("Disputed AR", money_m(s["disputed_ar_usd"]), f"{s['disputed_ar_pct']:.1f}% of AR",
                delta_color="off")
    c = st.columns(4)
    c[0].metric("Control pass rate", f"{cs['pass_rate_pct']:.0f}%", f"{cs['pass_count']}/{cs['total']} controls",
                delta_color="off")
    c[1].metric("Unbilled revenue (leakage)", money_m(s["unbilled_revenue_usd"]),
                "billed late or not at all", delta_color="off")
    c[2].metric("Credit-limit breach", money_m(s["credit_breach_amount_usd"]), "exposure over limit",
                delta_color="off")
    c[3].metric("Expected cash (13 weeks)", money_m(s["expected_cash_13w_usd"]), "collections forecast",
                delta_color="off")

    cc = st.columns(2)
    with cc[0]:
        st.markdown("**AR aging**")
        try:
            import pandas as pd
            df = pd.DataFrame(d["aging"])
            if not df.empty and "aging_bucket" in df and "open_ar_usd" in df:
                st.bar_chart(df.set_index("aging_bucket")["open_ar_usd"], height=240)
        except Exception:
            st.table([{"Bucket": r.get("aging_bucket"), "Open AR": money(r.get("open_ar_usd"))}
                      for r in d["aging"]])
    with cc[1]:
        st.markdown("**Bookings → Billings → Revenue → Cash**")
        try:
            import pandas as pd
            bdf = pd.DataFrame(d["bridge"], columns=["stage", "usd"]).set_index("stage")
            st.bar_chart(bdf["usd"], height=240)
        except Exception:
            for label, amt in d["bridge"]:
                st.markdown(f"- {label}: **{money(amt)}**")

    st.divider()
    st.markdown(f"#### Top issues the agents raised ({len(d['top_issues'])} shown, severity-ranked)")
    for e in d["top_issues"]:
        st.markdown(f"{sev_badge(e['severity'])}&nbsp; <b>{e['agent']}</b> - {clean(e['message'])}",
                    unsafe_allow_html=True)
    honest("Maker-checker sign-offs in this replay are auto-approved (no reviewer is at the "
           "console when the snapshot is generated). The controls, the hard-fail gate and the "
           "audit trail are real and run on every pass; what is simulated is the human keystroke.")

    with st.expander(f"🛡️ Full control register ({cs['total']} controls: {cs['hard']} hard + {cs['soft']} soft)"):
        st.table([{
            "ID": str(c["control_id"]), "Control": str(c["name"]), "Sev": str(c["severity"]),
            "Status": str(c["status"]), "Owner": str(c["owner"]),
            "Failing $": money(c["failing_amount_usd"]) if c["failing_amount_usd"] else "-",
            "Blocks": "⛔" if c["blocks_reporting"] else "",
        } for c in d["controls"]])

    with st.expander(f"📐 Governed metrics ({len(d['metrics'])}, each with owner + threshold band)"):
        st.table([{
            "Metric": str(m["name"]), "Value": fmt_cell(m["value"]),
            "Unit": str(m["unit"] or ""), "Status": str(m["status"] or ""),
            "Owner": str(m["owner"] or ""), "Threshold": fmt_cell(m["threshold"]),
        } for m in d["metrics"]])

    st.caption(f"Input scale: {sum(v for v in d['input_record_counts'].values() if isinstance(v,(int,float))):,.0f} "
               f"source rows across {len(d['input_record_counts'])} tables, {d['n_agents']} agents, "
               f"{cs['total']} controls, {len(d['metrics'])} metrics - all deterministic.")


# ==========================================================================
# STATION 3 - MONTH-END CLOSE (ported from v1)
# ==========================================================================

def render_close():
    A = CLOSE["agents"]
    PERIOD = "May 2026"
    TOP_LEVEL = ["Controller", "Treasury", "Administration", "Accounting & Reporting",
                 "FP&A", "Strategic Finance", "Internal Controls", "Audit"]

    def signoff(name):
        r = A.get(name, {}).get("review")
        if not r:
            return
        auto = " <span style='color:#92400E;font-weight:600'>(auto-approved in this replay)</span>" \
            if r.get("mode") == "auto" else ""
        st.markdown(f"<span style='color:#0F6E56;font-size:0.82rem;font-weight:600'>"
                    f"&#10003; First-line sign-off · {r['reviewer']}</span>{auto}",
                    unsafe_allow_html=True)

    section_title("3", "Month-end close - the CFO office",
                  "Eight specialist agents run the loop <b>record → close → report → analyze → "
                  "control → audit</b>. Every figure is code-computed; the agents write the "
                  "commentary. Two-tier sign-off: each function's domain expert signs first, then "
                  f"you act as CFO for the final gate. Closing <b>{PERIOD}</b> for Lumen Inc.")

    st.markdown("##### The team - a two-level finance org")
    team_rows = [
        [("🧾 Controller", "Close review: P&L consistency, margins, risk flags."),
         ("💵 Treasury", "Cash, burn, runway, 13-week cash forecast."),
         ("🗂️ Administration", "Supervises AR · AP · Tax."),
         ("📒 Accounting & Reporting", "Supervises the close and the 3 statements.")],
        [("📈 FP&A", "Forecast + variances (vs last month and vs budget)."),
         ("🎯 Strategic Finance", "Growth quality, capital efficiency, path to breakeven."),
         ("🛡️ Internal Controls", "Trial balance, FX, cutoff, authorizations."),
         ("🔎 Audit", "Independent third line: re-derives the figures, issues an opinion.")],
    ]
    team_html = "".join(
        f"<div class='gcard'><div class='role'>{role}</div><div class='tiny'>{desc}</div></div>"
        for row in team_rows for role, desc in row)
    st.markdown(f"<div class='cardgrid four'>{team_html}</div>", unsafe_allow_html=True)

    st.divider()
    if "close_ran" not in st.session_state:
        st.session_state.close_ran = False
    if "close_approved" not in st.session_state:
        st.session_state.close_approved = False

    if not st.session_state.close_ran:
        st.markdown("### ▶️  Run the month-end close")
        if st.button("Run the close", type="primary", key="run_close"):
            st.session_state.close_ran = True
            st.rerun()
        return

    ctrl, trez = A["Controller"], A["Treasury"]
    ar, ap, tax = A["Accounts Receivable"], A["Accounts Payable"], A["Tax"]
    close, rep = A["Accounting & Close"], A["Financial Reporting"]
    fpa, strat = A["FP&A"], A["Strategic Finance"]
    ctrls, aud, cfo = A["Internal Controls"], A["Audit"], A["CFO"]

    st.markdown("### 1 · The agents report")
    st.markdown("#### 🧾 Controller - the close")
    c = st.columns(4)
    c[0].metric("Revenue", money(ctrl["pnl"]["revenue"]))
    c[1].metric("Operating income", money(ctrl["pnl"]["operating_income"]),
                f"{ctrl['op_margin_pct']:.1f}% margin", delta_color="off")
    c[2].metric("Gross margin", f"{ctrl['gross_margin_pct']:.1f}%")
    c[3].metric("Receivables overdue", f"{ctrl['ar']['overdue_pct']:.0f}%", "of total AR", delta_color="off")
    signoff("Controller")

    st.markdown("#### 💵 Treasury - liquidity")
    f13 = trez.get("forecast", {})
    c = st.columns(4)
    c[0].metric("Cash", money(trez["cash"]))
    c[1].metric("Monthly burn", money(trez["burn"]))
    c[2].metric("Runway", f"{trez['runway']:.1f} months")
    if f13:
        c[3].metric("13-week ending cash", money(f13["ending_cash"]),
                    "stays positive" if not f13.get("week_cash_negative") else "goes negative",
                    delta_color="off")
    signoff("Treasury")

    st.markdown("#### 🗂️ Administration - working capital & tax (AR · AP · Tax)")
    c = st.columns(4)
    c[0].metric("AR overdue", money(ar["metrics"]["overdue"]),
                f"{ar['metrics']['overdue_pct']:.0f}% · DSO {ar['metrics']['dso']:.0f}d", delta_color="off")
    c[1].metric("AP overdue", money(ap["metrics"]["overdue"]), f"DPO {ap['metrics']['dpo']:.0f}d", delta_color="off")
    c[2].metric("Tax overdue", money(tax["metrics"]["overdue"]),
                f"of {money(tax['metrics']['pending_total'])} pending", delta_color="off")
    c[3].metric("Due within 30 days", money(ap["metrics"]["upcoming_30d"] + tax["metrics"]["upcoming_30d"]),
                "AP + tax", delta_color="off")
    for n in ("Accounts Receivable", "Accounts Payable", "Tax"):
        signoff(n)

    st.markdown("#### 📒 Accounting & Reporting - the close & the financial statements")
    recs = close["reconciliations"]
    if recs["all_reconciled"]:
        st.markdown("<div class='opinion'>✅ Close is clean - AR & AP subledgers tie to the GL, and "
                    "retained earnings roll forward by net income (the statements articulate).</div>",
                    unsafe_allow_html=True)
    inc, bs, cf = rep["income_statement"], rep["balance_sheet"], rep["cash_flow"]
    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown("**Income statement**")
        stmt_table([("Revenue", money(inc["revenue"])), ("Cost of revenue", money(-inc["cogs"])),
                    (f"Gross profit ({inc['gross_margin_pct']:.0f}%)", money(inc["gross"])),
                    ("Sales & marketing", money(-inc["sm"])), ("R&D", money(-inc["rd"])),
                    ("G&A", money(-inc["ga"])), (f"Net income ({inc['net_margin_pct']:.0f}%)", money(inc["net_income"]))])
    with s2:
        st.markdown("**Balance sheet**")
        stmt_table([("Cash", money(bs["assets"]["cash"])),
                    ("Accounts receivable", money(bs["assets"]["accounts_receivable"])),
                    ("Fixed assets", money(bs["assets"]["fixed_assets"])),
                    ("Total assets", money(bs["total_assets"])), ("Liabilities", money(bs["total_liabilities"])),
                    ("Equity", money(bs["total_equity"])), ("Check (A−L−E)", money(bs["balance_check"]))])
    with s3:
        st.markdown("**Cash flow (indirect)**")
        stmt_table([("Net income", money(cf["net_income"])), ("− Increase in AR", money(-cf["d_ar"])),
                    ("+ Increase in AP", money(cf["d_ap"])), ("+ Increase in deferred", money(cf["d_deferred"])),
                    ("Cash from operations", money(cf["cfo"])), ("Beginning cash", money(cf["cash_begin"])),
                    ("Ending cash", money(cf["cash_end"]))])
    st.caption(f"The three statements articulate; the cash-flow statement foots to the actual change "
               f"in cash ({money(cf['net_change'])} = {money(cf['actual_change'])}).")
    for n in ("Accounting & Close", "Financial Reporting"):
        signoff(n)

    st.markdown("#### 📈 FP&A - forecast & variances")
    f = fpa["forecast"]
    c = st.columns(4)
    c[0].metric("Next-month revenue (fcst)", money(f["revenue"]))
    c[1].metric("Next-month op income (fcst)", money(f["operating_income"]))
    oi = next(r for r in fpa["budget_variance"]["rows"] if r["label"] == "Operating income")
    c[2].metric("Op income vs budget", money(oi["var"]), f"{oi['var_pct']:.1f}% vs plan", delta_color="off")
    c[3].metric("Material lines vs plan", str(len(fpa["budget_variance"]["material"])))
    signoff("FP&A")

    st.markdown("#### 🎯 Strategic Finance - growth quality & capital efficiency")
    m = strat["metrics"]
    c = st.columns(4)
    c[0].metric("ARR run-rate", money(m["run_rate"]))
    c[1].metric("Rule of 40", f"{m['rule_of_40']:.0f}", "≥ 40 is healthy", delta_color="off")
    c[2].metric("Burn multiple", f"{m['burn_multiple']:.1f}x", "≤ 2 is efficient", delta_color="off")
    c[3].metric("Magic number", f"{m['magic_number']:.2f}", "> 0.75 is good", delta_color="off")
    signoff("Strategic Finance")

    st.markdown("#### 🛡️ Internal Controls - assurance")
    summ = ctrls["summary"]
    c = st.columns(4)
    c[0].metric("Controls passed", f"{summ['n_pass']} / {summ['n_pass']+summ['n_fail']+summ['n_exception']}")
    c[1].metric("Integrity failures", str(summ["n_fail"]))
    c[2].metric("Books balanced", "Yes" if summ["books_balanced"] else "No")
    c[3].metric("Authorization review", str(summ["approval_exceptions"]),
                f"payments ≥ $25k ({money(summ['approval_exceptions_total'])})", delta_color="off")
    signoff("Internal Controls")

    st.markdown("#### 🔎 Audit - independent assurance (third line)")
    st.markdown(f"<div class='opinion'>Audit opinion: <b>{aud['opinion'].upper()}</b> - "
                f"{aud['n_procedures']} procedures re-performed, {aud['n_exceptions']} exception(s).</div>",
                unsafe_allow_html=True)
    signoff("Audit")

    om = A.get("Operating Model", {})
    if om.get("stages"):
        st.divider()
        st.markdown("### 2 · The operating model - stages, controls & sign-offs")
        st.markdown("<span class='small'>The close runs as explicit <b>stages</b>. Each = the agent "
                    "does the work (maker), a <b>deterministic control in code</b> must hold (a hard "
                    "gate), and the <b>domain expert signs off</b> (checker). A stage that cannot pass "
                    "blocks the whole close - by design.</span>", unsafe_allow_html=True)
        stage_rows = []
        for sg in om["stages"]:
            reviewers = ", ".join(A.get(fn, {}).get("review", {}).get("reviewer", "-") for fn in sg["functions"])
            ctl = sg["control"]
            stage_rows.append({
                "Stage": f"{sg['id']} · {sg['name']}",
                "Deterministic control": "- (no code gate)" if ctl == "no code-level control" else ctl,
                "Signed off by": reviewers,
                "Status": "✓ Passed" if sg["status"] == "passed" else f"✗ Blocked ({sg.get('reason','')})",
            })
        st.table(stage_rows)
        if om.get("all_passed"):
            st.success(f"✅ All {len(om['stages'])} stages passed their control and domain-expert "
                       "sign-off. The close can proceed to the CFO.")

    st.divider()
    st.markdown("### 3 · First line - each function signed off by its domain expert")
    FIRST_LINE = ["Controller", "Accounting & Close", "Financial Reporting", "Treasury",
                  "Accounts Receivable", "Accounts Payable", "Tax", "FP&A", "Strategic Finance",
                  "Internal Controls", "Audit"]
    fl_rows, n_auto = [], 0
    for fn in FIRST_LINE:
        r = A.get(fn, {}).get("review")
        if r:
            is_auto = r.get("mode") == "auto"
            n_auto += is_auto
            fl_rows.append({"Function": fn, "Signed off by": r["reviewer"],
                            "Mode": "Auto (replay)" if is_auto else "Human",
                            "Status": "✓ Approved" if r["decision"] == "approved" else "✗ Rejected"})
    st.table(fl_rows)
    n_ok = sum(1 for r in fl_rows if r["Status"].startswith("✓"))
    st.info(f"First line: {n_ok}/{len(fl_rows)} functions cleared their domain-expert checker, and the "
            "cross-checks passed - the agents agree on the shared numbers.")
    if n_auto:
        honest(f"{n_auto}/{len(fl_rows)} sign-offs in this public replay were auto-approved (no reviewer "
               "at the console). The maker-checker workflow, audit trail and block-on-reject are real "
               "and run interactively; what is simulated is the human keystroke, not the control.")

    escalations = []
    for name in TOP_LEVEL:
        escalations += A.get(name, {}).get("escalations", [])
    order = {"CRITICAL": 0, "HIGH": 1}
    escalations = sorted(escalations, key=lambda e: order.get(e[0], 9))
    st.markdown(f"**{len(escalations)} risk flags raised** (each owned by one agent, no double-counting):")
    for sev, msg in escalations:
        st.markdown(f"{sev_badge(sev)}&nbsp; {clean(msg)}", unsafe_allow_html=True)

    st.divider()
    st.markdown("### 4 · 🧑‍⚖️ CFO final sign-off - your call")
    if not st.session_state.close_approved:
        st.warning("First line is complete - all functions are signed off by their domain experts. "
                   "The CFO now gives the **final consolidated sign-off** on the board pack and the "
                   "material items. **You are the CFO.**")
        if st.button("✅ Final sign-off as CFO → release the board pack", type="primary", key="cfo_signoff"):
            st.session_state.close_approved = True
            st.session_state["_scroll_boardpack"] = True
            st.rerun()
    else:
        st.markdown("<div id='boardpack-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 5 · 📋 Board pack")
        st.markdown(f"<div class='boardpack'>{clean(cfo['board_pack'])}</div>", unsafe_allow_html=True)
        st.markdown("#### Recommended actions")
        st.markdown(clean(cfo["actions"]))
        if st.session_state.pop("_scroll_boardpack", False):
            scroll_to("boardpack-anchor")
        st.caption("Generated by the CFO agent from the eight agents' inputs - every figure traces "
                   "back to code-computed numbers.")

    st.divider()
    st.markdown("### 🎛️  Play with the model")
    tab1, tab2 = st.tabs(["Materiality threshold", "Growth scenarios"])
    with tab1:
        st.markdown("How big a budget variance is worth flagging? Move the threshold and watch which "
                    "lines the system escalates (it also requires a \\$20k floor).")
        pct = st.slider("Materiality threshold (% of plan)", 1.0, 15.0, 5.0, 0.5)
        rows = A["FP&A"]["budget_variance"]["rows"]
        flagged = [r for r in rows if abs(r["var_pct"]) >= pct and abs(r["var"]) >= 20000]
        st.markdown(f"**{len(flagged)} line(s) flagged at {pct:.1f}%:**")
        if flagged:
            st.table([{"Line": r["label"], "Variance $": f"{r['var']:+,.0f}", "Variance %": f"{r['var_pct']:+.1f}%",
                       "Fav/Unfav": "Unfavorable" if r["flag"] == "U" else "Favorable"} for r in flagged])
        else:
            st.info("Nothing material at this threshold - the month was in line with plan.")
    with tab2:
        st.markdown("Growth helps the headline, but does it fix profitability? Margin is held constant "
                    "on purpose - to show growth alone doesn't reach breakeven.")
        scn = {s["name"]: s for s in A["Strategic Finance"]["metrics"]["scenarios"]}
        pick = st.radio("Scenario", list(scn.keys()), index=1, horizontal=True)
        s = scn[pick]
        c = st.columns(3)
        c[0].metric("Monthly growth", f"{s['mom_growth']*100:.1f}%")
        c[1].metric("ARR run-rate in 12 months", money(s["run_rate_12m"]))
        c[2].metric("Rule of 40", f"{s['rule_of_40']:.0f}", "healthy" if s["rule_of_40"] >= 40 else "below 40",
                    delta_color="off")

    with st.expander("🔍 Audit trail - every step is logged (governance)"):
        for e in CLOSE["audit"]:
            st.markdown(f"<span class='tiny'><code>{e['ts']}</code> · <b>{e['agent']}</b> · "
                        f"{e['status']} - {e['detail']}</span>", unsafe_allow_html=True)


# ==========================================================================
# STATION 4 - EVALS
# ==========================================================================

def render_evals():
    section_title("4", "Evals - does it actually hold?",
                  "Four independent, fully offline scoreboards. This is the trust layer: the AI's "
                  "numbers are right, and they don't quietly change over time.")

    c = st.columns(4)
    c[0].metric("Numbers accuracy", f"{EVALS['numbers']['passed']}/{EVALS['numbers']['total']}",
                "the month-end close", delta_color="off")
    c[1].metric("Self-improvement safety", f"{EVALS['safety']['passed']}/{EVALS['safety']['total']}",
                "guardrail tests", delta_color="off")
    c[2].metric("O2C control tower", f"{EVALS['o2c_suite']['passed']}/{EVALS['o2c_suite']['total']}",
                f"{EVALS['o2c_blind']['caught']}/{EVALS['o2c_blind']['planted']} planted errors found",
                delta_color="off")
    c[3].metric("Real audited SEC data", f"{EVALS['dlocal']['passed']}/{EVALS['dlocal']['total']}",
                "dLocal (NASDAQ: DLO)", delta_color="off")

    st.divider()
    st.markdown("#### 1 · Numbers accuracy - every close figure checked against a known-correct answer")
    st.markdown(f"<span class='small'>{EVALS['numbers']['total']} automated checks against a fixed "
                "answer key: P&L, cash, AR/AP/tax, 13-week forecast, internal controls, the full "
                "record-to-report close, strategic metrics. No AI involved in the check.</span>",
                unsafe_allow_html=True)
    with st.expander(f"See all {EVALS['numbers']['total']} checks"):
        for ck in EVALS["numbers"]["checks"]:
            st.markdown(f"{'✅' if ck['ok'] else '❌'} <span class='tiny'>{clean(ck['label'])}</span>",
                        unsafe_allow_html=True)

    st.markdown("#### 2 · Self-improvement safety - the AI cannot get past its limits")
    st.markdown(f"<span class='small'>{EVALS['safety']['total']} proofs that the self-tuning stays inside "
                "its guardrails: a change outside the allowed limits is rejected, each change is capped in "
                "size, the formulas can't be touched, a change that would hurt accuracy is rejected even if "
                "a human approves it, every change can be undone exactly, and everything is logged.</span>",
                unsafe_allow_html=True)
    with st.expander(f"See all {EVALS['safety']['total']} safety proofs"):
        for t in EVALS["safety"]["tests"]:
            st.markdown(f"✅ <span class='tiny'>{clean(t['desc'])}</span>", unsafe_allow_html=True)

    st.markdown("#### 3 · O2C control tower - and a blind test")
    st.markdown(f"<span class='small'>{EVALS['o2c_suite']['total']} tests on the Order-to-Cash tower. "
                f"The centerpiece is a blind test with 10 deliberately planted errors: the controls catch "
                f"<b>{EVALS['o2c_blind']['caught']}/{EVALS['o2c_blind']['planted']}</b> of them - by the "
                f"exact control and the exact record - and the pipeline correctly refuses to release a "
                f"report.</span>", unsafe_allow_html=True)
    with st.expander("See the 10 controls that caught the planted errors"):
        for cid in EVALS["o2c_blind"]["hard_failure_ids"]:
            st.markdown(f"⛔ <span class='tiny'><code>{cid}</code></span>", unsafe_allow_html=True)

    st.markdown("#### 4 · Real-data audit - reproduce dLocal's audited SEC financials")
    h = EVALS["dlocal"]["headline"]
    c = st.columns(4)
    c[0].metric("Net income FY2025", money_m(h["net_income_fy2025"] * 1000) if h["net_income_fy2025"] else "-")
    c[1].metric("Adjusted EBITDA FY2025", money_m(h["adjusted_ebitda_fy2025"] * 1000) if h["adjusted_ebitda_fy2025"] else "-")
    c[2].metric("Revenue growth YoY", f"{h['revenue_growth_pct']:.1f}%" if h["revenue_growth_pct"] else "-")
    c[3].metric("Total assets FY2025", money_m(h["total_assets_fy2025"] * 1000) if h["total_assets_fy2025"] else "-")
    st.markdown(f"<span class='small'>The engine recomputes {EVALS['dlocal']['total']} headline figures "
                "from dLocal's public inputs and diffs them against the filed SEC answer key "
                "(tolerances: USD thousands ±1, percentages ±0.1).</span>", unsafe_allow_html=True)
    with st.expander(f"See all {EVALS['dlocal']['total']} figures vs the SEC answer key"):
        st.table([{"Figure": r["key"], "Model": f"{r['model']:,.1f}" if r["model"] is not None else "-",
                   "SEC filing": f"{r['expected']:,.1f}" if r["expected"] is not None else "-",
                   "Δ": "" if r["delta"] is None else f"{r['delta']:g}",
                   "Unit": str(r["unit"]), "": "✅" if r["status"] == "PASS" else "❌"}
                  for r in EVALS["dlocal"]["rows"]])
    honest("dLocal is a dual-model AI-assisted external audit in the engineering sense - reproducing "
           "filed figures from public inputs - not a formal or statutory audit. Two further evals "
           "(contract extraction, grounded refusals) use a model and need an API key, so they are not "
           "shown in this no-key replay.")


# ==========================================================================
# STATION 5 - SELF-IMPROVEMENT
# ==========================================================================

def render_selfimprove():
    section_title("5", "Bounded self-improvement",
                  "The AI may propose a better <b>value</b> for exactly four finance parameters - and "
                  "nothing else. It can never touch a formula, widen its own limits, or adopt a change "
                  "on its own. This is the strongest proof the system stays under control.")

    st.markdown("#### The only values that can ever change")
    st.table([{"Parameter": p["name"], "Current": p["value"], "Bounds": f"[{p['min']}, {p['max']}]",
               "Max step": p["max_step"], "Human owner": p["owner"]} for p in SI["params"]])
    st.caption("Each parameter has hard bounds, a per-change step cap, a cooldown, and a named human "
               "owner. The AI cannot change this table.")

    st.divider()
    st.markdown("#### Walk the loop")
    tabs = st.tabs(["✅ Accepted", "⛔ Out of bounds", "⛔ Regresses evals", "↩️ Rollback", "📜 Audit trail"])

    with tabs[0]:
        a = SI["accept"]
        st.markdown(f"**Proposal:** raise `{a['param']}` from **{a['old']}** to **{a['proposed']}**.")
        ev = a["evidence"]
        st.markdown(f"<span class='small'>The number is computed deterministically from "
                    f"{ev.get('n_periods','?')} periods of real outcomes "
                    f"(realized rate {ev.get('realized_rate','?')}). The model only writes the "
                    f"rationale.</span>", unsafe_allow_html=True)
        c = st.columns(3)
        c[0].metric("Bounds check", "Pass" if not ev.get("clamped") else "Clamped", delta_color="off")
        c[1].metric("Eval no-regression", f"{a['eval']['candidate'][0]}/{a['eval']['candidate'][1]}",
                    f"baseline {a['eval']['baseline'][0]}/{a['eval']['baseline'][1]}", delta_color="off")
        c[2].metric("Backtest error", f"{a['backtest']['metric_new']:.0f}",
                    f"was {a['backtest']['metric_old']:.0f}", delta_color="off")
        if a["ok"]:
            st.success(f"All four gates pass and {a['approver']} (the owner) approves → champion promoted "
                       f"to v{a['new_version']}.")

    with tabs[1]:
        o = SI["reject_out_of_bounds"]
        st.markdown(f"**Proposal:** push `{o['param']}` to **{o['proposed']}** "
                    f"(bounds are {o['bounds']}).")
        st.markdown(f"{o['approver']} tries to approve it.")
        st.markdown(f"<div class='blocked'>⛔ Rejected even with human approval. "
                    f"Reasons: {o['reasons']}</div>", unsafe_allow_html=True)
        st.caption(f"Champion unchanged at {o['champion_after']}.")

    with tabs[2]:
        r = SI["reject_eval_regression"]
        st.markdown(f"**Proposal:** change `{r['param']}` from **{r['old']}** to **{r['proposed']}** "
                    "- in bounds and within step.")
        st.markdown(f"{r['approver']} approves it. But the deterministic evals would drop.")
        st.markdown(f"<div class='blocked'>⛔ Rejected. Reasons: {r['reasons']}</div>",
                    unsafe_allow_html=True)
        st.caption("This is the key proof: human approval is necessary but NOT sufficient. A change that "
                   "would regress the numbers is refused no matter who signs it.")

    with tabs[3]:
        rb = SI["rollback"]
        st.markdown(f"**Rollback** `{rb['param']}` to a prior champion in one step.")
        c = st.columns(2)
        c[0].metric("Before", rb["before"])
        c[1].metric("After", rb["after"])
        if rb["result"].get("ok"):
            st.success(f"Restored to {rb['after']} as a new version (history never rewritten).")

    with tabs[4]:
        st.markdown("Every action is added to a permanent, append-only audit trail.")
        for e in SI["audit_trail"]:
            st.markdown(f"<span class='tiny'><code>{e['ts']}</code> · <b>{e['action']}</b> - "
                        f"{clean(e['detail'])}</span>", unsafe_allow_html=True)

    honest("The safety limits are enforced on every change the system makes. Directly hand-editing "
           "the saved files would be tampering from outside the system - the same as editing the "
           "source code - which is out of scope here. The audit trail can only be added to, never "
           "rewritten.")


# --------------------------------------------------------------------------
# Router.
# --------------------------------------------------------------------------

if choice == NAV[0]:
    render_overview()
elif choice == NAV[1]:
    render_erp()
elif choice == NAV[2]:
    render_o2c()
elif choice == NAV[3]:
    render_close()
elif choice == NAV[4]:
    render_evals()
elif choice == NAV[5]:
    render_selfimprove()

st.divider()
st.markdown(
    "<span class='tiny'>Built by <b>Ignacio Viola</b> · 17 years in senior finance, now building AI "
    "systems for finance operations · Synthetic data (dLocal station uses real public SEC filings) · "
    "Every number is code-computed and regression-tested · "
    "<a href='https://github.com/ignacioviola1984-spec/ai-finance-engineering'>Source on GitHub</a></span>",
    unsafe_allow_html=True)
