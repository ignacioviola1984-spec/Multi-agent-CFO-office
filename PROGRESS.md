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

### Fase 5 — Evals, guardrails y confiabilidad  [OK]  (LA CORONA)
- evals/: eval_set.py (ground truth) + eval_runner.py (3 suites: numbers,
  extraction, grounding) con scorecard y exit code (regresion).
- Guardrail de grounding: el RAG debe negarse ante preguntas sin respuesta.
- Writeup "how I make a finance agent reliable" en evals/README.md.
- Suite numbers verificada (op income -756.823, cash 7.092.891; la caja se
  rebaselineo a 7.504.278 en Fase 7.5 al articular el balance, ver mas abajo).

### Fase 6.1 — Web app (Streamlit)  [OK]
- webapp/app.py: 3 pestanas (FX Agent, Operating Model con gate humano como
  boton, Document Intelligence con RAG + extraccion). Reusa el codigo del repo.
- Corre con: python -m streamlit run app.py

### Fase 7 — CFO Office: equipo multi-agente sobre estado compartido  [OK]
- cfo-office/: agentes especializados que se comunican por un "libro" comun
  (shared_state.CFOContext: put/get/audit/save a cfo_state.json), coordinados
  por un CFO orquestador. Evolucion del operating model de etapa fija a un
  equipo multi-agente con estado compartido y auditable.
- Agentes: Controller (cierre, margenes, AR), Treasury (caja, burn, runway),
  FP&A (forecast, variance MoM + variance vs presupuesto, anomalias) y el CFO
  (cfo_orchestrator) que reconcilia, consolida escalamientos y hace UN solo gate.
- Presupuesto vs actual (FP&A): se sumo budget.csv (plan deliberado, no actuals
  con ruido) + finance_core.{budget_usd,variance_usd,material_variances}. F/U por
  tipo de linea; % sobre abs(budget); el FX se cancela -> varianza puramente
  operativa. Materialidad 5% + piso USD 20k (revision mensual).
- Confiabilidad: cross_checks deterministicos entre agentes (Controller op income
  == actual de FP&A; burn de Treasury == -op income) que atrapan derivas antes
  del board; un solo HITL del CFO (los sub-agentes no duplican gate); sin doble
  conteo de escalamientos (cada riesgo tiene un dueno).
- evals/: 3 checks de regresion nuevos en la suite numbers (varianza op income,
  varianza G&A, conteo de lineas materiales). Suite numbers 6/6 PASS.
- Verificado sin tokens: corrida 2026-05 -> 5 escalamientos ALTA (perdida
  operativa, 97% AR vencida, runway 9.4m < 12, sobregasto G&A, op income vs plan);
  cross_checks OK con datos coherentes y FALLA ante inconsistencia (tamper test).
- Decision de proceso: el trabajo previo en orchestration/ (operating model v3
  con varianza) quedo descartado; la arquitectura elegida es el office. La
  capacidad de varianza vive en finance_core (compartida) y la usa el office.

### Fase 7.1 — Strategic Finance Agent  [OK]
- cfo-office/strategic_finance_agent.py: 4to agente del office. Lente de
  trayectoria y eficiencia (no de cierre): run-rate (ARR proxy), Rule of 40,
  burn multiple, magic number, mix de gasto, gap de margen a breakeven y 3
  escenarios de crecimiento. La matematica vive en finance_core.strategic_metrics
  (determinista, sin cliente -> testeable por evals); el agente solo narra.
- Integrado al CFO orquestador (1 Controller, 2 Treasury, 3 FP&A, 4 Strategic) +
  nuevo cross-check (run-rate de Strategic ata al revenue del Controller).
- Escalamientos propios sin pisar a nadie: burn multiple > 2 y "crecer no alcanza
  el breakeven" (palanca = margen, no volumen). Corrida 2026-05: 6 escalamientos
  consolidados distintos (burn multiple 11.6x, margen op -61%, etc.).
- finance_core.pnl_usd ahora expone S&M/R&D/G&A por separado.
- evals: 3 checks nuevos (run-rate, burn multiple, Rule of 40). Numbers 9/9 PASS.

