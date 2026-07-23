from src.inspection import _advance_desalignment_streak
from src.vision.inspector import InspectionSession


def test_desalignment_never_stops_before_three_consecutive_frames() -> None:
    state: dict = {}

    first, _ = _advance_desalignment_streak(
        state, detected=True, reason="desalineado", stop_frames=1
    )
    second, _ = _advance_desalignment_streak(
        state, detected=True, reason="desalineado", stop_frames=1
    )
    third, reason = _advance_desalignment_streak(
        state, detected=True, reason="desalineado", stop_frames=1
    )

    assert first is False
    assert second is False
    assert third is True
    assert "[3/3 frames]" in reason


def test_good_frame_resets_desalignment_streak() -> None:
    state: dict = {}

    _advance_desalignment_streak(
        state, detected=True, reason="desalineado", stop_frames=3
    )
    _advance_desalignment_streak(
        state, detected=True, reason="desalineado", stop_frames=3
    )
    reset, _ = _advance_desalignment_streak(
        state, detected=False, reason="", stop_frames=3
    )
    after_reset, _ = _advance_desalignment_streak(
        state, detected=True, reason="desalineado", stop_frames=3
    )

    assert reset is False
    assert after_reset is False
    assert state["streak"] == 1


def test_session_reset_clears_hidden_alignment_evidence() -> None:
    session = InspectionSession.__new__(InspectionSession)
    session._preloaded = {
        "desalign_state": {"streak": 2, "reason": "oculto durante grace"}
    }

    session.reset_stop_state()

    assert session._preloaded["desalign_state"] == {"streak": 0, "reason": ""}
