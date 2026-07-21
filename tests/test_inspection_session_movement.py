import numpy as np

import src.vision.inspector as inspector_module
from src.vision.inspector import InspectionSession


class _FakeOwner:
    def _get_tols(self, *_args):
        return {}

    def _get_pattern(self, *_args):
        return object()

    def _get_roi(self, *_args):
        return None

    def _get_ema(self, *_args):
        return {}

    def _get_detector(self, *_args):
        return object()


def test_live_session_waits_for_real_movement_before_first_inspection(monkeypatch) -> None:
    inspected: list[np.ndarray] = []
    sentinel = object()

    def _fake_inspect_frame(_model, frame, **_kwargs):
        inspected.append(frame.copy())
        return sentinel

    monkeypatch.setattr(inspector_module, "inspect_frame", _fake_inspect_frame)
    session = InspectionSession(
        "modelo_B",
        scanner_id="scanner_1",
        movement_threshold=4.0,
        require_initial_movement=True,
        resource_owner=_FakeOwner(),
    )

    stopped = np.zeros((20, 20, 3), dtype=np.uint8)
    moving = np.full((20, 20, 3), 20, dtype=np.uint8)

    assert session.inspect_frame(stopped) is None  # solo ceba referencia
    assert session.inspect_frame(stopped.copy()) is None
    assert inspected == []

    assert session.inspect_frame(moving) is sentinel
    assert len(inspected) == 1
    assert session._initial_movement_seen is True


def test_initial_movement_gate_does_not_change_batch_sessions(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        inspector_module,
        "inspect_frame",
        lambda *_args, **_kwargs: sentinel,
    )
    session = InspectionSession(
        "modelo_B",
        movement_threshold=4.0,
        resource_owner=_FakeOwner(),
    )

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    assert session.inspect_frame(frame) is sentinel
