---
name: auditar-bim
description: Audita modelos BIM (.ifc 2x3, 4 y 4.3) de plantas hidráulicas, edificación e infraestructura, consultando propiedades, sistemas y colisiones deterministas.
---

# Skill: Auditar BIM

1. Registra el archivo `.ifc` mediante `register_artifact(file_path, project_id)`.
2. Consulta el resumen por clase IFC y disciplina vía `bim://{project_id}/summary` o `bim_query_elements(artifact_id, group_by="ifc_class")`.
3. Revisa plantas/niveles (`storey`) y sistemas (`IfcSystem`) en tuberías, bombeo, estructuras y obra civil.
4. Ejecuta `bim_detect_clashes` para detectar colisiones hard/soft entre disciplinas.
5. Emite hallazgos trazables con `GlobalId`, `artifact_id` y `sha256`.
