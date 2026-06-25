# Nexo - co-piloto del productor de seguros (AR)

Nexo es un co-piloto de IA para **un único productor de seguros** en Argentina.
Ingiere la **cartera** del productor desde un Excel y corre agentes
especializados que **proponen acciones**. El productor **aprueba cada acción**
antes de que algo se exporte (human-in-the-loop). Nada se envía automáticamente.

Es un build **simplificado, de un solo productor y específico de cliente**: sin
multi-tenant, sin login, sin gestión de usuarios.

Vive dentro del repo `ai-finance-engineering` y **reusa la arquitectura** del
modelo operativo financiero (core determinístico + estado compartido +
maker/checker HITL + orquestador), re-implementada desde cero para el dominio de
seguros. **Nexo no importa nada** de los proyectos de finanzas.

---

## Qué hace / qué no hace

**Hace**
- Carga la cartera (una fila = una póliza de un cliente) desde Excel.
- Corre 5 agentes que **proponen** acciones con un mensaje en español rioplatense.
- Pone cada acción en una **bandeja de aprobación**; el productor aprueba, edita
  o rechaza.
- Exporta lo aprobado/editado a Excel y muestra un panel de métricas.

**No hace (fuera de alcance, a propósito)**
- ❌ Cotizador / motor de cotización · ❌ logins a portales · ❌ scraping ·
  ❌ multi-aseguradora quoting.
- ❌ Envío de mensajes (mail, WhatsApp, nada). Las únicas salidas son el **Excel**
  y la **web app**.
- ❌ Multi-tenant, auth, gestión de usuarios.

---

## Principio de diseño: números determinísticos, prosa del modelo

**Toda** fecha, conteo, monto, bucket, prima, comisión, score de confianza y
selección de cliente/póliza se calcula en Python (`cartera_core.py`). El LLM
**sólo** redacta el texto de los mensajes y la narrativa del panel. Nunca calcula
ni inventa un número, fecha, cliente o póliza.

Esto se hace cumplir con un **guard de grounding** (`llm.py`): cada agente declara
qué cifras puede citar el mensaje; si el modelo nombra una cifra que no está en el
payload, el guard la rechaza y se usa la plantilla determinística (nunca se emite
prosa sin fundamento).

Por defecto Nexo corre **offline** (plantillas determinísticas): gratis, rápido y
reproducible, sin API key. Para que **Claude** redacte:
`NEXO_USE_LLM=1` (requiere `ANTHROPIC_API_KEY` en el `.env` de la raíz del repo).

---

## Cómo correr

```bash
pip install -r nexo/requirements.txt

# 1) (re)generar la cartera sintética de demo (212 pólizas / 159 clientes)
python nexo/data/generate_synthetic_cartera.py

# 2) correr el loop completo (gate HITL interactivo)
python nexo/nexo_orchestrator.py
#    auto-aprobar todo (CI/replay) y producir el Excel:
NEXO_AUTO_APPROVE=1 python nexo/nexo_orchestrator.py
#    usar Claude para la prosa:
NEXO_USE_LLM=1 python nexo/nexo_orchestrator.py

# 3) bandeja de aprobación web (Aprobar / Editar / Rechazar + exportar)
streamlit run nexo/app.py

# tests y evals (sin API key)
python -m pytest nexo/tests/
python nexo/evals/run_evals.py        # exit 0 = todo ok
```

El Excel de salida queda en `nexo/outputs/acciones_aprobadas.xlsx` (sólo se
exportan las acciones **aprobadas o editadas**).

---

## Los 5 agentes

Cada agente: **detección determinística** (`cartera_core`) → candidatos
estructurados → el LLM (o la plantilla) **redacta** el mensaje → **score de
confianza determinístico** → a la bandeja como `pendiente`.

| Agente | Detecta | Severidad / confianza |
|--------|---------|------------------------|
| `renovaciones_agent` | pólizas activas que vencen en ≤ N días (default 30) | por urgencia (≤7d ALTA) |
| `cobranza_agent` | pólizas activas en mora, por tramo 0-30/31-60/61-90/90+ | por tramo (90+ ALTA, 97%) |
| `reactivacion_agent` | clientes inactivos (todo vencido/cancelado > M meses, default 6) | por recencia del lapse |
| `cross_sell_agent` | tiene un ramo y le falta el complementario (Auto sin Hogar, Comercio sin ART, Hogar sin Vida) | por fuerza de la regla |
| `analisis_cartera_agent` | métricas de cartera (sin acción por cliente) | redacta el insight del panel |

El score de confianza es `W_DATA·completitud_de_datos + W_RULE·fuerza_de_regla`
(determinístico, **no** del LLM).

---

## HITL (human-in-the-loop)

Toda acción nace `pendiente`. Estados: `pendiente → aprobada | editada | rechazada`.
Sólo `aprobada`/`editada` se exportan; los rechazos quedan **logueados** con su
motivo. Cada decisión registra **quién / qué / cuándo** en el audit trail
(`nexo/audit_log.jsonl`) y en el estado (`nexo/nexo_state.json`).

