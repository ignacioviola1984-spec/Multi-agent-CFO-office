# Progreso del training

Bitacora de avance, fase por fase.

## Hecho

### Fase 0 — Entorno de constructor  [OK]
- Instalado y configurado: Python, Git, GitHub.
- Git configurado (user.name, user.email, init.defaultBranch=main).
- Repo ai-finance-training creado y conectado.
- Ciclo completo: editar -> git add -> git commit -m -> git push.
- hello_finance.py: primer script (calculo de margen).

### Fase 1.1 — Primera llamada a una API  [OK]
- api_fx.py y fx_rates.py: tipos de cambio reales (Frankfurter).
- Tabla multi-moneda, conversion, manejo de errores.
- Conceptos: REST API, JSON, funciones, f-strings, try/except.

### Fase 1.2 — Tool use / function calling  [OK]
- agent_fx.py: primer agente. Claude decide usar get_rate, el codigo
  la ejecuta, Claude redacta la respuesta.
- Seguridad: API key en .env, protegida por .gitignore.
- Conceptos: tool use, tool schema, loop pedir-ejecutar-devolver.

### Fase 2.1 — Operar un MCP existente  [OK concepto]
- Visto el handshake cliente/servidor en vivo (QuickBooks pidio auth).
- Conceptos: MCP, server/client, tools vs resources, auth como control.
- Sandbox de QuickBooks descartado (onboarding de Intuit, no aporta skill).

### Fase 2.2 — Construir un MCP server propio  [OK]
- finance-mcp/: server MCP de "Lumen Inc.", SaaS post-seed, 6 entidades,
  6 monedas. Herramientas: list_entities, get_pnl, get_balance_sheet,
  get_ar_aging, get_cash_position.
- Consolidacion multi-moneda con FX de cierre. Balance cuadra (check=0).
- Datos sinteticos reproducibles (generate_data.py).
- Conceptos: FastMCP, definir tools, capa de datos, validaciones.

### Fase 2.3 — Conectar el server a un cliente y endurecerlo  [OK]
- test_client.py: cliente MCP por stdio (initialize, list_tools, call_tool).
- Validacion de entity_id; errores claros propagados por el protocolo.
- Superficie read-only deliberada (sin tools de escritura).
- Pendiente opcional: conectar a Claude Desktop via config.

### Fase 3.1 — Patrones de agentes  [OK]
- orchestration/patterns.py: prompt chaining (numeros->observaciones->
  resumen) y routing (clasifica->despacha al especialista).
- Reusa el MCP server como fuente de verdad de los numeros.
- Leccion real: el modelo etiquito "vencido" como >30 dias (53%) cuando
  ~97% estaba vencido. Numeros exactos, interpretacion enganosa. Por eso
  importan evals + human-in-the-loop.

### Fase 3.2 — Orquestador con sub-agents  [OK]
- orchestration/orchestrator.py: coordinador que corre 3 sub-agentes
  (close review, cash forecast, reporting) y pasa estado entre ellos.
- finance_core.py: numeros crudos deterministicos (runway = caja / burn).

### Fase 3.3 — Checks, audit trail y escalamiento (HITL)  [OK]
- orchestration/operating_model.py: "AI Finance Operating Model v2".
- Checks deterministicos entre etapas, audit trail (audit_log.jsonl),
  reglas de escalamiento por severidad, gate human-in-the-loop.
- Corrida real: 2 escalamientos ALTA -> freno -> aprobacion humana -> board.

### Fase 4 — RAG + embeddings para finanzas document-heavy  [OK]
- document-intelligence/: busqueda semantica, RAG con citas, extraccion
  estructurada de terminos de contratos a tabla.
- Embeddings: sentence-transformers (PyTorch) con fallback model2vec.
  Backend intercambiable (no toca la busqueda).
- Criterio: RAG para pregunta puntual sobre corpus grande; full-context
  para extraer campos de un doc chico.
- Debugging real (documentado en el README del proyecto): pregunta en espanol
  sobre docs en ingles no recuperaba el fragmento correcto. El modelo NO
  alucino, se nego a responder. Diagnostico: falla de retrieval cross-lingual.
  El fix correcto (modelo multilingue) NO cargo: segfault con Python 3.14 +
  torch 2.12 (no se puede atrapar). Resolucion pragmatica: modelo en ingles +
  queries en ingles (estable). Alternativas anotadas: model2vec multilingue
  (sin torch) o embedder hosteado. Leccion -> solo un eval set lo detecta
  sistematico. Entorno: Python 3.14 es bleeding-edge, algunos modelos rompen.

## Siguiente

### Fase 5 — Evals, guardrails y confiabilidad  (LA CORONA)
- Eval set con respuestas correctas conocidas para un agente.
- Guardrails, medicion de accuracy, regression testing de prompts.
- Writeup: como hago confiable un agente financiero.

### Fase 6 — Shippear y deployar
- Hostear un proyecto, scheduler, demo.

## Notas
- Background: 17 anios en finanzas senior. Plan Max 5x.
- Modo CFO grade: rigor contable, decisiones defendibles, validar antes
  de entregar, nada inflado.
- Regla: sin repo, no aprendiste. Pedir el "por que" de cada decision.
- Human-in-the-loop: validar entre etapas como control de calidad.
- Ritmo: NO decir "cerramos por hoy" ni sugerir cortar. Se sigue hasta
  que Nacho lo indique. El decide cuando parar.
