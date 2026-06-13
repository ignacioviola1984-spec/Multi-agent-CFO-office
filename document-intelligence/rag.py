"""
rag.py - RAG y extraccion estructurada (Fase 4.2).

Dos patrones complementarios sobre los documentos financieros:

  1) answer(): RAG. Recupera los fragmentos mas relevantes (search.py) y
     Claude responde usando SOLO eso, citando el documento fuente.
  2) extract_terms(): extraccion estructurada. Para cada contrato, Claude
     devuelve los terminos clave en JSON, que mostramos como tabla.

Criterio (la skill que importa): para una pregunta puntual sobre un corpus
grande, conviene RAG (traer solo lo relevante). Para extraer los campos de
UN documento chico, conviene pasarle el documento entero: ahi el RAG sobra.
Elegir bien es el punto, no usar RAG por defecto.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz del repo.
"""

import glob
import json
import os

from dotenv import load_dotenv
from anthropic import Anthropic

from search import search

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"
DOCS = os.path.join(HERE, "docs")


def answer(question, k=5):
    hits = search(question, k)
    context = "\n\n".join(f"[{name}] {snip}" for name, snip, _ in hits)
    prompt = (
        f"Contexto (fragmentos de documentos):\n{context}\n\n"
        f"Pregunta: {question}\n\n"
        "Responde usando SOLO el contexto. Cita el documento entre corchetes. "
        "Si el contexto no alcanza, decilo en vez de inventar."
    )
    txt = client.messages.create(
        model=MODEL, max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    ).content[0].text
    return hits, txt


CONTRACT_FIELDS = ["counterparty", "effective_date", "term", "renewal",
                   "fees", "payment_terms", "governing_law"]


def extract_terms():
    rows = []
    for path in sorted(glob.glob(os.path.join(DOCS, "contract_*.md"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        prompt = (
            f"Documento:\n{text}\n\n"
            f"Extrae estos campos y devolve SOLO un JSON: {CONTRACT_FIELDS}. "
            "Valores en strings cortos. Si un campo no aparece, pone 'n/a'."
        )
        raw = client.messages.create(
            model=MODEL, max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        ).content[0].text
        try:
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        except Exception:
            data = {f: "?" for f in CONTRACT_FIELDS}
        data["_file"] = os.path.basename(path)
        rows.append(data)
    return rows


if __name__ == "__main__":
    print("=" * 60)
    print("RAG - preguntas en lenguaje natural sobre los documentos")
    print("=" * 60)
    for q in ["What notice is required to avoid renewing the cloud contract?",
              "What are the payment terms for the marketing agency?"]:
        hits, txt = answer(q)
        print(f"\nQ: {q}")
        print("  fuentes recuperadas:", ", ".join(sorted(set(h[0] for h in hits))))
        print("  R:", txt.strip())

    print("\n" + "=" * 60)
    print("Extraccion estructurada de terminos de contratos")
    print("=" * 60)
    for r in extract_terms():
        print(f"\n{r['_file']}")
        for fld in CONTRACT_FIELDS:
            print(f"  {fld:14}: {r.get(fld, '?')}")
