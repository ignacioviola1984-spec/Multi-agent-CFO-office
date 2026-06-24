"""cash_application_agent.py - Tie bank receipts to payments, applications, and AR."""

import base_agent
from base_agent import O2CAgent, money, df_records
import o2c_core as core
import o2c_data_loader as loader
import o2c_policy as P


class CashApplicationAgent(O2CAgent):
    name = "CashApplicationAgent"
    purpose = "Tie bank cash to applications and AR; surface unapplied cash and FX."
    maker_owner = P.MAKER_OWNERS["CashApplicationAgent"]
    checker_owner = P.CHECKER_OWNERS["CashApplicationAgent"]
    input_tables = ["payments", "bank_receipts", "cash_application", "invoices", "credit_memos"]
    output_artifacts = ["unapplied_cash", "unmatched_receipts", "short_payments",
                        "overpayments", "fx_differences", "treasury_tie_out"]
    deterministic_checks_used = ["I_CASH_RECEIPT_TO_BANK", "J_CASH_APPLICATION_COMPLETENESS",
                                 "S5_HIGH_UNAPPLIED_CASH", "S10_FX_GAIN_LOSS"]

    def analyze(self, ctx):
        period = ctx.period
        capp = core.calculate_cash_application_status(ctx.dfs, period)
        unapp = core.calculate_unapplied_cash(ctx.dfs, period)

        rec = ctx.dfs["bank_receipts"]
        unmatched = rec[rec["matched_status"] != "matched"]

        # short / over payments vs net due (invoice total - approved credits)
        inv = ctx.dfs["invoices"][["invoice_id", "total_invoice_amount_usd"]]
        cm = ctx.dfs["credit_memos"]
        credit = (cm[cm["approval_status"] == "approved"].groupby("invoice_id")["credit_amount_usd"]
                  .sum().rename("credit_usd"))
        pay = ctx.dfs["payments"].copy()
        pay["payment_amount_usd"] = loader.to_usd(pay["payment_amount"], pay["currency"])
        pay = pay.merge(inv, left_on="remittance_reference", right_on="invoice_id", how="left")
        pay = pay.merge(credit, left_on="remittance_reference", right_index=True, how="left")
        pay["credit_usd"] = pay["credit_usd"].fillna(0.0)
        pay["net_due_usd"] = (pay["total_invoice_amount_usd"].fillna(0.0) - pay["credit_usd"]).round(2)
        matched = pay[pay["total_invoice_amount_usd"].notna()].copy()
        matched["delta"] = (matched["payment_amount_usd"] - matched["net_due_usd"]).round(2)
        short = matched[matched["delta"] < -1.0]
        over = matched[matched["delta"] > 1.0]

        fx = ctx.dfs["cash_application"]
        fx_abs = round(float(fx["fx_gain_loss"].abs().sum()), 2)

        applied = capp["applied_usd"]
        received = capp["received_usd"]
        tie_diff = round(received - applied - unapp["unmatched_receipt_usd"], 2)
        tie_ok = abs(tie_diff) <= max(1.0, received * 0.001)

        esc, actions = [], []
        if unapp["unapplied_count"]:
            esc.append(self.escalate("HIGH", f"{money(unapp['unapplied_cash_usd'])} unapplied cash "
                                      f"across {unapp['unapplied_count']} items"))
            actions.append("Cash Application: clear unapplied cash to recognize true collections.")
        if len(unmatched):
            esc.append(self.escalate("MEDIUM", f"{len(unmatched)} bank receipts not applied to AR"))
        if len(short):
            actions.append(f"Investigate {len(short)} short payments ({money(short['delta'].abs().sum())}).")

        headline = (f"app rate {capp['cash_application_rate_pct']}%, "
                    f"{money(unapp['unapplied_cash_usd'])} unapplied, tie {'OK' if tie_ok else 'BREAK'}")
        narrative = (
            f"Cash application for {period}: received {money(received)}, applied {money(applied)} "
            f"(rate {capp['cash_application_rate_pct']}%). Unapplied cash {money(unapp['unapplied_cash_usd'])} "
            f"across {unapp['unapplied_count']} items; {len(unmatched)} unmatched bank receipts; "
            f"{len(short)} short and {len(over)} over payments; FX gain/loss {money(fx_abs)}. "
            f"Treasury tie-out (received = applied + unapplied) is {'in balance' if tie_ok else 'out of balance'} "
            f"({money(tie_diff)}).")
        return {"headline": headline, "narrative": narrative,
                "cash_application_rate_pct": capp["cash_application_rate_pct"],
                "received_usd": received, "applied_usd": applied,
                "unapplied_cash_usd": unapp["unapplied_cash_usd"], "unapplied_count": unapp["unapplied_count"],
                "unmatched_receipt_count": int(len(unmatched)),
                "short_payment_count": int(len(short)), "overpayment_count": int(len(over)),
                "fx_gain_loss_abs_usd": fx_abs,
                "treasury_tie_out": {"received_usd": received, "applied_usd": applied,
                    "unmatched_usd": unapp["unmatched_receipt_usd"], "diff_usd": tie_diff,
                    "in_balance": bool(tie_ok)},
                "unapplied": df_records(unapp["unapplied"], ["cash_application_id", "payment_id",
                    "invoice_id", "payment_amount_usd", "unapplied_reason"]),
                "short_payments": df_records(short, ["payment_id", "remittance_reference",
                    "payment_amount_usd", "net_due_usd", "delta"]),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return CashApplicationAgent().run(ctx)
