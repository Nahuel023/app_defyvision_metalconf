# CLAUDE.md

## INSTRUCCIÓN OBLIGATORIA
**Leer `CHANGELOG.md` al inicio de CADA conversación, antes de responder cualquier
pregunta o tocar cualquier archivo. Contiene el historial completo de cambios,
decisiones de diseño y el estado actual del sistema.**

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Visual inspection system for punched sheet metal (Metalconf). Detects holes in stamped parts, compares against a reference pattern, and classifies frames as OK/NOK. Supports temporal decision logic (N consecutive NOK frames required to declare a failure), targeting Windows production deployments with PLC integration (Coolmay CX3G via Modbus TCP).

## Environment Setup

```powershell
# Initial setup (creates .venv, installs deps, installs FFmpeg)
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1

# Update existing installation
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

Python virtual environment is at `.venv/`. Always use:
```bash
.\.venv\Scripts\python.exe -m src.main [command]
```

## Common Commands

```bash
# Production mode: PLC + cameras + operator UI (real-time)
.\.venv\Scripts\python.exe -m src.main run

# Service/diagnostic mode with login (replaces old 'gui' command)
.\.venv\Scripts\python.exe -m src.main service

# Batch operator UI (folder analysis, no live camera/PLC)
.\.venv\Scripts\python.exe -m src.main operator-ui

# Build a reference pattern from an OK image
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/ref.jpg"

# Analyze a single image
.\.venv\Scripts\python.exe -m src.main run-image --model modelo_A --img "data/input/test.jpg" --show --save

# Analyze a folder of frames with temporal decision
.\.venv\Scripts\python.exe -m src.main run-folder --model modelo_A --input "data/frames" --fps 5 --save

# Run unit tests
.\.venv\Scripts\python.exe -m pytest tests/

# Extract frames from video (external tool)
ffmpeg -i "data/videos/video.mp4" -vf fps=2 "data/frames/frame_%04d.jpg"
```

## Architecture

### System Overview

The production mode (`run`) creates an `InspectionSystem` that wires together:

```
config/io_map.yaml
      │
      ▼
InspectionSystem  (src/controller/system.py)
  ├── PLCClient           (src/plc/client.py)                — thread-safe Modbus TCP wrapper
  ├── IOMap               (src/plc/io_map.py)                — semantic signal access by name
  ├── Camera × N          (src/vision/camera.py)             — one per scanner
  └── ScannerController × N  (src/controller/scanner_controller.py)
            └── calls inspect_image() per frame trigger
```

Each `ScannerController` reads `punch_sensor` from the PLC to know when to inspect, runs the vision pipeline, then writes `light_red/green/yellow` and `solenoid` outputs back to the PLC.

### Vision Pipeline

`inspect_image()` in `src/inspection.py` runs a **two-pass alignment** before comparing:

1. **Rotation correction** — `align_image_by_right_edge()` uses Hough lines on the rightmost 30% of the frame to detect near-vertical edges and rotates to straighten them. Corrections outside ±20° or below 0.2° are skipped.
2. **ROI crop** — optional, loaded from `data/patterns/{model}/roi.json`.
3. **First preprocess + detect** — thresholds the image and finds contours to get an initial set of `Hole` objects.
4. **Translation correction** — `_estimate_alignment_transform()` centroid-matches detected holes to the reference pattern and computes an affine shift via RANSAC (`cv2.estimateAffinePartial2D`).
5. **Second preprocess + detect** — repeats detection on the warped image for accurate positions.
6. **Compare** — `compare_missing_only()` nearest-neighbor matches each expected hole to a detected one within `tol_xy_px`; any unmatched expected hole is "missing".
7. **Annotate + return** — overlay is drawn; result wrapped in `InspectionResult`.

`build_pattern_from_image()` applies the same alignment (rotation + ROI) before detecting holes, so the pattern coordinates are in the aligned frame of reference.

`inspect_folder()` calls `inspect_image()` per frame, then applies `_apply_temporal_rule()`: only when the NOK streak reaches `consecutive_nok_frames` does `decision_status` flip to NOK.

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `src/main.py` | CLI entry point; subcommands: `run`, `service`, `operator-ui`, `build-pattern`, `run-image`, `run-folder` |
| `src/controller/system.py` | `InspectionSystem`: lifecycle orchestration (PLC + cameras + scanners) |
| `src/controller/scanner_controller.py` | Per-scanner inspection loop; reads PLC trigger, runs vision, writes PLC outputs |
| `src/plc/client.py` | `PLCClient`: thread-safe Modbus TCP wrapper with auto-reconnect and cooldown |
| `src/plc/io_map.py` | `IOMap`: loads `config/io_map.yaml`, exposes signals by semantic name |
| `src/ui/operator.py` | PyQt6 production operator UI (launched by `run`) |
| `src/ui/service.py` | PyQt6 service/diagnostic UI (launched by `service`) |
| `src/ui/login_dialog.py` | Login gate for service mode |
| `src/vision/camera.py` | Camera abstraction (OpenCV VideoCapture) |
| `src/vision/inspector.py` | Thin wrapper around `inspect_image()` for use inside the controller |
| `src/inspection.py` | Core vision logic: `inspect_image()`, `inspect_folder()`, result dataclasses |
| `src/pipeline/align_edge.py` | Hough-based rotation correction; returns `EdgeAlignResult` |
| `src/pipeline/preprocess.py` | Threshold/channel selection → binary mask |
| `src/pipeline/detect_holes.py` | Contour detection → `Hole` dataclass list |
| `src/pipeline/compare.py` | Nearest-neighbour matching → `CompareReport` |
| `src/pipeline/annotate.py` | Draw overlay on BGR image |
| `src/patterns/pattern_build.py` | Build `holes.json` from an OK reference image |
| `src/patterns/pattern_io.py` | Load/save `Pattern` (JSON with `points`, `radii`, `image_size`) |
| `src/patterns/roi.py` | Load/apply optional ROI bounding box |
| `src/utils/config.py` | `load_tolerances()` / `save_tolerances()` over `config/tolerancias.yaml` |
| `src/utils/logger.py` | `setup_logging()` — call once at startup |

### I/O Mapping

All PLC signals are defined in `config/io_map.yaml` and accessed exclusively through `IOMap` by semantic name — no raw offsets in control code:

```yaml
plc:
  ip: "192.168.10.175"
  port: 502