### Fase 7.2 — Administration Agent (AR / AP / Tax)  [OK]
- cfo-office/administration_agent.py: supervisor que SUB-ORQUESTA tres agentes
  (ar_agent, ap_agent, tax_agent) sobre el mismo estado compartido y consolida
  sus flags en un solo reporte "Administration". Jerarquia de 2 niveles:
  CFO -> Administration -> AR/AP/Tax. Cuelga del CFO como par de los demas.
- Datos nuevos: ap_invoices.csv (cuentas a pagar) y tax_obligations.csv
  (obligaciones impositivas por jurisdiccion). AR reusa ar_invoices.csv.
- finance_core: ar_metrics (overdue + DSO), ap_metrics (overdue/upcoming + DPO),
  tax_metrics (pending/overdue/upcoming por jurisdiccion). Numeros por codigo.
- Fix de doble-conteo: la cartera vencida la escala AHORA el AR agent (se saco
  del Controller); cada riesgo con un unico dueno. Nuevo cross-check: AR del
  AR agent ata al AR del Controller.
- CFO ve 5 reportes (Controller, Treasury, Administration, FP&A, Strategic).
- evals: 2 checks nuevos (AP vencido, Tax vencido). Suite numbers 11/11 PASS.
- Corrida 2026-05 (en vivo): jerarquia completa OK; 8 escalamientos distintos
  (op loss, runway, AR 97%, AP 416.764 vencido, Tax 118.496 vencido, G&A, burn
  multiple, breakeven); board pack incorpora AR/AP/Tax.

### Fase 7.3 — Treasury 13-week cash forecast  [OK]
- finance_core.cash_forecast_13w: forecast directo de caja semanal. Caja inicial
  + burn operativo recurrente (mensual prorrateado) + liquidacion one-time de los
  saldos existentes AR (cobrado al 90%), AP y tax por semana de vencimiento (lo
  vencido cae en semana 1). Asunciones explicitas, sin doble-conteo.
- treasury_agent: reporta el forecast (caja final, semana valle, si se mantiene
  positiva) junto al runway; escala CRITICA si la caja se vuelve negativa en el
  horizonte (vista granular de corto plazo vs runway mensual).
- evals: 2 checks nuevos (caja final 13s, se mantiene positiva). Numbers 13/13.
- Corrida 2026-05: runway 9.4m; 13s termina en USD 5,14M, valle semana 13,
  positiva en todo el horizonte (no dispara la escalacion critica).

### Fase 7.4 — Internal Controls Agent (aseguramiento, estilo SOX)  [OK]
- cfo-office/internal_controls_agent.py: 6to agente del office. Capa de
  ASEGURAMIENTO: no mide performance del negocio (eso es de los otros), testea
  la INTEGRIDAD de datos y proceso. Registro de 5 controles deterministicos en
  finance_core.control_checks (el agente solo narra):
  C1 balance de comprobacion (Activo = Pasivo + Patrimonio, por entidad),
  C2 completitud de FX (toda moneda/periodo con tasa),
  C3 corte de posteo (sin documentos con fecha futura),
  C4 desembolsos duplicados (AP misma entidad+proveedor+monto),
  C5 autorizacion de desembolsos (pagos sobre el tope unico de USD 25k).
- Cada control: estado PASS / FAIL (integridad rota) / EXCEPTION (items a revisar)
  + severidad. Escala SOLO fallas de control; no duplica riesgos con dueno
  (runway, AR/AP/tax vencidos, perdida op). Corrida 2026-05: 4 PASS, 0 FAIL,
  1 EXCEPTION (6 pagos > USD 25k, total USD 217.269 -> requieren autorizacion).
- Controles con dientes (probado con tamper test): romper el balance dispara C1
  FAIL; sacar una tasa FX dispara C2 FAIL. Fix de robustez: las conversiones a
  USD saltean filas sin tasa para que C2 reporte el faltante en vez de crashear.
- Integrado al CFO orquestador como etapa [6/6]; el board pack incorpora la
  pata de assurance y las acciones la enforcean (P3). 9 escalamientos distintos.
- evals: 4 checks nuevos (libros cuadran, 0 fallas de integridad, conteo y total
  de excepciones de autorizacion). Suite numbers 17/17 PASS.
