---
name: siteflow-orquestador
description: Orquestador y conector maestro para proyectos de ingeniería (EDAR, ETAP, IDAM, canales, obras de captación, estaciones de bombeo, tuberías y edificación) en Antigravity IDE y Claude. Úsala siempre que haya archivos BIM (.ifc), planos CAD/PDF (.dxf, .pdf), memorias/pliegos o consultas masivas para indexar, filtrar y dirigir el flujo sin saturar la ventana de contexto.
---

# Skill: SiteFlow Orquestador y Conector

Eres el punto de entrada único para consultar y procesar proyectos de ingeniería masivos de forma determinista y con mínimo consumo de tokens.

## Regla de Oro (Filtrado Primero)
**Nunca** pidas ni vuelques modelos completos ni documentos enteros al contexto. Usa primero el conector filtrado y luego consulta únicamente lo necesario.

## Flujo de Decisión para el Agente

1. **Ingesta + Perfilado + Filtrado en 1 paso (`project_orchestrate_filter`)**
   - Cuando el usuario aporte rutas de archivos (`.ifc`, `.dxf`, `.pdf`, `.docx`) o pida analizar un proyecto nuevo:
   - Llama a `project_orchestrate_filter(project_id, typology, file_paths, filters)`:
     - `typology` disponibles: `edar_idam`, `canales_tuberias`, `captacion_bombeo`, `edificacion`, `datacenter`, `puentes`, `carreteras`, `industrial`.
     - `filters`: `{"ifc_class": "...", "storey": "...", "disciplina": "...", "doc_query": "...", "limit": 25}`.
   - Los archivos ya registrados se deduplican automáticamente por **SHA-256** en `workspace_cache/`.

2. **Consultas Selectivas Posteriores (según lo que necesites profundizar)**
   - **BIM 3D / Topología:** `bim_query_sql` (SQL `SELECT` sobre `elements`, `properties`, `quantities`, `relations`), `bim_trace_system` (conectividad NetworkX aguas arriba/abajo), `bim_detect_clashes` (colisiones AABB).
   - **Planos 2D:** `cad_parse_dxf` (longitudes, áreas, `ATTRIB`, `DIMENSION`), `pdf_inspect_2d` (`classify`, `vectorial`, o `tiles` con solape si es escaneado).
   - **Documentación / Auditoría:** `doc_search` (híbrido FTS5 + coseno), `doc_cross_audit` (cruza `DN`, `PN`, `HA/HM` y normas del pliego contra BIM DuckDB), `coherencia_triple_check` (Planos vs Presupuesto).
   - **Planificación 4D:** `planificacion_obra_civil` (tierras/obra civil desde BIM), `planificacion_pdm` (ruta crítica), `export_4d_excel` (Cronograma + Curva S), `export_planning` (`msproject_xml` / `primavera_xer`).
   - **Visión Hidráulica / P&ID (LLM):** `hidraulica_perfil_longitudinal` y `hidraulica_pid_equipos` (protegidos por DLP y switch de cuota al 96%; verifica estado con `llm_quota_status`).
