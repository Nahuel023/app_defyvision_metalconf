import src.patterns.roi as roi_module
from src.inspection import _update_runtime_roi_drift
from src.patterns.roi import ROI, RuntimeROIInfo
from src.pipeline.compare import CompareReport


def _report() -> CompareReport:
    return CompareReport(
        expected=100,
        detected=100,
        missing=0,
        status="OK",
        missing_points=[],
        matched_detected_idx=list(range(100)),
    )


def _info(roi: ROI, shift_x: float) -> RuntimeROIInfo:
    return RuntimeROIInfo(
        frame_w=640,
        frame_h=480,
        saved_roi=roi,
        effective_roi=roi,
        detected_roi=ROI(200, 0, 260, 480),
        shift_x=shift_x,
    )


def test_roi_drift_following_works_without_missing_and_respects_total_limit(
    monkeypatch, tmp_path
) -> None:
    anchor = ROI(100, 0, 240, 480)
    persisted = tmp_path / "roi.json"
    monkeypatch.setattr(roi_module, "roi_path", lambda *_args, **_kwargs: persisted)
    pre = {
        "roi": anchor,
        "saved_roi": anchor,
        "roi_runtime_state": {},
    }

    shifts = [2.0, 2.0] + [6.0] * 8
    for shift_x in shifts:
        active = pre["roi"]
        _update_runtime_roi_drift(
            pre,
            active,
            _info(active, shift_x=shift_x),
            _report(),
            model="modelo_B",
            scanner_id="scanner_1",
            enabled=True,
            warmup_frames=2,
            trigger_delta_px=3.0,
            edge_missing_min=2,
            edge_band_px=30.0,
            streak_frames=2,
            step_px=1.0,
            max_total_shift_px=2.0,
            urgent_delta_px=1000.0,
            cooldown_frames=0,
            cooldown_max_frames=0,
            cooldown_mult=1.0,
            recenter_mode="move",
            require_edge_missing=False,
        )

    assert pre["roi"].x == 102
    assert pre["saved_roi"] == anchor
    assert pre["roi_runtime_state"]["applied_total_shift_px"] == 2.0
    assert '"x": 102' in persisted.read_text(encoding="utf-8")
