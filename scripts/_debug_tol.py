"""Test different tol_xy values for modelo_A."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from pathlib import Path
from src.inspection import _inspect_bgr
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.utils.config import load_tolerances
import cv2

MODEL = "modelo_A"
SCANNER = "scanner_2"
FOLDER = Path(r'C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF')

frames = sorted(FOLDER.glob("*.png"))[:10]  # first 10

for tol in [18, 22, 25, 28]:
    total_missing = 0
    for f in frames:
        tolerances = load_tolerances(MODEL)
        tolerances = dict(tolerances)
        tolerances['tol_xy_px'] = float(tol)
        img_full = cv2.imread(str(f))
        img_aligned, _ = align_image_by_right_edge(img_full)
        roi = load_roi(MODEL, SCANNER)
        img = apply_roi(img_aligned, roi) if roi else img_aligned
        pat = load_pattern(find_pattern_path(MODEL, SCANNER))
        result = _inspect_bgr(img, pat, tolerances)
        total_missing += result.report.missing
    avg = total_missing / len(frames)
    print(f"tol_xy={tol}: avg_missing={avg:.1f} over {len(frames)} frames")
