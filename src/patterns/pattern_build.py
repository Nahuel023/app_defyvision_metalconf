from pathlib import Path

from src.io.load_images import load_bgr_image
from src.patterns.pattern_io import Pattern, save_pattern, pattern_path, find_pattern_path
from src.pipeline.grid_fitting import estimate_spacing, estimate_phase, assign_cells
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances


def build_pattern_from_image(
    model: str,
    img_path: Path,
    scanner_id: str | None = None,
    threshold: int | None = None,
    min_area: float | None = None,
    circularity_min: float | None = None,
) -> Path:
    tolerances = load_tolerances(model)
    threshold = int(tolerances["threshold"] if threshold is None else threshold)
    min_area = float(tolerances["min_area"] if min_area is None else min_area)
    circularity_min = float(
        tolerances["circularity_min"] if circularity_min is None else circularity_min
    )
    aspect_ratio_max = float(tolerances["aspect_ratio_max"])

    img_full = load_bgr_image(img_path)

    img_aligned, align_res = align_image_by_right_edge(img_full)
    print(f"[align-pattern] angle_deg={align_res.angle_deg:.2f} lines={align_res.used_lines}")

    roi = load_roi(model, scanner_id)
    if roi is not None:
        img = apply_roi(img_aligned, roi)
    else:
        img = img_aligned

    h, w = img.shape[:2]
    mask = preprocess_for_holes(
        img,
        threshold=threshold,
        use_channel=str(tolerances["use_channel"]),
        polarity=str(tolerances["polarity"]),
        use_clahe=bool(tolerances.get("use_clahe", False)),
        clahe_clip=float(tolerances.get("clahe_clip", 2.0)),
        clahe_tile=int(tolerances.get("clahe_tile", 8)),
        use_otsu=bool(tolerances.get("use_otsu", False)),
        use_adaptive=bool(tolerances.get("use_adaptive", False)),
        adaptive_block_size=int(tolerances.get("adaptive_block_size", 61)),
        adaptive_c=float(tolerances.get("adaptive_c", -5.0)),
        blur_ksize=int(tolerances.get("blur_ksize", 5)),
        open_ksize=int(tolerances.get("open_ksize", 3)),
        close_ksize=int(tolerances.get("close_ksize", 5)),
    )
    # Use pattern_edge_margin_px if set; falls back to edge_margin_px.
    # A larger build margin excludes edge-zone holes that only appear at the
    # reference position and disappear when the sheet shifts.
    build_margin = float(tolerances.get(
        "pattern_edge_margin_px",
        tolerances.get("edge_margin_px", 0.0),
    ))
    holes = detect_holes_from_mask(
        mask,
        min_area=min_area,
        circularity_min=circularity_min,
        aspect_ratio_max=aspect_ratio_max,
        edge_margin_px=build_margin,
    )

    points = [(h_.x, h_.y) for h_ in holes]
    radii = [h_.r for h_ in holes]

    import numpy as np
    xs = np.array([p[0] for p in points])
    ys = np.array([p[1] for p in points])
    grid_min_sp = float(tolerances.get("grid_min_spacing", 30.0))
    # Use hardcoded dx/dy from config if provided (avoids estimate_spacing bias
    # when the mixed large+small hole sample shifts the mode away from true spacing).
    dx = float(tolerances["grid_dx"]) if "grid_dx" in tolerances else estimate_spacing(xs, min_spacing=grid_min_sp)
    dy = float(tolerances["grid_dy"]) if "grid_dy" in tolerances else estimate_spacing(ys, min_spacing=grid_min_sp)
    phase_x = estimate_phase(xs, dx)
    phase_y = estimate_phase(ys, dy)
    cells = assign_cells(points, dx, dy, phase_x, phase_y)
    n_total  = len(cells)
    n_unique = len(set(cells))
    n_dup    = n_total - n_unique
    print(f"[build-pattern] {len(points)} puntos  dx={dx:.1f} dy={dy:.1f}"
          f"  phase=({phase_x:.1f},{phase_y:.1f})")
    print(f"[build-pattern] Celdas totales: {n_total}  Unicas: {n_unique}  Duplicadas: {n_dup}")

    # Stagger detection: if even-cj and odd-cj rows have different X-origins
    # (e.g. Esterilla large/small hole alternation), compute the signed offset.
    even_xs = np.array([points[i][0] for i, (_, cj) in enumerate(cells) if cj % 2 == 0])
    odd_xs  = np.array([points[i][0] for i, (_, cj) in enumerate(cells) if cj % 2 == 1])
    stagger_x_odd: float | None = None
    if len(even_xs) >= 4 and len(odd_xs) >= 4:
        ph_even = estimate_phase(even_xs, dx)
        ph_odd  = estimate_phase(odd_xs, dx)
        stagger = float(((ph_odd - ph_even) + dx / 2.0) % dx - dx / 2.0)
        if abs(stagger) > 2.0:  # only store if meaningful (>2px)
            stagger_x_odd = stagger
            print(f"[build-pattern] Stagger detectado: phase_even={ph_even:.1f} phase_odd={ph_odd:.1f}"
                  f" stagger_x_odd={stagger:.1f}px")
    if n_dup > 0:
        from collections import Counter
        dup_cells = sorted(
            [(c, cnt) for c, cnt in Counter(cells).items() if cnt > 1],
            key=lambda x: x[0][1],
        )
        print(f"[build-pattern] ADVERTENCIA: {n_dup} celda(s) duplicada(s) detectadas:")
        for c, cnt in dup_cells:
            idxs = [i for i, cc in enumerate(cells) if cc == c]
            pts  = [(round(points[i][0], 1), round(points[i][1], 1)) for i in idxs]
            print(f"  (ci={c[0]}, cj={c[1]}) x{cnt}: {pts}")

    pat = Pattern(
        model=model, image_size=(w, h), points=points, radii=radii,
        dx=dx, dy=dy, phase_x=phase_x, phase_y=phase_y, cells=cells,
        stagger_x_odd=stagger_x_odd,
    )
    out = pattern_path(model, scanner_id)
    save_pattern(pat, out)
    return out
