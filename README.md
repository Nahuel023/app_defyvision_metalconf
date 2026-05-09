# app_defyvision_metalconf

Sistema de inspección visual para chapa punzonada (Metalconf). Detecta agujeros en piezas estampadas, compara contra un patrón de referencia por modelo y clasifica cada inspección como `OK/NOK`. Se integra con PLC Coolmay CX3G vía Modbus TCP para control de electroválvulas, iluminación y sensores de disparo.

## Características

- Alineación automática por rotación y traslación (Hough + RANSAC afín)
- Soporte multi-modelo: cada modelo tiene su patrón y tolerancias independientes
- Decisión temporal configurable: falla solo tras N frames consecutivos NOK
- Burst capture: N capturas por disparo, se selecciona la mejor
- Dos modos de inspección: `punch_triggered` (disparo por sensor PLC) y `continuous` (diferencia de frames)
- Calibración de cámara manual completa desde la UI de servicio
- Integración PLC: luces (azul/verde/amarillo/rojo) + electroválvula + backlight

## Requisitos

- Windows 10/11 + PowerShell
- Python 3.11+ con launcher `py`
- Git
- FFmpeg (instalado automáticamente por el script de setup)
- PLC Coolmay CX3G accesible por red (para modo producción)

## Instalación en Windows

```powershell
# Primer setup (crea .venv, instala dependencias, instala FFmpeg)
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1

# Actualizar proyecto existente (git pull + reinstalar deps)
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

El entorno virtual queda en `.venv/`. Siempre usar `.\.venv\Scripts\python.exe`.

## Modos de uso

### Producción — PLC + cámaras + UI operador

```powershell
.\.venv\Scripts\python.exe -m src.main run
```

Levanta `InspectionSystem` completo: PLC, cámaras, `ScannerController` por scanner y UI de operador en tiempo real.

### Servicio / diagnóstico

```powershell
.\.venv\Scripts\python.exe -m src.main service
```

Requiere login. Acceso a todas las pestañas de configuración y diagnóstico:
- **Tolerancias** — ajuste de parámetros de detección
- **Construir patrón** — genera `holes.json` desde imagen de referencia
- **Analizar imagen** — inspección de imagen individual con overlay
- **Analizar carpeta** — inspección de secuencia con decisión temporal
- **Grabación** — captura de secuencia live + análisis frame a frame
- **Hardware** — LEDs de estado X0-X15, toggle de salidas Y0-Y15
- **Cámara** — calibración manual completa de parámetros UVC (foco, exposición, gamma, etc.)

### Operador (batch sin cámara/PLC)

```powershell
.\.venv\Scripts\python.exe -m src.main operator-ui
```

UI de análisis de carpetas para evaluación offline.

### Comandos de consola

```powershell
# Construir patrón desde imagen OK de referencia
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/ref.jpg"

# Analizar imagen individual (--show abre ventana, --save guarda overlay)
.\.venv\Scripts\python.exe -m src.main run-image --model modelo_A --img "data/input/test.jpg" --show --save

# Analizar carpeta con decisión temporal
.\.venv\Scripts\python.exe -m src.main run-folder --model modelo_A --input "data/frames" --fps 5 --save

# Extraer frames de video
ffmpeg -i "data/videos/video.mp4" -vf fps=2 "data/frames/frame_%04d.jpg"

# Tests unitarios
.\.venv\Scripts\python.exe -m pytest tests/
```

## Arquitectura

### Sistema de producción

```
config/io_map.yaml
      │
      ▼
InspectionSystem          (src/controller/system.py)
  ├── PLCClient            (src/plc/client.py)          — Modbus TCP thread-safe
  ├── IOMap                (src/plc/io_map.py)          — señales por nombre semántico
  ├── Camera × N           (src/vision/camera.py)       — una por scanner
  └── ScannerController × N  (src/controller/scanner_controller.py)
            └── _inspect_loop / _continuous_loop
                    └── Inspector → inspect_image()
