"""Motor de Ingesta Geométrica 3D para modelos BIM (IFC2x3 / IFC4).

Capa H1 del Harness Determinista:
- Extrae sólidos 3D mediante el motor C++ OpenCascade de `ifcopenshell.geom` en paralelo.
- Calcula volúmenes exactos de malla cerrada, áreas netas, bounding boxes en coordenadas
  globales (`use-world-coords=True`), cotas $Z$, materiales, contenedores espaciales
  (`IfcBuildingStorey`) y cantidades declaradas en `Qto_*BaseQuantities`.
- Garantiza trazabilidad criptográfica mediante hash SHA-256 del archivo `.ifc`.
"""
import hashlib
import multiprocessing
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

PRODUCT_CLASSES: Tuple[str, ...] = (
    # Estructuras y Cimentación
    "IfcFooting",
    "IfcPile",
    "IfcColumn",
    "IfcBeam",
    "IfcSlab",
    "IfcWall",
    # Arquitectura y Envolvente
    "IfcStair",
    "IfcRoof",
    "IfcCovering",
    "IfcBuildingElementProxy",
    # Carpinterías y Huecos
    "IfcDoor",
    "IfcWindow",
    "IfcCurtainWall",
    "IfcPlate",
    "IfcMember",
    # MEP Fontanería y Saneamiento
    "IfcPipeSegment",
    "IfcPipeFitting",
    "IfcSanitaryTerminal",
    "IfcWasteTerminal",
    # MEP Climatización y Ventilación
    "IfcDuctSegment",
    "IfcDuctFitting",
    "IfcAirTerminal",
    "IfcUnitaryEquipment",
    "IfcEnergyConversionDevice",
    # MEP Electricidad y Telecomunicaciones
    "IfcCableCarrierSegment",
    "IfcCableSegment",
    "IfcLightFixture",
    "IfcElectricDistributionBoard",
    "IfcOutlet",
    # Obra Hidráulica, Proceso y compatibilidad IFC2X3
    "IfcTank",
    "IfcDistributionChamberElement",
    "IfcValve",
    "IfcPump",
    "IfcFlowMeter",
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowTerminal",
    "IfcFlowController",
    "IfcFlowMovingDevice",
    "IfcFlowStorageDevice",
    # IFC 4.3 Obra Lineal, Puentes, Geotecnia y Armaduras
    "IfcBridge",
    "IfcRoad",
    "IfcRailway",
    "IfcAlignment",
    "IfcEarthworksCut",
    "IfcEarthworksFill",
    "IfcCourse",
    "IfcPavement",
    "IfcReinforcingBar",
    "IfcTendon",
    "IfcFacilityPart",
)

