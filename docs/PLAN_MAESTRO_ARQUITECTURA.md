# Plan Maestro de Arquitectura e Implementación: Nuevo Sistema Agéntico BIM / Ingeniería Multi-Especialidad

> [!IMPORTANT]
> **REGLA DE INMUTABILIDAD (Cero cambios en SiteFlow Pro)**:
> El repositorio origen `/workspace/siteflowpro` es de **SOLO LECTURA** durante todo este proceso. Todas las transferencias deben realizarse mediante **copia (`cp`)** hacia la nueva carpeta de proyecto. Nunca mover, renombrar ni editar archivos dentro de `/workspace/siteflowpro`.

---

## 1. Objetivo del Nuevo Sistema

Construir un sistema agéntico independiente, conectado de forma nativa con **Antigravity IDE** y **Claude (Claude Desktop / Claude Code)** mediante el protocolo **MCP (Model Context Protocol)** y el estándar abierto de **Skills (`SKILL.md`)**, optimizado para:

1. **Extraer toda la información de modelos BIM** (`.ifc` 2x3, 4 y 4.3) con precisión geométrica determinista y consultas topológicas/relacionales.
2. **Interpretar planos 2D** (`.dwg`, `.dxf`, PDF vectorial y PDF escaneado/raster) combinando extracción geométrica exacta con visión multimodal por cuadrículas (*tiling*).
3. **Analizar documentación técnica extensa** (memorias, pliegos PPTP, anejos de cálculo, estudios geotécnicos) mediante RAG jerárquico y detección automática de discrepancias.
4. **Generar planificaciones 4D** (PDM/CPM, frentes, rendimientos, exportación estándar) vinculadas a elementos BIM/CAD.
5. **Operar en múltiples tipologías y todas las especialidades** (no solo edificación): Obra Civil/Lineal, Puentes, Hidráulica (EDAR/IDAM/Canales), Data Centers, Naves Industriales y Plantas de Proceso.

---

## 2. Diagnóstico Técnico Heredado de SiteFlow Pro

### 2.1. Fortalezas a Conservar
- **Motores deterministas ("Cero cifras inventadas")**:
  - `ifcopenshell` + OpenCascade C++ (`api/domain/bim_ingesta.py`, `api/domain/bim_harness.py`).
  - `ezdxf` para geometría CAD (`api/cad/converter.py`, `api/cad/parser.py`).
  - Planificador CPM/PDM (`api/domain/planificador_obra_civil.py`).
  - Trazabilidad criptográfica mediante **SHA-256**.
- **Contrato de salida estandarizado (`SkillResult`)** en `api/skills/base.py`: `datos`, `confianza`, `requiere_revision`, `fuente`, `recortes`.
- **Perfiles sectoriales (`ProjectModuleProfile`)** en `api/ml_module_manager.py` y `api/modules/*.json`.

### 2.2. Puntos Débiles a Resolver en el Nuevo Proyecto
| Componente en SiteFlow | Limitación Detectada | Solución en el Nuevo Sistema |
| :--- | :--- | :--- |
| `api/mcp_server.py` | Solo `stdio`, solo `tools`, pasa `ctx=None` mientras las skills de BIM/CAD/Visión exigen `ctx["file_bytes"]` (HTTP upload). | **MCP Gateway Dual** (`stdio` + `Streamable HTTP`) que acepta `file_path` / `project_id`, además de exponer **MCP Resources** (`bim://`, `doc://`) y **MCP Prompts**. |
| `api/agent/coordinator.py` | `MAX_STEPS=5`, JSON manual en texto, historial de 6 turnos, salidas truncadas a 2000 caracteres. | Delegar el razonamiento principal a **Antigravity / Claude** vía MCP + un **Ejecutor DAG Real** para flujos multi-paso paralelos sin truncado. |
| `api/skills/coordinador/plan_trabajo.py` | Genera un plan DAG estático pero no lo ejecuta. | **DAG Runner** con estado persistente por proyecto y ejecución paralela por especialidad. |
| `api/domain/bim_ingesta.py` | Lista fija de ~40 clases IFC, sin entidades IFC 4.3 de obra lineal, sin grafo de conectividad (`IfcRelConnectsPorts`), devuelve todos los elementos en RAM. | **Motor BIM-Graph**: indexación en **DuckDB** + grafo **NetworkX** + soporte **IFC 4.3** (`IfcBridge`, `IfcRoad`, `IfcAlignment`, `IfcEarthworksCut/Fill`). |
| `api/skills/cad/takeoff.py` y PDFs | DWG depende de conversor externo; PDFs tratados con `PyPDF2` básico. | **Pipeline Híbrido 2D**: `PyMuPDF` (`fitz`) para PDF vectorial exacto + *tiling* con solape para planos escaneados + `ezdxf` con resolución de XREFs y atributos `ATTRIB`/`MTEXT`. |
| `api/skills/documental/extraccion.py` | Extracción LLM en una sola llamada; inviable para pliegos de 200+ páginas. | **RAG Jerárquico** (Capítulos/Secciones/Tablas/Página) con `FTS5` + embeddings vectoriales + trazabilidad exacta. |
| `api/app.py` | Monolito FastAPI con 17 routers, SPA React, WhatsApp y auth web. | **No transferir**. Mantener el nuevo proyecto como un motor agéntico puro + servidor MCP (con API ligera opcional). |