- Review adversarial (workflow multiagente, 3 lentes: correctness / ownership /
  defensibility): 8 hallazgos, 6 confirmados. Fixes aplicados sin mover numeros:
  (1) C4 clave por float redondeado (no string) -> no se escapa un duplicado por
  formato; (2) _trial_balance_imbalance filtra por periodo ademas de entidad;
  (3) C5 reframe honesto -> el AP no tiene campo de aprobador, asi que es un
  screen POR MONTO ("Large disbursements pending authorization review", no
  afirma que falte la firma), umbral fundado contra la politica de gasto.
  Diferido (requiere decision de datos/baseline): control de conciliacion
  subledger-vs-GL (hoy el AR del balance USD 2,23M no ata al subledger USD 1,15M)
  y C4 fuzzy de casi-duplicados. Leccion: C1/C4 pasan por construccion en datos
  limpios -> valen como invariantes (probados failables por tamper test), pero un
  tie-out de fuentes independientes es el control con mas valor de aseguramiento.

### Fase 7.5 — Record-to-Report: Accounting & Close + Reporting + Audit  [OK]
- Cierra el loop punta a punta: registrar -> CERRAR -> REPORTAR -> analizar ->
  controlar -> AUDITAR. Tres agentes nuevos sobre una fundacion de datos nueva.
- Fundacion de datos (generate_data.py): balance ARTICULADO de 2 periodos. Las
  cuentas de control AR/AP se generan = al subledger (atan exacto), el patrimonio
  ROTA por el resultado (RE_05 = RE_04 + NI_05) y la caja es el activo de cuadre.
  Asi los 3 estados ATAN y el flujo de efectivo CUADRA contra la variacion de
  caja. Solo cambio balance_sheet.csv (stream aleatorio intacto -> el resto de
  los numeros no se movio). Rebaseline: caja 7.09M -> 7.50M; caja-fin-13s -> 5.55M.
- Accounting & Close (sub de "Accounting & Reporting"): concilia subledger AR/AP
  vs cuenta de control del GL + articulacion del patrimonio. Escala solo si NO
  concilia (en libros limpios, 0 flags; el valor es PROBAR que ata).
- Financial Reporting (sub de "Accounting & Reporting"): produce los 3 estados
  (resultados, balance, flujo de efectivo indirecto). Escala solo integridad
  (balance no cuadra / flujo no ata).
- Accounting & Reporting (supervisor, como Administration): corre Close -> Reporting
  y consolida en un reporte. Jerarquia de 2 niveles.
- Audit (top-level, INDEPENDIENTE - tercera linea): re-ejecuta conciliaciones,
  re-piesa el balance, verifica articulacion y que el flujo cuadre, y vouchea
  desembolsos de alto valor. Emite OPINION (unqualified/qualified/adverse). No
  re-escala partidas (eso es del cierre); su salida propia es la opinion.
- finance_core: _bs_usd, subledger_totals_usd, close_reconciliations,
  income_statement, balance_sheet_statement, cash_flow_statement, audit_procedures.
  cash_total_usd ahora filtra por periodo (el balance tiene 2 periodos).
- CFO orquestador: 8 etapas [1/8]..[8/8]; 2 cross-checks nuevos (net income de
  Reporting == op income del Controller; caja de Reporting == caja de Treasury);
  board pack incorpora cierre/reporting/audit. Corrida 2026-05: cierre conciliado,
  estados producidos y atados, opinion sin salvedades; sin ruido de escalamientos.
- evals: 5 checks nuevos (cierre concilia, net income, balance cuadra, flujo ata,
  opinion sin salvedades). Suite numbers 22/22. Tamper test: romper la cuenta de
  control AR dispara open item en el cierre y opinion ADVERSE en auditoria.
