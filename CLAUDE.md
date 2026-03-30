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

```
Image → ROI crop → Preprocess (threshold/channel) → detect_holes (contours)
      → align_edge (Hough rotation correction) → compare (match vs pattern)
      → annotate (overlay) → InspectionResult (OK/NOK + missing holes)
```

Temporal decision: `inspect_folder()` accumulates per-frame results and requires `consecutive_nok_frames` (from `config/tolerancias.yaml`) before declaring the sequence NOK.

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `src/main.py` | CLI entry point, argument routing |
| `src/inspection.py` | Core logic: `inspect_image()`, `inspect_folder()`, result dataclasses |
| `src/pipeline/` | Stateless processing steps (align, preprocess, detect, compare, annotate) |
| `src/patterns/` | Pattern build/load/save; ROI management |
| `src/qt_operator_app.py` | PyQt6 operator UI (production) |
| `src/gui_app.py` | Tkinter service UI (calibration/development) |
| `src/utils/config.py` | Loads and exposes `config/tolerancias.yaml` as a typed config object |

### Data Layout

```
data/patterns/{model_name}/holes.json   # Reference hole coordinates + image dims
data/patterns/{model_name}/roi.json     # Optional ROI bounding box
data/frames/                            # Input frames for folder analysis
data/output/ok/ and data/output/nok/    # Saved annotated results
```

### Configuration

All detection and temporal parameters live in `config/tolerancias.yaml`:
- Detection: `threshold`, `use_channel`, `polarity`, `min_area`, `circularity_min`, `tol_xy_px`, `aspect_ratio_max`
- Alignment: `align_match_tol_px`, `min_match_count`
- Temporal: `consecutive_nok_frames`, `frame_rate_hz`, `max_response_sec`

## Tech Stack

- **OpenCV** — image processing, contour detection, Hough line alignment
- **PyQt6** — operator UI
- **Tkinter** — service/dev UI (stdlib)
- **PyYAML** — config loading
- **FFmpeg** — video frame extraction (external, installed by setup script)

## Notes

- No test suite exists; validation is done manually via CLI and UI modes.
- The `setup_prov_pc/` directory contains legacy C# integration code unrelated to the main Python app.
- `data/video/` and generated frame/output directories are git-ignored.