---

## 3. Matriz de Transferencia de Archivos (SiteFlow → Nuevo Proyecto)

El agente debe crear la nueva carpeta (ej. `/workspace/engi-agent-core`) y copiar **únicamente** los módulos reutilizables según esta tabla:

| Archivo / Carpeta Origen (`/workspace/siteflowpro/...`) | Destino en Nuevo Proyecto (`.../src/...`) | Acción Requerida tras Copiar |
| :--- | :--- | :--- |
| `api/domain/bim_ingesta.py` | `src/engines/bim_graph/ingesta.py` | Ampliar con IFC 4.3, `Pset_*` dinámicos y volcado a DuckDB + NetworkX. |
| `api/domain/bim_harness.py` | `src/engines/bim_graph/harness.py` | Adaptar validaciones deterministas para consultar sobre DuckDB. |
| `api/cad/converter.py` y `api/cad/parser.py` | `src/engines/cad_vision/` | Añadir extracción de `ATTRIB`, `MTEXT`, `DIMENSION` y soporte XREF. |
| `api/domain/planificador_obra_civil.py` | `src/engines/planning_4d/cpm_engine.py` | Generalizar más allá de tierras: estructuras, MEP, lineal, comisionado, export XML/XER. |
| `api/skills/base.py` y `api/skills/router.py` | `src/core/skills_base.py` y `src/core/router.py` | Mantener `SkillResult`, añadir soporte `file_path` y ejecución asíncrona. |
| `api/skills/bim/*` (`takeoff.py`, `presupuesto.py`, `auditoria.py`) | `src/engines/bim_graph/skills/` | Desacoplar de `db` SQLAlchemy de SiteFlow; usar `state_store` local. |
| `api/skills/cad/*` (`takeoff.py`, `presupuesto.py`) | `src/engines/cad_vision/skills/` | Conectar con el nuevo pipeline híbrido PDF/CAD. |
| `api/skills/documental/*` (`extraccion.py`, `coherencia.py`) | `src/engines/docs_rag/skills/` | Conectar con el índice RAG jerárquico (no texto plano completo). |
| `api/skills/planificacion/*` (`secuencia.py`, `obra_civil.py`) | `src/engines/planning_4d/skills/` | Añadir vinculación 4D a `GlobalId` IFC. |
| `api/ml_module_manager.py` y `api/modules/*.json` | `src/specialties/profiles/` | Evolucionar a **Cartuchos Componibles** (Tipología + Especialidades). |
| `api/structured.py` y `api/confidence.py` | `src/core/structured.py` y `src/core/confidence.py` | Conservar lógica de validación Pydantic y umbrales de revisión. |
| `api/llm_pool.py` | `src/core/llm_pool.py` | Añadir proveedor Anthropic directo, Ollama local y capa DLP (`dlp_guard.py`). |
| `api/mcp_server.py` | `src/mcp_gateway/server.py` | Reescribir con `Tools` por ruta, `Resources` (`bim://`, `doc://`) y `Prompts`. |
| `api/core/artifact_store.py` | `src/core/artifact_store.py` | Desacoplar de `fastapi.HTTPException` y `api.ml_security`; usar para caché SHA-256 en `workspace_cache/`. |
| `api/domain/project_graph.py` | `src/core/project_graph.py` | Integrar como grafo persistente transversal (BIM ↔ CAD ↔ Docs ↔ WBS) y adaptarlo a rutas del nuevo workspace. |
| `api/domain/bim_index.py` | `src/engines/bim_graph/bim_index.py` | Integrar extracción de `Pset_*` / `IfcSystem` y migrar/unificar almacenamiento con DuckDB. |
| `api/domain/bim_clashes.py` | `src/engines/bim_graph/clashes.py` | Conservar detección determinista AABB (NumPy) de interferencias hard/soft y exponer vía MCP. |
| `api/skills/vision/tiling.py` | `src/engines/cad_vision/tiling.py` | Integrar en el pipeline híbrido 2D para planos raster/escaneados con solapamiento. |
| `api/skills/vision/hidraulica.py` | `src/engines/cad_vision/skills/hidraulica.py` | Desacoplar de `api.schemas` / `api.skills.vision.util`; conservar soporte de perfiles longitudinales y P&ID (EDAR/IDAM). |
| `api/domain/doc_index.py` | `src/engines/docs_rag/doc_index.py` | Base del motor documental jerárquico + índice `SQLite FTS5`; ampliar con tabla/embeddings vectoriales. |
| `api/domain/planificacion_pdm.py` | `src/engines/planning_4d/pdm_engine.py` | Conservar cálculo PDM completo (FS/SS/FF/SF + lags) y exportador MS Project XML. |
| `api/domain/primavera_p6.py` | `src/engines/planning_4d/primavera_p6.py` | Conservar importador/exportador bidireccional Primavera P6 `.xer` y MSPDI `.xml`. |
| `api/domain/rendimientos_obra_civil.py` | `src/engines/planning_4d/rendimientos.py` | Conservar catálogo determinista geotécnico/obra civil y ampliar a otras especialidades. |
| **NO COPIAR**: `api/app.py`, `api/whatsapp_channel.py`, `frontend/`, `api/routers/*` | — | Permanecen intactos y aislados en SiteFlow Pro. |

