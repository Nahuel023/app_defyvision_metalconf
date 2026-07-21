import numpy as np

from src.pipeline.compare import compare_missing_only
from src.pipeline.grid_fitting import grid_compare_points


def _cells(swapped: bool = False) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    for cj in range(12):
        six_holes = (cj % 2 == 1) ^ swapped
        columns = range(1, 7) if six_holes else range(1, 6)
        cells.extend((ci, cj) for ci in columns)
    return cells


def _points(cells: list[tuple[int, int]]) -> np.ndarray:
    points = []
    for ci, cj in cells:
        origin_x = 0.0 if cj % 2 else 18.0
        points.append((origin_x + ci * 36.0, 8.0 + cj * 14.0))
    return np.asarray(points, dtype=np.float32)


def test_grid_compare_can_select_opposite_stagger_row_parity() -> None:
    reference_cells = _cells(swapped=False)
    detected = _points(_cells(swapped=True))

    expected, selected_cells = grid_compare_points(
        detected,
        reference_cells,
        dx=36.0,
        dy=14.0,
        phase_ref_x=18.0,
        phase_ref_y=8.0,
        transform=None,
        img_w=242,
        img_h=180,
        margin=0.0,
        stagger_x_odd=-18.0,
        margin_x=12.0,
        margin_y=12.0,
        allow_row_parity_flip=True,
        parity_selection_tol_px=12.0,
    )
    report = compare_missing_only(
        expected,
        detected.tolist(),
        tol_xy_px=12.0,
        max_dx_px=12.0,
        max_missing=0,
        expected_cells=selected_cells,
        use_hungarian=True,
    )

    assert report.missing == 0
    assert selected_cells != reference_cells
    assert len(expected) >= 50


def test_grid_compare_keeps_original_parity_when_it_matches() -> None:
    reference_cells = _cells(swapped=False)
    detected = _points(reference_cells)

    expected, selected_cells = grid_compare_points(
        detected,
        reference_cells,
        dx=36.0,
        dy=14.0,
        phase_ref_x=18.0,
        phase_ref_y=8.0,
        transform=None,
        img_w=242,
        img_h=180,
        margin=0.0,
        stagger_x_odd=-18.0,
        margin_x=12.0,
        margin_y=12.0,
        allow_row_parity_flip=True,
        parity_selection_tol_px=12.0,
    )

    assert selected_cells == reference_cells
    assert len(expected) == len(detected)


def test_swapped_parity_still_reports_a_real_missing_hole() -> None:
    reference_cells = _cells(swapped=False)
    detected = _points(_cells(swapped=True))
    missing_point = tuple(detected[25])
    detected = np.delete(detected, 25, axis=0)

    expected, selected_cells = grid_compare_points(
        detected,
        reference_cells,
        dx=36.0,
        dy=14.0,
        phase_ref_x=18.0,
        phase_ref_y=8.0,
        transform=None,
        img_w=242,
        img_h=180,
        margin=0.0,
        stagger_x_odd=-18.0,
        margin_x=12.0,
        margin_y=12.0,
        allow_row_parity_flip=True,
        parity_selection_tol_px=12.0,
    )
    report = compare_missing_only(
        expected,
        detected.tolist(),
        tol_xy_px=12.0,
        max_dx_px=12.0,
        max_missing=0,
        expected_cells=selected_cells,
        use_hungarian=True,
    )

    assert report.missing == 1
    assert np.linalg.norm(np.asarray(report.missing_points[0]) - missing_point) <= 12.0
