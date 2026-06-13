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

## Siguiente

### Fase 3.2 / 3.3 — Orquestador con sub-agents + controles
- Orquestador + sub-agent por etapa (close, cash forecast, reporting).
- Checks entre etapas, audit trail, escalamiento (human-in-the-loop).

## Notas
- Background: 17 anios en finanzas senior. Plan Max 5x.
- Modo CFO grade: rigor contable, decisiones defendibles, validar antes
  de entregar, nada inflado.
- Regla: sin repo, no aprendiste. Pedir el "por que" de cada decision.
- Human-in-the-loop: validar entre etapas como control de calidad.
