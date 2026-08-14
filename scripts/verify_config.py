import ast, sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, ".")

ast.parse(open("src/controller/scanner_controller.py", encoding="utf-8").read())
print("scanner_controller.py: OK")

from src.utils.config import load_tolerances
tA = load_tolerances("modelo_A")
tB = load_tolerances("modelo_B")
print(f"modelo_A  continuous_position_threshold = {tA.get('continuous_position_threshold')}")
print(f"modelo_B  continuous_position_threshold = {tB.get('continuous_position_threshold')}")
print(f"modelo_B  consecutive_nok_frames        = {tB.get('consecutive_nok_frames')}")
print(f"modelo_B  edge_margin_px                = {tB.get('edge_margin_px')}")
print(f"modelo_B  tol_xy_px                     = {tB.get('tol_xy_px')}")

expected_stops = {
    ("scanner_1", "modelo_A"): (3, 3, 3),
    ("scanner_1", "modelo_B"): (5, 5, 5),
    ("scanner_2", "modelo_A"): (3, 3, 3),
    ("scanner_2", "modelo_B"): (3, 4, 4),
}
for (scanner_id, model), (nok_frames, machine_frames, align_frames) in expected_stops.items():
    cfg = load_tolerances(model, scanner_id=scanner_id)
    assert int(cfg["consecutive_nok_frames"]) == nok_frames
    assert int(cfg["machine_stop_missing_frames"]) == machine_frames
    assert int(cfg["pattern_align_stop_frames"]) == align_frames
    assert int(cfg["stop_min_frames"]) == 3
    assert cfg["machine_jam_enabled"] is True
    assert float(cfg["machine_jam_arm_s"]) == 60.0
    assert float(cfg["machine_jam_timeout_s"]) == 22.0
    print(
        f"{scanner_id}/{model}  parada NOK = "
        f"{cfg['consecutive_nok_frames']} / machine {cfg['machine_stop_missing_frames']} "
        f"/ align {cfg['pattern_align_stop_frames']} / piso {cfg['stop_min_frames']}  "
        f"atasco={cfg['machine_jam_arm_s']}+{cfg['machine_jam_timeout_s']}s"
    )
