"""Compositor de Cartuchos Multi-Especialidad (Tipología + Disciplinas)."""
import json
import os
from typing import Any, Dict, List, Optional

TYPOLOGY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "edar_idam": {
        "label": "Plantas de Tratamiento (EDAR/ETAP) y Desaladoras (IDAM)",
        "profile_files": ["depuradoras_edar.json", "desaladoras_idam.json"],
        "disciplines": ["geotecnia", "estructuras", "mep_proceso", "energia"],
    },
    "canales_tuberias": {
        "label": "Canales de Agua y Conducciones/Tuberías",
        "profile_files": ["canales_tuberias.json"],
        "disciplines": ["geotecnia", "obra_lineal", "mep_proceso", "estructuras"],
    },
    "captacion_bombeo": {
        "label": "Obras de Captación y Estaciones de Bombeo",
        "profile_files": ["canales_tuberias.json", "depuradoras_edar.json"],
        "disciplines": ["geotecnia", "estructuras", "mep_proceso", "energia"],
    },
    "edificacion": {
        "label": "Edificación Residencial y Terciaria",
        "profile_files": ["edificacion.json"],
        "disciplines": ["geotecnia", "estructuras", "arquitectura", "mep_proceso", "energia"],
    },
    "datacenter": {
        "label": "Data Centers e Infraestructura Crítica",
        "profile_files": ["centros_datos.json"],
        "disciplines": ["estructuras", "arquitectura", "mep_proceso", "energia"],
    },
    "puentes": {
        "label": "Puentes y Viaductos",
        "profile_files": ["puentes.json"],
        "disciplines": ["geotecnia", "estructuras", "obra_lineal"],
    },
    "carreteras": {
        "label": "Carreteras y Obra Lineal",
        "profile_files": [],
        "disciplines": ["geotecnia", "obra_lineal", "estructuras"],
    },
    "industrial": {
        "label": "Naves Industriales y Plantas de Proceso",
        "profile_files": ["naves_industriales.json"],
        "disciplines": ["geotecnia", "estructuras", "arquitectura", "mep_proceso", "energia"],
    },
}


def _profiles_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _disciplines_dir() -> str:
    return os.path.join(os.path.dirname(_profiles_dir()), "disciplines")


def compose_specialty_profile(
    typology: str,
    disciplines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Combina una tipología de proyecto con sus cartuchos de disciplina."""
    key = (typology or "").strip().lower()
    if key not in TYPOLOGY_DEFAULTS:
        raise ValueError(
            f"Tipología '{typology}' no reconocida. Disponibles: {sorted(TYPOLOGY_DEFAULTS.keys())}"
        )

    cfg = TYPOLOGY_DEFAULTS[key]
    selected_disciplines = disciplines or cfg["disciplines"]

    ifc_classes: List[str] = []
    normativas: List[str] = []
    checks: List[str] = []
    loaded_disciplines: Dict[str, Any] = {}

    d_dir = _disciplines_dir()
    for d_name in selected_disciplines:
        d_path = os.path.join(d_dir, f"{d_name}.json")
        if not os.path.isfile(d_path):
            raise FileNotFoundError(f"Cartucho de disciplina no encontrado: {d_name}")
        with open(d_path, "r", encoding="utf-8") as f:
            d_data = json.load(f)
        loaded_disciplines[d_name] = d_data
        for c in d_data.get("ifc_classes", []):
            if c not in ifc_classes:
                ifc_classes.append(c)
        for n in d_data.get("normativas", []):
            if n not in normativas:
                normativas.append(n)
        for chk in d_data.get("checks_obligatorios", []):
            if chk not in checks:
                checks.append(chk)

    base_profiles: List[Dict[str, Any]] = []
    p_dir = _profiles_dir()
    for pf in cfg["profile_files"]:
        p_path = os.path.join(p_dir, pf)
        if os.path.isfile(p_path):
            with open(p_path, "r", encoding="utf-8") as f:
                base_profiles.append(json.load(f))

    return {
        "typology": key,
        "label": cfg["label"],
        "disciplines": list(selected_disciplines),
        "ifc_classes": ifc_classes,
        "normativas": normativas,
        "checks_obligatorios": checks,
        "disciplines_detail": loaded_disciplines,
        "legacy_profiles_loaded": len(base_profiles),
    }
