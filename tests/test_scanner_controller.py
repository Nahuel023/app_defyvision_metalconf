from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.controller.scanner_controller import ScannerController
from src.utils.state import ScannerState


class _FakeIO:
    def __init__(self) -> None:
        self.plc_config = {"poll_interval_ms": 50}
        self._scanner_cfg = {
            "scanner_1": {
                "model": "modelo_A",
                "inspection": {},
            }
        }
        self.writes: list[tuple[str, bool]] = []
        self.batches: list[list[tuple[str, bool]]] = []
        self.block_solenoid_on = False

    def scanner_config(self, scanner_id: str) -> dict:
        return self._scanner_cfg[scanner_id]

    def read(self, signal: str):
        return None

    def write(self, signal: str, value: bool) -> bool:
        self.writes.append((signal, value))
        if self.block_solenoid_on and signal.endswith(".solenoid") and value:
            return False
        return True

    def write_batch(self, batch: list[tuple[str, bool]]) -> bool:
        self.batches.append(list(batch))
        return True

    def write_critical(self, signal: str, value: bool, *,
                       retries: int = 5, retry_delay_s: float = 0.15,
                       verify: bool = True) -> bool:
        return self.write(signal, value)


class _FakeCamera:
    def __init__(self) -> None:
        self.is_running = False
        self.start_calls = 0

    def start(self) -> bool:
        self.start_calls += 1
        self.is_running = True
        return True


class _StuckThread:
    name = "stuck-worker"

    def __init__(self) -> None:
        self.join_calls = 0

    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        self.join_calls += 1


class _EvidenceSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def flush_event(self, event_type: str, reason: str = "", **kwargs):
        self.calls.append((event_type, reason, kwargs))
        return Path("event")

    def is_post_event_active(self) -> bool:
        return False

    def finish_post_event(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_start_is_blocked_when_license_invalid(monkeypatch) -> None:
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    monkeypatch.setattr(controller, "_license_allows_operation", lambda: False)

    try:
        assert controller.start() is False
        assert camera.start_calls == 0
        assert controller.state == ScannerState.IDLE
    finally:
        controller.shutdown()


def test_machine_stop_passes_exact_trigger_and_overlay_to_evidence() -> None:
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    if controller._recorder is not None:
        controller._recorder.close()
    spy = _EvidenceSpy()
    controller._recorder = spy
    controller._startup_grace_remaining = 0
    controller._startup_grace_seconds = 0.0
    controller._transition(ScannerState.RUNNING)

    try:
        controller.inject_machine_stop("PATRON DESALINEADO")

        assert len(spy.calls) == 1
        event_type, reason, kwargs = spy.calls[0]
        assert event_type == "machine_stop"
        assert "faltantes persistentes" in reason
        assert isinstance(kwargs["trigger_frame"], np.ndarray)
        assert isinstance(kwargs["trigger_overlay"], np.ndarray)
        assert kwargs["trigger_frame"].shape == kwargs["trigger_overlay"].shape
    finally:
        controller.shutdown()


def test_desalignment_event_reason_is_explicit() -> None:
    result = SimpleNamespace(
        tilt_warn=False,
        pattern_alignment_warn=True,
        report=SimpleNamespace(missing=12),
    )

    assert ScannerController._derive_stop_reason(result) == "patron desalineado"


def test_reload_cache_requests_live_session_refresh(monkeypatch) -> None:
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    invalidations: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(
        controller._inspector,
        "invalidate",
        lambda model=None, scanner_id=None: invalidations.append((model, scanner_id)),
    )

    try:
        controller._nok_streak = 3
        controller._lq_streak = 2
        assert controller._cache_revision == 0
        controller.reload_cache()
        assert controller._cache_revision == 1
        assert controller._nok_streak == 0
        assert controller._lq_streak == 0
        assert invalidations == [("modelo_A", "scanner_1")]
    finally:
        controller.shutdown()


def test_handle_license_failure_stops_running_scanner() -> None:
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)

    try:
        controller._transition(ScannerState.RUNNING)
        controller._handle_license_failure()

        assert controller.state == ScannerState.STOPPED
        assert controller._stop_event.is_set()
        assert ("scanner_1.solenoid", False) in io.writes
        assert io.batches[-1] == [
            ("scanner_1.light_blue", False),
            ("scanner_1.light_green", False),
            ("scanner_1.light_yellow", False),
            ("scanner_1.light_red", False),
        ]
    finally:
        controller.shutdown()