---

## 4. Plan Maestro de Implementación por Fases (Para el Agente)

### Fase 0: Inicialización Segura del Nuevo Workspace
- [x] **0.1.** Definir la ruta del nuevo proyecto fuera de `/workspace/siteflowpro`: `/workspace/proyectos_compartidos/siteflow-agentic sistem ide+claude`.
- [x] **0.2.** Inicializar estructura de directorios:
  - `.agents/rules/`, `.agents/skills/`
  - `src/mcp_gateway/`, `src/orchestrator/`, `src/engines/{bim_graph,cad_vision,docs_rag,planning_4d}/`, `src/specialties/{profiles,disciplines}/`, `src/core/`
  - `tests/`, `workspace_cache/`
- [x] **0.3.** Crear `pyproject.toml` y `.gitignore` (incluyendo `workspace_cache/`, `.env`, `*.duckdb`). Dependencias base: `ifcopenshell`, `ezdxf`, `pymupdf`, `duckdb`, `networkx`, `mcp`, `pydantic`, `openpyxl`, `pytest`.
- [x] **0.4.** Ejecutar la copia controlada (`cp`) de los archivos listados en la **Matriz de Transferencia** (Sección 3), incluyendo los módulos evaluados v2.5/v2.6 (`artifact_store.py`, `project_graph.py`, `bim_index.py`, `clashes.py`, `tiling.py`, `hidraulica.py`, `doc_index.py`, `pdm_engine.py`, `primavera_p6.py`, `rendimientos.py`).
- [x] **0.5.** Verificar que `/workspace/proyectos_compartidos/siteflowpro` permanece sin modificaciones provocadas por la transferencia.

### Fase 1: Conectividad Nativa Antigravity IDE + Claude (MCP Gateway)
- [x] **1.1.** Implementar `src/mcp_gateway/server.py` con soporte dual (`stdio` y `Streamable HTTP`).
- [x] **1.2.** Adaptar los `Tools` MCP para recibir **`file_path` y `project_id`** (nunca `file_bytes` en base64 por JSON). Implementar caché por hash SHA-256 en `workspace_cache/`.
- [x] **1.3.** Implementar **MCP Resources**:
  - `bim://{project_id}/summary` (resumen de edificio, plantas, disciplinas, defectos)
  - `bim://{project_id}/storeys/{storey}` (elementos filtrados por planta)
  - `bim://{project_id}/query/{sql_or_filter}` (consultas bajo demanda)
  - `doc://{project_id}/toc` y `doc://{project_id}/section/{id}`
