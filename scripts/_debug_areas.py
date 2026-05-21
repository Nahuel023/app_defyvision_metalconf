# -*- coding: utf-8 -*-
"""Histograma de areas de blobs para diagnosticar min_area optimo."""
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
    areas = sorted([cv2.contourArea(c) for c in cnts if cv2.contourArea(c) > 10])
    bins = [10, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 9999]
    print(f"{fname}:")
    for i in range(len(bins) - 1):
        n = sum(1 for a in areas if bins[i] <= a < bins[i + 1])
        if n:
            bar = "#" * min(n, 50)
            print(f"  {bins[i]:5d}-{bins[i+1]:5d}px2: {n:3d}  {bar}")
    print(f"  TOTAL blobs>10: {len(areas)}")
    for threshold in [150, 200, 250, 300]:
        n = sum(1 for a in areas if a >= threshold)
        print(f"  min_area>={threshold}: {n} blobs")
    print()
