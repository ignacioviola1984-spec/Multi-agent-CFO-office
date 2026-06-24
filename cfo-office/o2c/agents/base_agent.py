"""
base_agent.py - Base class and shared context for the O2C agents.

These agents are DELIBERATELY deterministic (no LLM, no API key). Each agent is
a maker: it diagnoses exceptions, prioritizes work, explains the situation in a
templated narrative built from computed numbers, routes approvals, and proposes
actions. It never invents a number - every figure comes from o2c_core /
o2c_controls. A human checker (the orchestrator's maker-checker step) signs off.

This mirrors cfo-office/shared_state.py (CFOContext): one shared book, an audit
trail of who-wrote-what-when, structured findings other agents and the
orchestrator consume.
"""

import os
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))     # .../o2c/agents
O2C = os.path.dirname(HERE)                            # .../o2c
for _p in (O2C, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_policy as P  # noqa: E402


class O2CContext:
    """Shared state for one control-tower run: precomputed calculations, agent
    findings, and an append-only audit trail."""

    def __init__(self, dfs, period, calc):
        self.dfs = dfs
        self.period = period
        self.calc = calc                  # shared deterministic core/control outputs
        self.findings = {}                # agent name -> findings dict
        self.reviews = {}                 # agent name -> maker/checker review record
        self.audit = []                   # list of audit events

    def put(self, agent, payload):
        self.findings.setdefault(agent, {}).update(payload)

    def get(self, agent, key=None, default=None):
        a = self.findings.get(agent, {})
        return a if key is None else a.get(key, default)

    def record(self, actor, status, detail):
        evt = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "actor": actor, "status": status, "detail": detail}
        self.audit.append(evt)
        return evt

    def escalations(self):
        """All agent escalations, ordered by severity."""
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        out = []
        for agent, f in self.findings.items():
            for e in f.get("escalations", []):
                out.append({"agent": agent, "severity": e[0], "message": e[1]})
        return sorted(out, key=lambda e: order.get(e["severity"], 9))


def money(x):
    return f"USD {x:,.0f}"


def df_records(df, cols=None, n=25):
    """Top-N rows of a DataFrame as plain dicts (JSON-safe) for findings output."""
    if df is None or len(df) == 0:
        return []
    d = df[cols] if cols else df
    return d.head(n).astype(object).where(d.head(n).notna(), None).to_dict("records")


class O2CAgent:
    """Base maker agent. Subclasses set the metadata and implement analyze()."""

    name = "O2CAgent"
    purpose = ""
    maker_owner = ""
    checker_owner = ""
    input_tables = []
    output_artifacts = []
    deterministic_checks_used = []

    def analyze(self, ctx):
        """Return a findings dict. Must be deterministic. Subclass implements."""
        raise NotImplementedError

    def run(self, ctx):
        ctx.record(self.name, "start", self.purpose)
        findings = self.analyze(ctx)
        findings.setdefault("escalations", [])
        findings.setdefault("recommended_actions", [])
        findings["agent"] = self.name
        findings["maker_owner"] = self.maker_owner
        findings["checker_owner"] = self.checker_owner
        findings["input_tables"] = self.input_tables
        findings["output_artifacts"] = self.output_artifacts
        findings["deterministic_checks_used"] = self.deterministic_checks_used
        ctx.put(self.name, findings)
        n_esc = len(findings.get("escalations", []))
        ctx.record(self.name, "ok", f"{findings.get('headline', 'analysis complete')} "
                                    f"({n_esc} escalation(s))")
        return findings

    def escalate(self, severity, message):
        return [severity, message]