```

### Pipeline de visión

`inspect_image()` en `src/inspection.py`:

1. **Corrección de rotación** — `align_image_by_right_edge()` detecta bordes verticales con Hough y endereza la imagen
2. **ROI** — recorte opcional desde `data/patterns/{model}/roi.json`
3. **Preprocesado + detección** — umbralado y contornos → lista de `Hole`
4. **Estimación de transformación afín** — `_estimate_alignment_transform()`: voting histogram → RANSAC → `cv2.estimateAffinePartial2D`
5. **Proyección inversa del patrón** — `cv2.invertAffineTransform` proyecta los puntos esperados al espacio actual
6. **Guarda de alias** — si la tasa de matching < 80 %, se prueba corrección ± dx
7. **Comparación** — `compare_missing_only()` nearest-neighbour; agujeros sin match = missing
8. **Overlay + resultado** — `InspectionResult` con status, missing_points, shift_xy, ángulo

### Módulos principales

| Módulo | Responsabilidad |
|--------|----------------|
| `src/main.py` | CLI: subcomandos `run`, `service`, `operator-ui`, `build-pattern`, `run-image`, `run-folder` |
| `src/controller/system.py` | `InspectionSystem`: ciclo de vida (PLC + cámaras + scanners) |
| `src/controller/scanner_controller.py` | FSM por scanner: IDLE → RUNNING → FAULT / ERROR; loops de inspección |
| `src/plc/client.py` | `PLCClient`: wrapper Modbus TCP con auto-reconexión |
| `src/plc/io_map.py` | `IOMap`: carga `io_map.yaml`, acceso por nombre semántico |
| `src/ui/operator.py` | UI operador PyQt6 (producción) |
| `src/ui/service.py` | UI servicio PyQt6 con login (7 pestañas) |
| `src/vision/camera.py` | Abstracción de cámara OpenCV con aplicación de settings UVC |
| `src/vision/inspector.py` | Wrapper fino sobre `inspect_image()` para el controlador |
| `src/inspection.py` | Lógica de visión central: `inspect_image()`, `inspect_folder()` |
| `src/pipeline/align_edge.py` | Corrección de rotación por Hough |
| `src/pipeline/preprocess.py` | Umbralado / CLAHE / canal → máscara binaria |
| `src/pipeline/detect_holes.py` | Detección de contornos → lista de `Hole` |
| `src/pipeline/compare.py` | Matching nearest-neighbour → `CompareReport` |
| `src/pipeline/grid_fitting.py` | Estimación de espaciado y fase de grilla para el patrón |
| `src/patterns/pattern_build.py` | Construye `holes.json` desde imagen OK de referencia |
| `src/patterns/pattern_io.py` | Carga/guarda `Pattern` (JSON con puntos, radios, metadata de grilla) |
| `src/patterns/roi.py` | ROI opcional |
| `src/utils/config.py` | `load_tolerances(model)` / `save_tolerances()` |
| `src/utils/camera_config.py` | `load_camera_settings(scanner_id)` / `save_camera_settings()` |

## Configuración

### Tolerancias de detección — `config/tolerancias.yaml`

Parámetros globales aplicables a todos los modelos. Los modelos pueden sobreescribir cualquier parámetro en la sección `models:`.

```yaml
threshold: 175              # Umbral de binarización
use_channel: r              # Canal usado: gray, r, g, b
polarity: bright            # bright = agujeros claros (backlight)
min_area: 80.0              # Área mínima de contorno (px²)
circularity_min: 0.8        # Circularidad mínima (0-1)
tol_xy_px: 22.0             # Distancia máxima de matching (px)
aspect_ratio_max: 2.0       # Rechaza contornos elongados
align_match_tol_px: 150.0   # Tolerancia de matching para alineación afín
min_match_count: 6          # Mínimo de matches para aplicar corrección afín
consecutive_nok_frames: 8   # NOK consecutivos para declarar FAULT
burst_frames: 5             # Capturas por disparo (se selecciona la mejor)
burst_delay_ms: 50          # Delay entre capturas del burst (ms)
edge_margin_px: 15.0        # Margen de borde en inspección
pattern_edge_margin_px: 40.0  # Margen de borde al construir patrón (más conservador)
inspection_mode: punch_triggered  # punch_triggered | continuous

