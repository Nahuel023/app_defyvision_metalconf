import time

import numpy as np
import pytest

from src.controller.scanner_controller import ScannerController
import src.controller.scanner_controller as scanner_controller_module
from src.utils.state import OperationMode, ScannerState


@pytest.fixture(autouse=True)
def _disable_real_event_writes(monkeypatch):
    """Los tests inyectan su recorder; nunca deben escribir en data/events real."""
    original = scanner_controller_module.load_tolerances

    def load_without_events(*args, **kwargs):
        cfg = original(*args, **kwargs)
        cfg["events_enabled"] = False
        return cfg

    monkeypatch.setattr(
        scanner_controller_module, "load_tolerances", load_without_events
    )


class _FakeIO:
    def __init__(self, scanner_id: str = "scanner_1", model: str = "modelo_A") -> None:
        self.plc_config = {"poll_interval_ms": 50}
        self._scanner_cfg = {
            scanner_id: {
                "model": model,
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


class _FailingCamera(_FakeCamera):
    def start(self) -> bool:
        self.start_calls += 1
        return False


class _StuckThread:
    name = "stuck-worker"

    def __init__(self) -> None:
        self.join_calls = 0

    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        self.join_calls += 1


class _EvidenceRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def flush_event(self, event_type: str, reason: str, **kwargs) -> None:
        self.events.append((event_type, reason, kwargs))

    def get_post_event_dir(self):
        return None

    def close(self) -> None:
        return None


@pytest.mark.parametrize("scanner_id", ["scanner_1", "scanner_2"])
@pytest.mark.parametrize("model", ["modelo_A", "modelo_B"])
def test_run_frames_stop_at_15_seconds_for_every_profile(
    monkeypatch, scanner_id, model
):
    from src.ui.operator import _is_machine_jam_reason

    io = _FakeIO(scanner_id, model)
    controller = ScannerController(scanner_id, io, _FakeCamera())
    recorder = _EvidenceRecorder()
    controller._recorder = recorder
    try:
        controller._state = ScannerState.RUNNING
        monkeypatch.setattr(scanner_controller_module.time, "monotonic", lambda: 100.0)
        # El arranque sin primer analisis conserva su proteccion anterior.
        assert not controller._check_run_frame_stall()
        controller._run_frame_last_mono = 85.001
        assert not controller._check_run_frame_stall()
        controller._run_frame_last_mono = 85.0
        assert controller._check_run_frame_stall()
        assert controller.state == ScannerState.ERROR
        assert (f"{scanner_id}.solenoid", False) in io.writes
        assert (f"{scanner_id}.light_red", True) in io.batches[-1]
        assert controller._stop_event.is_set()
        reason = controller.get_status()["state_reason"]
        assert "15 segundos" in reason
        assert _is_machine_jam_reason(reason)
        assert recorder.events[0][0] == "machine_jam"
        assert recorder.events[0][2]["metadata"]["timeout_seconds"] == 15.0
        assert not controller._check_run_frame_stall()
        assert len(recorder.events) == 1
    finally:
        controller.shutdown()


@pytest.mark.parametrize("state", [ScannerState.IDLE, ScannerState.STOPPED,
                                  ScannerState.FAULT, ScannerState.ERROR])
def test_run_frames_watchdog_ignores_non_running_states(monkeypatch, state):
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    try:
        controller._state = state
        controller._run_frame_last_mono = 1.0
        monkeypatch.setattr(scanner_controller_module.time, "monotonic", lambda: 100.0)
        assert not controller._check_run_frame_stall()
        assert controller.state == state
    finally:
        controller.shutdown()


@pytest.mark.parametrize("repeated_capture", [False, True])
def test_live_loop_rearms_on_result_then_stops_without_frame_progress(
    monkeypatch, repeated_capture
):
    from types import SimpleNamespace

    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    clock = [100.0]
    reads = [0]
    results = []

    def capture():
        reads[0] += 1
        assert reads[0] <= 4, "El loop debe detenerse a los 15 segundos"
        clock[0] = [100.0, 114.0, 128.9, 129.0][reads[0] - 1]
        seq = min(reads[0], 2) if repeated_capture else reads[0]
        return np.zeros((10, 10, 3), dtype=np.uint8), seq

    session = SimpleNamespace(
        _model="modelo_A", last_position_diff=0.0,
        inspect_frame=lambda *a, **kw: object() if reads[0] <= 2 else None,
    )
    monkeypatch.setattr(scanner_controller_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(scanner_controller_module, "InspectionSession", lambda *a, **kw: session)
    monkeypatch.setattr(controller, "_run_startup_selftest", lambda model: True)
    monkeypatch.setattr(controller, "_run_roi_precalibration", lambda *a: None)
    monkeypatch.setattr(controller, "_handle_result", lambda res, *a, **kw: results.append(res))
    controller._camera.get_fresh_frame = capture
    try:
        controller._state = ScannerState.RUNNING
        controller._continuous_loop_impl()
        assert len(results) == 2
        assert reads[0] == 4
        assert controller._run_frame_last_mono == 114.0
        assert controller.state == ScannerState.ERROR
        assert "15 segundos" in controller.get_status()["state_reason"]
    finally:
        controller.shutdown()


def test_poller_stops_stalled_inspector_independently(monkeypatch):
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    try:
        controller._state = ScannerState.RUNNING
        controller._run_frame_last_mono = 100.0
        monkeypatch.setattr(scanner_controller_module.time, "monotonic", lambda: 115.0)
        controller._poll_loop()
        assert controller.state == ScannerState.ERROR
        assert ("scanner_1.solenoid", False) in controller._io.writes
    finally:
        controller.shutdown()


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


@pytest.mark.parametrize("scanner_id", ["scanner_1", "scanner_2"])
@pytest.mark.parametrize("model", ["modelo_A", "modelo_B"])
def test_first_ok_frame_resets_all_active_nok_counters(
    scanner_id: str, model: str
) -> None:
    """Un OK corta de inmediato tanto la racha como el contador visible NOK."""
    controller = ScannerController(
        scanner_id, _FakeIO(scanner_id=scanner_id, model=model), _FakeCamera()
    )
    try:
        controller._state = ScannerState.RUNNING
        controller.inject_result(False, count=2)
        assert controller.get_status()["nok_streak"] == 2
        assert controller.get_status()["nok_count"] == 2

        controller.inject_result(True, count=1)
        status = controller.get_status()
        assert status["nok_streak"] == 0
        assert status["nok_count"] == 0

        # El NOK siguiente inicia una racha completamente nueva desde uno.
        controller.inject_result(False, count=1)
        status = controller.get_status()
        assert status["nok_streak"] == 1
        assert status["nok_count"] == 1
    finally:
        controller.shutdown()


def test_start_enters_running_even_if_solenoid_is_blocked(monkeypatch) -> None:
    """El IOMap bloquea el solenoide en modo seguro, pero start() arranca igual:
    la escritura se intenta y su rechazo no impide la sesión de inspección."""
    io = _FakeIO()
    io.block_solenoid_on = True
    camera = _FakeCamera()
    controller = ScannerController("scanner_1", io, camera)
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
    try:
        assert controller.start() is False
        assert controller.state == ScannerState.IDLE
        assert camera.start_calls == 0
        controller._join_threads()
        assert controller._poller_thread is stuck
    finally:
        controller._poller_thread = None
        controller.shutdown()


def test_camera_start_error_exposes_operator_reason() -> None:
    controller = ScannerController("scanner_1", _FakeIO(), _FailingCamera())
    controller._mode = OperationMode.AUTO
    try:
        assert controller.start() is False
        status = controller.get_status()
        assert status["state"] == ScannerState.ERROR
        assert status["state_reason"] == (
            "Cámara sin señal: no se pudo iniciar la captura"
        )
    finally:
        controller.shutdown()


@pytest.mark.parametrize("scanner_id", ["scanner_1", "scanner_2"])
@pytest.mark.parametrize("model", ["modelo_A", "modelo_B"])
def test_configured_nok_streak_stops_every_scanner_and_model_during_startup_grace(
    scanner_id: str, model: str
) -> None:
    """La gracia de encuadre nunca puede ocultar la racha NOK configurada."""
    io = _FakeIO(scanner_id=scanner_id, model=model)
    controller = ScannerController(scanner_id, io, _FakeCamera())
    delivered_results = []
    controller.on_result = lambda result, streak: delivered_results.append(
        (result, streak)
    )
    try:
        controller._state = ScannerState.RUNNING
        controller._startup_grace_remaining = 100
        controller._startup_grace_seconds = 30.0
        controller._run_loop_start_mono = time.monotonic()
        threshold = controller._consecutive_nok

        controller.inject_result(False, count=threshold - 1)
        assert controller.state == ScannerState.RUNNING
        assert controller.get_status()["nok_streak"] == threshold - 1
        assert (f"{scanner_id}.solenoid", False) not in io.writes

        controller.inject_result(False, count=1)
        status = controller.get_status()
        assert status["state"] == ScannerState.FAULT
        assert status["nok_streak"] == threshold
        assert status["state_reason"] == f"{threshold} imágenes NOK consecutivas"
        assert delivered_results[-1][0].machine_stop is True
        assert delivered_results[-1][1] == threshold
        assert (f"{scanner_id}.solenoid", False) in io.writes
        assert io.batches[-1] == [
            (f"{scanner_id}.light_blue", False),
            (f"{scanner_id}.light_green", False),
            (f"{scanner_id}.light_yellow", False),
            (f"{scanner_id}.light_red", True),
        ]
    finally:
        controller.shutdown()


@pytest.mark.parametrize("scanner_id", ["scanner_1", "scanner_2"])
@pytest.mark.parametrize("model", ["modelo_A", "modelo_B"])
def test_simultaneous_final_nok_and_machine_stop_always_cuts_solenoid(
    scanner_id: str, model: str
) -> None:
    """Dos detectores coincidentes no pueden anular la parada fisica.

    Reproduce la falla de produccion: los primeros resultados acumulan la racha
    NOK configurada y el ultimo tambien llega con machine_stop=True. La salida
    OFF debe escribirse para todos los scanners/patrones y el hilo detenerse.
    """
    io = _FakeIO(scanner_id=scanner_id, model=model)
    controller = ScannerController(scanner_id, io, _FakeCamera())
    controller._recorder = None
    delivered_results = []
    controller.on_result = lambda result, streak: delivered_results.append(
        (result, streak)
    )
    try:
        controller._state = ScannerState.RUNNING
        controller._startup_grace_remaining = 0
        controller._startup_grace_seconds = 0.0
        threshold = controller._consecutive_nok

        controller.inject_result(False, count=threshold - 1)
        assert controller.state == ScannerState.RUNNING
        assert controller.nok_streak == threshold - 1

        writes_before = len(io.writes)
        controller.inject_machine_stop("NOK FINAL SIMULTANEO")
        terminal_writes = io.writes[writes_before:]

        assert controller.state == ScannerState.STOPPED
        assert controller.nok_streak == threshold
        assert controller._stop_event.is_set()
        assert (f"{scanner_id}.solenoid", False) in terminal_writes
        assert delivered_results[-1][0].machine_stop is True
        assert delivered_results[-1][1] == threshold
        assert io.batches[-1] == [
            (f"{scanner_id}.light_blue", False),
            (f"{scanner_id}.light_green", False),
            (f"{scanner_id}.light_yellow", False),
            (f"{scanner_id}.light_red", True),
        ]
    finally:
        controller.shutdown()


def test_machine_stop_passes_exact_terminal_overlay_to_event_recorder() -> None:
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    recorder = _EvidenceRecorder()
    controller._recorder = recorder
    try:
        controller._state = ScannerState.RUNNING
        controller._startup_grace_remaining = 0
        controller._startup_grace_seconds = 0.0

        controller.inject_machine_stop("DEFECTO DE PRUEBA")

        assert len(recorder.events) == 1
        event_type, reason, evidence = recorder.events[0]
        assert event_type == "machine_stop"
        assert reason == "15 faltantes persistentes"
        assert evidence["trigger_role"] == "visual_trigger"
        assert evidence["trigger_overlay"] is not None
        assert evidence["metadata"]["missing"] == 15
        assert evidence["metadata"]["visual_cause"] is True
    finally:
        controller.shutdown()


def test_nonvisual_error_records_last_frame_as_labeled_context() -> None:
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    recorder = _EvidenceRecorder()
    controller._recorder = recorder
    context = np.full((120, 160, 3), 80, dtype=np.uint8)
    controller._last_evidence_frame = context.copy()
    controller._last_evidence_frame_mono = time.monotonic()
    try:
        controller._record_terminal_event(
            "camera_loss",
            "Cámara sin señal durante 3.0 s",
            visual_cause=False,
            metadata={"missing_seconds": 3.0},
        )

        assert len(recorder.events) == 1
        _, _, evidence = recorder.events[0]
        assert evidence["trigger_role"] == "last_known_context"
        assert np.array_equal(evidence["trigger_frame"], context)
        overlay = evidence["trigger_overlay"]
        assert overlay is not None
        assert tuple(int(v) for v in overlay[10, 10]) == (0, 0, 170)
        assert evidence["metadata"]["visual_cause"] is False
    finally:
        controller.shutdown()
