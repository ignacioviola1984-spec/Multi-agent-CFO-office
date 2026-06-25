"""
cartera_core.py - Deterministic detection + metrics. The single source of numbers.

Mirrors orchestration/finance_core.py for the insurance domain: every count,
date, amount, bucket, prima, comision and confidence score is computed here, in
pure Python. The agents narrate; they never compute. Anything the LLM is allowed
to say must trace back to a number produced by this module.

The detectors hang off a `Cartera` object (the loaded portfolio) so the same code
serves the committed demo file and an uploaded one. `load_cartera(path)` is the
entrypoint; the detectors are its methods.
"""

import os
import sys
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import schema
from schema import AS_OF, GRACE_DAYS, CROSS_SELL_RULES, parse


# --------------------------------------------------------------------------
# Confidence score (deterministic): a blend of data completeness and rule
# strength. NEVER produced by the LLM. Returned as a 0..1 float; agents render
# it as a percentage.
# --------------------------------------------------------------------------
W_DATA = 0.40   # weight on how complete the client's data is
W_RULE = 0.60   # weight on how strong the detection rule is on its own


def data_completeness(values) -> float:
    """Fraction of the given fields that are present (non-empty). 0..1."""
    vals = list(values)
    if not vals:
        return 0.0
    present = sum(1 for v in vals if not schema._is_blank(v))
    return present / len(vals)


def confidence(completeness: float, rule_strength: float) -> float:
    """Deterministic confidence in [0, 1]. Higher with more complete data and a
    stronger rule. Pure function of its inputs - no model, no randomness."""
    c = max(0.0, min(1.0, completeness))
    r = max(0.0, min(1.0, rule_strength))
    return round(W_DATA * c + W_RULE * r, 4)


# --------------------------------------------------------------------------
# Per-policy deterministic helpers.
# --------------------------------------------------------------------------

def dias_para_vencer(policy, as_of: date = AS_OF) -> int:
    """Days until the policy's renewal (negative if already past)."""
    return (parse(policy.fecha_vencimiento) - as_of).days


def is_en_mora(policy) -> bool:
    """In arrears in the collections sense: active coverage with the premium
    unpaid. Lapsed (vencida/cancelada) policies are not 'en mora' here."""
    return policy.estado_poliza == "activa" and policy.estado_pago == "en_mora"


def dias_mora(policy, as_of: date = AS_OF) -> int:
    """Days past due for an en-mora policy = days since last payment beyond one
    monthly grace cycle. Unknown last payment is treated as worst case (>90)."""
    if schema._is_blank(policy.fecha_ultimo_pago):
        return 999
    raw = (as_of - parse(policy.fecha_ultimo_pago)).days - GRACE_DAYS
    return max(0, raw)


def mora_bucket(dias: int) -> str:
    if dias <= 30:
        return "0-30"
    if dias <= 60:
        return "31-60"
    if dias <= 90:
        return "61-90"
    return "90+"


def _contact(policy) -> dict:
    return {"email": policy.email, "telefono": policy.telefono}


# --------------------------------------------------------------------------
# The loaded portfolio + the five detectors.
# --------------------------------------------------------------------------

