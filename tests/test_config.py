from pathlib import Path

import yaml

from src.utils.config import (
    load_tolerances,
    save_model_overrides,
    save_scanner_overrides,
    save_tolerances,
)


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


def test_scanner_model_overrides_do_not_leak_between_materials(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    save_tolerances({
        "threshold": 100,
        "models": {
            "modelo_A": {"threshold": 180},
            "modelo_B": {"threshold": 120},
        },
    })
    (config_dir / "io_map.yaml").write_text(
        "scanner_2:\n"
        "  inspection_models:\n"
        "    modelo_B:\n"
        "      threshold: 145\n"
        "  inputs: {}\n",
        encoding="utf-8",
    )

    assert load_tolerances("modelo_A", "scanner_2")["threshold"] == 180
    assert load_tolerances("modelo_B", "scanner_2")["threshold"] == 145

    save_scanner_overrides("scanner_2", {"threshold": 150}, model="modelo_B")
    assert load_tolerances("modelo_A", "scanner_2")["threshold"] == 180
    assert load_tolerances("modelo_B", "scanner_2")["threshold"] == 150


def test_scanner2_microperforado_profile_keeps_safe_live_tracking() -> None:
    cfg = load_tolerances(model="modelo_B", scanner_id="scanner_2")
    esterilla = load_tolerances(model="modelo_A", scanner_id="scanner_2")

    assert cfg["grid_allow_row_parity_flip"] is True
    assert cfg["grid_parity_selection_tol_px"] < cfg["grid_dy"] / 2.0
    assert cfg["frame_missing_nok_threshold"] == 3
    assert cfg["machine_stop_min_missing"] == 1
    assert cfg["machine_stop_require_frame_nok"] is False
    # ROI FIJO: el auto-recentrado quedo desactivado en ambos scanners para que la
    # posicion del ROI no se corra ni se re-guarde al reiniciar (pedido operativo).
    assert cfg["roi_recenter_enabled"] is False
    assert cfg["roi_recenter_mode"] == "move"
    assert cfg["roi_recenter_require_edge_missing"] is False
    assert cfg["roi_precal_enabled"] is False
    assert cfg["roi_recenter_cooldown_frames"] == cfg["roi_recenter_cooldown_max_frames"]
    assert esterilla["grid_dx"] == 41.9
    assert esterilla["grid_dy"] == 22.46
    assert esterilla["grid_stagger_x_odd"] == 20.95
    assert esterilla["grid_allow_row_parity_flip"] is True
    assert esterilla["grid_parity_selection_tol_px"] < esterilla["grid_dy"] / 2.0
    assert esterilla["blur_score_min"] == 255.0
    assert esterilla["tol_xy_px"] == 18.0
    assert esterilla["roi_recenter_enabled"] is False
    assert esterilla["stop_min_frames"] == 3
    assert esterilla["low_quality_stop_frames"] == 16
    # Una captura nueva de la camara no implica material nuevo: Esterilla debe
    # ignorar el ruido/JPEG mientras la chapa permanece en la misma posicion.
    assert esterilla["continuous_position_threshold"] == 3.0


def test_both_microperforado_scanners_require_three_alignment_frames() -> None:
    for scanner_id in ("scanner_1", "scanner_2"):
        cfg = load_tolerances(model="modelo_B", scanner_id=scanner_id)
        assert cfg["pattern_align_stop_frames"] >= 3
        assert cfg["pattern_align_severe_abs_max_px"] == 0.0


def test_every_production_pattern_uses_safe_stop_defaults() -> None:
    for scanner_id in ("scanner_1", "scanner_2"):
        for model in ("modelo_A", "modelo_B"):
            cfg = load_tolerances(model=model, scanner_id=scanner_id)
            assert cfg["pattern_align_stop_frames"] >= 3
            assert cfg["consecutive_nok_frames"] >= 500


def test_tolerance_window_cannot_reenable_single_frame_alignment_stop() -> None:
    from src.ui.tolerance_window import _PARAMS

    params = {item["key"]: item for item in _PARAMS}
    assert params["pattern_align_stop_frames"]["vmin"] == 3
    assert params["pattern_align_severe_abs_max_px"]["vmin"] == 0.0
