"""
Análisis comparativo de grabaciones por perfil de cámara.
Corre inspect_image en todos los frames de cada grabación y reporta estadísticas.
"""
import json
import sys
import warnings
from pathlib import Path
import numpy as np

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.inspection import inspect_image

RECORDINGS_NEW = [
    "20260513_103221",
    "20260513_103300",
    "20260513_103419",
    "20260513_103507",
    "20260513_103541",
    "20260513_103559",
    "20260513_103632",
]

MODEL = "modelo_A"


def laplacian_var(path: Path) -> float:
    import cv2
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return -1.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def optical_flow_shift(prev_gray, curr_gray) -> float:
    import cv2
    if prev_gray is None or curr_gray is None:
        return 0.0
    try:
        pts = cv2.goodFeaturesToTrack(prev_gray, maxCorners=200, qualityLevel=0.01, minDistance=7)
        if pts is None or len(pts) < 10:
            return 0.0
        new_pts, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, pts, None)
        good = status.ravel() == 1
        if good.sum() < 10:
            return 0.0
        dy = (new_pts[good] - pts[good])[:, 0, 1]
        return float(np.median(np.abs(dy)))
    except Exception:
        return 0.0


def analyze_recording(rec_id: str):
    import cv2
    rec_dir = ROOT / "data" / "recordings" / rec_id
    frames = sorted(rec_dir.glob("frame_*.png"))
    if not frames:
        frames = sorted(rec_dir.glob("*.png"))
    if not frames:
        print(f"\n[{rec_id}] NO FRAMES")
        return

    # Read meta
    meta_path = rec_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    cam_fps = meta.get("cam_fps_real", "?")
    cam_settings = meta.get("camera_settings", {})
    profile = meta.get("camera_profile", "?")

    missing_list = []
    detected_list = []
    shifts_x = []
    shifts_y = []
    lapvar_list = []
    ok_count = 0
    nok_count = 0
    cur_streak_status = None
    status_streak = 0
    max_nok_streak = 0
    prev_gray = None
    flow_shifts = []
    align_fails = 0

    for i, fp in enumerate(frames):
        try:
            result = inspect_image(MODEL, fp, save=False)
        except Exception as e:
            continue

        missing = result.report.missing
        detected = result.report.detected
        status = result.status
        missing_list.append(missing)
        detected_list.append(detected)

        if status == "OK":
            ok_count += 1
            if cur_streak_status == "NOK":
                max_nok_streak = max(max_nok_streak, status_streak)
            cur_streak_status = "OK"
            status_streak = 1 if cur_streak_status != "OK" else status_streak + 1
        else:
            nok_count += 1
            if cur_streak_status == "NOK":
                status_streak += 1
            else:
                status_streak = 1
                cur_streak_status = "NOK"
            max_nok_streak = max(max_nok_streak, status_streak)

        if result.shift_xy is not None:
            shifts_x.append(result.shift_xy[0])
            shifts_y.append(result.shift_xy[1])
        else:
            align_fails += 1

        # Laplacian (every 5th frame for speed)
        if i % 5 == 0:
            lv = laplacian_var(fp)
            lapvar_list.append(lv)

        # Optical flow
        gray = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
        if gray is not None:
            flow = optical_flow_shift(prev_gray, gray)
            if flow > 0:
                flow_shifts.append(flow)
            prev_gray = gray

    n = len(missing_list)
    if n == 0:
        print(f"\n[{rec_id}] ANALYSIS FAILED")
        return

    ok_pct = 100.0 * ok_count / n
    avg_missing = np.mean(missing_list)
    min_missing = int(np.min(missing_list))
    max_missing = int(np.max(missing_list))
    std_missing = np.std(missing_list)
    avg_detected = np.mean(detected_list)

    print(f"\n{'='*72}")
    print(f"  {rec_id}  [{profile}]")
    print(f"{'='*72}")
    fps_str = f"{cam_fps:.2f}" if isinstance(cam_fps, float) else str(cam_fps)
    print(f"  Frames: {n}   cam_fps_real: {fps_str}  (timer_fps: {meta.get('fps','?')})")
    exp = cam_settings.get('exposure', '?? (no leído)')
    print(f"  exposure:{exp}  focus:{cam_settings.get('focus','?')}  gain:{cam_settings.get('gain','?')}  brightness:{cam_settings.get('brightness','?')}  contrast:{cam_settings.get('contrast','?')}  sharpness:{cam_settings.get('sharpness','?')}")
    print(f"  OK: {ok_count} ({ok_pct:.1f}%)  NOK: {nok_count}  align_fails: {align_fails}  max_nok_streak: {max_nok_streak}")
    print(f"  Missing  → avg:{avg_missing:.1f}  min:{min_missing}  max:{max_missing}  std:{std_missing:.1f}")
    print(f"  Detected → avg:{avg_detected:.1f}")

    if shifts_x:
        print(f"  Shift X  → avg:{np.mean(shifts_x):+.1f}  std:{np.std(shifts_x):.1f}  absmax:{np.max(np.abs(shifts_x)):.1f}")
        print(f"  Shift Y  → avg:{np.mean(shifts_y):+.1f}  std:{np.std(shifts_y):.1f}  absmax:{np.max(np.abs(shifts_y)):.1f}")

    if lapvar_list:
        print(f"  Laplacian→ avg:{np.mean(lapvar_list):.1f}  min:{np.min(lapvar_list):.1f}  max:{np.max(lapvar_list):.1f}  std:{np.std(lapvar_list):.1f}")

    if flow_shifts:
        print(f"  FlowShift→ avg:{np.mean(flow_shifts):.1f}px  max:{np.max(flow_shifts):.1f}px  p95:{np.percentile(flow_shifts,95):.1f}px  (px/frame)")

    # Distribution of missing
    b = {"0": 0, "1-5": 0, "6-15": 0, "16-30": 0, ">30": 0}
    for m in missing_list:
        if m == 0: b["0"] += 1
        elif m <= 5: b["1-5"] += 1
        elif m <= 15: b["6-15"] += 1
        elif m <= 30: b["16-30"] += 1
        else: b[">30"] += 1
    total = sum(b.values())
    pcts = {k: f"{v}({100*v//total}%)" for k,v in b.items()}
    print(f"  MissDist → {pcts}")


if __name__ == "__main__":
    print("=" * 72)
    print(" Análisis comparativo de perfiles de cámara — modelo_A (Esterilla)")
    print(" Material INVERTIDO — simulación avance continuo — scanner_2")
    print("=" * 72)
    for rec_id in RECORDINGS_NEW:
        analyze_recording(rec_id)
    print(f"\n{'='*72}\nFIN\n")
