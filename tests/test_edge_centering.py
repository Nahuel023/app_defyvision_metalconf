from types import SimpleNamespace

import pytest

from src.pipeline.edge_centering import (
    _robust_outer_column_centers,
    _robust_pattern_physical_bounds,
)


def test_pattern_bounds_ignore_single_glare_contour() -> None:
    holes = [
        SimpleNamespace(x=50.0, y=80.0, r=6.0),
        SimpleNamespace(x=50.5, y=160.0, r=6.0),
        SimpleNamespace(x=51.0, y=240.0, r=6.0),
        SimpleNamespace(x=51.5, y=320.0, r=6.0),
        SimpleNamespace(x=230.0, y=80.0, r=6.0),
        SimpleNamespace(x=230.5, y=160.0, r=6.0),
        SimpleNamespace(x=231.0, y=240.0, r=6.0),
        SimpleNamespace(x=231.5, y=320.0, r=6.0),
        # Reflejo falso muy a la izquierda: antes movía el borde escalar completo.
        SimpleNamespace(x=8.0, y=220.0, r=6.0),
    ]
    left_by_band = {
        0: (50.0, 80.0),
        1: (50.5, 160.0),
        2: (51.0, 240.0),
        3: (51.5, 320.0),
    }
    right_by_band = {
        0: (230.0, 80.0),
        1: (230.5, 160.0),
        2: (231.0, 240.0),
        3: (231.5, 320.0),
    }

    left, right = _robust_pattern_physical_bounds(
        holes, left_by_band, right_by_band, mid_y=200.0
    )

    assert left == pytest.approx(44.75, abs=0.2)
    assert right == pytest.approx(236.75, abs=0.2)


def test_outer_column_skips_a_chain_of_sparse_reflections() -> None:
    holes = []
    for x, count in ((5.0, 1), (21.0, 1), (34.0, 2), (50.0, 17),
                     (68.0, 18), (230.0, 17), (240.0, 1)):
        holes.extend(
            SimpleNamespace(x=x, y=float(i * 14), r=6.0)
            for i in range(count)
        )

    left, right = _robust_outer_column_centers(holes)

    assert left == pytest.approx(50.0)
    assert right == pytest.approx(230.0)
