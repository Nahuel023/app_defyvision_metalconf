"""Find best reference frame in new recordings for pattern rebuild."""
from pathlib import Path
from src.inspection import inspect_image

candidates = []

for rec_name in ["20260509_125325", "20260509_125449"]:
    rec = Path(f"data/recordings/{rec_name}")
    for p in sorted(rec.glob("*.png")):
        r = inspect_image("modelo_A", p)
        if r.shift_xy is None:
            continue
        sx, sy = r.shift_xy
        shift_mag = (sx**2 + sy**2) ** 0.5
        missing = len(r.report.missing_points)
        det = len(r.holes)
        candidates.append((shift_mag, missing, -det, p, r))

candidates.sort()

print("Top 10 frames por menor desplazamiento:")
print(f"{'Frame':<55} {'det':>4} {'missing':>7} {'shift(x,y)':>18} {'status'}")
print("-" * 100)
for shift_mag, missing, neg_det, p, r in candidates[:10]:
    sx, sy = r.shift_xy
    det = -neg_det
    print(f"{str(p):<55} {det:>4} {missing:>7} ({sx:+6.1f},{sy:+6.1f})   {r.status}")

print()
best = candidates[0]
_, _, _, p, r = best
sx, sy = r.shift_xy
print(f"RECOMENDADO: {p}")
print(f"  det={len(r.holes)}  missing={len(r.report.missing_points)}"
      f"  shift=({sx:+.1f},{sy:+.1f})  status={r.status}")
