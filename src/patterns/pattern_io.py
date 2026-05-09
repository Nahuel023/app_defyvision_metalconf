import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional


@dataclass(frozen=True)
class Pattern:
    model: str
    image_size: Tuple[int, int]             # (W, H)
    points: List[Tuple[float, float]]       # [(x, y), ...]  — kept for fallback
    radii: Optional[List[float]] = None
    # Grid topology (computed at build time, enables position-invariant inspection)
    dx: Optional[float] = None              # horizontal spacing (px)
    dy: Optional[float] = None              # vertical spacing (px)
    phase_x: Optional[float] = None        # fractional x-origin (px, 0…dx)
    phase_y: Optional[float] = None        # fractional y-origin (px, 0…dy)
    cells: Optional[List[Tuple[int, int]]] = None  # (col, row) per hole

    @property
    def has_grid(self) -> bool:
        return (self.dx is not None and self.dy is not None
                and self.cells is not None and len(self.cells) > 0)


def pattern_path(model: str) -> Path:
    return Path("data") / "patterns" / model / "holes.json"


def save_pattern(p: Pattern, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 2,
        "model": p.model,
        "image_size": [p.image_size[0], p.image_size[1]],
        "points": [{"x": x, "y": y} for (x, y) in p.points],
    }
    if p.radii is not None:
        payload["radii"] = [float(r) for r in p.radii]
    if p.has_grid:
        payload["grid"] = {
            "dx": round(float(p.dx), 3),
            "dy": round(float(p.dy), 3),
            "phase_x": round(float(p.phase_x), 3),
            "phase_y": round(float(p.phase_y), 3),
            "cells": [[ci, cj] for ci, cj in p.cells],
        }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_pattern(path: Path) -> Pattern:
    payload = json.loads(path.read_text(encoding="utf-8"))

    w, h = payload["image_size"]
    pts = [(float(d["x"]), float(d["y"])) for d in payload["points"]]
    radii = payload.get("radii", None)

    grid_data = payload.get("grid")
    if grid_data:
        dx      = float(grid_data["dx"])
        dy      = float(grid_data["dy"])
        phase_x = float(grid_data["phase_x"])
        phase_y = float(grid_data["phase_y"])
        cells   = [(int(c[0]), int(c[1])) for c in grid_data["cells"]]
    else:
        dx = dy = phase_x = phase_y = None
        cells = None

    return Pattern(
        model=str(payload.get("model", "")),
        image_size=(int(w), int(h)),
        points=pts,
        radii=None if radii is None else [float(r) for r in radii],
        dx=dx, dy=dy, phase_x=phase_x, phase_y=phase_y, cells=cells,
    )