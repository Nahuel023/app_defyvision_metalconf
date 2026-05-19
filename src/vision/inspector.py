"""
Wrapper sobre el pipeline de inspección para uso desde el controlador.

Añade:
  - Cache de tolerancias, patrón y ROI por (model, scanner_id) — elimina
    las lecturas de disco en cada frame.
  - Estado EMA del ángulo de rotación — suaviza estimaciones ruidosas de Hough.
  - invalidate(): fuerza recarga del cache cuando el modelo cambia.
"""

import logging
from typing import Optional

import numpy as np

from src.inspection import InspectionResult, inspect_frame
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi
from src.utils.config import load_tolerances

logger = logging.getLogger(__name__)


class Inspector:
    def __init__(self) -> None:
        self._tols:    dict[str, dict]   = {}   # model → tolerances dict
        self._pattern: dict[tuple, object] = {}  # (model, scanner_id) → Pattern
        self._roi:     dict[tuple, object] = {}  # (model, scanner_id) → ROI | None
        self._ema:     dict[str | None, dict] = {}  # scanner_id → {'angle': float}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def inspect(
        self,
        model: str,
        frame: np.ndarray,
        frame_id: str = "live",
        save: bool = False,
        scanner_id: str | None = None,
    ) -> Optional[InspectionResult]:
        """
        Ejecuta la inspección sobre un frame BGR.
        Devuelve None si el modelo no existe o el pipeline falla.
        """
        try:
            preloaded = {
                "tolerances": self._get_tols(model),
                "pattern":    self._get_pattern(model, scanner_id),
                "roi":        self._get_roi(model, scanner_id),
                "ema_state":  self._get_ema(scanner_id),
            }
            return inspect_frame(
                model, frame, frame_id=frame_id, save=save,
                scanner_id=scanner_id, _preloaded=preloaded,
            )
        except FileNotFoundError:
            logger.error(f"Patrón no encontrado: scanner={scanner_id} modelo={model}")
            return None
        except Exception as exc:
            logger.error(f"Error en inspección ({model}): {exc}")
            return None

    def invalidate(
        self,
        model: str | None = None,
        scanner_id: str | None = None,
    ) -> None:
        """Fuerza recarga del cache. Llamar cuando cambia el modelo."""
        if model is None:
            self._tols.clear()
            self._pattern.clear()
            self._roi.clear()
        else:
            self._tols.pop(model, None)
            for k in [k for k in self._pattern if k[0] == model]:
                del self._pattern[k]
            for k in [k for k in self._roi if k[0] == model]:
                del self._roi[k]
        # Resetear EMA del scanner afectado para no arrastrar ángulos obsoletos
        if scanner_id is not None:
            self._ema.pop(scanner_id, None)
        else:
            self._ema.clear()

    # ------------------------------------------------------------------
    # Cache interno
    # ------------------------------------------------------------------

    def _get_tols(self, model: str) -> dict:
        if model not in self._tols:
            self._tols[model] = load_tolerances(model)
        return self._tols[model]

    def _get_pattern(self, model: str, scanner_id: str | None):
        key = (model, scanner_id)
        if key not in self._pattern:
            self._pattern[key] = load_pattern(find_pattern_path(model, scanner_id))
        return self._pattern[key]

    def _get_roi(self, model: str, scanner_id: str | None):
        key = (model, scanner_id)
        if key not in self._roi:
            self._roi[key] = load_roi(model, scanner_id)
        return self._roi[key]

    def _get_ema(self, scanner_id: str | None) -> dict:
        if scanner_id not in self._ema:
            self._ema[scanner_id] = {}
        return self._ema[scanner_id]
