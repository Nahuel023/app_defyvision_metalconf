import numpy as np

from src.patterns.roi import ROI, estimate_roi_from_pattern_holes
from src.utils.config import load_tolerances


def _pattern_columns() -> np.ndarray:
    # Once posiciones X combinadas de una grilla staggered, repetidas a lo
    # largo de la chapa como ocurre en microperforado.
    columns = np.arange(35.0, 222.0, 18.5)
    return np.tile(columns, 18)


def test_hole_anchor_uses_both_pattern_edges_and_ignores_isolated_noise() -> None:
    pattern_x = _pattern_columns()
    rng = np.random.default_rng(20260819)
    detected_x = pattern_x + 205.0 + rng.normal(0.0, 0.45, pattern_x.size)
    # Reflejos/timestamp fuera del grupo perforado no deben estirar el ROI.
    detected_x = np.concatenate(
        [detected_x, np.array([12.0, 18.0, 480.0, 495.0, 520.0, 533.0])]
    )

    result = estimate_roi_from_pattern_holes(
        detected_x,
        pattern_x,
        ROI(x=213, y=0, w=255, h=480),
        frame_w=640,
        frame_h=480,
        edge_quantile=0.05,
        cluster_extra_px=20.0,
        min_holes=40,
        min_pattern_ratio=0.25,
        max_span_delta_px=12.0,
        max_shift_px=120.0,
    )

    assert result is not None
    assert result.roi == ROI(x=205, y=0, w=255, h=480)
    assert result.cluster_count == pattern_x.size
    assert result.span_delta_px < 1.0


def test_hole_anchor_rejects_insufficient_or_wrong_width_evidence() -> None:
    pattern_x = _pattern_columns()
    reference = ROI(x=213, y=0, w=255, h=480)

    insufficient = estimate_roi_from_pattern_holes(
        [240.0, 260.0, 280.0],
        pattern_x,
        reference,
        frame_w=640,
        frame_h=480,
        min_holes=40,
    )
    wrong_width = estimate_roi_from_pattern_holes(
        np.linspace(240.0, 360.0, pattern_x.size),
        pattern_x,
        reference,
        frame_w=640,
        frame_h=480,
        min_holes=40,
        max_span_delta_px=12.0,
    )

    assert insufficient is None
    assert wrong_width is None


def test_hole_anchor_follows_small_and_large_horizontal_movements() -> None:
    pattern_x = _pattern_columns()
    reference = ROI(x=213, y=0, w=255, h=480)

    for expected_x in (175, 193, 205, 209, 217, 235):
        result = estimate_roi_from_pattern_holes(
            pattern_x + float(expected_x),
            pattern_x,
            reference,
            frame_w=640,
            frame_h=480,
            min_holes=40,
            max_span_delta_px=12.0,
            max_shift_px=120.0,
        )

        assert result is not None
        assert result.roi.x == expected_x
        assert result.roi.w == reference.w


def test_hole_anchor_is_enabled_only_for_validated_scanner1_microperforado() -> None:
    scanner1_micro = load_tolerances("modelo_B", "scanner_1")
    scanner1_esterilla = load_tolerances("modelo_A", "scanner_1")
    scanner2_micro = load_tolerances("modelo_B", "scanner_2")

    assert scanner1_micro["roi_hole_anchor_enabled"] is True
    assert scanner1_micro["roi_hole_anchor_min_holes"] == 40
    assert scanner1_esterilla["roi_hole_anchor_enabled"] is False
    assert scanner2_micro["roi_hole_anchor_enabled"] is False
