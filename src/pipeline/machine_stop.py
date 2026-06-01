"""
Persistent missing-hole detector for machine-stop logic.

Tracks clusters of consistently-missing holes across consecutive frames.
When a cluster's streak reaches `missing_frames`, triggers a
DETENCION DE MAQUINA signal — indicating a likely broken or clogged punch.

Two tracking modes
------------------
track_by_grid=False (legacy pixel mode):
    Zones are matched by pixel X column within `same_zone_px`.  Works for any
    pattern type but can drift when the sheet advances and the defect moves.

track_by_grid=True (grid identity mode, default):
    Zones are keyed by the canonical column index `ci` of the grid cell.
    When `missing_cells` is provided to update(), each missing hole is
    identified by its (ci, cj) coordinates — the same ci appears in the same
    punch column regardless of the sheet's vertical position.  This makes the
    detector robust to the sheet advancing in Y between frames.
    Falls back to pixel mode automatically if missing_cells is not provided.

Common rules (both modes)
--------------------------
- LOW_QUALITY frames do NOT increment/reset streaks and do NOT report a stop.
- Edge filter: missing points within `same_zone_px` of the top or bottom of
  the ROI are ignored (sheet entering / exiting the camera view).
"""
from __future__ import annotations

from typing import Sequence


class MachineStopDetector:
    def __init__(
        self,
        enabled: bool = False,
        missing_frames: int = 5,
        min_missing: int = 1,
        same_zone_px: float = 35.0,
        ignore_near_miss: bool = True,
        track_by_grid: bool = True,
        same_column_tol_cells: int = 0,
    ) -> None:
        self._enabled               = enabled
        # Faltantes: SIEMPRE requiere persistencia (>=2). Un solo frame con faltantes
        # NUNCA debe parar la maquina (el metal pudo haberse corrido). La verticalidad,
        # en cambio, puede parar con un solo frame (se maneja fuera del detector).
        self._missing_frames        = max(2, missing_frames)
        self._min_missing           = max(1, min_missing)
        self._same_zone_px          = max(1.0, same_zone_px)
        self._ignore_near_miss      = ignore_near_miss
        self._track_by_grid         = track_by_grid
        self._same_column_tol_cells = max(0, same_column_tol_cells)

        # Pixel-mode zones: list of {x, y, streak, count}
        self._zones: list[dict] = []
        # Grid-mode zones: {ci: {streak, count, x, y}}
        self._grid_zones: dict[int, dict] = {}

        self._triggered = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all zone state. Call when starting a new inspection session."""
        self._zones.clear()
        self._grid_zones.clear()
        self._triggered = False

    def update(
        self,
        missing_points: Sequence[tuple[float, float]],
        near_miss_pairs: Sequence,
        frame_quality: str,
        img_h: int,
        missing_cells: Sequence[tuple[int, int]] | None = None,
    ) -> tuple[bool, list[tuple[float, float]]]:
        """Update with this frame's missing holes.

        Args:
            missing_points:  ROI-relative (x, y) of expected holes with no match.
            near_miss_pairs: list of ((exp_x, exp_y), (det_x, det_y)) near misses
                             (accepted for API compatibility, not used in grid mode).
            frame_quality:   "GOOD" | "LOW_QUALITY"
            img_h:           ROI height in pixels (for Y-edge margin).
            missing_cells:   optional (ci, cj) parallel to missing_points.
                             When provided and track_by_grid=True, tracking is done
                             by grid column ci instead of pixel X.

        Returns:
            (triggered, triggered_zone_positions)
            - triggered: True when any zone has streak >= missing_frames.
            - triggered_zone_positions: (x, y) centres of triggered zones.

        LOW_QUALITY frames leave streak state unchanged, but never report a
        stop on that frame. A blurry/unstable image is not evidence enough to
        show DETENCION DE MAQUINA.
        """
        if not self._enabled:
            return False, []

        if self._track_by_grid and missing_cells is not None:
            return self._update_grid(missing_points, missing_cells, frame_quality, img_h)
        return self._update_pixel(missing_points, near_miss_pairs, frame_quality, img_h)

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    @property
    def triggered_columns(self) -> list[int]:
        """Return sorted ci values of triggered grid zones (grid mode only)."""
        if not self._track_by_grid:
            return []
        return sorted(
            ci for ci, z in self._grid_zones.items()
            if z["streak"] >= self._missing_frames
        )

    # ------------------------------------------------------------------
    # Grid-identity tracking
    # ------------------------------------------------------------------

    def _update_grid(
        self,
        missing_points: Sequence[tuple[float, float]],
        missing_cells: Sequence[tuple[int, int]],
        frame_quality: str,
        img_h: int,
    ) -> tuple[bool, list[tuple[float, float]]]:
        if frame_quality == "LOW_QUALITY":
            return False, []

        # Y-edge filter: ignore holes entering / exiting the camera view.
        edge_y = self._same_zone_px
        effective: list[tuple[int, float, float]] = []  # (ci, x, y)
        for (x, y), (ci, _cj) in zip(missing_points, missing_cells):
            if y < edge_y or y > img_h - edge_y:
                continue
            effective.append((ci, x, y))

        # Reset per-frame match counts
        for z in self._grid_zones.values():
            z["count"] = 0

        # Match each effective point to an existing zone by ci column index.
        unmatched: list[tuple[int, float, float]] = []
        for (ci, x, y) in effective:
            best_zci = None
            best_d   = float("inf")
            for zci in self._grid_zones:
                d = abs(zci - ci)
                if d <= self._same_column_tol_cells and d < best_d:
                    best_d   = d
                    best_zci = zci
            if best_zci is not None:
                z = self._grid_zones[best_zci]
                z["count"] += 1
                z["y"] = z["y"] * 0.7 + y * 0.3   # track Y for visualisation
                z["x"] = z["x"] * 0.7 + x * 0.3
            else:
                unmatched.append((ci, x, y))

        # Increment streak for zones with enough matches; reset others.
        for z in self._grid_zones.values():
            if z["count"] >= self._min_missing:
                z["streak"] += 1
            else:
                z["streak"] = 0

        # Prune dead zones
        self._grid_zones = {
            ci: z for ci, z in self._grid_zones.items() if z["streak"] > 0
        }

        # Create zones for new unmatched points.
        # Guard against duplicate ci from earlier surviving zones.
        for (ci, x, y) in unmatched:
            if not any(abs(ci - zci) <= self._same_column_tol_cells
                       for zci in self._grid_zones):
                self._grid_zones[ci] = {"streak": 1, "count": 1, "x": x, "y": y}

        self._triggered = any(
            z["streak"] >= self._missing_frames for z in self._grid_zones.values()
        )
        return self._triggered, self._triggered_positions()

    # ------------------------------------------------------------------
    # Pixel-column tracking (legacy / fallback)
    # ------------------------------------------------------------------

    def _update_pixel(
        self,
        missing_points: Sequence[tuple[float, float]],
        near_miss_pairs: Sequence,
        frame_quality: str,
        img_h: int,
    ) -> tuple[bool, list[tuple[float, float]]]:
        if frame_quality == "LOW_QUALITY":
            return False, []

        edge_y = self._same_zone_px
        effective: list[tuple[float, float]] = []
        for (x, y) in missing_points:
            if y < edge_y or y > img_h - edge_y:
                continue
            effective.append((x, y))

        for z in self._zones:
            z["count"] = 0

        unmatched: list[tuple[float, float]] = []
        for (x, y) in effective:
            best_i = -1
            best_d = float("inf")
            for i, z in enumerate(self._zones):
                d = abs(z["x"] - x)
                if d <= self._same_zone_px and d < best_d:
                    best_d = d
                    best_i = i
            if best_i >= 0:
                z = self._zones[best_i]
                z["count"] += 1
                z["x"] = z["x"] * 0.7 + x * 0.3
                z["y"] = z["y"] * 0.7 + y * 0.3
            else:
                unmatched.append((x, y))

        for z in self._zones:
            if z["count"] >= self._min_missing:
                z["streak"] += 1
            else:
                z["streak"] = 0

        self._zones = [z for z in self._zones if z["streak"] > 0]

        for (x, y) in unmatched:
            self._zones.append({"x": x, "y": y, "streak": 1, "count": 1})

        self._triggered = any(
            z["streak"] >= self._missing_frames for z in self._zones
        )
        return self._triggered, self._triggered_positions()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _triggered_positions(self) -> list[tuple[float, float]]:
        if self._track_by_grid:
            return [
                (z["x"], z["y"])
                for z in self._grid_zones.values()
                if z["streak"] >= self._missing_frames
            ]
        return [
            (z["x"], z["y"])
            for z in self._zones
            if z["streak"] >= self._missing_frames
        ]
