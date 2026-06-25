"""
generate_synthetic_cartera.py - Build a realistic *synthetic* cartera.

Produces nexo/data/cartera_demo.xlsx: ~150 clients / ~220 policies of an
Argentine insurance broker, with a deliberate spread so every agent has work to
do - upcoming renewals, mora across all four buckets, inactive (lapsed) clients
to reactivate, and cross-sell gaps (Auto without Hogar, Comercio without ART...).

100% synthetic: names are drawn from generic pools, emails use example.com, and
no real client PII is ever required or written. Fully deterministic given a seed
(default 42) and anchored on schema.AS_OF, so the file is reproducible.

Run:  python nexo/data/generate_synthetic_cartera.py
"""

import os
import random
import sys
from datetime import timedelta

# Self-bootstrap: put nexo/ on the path so `import schema`/`paths` work whether
# this file is run directly or imported as data.generate_synthetic_cartera.
_NEXO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _NEXO not in sys.path:
    sys.path.insert(0, _NEXO)

import schema
import paths
from schema import AS_OF, Policy

FIRST_NAMES = [
    "Juan", "Maria", "Carlos", "Lucia", "Diego", "Sofia", "Martin", "Valentina",
    "Sebastian", "Camila", "Nicolas", "Florencia", "Matias", "Agustina", "Federico",
    "Julieta", "Pablo", "Carolina", "Gonzalo", "Micaela", "Lucas", "Paula", "Tomas",
    "Daniela", "Ramiro", "Brenda", "Ezequiel", "Romina", "Facundo", "Antonella",
    "Ignacio", "Belen", "Maximiliano", "Rocio", "Joaquin", "Milagros", "Emiliano",
    "Abril", "Santiago", "Catalina",
]
LAST_NAMES = [
    "Gomez", "Rodriguez", "Fernandez", "Lopez", "Martinez", "Garcia", "Perez",
    "Sanchez", "Romero", "Sosa", "Torres", "Alvarez", "Ruiz", "Diaz", "Acosta",
    "Benitez", "Medina", "Suarez", "Herrera", "Aguirre", "Gimenez", "Molina",
    "Silva", "Castro", "Rojas", "Ortiz", "Nunez", "Luna", "Cabrera", "Rios",
    "Ferreyra", "Godoy", "Vega", "Cardozo", "Maldonado", "Paez", "Villalba",
]

# Per-ramo realistic monthly premium (ARS), insured sum (ARS) and commission ranges.
PRIMA = {
    "Auto": (25_000, 85_000), "Hogar": (8_000, 26_000), "Vida": (5_000, 22_000),
    "Comercio": (30_000, 150_000), "ART": (20_000, 120_000),
    "Combinado": (15_000, 42_000), "Motovehiculo": (9_000, 30_000),
}
SUMA = {
    "Auto": (8_000_000, 45_000_000), "Hogar": (10_000_000, 60_000_000),
    "Vida": (5_000_000, 50_000_000), "Comercio": (20_000_000, 200_000_000),
    "ART": (15_000_000, 150_000_000), "Combinado": (12_000_000, 70_000_000),
    "Motovehiculo": (2_000_000, 9_000_000),
}
COMISION = {
    "Auto": (0.10, 0.15), "Hogar": (0.15, 0.22), "Vida": (0.10, 0.20),
    "Comercio": (0.12, 0.20), "ART": (0.06, 0.10), "Combinado": (0.15, 0.20),
    "Motovehiculo": (0.12, 0.18),
}

# Target mora distribution (active policies in arrears), one client each, so the
# buckets are populated and reconcile exactly against the total en mora.
MORA_PLAN = {"0-30": 14, "31-60": 12, "61-90": 8, "90+": 11}
# A representative days-past-due inside each bucket (deterministic, not random,
# so each planted policy lands squarely in its intended bucket).
MORA_DPD = {"0-30": 15, "31-60": 45, "61-90": 75, "90+": 120}


def _d(days_from_asof):
    """A date offset from AS_OF, as an ISO string (negative = past)."""
    return (AS_OF + timedelta(days=days_from_asof)).isoformat()