class Cartera:
    def __init__(self, policies):
        self.policies = list(policies)

    # -- grouping ------------------------------------------------------
    def by_client(self):
        """cliente_id -> list[Policy], in stable insertion order."""
        groups = defaultdict(list)
        for p in self.policies:
            groups[p.cliente_id].append(p)
        return groups

    def _client_ramos(self, policies, only_active=False):
        return {p.ramo for p in policies if (p.estado_poliza == "activa" or not only_active)}

    # -- detector 1: renovaciones --------------------------------------
    def policies_expiring(self, days: int = 30, as_of: date = AS_OF):
        """Active policies whose renewal falls within `days` (inclusive, future).
        Sorted soonest-first."""
        out = []
        for p in self.policies:
            if p.estado_poliza != "activa":
                continue
            d = dias_para_vencer(p, as_of)
            if 0 <= d <= days:
                out.append({
                    "cliente_id": p.cliente_id, "nombre": p.nombre,
                    "email": p.email, "telefono": p.telefono,
                    "numero_poliza": p.numero_poliza, "ramo": p.ramo,
                    "aseguradora": p.aseguradora,
                    "fecha_vencimiento": p.fecha_vencimiento,
                    "dias_para_vencer": d, "prima_mensual": p.prima_mensual,
                    "comision_pct": p.comision_pct,
                })
        out.sort(key=lambda r: (r["dias_para_vencer"], r["cliente_id"]))
        return out

    # -- detector 2: reactivacion --------------------------------------
    def inactive_clients(self, months: int = 6, as_of: date = AS_OF):
        """Clients with NO active policy whose most recent policy lapsed more than
        `months` ago (~30-day months). Reactivation candidates. Sorted by client."""
        threshold_days = months * 30
        out = []
        for cid, pols in self.by_client().items():
            if any(p.estado_poliza == "activa" for p in pols):
                continue  # still active somewhere -> not inactive
            last_venc = max(parse(p.fecha_vencimiento) for p in pols)
            dias_inactivo = (as_of - last_venc).days
            if dias_inactivo <= threshold_days:
                continue  # lapsed recently -> below the reactivation horizon
            ref = max(pols, key=lambda p: parse(p.fecha_vencimiento))
            out.append({
                "cliente_id": cid, "nombre": ref.nombre,
                "email": ref.email, "telefono": ref.telefono,
                "n_polizas": len(pols),
                "ramos": sorted({p.ramo for p in pols}),
                "ultima_aseguradora": ref.aseguradora,
                "ultima_vencimiento": ref.fecha_vencimiento,
                "dias_inactivo": dias_inactivo,
                "prima_ultima": ref.prima_mensual,
            })
        out.sort(key=lambda r: r["cliente_id"])
        return out

    # -- detector 3: cross-sell ----------------------------------------
    def cross_sell_candidates(self):
        """Clients holding an ACTIVE policy in a `has` ramo but with NO policy at
        all in the complementary `missing` ramo. One candidate per (client,
        missing ramo), keeping the strongest matching rule. Sorted by client."""
        best = {}
        for cid, pols in self.by_client().items():
            active_ramos = {p.ramo for p in pols if p.estado_poliza == "activa"}
            held_ramos = {p.ramo for p in pols}
            for rule in CROSS_SELL_RULES:
                if rule["has"] in active_ramos and rule["missing"] not in held_ramos:
                    base = next(p for p in pols
                                if p.ramo == rule["has"] and p.estado_poliza == "activa")
                    key = (cid, rule["missing"])
                    cand = {
                        "cliente_id": cid, "nombre": base.nombre,
                        "email": base.email, "telefono": base.telefono,
                        "has_ramo": rule["has"], "missing_ramo": rule["missing"],
                        "strength": rule["strength"], "rationale": rule["rationale"],
                        "base_poliza": base.numero_poliza,
                        "base_aseguradora": base.aseguradora,
                        "prima_ref": base.prima_mensual,
                    }
                    if key not in best or cand["strength"] > best[key]["strength"]:
                        best[key] = cand
        out = list(best.values())
        out.sort(key=lambda r: (r["cliente_id"], r["missing_ramo"]))
        return out

    # -- detector 4: cobranza (mora buckets) ---------------------------
    def mora_buckets(self, as_of: date = AS_OF):
        """En-mora active policies bucketed by days past due. The per-bucket counts
        and primas reconcile exactly to the totals (every item in one bucket)."""
        buckets = {b: {"count": 0, "prima_mensual": 0.0, "items": []}
                   for b in schema.MORA_BUCKETS}
        items = []
        for p in self.policies:
            if not is_en_mora(p):
                continue
            dias = dias_mora(p, as_of)
            b = mora_bucket(dias)
            item = {
                "cliente_id": p.cliente_id, "nombre": p.nombre,
                "email": p.email, "telefono": p.telefono,
                "numero_poliza": p.numero_poliza, "ramo": p.ramo,
                "aseguradora": p.aseguradora, "dias_mora": dias, "bucket": b,
                "prima_mensual": p.prima_mensual,
            }
            buckets[b]["count"] += 1
            buckets[b]["prima_mensual"] += p.prima_mensual
            buckets[b]["items"].append(item)
            items.append(item)
        items.sort(key=lambda r: (-r["dias_mora"], r["cliente_id"]))
        return {
            "buckets": buckets,
            "total_count": len(items),
            "total_prima_mensual": round(sum(i["prima_mensual"] for i in items), 2),
            "items": items,
        }

    # -- detector 5: portfolio metrics ---------------------------------
    def portfolio_metrics(self, expiring_days: int = 30, as_of: date = AS_OF):
        """Deterministic portfolio metrics for the dashboard. No per-client action."""
        clients = self.by_client()
        activas = [p for p in self.policies if p.estado_poliza == "activa"]
        vencidas = [p for p in self.policies if p.estado_poliza == "vencida"]
        canceladas = [p for p in self.policies if p.estado_poliza == "cancelada"]

        prima_activa = round(sum(p.prima_mensual for p in activas), 2)
        comision_mensual = round(sum(p.prima_mensual * p.comision_pct for p in activas), 2)

        mix_aseg = defaultdict(lambda: {"count": 0, "prima_mensual": 0.0})
        mix_ramo = defaultdict(lambda: {"count": 0, "prima_mensual": 0.0})
        for p in activas:
            mix_aseg[p.aseguradora]["count"] += 1
            mix_aseg[p.aseguradora]["prima_mensual"] += p.prima_mensual
            mix_ramo[p.ramo]["count"] += 1
            mix_ramo[p.ramo]["prima_mensual"] += p.prima_mensual
        for d in (mix_aseg, mix_ramo):
            for v in d.values():
                v["prima_mensual"] = round(v["prima_mensual"], 2)

        en_mora = [p for p in activas if p.estado_pago == "en_mora"]
        prima_mora = sum(p.prima_mensual for p in en_mora)

        clientes_inactivos = sum(
            1 for pols in clients.values()
            if pols and not any(p.estado_poliza == "activa" for p in pols))
        clientes_activos = len(clients) - clientes_inactivos

        return {
            "total_polizas": len(self.policies),
            "total_clientes": len(clients),
            "polizas_activas": len(activas),
            "polizas_vencidas": len(vencidas),
            "polizas_canceladas": len(canceladas),
            "prima_mensual_total": prima_activa,
            "comision_mensual_total": comision_mensual,
            "mix_por_aseguradora": {k: dict(v) for k, v in sorted(mix_aseg.items())},
            "mix_por_ramo": {k: dict(v) for k, v in sorted(mix_ramo.items())},
            "polizas_en_mora": len(en_mora),
            "pct_en_mora_polizas": round(len(en_mora) / len(activas) * 100, 2) if activas else 0.0,
            "pct_en_mora_prima": round(prima_mora / prima_activa * 100, 2) if prima_activa else 0.0,
            "vencimientos_proximos": len(self.policies_expiring(expiring_days, as_of)),
            "vencimientos_dias": expiring_days,
            "clientes_activos": clientes_activos,
            "clientes_inactivos": clientes_inactivos,
            # Retention proxy: share of clients still holding at least one active
            # policy. Documented as a proxy (no per-renewal lapse history here).
            "retencion_pct": round(clientes_activos / len(clients) * 100, 2) if clients else 0.0,
        }


def load_cartera(path=None) -> Cartera:
    """Load a cartera Excel into a Cartera. Accepts a path or a file-like object
    (e.g. a Streamlit upload). Defaults to the committed demo file. Falls back to
    the first sheet if there is no sheet named 'cartera'."""
    import pandas as pd
    import paths
    src = path if path is not None else paths.DEMO_CARTERA
    try:
        df = pd.read_excel(src, sheet_name="cartera", engine="openpyxl")
    except (ValueError, KeyError):
        df = pd.read_excel(src, sheet_name=0, engine="openpyxl")
    policies = [schema.Policy.from_row(rec) for rec in df.to_dict(orient="records")]
    return Cartera(policies)