- Review adversarial (workflow, 4 lentes incl. regresion): 16 hallazgos, 13
  confirmados, 3 descartados (uno cuyo "fix" habria roto la articulacion). Fixes:
  (1) REGRESION que YO introduje: el MCP server (server.py get_balance_sheet /
  get_cash_position) sumaba los 2 periodos del balance -> caja USD 15,6M en vez de
  7,5M, en silencio (el check A=P+PN seguia en ~0). Fix: filtro por periodo.
  patterns.py lo heredaba (se arregla solo). (2) Audit ahora es INDEPENDIENTE de
  verdad: re-deriva los tie-outs/cuadres desde el mayor y el subledger crudos, no
  llamando a las funciones de cierre/reporting (un bug en ellas ya no se replica).
  (3) cash_flow_statement toma tolerance (la auditoria la propaga). (4) prev period
  basado en los periodos del BALANCE (no del P&L) -> sin BREAK espurio en periodos
  sin comparativo. (5) Framing honesto: el cuadre del flujo es por construccion en
  libros consistentes -> vale como guarda de consistencia/regresion (failable por
  tamper), no aseguramiento independiente. Docs: README MCP (2 periodos), PROGRESS.
  Pendiente FLAGGEADO: el snapshot del demo publico quedo desactualizado (muestra
  la caja vieja 7,09M) -> requiere refresh del demo (fase aparte).

### Fase 7 — Fuente de datos real swappable: QuickBooks Online (sandbox)  [OK]
- sources/: QuickBooks Online (sandbox) como fuente real y SWAPPABLE que alimenta
  una capa canonica propia. El motor (finance_core) y la superficie MCP leen SOLO
  canonico; nunca dependen de nombres de objetos de QuickBooks. Hoy QuickBooks,
  manana NetSuite/SAP/Odoo/Zoho implementando la misma interfaz, sin tocar el motor.
- OAuth2 (auth-code) contra sandbox: endpoints desde el discovery doc de Intuit (no
  hardcodeados), access token con auto-refresh (~60 min), refresh token que ROTA
  (~24h) -> se persiste siempre el ultimo con su nueva expiracion; token store fuera
  del repo y gitignoreado; 401 -> refresh -> retry.
- Adapter read-only ENFORCED en codigo (solo GET/query, ningun create/update/delete;
  el scope permite escritura, la restriccion la hace el codigo), minorversion=75,
  backoff exponencial ante 429.
- Mapper deterministico QuickBooks -> canonico (12 codigos rollup); validaciones
  determinIsticas (balance cuadra, trial balance balancea, AR ata al control, sin
  postings futuros, moneda presente) que salen non-zero al fallar; snapshot inmutable
  append-only (raw/ + canonical/ + manifest con sha256 de cada archivo).
- Wiring: una sola linea source-agnostic en finance_core (FINANCE_DATA_DIR), default
  identico -> el path sintetico y todos los tests/evals intactos (22/22 numbers,
  12/12 self-improvement). Tests offline contra un fixture (sin API viva ni secret),
  sumados al CI.
- BOUNDARY honesto: sandbox != prod; la sample company es single-entity / single-
  currency (US/USD), asi que valida la capa transaccional (AR/AP/billing) y el R2R
  (P&L, balance, trial balance) contra datos reales, pero NO la consolidacion
  multi-entidad / multi-moneda (eso lo ejercita solo el modelo sintetico). budget y
  tax_obligations salen vacios para QuickBooks (sin objeto limpio), documentado. El
  fixture commiteado es representativo (modelado sobre las shapes documentadas de
  Intuit); record_fixture.py captura uno real con credenciales propias.

## Backlog del departamento (multi-agente, hacia el "full finance department")
- Faltantes mapeados (ver chat de gap analysis): Strategic Finance [HECHO],
  Administration/AR/AP/Tax [HECHO], Internal Controls [HECHO], profundizar
  Treasury (cash forecast 13s) [HECHO], Accounting & Close [HECHO], Financial
  Reporting (3 estados) [HECHO], Audit [HECHO]; faltan (profundidad, no pilares):
  Finance Compliance regulatorio, Payroll/costo de gente, AgentOps (monitoreo +
  CI), Finance Data (capa unificada).
- Demo publica (cfo-demo): ACTUALIZADO a los 8 agentes (Fase 7.6). Snapshot
  regenerado de una corrida real completa; app.py renderiza toda la jerarquia +
  los 3 estados financieros + control register + opinion de auditoria. Caja del
  demo ahora 7,50M (coincide con el repo). Probado headless con Streamlit AppTest.
  Falta SOLO el re-deploy en Streamlit Cloud (paso manual de Nacho).

