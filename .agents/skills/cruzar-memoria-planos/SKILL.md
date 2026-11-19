---
name: cruzar-memoria-planos
description: Cruza especificaciones técnicas de memorias y pliegos (PPTP) contra modelos BIM y planos 2D para detectar discrepancias trazables.
---

# Skill: Cruzar Memoria y Planos

1. Consulta el índice del documento mediante `doc://{project_id}/toc`.
2. Busca parámetros críticos (diámetros DN, presiones PN, calidades de hormigón/acero, normativas UNE/EN/CTE) con `doc_search`.
3. Contrasta los requisitos encontrados frente a `bim_query_elements` y planos 2D.
4. Documenta cada discrepancia con `artifact_id`, `sha256`, página/sección y elemento afectado.
