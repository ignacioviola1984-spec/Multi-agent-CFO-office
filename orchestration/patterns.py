"""
patterns.py - Patrones de orquestacion de agentes (Fase 3.1).

Implementa los dos patrones base de "Building Effective Agents" sobre los
datos financieros reales de Lumen, reutilizando las funciones del MCP
server (una sola fuente de verdad para los numeros).

  1) Prompt chaining: pipeline fijo. La salida de un paso alimenta al
     siguiente.  numeros -> observaciones clave -> resumen ejecutivo.
  2) Routing: un router clasifica la pregunta y la despacha al
     especialista correcto (P&L, caja o aging).

Principio CFO-grade: los NUMEROS los calcula el codigo (deterministico,
sin alucinacion). Claude solo observa, decide la ruta y redacta.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz del repo.
Correr:  python patterns.py
"""

import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

# Reutilizamos la logica financiera del MCP server (no la reescribimos).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finance-mcp"))
import server as fin  # noqa: E402

load_dotenv(os.path.join(HERE, "..", ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"


def ask(prompt, max_tokens=600):
    """Una llamada simple a Claude. Devuelve solo el texto de la respuesta."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# --------------------------------------------------------------------------
# PATRON 1: Prompt chaining (pipeline fijo de pasos encadenados)
# --------------------------------------------------------------------------

def narrative_pipeline(period="2026-05"):
    # Paso 0 (codigo, no LLM): los numeros reales y exactos.
    pnl = fin.get_pnl(period, "USD")

    # Paso A (LLM): de los numeros a 3 observaciones clave.
    observaciones = ask(
        f"Estos son los numeros del P&L consolidado:\n\n{pnl}\n\n"
        "Listá las 3 observaciones mas importantes para un CFO, en bullets cortos. "
        "Usá solo los numeros de arriba; no inventes ninguno."
    )

    # Paso B (LLM): de las observaciones a un parrafo ejecutivo.
    resumen = ask(
        "Convertí estas observaciones en un parrafo ejecutivo breve, tono CFO, "
        f"directo y sin relleno:\n\n{observaciones}"
    )
    return pnl, observaciones, resumen


# --------------------------------------------------------------------------
# PATRON 2: Routing (clasifica la pregunta y despacha al especialista)
# --------------------------------------------------------------------------

# Cada "especialista" es una funcion del MCP server que trae datos reales.
HANDLERS = {
    "pnl": lambda: fin.get_pnl("2026-05", "USD"),
    "cash": lambda: fin.get_cash_position("USD"),
    "aging": lambda: fin.get_ar_aging("USD"),
}


def route_and_answer(pregunta):
    # Paso A (LLM router): clasificar la pregunta en una sola categoria.
    categoria = ask(
        "Clasificá esta pregunta financiera en UNA palabra entre: pnl, cash, aging.\n"
        f"Pregunta: {pregunta}\n"
        "Respondé solo esa palabra, sin nada mas.",
        max_tokens=10,
    ).strip().lower()

    if categoria not in HANDLERS:
        return categoria, f"No supe rutear la pregunta (router dijo: '{categoria}')."

    # Paso B (codigo): el especialista correcto trae los datos reales.
    datos = HANDLERS[categoria]()

    # Paso C (LLM): redactar la respuesta usando solo esos datos.
    respuesta = ask(
        f"Pregunta del usuario: {pregunta}\n\nDatos reales:\n{datos}\n\n"
        "Respondé en 2-3 frases, usando solo estos datos. No inventes numeros."
    )
    return categoria, respuesta


if __name__ == "__main__":
    print("=" * 60)
    print("PATRON 1 - Prompt chaining")
    print("numeros -> observaciones -> resumen ejecutivo")
    print("=" * 60)
    _, obs, resumen = narrative_pipeline("2026-05")
    print("\n[Paso A] Observaciones clave:\n" + obs)
    print("\n[Paso B] Resumen ejecutivo:\n" + resumen)

    print("\n" + "=" * 60)
    print("PATRON 2 - Routing")
    print("clasifica la pregunta -> despacha al especialista")
    print("=" * 60)
    preguntas = [
        "Como venimos de caja?",
        "Cual fue el margen operativo en mayo?",
        "Cuanto tenemos vencido de clientes?",
    ]
    for q in preguntas:
        categoria, respuesta = route_and_answer(q)
        print(f"\nPregunta: {q}")
        print(f"  -> ruta elegida: {categoria}")
        print(f"  -> respuesta: {respuesta}")
