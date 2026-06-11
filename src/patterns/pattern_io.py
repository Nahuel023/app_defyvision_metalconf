import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional

import yaml

_log = logging.getLogger(__name__)


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
    # Staggered-grid support: if odd-cj rows have a different X-origin than even-cj
    # rows (e.g. Esterilla large/small hole alternation), store the signed X-phase
    # offset so grid_compare_points can generate correct expected positions for both.
    stagger_x_odd: Optional[float] = None        # odd-cj X-phase offset (px); 0 = no stagger

    @property
    def has_grid(self) -> bool:
        return (self.dx is not None and self.dy is not None
                and self.cells is not None and len(self.cells) > 0)


def pattern_path(model: str, scanner_id: str | None = None) -> Path:
    """Return the canonical write path for a pattern (does not check existence)."""
    if scanner_id:
        return Path("data") / "patterns" / scanner_id / model / "holes.json"
    return Path("data") / "patterns" / model / "holes.json"


def infer_scanner_id(model: str, source_path: str | Path | None = None) -> str | None:
    """Infer scanner_id from a source path or the current io_map model assignment."""
    if source_path is not None:
        text = str(source_path).lower().replace("\\", "/")
        match = re.search(r"scanner[_-]?(\d+)", text)
        if match:
            return f"scanner_{int(match.group(1))}"

    cfg_path = Path("config") / "io_map.yaml"
    if not cfg_path.exists():
        return None

    try:
        payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    exact = [
        sid for sid, cfg in payload.items()
        if sid != "plc" and str((cfg or {}).get("model", "")).strip() == model
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def find_pattern_path(model: str, scanner_id: str | None = None) -> Path:
    """Resolve pattern path with fallback: scanner-specific → model-only."""
    scanner_id = scanner_id or infer_scanner_id(model)
    if scanner_id:
        p = Path("data") / "patterns" / scanner_id / model / "holes.json"
        if p.exists():
            return p
        _log.warning(
            "No hay patrón específico para %s/%s — usando patrón global data/patterns/%s/holes.json. "
            "Recalibrá con: build-pattern --model %s --scanner %s --img <ref.jpg>",
            scanner_id, model, model, model, scanner_id,
        )
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
        grid: dict = {
            "dx": round(float(p.dx), 3),
            "dy": round(float(p.dy), 3),
            "phase_x": round(float(p.phase_x), 3),
            "phase_y": round(float(p.phase_y), 3),
            "cells": [[ci, cj] for ci, cj in p.cells],
        }
        if p.stagger_x_odd is not None and p.stagger_x_odd != 0.0:
            grid["stagger_x_odd"] = round(float(p.stagger_x_odd), 3)
        payload["grid"] = grid
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_pattern(path: Path) -> Pattern:
    payload = json.loads(path.read_text(encoding="utf-8"))

    w, h = payload["image_size"]
    pts = [(float(d["x"]), float(d["y"])) for d in payload["points"]]
    radii = payload.get("radii", None)

    grid_data = payload.get("grid")
    if grid_data:
        dx           = float(grid_data["dx"])
        dy           = float(grid_data["dy"])
        phase_x      = float(grid_data["phase_x"])
        phase_y      = float(grid_data["phase_y"])
        cells        = [(int(c[0]), int(c[1])) for c in grid_data["cells"]]
        stagger_x_odd = float(grid_data["stagger_x_odd"]) if "stagger_x_odd" in grid_data else None
    else:
        dx = dy = phase_x = phase_y = None
        cells = None
        stagger_x_odd = None

    return Pattern(
        model=str(payload.get("model", "")),
        image_size=(int(w), int(h)),
        points=pts,
        radii=None if radii is None else [float(r) for r in radii],
        dx=dx, dy=dy, phase_x=phase_x, phase_y=phase_y, cells=cells,
        stagger_x_odd=stagger_x_odd,
    )
