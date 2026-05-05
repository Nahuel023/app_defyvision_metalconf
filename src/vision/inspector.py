"""
Wrapper fino sobre el pipeline de inspección para uso desde el controlador.
Expone un único método: inspect(model, frame) → InspectionResult
"""

import logging
from typing import Optional

import numpy as np

from src.inspection import InspectionResult, inspect_frame

logger = logging.getLogger(__name__)


class Inspector:
    def inspect(
        self,
        model: str,
        frame: np.ndarray,
        frame_id: str = "live",
        save: bool = False,
    ) -> Optional[InspectionResult]:
        """
        Ejecuta la inspección sobre un frame BGR.
        Devuelve None si el modelo no existe o el pipeline falla.
        """
        try:
            return inspect_frame(model, frame, frame_id=frame_id, save=save)
        except FileNotFoundError:
            logger.error(f"Modelo '{model}' no encontrado en data/patterns/")
            return None
        except Exception as exc:
            logger.error(f"Error en inspección ({model}): {exc}")
            return None
