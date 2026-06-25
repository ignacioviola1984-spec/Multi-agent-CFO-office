"""
app.py - Nexo: bandeja de aprobación del productor (Streamlit).

Espejo del patrón HITL-como-botón de webapp/app.py, para el dominio del
productor de seguros:

  subir la cartera (o usar la demo) -> correr los agentes -> revisar la BANDEJA
  de aprobación (cada acción pendiente con tipo, cliente, detalle, confianza % y
  el mensaje propuesto EDITABLE, más botones Aprobar / Aprobar con edición /
  Rechazar) -> ver el PANEL con las métricas e insight -> exportar a Excel sólo
  lo aprobado/editado.

Nada se exporta sin la decisión del productor. Los números los calcula el código;
el modelo sólo redacta.

Correr:  streamlit run nexo/app.py
"""

import os
import sys

import streamlit as st
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
load_dotenv(os.path.join(HERE, "..", ".env"))

# En un host, la key llega como secret -> al entorno antes de crear el cliente.
# Envuelto en try/except: sin archivo de secrets, acceder a st.secrets puede
# lanzar, y no queremos que eso rompa el arranque local.
try:
    if "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

import cartera_core as cc
import review
import nexo_orchestrator as orch
import llm
from shared_state import CarteraContext
from outputs import excel_writer

st.set_page_config(page_title="Nexo · co-piloto del productor", layout="wide")
st.title("Nexo - co-piloto del productor de seguros")
st.caption("Cartera sintética de demo. Los números los calcula el código; los agentes "
           "redactan. Vos aprobás cada acción antes de exportar nada (human-in-the-loop).")

SEV_EMOJI = {"ALTA": "🔴", "MEDIA": "🟠", "BAJA": "🟢"}
TIPO_LABEL = {"renovacion": "Renovación", "cobranza": "Cobranza",
              "reactivacion": "Reactivación", "cross_sell": "Cross-sell"}

# --------------------------------------------------------------------------
# Barra lateral: fuente de datos + correr los agentes.
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("1) Cartera")
    fuente = st.radio("Fuente de datos", ["Cartera demo", "Subir Excel"], key="fuente")
    archivo = None
    if fuente == "Subir Excel":
        archivo = st.file_uploader("Cartera (.xlsx)", type=["xlsx"])

    st.header("2) Redacción")
    usar_llm = st.checkbox("Usar Claude para redactar (requiere API key)",
                           value=llm.use_llm())
    st.caption("Sin tildar, los mensajes salen de plantillas determinísticas "
               "(gratis, reproducible). Con Claude, el modelo redacta y el guard "
               "rechaza cualquier cifra que no esté en los datos.")

    st.header("3) Correr")
    if st.button("▶ Correr agentes", type="primary", use_container_width=True):
        os.environ["NEXO_USE_LLM"] = "1" if usar_llm else "0"
        try:
            cart = cc.load_cartera(archivo if (fuente == "Subir Excel" and archivo) else None)
            ctx = CarteraContext(fresh_audit=True)
            ctx, issues = orch.build_inbox(cart, ctx)
            st.session_state.ctx_state = ctx.state
            st.session_state.issues = issues
            st.session_state.export_bytes = None
        except Exception as e:
            st.session_state.ctx_state = None
            st.error(f"No se pudo cargar/correr la cartera: {e}")
    st.caption(f"Modo de redacción actual: {'Claude (LLM)' if llm.use_llm() else 'plantillas'}")


def _ctx():
    """Reconstruct a CarteraContext bound to the session state (so edits persist)."""
    if not st.session_state.get("ctx_state"):
        return None
    ctx = CarteraContext()
    ctx.state = st.session_state.ctx_state
    return ctx


ctx = _ctx()
if ctx is None:
    st.info("Elegí la cartera y tocá **Correr agentes** en la barra lateral para empezar.")
    st.stop()

if st.session_state.get("issues"):
    st.error("El inbox no reconcilia con los detectores; revisá los datos:")
    for i in st.session_state["issues"]:
        st.write("-", i)
    st.stop()

tab_inbox, tab_panel = st.tabs(["📥 Bandeja de aprobación", "📊 Panel"])

