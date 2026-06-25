"""
schema.py - The Nexo domain layer (the cartera schema).

One row of the cartera = one policy held by a client. The dataclass below is the
single definition of a policy; the synthetic generator writes these columns to
Excel and `cartera_core.load_cartera` reads them back into `Policy` objects.

Determinism anchor: AS_OF is the reference "today" for the whole system. Every
detector defaults to it instead of `date.today()`, so the demo cartera and the
numbers derived from it are reproducible (the same way finance_core anchors on a
fixed period). It can be overridden for a real run.
"""

from dataclasses import dataclass, asdict, fields
from datetime import date, datetime
from typing import Optional

# Reference "today". The synthetic cartera is built relative to this date so the
# spread of expirations, mora buckets and inactive clients is meaningful and
# reproducible. Override via cartera_core for a live run against real dates.
AS_OF = date(2026, 6, 30)

# Argentine insurers and lines of business (ramos) used by the generator and the UI.
ASEGURADORAS = [
    "Zurich", "La Caja", "Sancor Seguros", "Federacion Patronal",
    "San Cristobal", "Mercantil Andina", "Allianz", "Provincia Seguros",
]
RAMOS = ["Auto", "Hogar", "Vida", "Comercio", "ART", "Combinado", "Motovehiculo"]

ESTADO_PAGO = ("al_dia", "en_mora")
ESTADO_POLIZA = ("activa", "vencida", "cancelada")

# Cross-sell rules: a client holding an ACTIVE policy in `has` but with NO active
# policy in `missing` is a candidate to be offered `missing`. `strength` (0..1)
# feeds the deterministic confidence score (how strong the rule is on its own).
CROSS_SELL_RULES = [
    {"has": "Auto", "missing": "Hogar", "strength": 0.75,
     "rationale": "quien asegura el auto suele tener vivienda sin cubrir"},
    {"has": "Comercio", "missing": "ART", "strength": 0.90,
     "rationale": "un comercio con empleados necesita ART por ley"},
    {"has": "Hogar", "missing": "Vida", "strength": 0.60,
     "rationale": "proteger a la familia complementa la cobertura del hogar"},
]

# Mora buckets by days past due (lower-inclusive, upper-inclusive except the open top).
MORA_BUCKETS = ["0-30", "31-60", "61-90", "90+"]

# One monthly billing cycle of grace: a monthly premium paid resets the clock for
# ~30 days, so "días de mora" = days since last payment beyond that cycle.
GRACE_DAYS = 30

# Ordered columns for the cartera Excel (one row per policy).
COLUMNS = [
    "cliente_id", "nombre", "email", "telefono", "fecha_nacimiento",
    "numero_poliza", "ramo", "aseguradora", "fecha_alta", "fecha_vencimiento",
    "prima_mensual", "suma_asegurada", "comision_pct",
    "estado_pago", "fecha_ultimo_pago", "estado_poliza",
]


@dataclass
class Policy:
    """One policy held by one client. Dates are ISO strings ('YYYY-MM-DD');
    optional fields may be None. cliente_id ties a client's policies together."""
    cliente_id: str
    nombre: str
    email: str
    telefono: str
    numero_poliza: str
    ramo: str
    aseguradora: str
    fecha_alta: str
    fecha_vencimiento: str
    prima_mensual: float
    comision_pct: float
    estado_pago: str
    estado_poliza: str
    fecha_nacimiento: Optional[str] = None
    suma_asegurada: Optional[float] = None
    fecha_ultimo_pago: Optional[str] = None

    def to_row(self) -> dict:
        return {c: getattr(self, c) for c in COLUMNS}

    @classmethod
    def from_row(cls, row: dict) -> "Policy":
        """Build a Policy from a raw dict (e.g. a pandas record), coercing types
        and normalizing empties to None. Unknown extra keys are ignored."""
        known = {f.name for f in fields(cls)}
        clean = {}
        for k in known:
            v = row.get(k, None)
            clean[k] = _norm(k, v)
        return cls(**clean)


_DATE_FIELDS = {"fecha_nacimiento", "fecha_alta", "fecha_vencimiento", "fecha_ultimo_pago"}
_FLOAT_FIELDS = {"prima_mensual", "comision_pct", "suma_asegurada"}


def _is_blank(v) -> bool:
    if v is None:
        return True
    # pandas NaN is the only value not equal to itself
    if isinstance(v, float) and v != v:
        return True
    return isinstance(v, str) and v.strip() == ""


def _norm(field: str, v):
    """Coerce a raw cell to the schema's type. Blanks -> None."""
    if _is_blank(v):
        return None
    if field in _DATE_FIELDS:
        return iso(v)
    if field in _FLOAT_FIELDS:
        return float(v)
    return str(v).strip()


def iso(v) -> str:
    """Normalize a date-like value (date, datetime, pandas Timestamp, or string)
    to an ISO 'YYYY-MM-DD' string."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    # pandas often hands back 'YYYY-MM-DD 00:00:00'
    return s.split(" ")[0].split("T")[0]


def parse(iso_str: str) -> date:
    """Parse an ISO 'YYYY-MM-DD' string to a date."""
    return date.fromisoformat(iso(iso_str))
