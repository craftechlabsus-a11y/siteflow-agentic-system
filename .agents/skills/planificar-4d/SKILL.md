---
name: planificar-4d
description: Genera planificaciones 4D (PDM/CPM) vinculadas a elementos BIM y rendimientos de obra civil, con exportación a MS Project XML y Primavera P6 (.xer).
---

# Skill: Planificar 4D

1. Obtén volúmenes y frentes desde `bim_query_elements` o mediciones validadas.
2. Aplica rendimientos deterministas de `src/engines/planning_4d/rendimientos.py` (tierras, hormigón, tuberías, montaje electromecánico).
3. Resuelve el grafo de precedencias (FS, SS, FF, SF + lags) con `planificacion_pdm`.
4. Exporta el cronograma resultante con `export_planning` (`msproject_xml` o `primavera_xer`).
