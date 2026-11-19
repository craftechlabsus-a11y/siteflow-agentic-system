# ENGINEERING_RULES.md — Reglas para Antigravity IDE

1. **Arquitectura Pura de Agentes**: No añadir capas web SaaS, autenticación de usuarios finales ni UI monolítica. La interfaz son los agentes (Antigravity IDE y Claude) mediante MCP y Skills.
2. **Determinismo Primero**:
   - Geometría y cantidades IFC → `ifcopenshell` + DuckDB / NetworkX (`src/engines/bim_graph/`).
   - Planos 2D → `ezdxf` + `PyMuPDF` (`fitz`) + *tiling* con solape (`src/engines/cad_vision/`).
   - Pliegos y memorias → `SQLite FTS5` + chunking jerárquico (`src/engines/docs_rag/`).
   - Planificación 4D → motor PDM/CPM y exportadores `.xml` / `.xer` (`src/engines/planning_4d/`).
3. **Transferencia Segura**: Todo archivo externo entra por `file_path` vía `ArtifactStore.register_file_path` y se cachea en `workspace_cache/` por SHA-256.
4. **Validación**: Toda modificación en motores debe incluir pruebas unitarias en `tests/` ejecutables con `pytest`.
5. **Versionado Automático (SemVer + Conventional Commits)**:
   - Usar siempre mensajes de commit con formato `<tipo>(<alcance>): <descripción>` (`feat:`, `fix:`, `perf:`, `refactor:`, `docs:`, `test:`, `chore:`, `ci:`).
   - Desarrollar nuevas versiones o mejoras en ramas (`feat/...`, `fix/...`) antes de integrar a `main`.
   - Al subir a `main`, `.github/workflows/release.yml` calcula y publica automáticamente la siguiente versión (`vX.Y.Z`), actualiza `pyproject.toml`, `src/__init__.py` y `CHANGELOG.md`.
