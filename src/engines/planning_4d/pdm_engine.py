"""Motor de Planificación 4D PDM (FS, SS, FF, SF + lags) y exportación MS Project XML (v2.6.0)."""
from collections import defaultdict, deque
from typing import Any, Dict, List
import xml.etree.ElementTree as ET

_VALID_REL_TYPES = {"FS", "SS", "FF", "SF"}


def solve_pdm(
    activities: List[Dict[str, Any]],
    dependencies: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calcula el grafo PDM completo (ES, EF, LS, LF, holgura total y ruta crítica).

    activities: [{"id": "A", "name": "...", "duration": 5.0}, ...]
    dependencies: [{"pred": "A", "succ": "B", "type": "FS|SS|FF|SF", "lag": 0.0}, ...]
    """
    acts: Dict[str, Dict[str, Any]] = {}
    for a in activities:
        aid = str(a["id"])
        dur = float(a.get("duration", 0.0))
        if dur < 0:
            raise ValueError(f"Duración negativa no permitida en actividad '{aid}': {dur}")
        acts[aid] = {"id": aid, "name": a.get("name", aid), "duration": dur}

    preds: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    succs: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    indegree: Dict[str, int] = {aid: 0 for aid in acts}

    for dep in dependencies:
        p = str(dep["pred"])
        s = str(dep["succ"])
        rtype = str(dep.get("type", "FS")).upper()
        lag = float(dep.get("lag", 0.0))
        if p not in acts or s not in acts:
            raise ValueError(f"Dependencia referencia actividad inexistente: {p} -> {s}")
        if rtype not in _VALID_REL_TYPES:
            raise ValueError(f"Tipo de relación PDM inválido '{rtype}'; use FS, SS, FF o SF.")
        if p == s:
            raise ValueError(f"Autodependencia circular detectada en '{p}'.")
        item = {"pred": p, "succ": s, "type": rtype, "lag": lag}
        preds[s].append(item)
        succs[p].append(item)
        indegree[s] += 1

    # Ordenación topológica (Kahn) con detección de ciclos
    q = deque([aid for aid, deg in indegree.items() if deg == 0])
    topo: List[str] = []
    while q:
        u = q.popleft()
        topo.append(u)
        for edge in succs[u]:
            v = edge["succ"]
            indegree[v] -= 1
            if indegree[v] == 0:
                q.append(v)

    if len(topo) != len(acts):
        raise ValueError("Ciclo detectado en el grafo de dependencias PDM.")

    # Forward Pass (ES / EF)
    es: Dict[str, float] = {}
    ef: Dict[str, float] = {}
    for u in topo:
        d_u = acts[u]["duration"]
        start_u = 0.0
        for edge in preds[u]:
            p = edge["pred"]
            rt = edge["type"]
            lag = edge["lag"]
            if rt == "FS":
                cand = ef[p] + lag
            elif rt == "SS":
                cand = es[p] + lag
            elif rt == "FF":
                cand = ef[p] + lag - d_u
            else:  # SF
                cand = es[p] + lag - d_u
            if cand > start_u:
                start_u = cand
        es[u] = round(start_u, 4)
        ef[u] = round(start_u + d_u, 4)

    project_duration = max(ef.values(), default=0.0)

    # Backward Pass (LF / LS)
    ls: Dict[str, float] = {}
    lf: Dict[str, float] = {}
    for u in reversed(topo):
        d_u = acts[u]["duration"]
        finish_u = project_duration
        for edge in succs[u]:
            s = edge["succ"]
            rt = edge["type"]
            lag = edge["lag"]
            if rt == "FS":
                cand = ls[s] - lag
            elif rt == "SS":
                cand = ls[s] - lag + d_u
            elif rt == "FF":
                cand = lf[s] - lag
            else:  # SF
                cand = lf[s] - lag + d_u
            if cand < finish_u:
                finish_u = cand
        lf[u] = round(finish_u, 4)
        ls[u] = round(finish_u - d_u, 4)

    schedule = []
    critical_path = []
    for u in topo:
        total_float = round(ls[u] - es[u], 4)
        is_crit = abs(total_float) < 1e-6
        if is_crit:
            critical_path.append(u)
        schedule.append(
            {
                "id": u,
                "name": acts[u]["name"],
                "duration": acts[u]["duration"],
                "ES": es[u],
                "EF": ef[u],
                "LS": ls[u],
                "LF": lf[u],
                "total_float": total_float,
                "is_critical": is_crit,
            }
        )

    return {
        "project_duration": round(project_duration, 4),
        "critical_path": critical_path,
        "schedule": schedule,
    }


def export_msproject_xml(
    project_name: str,
    schedule_result: Dict[str, Any],
    dependencies: List[Dict[str, Any]],
) -> str:
    """Exporta un cronograma PDM a formato estándar MS Project XML."""
    _TYPE_CODE = {"FF": "0", "FS": "1", "SF": "2", "SS": "3"}
    root = ET.Element("Project", xmlns="http://schemas.microsoft.com/project")
    ET.SubElement(root, "Name").text = project_name
    tasks_el = ET.SubElement(root, "Tasks")

    uid_map: Dict[str, str] = {}
    preds_by_succ: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for d in dependencies:
        preds_by_succ[str(d["succ"])].append(d)

    for idx, item in enumerate(schedule_result.get("schedule", []), start=1):
        uid = str(idx)
        uid_map[str(item["id"])] = uid
        t = ET.SubElement(tasks_el, "Task")
        ET.SubElement(t, "UID").text = uid
        ET.SubElement(t, "ID").text = uid
        ET.SubElement(t, "Name").text = str(item.get("name") or item["id"])
        ET.SubElement(t, "Duration").text = f"PT{int(round(item['duration'] * 8))}H0M0S"
        ET.SubElement(t, "Critical").text = "1" if item.get("is_critical") else "0"

        for d in preds_by_succ.get(str(item["id"]), []):
            p_uid = uid_map.get(str(d["pred"]))
            if not p_uid:
                continue
            pl = ET.SubElement(t, "PredecessorLink")
            ET.SubElement(pl, "PredecessorUID").text = p_uid
            ET.SubElement(pl, "Type").text = _TYPE_CODE.get(str(d.get("type", "FS")).upper(), "1")
            ET.SubElement(pl, "LinkLag").text = str(int(round(float(d.get("lag", 0.0)) * 4800)))

    return ET.tostring(root, encoding="unicode")
