import threading

from src.controller.system import InspectionSystem


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def stop(self) -> None:
        self.calls += 1

    def shutdown(self) -> None:
        self.calls += 1

    def disconnect(self) -> None:
        self.calls += 1


class _FakeIO:
    def __init__(self) -> None:
        self.critical_calls = 0
        self.batch_calls = 0

    def write_critical(self, *_args, **_kwargs) -> bool:
        self.critical_calls += 1
        return True

    def write_batch(self, *_args, **_kwargs) -> bool:
        self.batch_calls += 1
        return True


def test_shutdown_is_idempotent() -> None:
    system = InspectionSystem.__new__(InspectionSystem)
    system._shutdown_lock = threading.Lock()
    system._shutdown_started = False
    system._recorder = _Counter()
    scanner = _Counter()
    camera = _Counter()
    system._scanners = {"scanner_1": scanner}
    system._cameras = {"scanner_1": camera}
    system._client = _Counter()
    system._io = _FakeIO()

    system.shutdown()
    system.shutdown()

    assert system._recorder.calls == 1
    assert scanner.calls == 1
    assert camera.calls == 1
    assert system._client.calls == 1
    assert system._io.critical_calls == 1
    assert system._io.batch_calls == 1
