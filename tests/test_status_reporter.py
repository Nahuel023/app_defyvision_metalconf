import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from src.metrics.status_reporter import (
    STATUS_IDLE,
    STATUS_IN_USE,
    STATUS_OFF,
    StatusReporter,
)


class _FakeSystem:
    def __init__(self, states):
        self.states = dict(states)

    def scanner_ids(self):
        return list(self.states)

    def scanner(self, sid):
        return SimpleNamespace(state=SimpleNamespace(value=self.states[sid]))


@pytest.fixture
def server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/exec", received
    httpd.shutdown()
    httpd.server_close()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_machine_status_mapping():
    assert StatusReporter.machine_status({"s1": "running", "s2": "idle"}) == STATUS_IN_USE
    assert StatusReporter.machine_status({"s1": "idle", "s2": "stopped"}) == STATUS_IDLE
    assert StatusReporter.machine_status({"s1": "fault", "s2": "error"}) == STATUS_IDLE
    assert StatusReporter.machine_status({}) == STATUS_IDLE


def test_from_app_config_disabled_or_without_url(tmp_path):
    cfg = tmp_path / "app.yaml"
    assert StatusReporter.from_app_config(cfg) is None

    cfg.write_text("status_report:\n  enabled: false\n  url: http://x/exec\n", encoding="utf-8")
    assert StatusReporter.from_app_config(cfg) is None

    cfg.write_text("status_report:\n  enabled: true\n  url: ''\n", encoding="utf-8")
    assert StatusReporter.from_app_config(cfg) is None

    cfg.write_text("status_report: [roto\n", encoding="utf-8")
    assert StatusReporter.from_app_config(cfg) is None

    cfg.write_text(
        "status_report:\n  enabled: true\n  url: http://x/exec\n  machine: M1\n",
        encoding="utf-8",
    )
    reporter = StatusReporter.from_app_config(cfg)
    assert reporter is not None and reporter._machine == "M1"


def test_sends_on_start_on_change_and_off_on_stop(server):
    url, received = server
    system = _FakeSystem({"scanner_1": "idle", "scanner_2": "idle"})
    reporter = StatusReporter(url, machine="M1", token="abc", poll_s=0.05)
    reporter.start(system)
    try:
        assert _wait_for(lambda: len(received) >= 1)
        first = received[0]
        assert first["status"] == STATUS_IDLE
        assert first["machine"] == "M1"
        assert first["token"] == "abc"
        assert first["scanners"] == {"scanner_1": "idle", "scanner_2": "idle"}

        system.states["scanner_2"] = "running"
        assert _wait_for(lambda: any(p["status"] == STATUS_IN_USE for p in received))
    finally:
        reporter.stop(send_off=True)
    assert received[-1]["status"] == STATUS_OFF


def test_unreachable_url_never_raises():
    reporter = StatusReporter("http://127.0.0.1:9/exec", timeout_s=1.0, poll_s=0.05)
    reporter.start(_FakeSystem({"scanner_1": "running"}))
    time.sleep(0.2)
    reporter.stop(send_off=True)
    assert reporter._last_error_logged is not None
