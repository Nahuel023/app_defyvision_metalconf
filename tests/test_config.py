from pathlib import Path

import yaml

from src.utils.config import load_tolerances, save_model_overrides, save_tolerances


def test_load_tolerances_cache_invalidation_after_save(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()

    save_tolerances({"threshold": 123})
    first = load_tolerances()
    assert first["threshold"] == 123

    save_tolerances({"threshold": 234})
    second = load_tolerances()
    assert second["threshold"] == 234


def test_load_tolerances_picks_up_io_map_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    save_tolerances({"threshold": 120})
    io_map_path = config_dir / "io_map.yaml"
    io_map_path.write_text(
        yaml.safe_dump(
            {
                "plc": {"ip": "127.0.0.1", "port": 502},
                "scanner_1": {"inspection": {"threshold": 150}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    first = load_tolerances(scanner_id="scanner_1")
    assert first["threshold"] == 150

    io_map_path.write_text(
        yaml.safe_dump(
            {
                "plc": {"ip": "127.0.0.1", "port": 502},
                "scanner_1": {"inspection": {"threshold": 180}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    second = load_tolerances(scanner_id="scanner_1")
    assert second["threshold"] == 180


def test_save_model_overrides_invalidates_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()

    save_tolerances({"threshold": 100})
    assert load_tolerances(model="modelo_A")["threshold"] == 100

    save_model_overrides("modelo_A", {"threshold": 210})
    assert load_tolerances(model="modelo_A")["threshold"] == 210
