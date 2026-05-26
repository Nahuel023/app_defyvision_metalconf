"""
Persistent missing-hole detector for machine-stop logic.

Tracks spatial clusters of consistently-missing holes across consecutive
frames. When a cluster's streak reaches `missing_frames`, triggers a
DETENCION DE MAQUINA signal — indicating a likely broken or clogged punch.

LOW_QUALITY frames (blur, movement) do NOT increment or reset streaks.

Near-miss points (a detected hole just outside tol_xy_px) can be excluded
to avoid false alarms caused by measurement jitter near the tolerance edge.

Edge filter: missing points within `same_zone_px` of the top or bottom of
the ROI are ignored (they appear when the sheet enters or exits the camera).

In continuous feed the same broken/clogged punch moves vertically through the
ROI.  Zones are therefore matched primarily by X column; Y is tracked for
visualization but does not reset the streak.
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
    ) -> None:
        self._enabled          = enabled
        self._missing_frames   = max(1, missing_frames)
        self._min_missing      = max(1, min_missing)
        self._same_zone_px     = max(1.0, same_zone_px)
        self._ignore_near_miss = ignore_near_miss
        # Each zone: {x, y, streak, count}
        self._zones: list[dict] = []
        self._triggered = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all zone state. Call when starting a new inspection session."""
        self._zones.clear()
        self._triggered = False

    def update(
        self,
        missing_points: Sequence[tuple[float, float]],
        near_miss_pairs: Sequence,
        frame_quality: str,
        img_h: int,
    ) -> tuple[bool, list[tuple[float, float]]]:
        """Update with this frame's missing holes.

        Args:
            missing_points:  ROI-relative (x, y) of expected holes with no match.
            near_miss_pairs: list of ((exp_x, exp_y), (det_x, det_y)) near misses.
            frame_quality:   "GOOD" | "LOW_QUALITY"
            img_h:           ROI height in pixels (for Y-edge margin).

        Returns:
            (triggered, triggered_zone_positions)
            - triggered: True when any zone has streak >= missing_frames.
            - triggered_zone_positions: (x, y) centers of triggered zones.

        LOW_QUALITY frames leave state unchanged and return the current value.
        """
        if not self._enabled:
            return False, []

        if frame_quality == "LOW_QUALITY":
            return self._triggered, self._triggered_positions()

        # Apply Y-edge filter.  Do not discard near-misses here: if they repeat
        # in the same X column across frames, they are evidence of a real issue.
        edge_y = self._same_zone_px
        effective: list[tuple[float, float]] = []
        for (x, y) in missing_points:
            if y < edge_y or y > img_h - edge_y:
                continue
            effective.append((x, y))

        # Reset per-frame match count on existing zones
        for z in self._zones:
            z["count"] = 0

        # Greedy closest-first: match by X column.  The sheet advances in Y,
        # so a persistent punch defect appears at changing Y but similar X.
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
                # Weighted moving average — zone center tracks the hole position
                z["x"] = z["x"] * 0.7 + x * 0.3
                z["y"] = z["y"] * 0.7 + y * 0.3
            else:
                unmatched.append((x, y))

        # Increment streak for zones with enough matches; reset others
        for z in self._zones:
            if z["count"] >= self._min_missing:
                z["streak"] += 1
            else:
                z["streak"] = 0

        # Prune dead zones
        self._zones = [z for z in self._zones if z["streak"] > 0]

        # Create zones for unmatched points
        for (x, y) in unmatched:
            self._zones.append({"x": x, "y": y, "streak": 1, "count": 1})

        self._triggered = any(
            z["streak"] >= self._missing_frames for z in self._zones
        )
        return self._triggered, self._triggered_positions()

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _triggered_positions(self) -> list[tuple[float, float]]:
        return [
            (z["x"], z["y"])
            for z in self._zones
            if z["streak"] >= self._missing_frames
        ]
