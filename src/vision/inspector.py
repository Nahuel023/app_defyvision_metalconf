"""
Wrapper sobre el pipeline de inspección para uso desde el controlador.

Añade:
  - Cache de tolerancias, patrón y ROI por (model, scanner_id) — elimina
    las lecturas de disco en cada frame.
  - Estado EMA del ángulo de rotación — suaviza estimaciones ruidosas de Hough.
  - invalidate(): fuerza recarga del cache cuando el modelo cambia.
"""

import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from src.inspection import InspectionResult, inspect_frame
from src.io.load_images import load_bgr_image
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi
from src.pipeline.machine_stop import MachineStopDetector
from src.utils.config import load_tolerances

logger = logging.getLogger(__name__)


class InspectionSession:
    """Shared stateful session for live and folder analysis."""

    def __init__(
        self,
        model: str,
        scanner_id: str | None = None,
        save: bool = False,
        movement_threshold: float = 0.0,
        min_interval_sec: float = 0.0,
        resource_owner: "Inspector | None" = None,
    ) -> None:
        self._model = model
        self._scanner_id = scanner_id
        self._save = save
        self._movement_threshold = max(0.0, float(movement_threshold))
        self._min_interval_sec = max(0.0, float(min_interval_sec))
        self._frame_counter = 0
        self._last_gray: np.ndarray | None = None
        self._last_insp_time = 0.0
        self.last_position_diff = 0.0

        if resource_owner is not None:
            self._preloaded = {
                "tolerances": resource_owner._get_tols(model, scanner_id),
                "pattern": resource_owner._get_pattern(model, scanner_id),
                "roi": resource_owner._get_roi(model, scanner_id),
                "saved_roi": resource_owner._get_roi(model, scanner_id),
                "ema_state": resource_owner._get_ema(scanner_id),
                "machine_stop_detector": resource_owner._get_detector(model, scanner_id),
                "desalign_state": {"streak": 0, "reason": ""},
                "roi_runtime_state": {},
            }
        else:
            tols = load_tolerances(model, scanner_id=scanner_id)
            _roi = load_roi(model, scanner_id)
            self._preloaded = {
                "tolerances": tols,
                "pattern": load_pattern(find_pattern_path(model, scanner_id)),
                "roi": _roi,
                "saved_roi": _roi,
                "ema_state": {},
                "desalign_state": {"streak": 0, "reason": ""},
                "roi_runtime_state": {},
            }
            if bool(tols.get("machine_stop_enabled", False)):
                self._preloaded["machine_stop_detector"] = MachineStopDetector(
                    enabled=True,
                    missing_frames=int(tols.get("machine_stop_missing_frames", 5)),
                    min_missing=int(tols.get("machine_stop_min_missing", 1)),
                    same_zone_px=float(tols.get("machine_stop_same_zone_px", 35.0)),
                    ignore_near_miss=bool(tols.get("machine_stop_ignore_near_miss", True)),
                    track_by_grid=bool(tols.get("machine_stop_track_by_grid", True)),
                    same_column_tol_cells=int(tols.get("machine_stop_same_column_tol_cells", 0)),
                )

    def inspect_frame(
        self,
        frame: np.ndarray,
        frame_id: str | None = None,
        force: bool = False,
    ) -> Optional[InspectionResult]:
        now = time.monotonic()
        if not force and self._min_interval_sec > 0.0:
            if (now - self._last_insp_time) < self._min_interval_sec:
                return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._last_gray is not None and not force:
            diff = float(np.mean(cv2.absdiff(gray, self._last_gray)))
            self.last_position_diff = diff
            if self._movement_threshold > 0.0 and diff < self._movement_threshold:
                return None
        else:
            self.last_position_diff = 0.0

        self._frame_counter += 1
        if frame_id is None:
            frame_id = f"{self._scanner_id or 'session'}_{self._frame_counter:04d}"

        result = inspect_frame(
            self._model,
            frame,
            frame_id=frame_id,
            save=self._save,
            scanner_id=self._scanner_id,
            _preloaded=self._preloaded,
        )
        self._last_gray = gray
        self._last_insp_time = time.monotonic()
        return result

    def inspect_path(
        self,
        image_path: str | Path,
        force: bool = False,
    ) -> Optional[InspectionResult]:
        path = Path(image_path)
        frame = load_bgr_image(path)
        return self.inspect_frame(frame, frame_id=path.stem, force=force)


class Inspector:
    def __init__(self) -> None:
        self._tols:      dict[tuple, dict]         = {}   # (model, scanner_id) → tolerances dict
        self._pattern:   dict[tuple, object]       = {}   # (model, scanner_id) → Pattern
        self._roi:       dict[tuple, object]       = {}   # (model, scanner_id) → ROI | None
        self._ema:       dict[str | None, dict]    = {}   # scanner_id → {'angle': float}
        self._detectors: dict[tuple, MachineStopDetector] = {}  # (model, scanner_id) → detector

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
            session = InspectionSession(
                model,
                scanner_id=scanner_id,
                save=save,
                resource_owner=self,
            )
            return session.inspect_frame(frame, frame_id=frame_id, force=True)
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
            self._detectors.clear()
        else:
            for k in [k for k in self._tols if k[0] == model]:
                del self._tols[k]
            for k in [k for k in self._pattern if k[0] == model]:
                del self._pattern[k]
            for k in [k for k in self._roi if k[0] == model]:
                del self._roi[k]
            for k in [k for k in self._detectors if k[0] == model]:
                del self._detectors[k]
        # Resetear EMA del scanner afectado para no arrastrar ángulos obsoletos
        if scanner_id is not None:
            self._ema.pop(scanner_id, None)
        else:
            self._ema.clear()

    # ------------------------------------------------------------------
    # Cache interno
    # ------------------------------------------------------------------

    def _get_tols(self, model: str, scanner_id: str | None) -> dict:
        key = (model, scanner_id)
        if key not in self._tols:
            self._tols[key] = load_tolerances(model, scanner_id=scanner_id)
        return self._tols[key]

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

    def _get_detector(self, model: str, scanner_id: str | None) -> MachineStopDetector:
        key = (model, scanner_id)
        if key not in self._detectors:
            tols = self._get_tols(model, scanner_id)
            self._detectors[key] = MachineStopDetector(
                enabled=bool(tols.get("machine_stop_enabled", False)),
                missing_frames=int(tols.get("machine_stop_missing_frames", 5)),
                min_missing=int(tols.get("machine_stop_min_missing", 1)),
                same_zone_px=float(tols.get("machine_stop_same_zone_px", 35.0)),
                ignore_near_miss=bool(tols.get("machine_stop_ignore_near_miss", True)),
                track_by_grid=bool(tols.get("machine_stop_track_by_grid", True)),
                same_column_tol_cells=int(tols.get("machine_stop_same_column_tol_cells", 0)),
            )
        return self._detectors[key]
