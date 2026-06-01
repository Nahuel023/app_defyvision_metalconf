"""Evaluate modelo_A (esterilla) on the UNIQUE frames of a folder (dedup by MD5)."""
import sys, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from src.inspection import inspect_image
from src.utils.config import load_tolerances

FOLDER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO")
MODEL, SCANNER = "modelo_A", "scanner_2"

files = sorted(p for p in FOLDER.iterdir() if p.suffix.lower() == ".png")
seen, uniques = {}, []
for p in files:
    hd = hashlib.md5(p.read_bytes()).hexdigest()
    if hd not in seen:
        seen[hd] = p.name
        uniques.append(p)
print(f"total={len(files)} unique={len(uniques)}")

tol = load_tolerances(MODEL)
nok_thr = float(tol.get("frame_missing_nok_threshold", 35))
print(f"nok_threshold(missing)={nok_thr}  grid_dx={tol.get('grid_dx')} grid_dy={tol.get('grid_dy')} tol_xy={tol.get('tol_xy_px')}")

rows = []
for p in uniques:
    r = inspect_image(MODEL, p, scanner_id=SCANNER)
    rep = r.report
    rows.append((p.name, rep.expected, rep.detected, rep.missing, rep.extra,
                 r.detection_ratio, r.status))

miss = np.array([x[3] for x in rows]); extra = np.array([x[4] for x in rows])
ratio = np.array([x[5] for x in rows])
nok = sum(1 for x in rows if x[3] >= nok_thr)
print(f"\n{'frame':<18}{'exp':>5}{'det':>5}{'miss':>6}{'extra':>6}{'ratio':>7}  status")
for name, e, d, m, x, rt, st in rows:
    flag = "  <-- NOK" if m >= nok_thr else ""
    print(f"{name:<18}{e:>5}{d:>5}{m:>6}{x:>6}{rt*100:>6.0f}%  {st}{flag}")
print(f"\nAGGREGATE over {len(rows)} unique frames:")
print(f"  missing : min={miss.min()} max={miss.max()} mean={miss.mean():.1f} median={np.median(miss):.0f}")
print(f"  extra   : min={extra.min()} max={extra.max()} mean={extra.mean():.1f}")
print(f"  ratio   : mean={ratio.mean()*100:.0f}%")
print(f"  NOK(missing>={nok_thr:.0f}): {nok}/{len(rows)}")