def test_start_enters_running_even_if_solenoid_is_blocked(monkeypatch) -> None:
    """El IOMap bloquea el solenoide en modo seguro, pero start() arranca igual:
    la escritura se intenta y su rechazo no impide la sesión de inspección."""
    io = _FakeIO()
    io.block_solenoid_on = True
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    monkeypatch.setattr(controller, "_license_allows_operation", lambda: True)

    try:
        assert controller.start() is True
        assert controller.state == ScannerState.RUNNING
        assert ("scanner_1.solenoid", True) in io.writes
        assert io.batches[-1] == [
            ("scanner_1.light_blue", False),
            ("scanner_1.light_green", True),
            ("scanner_1.light_yellow", False),
            ("scanner_1.light_red", False),
        ]
    finally:
        controller.shutdown()


def test_start_is_blocked_while_previous_worker_is_still_alive(monkeypatch) -> None:
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    stuck = _StuckThread()
    controller._poller_thread = stuck
    monkeypatch.setattr(controller, "_license_allows_operation", lambda: True)

    try:
        assert controller.start() is False
        assert controller.state == ScannerState.IDLE
        assert camera.start_calls == 0
        controller._join_threads()
        assert controller._poller_thread is stuck
    finally:
        controller._poller_thread = None
        controller.shutdown()


def test_reset_clears_stopped_by_fault_flag() -> None:
    """Tras una parada por falla, reset() debe volver a IDLE y limpiar el flag
    de falla (empieza de cero). Respalda el auto-retorno a IDLE de la UI."""
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    if controller._recorder is not None:
        controller._recorder.close()
        controller._recorder = None
    controller._startup_grace_remaining = 0
    controller._startup_grace_seconds = 0.0
    controller._transition(ScannerState.RUNNING)

    try:
        controller.inject_machine_stop("PATRON DESALINEADO")
        status = controller.get_status()
        assert status["state"] == ScannerState.STOPPED
        assert status["stopped_by_fault"] is True

        assert controller.reset() is True
        cleared = controller.get_status()
        assert cleared["state"] == ScannerState.IDLE
        assert cleared["stopped_by_fault"] is False
    finally:
        controller.shutdown()


def test_good_frames_increment_ok_while_low_quality_is_inconclusive() -> None:
    """Protege el flujo que ve el operario en los contadores.

    Un resultado nítido debe contabilizarse inmediatamente. LOW_QUALITY sigue
    siendo inconcluso: cuenta como problema de calidad, pero no inventa OK/NOK.
    """
    io = _FakeIO()
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
    if controller._recorder is not None:
        controller._recorder.close()
        controller._recorder = None
    controller._ok_buf_enabled = False
    controller._tl_enabled = False
    controller._startup_grace_remaining = 0
    controller._startup_grace_seconds = 0.0
    controller._transition(ScannerState.RUNNING)

    def result(status: str, quality: str):
        return SimpleNamespace(
            status=status,
            frame_quality=quality,
            report=SimpleNamespace(missing=0),
            detection_ratio=1.0,
            alignment_ok=True,
            machine_stop=False,
            overlay=None,
            image=None,
        )

    try:
        controller._handle_result(result("OK", "GOOD"))
        controller._handle_result(result("NOK", "LOW_QUALITY"))

        status = controller.get_status()
        assert status["ok_count"] == 1
        assert status["nok_count"] == 0
        assert status["low_quality_count"] == 1
        assert status["total_inspections"] == 2
    finally:
        controller.shutdown()
