"""Skill CAD — Takeoff geométrico EXACTO desde DWG/DXF.

Extrae cantidades deterministas de la geometría del plano (longitudes, áreas y conteos
por capa/bloque), convertidas a metros. Es exacto (no estimado por visión) → alta
confianza y fuente trazable 'cad_geometria'. Alimenta el takeoff/presupuesto (P3).
"""
import os
import tempfile
import logging

from api.skills.base import register_skill, SkillResult
from api.schemas.skills_cad import CadTakeoffInputs

logger = logging.getLogger("api.skills.cad.takeoff")


@register_skill(
    "cad_takeoff_geometrico",
    cerebro="vision", familia="takeoff",
    descripcion="Takeoff EXACTO desde un plano CAD (DWG/DXF): longitudes, áreas y conteos "
                "por capa y por bloque, en metros. Cantidades deterministas, no estimadas.",
    tipos_proyecto=["*"],
    capacidad="cad",
    inputs_model=CadTakeoffInputs,
    outputs_model=None,
    version="1.0",
)
def cad_takeoff(params: CadTakeoffInputs, *, db=None, ctx=None) -> SkillResult:
    ctx = ctx or {}
    file_bytes = ctx.get("file_bytes")
    filename = params.filename or ctx.get("filename") or "plano.dxf"
    if not file_bytes:
        return SkillResult(datos={"error": "No se recibió archivo CAD (DWG/DXF)"},
                           requiere_revision=True,
                           fuente={"tipo": "skill", "skill": "cad_takeoff_geometrico"})

    suffix = os.path.splitext(filename)[1].lower() or ".dxf"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        from api.cad.converter import to_dxf
        from api.cad.parser import parse_dxf

        dxf_path = to_dxf(tmp_path)
        if dxf_path is None:
            return SkillResult(
                datos={"error": "No se pudo leer/convertir el CAD. Si es DWG, falta el conversor "
                                "(ODA File Converter / dwg2dxf) en el servidor. El DXF sí funciona."},
                requiere_revision=True,
                fuente={"tipo": "skill", "skill": "cad_takeoff_geometrico"})

        geo = parse_dxf(dxf_path)
        geo["desglose_mediciones"] = generar_desglose_mediciones_cad(
            geo,
            espesores_m=getattr(params, "espesores_m", None),
            factores_correccion=getattr(params, "factores_correccion", None),
        )
        requiere = not geo.get("unidades_especificadas", False)  # sin unidades → revisar
        return SkillResult(
            datos=geo,
            confianza=0.95 if not requiere else 0.6,
            requiere_revision=requiere,
            fuente={"tipo": "cad_geometria", "skill": "cad_takeoff_geometrico",
                    "exacto": True, "formato": suffix.lstrip(".")},
        )
    except Exception as e:
        logger.warning(f"cad_takeoff falló: {e}")
        return SkillResult(datos={"error": f"No es un CAD legible: {e}"}, requiere_revision=True,
                           fuente={"tipo": "skill", "skill": "cad_takeoff_geometrico"})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def generar_desglose_mediciones_cad(
    geo: dict,
    espesores_m: dict = None,
    factores_correccion: dict = None,
) -> list:
    """Transforma la geometría CAD extraída en líneas analíticas de medición (N x L x A x H).

    Soporta las 6 disciplinas maestras de ingeniería y arquitectura con unidades
    pertinentes (m3, m2, m, ud) y trazabilidad completa.
    """
    from api.domain.mapeo_capas import (
        clasificar_capa_multidisciplinar, clasificar_capa, normalizar_capa,
        espesor_para, factor_correccion_para, TIPOLOGIAS_SIN_VOLUMEN
    )
    mediciones = []

    capas = geo.get("por_capa")
    if not isinstance(capas, dict):
        capas = geo.get("capas") if isinstance(geo.get("capas"), dict) else {}
    for capa, datos in (capas or {}).items():
        if not isinstance(datos, dict):
            continue
        disc, tipologia, unidad_def, motivo = clasificar_capa_multidisciplinar(capa)
        if not disc or motivo != "match":
            # Fallback a clasificador estructural clásico
            tipologia_est, motivo_est = clasificar_capa(capa)
            if not tipologia_est:
                continue
            disc = "ESTRUCTURAS"
            tipologia = tipologia_est
            unidad_def = "m2" if tipologia in TIPOLOGIAS_SIN_VOLUMEN else "m3"

        factor = factor_correccion_para(tipologia, factores_correccion)
        area_m2 = float(datos.get("area_m2", 0.0)) * factor
        longitud_m = float(datos.get("longitud_m", 0.0)) * factor
        canto, es_supuesto = espesor_para(tipologia, espesores_m)
        canto = float(canto or 0.0)

        factor_tag = f" [factor corrección {factor}]" if factor != 1.0 else ""
        supuesto_tag = " (predimensionado supuesto)" if es_supuesto else ""

        # Clasificación por disciplina y unidad
        if disc == "ESTRUCTURAS":
            if tipologia in TIPOLOGIAS_SIN_VOLUMEN:
                total_m2 = area_m2 if area_m2 > 0 else longitud_m
                mediciones.append({
                    "sub_zona": normalizar_capa(capa),
                    "descripcion_detalle": f"Superficie vectorial CAD en capa '{capa}' ({tipologia}){factor_tag}",
                    "unidades_iguales": 1.0,
                    "longitud": round(total_m2, 3),
                    "latitud_ancho": 1.0,
                    "altura_espesor": 1.0,
                    "total_parcial": round(total_m2, 3),
                    "origen": "cad_takeoff",
                    "entidad_cad_id": capa,
                    "tipologia": tipologia,
                    "disciplina": disc,
                    "unidad": "m2",
                    "factor_correccion": factor,
                    "canto_es_supuesto": False,
                })
            elif tipologia == "columnas":
                total_m3 = area_m2 * canto
                mediciones.append({
                    "sub_zona": normalizar_capa(capa),
                    "descripcion_detalle": f"Pilares: sección en planta {round(area_m2, 2)} m² x altura {canto} m ('{capa}'){factor_tag}{supuesto_tag}",
                    "unidades_iguales": 1.0,
                    "longitud": round(area_m2, 3),
                    "latitud_ancho": 1.0,
                    "altura_espesor": round(canto, 3),
                    "total_parcial": round(total_m3, 3),
                    "origen": "cad_takeoff",
                    "entidad_cad_id": capa,
                    "tipologia": tipologia,
                    "disciplina": disc,
                    "unidad": "m3",
                    "factor_correccion": factor,
                    "canto_es_supuesto": es_supuesto,
                })
            elif tipologia == "vigas":
                if area_m2 > 0:
                    total_m3 = area_m2 * canto
                    mediciones.append({
                        "sub_zona": normalizar_capa(capa),
                        "descripcion_detalle": f"Vigas: área {round(area_m2, 2)} m² x canto {canto} m ('{capa}'){factor_tag}{supuesto_tag}",
                        "unidades_iguales": 1.0,
                        "longitud": round(area_m2, 3),
                        "latitud_ancho": 1.0,
                        "altura_espesor": round(canto, 3),
                        "total_parcial": round(total_m3, 3),
                        "origen": "cad_takeoff",
                        "entidad_cad_id": capa,
                        "tipologia": tipologia,
                        "disciplina": disc,
                        "unidad": "m3",
                        "factor_correccion": factor,
                        "canto_es_supuesto": es_supuesto,
                    })
                elif longitud_m > 0:
                    ancho_est = 0.40
                    total_m3 = longitud_m * ancho_est * canto
                    mediciones.append({
                        "sub_zona": normalizar_capa(capa),
                        "descripcion_detalle": f"Vigas: long {round(longitud_m, 2)} m x ancho {ancho_est} m x canto {canto} m ('{capa}'){factor_tag}{supuesto_tag}",
                        "unidades_iguales": 1.0,
                        "longitud": round(longitud_m, 3),
                        "latitud_ancho": round(ancho_est, 3),
                        "altura_espesor": round(canto, 3),
                        "total_parcial": round(total_m3, 3),
                        "origen": "cad_takeoff",
                        "entidad_cad_id": capa,
                        "tipologia": tipologia,
                        "disciplina": disc,
                        "unidad": "m3",
                        "factor_correccion": factor,
                        "canto_es_supuesto": es_supuesto,
                    })
            else:
                if area_m2 > 0:
                    total_m3 = area_m2 * canto
                    mediciones.append({
                        "sub_zona": normalizar_capa(capa),
                        "descripcion_detalle": f"{tipologia.capitalize()}: área {round(area_m2, 2)} m² x espesor {canto} m ('{capa}'){factor_tag}{supuesto_tag}",
                        "unidades_iguales": 1.0,
                        "longitud": round(area_m2, 3),
                        "latitud_ancho": 1.0,
                        "altura_espesor": round(canto, 3),
                        "total_parcial": round(total_m3, 3),
                        "origen": "cad_takeoff",
                        "entidad_cad_id": capa,
                        "tipologia": tipologia,
                        "disciplina": disc,
                        "unidad": "m3",
                        "factor_correccion": factor,
                        "canto_es_supuesto": es_supuesto,
                    })
        elif unidad_def == "m":
            val = longitud_m if longitud_m > 0 else area_m2
            mediciones.append({
                "sub_zona": normalizar_capa(capa),
                "descripcion_detalle": f"{disc}: Trazado lineal en capa '{capa}' ({tipologia})",
                "unidades_iguales": 1.0,
                "longitud": round(val, 3),
                "latitud_ancho": 1.0,
                "altura_espesor": 1.0,
                "total_parcial": round(val, 3),
                "origen": "cad_takeoff",
                "entidad_cad_id": capa,
                "tipologia": tipologia,
                "disciplina": disc,
                "unidad": "m",
                "factor_correccion": factor,
                "canto_es_supuesto": False,
            })
        elif unidad_def == "m2":
            val = area_m2 if area_m2 > 0 else longitud_m
            mediciones.append({
                "sub_zona": normalizar_capa(capa),
                "descripcion_detalle": f"{disc}: Superficie en capa '{capa}' ({tipologia})",
                "unidades_iguales": 1.0,
                "longitud": round(val, 3),
                "latitud_ancho": 1.0,
                "altura_espesor": 1.0,
                "total_parcial": round(val, 3),
                "origen": "cad_takeoff",
                "entidad_cad_id": capa,
                "tipologia": tipologia,
                "disciplina": disc,
                "unidad": "m2",
                "factor_correccion": factor,
                "canto_es_supuesto": False,
            })
        else:  # ud
            mediciones.append({
                "sub_zona": normalizar_capa(capa),
                "descripcion_detalle": f"{disc}: Elemento discreto en capa '{capa}' ({tipologia})",
                "unidades_iguales": 1.0,
                "longitud": 1.0,
                "latitud_ancho": 1.0,
                "altura_espesor": 1.0,
                "total_parcial": 1.0,
                "origen": "cad_takeoff",
                "entidad_cad_id": capa,
                "tipologia": tipologia,
                "disciplina": disc,
                "unidad": "ud",
                "factor_correccion": 1.0,
                "canto_es_supuesto": False,
            })

    return mediciones
