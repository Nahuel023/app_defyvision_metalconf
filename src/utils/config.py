import re
from pathlib import Path
from typing import Any

import yaml

from src.utils.atomic_write import atomic_write_text


DEFAULT_TOLERANCES: dict[str, Any] = {
    "threshold": 120,
    "use_channel": "r",
    "polarity": "bright",
    "min_area": 80,
    "max_area": None,       # None = sin límite superior; setear ~5× área esperada por modelo
    "circularity_min": 0.6,
    "tol_xy_px": 12.0,
    "aspect_ratio_max": 2.5,
    "align_match_tol_px": 80.0,
    "min_match_count": 8,
    # El FAULT generico por cualquier racha NOK queda desactivado en produccion.
    # Las paradas reales usan detectores especificos y persistentes (faltante o
    # desalineacion), evitando que causas NOK distintas se mezclen en una falsa falla.
    "consecutive_nok_frames": 9999,
    "frame_rate_hz": 5.0,
    "max_response_sec": 1.0,
    "edge_margin_px": 15.0,
    "use_clahe": False,
    "clahe_clip": 2.0,
    "clahe_tile": 8,
    "use_otsu": False,
    "use_adaptive": False,
    "adaptive_block_size": 61,
    "adaptive_c": -5.0,
    "tilt_warn_deg": 0.0,
    "blur_ksize": 5,
    "open_ksize": 3,
    "close_ksize": 5,
    "min_detection_ratio": 0.30,
    "max_extra": -1,
    "startup_selftest_enabled": False,
    "selftest_timeout_s": 10.0,
    "max_inspection_hz": 0,
    "grid_min_spacing": 30.0,
    # Frame-level visual decision: when missing > this value, result.status is NOK.
    # None means use the production compare threshold (grid_max_missing).
    "frame_missing_nok_threshold": None,
    "center_offset_tol_px": 0.0,   # 0 = medir y mostrar, nunca NOK; >0 = tolerancia activa
    "edge_centering_bands": 16,              # bandas horizontales para muestreo de bordes
    "pattern_edge_min_holes_per_band": 1,    # min agujeros por banda para incluirla en patrón
    "pattern_edge_smooth_window": 1,         # ventana de mediana para suavizar series de zigzag
    "pattern_edge_boundary_tol_px": 0.0,     # legacy/deprecated; IQR filter en edge_centering.py cubre este caso
    "grid_affine_refinement": False,  # True = corrección affine local post-fase-global
    "blur_score_min": 0.0,            # 0 = deshabilitado; >0 = varianza mín del Laplaciano
    "roi_min_width": 0,               # 0 = deshabilitado; >0 = ancho mínimo (px) que el ROI NUNCA baja (recenter/precal/ema/UI). Evita ROIs angostas que clipan agujeros.
    "stop_min_frames": 0,             # 0 = usa pisos globales (faltantes>=2, desalineacion>=3). >0 = piso DURO por scanner: ninguna parada (racha NOK/faltantes/desalineacion) dispara antes de N frames consecutivos.
    "low_quality_max_streak": 10,     # frames LOW_QUALITY consecutivos antes de resetear racha
    "nok_count_reset_frames": 200,    # frames OK seguidos antes de resetear a 0 el contador NOK de la sesion
    "extra_min_dist_factor": 0.0,     # 0 = todos los extras se muestran; >0 = umbral en múltiplos de tol_xy_px
    "machine_stop_enabled": False,
    "machine_stop_missing_frames": 5,
    "machine_stop_min_missing": 1,
    "machine_stop_same_zone_px": 35.0,
    "machine_stop_ignore_near_miss": True,
    "machine_stop_track_by_grid": True,
    "machine_stop_same_column_tol_cells": 0,
    # Verticalidad: un solo frame con la chapa desviada puede parar la maquina
    # (inmediato). Los faltantes solo paran por persistencia (machine_stop_missing_frames).
    "machine_stop_on_tilt": False,
    "grid_lateral_shift_max_px": 0.0,
    # Desalineacion del patron (corrimiento de verticalidad/cizalla): si la fraccion de
    # esperados sin ajustar supera el ratio → DETENER MAQUINA (un frame alcanza).
    # ── Grabación de evidencia pre-evento ─────────────────────────────
    # ── Buffer circular de frames OK en disco ─────────────────────────
    "ok_buffer_enabled": True,         # guarda las últimas N inspecciones OK
    "ok_buffer_count": 200,            # cuántos frames OK mantener por scanner
    "ok_buffer_every": 3,              # guardar 1 de cada N frames OK (throttle)
    "ok_buffer_jpeg_quality": 75,      # calidad JPEG (75 = buen balance tamaño/calidad)
    "timeline_buffer_enabled": True,   # buffer cronológico de todos los frames (OK+NOK+LQ+STOP)
    "timeline_buffer_count": 500,      # últimos N frames a mantener por scanner
    "disk_writer_queue_max": 256,      # cola máxima del writer serial de disco por scanner
    # ──────────────────────────────────────────────────────────────────
    "events_enabled": False,           # activar en producción
    "events_max_disk_gb": 10.0,        # presupuesto total de data/events
    "pre_event_seconds": 60.0,         # 1 minuto antes de la parada
    "post_event_seconds": 30.0,        # 30 segundos después de la parada
    "pre_event_fps": 5.0,              # fotogramas/s a acumular en buffer
    "pre_event_jpeg_quality": 80,      # calidad JPEG del buffer (10-95)
    "pre_event_max_ram_mb": 256.0,     # 1 min × 5 fps × ~100 KB ≈ 30 MB; 256 con margen
    "camera_missing_error_timeout_s": 3.0,  # segundos sin frames antes de ERROR en AUTO
    # ──────────────────────────────────────────────────────────────────
    "pattern_desalign_enabled": False,
    "pattern_desalign_missing_ratio": 0.5,
    "pattern_desalign_min_angle_deg": 0.0,
    "pattern_desalign_zigzag_std_px": 0.0,
    "pattern_desalign_center_std_px": 0.0,
    "pattern_desalign_center_abs_px": 0.0,
    "pattern_desalign_bottom_shift_px": 0.0,
    "grid_extend_rows_after": 0,
    "use_hungarian_matching": False,
    "verticality_quality_enabled": False,
    "chapa_zigzag_std_max_px": 4.0,
    "chapa_zigzag_abs_max_px": 10.0,
    "chapa_no_line_min_used_lines": 0,
    "chapa_no_line_abs_max_px": 0.0,
    "pattern_align_enabled": False,
    "pattern_align_min_missing": 0,
    "pattern_align_std_max_px": 5.0,
    "pattern_align_abs_max_px": 30.0,
    "pattern_align_machine_stop": True,
    "pattern_align_stop_frames": 3,
    "pattern_global_offset_max_px": 0.0,
    "pattern_slope_delta_max_deg": 0.0,
    "pattern_align_severe_abs_max_px": 0.0,
    "pattern_align_severe_slope_deg": 0.0,
    "pattern_center_align_enabled": False,
    "pattern_center_zigzag_std_max_px": 4.0,
    "pattern_center_zigzag_abs_max_px": 6.5,
    "roi_autocorrect_enabled": False,
    "roi_autocorrect_max_shift_px": 0.0,
    "roi_autocorrect_max_width_delta_px": 0.0,
    "roi_detect_margin_px": 0,
    "roi_detect_min_contrast": 30.0,
    "roi_recenter_enabled": False,
    "roi_recenter_warmup_frames": 20,
    "roi_recenter_trigger_delta_px": 6.0,
    "roi_recenter_edge_missing_min": 3,
    "roi_recenter_edge_band_px": 28.0,
    "roi_recenter_streak_frames": 6,
    "roi_recenter_step_px": 1.0,
    "roi_recenter_max_total_shift_px": 40.0,
    "roi_recenter_urgent_delta_px": 15.0,    # drift >= este valor actua al toque (streak=1)
    "roi_recenter_cooldown_frames": 15,      # frames de pausa tras primera correccion (~3s a 5fps)
    "roi_recenter_cooldown_max_frames": 120, # techo del cooldown (~24s a 5fps)
    "roi_recenter_cooldown_mult": 2.0,       # multiplicador del cooldown por cada correccion
    "roi_recenter_mode": "resize",           # "move" = desplaza x; "resize" = expande el borde
    "roi_recenter_require_edge_missing": True,# False = centra sin esperar missing de borde
    "roi_recenter_max_width_growth_px": 60.0,# maximo crecimiento total del ancho en modo resize
    "roi_precal_enabled": True,              # pre-calibrar ROI antes de iniciar el loop de inspeccion
    "roi_precal_frames": 8,                  # frames a capturar por iteracion de pre-cal
    "roi_precal_max_iters": 4,               # maximas iteraciones de correccion pre-inicio
    "roi_precal_threshold_px": 10.0,        # shift medio (px) que dispara correccion en pre-cal
    "roi_precal_overshoot_px": 3,           # pixeles extra sobre el shift medido (margen de seguridad)
    "startup_grace_frames": 30,             # frames iniciales sin machine_stop ni fault (da tiempo al roi_recenter)
    "startup_grace_seconds": 0.0,            # segundos iniciales sin machine_stop ni fault (cubre el tiempo mecanico hasta que la chapa entra en camara, 0 = desactivado)
    # Corrección EMA lenta de ROI — escribe roi.json solo cuando el drift es muy sostenido.
    # Seguro para 24/7: a 5fps tarda mínimo ~3 min en aplicar 1px de corrección.
    "roi_slow_ema_enabled": False,       # activar por scanner en io_map.yaml overrides
    "roi_slow_ema_alpha": 0.002,         # EMA α — converge en ~500 frames (~100s a 5fps)
    "roi_slow_ema_threshold_px": 15.0,   # drift mínimo (px) para arrancar confirmación
    "roi_slow_ema_confirm_frames": 500,  # frames sostenidos antes de escribir 1px (~100s)
    "roi_slow_ema_cooldown_frames": 1500,# frames de pausa entre correcciones (~5min a 5fps)
    "roi_slow_ema_max_total_px": 40,     # máximo ±40px desde la ROI original del archivo
    "roi_slow_ema_save_every": 300,      # guardar estado en disco cada N frames (~60s)
    "roi_slow_ema_warmup_frames": 300,   # frames iniciales para establecer baseline estatico (~60s)
    "compare_top_ignore_px": 0.0,     # recorta solo en comparacion (no en deteccion/alineacion)
    "compare_bottom_ignore_px": 0.0,  # ignora bordes sup/inf del frame/ROI si molestan
    "compare_left_ignore_px": 0.0,   # ignora franja izquierda en comparacion
    "compare_right_ignore_px": 0.0,  # ignora franja derecha en comparacion
    "pattern_hull_margin_px": 0.0,    # 0 = deshabilitado; >0 = descarta detectados fuera de la envolvente del patron
    # Per-type hole classification (models with two distinct hole sizes, e.g. modelo_A)
    # 0 = disabled (single-type mode); >0 = area threshold (px²) between small/large holes
    "hole_type_split_area": 0.0,
    "min_area_small": 0.0,    # 0 = fall back to global min_area
    "max_area_small": 0.0,    # 0 = no upper limit for small holes
    "min_area_large": 0.0,    # 0 = fall back to global min_area
    "max_area_large": 0.0,    # 0 = no upper limit for large holes
}


