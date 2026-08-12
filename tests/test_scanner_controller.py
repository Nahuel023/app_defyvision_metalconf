from src.controller.scanner_controller import ScannerController
from src.utils.state import OperationMode, ScannerState


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
