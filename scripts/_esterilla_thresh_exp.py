"""Compare thresholding strategies for esterilla hole detection."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2, numpy as np

from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.utils.config import load_tolerances

MODEL, SCANNER = "modelo_A", "scanner_2"
OUT = r"C:\Tadeo\METALCONF\app_defyvision_metalconf\data"
frames = sys.argv[1:] or [
    r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0162.png",
    r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0177.png",
    r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0172.png",
]
tol = load_tolerances(MODEL)
roi = load_roi(MODEL, SCANNER)
em = float(tol.get("edge_margin_px", 3.0))
mn, cm, ar = float(tol["min_area"]), float(tol["circularity_min"]), float(tol["aspect_ratio_max"])

def chan(img):
    c = img[:, :, 2]  # R channel
    return c

def det(mask):
    return detect_holes_from_mask(mask, min_area=mn, circularity_min=cm, aspect_ratio_max=ar, edge_margin_px=em)

def make_masks(img):
    c = chan(img)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    cc = clahe.apply(c)
    blur = cv2.GaussianBlur(cc, (5,5), 0)
    masks = {}
    # A current: clahe + otsu global
    _, A = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # B adaptive gaussian
    B = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 61, -5)
    # C clahe clip 5 + otsu
    cc5 = cv2.createCLAHE(clipLimit=5.0, tileGridSize=(8,8)).apply(c)
    _, C = cv2.threshold(cv2.GaussianBlur(cc5,(5,5),0), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    for k,m in (("A_otsu",A),("B_adapt",B),("C_clahe5",C)):
        ko = np.ones((3,3),np.uint8); kc = np.ones((5,5),np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ko, 1)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kc, 1)
        masks[k]=m
    return masks

for f in frames:
    img_full = load_bgr_image(Path(f))
    img_aligned,_ = align_image_by_right_edge(img_full)
    img = apply_roi(img_aligned, roi) if roi is not None else img_aligned
    name = Path(f).stem
    masks = make_masks(img)
    line = f"{name}: "
    for k,m in masks.items():
        line += f"{k}={len(det(m))}  "
    print(line)
    # save overlays for first frame
    if f == frames[0]:
        for k,m in masks.items():
            ov = img.copy()
            for hh in det(m):
                cv2.circle(ov,(int(hh.x),int(hh.y)),int(hh.r),(0,255,0),2)
            cv2.imwrite(OUT + rf"\debug_thr_{k}.png", ov)
            cv2.imwrite(OUT + rf"\debug_thrmask_{k}.png", m)
print("saved debug_thr_*.png for first frame")
