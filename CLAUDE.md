# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Visual inspection system for punched sheet metal (Metalconf). Detects holes in stamped parts, compares against a reference pattern, and classifies frames as OK/NOK. Supports temporal decision logic (N consecutive NOK frames required to declare a failure), targeting Windows production deployments.

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
# Operator UI (production)
.\.venv\Scripts\python.exe -m src.main operator-ui

# Service/calibration UI (development)
.\.venv\Scripts\python.exe -m src.main gui

# Build a reference pattern from an OK image
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/ref.jpg"

# Analyze a single image
.\.venv\Scripts\python.exe -m src.main run-image --model modelo_A --img "data/input/test.jpg" --show --save

# Analyze a folder of frames with temporal decision
.\.venv\Scripts\python.exe -m src.main run-folder --model modelo_A --input "data/frames" --fps 5 --save

# Extract frames from video (external tool)
ffmpeg -i "data/videos/video.mp4" -vf fps=2 "data/frames/frame_%04d.jpg"
```

## Architecture

### Pipeline Flow

`inspect_image()` runs a **two-pass alignment** before comparing:

1. **Rotation correction** — `align_image_by_right_edge()` uses Hough lines on the rightmost 30% of the frame to detect near-vertical edges and rotates to straighten them.
2. **ROI crop** — optional, loaded from `data/patterns/{model}/roi.json`.
3. **First preprocess + detect** — thresholds the image and finds contours to get an initial set of `Hole` objects.
4. **Translation correction** — `_estimate_alignment_transform()` centroid-matches detected holes to the reference pattern and computes an affine shift via RANSAC.
5. **Second preprocess + detect** — repeats detection on the warped image for accurate positions.
6. **Compare** — `compare_missing_only()` nearest-neighbor matches each expected hole to a detected one within `tol_xy_px`; any unmatched expected hole is "missing".
7. **Annotate + return** — overlay is drawn; result wrapped in `InspectionResult`.

`inspect_folder()` calls `inspect_image()` per frame, then applies `_apply_temporal_rule()`: only when the NOK streak reaches `consecutive_nok_frames` does `decision_status` flip to NOK.

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `src/main.py` | CLI entry point, argument routing |
| `src/inspection.py` | Core logic: `inspect_image()`, `inspect_folder()`, result dataclasses |
| `src/pipeline/align_edge.py` | Hough-based rotation correction; returns `EdgeAlignResult` |
| `src/pipeline/preprocess.py` | Threshold/channel selection → binary mask |
| `src/pipeline/detect_holes.py` | Contour detection → `Hole` dataclass list |
| `src/pipeline/compare.py` | Nearest-neighbour matching → `CompareReport` |
| `src/pipeline/annotate.py` | Draw overlay on BGR image |
| `src/patterns/pattern_build.py` | Build `holes.json` from an OK reference image |
| `src/patterns/pattern_io.py` | Load/save `Pattern` (JSON with `points`, `radii`, `image_size`) |
| `src/patterns/roi.py` | Load/apply optional ROI bounding box |
| `src/io/` | `load_bgr_image`, `save_image` wrappers |
| `src/qt_operator_app.py` | PyQt6 operator UI; runs folder analysis in a `QThread` worker |
| `src/gui_app.py` | Tkinter service UI; supports per-parameter tolerance editing and live save |
| `src/utils/config.py` | `load_tolerances()` / `save_tolerances()` over `config/tolerancias.yaml` |

### Data Layout

```
data/patterns/{model_name}/holes.json   # reference hole coordinates + image dims
data/patterns/{model_name}/roi.json     # optional ROI bounding box
data/frames/                            # input frames for folder analysis
data/output/ok/                         # saved annotated overlays for OK frames
data/output/nok/                        # saved annotated overlays for NOK frames
data/output/debug/                      # binary masks saved alongside overlays
```

### Configuration

All detection and temporal parameters live in `config/tolerancias.yaml`. `load_tolerances()` merges the file with `DEFAULT_TOLERANCES` in `src/utils/config.py`, so missing keys fall back to code defaults.

| Key | Default | Effect |
|-----|---------|--------|
| `threshold` | 160 | Pixel intensity cutoff for binarisation |
| `use_channel` | `gray` | Channel before threshold: `gray`, `r`, `g`, `b` |
| `polarity` | `bright` | `bright` = holes are bright; `dark` = inverted |
| `min_area` | 40.0 | Minimum contour area (px²) to be a hole |
| `circularity_min` | 0.4 | Minimum circularity score (0–1) |
| `tol_xy_px` | 30.0 | Max distance (px) for a detected hole to match an expected one |
| `aspect_ratio_max` | 2.5 | Rejects elongated contours |
| `align_match_tol_px` | 80.0 | Max distance for centroid-based hole matching during alignment |
| `min_match_count` | 8 | Minimum matched holes required to apply affine correction |
| `consecutive_nok_frames` | 5 | NOK streak length to trigger temporal NOK decision |
| `frame_rate_hz` | 5.0 | FPS used to compute response time |
| `max_response_sec` | 1.0 | Target maximum response time (sec) |

## Tech Stack

- **OpenCV** — image processing, contour detection, Hough line alignment, RANSAC affine estimation
- **PyQt6** — operator UI (production); analysis runs in `QThread` to keep UI responsive
- **Tkinter** — service/dev UI (stdlib); includes editable tolerance panel that calls `save_tolerances()`
- **PyYAML** — config loading
- **FFmpeg** — video frame extraction (external, installed by setup script)

## Notes

- No automated test suite; validation is done via CLI and UI modes.
- `setup_prov_pc/` contains legacy C# integration code unrelated to the main Python app.
- `data/video/` and generated frame/output directories are git-ignored.
- The operator UI (`qt_operator_app.py`) can launch the service UI as a subprocess via the "Modo servicio" button.