### Fase 8 — Modelo realista: HITL por agente (maker-checker)  [OK]
- PRINCIPIO (Nacho, 2026-06-14): arriba de CADA agente un HITL con expertise de
  dominio. "Solo asi funciona" -> condicion de viabilidad. Un CFO generalista no
  puede aprobar competentemente TODO el flujo operativo (toca de oido); por eso el
  modelo anterior (8 agentes + 1 gate del CFO) era una simplificacion irreal.
- Implementado (cfo-office/review.py): maker-checker por funcion. Cada funcion la
  firma su experto -> Accounting Manager firma Controller y el cierre; Treasurer
  firma Treasury; Collections/AP/Tax Managers firman AR/AP/Tax; Reporting Manager
  firma los estados; FP&A Director; VP Finance; Internal Controls Manager; Internal
  Audit Lead. 11 firmas de primera linea + gate FINAL del CFO sobre lo
  consolidado/material (no re-revisa el detalle).
- Si una funcion no esta firmada por su revisor -> el cierre se BLOQUEA antes del
  CFO (no se fabrica board pack sobre trabajo no revisado). Probado: un rechazo de
  Tax bloquea. Corrida auto (CFO_AUTO_REVIEW=1): 11/11 firmadas + CFO, 52 eventos
  de audit. review.py auto-aprueba en no-interactivo (replay/CI), marcado como tal.
- Reframe: el sistema es un MULTIPLICADOR sobre cada experto, no un reemplazo del
  equipo -> lo que lo hace confiable Y adoptable.
- Discurso del repo CORREGIDO: README, cfo-office/README y PRODUCTION-READINESS ya
  no venden "un solo gate"; describen el modelo de dos niveles. El demo (cfo-demo)
  muestra las 11 firmas por experto + el gate final del CFO. Evals 22/22.

### Fase 8.1 — Operating model por ETAPAS (motor explicito)  [OK]
- Decision (Nacho, opcion A): convertir "pipeline con reviews" en un MODELO
  OPERATIVO por etapas, de punta a punta, con HITL gates por etapa. Fuera de
  alcance por pedido: Compliance, Payroll, AgentOps/CI, materiality routing.
- cfo-office\stages.py: el cierre como 8 ETAPAS. Cada etapa = maker (agente) +
  CONTROL deterministico en codigo (no el modelo) + firma del experto de dominio
  (checker) + on-reject: REWORK (cap MAX_ATTEMPTS=2) -> BLOCK. Una etapa bloqueada
  frena TODO el cierre; el gate final del CFO es contingente a que todas pasen.
  Controles duros: etapa 4 (subledgers atan, BS cuadra, cash flow foots), etapa 7
  (0 fallas de integridad), etapa 8 (opinion != adverse).
- cfo_orchestrator.py refactorizado: run() llama stages.run_all(); si una etapa
  bloquea, status "blocked_stage" y corta. Luego cross-checks (6) + escalamientos
  + gate FINAL del CFO. fpa_agent / docstrings: limpieza de wording "un solo gate".
- OPERATING-MODEL.md (nuevo): doc canonico etapas x controles x firmantes x
  rework/block x gate CFO. cfo-demo: tabla de etapas + columna "mode" + badge
  "auto-approved in this replay" + disclosure honesto (las firmas del replay son
  auto). cfo-demo/README actualizado a dos niveles.
- Verificado: corrida staged auto = 8/8 PASS + CFO 11/11, 68 eventos de audit;
  test de control-fail -> etapa "blocked" (rework y luego block). Evals 22/22.

### Fase 8.2 — Validacion con datos reales: dLocal (NASDAQ: DLO)  [OK]  (2026-06-17)
- Hito: la matematica statement-level del modelo se ATA contra los numeros
  reportados de una empresa publica real, usando SOLO sus filings publicos de la
  SEC. La afirmacion de exactitud/determinismo pasa de "asertada" a "checkeada
  contra la realidad". Evidencia y boundaries completas en
  test-dlocal/AUDIT_EVIDENCE.md.