DISCIPLINA_BY_IFC_CLASS: Dict[str, str] = {
    "IfcFooting": "ESTRUCTURAS",
    "IfcPile": "ESTRUCTURAS",
    "IfcColumn": "ESTRUCTURAS",
    "IfcBeam": "ESTRUCTURAS",
    "IfcSlab": "ESTRUCTURAS",
    "IfcWall": "ESTRUCTURAS",
    "IfcStair": "ARQUITECTURA",
    "IfcRoof": "ARQUITECTURA",
    "IfcCovering": "ARQUITECTURA",
    "IfcDoor": "CARPINTERIAS",
    "IfcWindow": "CARPINTERIAS",
    "IfcCurtainWall": "CARPINTERIAS",
    "IfcPlate": "CARPINTERIAS",
    "IfcMember": "CARPINTERIAS",
    "IfcPipeSegment": "MEP_FONTANERIA",
    "IfcPipeFitting": "MEP_FONTANERIA",
    "IfcSanitaryTerminal": "MEP_FONTANERIA",
    "IfcWasteTerminal": "MEP_FONTANERIA",
    "IfcDuctSegment": "MEP_HVAC",
    "IfcDuctFitting": "MEP_HVAC",
    "IfcAirTerminal": "MEP_HVAC",
    "IfcUnitaryEquipment": "MEP_HVAC",
    "IfcEnergyConversionDevice": "MEP_HVAC",
    "IfcCableCarrierSegment": "MEP_ELECTRICIDAD",
    "IfcCableSegment": "MEP_ELECTRICIDAD",
    "IfcLightFixture": "MEP_ELECTRICIDAD",
    "IfcElectricDistributionBoard": "MEP_ELECTRICIDAD",
    "IfcOutlet": "MEP_ELECTRICIDAD",
    # Obra Hidráulica, Proceso y compatibilidad IFC2X3
    "IfcTank": "OBRA_HIDRAULICA",
    "IfcDistributionChamberElement": "OBRA_HIDRAULICA",
    "IfcFlowStorageDevice": "OBRA_HIDRAULICA",
    "IfcValve": "PROCESO_Y_BOMBEO",
    "IfcPump": "PROCESO_Y_BOMBEO",
    "IfcFlowMeter": "PROCESO_Y_BOMBEO",
    "IfcFlowController": "PROCESO_Y_BOMBEO",
    "IfcFlowMovingDevice": "PROCESO_Y_BOMBEO",
    "IfcFlowSegment": "MEP_FONTANERIA",
    "IfcFlowFitting": "MEP_FONTANERIA",
    "IfcFlowTerminal": "MEP_FONTANERIA",
    # IFC 4.3 Obra Lineal, Puentes, Geotecnia y Armaduras
    "IfcBridge": "ESTRUCTURAS",
    "IfcRoad": "OBRA_LINEAL",
    "IfcRailway": "OBRA_LINEAL",
    "IfcAlignment": "OBRA_LINEAL",
    "IfcEarthworksCut": "MOVIMIENTO_TIERRAS",
    "IfcEarthworksFill": "MOVIMIENTO_TIERRAS",
    "IfcCourse": "OBRA_LINEAL",
    "IfcPavement": "OBRA_LINEAL",
    "IfcReinforcingBar": "ESTRUCTURAS",
    "IfcTendon": "ESTRUCTURAS",
    "IfcFacilityPart": "OBRA_CIVIL",
}


