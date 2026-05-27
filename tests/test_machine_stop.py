from src.pipeline.machine_stop import MachineStopDetector


def test_grid_tracking_resets_on_empty_missing_frame() -> None:
    detector = MachineStopDetector(
        enabled=True,
        missing_frames=3,
        min_missing=1,
        track_by_grid=True,
    )

    triggered, _ = detector.update(
        [(10.0, 50.0)], [], "GOOD", 100, missing_cells=[(4, 1)]
    )
    assert not triggered

    triggered, _ = detector.update([], [], "GOOD", 100, missing_cells=[])
    assert not triggered
    assert detector.triggered_columns == []

    triggered, _ = detector.update(
        [(10.0, 50.0)], [], "GOOD", 100, missing_cells=[(4, 2)]
    )
    assert not triggered
    assert detector.triggered_columns == []


def test_grid_tracking_needs_consecutive_frames() -> None:
    detector = MachineStopDetector(
        enabled=True,
        missing_frames=3,
        min_missing=1,
        track_by_grid=True,
    )

    for cj in (1, 2):
        triggered, _ = detector.update(
            [(10.0, 50.0)], [], "GOOD", 100, missing_cells=[(4, cj)]
        )
        assert not triggered

    triggered, _ = detector.update(
        [(10.0, 50.0)], [], "GOOD", 100, missing_cells=[(4, 3)]
    )
    assert triggered
    assert detector.triggered_columns == [4]


def test_low_quality_frame_does_not_report_stop() -> None:
    detector = MachineStopDetector(
        enabled=True,
        missing_frames=2,
        min_missing=1,
        track_by_grid=True,
    )

    triggered, _ = detector.update(
        [(10.0, 50.0)], [], "GOOD", 100, missing_cells=[(4, 1)]
    )
    assert not triggered

    triggered, _ = detector.update(
        [(10.0, 50.0)], [], "GOOD", 100, missing_cells=[(4, 2)]
    )
    assert triggered

    triggered, positions = detector.update(
        [(10.0, 50.0)], [], "LOW_QUALITY", 100, missing_cells=[(4, 3)]
    )
    assert not triggered
    assert positions == []
