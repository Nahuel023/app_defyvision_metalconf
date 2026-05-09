"""Check NOK-at-rest streak detail for all recordings."""
from pathlib import Path
from src.inspection import inspect_image

THRESHOLD = 8
REST_SHIFT_MAX = 50.0

base = Path("data/recordings")
recs = sorted(p for p in base.iterdir() if p.is_dir())

for rec in recs:
    frames = sorted(rec.glob("*.png"))
    results = [inspect_image("modelo_A", p) for p in frames]

    streak = 0
    max_streak = 0
    max_streak_end = 0

    print(f"\n{'='*60}")
    print(f"{rec.name}  ({len(results)} frames)")
    print(f"{'='*60}")

    nok_rest = []
    for i, r in enumerate(results):
        sy = r.shift_xy[1] if r.shift_xy else 0.0
        sx = r.shift_xy[0] if r.shift_xy else 0.0
        at_rest = abs(sy) <= REST_SHIFT_MAX

        if at_rest:
            if r.status == "NOK":
                streak += 1
                if streak > max_streak:
                    max_streak = streak
                    max_streak_end = i
                miss_y = sorted(round(y, 0) for _, y in r.report.missing_points)
                nok_rest.append((i, r, sx, sy, streak, miss_y))
            else:
                streak = 0

    for i, r, sx, sy, stk, miss_y in nok_rest:
        print(f"  frame_{i:04d}: det={len(r.holes):3d}  missing={len(r.report.missing_points)}"
              f"  shift=({sx:+5.1f},{sy:+5.1f})  streak={stk}  miss_Y={miss_y[:6]}")

    if not nok_rest:
        print("  Sin NOK en reposo.")

    fault = max_streak >= THRESHOLD
    print(f"\n  Max racha NOK en reposo : {max_streak}")
    print(f"  Dispararía FAULT (>={THRESHOLD}): {'SI !!!' if fault else 'NO'}")