# --------------------------------------------------------------------------
# Bandeja de aprobación (HITL).
# --------------------------------------------------------------------------
with tab_inbox:
    s = review.summary(ctx)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Propuestas", s["total"])
    c2.metric("Pendientes", s["pendientes"])
    c3.metric("Aprobadas/editadas", s["exportables"])
    c4.metric("Rechazadas", s["by_estado"].get(review.RECHAZADA, 0))

    f1, f2 = st.columns([2, 1])
    tipos = ["(todos)"] + sorted({d["tipo"] for d in ctx.state["inbox"]})
    tipo_sel = f1.selectbox("Filtrar por tipo", tipos, format_func=lambda t: TIPO_LABEL.get(t, t))
    max_n = f2.number_input("Máximo a mostrar", 5, 500, 25, step=5)

    pend = [d for d in review.prioritized(ctx) if d["estado"] == review.PENDIENTE]
    if tipo_sel != "(todos)":
        pend = [d for d in pend if d["tipo"] == tipo_sel]

    if not pend:
        st.success("No quedan acciones pendientes con este filtro. 🎉")
    st.caption(f"Mostrando {min(len(pend), int(max_n))} de {len(pend)} pendientes "
               "(ordenadas por severidad y confianza).")

    for d in pend[:int(max_n)]:
        sev = d["severidad"]
        head = (f"{SEV_EMOJI.get(sev,'')} {TIPO_LABEL.get(d['tipo'], d['tipo'])} · "
                f"{d['cliente_nombre']} · conf {d['confianza']:.0%}")
        with st.expander(head, expanded=(sev == "ALTA")):
            st.markdown(f"**Detalle:** {d['detalle']}")
            canal = " · ".join(filter(None, [d.get("email"), d.get("telefono")])) or "sin contacto"
            st.caption(f"Cliente {d['cliente_id']} · {canal}"
                       + (f" · póliza {d['poliza']}" if d.get("poliza") else ""))
            nuevo = st.text_area("Mensaje propuesto (editable)", d["mensaje_propuesto"],
                                 key=f"msg_{d['id']}", height=120)
            nota = st.text_input("Nota de decisión (opcional)", key=f"nota_{d['id']}")
            b1, b2, b3 = st.columns(3)
            if b1.button("✓ Aprobar", key=f"ap_{d['id']}", use_container_width=True):
                review.approve(ctx, d["id"], note=nota)
                st.session_state.ctx_state = ctx.state
                st.rerun()
            if b2.button("✎ Aprobar con edición", key=f"ed_{d['id']}", use_container_width=True):
                review.edit(ctx, d["id"], nuevo, note=nota)
                st.session_state.ctx_state = ctx.state
                st.rerun()
            if b3.button("✗ Rechazar", key=f"re_{d['id']}", use_container_width=True):
                review.reject(ctx, d["id"], note=nota)
                st.session_state.ctx_state = ctx.state
                st.rerun()

    st.divider()
    st.subheader("Exportar")
    st.caption("Sólo se exportan las acciones aprobadas o editadas.")
    cexp1, cexp2 = st.columns([1, 2])
    if cexp1.button("📤 Generar Excel de aprobadas", use_container_width=True):
        path = excel_writer.export(ctx)
        with open(path, "rb") as f:
            st.session_state.export_bytes = f.read()
        st.success(f"Exportadas {len(review.approved_for_export(ctx))} acciones.")
    if st.session_state.get("export_bytes"):
        cexp2.download_button(
            "⬇ Descargar acciones_aprobadas.xlsx", st.session_state.export_bytes,
            file_name="acciones_aprobadas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True)

# --------------------------------------------------------------------------
# Panel (dashboard).
# --------------------------------------------------------------------------
with tab_panel:
    m = ctx.state.get("metrics", {})
    if not m:
        st.info("Corré los agentes para ver el panel.")
    else:
        st.subheader("Insight del panel")
        st.success(ctx.get("analisis_cartera_agent", "narrative", ""))

        g = st.columns(4)
        g[0].metric("Pólizas", m["total_polizas"])
        g[1].metric("Clientes", m["total_clientes"])
        g[2].metric("Prima mensual (ARS)", f"{m['prima_mensual_total']:,.0f}".replace(",", "."))
        g[3].metric("Comisión mensual (ARS)", f"{m['comision_mensual_total']:,.0f}".replace(",", "."))
        g2 = st.columns(4)
        g2[0].metric("Mora (% pólizas)", f"{m['pct_en_mora_polizas']:.1f}%")
        g2[1].metric("Vencen en 30 días", m["vencimientos_proximos"])
        g2[2].metric("Clientes inactivos", m["clientes_inactivos"])
        g2[3].metric("Retención", f"{m['retencion_pct']:.1f}%")

        import pandas as pd
        cmix1, cmix2 = st.columns(2)
        with cmix1:
            st.markdown("**Mix por aseguradora**")
            st.dataframe(pd.DataFrame(
                [{"aseguradora": k, "pólizas": v["count"],
                  "prima_mensual": round(v["prima_mensual"])}
                 for k, v in m["mix_por_aseguradora"].items()]
            ).sort_values("prima_mensual", ascending=False), hide_index=True,
                use_container_width=True)
        with cmix2:
            st.markdown("**Mix por ramo**")
            st.dataframe(pd.DataFrame(
                [{"ramo": k, "pólizas": v["count"],
                  "prima_mensual": round(v["prima_mensual"])}
                 for k, v in m["mix_por_ramo"].items()]
            ).sort_values("pólizas", ascending=False), hide_index=True,
                use_container_width=True)