class _Gen:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.seq = 0
        self.cli = 0
        self.policies = []

    # --- identity ------------------------------------------------------
    def new_client(self):
        self.cli += 1
        cid = f"CLI-{self.cli:04d}"
        nombre = f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"
        handle = nombre.lower().replace(" ", ".")
        # Occasionally leave contact data missing so the confidence score (which
        # factors data completeness) has something real to vary on.
        email = f"{handle}.{self.cli}@example.com" if self.rng.random() > 0.07 else None
        telefono = (f"+54 9 11 {self.rng.randint(2000, 6999)} {self.rng.randint(1000, 9999)}"
                    if self.rng.random() > 0.07 else None)
        nacimiento = (_d(-self.rng.randint(20, 70) * 365) if self.rng.random() > 0.30 else None)
        return {"cliente_id": cid, "nombre": nombre, "email": email,
                "telefono": telefono, "fecha_nacimiento": nacimiento}

    # --- one policy ----------------------------------------------------
    def policy(self, client, ramo, *, estado_poliza, estado_pago,
               venc_days, ultimo_pago_days):
        self.seq += 1
        lo, hi = PRIMA[ramo]
        prima = round(self.rng.uniform(lo, hi), -2)
        slo, shi = SUMA[ramo]
        suma = round(self.rng.uniform(slo, shi), -3) if self.rng.random() > 0.25 else None
        clo, chi = COMISION[ramo]
        comision = round(self.rng.uniform(clo, chi), 3)
        venc = AS_OF + timedelta(days=venc_days)
        alta = venc - timedelta(days=365)  # annual policy
        p = Policy(
            cliente_id=client["cliente_id"], nombre=client["nombre"],
            email=client["email"], telefono=client["telefono"],
            fecha_nacimiento=client["fecha_nacimiento"],
            numero_poliza=f"{ramo[:3].upper()}-{self.seq:05d}",
            ramo=ramo, aseguradora=self.rng.choice(schema.ASEGURADORAS),
            fecha_alta=alta.isoformat(), fecha_vencimiento=venc.isoformat(),
            prima_mensual=prima, suma_asegurada=suma, comision_pct=comision,
            estado_pago=estado_pago, estado_poliza=estado_poliza,
            fecha_ultimo_pago=(None if ultimo_pago_days is None
                               else _d(-ultimo_pago_days)),
        )
        self.policies.append(p)
        return p

    # --- active al-dia policy with a renewal in `venc_days` days --------
    def active_aldia(self, client, ramo, venc_days):
        return self.policy(client, ramo, estado_poliza="activa", estado_pago="al_dia",
                           venc_days=venc_days, ultimo_pago_days=self.rng.randint(0, 28))


def build(seed=42):
    """Build the synthetic cartera. Returns a list[Policy]."""
    g = _Gen(seed)
    ramos_main = ["Auto", "Hogar", "Vida", "Comercio", "Combinado", "Motovehiculo"]

    # 1) Renewals due soon (active, al dia) - renovaciones agent. Mixed horizons,
    #    most within 30 days, some 31-60 to exercise the default-vs-wider window.
    for _ in range(22):
        c = g.new_client()
        g.active_aldia(c, g.rng.choice(ramos_main), g.rng.randint(3, 29))
    for _ in range(12):
        c = g.new_client()
        g.active_aldia(c, g.rng.choice(ramos_main), g.rng.randint(31, 58))

    # 2) Mora across all four buckets (active policies in arrears) - cobranza agent.
    for bucket, n in MORA_PLAN.items():
        dpd = MORA_DPD[bucket]
        for _ in range(n):
            c = g.new_client()
            # last payment = grace cycle + days-past-due ago; renewal still in the future.
            g.policy(c, g.rng.choice(["Auto", "Hogar", "Comercio", "ART", "Combinado"]),
                     estado_poliza="activa", estado_pago="en_mora",
                     venc_days=g.rng.randint(40, 300),
                     ultimo_pago_days=schema.GRACE_DAYS + dpd)

    # 3) Inactive / lapsed clients (all policies vencida or cancelada, > 6 months
    #    ago) - reactivacion agent. >180 days lapsed.
    for _ in range(20):
        c = g.new_client()
        n_pol = g.rng.choice([1, 1, 2])
        for _ in range(n_pol):
            estado = g.rng.choice(["vencida", "vencida", "cancelada"])
            g.policy(c, g.rng.choice(ramos_main), estado_poliza=estado,
                     estado_pago="al_dia",
                     venc_days=-g.rng.randint(200, 720),
                     ultimo_pago_days=g.rng.randint(200, 720))

    # 4) Cross-sell gaps - cross_sell agent. Each holds an active "has" ramo and
    #    deliberately lacks the complementary one. Renewals far off so they don't
    #    double as renovaciones.
    crosssell_specs = (
        [("Auto", 120)] * 12 +        # Auto sin Hogar / sin Vida
        [("Comercio", 150)] * 10 +    # Comercio sin ART
        [("Hogar", 200)] * 8          # Hogar sin Vida
    )
    g.rng.shuffle(crosssell_specs)
    for has, venc in crosssell_specs:
        c = g.new_client()
        g.active_aldia(c, has, venc + g.rng.randint(-20, 20))

    # 5) Healthy multi-policy clients - already cross-covered, far-off renewals,
    #    al dia. These produce NO action (so the inbox is not 100% noise) and add
    #    realistic second policies per client.
    for _ in range(30):
        c = g.new_client()
        g.active_aldia(c, "Auto", g.rng.randint(70, 200))
        g.active_aldia(c, "Hogar", g.rng.randint(70, 200))
        if g.rng.random() > 0.4:
            g.active_aldia(c, g.rng.choice(["Vida", "Combinado"]), g.rng.randint(70, 200))

    return g.policies


def main(seed=42, path=None):
    """Generate the cartera and write it to Excel. Returns the output path."""
    import pandas as pd
    path = path or paths.DEMO_CARTERA
    paths.ensure_dirs()
    policies = build(seed)
    df = pd.DataFrame([p.to_row() for p in policies], columns=schema.COLUMNS)
    df.to_excel(path, index=False, sheet_name="cartera", engine="openpyxl")
    n_clients = df["cliente_id"].nunique()
    print(f"Generadas {len(df)} polizas de {n_clients} clientes -> {path}")
    return path


if __name__ == "__main__":
    main()