scanner_1:
  camera_index: 0
  model: "modelo_A"
  inputs:
    punch_sensor: 0   # X0 — LOW = sheet stopped → inspect
    mode_switch:  1   # X1 — 0=MANUAL, 1=AUTO
  outputs:
    light_red:    0   # Y0 — fault / forced stop
    light_green:  1   # Y1 — running OK
    light_yellow: 2   # Y2 — NOK streak active
    light_blue:   3   # Y3 — idle / ready
    solenoid:    10   # Y10 — electrovalve (activates punch)
    backlight:   12   # Y12 — inspection backlight
```

`scanner_2` mirrors the same structure on different offsets. To add a scanner: add a new `scanner_N` block to `config/io_map.yaml` and create its pattern with `build-pattern`.

### Data Layout

```
config/io_map.yaml              # PLC I/O mapping (scanners, signals, camera indices)
config/tolerancias.yaml         # vision detection parameters
data/patterns/{model}/holes.json  # reference hole coordinates + image dims
data/patterns/{model}/roi.json    # optional ROI bounding box
data/frames/                    # input frames for folder analysis
data/output/ok/                 # saved annotated overlays for OK frames
data/output/nok/                # saved annotated overlays for NOK frames
data/output/debug/              # binary masks saved alongside overlays
```

### Configuration

All detection and temporal parameters live in `config/tolerancias.yaml`. `load_tolerances()` merges the file with `DEFAULT_TOLERANCES` in `src/utils/config.py`, so missing keys fall back to code defaults.

| Key | Default | Effect |
|-----|---------|--------|
| `threshold` | 120 | Pixel intensity cutoff for binarisation |
| `use_channel` | `r` | Channel before threshold: `gray`, `r`, `g`, `b` |
| `polarity` | `bright` | `bright` = holes are bright; `dark` = inverted |
| `min_area` | 80.0 | Minimum contour area (px²) to be a hole |
| `circularity_min` | 0.6 | Minimum circularity score (0–1) |
| `tol_xy_px` | 12.0 | Max distance (px) for a detected hole to match an expected one |
| `aspect_ratio_max` | 2.5 | Rejects elongated contours |
| `align_match_tol_px` | 80.0 | Max distance for centroid-based hole matching during alignment |
| `min_match_count` | 8 | Minimum matched holes required to apply affine correction |
| `consecutive_nok_frames` | 5 | NOK streak length to trigger temporal NOK decision |
| `frame_rate_hz` | 5.0 | FPS used to compute response time |
| `max_response_sec` | 1.0 | Target maximum response time (sec) |

## Tech Stack

- **OpenCV** — image processing, contour detection, Hough line alignment, RANSAC affine estimation
- **PyQt6** — all UIs (operator + service); blocking work runs in `QThread` workers
- **pymodbus** — Modbus TCP communication with Coolmay CX3G PLC
- **PyYAML** — config loading
- **FFmpeg** — video frame extraction (external, installed by setup script)

## Notes

- `tests/test_io_map.py` is the only automated test. Run with `.\.venv\Scripts\python.exe -m pytest tests/`.
- `src/gui_app.py` (Tkinter) was removed; service UI is now PyQt6 (`src/ui/service.py`). The old `gui` subcommand no longer exists — use `service` instead.
- `data/video/` and generated frame/output directories are git-ignored.
- When moving to a new PC or changing camera/lens/lighting, always regenerate the reference pattern with `build-pattern` after recalibrating `config/tolerancias.yaml`.