- [x] **1.4.** Implementar **MCP Prompts** por flujo (`/auditar-bim`, `/extraer-mediciones`, `/planificar-4d`, `/cruzar-memoria-planos`).
- [x] **1.5.** Crear la configuración compartida de agentes:
  - `.mcp.json` en la raíz del nuevo proyecto (para Claude Code / Desktop y Antigravity).
  - `CLAUDE.md` con reglas de ingeniería y uso de herramientas MCP.
  - `.agents/rules/ENGINEERING_RULES.md` (equivalente para Antigravity IDE).
  - `.agents/skills/*/SKILL.md` para cada flujo especializado.

### Fase 2: Motor BIM-Graph + Motor Planos 2D Híbrido
- [x] **2.1. BIM-Graph (`src/engines/bim_graph/`)**:
  - Ampliar `PRODUCT_CLASSES` con **IFC 4.3** (`IfcBridge`, `IfcRoad`, `IfcRailway`, `IfcAlignment`, `IfcEarthworksCut`, `IfcEarthworksFill`, `IfcCourse`, `IfcPavement`, `IfcReinforcingBar`, `IfcTendon`).
  - Extraer dinámicamente todos los `Pset_*` y `Qto_*` y almacenarlos en **DuckDB** (tablas `elements`, `properties`, `quantities`, `relations`).
  - Construir el grafo topológico en **NetworkX** usando `IfcRelConnectsPorts`, `IfcRelConnectsElements`, `IfcRelVoidsElement` e `IfcRelContainedInSpatialStructure`.
  - Exponer herramienta MCP `bim_query_sql` y `bim_trace_system` para que Claude/Antigravity consulten el modelo sin saturar su contexto.
- [x] **2.2. Planos 2D Híbrido (`src/engines/cad_vision/`)**:
  - Clasificador de entrada PDF: **Vectorial vs. Escaneado**.
  - Extractor vectorial exacto con `PyMuPDF` (`fitz`): capas, textos, cotas y trazos.
  - Generador de *tiling* con solape + vista general para planos raster/escaneados.
  - Mejorar parser `ezdxf` para atributos de bloques (`ATTRIB`), `MTEXT` y resolución de capas/cotas.

### Fase 3: Motor Documental RAG Jerárquico + Auditoría Cruzada
- [x] **3.1.** Implementar chunking estructural de PDFs/DOCX (`Capítulo → Sección → Tabla → Página + SHA-256`).
- [x] **3.2.** Indexar en **SQLite FTS5** + índice vectorial local para búsqueda híbrida exacta (códigos de partida, normativas UNE/EN/CTE/Eurocódigos, diámetros, presiones).
- [x] **3.3.** Crear herramienta MCP `doc_cross_audit` que cruce especificaciones extraídas de la Memoria/Pliego contra los elementos reales en `bim_graph` y `cad_vision`, emitiendo hallazgos trazables.

### Fase 4: Cartuchos Multi-Especialidad, Planificador 4D y DAG Runner
- [x] **4.1.** Refactorizar `api/modules/*.json` en **Cartuchos de Disciplina** (`estructuras`, `geotecnia`, `arquitectura`, `mep_proceso`, `obra_lineal`, `energia`) + **Perfiles Componibles de Tipología** (`edificacion`, `datacenter`, `edar_idam`, `puentes`, `carreteras`, `industrial`).
- [x] **4.2.** Ampliar `cpm_engine.py` con librerías de rendimientos y precedencias para todas las especialidades (incluyendo pruebas, comisionado FAT/SAT y obra lineal) y vinculación `WBS ↔ GlobalId`.
- [x] **4.3.** Añadir exportadores 4D a JSON Gantt, Excel con Curva S y XML (MS Project) / XER (Primavera P6).
- [x] **4.4.** Implementar `src/orchestrator/dag_runner.py` para ejecutar planes complejos multi-especialidad con estado persistente y validación *human-in-the-loop* cuando `confianza < umbral`.

### Fase 5: Pruebas y Verificación de Conectividad
- [x] **5.1.** Suite de tests `pytest` para cada motor (`bim_graph`, `cad_vision`, `docs_rag`, `planning_4d`).
- [x] **5.2.** Test de integración MCP (`list_tools`, `list_resources`, `call_tool` con un `.ifc` y un `.dxf` de prueba).
- [x] **5.3.** Prueba real de conexión desde **Antigravity IDE** y desde **Claude** ejecutando un flujo completo: *Ingesta IFC + Lectura de Memoria + Planificación 4D*.