def calcular_sha256_archivo(path: str) -> str:
    """Calcula el hash criptográfico SHA-256 de un archivo en disco por bloques."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extraer_elementos_ifc(
    ifc_path: str,
    max_workers: Optional[int] = None,
) -> Dict[str, Any]:
    """Extrae la geometría 3D verificada y los metadatos semánticos de un modelo IFC.

    Args:
        ifc_path: Ruta al archivo `.ifc` en disco.
        max_workers: Hilos C++ para el iterador geométrico OpenCascade.

    Returns:
        Diccionario con metadatos del proyecto (`sha256`, `schema`, `unit_scale`,
        `project_name`, `storeys`) y la lista determinista de `elements`.
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.shape as ush
    import ifcopenshell.util.unit as uunit

    if not os.path.exists(ifc_path):
        raise FileNotFoundError(f"Archivo IFC no encontrado: {ifc_path}")

    sha256_hex = calcular_sha256_archivo(ifc_path)
    f = ifcopenshell.open(ifc_path)
    unit_scale = float(uunit.calculate_unit_scale(f))

    projects = f.by_type("IfcProject")
    project_name = (projects[0].Name if projects and projects[0].Name else "Proyecto BIM")
    storeys_raw = f.by_type("IfcBuildingStorey")
    storeys_info = [
        {
            "gid": st.GlobalId,
            "name": st.Name or f"Planta_{idx}",
            "elevation": float(getattr(st, "Elevation", 0.0) or 0.0),
        }
        for idx, st in enumerate(storeys_raw)
    ]

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    workers = max_workers or max(1, multiprocessing.cpu_count() - 1)

    geo_by_gid: Dict[str, Dict[str, Any]] = {}
    iterator = ifcopenshell.geom.iterator(settings, f, workers)
    if iterator.initialize():
        while True:
            shape = iterator.get()
            geom = shape.geometry
            verts = ush.get_vertices(geom)
            if len(verts) > 0:
                mn = verts.min(axis=0)
                mx = verts.max(axis=0)
                vol = float(ush.get_volume(geom))
                geo_by_gid[shape.guid] = {
                    "vol": vol,
                    "bmin": [float(x) for x in mn.tolist()],
                    "bmax": [float(x) for x in mx.tolist()],
                }
            if not iterator.next():
                break

    elements: List[Dict[str, Any]] = []
    seen_gids = set()
    for cls in PRODUCT_CLASSES:
        try:
            class_elements = f.by_type(cls)
        except RuntimeError:
            # Entidad no existente en el esquema del archivo (p. ej. IFC4 en un modelo IFC2X3)
            continue
        for el in class_elements:
            gid = el.GlobalId
            if gid in seen_gids or gid not in geo_by_gid:
                continue
            seen_gids.add(gid)
            g = geo_by_gid[gid]
            ext = sorted(np.array(g["bmax"]) - np.array(g["bmin"]))  # d1 <= d2 <= d3
            zext = float(g["bmax"][2] - g["bmin"][2])
            mat = ue.get_material(el)
            mat_name = getattr(mat, "Name", None) if mat else None

            qtos = ue.get_psets(el, qtos_only=True)
            declared_vol: Optional[float] = None
            declared_area: Optional[float] = None
            for qset in qtos.values():
                if not isinstance(qset, dict):
                    continue
                if "NetVolume" in qset and qset["NetVolume"] is not None:
                    declared_vol = float(qset["NetVolume"])
                elif "GrossVolume" in qset and qset["GrossVolume"] is not None and declared_vol is None:
                    declared_vol = float(qset["GrossVolume"])
                if "NetSideArea" in qset and qset["NetSideArea"] is not None:
                    declared_area = float(qset["NetSideArea"])
                elif "NetArea" in qset and qset["NetArea"] is not None and declared_area is None:
                    declared_area = float(qset["NetArea"])

            # Extracción de PropertySets técnicos (Pset_*)
            psets = ue.get_psets(el, psets_only=True)
            fire_rating: Optional[str] = None
            is_external: Optional[bool] = None
            load_bearing: Optional[bool] = None
            thermal_transmittance: Optional[float] = None
            nominal_diameter: Optional[float] = None

            for pset_name, props in psets.items():
                if not isinstance(props, dict):
                    continue
                if "FireRating" in props and props["FireRating"]:
                    fire_rating = str(props["FireRating"])
                if "IsExternal" in props and props["IsExternal"] is not None:
                    is_external = bool(props["IsExternal"])
                if "LoadBearing" in props and props["LoadBearing"] is not None:
                    load_bearing = bool(props["LoadBearing"])
                if "ThermalTransmittance" in props and props["ThermalTransmittance"] is not None:
                    try:
                        thermal_transmittance = float(props["ThermalTransmittance"])
                    except (ValueError, TypeError):
                        pass
                if "NominalDiameter" in props and props["NominalDiameter"] is not None:
                    try:
                        nominal_diameter = float(props["NominalDiameter"])
                    except (ValueError, TypeError):
                        pass

            container = ue.get_container(el)
            storey_name = container.Name if (container and getattr(container, "Name", None)) else None
            thick = float(ext[0])
            area_geom = float(g["vol"] / thick) if thick > 1e-6 else 0.0

            ifc_cls = el.is_a()
            disc = DISCIPLINA_BY_IFC_CLASS.get(ifc_cls, "ESTRUCTURAS")
            # Refinamiento para IfcWall: si no es de carga y es de ladrillo/yeso, clasificar en Arquitectura
            if ifc_cls == "IfcWall" and load_bearing is False:
                disc = "ARQUITECTURA"
            elif ifc_cls == "IfcWall" and mat_name and any(k in mat_name.lower() for k in ("ladrillo", "yeso", "pladur", "partition")):
                disc = "ARQUITECTURA"

            elements.append({
                "gid": gid,
                "ifc_class": ifc_cls,
                "name": el.Name or "",
                "material": mat_name,
                "storey": storey_name,
                "disciplina_ifc": disc,
                "vol": float(g["vol"]),
                "area": area_geom,
                "declared_vol": declared_vol,
                "declared_area": declared_area,
                "d1": float(ext[0]),
                "d2": float(ext[1]),
                "d3": float(ext[2]),
                "zext": zext,
                "zmin": float(g["bmin"][2]),
                "bmin": g["bmin"],
                "bmax": g["bmax"],
                "psets": {
                    "fire_rating": fire_rating,
                    "is_external": is_external,
                    "load_bearing": load_bearing,
                    "thermal_transmittance": thermal_transmittance,
                    "nominal_diameter": nominal_diameter,
                },
            })

    return {
        "sha256": sha256_hex,
        "schema": str(f.schema),
        "unit_scale": unit_scale,
        "project_name": project_name,
        "storeys": storeys_info,
        "total_elements": len(elements),
        "elements": elements,
    }


