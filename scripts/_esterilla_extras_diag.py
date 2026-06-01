"""Collect extra-hole positions across unique frames to see if they are
consistent edge holes (→ register) or scattered spurious (→ keep flagged)."""
import sys, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from src.inspection import inspect_image
from src.patterns.roi import load_roi

MODEL, SCANNER = "modelo_A", "scanner_2"
FOLDER = Path(r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO")
roi = load_roi(MODEL, SCANNER)
roi_w = roi.w if roi else 380
roi_h = roi.h if roi else 1080

files = sorted(p for p in FOLDER.iterdir() if p.suffix.lower() == ".png")
seen, uniq = set(), []
for p in files:
    h = hashlib.md5(p.read_bytes()).hexdigest()
    if h not in seen:
        seen.add(h); uniq.append(p)

all_ex = []
per_frame = []
for p in uniq:
    r = inspect_image(MODEL, p, scanner_id=SCANNER)
    ex = r.report.extra_points
    per_frame.append((p.stem, len(ex)))
    for (x, y) in ex:
        all_ex.append((x, y))

print(f"frames={len(uniq)}  total extras={len(all_ex)}  avg={len(all_ex)/len(uniq):.1f}")
arr = np.array(all_ex)
# zone classification within ROI (w x h)
def zone(x, y):
    zx = "L" if x < 0.18*roi_w else ("R" if x > 0.82*roi_w else "C")
    zy = "T" if y < 0.10*roi_h else ("B" if y > 0.90*roi_h else "M")
    return zy + zx
from collections import Counter
zc = Counter(zone(x, y) for x, y in all_ex)
print("zonas (Y:T/M/B  X:L/C/R):", dict(zc))
print(f"x range {arr[:,0].min():.0f}..{arr[:,0].max():.0f}  y range {arr[:,1].min():.0f}..{arr[:,1].max():.0f}")

# cluster by rounding to 30px grid to find recurrent positions
keyc = Counter((round(x/30)*30, round(y/30)*30) for x, y in all_ex)
recurrent = [(k, c) for k, c in keyc.items() if c >= len(uniq)*0.5]
print(f"\nposiciones recurrentes (en >=50% de frames):")
for (x, y), c in sorted(recurrent, key=lambda t: -t[1]):
    print(f"  ~({x},{y})  en {c}/{len(uniq)} frames")
