"""
app.py - Web app de demo (Fase 6).

Una interfaz usable sobre los tres proyectos del repo, para que alguien no
tecnico los opere sin tocar codigo:

  - FX Agent: pregunta en lenguaje natural, el agente usa una tool y responde.
  - Operating Model: corre el cierre con sub-agentes y un gate humano (boton).
  - Document Intelligence: pregunta sobre contratos (RAG) y extrae terminos.

Correr:  streamlit run app.py
Requiere ANTHROPIC_API_KEY en el .env de la raiz del repo.
"""

import os
import sys

import streamlit as st
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
for sub in ("api-integration", "orchestration", "document-intelligence"):
    sys.path.insert(0, os.path.join(ROOT, sub))
load_dotenv(os.path.join(ROOT, ".env"))

# En produccion (host) la key llega como secret -> la pasamos al entorno
# antes de que los modulos creen su cliente de Anthropic.
if "ANTHROPIC_API_KEY" in st.secrets:
    os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]

st.set_page_config(page_title="AI Finance Engineering", layout="wide")
st.title("AI Finance Engineering — Live Demo")
st.caption(
    "Demo de Lumen Inc., una SaaS post-seed (datos sinteticos). "
    "Los numeros los calcula el codigo; los agentes razonan y redactan."
)

tab_fx, tab_ops, tab_docs = st.tabs(
    ["FX Agent", "Operating Model", "Document Intelligence"]
)

# --------------------------------------------------------------------------
# FX Agent
# --------------------------------------------------------------------------
with tab_fx:
    st.subheader("Agente de tipos de cambio (tool use)")
    st.write("El modelo decide cuando llamar una herramienta de FX; el codigo la ejecuta contra una API real.")
    q = st.text_input("Pregunta", "Cuanto son 1500 dolares en euros?", key="fx_q")
    if st.button("Preguntar", key="fx_btn"):
        with st.spinner("El agente esta pensando..."):
            import agent_fx
            out = agent_fx.run(q)
        if out["tool"]:
            st.markdown(f"**Herramienta pedida:** `{out['tool']}`  ·  argumentos: `{out['args']}`")
            st.markdown(f"**Dato real de la API:** `{out['rate']}`")
        st.success(out["answer"])

# --------------------------------------------------------------------------
# Operating Model (con human-in-the-loop)
# --------------------------------------------------------------------------
with tab_ops:
    st.subheader("AI Finance Operating Model v2")
    st.write("Sub-agentes de cierre y caja, motor de escalamiento, y un gate humano antes del reporte al board.")
    period = st.selectbox("Periodo", ["2026-05", "2026-04", "2026-03"], key="ops_period")

    if st.button("Correr cierre", key="ops_run"):
        with st.spinner("Corriendo sub-agentes..."):
            import finance_core as fc
            from orchestrator import close_review_agent, cash_forecast_agent
            import operating_model as om
            _, close_out = close_review_agent(period)
            cash = cash_forecast_agent(period)
            esc = om.escalations(fc.pnl_usd(period), cash)
        st.session_state.ops = {
            "period": period, "close": close_out, "cash": cash,
            "esc": esc, "report": None,
        }

    if "ops" in st.session_state and st.session_state.ops["period"] == period:
        ops = st.session_state.ops
        st.markdown("**Revision de cierre**")
        st.text(ops["close"])
        st.markdown(f"**Forecast de caja** — runway: {ops['cash']['runway']:.1f} meses")
        st.text(ops["cash"]["narrative"])

        if ops["esc"]:
            st.warning("Escalamientos que requieren aprobacion humana:")
            for sev, msg in ops["esc"]:
                st.markdown(f"- **[{sev}]** {msg}")

        if ops["report"] is None:
            if st.button("Aprobar y generar reporte al board", key="ops_approve"):
                with st.spinner("Generando reporte..."):
                    from orchestrator import reporting_agent
                    ops["report"] = reporting_agent(ops["close"], ops["cash"]["narrative"])
        if ops["report"]:
            st.markdown("**Resumen para el board**")
            st.success(ops["report"])

# --------------------------------------------------------------------------
# Document Intelligence (RAG + extraccion)
# --------------------------------------------------------------------------
with tab_docs:
    st.subheader("Finance Document Intelligence")
    st.write("Preguntas en lenguaje natural sobre contratos (RAG con citas) y extraccion de terminos clave.")

    dq = st.text_input("Pregunta sobre los documentos",
                       "What are the payment terms for the marketing agency?", key="doc_q")
    if st.button("Preguntar (RAG)", key="doc_btn"):
        with st.spinner("Buscando y respondiendo..."):
            from rag import answer
            hits, txt = answer(dq)
        st.success(txt)
        st.caption("Fuentes recuperadas: " + ", ".join(sorted(set(h[0] for h in hits))))

    st.divider()
    if st.button("Extraer terminos de todos los contratos", key="doc_extract"):
        with st.spinner("Extrayendo..."):
            from rag import extract_terms
            rows = extract_terms()
        import pandas as pd  # streamlit lo trae de dependencia
        st.dataframe(pd.DataFrame(rows).set_index("_file"))
