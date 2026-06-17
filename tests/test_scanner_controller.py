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

    def scanner_config(self, scanner_id: str) -> dict:
        return self._scanner_cfg[scanner_id]

    def read(self, signal: str):
        return None

    def write(self, signal: str, value: bool) -> None:
        self.writes.append((signal, value))

    def write_batch(self, batch: list[tuple[str, bool]]) -> None:
        self.batches.append(list(batch))


class _FakeCamera:
    def __init__(self) -> None:
        self.is_running = False
        self.start_calls = 0

    def start(self) -> bool:
        self.start_calls += 1
        self.is_running = True
        return True


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
