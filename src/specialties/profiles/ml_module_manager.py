"""
Sistema de Módulos por Tipo de Proyecto — SiteFlow Pro
ml_module_manager.py

Motor central que carga los perfiles de cada sector (Edificación, Naves
Industriales, Centros de Datos, Puentes) y los inyecta en toda la cadena
de procesamiento IA: RAG, Chat Assistant, EVM Calculator y Alerting Engine.
"""

import json
import os
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Directorio donde residen los perfiles JSON
_MODULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules")


# ============================================================================
# DATACLASS — Perfil de módulo
# ============================================================================

@dataclass
class ProjectModuleProfile:
    """
    Perfil completo de configuración de un módulo/sector.
    Se carga desde JSON y se inyecta en el resto de módulos ML.
    """
    module_id: str                         # "edificacion" | "naves_industriales" | ...
    name: str                              # Nombre legible
    name_short: str                        # Nombre corto para badges
    icon: str                              # Emoji representativo
    description: str = ""
    color_primary: str = "#2563eb"         # Color de tematización frontend
    color_secondary: str = "#1e3a8a"

    # ── Unidades de medida ──────────────────────────────────────────────
    unit_main: str = "m²"                  # "m²", "MW", "ml tablero"
    unit_secondary: str = ""
    cost_range_min: float = 0.0            # €/unidad mínimo
    cost_range_max: float = 0.0            # €/unidad máximo

    # ── Ratios de capítulos (%) ─────────────────────────────────────────
    cost_ratios: Dict[str, float] = field(default_factory=dict)

    # ── KPIs específicos del módulo ─────────────────────────────────────
    kpi_names: List[str] = field(default_factory=list)
    kpi_descriptions: Dict[str, str] = field(default_factory=dict)
    kpi_units: Dict[str, str] = field(default_factory=dict)

    # ── Umbrales de alertas (sobreescribe los genéricos) ───────────────
    alert_thresholds: Dict[str, float] = field(default_factory=dict)

    # ── Vocabulario para RAG e intent classifier ────────────────────────
    rag_keywords: List[str] = field(default_factory=list)
    intent_keywords: Dict[str, List[str]] = field(default_factory=dict)

    # ── Extensión del system prompt del Chat Assistant ──────────────────
    system_prompt_extension: str = ""

    # ── Normativa de referencia ─────────────────────────────────────────
    normativas: List[str] = field(default_factory=list)
    certificaciones: List[str] = field(default_factory=list)

    # ── Partidas típicas del presupuesto ────────────────────────────────
    budget_chapters: List[Dict[str, Any]] = field(default_factory=list)

    # ── Fases EVM especiales ────────────────────────────────────────────
    evm_phases: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectModuleProfile":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})


# ============================================================================
# MODULE MANAGER
# ============================================================================

class ModuleManager:
    """
    Singleton que gestiona los 4 perfiles de módulo.
    Proporciona métodos de inyección en los distintos subsistemas de IA.
    """

    _BUILTIN_MODULE_IDS = [
        "edificacion",
        "naves_industriales",
        "centros_datos",
        "puentes",
        "canales_tuberias",
        "depuradoras_edar",
        "desaladoras_idam",
    ]

    def __init__(self):
        self._profiles: Dict[str, ProjectModuleProfile] = {}
        self._active_module: Optional[str] = "edificacion"  # default
        self._load_profiles()

    # ── Carga ───────────────────────────────────────────────────────────

    def _load_profiles(self):
        os.makedirs(_MODULES_DIR, exist_ok=True)
        for mid in self._BUILTIN_MODULE_IDS:
            path = os.path.join(_MODULES_DIR, f"{mid}.json")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._profiles[mid] = ProjectModuleProfile.from_dict(data)
                    logger.info(f"✅ Módulo cargado: {mid}")
                except Exception as e:
                    logger.error(f"Error cargando módulo {mid}: {e}")
            else:
                logger.warning(f"⚠️ Perfil no encontrado: {path}")

    # ── Consulta ────────────────────────────────────────────────────────

    def get_profile(self, module_id: str) -> Optional[ProjectModuleProfile]:
        return self._profiles.get(module_id)

    def get_active_profile(self) -> Optional[ProjectModuleProfile]:
        return self._profiles.get(self._active_module)

    def list_profiles(self) -> List[ProjectModuleProfile]:
        return list(self._profiles.values())

    def list_profiles_summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "module_id": p.module_id,
                "name": p.name,
                "name_short": p.name_short,
                "icon": p.icon,
                "description": p.description,
                "color_primary": p.color_primary,
                "unit_main": p.unit_main,
                "cost_range": f"{p.cost_range_min:,.0f} – {p.cost_range_max:,.0f} €/{p.unit_main}",
                "kpi_names": p.kpi_names[:4],
                "normativas": p.normativas[:3],
            }
            for p in self._profiles.values()
        ]

    def set_active_module(self, module_id: str) -> bool:
        if module_id in self._profiles:
            self._active_module = module_id
            logger.info(f"🎯 Módulo activo: {module_id}")
            self._apply_injections(module_id)
            return True
        logger.warning(f"Módulo desconocido: {module_id}")
        return False

    # ── Inyección ───────────────────────────────────────────────────────

    def _apply_injections(self, module_id: str):
        """Propaga el módulo activo a todos los subsistemas."""
        profile = self._profiles[module_id]
        self.inject_into_rag(profile)
        self.inject_into_chat(profile)
        self.inject_into_alerts(profile)

    def inject_into_rag(self, profile: ProjectModuleProfile):
        try:
            from api.ml_rag import get_rag_system
            rag = get_rag_system()
            if hasattr(rag, "set_module_context"):
                rag.set_module_context(profile)
        except Exception as e:
            logger.warning(f"RAG injection skipped: {e}")

    def inject_into_chat(self, profile: ProjectModuleProfile):
        try:
            from api.ml_chat_assistant import get_chat_assistant
            chat = get_chat_assistant()
            if hasattr(chat, "set_module_profile"):
                chat.set_module_profile(profile)
        except Exception as e:
            logger.warning(f"Chat injection skipped: {e}")

    def inject_into_alerts(self, profile: ProjectModuleProfile):
        try:
            from api.ml_alerting import get_alerting_engine
            engine = get_alerting_engine()
            if profile.alert_thresholds and hasattr(engine, "UMBRALES"):
                engine.UMBRALES.update(profile.alert_thresholds)
        except Exception as e:
            logger.warning(f"Alerts injection skipped: {e}")

    def get_evm_config(self, module_id: str = None) -> Dict[str, Any]:
        """Devuelve la configuración EVM específica del módulo."""
        profile = self.get_profile(module_id or self._active_module)
        if not profile:
            return {}
        return {
            "module_id": profile.module_id,
            "alert_thresholds": profile.alert_thresholds,
            "evm_phases": profile.evm_phases,
            "kpi_names": profile.kpi_names,
        }


# ============================================================================
# SINGLETON
# ============================================================================

_manager: Optional[ModuleManager] = None

def get_module_manager() -> ModuleManager:
    global _manager
    if _manager is None:
        _manager = ModuleManager()
    return _manager