# Overrides por modelo (solo lo que difiere del global):
models:
  modelo_A: {}
```

### Mapeo I/O — `config/io_map.yaml`

Define IP del PLC, índice de cámara y señales para cada scanner.

```yaml
plc:
  ip: "192.168.10.175"
  port: 502

scanner_1:
  camera_index: 0
  model: "modelo_A"
  inputs:
    punch_sensor: 0   # X0 — LOW = chapa detenida → inspeccionar
    mode_switch:  1   # X1 — 0=MANUAL, 1=AUTO
  outputs:
    light_red:    0   # Y0 — FAULT / parada forzada
    light_green:  1   # Y1 — RUNNING OK
    light_yellow: 2   # Y2 — racha NOK activa
    light_blue:   3   # Y3 — IDLE / listo
    solenoid:    10   # Y10 — electroválvula (activa punzón)
    backlight:   12   # Y12 — luz de inspección
```

Para agregar un scanner: añadir bloque `scanner_N` en `io_map.yaml` y generar su patrón con `build-pattern`.

### Calibración de cámara — `config/camera.yaml`

Parámetros UVC fijos por scanner (configurables desde pestaña "Cámara" en modo servicio). Ejemplo para C920:

```yaml
scanner_1:
  autofocus: false
  auto_exposure: false
  auto_white_balance: false
  focus: 34
  exposure: -9
  white_balance: 4821
  brightness: 60
  contrast: 140
  saturation: 79
  sharpness: 100
  gamma: 110
  backlight_compensation: 0
```

**Importante:** `backlight_compensation: 0` es crítico cuando se usa backlight como fuente de iluminación principal.

## Layout de datos

```
config/io_map.yaml                     # Mapeo I/O del PLC
config/tolerancias.yaml                # Parámetros de visión y decisión temporal
config/camera.yaml                     # Settings UVC por scanner
data/patterns/{model}/holes.json       # Patrón de referencia (puntos + metadata grilla)
data/patterns/{model}/roi.json         # ROI opcional
data/output/ok/                        # Overlays de frames OK guardados
data/output/nok/                       # Overlays de frames NOK guardados
data/output/debug/                     # Máscaras binarias de debug
```

## Agregar un nuevo modelo

1. Tomar imagen de referencia OK con la pieza correctamente posicionada
2. Ajustar tolerancias si el modelo tiene agujeros de diferente tamaño o distribución (sección `models:` en `tolerancias.yaml`)
3. Construir el patrón:
   ```powershell
   .\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_B --img "data/input/modeloB_OK.jpg"
   ```
4. Verificar con imagen de prueba:
   ```powershell
   .\.venv\Scripts\python.exe -m src.main run-image --model modelo_B --img "data/input/modeloB_test.jpg" --show
   ```

## Puesta en marcha en nueva PC

1. Clonar el repositorio:
   ```powershell
   git clone https://github.com/Nahuel023/app_defyvision_metalconf.git
   cd app_defyvision_metalconf
   ```
2. Ejecutar setup:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
   ```
3. Verificar conectividad con el PLC (ping a la IP configurada en `io_map.yaml`)
4. Calibrar cámara desde modo servicio (pestaña "Cámara") con la máquina encendida
5. Regenerar patrón si el setup óptico difiere del original:
   ```powershell
   .\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/ref.jpg"
   ```
6. Probar en modo servicio antes de pasar a producción

## Stack tecnológico

- **OpenCV** — procesamiento de imagen, detección de contornos, alineación Hough, RANSAC afín
- **PyQt6** — UI operador y servicio; procesamiento en `QThread`
- **pymodbus** — comunicación Modbus TCP con PLC Coolmay CX3G
- **PyYAML** — carga de configuración
- **FFmpeg** — extracción de frames de video (externo, instalado por el script)
