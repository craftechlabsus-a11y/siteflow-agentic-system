# CLAUDE.md — Reglas de Ingeniería del Sistema Agéntico

Este repositorio es un **motor agéntico de ingeniería multi-especialidad** (no una app SaaS) conectado vía **MCP** y **Skills** con **Antigravity IDE** y **Claude**.

## Dominios Cubiertos
- Plantas de tratamiento de aguas (EDAR / ETAP) y plantas desaladoras (IDAM)
- Obras de captación, estaciones de bombeo, canales de agua y tuberías
- Edificación, naves industriales, puentes, obra lineal y centros de datos

## Principios Inviolables
1. **Cero cifras inventadas**: Toda medición, cota, diámetro, presión, volumen o duración debe provenir de un motor determinista (`bim_graph`, `cad_vision`, `docs_rag`, `planning_4d`) o de una fuente documental trazable.
2. **Solo rutas, nunca `file_bytes` en JSON**: Usa `register_artifact(file_path, project_id)` para indexar archivos en `workspace_cache/` por SHA-256.
3. **Consultas selectivas**: No cargues modelos IFC completos ni pliegos de 200+ páginas en contexto. Usa `bim://`, `doc://`, `bim_query_elements` y `doc_search`.
4. **Trazabilidad SHA-256**: Todo hallazgo o discrepancia debe incluir `artifact_id`, `sha256`, sección/página o `GlobalId` IFC y nivel de impacto.
5. **Inmutabilidad de origen**: Nunca modifiques archivos en `/workspace/proyectos_compartidos/siteflowpro`.

## Flujos MCP Principales
- `project_orchestrate_filter` → **punto de entrada recomendado** (skill `siteflow-orquestador`): ingesta paralela por SHA-256 + filtrado selectivo BIM/DXF/Docs en 1 llamada
- `register_artifact` → registra e identifica por SHA-256
- `bim_query_elements` / `bim_detect_clashes` → consultas y colisiones BIM
- `doc_search` → búsqueda híbrida FTS5 en memorias y pliegos
- `planificacion_pdm` / `export_planning` → cálculo de ruta crítica PDM y exportación XML/XER
- `project_graph_summary` → estado transversal del proyecto

## Versionado Automático y Flujo Git (Obligatorio)
- **Conventional Commits** obligatorios (validados por `.githooks/commit-msg`):
  - `feat(alcance): ...` → incrementa versión **MINOR** (`0.X.0`)
  - `fix(alcance): ...` / `perf(alcance): ...` → incrementa versión **PATCH** (`0.0.X`)
  - `feat!(alcance): ...` o `BREAKING CHANGE:` en el cuerpo → incrementa **MAJOR** (`X.0.0`)
  - `docs:`, `refactor:`, `test:`, `chore:`, `ci:`, `build:` → no generan nueva versión
- **Ramas**: Trabaja mejoras en ramas `feat/<nombre>`, `fix/<nombre>` o `refactor/<nombre>` y fusiona a `main`.
- **Release automático**: Al hacer `push` o merge a `main`, GitHub Actions (`.github/workflows/release.yml`) ejecuta `python-semantic-release`, actualiza `pyproject.toml`, `src/__init__.py` y `CHANGELOG.md`, crea el tag `vX.Y.Z` y publica la Release en GitHub.
- En nuevos clones, activar el hook local con: `git config core.hooksPath .githooks`.
