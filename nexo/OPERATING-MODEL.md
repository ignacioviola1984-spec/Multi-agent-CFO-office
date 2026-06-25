# Nexo - modelo operativo (arquitectura)

Nexo re-implementa, para el dominio del productor de seguros, el mismo **modelo
operativo confiable** del CFO office: el modelo trabaja, pero hay **controles de
código** y un **humano** en los puntos críticos. Nada de Nexo importa código de
finanzas; replica los **patrones**.

## El espejo con el modelo financiero

| Pieza de Nexo | Patrón de origen (finanzas) | Qué se reusó |
|---------------|------------------------------|--------------|
| `cartera_core.py` | `orchestration/finance_core.py` | cálculo determinístico; una sola fuente de números |
| `shared_state.py` (`CarteraContext`) | `cfo-office/shared_state.py` (`CFOContext`) | libro común `put/get`, flags, audit trail, persistencia a JSON |
| `review.py` (un solo checker) | `cfo-office/review.py` (maker-checker) | simplificado a **un** checker: el productor |
| `nexo_orchestrator.py` | `cfo-office/cfo_orchestrator.py` | correr agentes sobre el estado, cross-checks, consolidar, gate final |
| `app.py` | `webapp/app.py` | UI con el HITL **como botón** |
| capa de confiabilidad | `orchestration/operating_model.py` | checks determinísticos entre etapas, audit con timestamp, escalamiento por severidad, gate HITL |

## El pipeline

```
            cartera.xlsx
                 │
                 ▼
        ┌──────────────────┐   los números salen de cartera_core (determinístico)
        │  cartera_core    │   los agentes sólo redactan; el guard frena cifras inventadas
        └──────────────────┘
                 │  detección estructurada
                 ▼
   ┌───────────────────────────────────────────────────────┐
   │  5 agentes (maker) sobre un CarteraContext compartido  │
   │  analisis · renovaciones · cobranza · reactivacion ·   │
   │  cross_sell  → cada acción entra como `pendiente`      │
   └───────────────────────────────────────────────────────┘
                 │
                 ▼
        ┌──────────────────┐   el inbox reconcilia con los detectores
        │   cross-checks   │   (si un agente se desvía de la fuente de números, salta acá)
        └──────────────────┘
                 │  ok
                 ▼
        ┌──────────────────┐   ordenada por severidad y luego confianza
        │  inbox priorizada│
        └──────────────────┘
                 │
                 ▼
        ┌──────────────────┐   el productor (único checker) aprueba / edita / rechaza
        │   gate HITL      │   auto-approve sólo en CI/replay, marcado `by="auto"`
        └──────────────────┘
                 │  sólo aprobadas/editadas
                 ▼
     Excel (acciones + dashboard)        audit trail (audit_log.jsonl + nexo_state.json)
```

## Las dos capas de confiabilidad

1. **Controles de código entre el modelo y la salida.**
   - El **guard de grounding** (`llm.py`) rechaza cualquier cifra del modelo que
     no esté en el payload del agente; cae a la plantilla determinística. La
     prosa sin fundamento nunca se emite.
   - Los **cross-checks** (`nexo_orchestrator.cross_checks`) prueban que el inbox
     reconcilia con los detectores: el conteo de acciones por tipo == lo que
     reportó cada agente, `propuestos ≤ detectados`, los buckets de mora suman su
     total, y las métricas del panel concuerdan con los detectores
     (`polizas_en_mora`, vencimientos, total de pólizas). Es la misma idea que el
     `cross_checks` del CFO: si un agente deriva distinto, salta acá y no en la
     salida.

2. **Humano en el punto crítico (HITL).**
   - Un solo checker, el **productor**. Toda acción es `pendiente` hasta que él
     decide. Sólo lo aprobado/editado se exporta; los rechazos quedan logueados.
   - El registro **quién/qué/cuándo** (reviewer, decisión, nota, timestamps) es la
     evidencia de control que el patrón maker-checker exige, simplificada a un
     único firmante.

## Trazabilidad

Cada paso -cada propuesta de agente, cada flag, cada decisión, cada cross-check-
se agrega al **audit trail** en memoria y al `nexo/audit_log.jsonl` (append-only),
y el estado completo (agentes, flags, audit, inbox, métricas) se persiste a
`nexo/nexo_state.json`. Se sabe quién escribió qué y cuándo: el sistema es
auditable y **replayable** (con `NEXO_AUTO_APPROVE=1` corre de punta a punta sin
intervención, reproduciendo los mismos números).

## Separación determinístico / LLM (la regla dura)

- **Determinístico (código):** detección, conteos, fechas, montos, buckets,
  primas, comisiones, score de confianza, severidad, selección de
  cliente/póliza, métricas, cross-checks, export.
- **LLM (prosa):** el texto de cada mensaje y la narrativa del panel - y siempre
  pasando por el guard de grounding.

Si el LLM no está disponible o se desactiva, el sistema funciona igual con
plantillas determinísticas. Lo que **nunca** cambia es que ningún número proviene
del modelo.