def tolerances_path() -> Path:
    return Path("config/tolerancias.yaml")


_TOLERANCES_CACHE_KEY: tuple[float | None, float | None] | None = None
_TOLERANCES_CACHE_DATA: dict[str, Any] | None = None
_TOLERANCES_MERGED_CACHE: dict[
    tuple[str | None, str | None, tuple[float | None, float | None]],
    dict[str, Any],
] = {}


def _mtime_or_none(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _invalidate_tolerances_cache() -> None:
    global _TOLERANCES_CACHE_KEY, _TOLERANCES_CACHE_DATA
    _TOLERANCES_CACHE_KEY = None
    _TOLERANCES_CACHE_DATA = None
    _TOLERANCES_MERGED_CACHE.clear()


def load_tolerances(model: str | None = None, scanner_id: str | None = None) -> dict[str, Any]:
    """Load tolerances, optionally merging per-model overrides.

    tolerancias.yaml structure:
        # global params (apply to every model)
        threshold: 175
        min_area: 80.0
        ...
        # per-model overrides (only specify what differs from global)
        models:
          modelo_A: {}        # Esterilla
          modelo_B:           # Microperforado
            min_area: 60.0
            tol_xy_px: 18.0
    """
    cfg_path = tolerances_path()
    io_map_path = Path("config") / "io_map.yaml"
    cache_key = (_mtime_or_none(cfg_path), _mtime_or_none(io_map_path))

    global _TOLERANCES_CACHE_KEY, _TOLERANCES_CACHE_DATA
    if _TOLERANCES_CACHE_KEY != cache_key:
        if not cfg_path.exists():
            data = {}
        else:
            with cfg_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        _TOLERANCES_CACHE_KEY = cache_key
        _TOLERANCES_CACHE_DATA = data if isinstance(data, dict) else {}
        _TOLERANCES_MERGED_CACHE.clear()

    merged_cache_key = (model, scanner_id, cache_key)
    cached = _TOLERANCES_MERGED_CACHE.get(merged_cache_key)
    if cached is not None:
        return dict(cached)

    data = _TOLERANCES_CACHE_DATA or {}

    cfg = dict(DEFAULT_TOLERANCES)
    # Apply global params (skip the 'models' block)
    cfg.update({k: v for k, v in data.items() if k != "models" and v is not None})

    # Apply per-model overrides if a model is specified
    if model:
        model_overrides = (data.get("models") or {}).get(model) or {}
        cfg.update({k: v for k, v in model_overrides.items() if v is not None})

    # Apply per-scanner overrides. ``inspection`` is common to every model;
    # ``inspection_models.<model>`` is the preferred place for optical/model
    # calibrations so switching material in the operator UI cannot leak the
    # microperforado geometry into esterilla (or vice versa).
    if scanner_id:
        if io_map_path.exists():
            try:
                with io_map_path.open("r", encoding="utf-8") as f:
                    io_data = yaml.safe_load(f) or {}
                scanner_cfg = (io_data.get(scanner_id) or {})
                insp_cfg = scanner_cfg.get("inspection") or {}
                cfg.update({k: v for k, v in insp_cfg.items() if v is not None})
                if model:
                    model_insp_cfg = (
                        (scanner_cfg.get("inspection_models") or {}).get(model) or {}
                    )
                    cfg.update({
                        k: v for k, v in model_insp_cfg.items() if v is not None
                    })
            except Exception:
                pass

    _TOLERANCES_MERGED_CACHE[merged_cache_key] = dict(cfg)
    return cfg


def save_model_overrides(model: str, updates: dict[str, Any]) -> None:
    """Actualiza solo los overrides de un modelo en tolerancias.yaml.

    Lee el archivo completo, parchea únicamente `models.<model>` con los
    valores de `updates` y lo reescribe. No toca parámetros globales ni
    otros modelos.
    """
    cfg_path = tolerances_path()
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    models = data.setdefault("models", {})
    models[model] = dict(
        models.get(model) or {},
        **{k: v for k, v in updates.items() if v is not None},
    )

    atomic_write_text(
        cfg_path,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    )
    _invalidate_tolerances_cache()


def _format_yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    # Cualquier otro tipo (string u otro): dejar que PyYAML decida si necesita
    # comillas — p.ej. "null"/"yes", algo con ':' o que arranca con un caracter
    # especial (@, *, &, #) — en vez de asumir que siempre es seguro sin comillas.
    dumped = yaml.safe_dump(value, default_flow_style=True).strip()
    if dumped.endswith("\n..."):
        dumped = dumped[: -len("\n...")]
    return dumped


def save_scanner_overrides(
    scanner_id: str,
    updates: dict[str, Any],
    model: str | None = None,
) -> None:
    """Actualiza overrides del scanner preservando comentarios y mapeo PLC.

    Si se informa ``model``, escribe en
    ``<scanner_id>.inspection_models.<model>``. Sin modelo conserva soporte
    legacy para ``<scanner_id>.inspection``.
    """
    path = Path("config/io_map.yaml")
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for key, value in updates.items():
        if value is None:
            continue
        lines = _set_io_map_inspection_param(
            lines, scanner_id, key, value, model=model
        )

    atomic_write_text(path, "".join(lines))
    _invalidate_tolerances_cache()


def _set_io_map_inspection_param(
    lines: list[str],
    scanner_id: str,
    key: str,
    value: Any,
    model: str | None = None,
) -> list[str]:
    scanner_re = re.compile(rf"^{re.escape(scanner_id)}:\s*$")
    top_key_re = re.compile(r"^\S")        # columna 0 = nuevo bloque top-level
    inspection_re = re.compile(r"^  inspection:\s*$")
    inspection_models_re = re.compile(r"^  inspection_models:\s*$")
    sibling_re = re.compile(r"^  \S")      # otra clave a 2 espacios (inputs:, outputs:, etc.)
    param_re = re.compile(rf"^(\s+){re.escape(key)}\s*:\s*.*$")

    start = next((i for i, l in enumerate(lines) if scanner_re.match(l)), None)
    if start is None:
        raise KeyError(f"No se encontro el bloque '{scanner_id}:' en io_map.yaml")

    end = next(
        (i for i in range(start + 1, len(lines)) if top_key_re.match(lines[i])),
        len(lines),
    )

    if model:
        models_start = next(
            (i for i in range(start + 1, end) if inspection_models_re.match(lines[i])),
            None,
        )
        if models_start is None:
            raise KeyError(
                f"'{scanner_id}' no tiene bloque 'inspection_models:' en io_map.yaml"
            )
        models_end = next(
            (i for i in range(models_start + 1, end) if sibling_re.match(lines[i])),
            end,
        )
        model_re = re.compile(rf"^    {re.escape(model)}:\s*$")
        model_sibling_re = re.compile(r"^    \S")
        insp_start = next(
            (i for i in range(models_start + 1, models_end) if model_re.match(lines[i])),
            None,
        )
        if insp_start is None:
            lines.insert(models_end, f"    {model}:\n")
            insp_start = models_end
            models_end += 1
        insp_end = next(
            (
                i for i in range(insp_start + 1, models_end)
                if model_sibling_re.match(lines[i])
            ),
            models_end,
        )
        insert_indent = "      "
    else:
        insp_start = next(
            (i for i in range(start + 1, end) if inspection_re.match(lines[i])), None
        )
        if insp_start is None:
            raise KeyError(f"'{scanner_id}' no tiene bloque 'inspection:' en io_map.yaml")
        insp_end = next(
            (i for i in range(insp_start + 1, end) if sibling_re.match(lines[i])), end
        )
        insert_indent = "    "

    formatted = _format_yaml_scalar(value)
    for i in range(insp_start + 1, insp_end):
        m = param_re.match(lines[i])
        if m:
            lines[i] = f"{m.group(1)}{key}: {formatted}\n"
            return lines

    lines.insert(insp_end, f"{insert_indent}{key}: {formatted}\n")
    return lines


def save_tolerances(data: dict[str, Any]) -> None:
    cfg = dict(DEFAULT_TOLERANCES)
    cfg.update({k: v for k, v in data.items() if v is not None})

    cfg_path = tolerances_path()
    atomic_write_text(cfg_path, yaml.safe_dump(cfg, sort_keys=False))
    _invalidate_tolerances_cache()