# --------------------------------------------------------------------------------------
# Generador determinista de edificios IFC4 con verdad-terreno (para tests y calibración)
# --------------------------------------------------------------------------------------
NAMES_TRAIN_TEMPLATE = {
    "muro_hormigon": ["MURO HA {e}", "Muro hormigón armado e={e}cm", "Wall - Concrete {e}"],
    "tabique": ["Tabique ladrillo {e}", "Partition brick {e}", "TABIQUE LHD {e}cm"],
    "losa": ["Forjado unidireccional {e}", "Losa maciza {e}cm", "Slab - Concrete {e}"],
    "pilar": ["Pilar HA {e}x{e}", "Column concrete {e}", "PILAR_{e}"],
    "viga": ["Viga canto {e}", "Beam concrete {e}", "VIGA_{e}"],
    "zapata": ["Zapata aislada {e}", "Footing {e}", "ZAPATA_{e}"],
}

NAMES_TEST_TEMPLATE = {
    "muro_hormigon": ["Pantalla de contención {e}", "RC wall {e} mm", "Muro estructural {e}"],
    "tabique": ["Trasdosado fábrica {e}", "Interior wall masonry {e}", "Separación vivienda {e}"],
    "losa": ["Placa alveolar {e}", "Floor plate {e}", "Entrevigado {e}"],
    "pilar": ["Soporte vertical {e}", "Post RC {e}", "Pilastra {e}"],
    "viga": ["Dintel {e}", "Girder RC {e}", "Jácena {e}"],
    "zapata": ["Cimiento puntual {e}", "Pad foundation {e}", "Encepado {e}"],
}

IFC_CLASS_BY_TIP = {
    "muro_hormigon": "IfcWall",
    "tabique": "IfcWall",
    "losa": "IfcSlab",
    "pilar": "IfcColumn",
    "viga": "IfcBeam",
    "zapata": "IfcFooting",
}

MATERIAL_BY_TIP = {
    "muro_hormigon": "Hormigón HA-30/B/20/IIa",
    "tabique": "Ladrillo hueco doble",
    "losa": "Hormigón HA-25/B/20/I",
    "pilar": "Hormigón HA-30/B/20/IIa",
    "viga": "Hormigón HA-25/B/20/I",
    "zapata": "Hormigón HA-25/B/20/I",
}