- Preparador + auditor reproducibles (dos comandos, sin LLM, sin API keys):
  test-dlocal/run_dlocal_test.py regenera 17 figuras statement-level desde CSVs
  de input publicos de dLocal; test-dlocal/audit_dlocal_test.py las diffea
  contra una answer key derivada de la SEC (test-dlocal/EXPECTED_from_dLocal_SEC_filings.csv).
  Resultado: 17 PASS, 0 FAIL, exit 0. Python puro de libreria estandar,
  determinista (mismos bytes al re-correr).
- Las 17 figuras son statement-level: subtotales de P&L (gross profit, operating
  profit, profit before tax, net income FY2025, net income FY2024), Adjusted
  EBITDA, totales de secciones del balance (total assets FY2025 y FY2024, total
  liabilities, total equity), caja de cierre, margenes (gross, net, Adjusted
  EBITDA) y crecimiento YoY (revenue, gross profit, net income). Atan a los
  numeros consolidados reportados de dLocal FY2024/FY2025 (IFRS, USD).
- Auditor read-only y fail-closed (auto-testeado): un valor USD equivocado falla,
  claves faltantes/extra/duplicadas fallan, no-numerico falla; un porcentaje en
  el borde de la tolerancia 0.1 pasa.
- Review externo asistido por IA, dual-model: Codex reviso de forma independiente
  el repo, el diseno del test, la evidencia del eval local y los limites de las
  afirmaciones, EXTERNO al camino de generacion de output del modelo. "Externo"
  significa externo al preparador/camino de generacion. NO es una auditoria
  externa o estatutaria formal, ni una certificacion, ni una opinion de
  aseguramiento, ni un sustituto de un auditor humano.
- Eval harness local: pasa 33/33 (Numbers 22/22, Extraction 9/9, Grounding 2/2).
  Se enuncia como "pasa localmente"; no se afirma verificacion externa o de
  tercero del eval harness.
- Stress test sintetico (en frio): el modelo corrio contra CUATRO datasets
  sinteticos de fin de mes con ROUGHLY 30 errores sembrados cada uno. La
  deteccion es fuerte (la gran mayoria de las trampas se atrapan por scans de
  ID-plantado y de columnas-flag). El gap recurrente es cuantificar y clasificar
  los ajustes (montos, P&L-vs-balance, donde van las perdidas de credito), que
  todavia necesito corregir contra ground truth. Por eso el human checker se
  queda en el loop.
- BOUNDARY (no enterrar): statement-level y analitico SOLO. Ninguna empresa
  publica divulga subledgers transaccionales, asi que los agentes
  transaccionales AR/AP/tax y la consolidacion multi-entidad / multi-moneda NO
  estan validados con datos reales. El pass de dLocal valida la matematica
  determinista statement-level contra los numeros reportados de una empresa
  publica real; NO prueba el operating model completo sobre datos reales
  transaccionales. Solo datos publicos: construido unicamente con los filings
  publicos de dLocal en la SEC. dLocal no esta afiliada a este proyecto y no lo
  endorso, patrocino ni reviso. No se uso ningun dato no-publico, interno o
  confidencial. El ejercicio es ilustrativo.
- Tres angulos independientes de aseguramiento, honestos por construccion:
  trampas sinteticas adversariales (deteccion), reconciliacion contra empresa
  publica real (exactitud) y un review independiente de segundo modelo
  (dual-model), mas un auditor read-only y fail-closed. El alcance y los limites
  son explicitos; sin hype.

## Siguiente

### Fase 6.2 — Deploy + demo
- Subir online (host gratis) para URL publica.
- Grabar 3 videos POV (guiones listos), uno por proyecto.

## Notas
- Background: 17 anios en finanzas senior. Plan Max 5x.
- Modo CFO grade: rigor contable, decisiones defendibles, validar antes
  de entregar, nada inflado.
- Regla: sin repo, no aprendiste. Pedir el "por que" de cada decision.
- Human-in-the-loop: validar entre etapas como control de calidad.
- Ritmo: NO decir "cerramos por hoy" ni sugerir cortar. Se sigue hasta
  que Nacho lo indique. El decide cuando parar.
