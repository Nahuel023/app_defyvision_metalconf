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

for scanner_id in ("scanner_1", "scanner_2"):
    for model in ("modelo_A", "modelo_B"):
        cfg = load_tolerances(model, scanner_id=scanner_id)
        assert int(cfg["consecutive_nok_frames"]) == 3
        assert int(cfg["stop_min_frames"]) == 3
        assert cfg["machine_jam_enabled"] is True
        assert float(cfg["machine_jam_arm_s"]) == 60.0
        assert float(cfg["machine_jam_timeout_s"]) == 22.0
        print(
            f"{scanner_id}/{model}  parada NOK = "
            f"{cfg['consecutive_nok_frames']} / piso {cfg['stop_min_frames']}  "
            f"atasco={cfg['machine_jam_arm_s']}+{cfg['machine_jam_timeout_s']}s"
        )
