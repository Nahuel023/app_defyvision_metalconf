from pathlib import Path

import yaml

from src.plc.io_map import IOMap


class _FakeClient:
    def __init__(self) -> None:
        self.batch_calls: list[tuple[int, list[bool]]] = []

    def write_coils_batch(self, offset: int, values: list[bool]) -> bool:
        self.batch_calls.append((offset, values))
        return True

    def write_coil(self, _offset: int, _value: bool) -> bool:
        return True


def test_partial_safe_mode_batch_reports_failure(tmp_path: Path) -> None:
    cfg = {
        "plc": {"ip": "127.0.0.1"},
        "scanner_1": {
            "outputs": {"solenoid": 0, "light_green": 1},
        },
    }
    path = tmp_path / "io_map.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    client = _FakeClient()
    io = IOMap(client, path)

    ok = io.write_batch([
        ("scanner_1.solenoid", True),
        ("scanner_1.light_green", True),
    ])

    assert ok is False
    assert client.batch_calls == [(1, [True])]
