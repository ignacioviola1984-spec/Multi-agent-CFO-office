"""
shared_state.py - Estado compartido del CFO office.

Es el "libro" comun que todos los agentes leen y escriben. La comunicacion
entre agentes pasa por aca (no por una malla libre): cada agente deja su
resultado estructurado, y los demas (y el CFO orquestador) lo consumen.
Cada paso queda en un audit trail. Se persiste a cfo_state.json.

Esto es lo que hace auditable y trazable al sistema: se sabe quien escribio
que y cuando.
"""

import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "cfo_state.json")


class CFOContext:
    def __init__(self):
        self.state = {"agents": {}, "audit": []}

    def put(self, agent, payload):
        """Un agente deja su resultado en el libro comun."""
        self.state["agents"].setdefault(agent, {}).update(payload)

    def get(self, agent, key=None, default=None):
        """Otro agente lee lo que dejo un agente previo (comunicacion lateral)."""
        a = self.state["agents"].get(agent, {})
        return a if key is None else a.get(key, default)

    def audit(self, agent, status, detail):
        evt = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "agent": agent, "status": status, "detail": detail,
        }
        self.state["audit"].append(evt)
        print(f"  [audit] {agent:12} {status:10} {detail}")

    def save(self):
        # allow_nan=False: si un inf/NaN se cuela (ej. un runway mal calculado),
        # falla fuerte aca en vez de escribir un JSON invalido (Infinity/NaN)
        # que un parser estricto rechaza despues. Mejor romper en la fuente.
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2, allow_nan=False)
        return STATE_PATH
