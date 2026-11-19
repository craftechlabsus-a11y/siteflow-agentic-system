"""Catálogo determinista de rendimientos y factores para Obra Civil y Movimiento de Tierras (v2.5.0).

Fuente de ingeniería determinista:
  - Rendimientos medios por unidad de obra y cuadrilla/equipo tipo.
  - Coeficientes geotécnicos de esponjamiento, compactación, sobreancho y talud.
  - Tiempos mínimos de curado/fraguado (lags PDM) entre hormigonado y trasdós/alzado.
"""
from typing import Any, Dict

FACTORES_TERRENO: Dict[str, Dict[str, float]] = {
    "tierra": {
        "esponjamiento": 1.25,
        "compactacion": 0.92,
        "sobreancho_m": 0.60,
        "talud_h_v": 0.50,          # 1H:2V
        "factor_rendimiento_exc": 1.00,
    },
    "transito": {
        "esponjamiento": 1.30,
        "compactacion": 0.94,
        "sobreancho_m": 0.60,
        "talud_h_v": 0.35,
        "factor_rendimiento_exc": 0.55,
    },
    "roca": {
        "esponjamiento": 1.38,
        "compactacion": 0.95,
        "sobreancho_m": 0.50,
        "talud_h_v": 0.15,
        "factor_rendimiento_exc": 0.22,
    },
}

# Rendimiento base por 1 frente/cuadrilla en jornada de 8h
RENDIMIENTOS_OBRA_CIVIL: Dict[str, Dict[str, Any]] = {
    "desbroce_y_replanteo": {"unidad": "m2", "rendimiento_dia": 3000.0, "equipo": "Topografía + Bulldozer", "lag_dias": 0, "operarios": 3, "maquinas": 1},
    "excavacion_tierras": {"unidad": "m3", "rendimiento_dia": 420.0, "equipo": "Retroexcavadora 25t + Dumper", "lag_dias": 0, "operarios": 4, "maquinas": 3},
    "entibacion_y_agotamiento": {"unidad": "m2", "rendimiento_dia": 35.0, "equipo": "Cuadrilla entibación + Wellpoint", "lag_dias": 0, "operarios": 5, "maquinas": 2},
    "hormigon_limpieza": {"unidad": "m3", "rendimiento_dia": 80.0, "equipo": "Camión bomba + 4 oficiales", "lag_dias": 1, "operarios": 4, "maquinas": 1},
    "cimentacion_zapatas_losas": {"unidad": "m3", "rendimiento_dia": 55.0, "equipo": "Ferralla + Encofrado + Bomba", "lag_dias": 3, "operarios": 8, "maquinas": 2},
    "muros_y_estructuras_hidraulicas": {"unidad": "m3", "rendimiento_dia": 24.0, "equipo": "Encofrado trepante/estanco + Grúa", "lag_dias": 5, "operarios": 10, "maquinas": 2},
    "canales_y_arquetas": {"unidad": "m3", "rendimiento_dia": 30.0, "equipo": "Cuadrilla obra civil lineal", "lag_dias": 3, "operarios": 6, "maquinas": 2},
    "pruebas_estanqueidad": {"unidad": "ud", "rendimiento_dia": 0.25, "equipo": "Instrumentación + Llenado EN 12255", "lag_dias": 0, "operarios": 3, "maquinas": 1},
    "relleno_y_compactacion": {"unidad": "m3", "rendimiento_dia": 320.0, "equipo": "Compactador + Tongadas 30cm", "lag_dias": 0, "operarios": 4, "maquinas": 2},
}

RENDIMIENTOS_ESPECIALIDADES: Dict[str, Dict[str, Any]] = {
    **RENDIMIENTOS_OBRA_CIVIL,
    "tendido_tuberia_dn_pequeno": {"unidad": "ml", "rendimiento_dia": 48.0, "equipo": "Cuadrilla fontanería + Miniexcavadora", "lag_dias": 0, "operarios": 4, "maquinas": 1},
    "tendido_tuberia_dn_grande": {"unidad": "ml", "rendimiento_dia": 18.0, "equipo": "Grúa + Soldadura/Bridas + Nivel láser", "lag_dias": 0, "operarios": 6, "maquinas": 2},
    "montaje_bombeo_y_valvuleria": {"unidad": "ud", "rendimiento_dia": 1.5, "equipo": "Montador mecánico + Polipasto", "lag_dias": 0, "operarios": 4, "maquinas": 1},
    "prueba_presion_hidrostatica": {"unidad": "ml", "rendimiento_dia": 250.0, "equipo": "Bomba presión + Manómetro certificado", "lag_dias": 1, "operarios": 3, "maquinas": 1},
    "canalizacion_y_bandejas_bt_mt": {"unidad": "ml", "rendimiento_dia": 90.0, "equipo": "Oficiales electricistas", "lag_dias": 0, "operarios": 4, "maquinas": 0},
    "cuadros_y_scada": {"unidad": "ud", "rendimiento_dia": 0.5, "equipo": "Instrumentación y Control", "lag_dias": 0, "operarios": 3, "maquinas": 0},
    "comisionado_fat": {"unidad": "ud", "rendimiento_dia": 0.5, "equipo": "Ingeniería + Fabricante", "lag_dias": 0, "operarios": 3, "maquinas": 0},
    "comisionado_sat_puesta_marcha": {"unidad": "ud", "rendimiento_dia": 0.2, "equipo": "Equipo puesta en marcha integral", "lag_dias": 0, "operarios": 5, "maquinas": 0},
}


def estimate_duration_days(
    activity_key: str,
    quantity: float,
    *,
    crews: int = 1,
    min_days: float = 1.0,
) -> float:
    """Calcula la duración determinista (días) = max(min_days, cantidad / (rendimiento_dia * frentes))."""
    spec = RENDIMIENTOS_ESPECIALIDADES.get(activity_key)
    if not spec:
        raise KeyError(f"Rendimiento no definido para '{activity_key}'")
    rate = float(spec["rendimiento_dia"]) * max(1, int(crews))
    return round(max(min_days, float(quantity) / rate), 2)
