from pathlib import Path

from src.io.load_images import load_bgr_image
from src.patterns.pattern_io import Pattern, save_pattern, pattern_path, find_pattern_path
from src.pipeline.grid_fitting import estimate_spacing, estimate_phase, assign_cells
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import EdgeAlignResult, align_image_by_right_edge
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
    tolerances = load_tolerances(model, scanner_id=scanner_id)
    threshold = int(tolerances["threshold"] if threshold is None else threshold)
    min_area = float(tolerances["min_area"] if min_area is None else min_area)
    circularity_min = float(
        tolerances["circularity_min"] if circularity_min is None else circularity_min
    )
    aspect_ratio_max = float(tolerances["aspect_ratio_max"])

    img_full = load_bgr_image(img_path)

    edge_align_enabled = bool(tolerances.get("edge_align_enabled", True))
    if edge_align_enabled:
        img_aligned, align_res = align_image_by_right_edge(img_full)
    else:
        img_aligned = img_full
        align_res = EdgeAlignResult(angle_deg=0.0, used_lines=0)
    print(f"[align-pattern] angle_deg={align_res.angle_deg:.2f} lines={align_res.used_lines}")

    roi = load_roi(model, scanner_id)
    if roi is not None:
        img = apply_roi(img_aligned, roi)
    else:
        img = img_aligned

    h, w = img.shape[:2]
    build_use_clahe = bool(tolerances.get(
        "pattern_use_clahe",
        tolerances.get("use_clahe", False),
    ))

    mask = preprocess_for_holes(
        img,
        threshold=threshold,
        use_channel=str(tolerances["use_channel"]),
        polarity=str(tolerances["polarity"]),
        use_clahe=build_use_clahe,
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
    # Supports per-side overrides: pattern_edge_margin_left/right/top/bottom_px.
    # A larger build margin excludes edge-zone holes that only appear at the
    # reference position and disappear when the sheet shifts.
    build_margin = float(tolerances.get(
        "pattern_edge_margin_px",
        tolerances.get("edge_margin_px", 0.0),
    ))
    m_left   = float(tolerances.get("pattern_edge_margin_left_px",   build_margin))
    m_right  = float(tolerances.get("pattern_edge_margin_right_px",  build_margin))
    m_top    = float(tolerances.get("pattern_edge_margin_top_px",    build_margin))
    m_bottom = float(tolerances.get("pattern_edge_margin_bottom_px", build_margin))

    holes = detect_holes_from_mask(
        mask,
        min_area=min_area,
        circularity_min=circularity_min,
        aspect_ratio_max=aspect_ratio_max,
        edge_margin_px=0.0,  # per-side filtering applied below
    )
    holes = [
        hh for hh in holes
        if hh.x - hh.r >= m_left
        and hh.x + hh.r <= w - m_right
        and hh.y - hh.r >= m_top
        and hh.y + hh.r <= h - m_bottom
    ]

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
    # Compute phase_y first (no stagger in Y). Then compute phase_x from even rows
    # only when stagger is configured: mixing even+odd rows produces a bimodal
    # X-distribution and estimate_phase picks the wrong peak.
    phase_y = estimate_phase(ys, dy)
    stagger_override = tolerances.get("grid_stagger_x_odd")
    if stagger_override is not None:
        cjs_tmp = np.array([int((y - phase_y) / dy + 0.5) for y in ys])
        even_xs = xs[cjs_tmp % 2 == 0]
        phase_x = estimate_phase(even_xs, dx) if len(even_xs) >= 4 else estimate_phase(xs, dx)
    else:
        phase_x = estimate_phase(xs, dx)
    # Determine stagger_x_odd before calling assign_cells so that odd-row holes
    # are assigned the correct CI index (accounting for the phase shift between
    # even and odd rows).  Use the config value when available; otherwise do a
    # preliminary stagger-less assignment to separate even/odd rows, estimate the
    # stagger from their X-phase difference, then re-assign with the correct stagger.
    stagger_x_odd: float | None = None
    if stagger_override is not None:
        stagger_x_odd = float(stagger_override)
        print(f"[build-pattern] Stagger (config): stagger_x_odd={stagger_x_odd:.1f}px")
    else:
        # First pass without stagger to get row parity
        cells_tmp = assign_cells(points, dx, dy, phase_x, phase_y)
        even_xs = np.array([points[i][0] for i, (_, cj) in enumerate(cells_tmp) if cj % 2 == 0])
        odd_xs  = np.array([points[i][0] for i, (_, cj) in enumerate(cells_tmp) if cj % 2 == 1])
        if len(even_xs) >= 4 and len(odd_xs) >= 4:
            ph_even = estimate_phase(even_xs, dx)
            ph_odd  = estimate_phase(odd_xs, dx)
            stagger = float(((ph_odd - ph_even) + dx / 2.0) % dx - dx / 2.0)
            if abs(stagger) > 2.0:
                stagger_x_odd = stagger
                print(f"[build-pattern] Stagger detectado: phase_even={ph_even:.1f} phase_odd={ph_odd:.1f}"
                      f" stagger_x_odd={stagger:.1f}px")

    cells = assign_cells(points, dx, dy, phase_x, phase_y,
                         stagger_x_odd=stagger_x_odd if stagger_x_odd is not None else 0.0)
    n_total  = len(cells)
    n_unique = len(set(cells))
    n_dup    = n_total - n_unique
    print(f"[build-pattern] {len(points)} puntos  dx={dx:.1f} dy={dy:.1f}"
          f"  phase=({phase_x:.1f},{phase_y:.1f})")
    print(f"[build-pattern] Celdas totales: {n_total}  Unicas: {n_unique}  Duplicadas: {n_dup}")
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

        # Keep only the point closest to the implied grid location for each duplicate cell.
        dedup_points: list[tuple[float, float]] = []
        dedup_radii: list[float] = []
        dedup_cells: list[tuple[int, int]] = []
        by_cell: dict[tuple[int, int], list[int]] = {}
        for idx, cell in enumerate(cells):
            by_cell.setdefault(cell, []).append(idx)

        for cell, idxs in sorted(by_cell.items(), key=lambda item: (item[0][1], item[0][0])):
            if len(idxs) == 1:
                keep_idx = idxs[0]
            else:
                ci, cj = cell
                _stagger = stagger_x_odd or 0.0
                # Mirror assign_cells: odd rows use (phase_x + stagger) % dx as origin
                if cj % 2 == 1 and _stagger != 0.0:
                    exp_x = (phase_x + _stagger) % dx + ci * dx
                else:
                    exp_x = phase_x + ci * dx
                exp_y = phase_y + cj * dy
                keep_idx = min(
                    idxs,
                    key=lambda ii: (points[ii][0] - exp_x) ** 2 + (points[ii][1] - exp_y) ** 2,
                )
            dedup_points.append(points[keep_idx])
            dedup_radii.append(radii[keep_idx])
            dedup_cells.append(cell)

        points = dedup_points
        radii = dedup_radii
        cells = dedup_cells
        print(f"[build-pattern] Duplicadas depuradas: patron final con {len(points)} puntos.")

    built_with_roi = (roi.x, roi.y, roi.w, roi.h) if roi is not None else None
    pat = Pattern(
        model=model, image_size=(w, h), points=points, radii=radii,
        dx=dx, dy=dy, phase_x=phase_x, phase_y=phase_y, cells=cells,
        stagger_x_odd=stagger_x_odd,
        built_with_roi=built_with_roi,
    )
    out = pattern_path(model, scanner_id)
    save_pattern(pat, out)
    return out
