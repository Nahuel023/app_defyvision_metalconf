"""Analiza circularidad y aspect-ratio de blobs en frames con blur."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import cv2, numpy as np
from src.patterns.roi import apply_roi, load_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances

tol = load_tolerances("modelo_B")
roi = load_roi("modelo_B", None)

for fname in ["frame_0001.png", "frame_0066.png", "frame_0067.png"]:
    img = cv2.imread(f"C:/Tadeo/METALCONF/imagenes_prueba_METALCONF/20260519_121741/{fname}")
    img_al, _ = align_image_by_right_edge(img)
    img_r = apply_roi(img_al, roi) if roi else img_al
    mask = preprocess_for_holes(img_r, threshold=int(tol["threshold"]),
        use_channel=tol["use_channel"], polarity=tol["polarity"],
        use_clahe=bool(tol.get("use_clahe")), clahe_clip=float(tol.get("clahe_clip", 2)),
        clahe_tile=int(tol.get("clahe_tile", 8)), use_otsu=bool(tol.get("use_otsu")))

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    circs, aspects, areas = [], [], []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 50:
            continue
        perim = cv2.arcLength(c, True)
        circ = (4 * np.pi * area / (perim ** 2)) if perim > 0 else 0
        rect = cv2.minAreaRect(c)
        w, h = sorted(rect[1])  # w<=h
        aspect = h / max(w, 1)
        circs.append(circ)
        aspects.append(aspect)
        areas.append(area)

    circs = np.array(circs)
    aspects = np.array(aspects)
    areas = np.array(areas)

    print(f"\n=== {fname} === total blobs(>50px2)={len(circs)}")
    print(f"  Circularidad: min={circs.min():.2f}  p25={np.percentile(circs,25):.2f}  "
          f"median={np.median(circs):.2f}  p75={np.percentile(circs,75):.2f}  max={circs.max():.2f}")
    print(f"  AspectRatio:  min={aspects.min():.2f}  p25={np.percentile(aspects,25):.2f}  "
          f"median={np.median(aspects):.2f}  p75={np.percentile(aspects,75):.2f}  max={aspects.max():.2f}")

    # Cuántos pasan con distintos umbrales
    for cmin in [0.75, 0.60, 0.50, 0.40]:
        for amax in [2.0, 3.0, 4.0]:
            n = int(((circs >= cmin) & (aspects <= amax) & (areas >= 300)).sum())
            print(f"    circ>={cmin:.2f} aspect<={amax:.1f} area>=300 = {n} holes")
