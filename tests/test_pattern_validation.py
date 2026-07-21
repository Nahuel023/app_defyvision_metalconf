import json
from pathlib import Path

import pytest

from src.patterns.pattern_io import load_pattern
from src.patterns.roi import ROI


@pytest.mark.parametrize(
    "values",
    [(-1, 0, 10, 10), (0, -1, 10, 10), (0, 0, 0, 10), (0, 0, 10, 0)],
)
def test_roi_rejects_invalid_geometry(values) -> None:
    with pytest.raises(ValueError, match="ROI invalida"):
        ROI(*values)


def test_pattern_rejects_mismatched_grid_lengths(tmp_path: Path) -> None:
    path = tmp_path / "holes.json"
    path.write_text(
        json.dumps(
            {
                "model": "modelo_B",
                "image_size": [100, 100],
                "points": [{"x": 10, "y": 10}, {"x": 20, "y": 20}],
                "radii": [3, 3],
                "grid": {
                    "dx": 10,
                    "dy": 10,
                    "phase_x": 0,
                    "phase_y": 0,
                    "cells": [[1, 1]],
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cells=1 != points=2"):
        load_pattern(path)
