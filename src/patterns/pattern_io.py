import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional


@dataclass(frozen=True)
class Pattern:
    model: str
    image_size: Tuple[int, int]             # (W, H)
    points: List[Tuple[float, float]]       # [(x, y), ...]
    radii: Optional[List[float]] = None     # por si después querés diámetro


def pattern_path(model: str) -> Path:
    return Path("data") / "patterns" / model / "holes.json"


def save_pattern(p: Pattern, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 1,
        "model": p.model,
        "image_size": [p.image_size[0], p.image_size[1]],
        "points": [{"x": x, "y": y} for (x, y) in p.points],
    }
    if p.radii is not None:
        payload["radii"] = [float(r) for r in p.radii]

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_pattern(path: Path) -> Pattern:
    payload = json.loads(path.read_text(encoding="utf-8"))

    w, h = payload["image_size"]
    pts = [(float(d["x"]), float(d["y"])) for d in payload["points"]]
    radii = payload.get("radii", None)

    return Pattern(
        model=str(payload.get("model", "")),
        image_size=(int(w), int(h)),
        points=pts,
        radii=None if radii is None else [float(r) for r in radii],
    )