- **App Streamlit**: aprobación/edición/rechazo **por acción** (el flujo real).
- **CLI orquestador**: gate de **lote** (aprobar todo / abortar). Con
  `NEXO_AUTO_APPROVE=1` auto-aprueba para CI/replay, registrado como `by="auto"`
  - **nunca** se hace pasar por una firma humana.

---

## Modelo de datos (la cartera)

Una fila = una póliza de un cliente (`schema.Policy`):

`cliente_id, nombre, email, telefono, fecha_nacimiento(opc), numero_poliza, ramo,
aseguradora, fecha_alta, fecha_vencimiento, prima_mensual, suma_asegurada(opc),
comision_pct, estado_pago(al_dia/en_mora), fecha_ultimo_pago, estado_poliza
(activa/vencida/cancelada)`.

La actividad/inactividad del cliente se **deriva** de los estados y fechas de sus
pólizas (no se exige un flag explícito). Ramos AR (Auto, Hogar, Vida, Comercio,
ART, Combinado, Motovehiculo) y aseguradoras AR (Zurich, La Caja, Sancor,
Federación Patronal, San Cristóbal, Mercantil Andina, Allianz, Provincia).

---

## Variables de entorno

| Variable | Default | Efecto |
|----------|---------|--------|
| `NEXO_USE_LLM` | `0` | `1` = Claude redacta; si no, plantillas determinísticas |
| `NEXO_AUTO_APPROVE` | - | `1` = el orquestador auto-aprueba (CI/replay) |
| `NEXO_RENEW_DAYS` | `30` | ventana de renovaciones |
| `NEXO_INACTIVE_MONTHS` | `6` | umbral de inactividad |
| `NEXO_CROSSSELL_LIMIT` | `20` | tope (logueado) de propuestas de cross-sell en la bandeja |
| `ANTHROPIC_API_KEY` | - | se lee del `.env` de la raíz del repo (sólo con `NEXO_USE_LLM=1`) |

---

## Decisiones de diseño y supuestos (ambigüedades resueltas)

Donde el spec dejaba margen, elegí la opción más simple consistente con las
restricciones:

1. **Fecha ancla `AS_OF = 2026-06-30`** en lugar de `date.today()`, para que la
   cartera demo y todos los detectores sean **reproducibles** (igual que
   `finance_core` se ancla a un período fijo). Se puede sobreescribir para una
   corrida real.
2. **Offline por defecto, Claude opt-in.** La prosa puede ser del modelo, pero la
   degradación a plantilla (sin key / si el guard rechaza) mantiene el sistema
   corriendo, gratis y determinístico para tests/evals/CI.
3. **Guard de grounding numérico**: ante una cifra inventada, se cae a la
   plantilla (nunca se emite prosa sin fundamento). El grounding más amplio
   (cliente/póliza) se valida en las evals.
4. **`load_cartera()` es la función; los detectores son métodos** del objeto
   `Cartera` que devuelve (soporta tanto el archivo demo como un Excel subido).
5. **Días de mora** = días desde el último pago **menos un ciclo de gracia
   mensual** (`GRACE_DAYS=30`): una prima mensual paga reinicia el reloj ~30 días.
6. **Retención** es un *proxy*: % de clientes que conservan ≥1 póliza activa (no
   hay histórico de lapses por renovación en estos datos).
7. **Tope de cross-sell** (default 20, **logueado**): las oportunidades de
   cross-sell son abundantes (~99 en la demo); el tope mantiene la bandeja usable
   sin truncar en silencio. Detección completa disponible en las métricas.
8. **Datos 100% sintéticos** (nombres genéricos, `@example.com`). Los datos
   reales del productor se mantienen fuera del repo (`.gitignore`).

---

## Mapa de archivos

```
nexo/
  schema.py                 modelo de dominio (Policy, AS_OF, ramos, reglas)
  cartera_core.py           detección + métricas determinísticas (fuente de números)
  llm.py                    capa de prosa + guard de grounding + fallback a plantilla
  agent_base.py             scaffolding común de los agentes
  renovaciones_agent.py     ┐
  cobranza_agent.py         │  los 5 agentes
  reactivacion_agent.py     │
  cross_sell_agent.py       │
  analisis_cartera_agent.py ┘
  shared_state.py           CarteraContext (estado + audit + persistencia)
  review.py                 bandeja de un solo checker (el productor)
  nexo_orchestrator.py      loop: agentes → cross-checks → inbox priorizada → HITL → export
  app.py                    bandeja de aprobación (Streamlit)
  outputs/excel_writer.py   export a Excel (aprobadas + dashboard)
  data/generate_synthetic_cartera.py   generador sintético → cartera_demo.xlsx
  evals/run_evals.py        grounding · determinismo · scope (exit != 0 si falla)
  tests/                    46 tests (core, spine, agentes, orquestador, export)
  paths.py                  rutas canónicas
  OPERATING-MODEL.md        arquitectura y espejo con el modelo financiero
```

Ver [OPERATING-MODEL.md](OPERATING-MODEL.md) para la arquitectura en detalle.