def construir_modelo_ifc_sintetico(
    path: str,
    n_storeys: int = 2,
    seed: int = 7,
    defect_rate: float = 1.0,
    template_set: str = "mixed",
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """Construye un edificio IFC4 real con representaciones sólidas 3D y verdad-terreno.

    Inyecta de forma controlada los 6 defectos habituales en modelos BIM de licitación:
    `sin_qto`, `qto_miente`, `sin_material`, `proxy_ambiguo`, `outlier_geometrico` y `duplicado`.
    """
    import ifcopenshell.api as api
    import ifcopenshell.util.placement as uplace
    import ifcopenshell.util.unit as uunit

    rng = random.Random(seed)
    f = api.run("project.create_file", version="IFC4")
    proj = api.run("root.create_entity", f, ifc_class="IfcProject", name="Edificio Certificacion BIM")
    api.run("unit.assign_unit", f)
    model_ctx = api.run("context.add_context", f, context_type="Model")
    body = api.run(
        "context.add_context",
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_ctx,
    )
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Parcela")
    bld = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Bloque Principal")
    api.run("aggregate.assign_object", f, products=[site], relating_object=proj)
    api.run("aggregate.assign_object", f, products=[bld], relating_object=site)

    usc = float(uunit.calculate_unit_scale(f))
    materials = {k: api.run("material.add_material", f, name=v) for k, v in MATERIAL_BY_TIP.items()}
    truth: Dict[str, Dict[str, Any]] = {}
    counters = {"i": 0}

    def next_name(tip: str, e_val: int, held_out: bool) -> str:
        pool = (NAMES_TEST_TEMPLATE if held_out else NAMES_TRAIN_TEMPLATE)[tip]
        return rng.choice(pool).format(e=e_val)

    def add_element(
        storey: Any,
        tip: str,
        dims: Tuple[float, float, float],
        xy: Tuple[float, float],
        z: float,
        e_label: int,
        held_out: bool = False,
        force: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, float]:
        counters["i"] += 1
        force = force or {}
        ifc_class = IFC_CLASS_BY_TIP[tip]
        as_proxy = bool(force.get("proxy", False))
        el = api.run(
            "root.create_entity",
            f,
            ifc_class="IfcBuildingElementProxy" if as_proxy else ifc_class,
            name=force.get("name") or next_name(tip, e_label, held_out),
        )
        m = np.eye(4)
        m[0, 3], m[1, 3], m[2, 3] = xy[0], xy[1], z
        api.run("geometry.edit_object_placement", f, product=el, matrix=m)

        if tip in ("muro_hormigon", "tabique"):
            length, height, thick = dims
            rep = api.run(
                "geometry.add_wall_representation",
                f,
                context=body,
                length=length,
                height=height,
                thickness=thick,
            )
            vol = length * height * thick
            area = length * height
        elif tip == "losa":
            a_len, b_len, depth = dims
            rep = api.run(
                "geometry.add_slab_representation",
                f,
                context=body,
                depth=depth,
                polyline=[(0, 0), (a_len, 0), (a_len, b_len), (0, b_len), (0, 0)],
            )
            vol = a_len * b_len * depth
            area = a_len * b_len
        else:
            b_sec, h_sec, depth = dims
            if tip == "viga":
                prof = f.create_entity(
                    "IfcRectangleProfileDef",
                    ProfileType="AREA",
                    XDim=h_sec / usc,
                    YDim=b_sec / usc,
                )
                m[:3, :3] = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float)
                api.run("geometry.edit_object_placement", f, product=el, matrix=m)
            else:
                prof = f.create_entity(
                    "IfcRectangleProfileDef",
                    ProfileType="AREA",
                    XDim=b_sec / usc,
                    YDim=h_sec / usc,
                )
            rep = api.run("geometry.add_profile_representation", f, context=body, profile=prof, depth=depth)
            vol = b_sec * h_sec * depth
            area = b_sec * h_sec

        api.run("geometry.assign_representation", f, product=el, representation=rep)
        api.run("spatial.assign_container", f, products=[el], relating_structure=storey)
        if not force.get("no_material"):
            api.run("material.assign_material", f, products=[el], material=materials[tip])

        bucket = (
            int(round(dims[2] * 100))
            if tip in ("losa", "muro_hormigon", "tabique")
            else int(round(dims[0] * 100))
            if tip == "pilar"
            else int(round(dims[1] * 100))
            if tip == "viga"
            else 180
        )
        rec: Dict[str, Any] = {
            "tip": tip,
            "vol": vol,
            "area": area,
            "bucket": bucket,
            "ifc_class": el.is_a(),
            "storey": storey.Name,
            "defects": [],
            "held_out_name": held_out,
            "name": el.Name,
        }
        if force.get("no_qto"):
            rec["defects"].append("sin_qto")
        else:
            qto_factor = float(force.get("qto_factor", 1.0))
            declared = vol * qto_factor
            if "qto_factor" in force:
                rec["defects"].append("qto_miente")
            qset = api.run("pset.add_qto", f, product=el, name=f"Qto_{ifc_class[3:]}BaseQuantities")
            props = {"NetVolume": float(declared)}
            if tip in ("muro_hormigon", "tabique"):
                props["NetSideArea"] = float(area * qto_factor)
            api.run("pset.edit_qto", f, qto=qset, properties=props)

        if force.get("no_material"):
            rec["defects"].append("sin_material")
        if as_proxy:
            rec["defects"].append("proxy_ambiguo")
        if force.get("outlier"):
            rec["defects"].append("outlier_geometrico")
        truth[el.GlobalId] = rec
        return el, vol

    def pick_force() -> Dict[str, Any]:
        force: Dict[str, Any] = {}
        if rng.random() < 0.08 * defect_rate:
            force["no_qto"] = True
        elif rng.random() < 0.07 * defect_rate:
            force["qto_factor"] = rng.choice([0.55, 0.70, 1.30, 1.45])
        if rng.random() < 0.05 * defect_rate:
            force["no_material"] = True
        if rng.random() < 0.04 * defect_rate:
            force["proxy"] = True
            force["name"] = rng.choice([
                "Elemento genérico {:03d}",
                "Objeto importado {:03d}",
                "Generic Model {:03d}",
                "Sin nombre {:03d}",
            ]).format(counters["i"])
        return force

    for s in range(n_storeys):
        storey = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=f"Planta {s}")
        storey.Elevation = 3.2 * s
        api.run("aggregate.assign_object", f, products=[storey], relating_object=bld)
        z = 3.2 * s
        is_held = lambda: template_set == "test" or (template_set == "mixed" and rng.random() < 0.35)  # noqa: E731

        thick = rng.choice([0.25, 0.30, 0.35])
        add_element(storey, "losa", (24.0, 16.0, thick), (0.0, 0.0), z, int(thick * 100), is_held(), pick_force())

        for ix in range(5):
            for iy in range(4):
                sec = rng.choice([0.30, 0.35, 0.40])
                add_element(
                    storey,
                    "pilar",
                    (sec, sec, 3.0),
                    (ix * 6.0, iy * 5.0),
                    z,
                    int(sec * 100),
                    is_held(),
                    pick_force(),
                )

        for ix in range(4):
            for iy in range(4):
                h_beam = rng.choice([0.40, 0.45, 0.50])
                add_element(
                    storey,
                    "viga",
                    (0.30, h_beam, 6.0),
                    (ix * 6.0, iy * 5.0),
                    z + 2.7,
                    int(h_beam * 100),
                    is_held(),
                    pick_force(),
                )

        for k in range(6):
            t_wall = rng.choice([0.20, 0.25, 0.30])
            add_element(
                storey,
                "muro_hormigon",
                (rng.choice([4.0, 5.0, 6.0]), 3.0, t_wall),
                (k * 3.0, 20.0),
                z,
                int(t_wall * 100),
                is_held(),
                pick_force(),
            )

        for k in range(10):
            t_part = rng.choice([0.07, 0.10, 0.12])
            add_element(
                storey,
                "tabique",
                (rng.choice([2.5, 3.0, 4.0]), 2.7, t_part),
                (k * 2.0, 22.0),
                z,
                int(t_part * 100),
                is_held(),
                pick_force(),
            )

        if s == 0:
            for ix in range(5):
                for iy in range(4):
                    add_element(
                        storey,
                        "zapata",
                        (1.8, 1.8, 0.8),
                        (ix * 6.0, iy * 5.0),
                        -0.8,
                        180,
                        is_held(),
                        pick_force(),
                    )

    if defect_rate > 0:
        st0 = f.by_type("IfcBuildingStorey")[0]
        add_element(
            st0,
            "muro_hormigon",
            (4.0, 3.0, 1.8),
            (30.0, 0.0),
            0.0,
            180,
            False,
            {"outlier": True, "name": "MURO HA 18"},
        )
        pilares = [e for e in f.by_type("IfcColumn") if e.GlobalId in truth]
        if len(pilares) > 3:
            src = pilares[3]
            src_t = truth[src.GlobalId]
            sec_src = src_t["bucket"] / 100.0
            dup, _ = add_element(
                st0,
                "pilar",
                (sec_src, sec_src, 3.0),
                (0.0, 0.0),
                0.0,
                src_t["bucket"],
                False,
                {"name": src.Name},
            )
            api.run(
                "geometry.edit_object_placement",
                f,
                product=dup,
                matrix=uplace.get_local_placement(src.ObjectPlacement),
                is_si=False,
            )
            truth[dup.GlobalId]["defects"].append("duplicado")
            truth[dup.GlobalId]["dup_of"] = src.GlobalId

    f.write(path)
    return path, truth
