---
name: extraer-mediciones
description: Extrae mediciones deterministas desde modelos BIM (.ifc) y planos 2D (.dxf, .dwg, PDF) sin inventar cifras.
---

# Skill: Extraer Mediciones

1. Registra los archivos fuente con `register_artifact(file_path, project_id)`.
2. Para BIM, consulta cantidades y agrupaciones con `bim_query_elements` (`ifc_class`, `storey`, `disciplina`).
3. Para planos 2D, usa el pipeline híbrido (`cad_vision`: vectorial `ezdxf`/`fitz` + *tiling* para raster).
4. Consolida unidades explícitas (`m`, `m2`, `m3`, `kg`, `ud`) y marca con `requiere_revision` cualquier partida sin soporte geométrico directo.
