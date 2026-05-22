# CHANGELOG — DefyVision Metalconf

## INSTRUCCIÓN PARA CLAUDE (leer siempre)
> **Al iniciar cualquier sesión de trabajo, leer este archivo completo antes de responder
> o tocar cualquier código. Contiene el historial de decisiones, cambios aplicados y
> contexto que no está en el código ni en el git log.**
>
> **Al finalizar cada cambio de código, actualizar este archivo** con una entrada en la
> sesión activa: qué se cambió, en qué archivo, por qué. Sin esto la trazabilidad se rompe.

---

## Descripción del sistema

Sistema de inspección visual automática para chapas metálicas punzonadas (Metalconf).
Detecta agujeros en piezas estampadas, compara contra un patrón de referencia y
clasifica cada frame como OK/NOK.

**Stack:** Python + OpenCV + PyQt6 + Modbus TCP (PLC Coolmay CX3G).  
**Deployment:** Windows, producción en planta con 2 scanners/cámaras USB.  
**Comando de producción:** `.\.venv\Scripts\python.exe -m src.main run`

### Flujo principal en producción (`run`)
```
PLC (Modbus TCP) ←→ InspectionSystem
                         ├── ScannerController × 2  (FSM: IDLE/RUNNING/FAULT/STOPPED)
                         │      └── Inspector → pipeline de visión
                         └── OperatorUI (PyQt6)
```

### Pipeline de visión por frame
1. Corrección de rotación (Hough sobre borde derecho) → EMA suavizada
2. ROI crop (opcional, desde `data/patterns/{model}/roi.json`)
3. Preprocess: canal R → CLAHE → Otsu → MORPH_OPEN → MORPH_CLOSE
4. Detección de contornos → filtro área/circularidad/aspect ratio → centroide
5. Alineación: grid invariante de posición (modelo_B) ó RANSAC affine (modelo_A)
6. Comparación nearest-neighbor (vectorizado numpy) contra patrón esperado
7. Resultado OK/NOK + overlay anotado

---

## Historial de sesiones

---

### Sesión 2026-05-22 — Tadeo + Claude

#### Contexto de la sesión
Continuación de sesión 2026-05-21. Sistema estable en 185/185 OK con grabación de referencia.
Trabajo en mejoras visuales del overlay de centrado y rediseño del tab de grabación.

---

#### Cambio 28 — Centrado: detección real en ventanas full-frame + fix perf anotación

**Motivación / diagnóstico:**
`run-folder` sobre 185 frames tardaba ~182 s (~1 s/frame). El origen eran dos bugs:

1. **Bug funcional:** `compute_centering()` recibía la imagen ROI-recortada (650×1077).
   La ROI excluye ambos backlights (izq: col 416–687; ROI empieza en x=710; der: col
   1374–1645; ROI termina en x=1360). Sin backlight, el algoritmo detectaba los propios
   bordes de la ROI como borde de chapa → `left_x=0, right_x=649` (nulos).

2. **Bug de rendimiento en anotación:** `_draw_edge_polyline()` llamaba
   `_draw_transparent_line()` una vez por segmento (15 segmentos × 4 bordes = 60 llamadas
   por frame). Cada llamada: `np.zeros_like(img_1077×650)` + `any(axis=2)` sobre 700 K
   píxeles → 3 s en 5 frames solo en numpy.ufunc.reduce según cProfile.

**Solución:**

A) **`src/pipeline/edge_centering.py`** — reescrito completamente:
   - Nueva firma: `compute_centering(img_full, holes, roi=None, tol_px=0.0)`
   - Cuando `roi` está presente, `img_full` es el frame completo alineado (ambos
     backlights visibles). Cuando `roi=None`, mantiene la detección legacy por bandas
     (sin cambio de comportamiento para el path sin ROI).
   - Nueva función `_detect_sheet_edges_in_windows()`:
     - Ventana izquierda: `[max(0, roi.x-350) : roi.x+80]` (full-frame x)
     - Ventana derecha: `[roi.x+roi.w-80 : min(W, roi.x+roi.w+350)]`
     - Canal R (backlight rojo → brillante)
     - Downsampling de filas con paso `_DS=4` para velocidad
     - Kernel de suavizado pequeño `_SMOOTH_K=7` (preserva magnitud del gradiente)
     - Left: `argmin(diff(col_profile))` → transición brillante→oscura
     - Right: `argmax(diff(col_profile))` → transición oscura→brillante
     - Umbral: rango de brillo ≥ 20 AND gradiente ≥ 3% del rango
     - Devuelve coordenadas en espacio ROI-relativo (mismo que los agujeros)
   - `_N_BANDS=16` de detección por banda, mismo número que antes
   - `_MIN_RELIABLE_BANDS=6` sin cambio
   - Márgenes calculados en espacio ROI (consistente con holes)

B) **`src/inspection.py` línea 278:**
   ```python
   # ANTES (roto):
   centering = compute_centering(img, holes, tol_px=center_offset_tol_px)
   # DESPUÉS (correcto):
   centering = compute_centering(img_aligned, holes, roi=roi, tol_px=center_offset_tol_px)
   ```

C) **`src/pipeline/annotate.py` — `_draw_edge_polyline()`:**
   - Antes: 1 capa temporal + 1 alpha blend POR SEGMENTO (hasta 15 por polilínea)
   - Después: todos los segmentos se dibujan en UNA capa, 1 sola pasada de alpha blend
   - Reduce 60 operaciones de blend/frame → 4 (una por borde)
   - Eliminada la llamada a `_draw_transparent_line` desde `_draw_edge_polyline`

**Resultados medidos:**
- `frame_0009`: `left_x = -31.2 px` (ROI-relativo) → full-frame ≈ 679 px (borde real)
              `right_x = 700.9 px` (ROI-relativo) → full-frame ≈ 1411 px (borde real)
  (antes: 0 y 649 — bordes de ROI; o 0 y 1919 — bordes de frame)
- 16/16 bandas detectadas, `centering_reliable=True`
- `compute_centering`: ~6 ms/frame (antes ~1 010 ms)
- `run-folder` 185 frames: **27.9 s total** (antes 182.75 s → 6.5× más rápido)
- `inspect_image` aislado: ~175 ms/frame (antes ~1 094 ms)
- 185/185 OK mantenido (centrado es informacional, `center_offset_tol_px=0.0`)

**Sin tocar:** PLC, solenoides, lógica temporal, lógica de comparación de agujeros.

---

#### Cambio 27 — Rediseño tab Grabación: estética industrial + exportación de imágenes

**Motivación:** El tab de Grabación en la UI de Servicio tenía controles apilados en una sola
fila horizontal, sin jerarquía visual, sin indicador de estado claro y sin forma de guardar
las imágenes analizadas.

**Decisión:**
- Rediseño completo de `RecordingTab` en `src/ui/service.py` con layout industrial oscuro
- Separación clara en 3 secciones: GRABACIÓN / ANÁLISIS / NAVEGADOR DE CAPTURAS
- Panel de estado de grabación prominente (badge con estado+count+carpeta)
- Navegación con botones primero/último, contador de frame grande y legible
- Toggle de overlay con estilo ON/OFF coloreado
- Nuevo sistema de exportación: guardar frame actual (auto a data/output/export/)
  y exportar rango de frames (spinbox Desde/Hasta → carpeta con timestamp)

**Archivos modificados:**
- `src/ui/service.py`:
  - Añadido `QFrame` a imports (necesario para separadores horizontales/verticales)
  - `RecordingTab` completamente reescrito:
    - `_build_recording_section()`: config row + action row con badge de estado
    - `_build_analysis_section()`: botones + progress + resumen coloreado
    - `_build_browser_section()`: nav (primero/último), toggle overlay coloreado,
      fila de exportación (guardar actual + rango con spinboxes)
    - `_set_rec_badge()`: actualiza badge STANDBY/GRABANDO/LISTO/ANALIZANDO/ANALIZADO
    - `_save_current_frame()`: auto-save overlay → data/output/export/{ts}.png
    - `_export_range()`: exporta frames f_from..f_to → data/output/export/rango_{ts}/
    - `_update_export_range_max()`: sincroniza spinboxes al cargar/grabar frames
    - `_update_export_label()`: actualiza texto del botón exportar con cantidad
    - `_on_overlay_toggled()`: toggle ON/OFF con texto dinámico
    - `_hline()`, `_vline()`, `_lbl()`, `_make_combo()`: helpers de UI
    - `_mk_btn()` ahora acepta parámetros h/fs/w para mayor flexibilidad
  - Funcionalidades existentes 100% preservadas (grabación, análisis, carga de carpeta,
    análisis en vivo, info de cámara, resumen temporal)

**Nuevo flujo de exportación:**
- Frame actual: botón "Guardar frame actual" (habilitado solo si hay resultado)
  → guarda `frame_NNNN_STATUS_YYYYMMDDHHMMSS.png` en `data/output/export/`
- Rango: spinboxes Desde/Hasta + botón "Exportar N frames"
  → crea `data/output/export/rango_YYYYMMDDHHMMSS/` con todos los overlays del rango
  → habilitado solo cuando el análisis cubre el rango completo seleccionado

---

#### Cambio 26 — Detección de bordes real por bandas (polyline overlay)

**Motivación:** El overlay de centrado mostraba líneas verticales perfectas (un único X por lado)
basadas en el perfil de columna global. No reflejaba la realidad: si el borde de la chapa no
es perfectamente vertical o hay variación por altura, se perdía esa información.

**Decisión:** Dividir la imagen en 16 bandas horizontales, detectar el borde metálico en cada
banda por separado, y dibujar una polilínea real en lugar de una línea perfectamente vertical.
Para el patrón punzonado, usar los agujeros reales detectados por banda (hole.x ± hole.r),
no el bbox del patrón teórico.

**Archivos modificados:**

- `src/pipeline/edge_centering.py`:
  - Nueva constante `_N_BANDS = 16`, `_MIN_RELIABLE_BANDS = 6`
  - Nueva función `_detect_edges_by_band()` → devuelve `dict[band_idx → (x, cy)]` para left/right
  - Nueva función `_pattern_bounds_by_band()` → bounds del patrón real por banda
  - Nueva función `_fit_line_robust()` → ajuste x=a*y+b con sigma-clip outlier rejection
  - Nueva función `_line_x_at_y()` → evalúa la línea en Y dado
  - `_detect_metal_edges_full()` → fallback de imagen completa (renombrado, antes `_detect_metal_edges`)
  - `CenteringResult` extendido con nuevos campos (todos con default para compatibilidad):
    - `left_edge_points`, `right_edge_points`: tupla de (x, y) por banda, borde de chapa real
    - `pattern_left_points`, `pattern_right_points`: tupla de (x, y) por banda, borde patrón real
    - `left_margin_std`, `right_margin_std`: std dev de márgenes por banda
    - `centering_reliable`: False si < 6 bandas detectadas
  - `compute_centering()` reescrito para usar detección por bandas:
    - `left_x`, `right_x` escalares ahora vienen de la línea robusta evaluada en mid-height
    - Si no hay suficientes puntos, fallback a mediana, luego a perfil de imagen completa
    - Estadísticas de margen por banda cuando hay correspondencia edge+patrón

- `src/pipeline/annotate.py`:
  - Nueva función `_draw_edge_polyline()` a nivel módulo: dibuja polilínea con alpha + puntos sample
  - `draw_centering_overlay()` actualizado:
    - Usa polilínea real cuando `left_edge_points` / `right_edge_points` tienen ≥ 2 puntos
    - Fallback a línea vertical cuando no hay datos por banda
    - Texto de margen extendido: agrega `Var: ±Xpx` (std dev de márgenes)
    - Badge "BORDES NO CONFIABLES" (naranja) cuando `centering_reliable=False`
    - Badge "NOK CENTRADO" (rojo) preservado sin cambios

**Comportamiento observado en debug_crop_frame9.png (1077×1054px):**
- 16/16 bandas detectadas en ambos lados → `centering_reliable=True`
- `left_x≈203px`, `right_x≈893px` (consistente con medición anterior)
- Polilínea gris para bordes de chapa, cyan para patrón, puntos pequeños en cada muestra

**Garantía 185/185 OK mantenida:** La lógica de inspección (detección, comparación,
regla temporal) no se tocó. El centrado es puramente informacional (`center_offset_tol_px=0.0`
por defecto).

---

### Sesión 2026-05-19 — Tadeo + Claude

#### Contexto de la sesión
Primera sesión de trabajo con el código ya en estado V1 funcional.
Sistema con 2 scanners, PLC, cámaras USB montadas fijas, backlight estable.
`consecutive_nok_frames: 9999` en config = FAULT deshabilitado temporalmente (calibración).

---

#### Cambio 1 — Bloqueo de seguridad solenoides Y10/Y11

**Motivación:** Los solenoides Y10 (scanner_1) e Y11 (scanner_2) controlan pistones
físicos en la máquina. En modo diagnóstico HW había botones que los podían activar
accidentalmente, representando un riesgo real de accidente. El control automático de
pistones está planificado pero no implementado aún.

**Decisión:** Bloqueo doble — software + visual — hasta que el control automático esté listo.

**Archivos modificados:**
- `src/plc/io_map.py` → `IOMap.write()` rechaza cualquier `signal.endswith(".solenoid") and value=True`
  con WARNING en log y retorna False. Última línea de defensa.
- `src/controller/scanner_controller.py` → removidas las 3 líneas `write(solenoid, True)`
  en `start()` (AUTO y MANUAL) y `start_simulate()`. Los `write(solenoid, False)` de
  parada/fault/shutdown se mantienen intactos (son salidas de seguridad).
- `src/ui/service.py` → botón "Solenoide" deshabilitado (gris, texto "LOCK") en:
  - Tab "Prueba de salidas PLC" (PLCOutputTestTab)
  - Tab "Diagnóstico HW" → botones Y8 e Y9 (offsets de los solenoides)
  - `refresh()` de ambas tabs omite esos botones para no sobreescribir el estilo

**Para re-habilitar en el futuro:** Remover el guard en `IOMap.write()`, restaurar
las líneas en `scanner_controller.py`, y re-habilitar los botones en `service.py`.

---

#### Cambio 2 — Arranque rápido (startup)

**Problema:** El comando `run` tardaba 2–4 segundos en mostrar la UI porque
`Camera.start()` llamaba `_open_capture()` de forma sincrónica. MSMF (Windows Media
Foundation) + 3 warmup frames a 5fps = ~1.5s de bloqueo ANTES de que aparezca la ventana.

**Solución:** `Camera.start()` ahora es no-bloqueante. Lanza el thread de captura
inmediatamente y retorna `True`. El loop de captura ya tenía `retry_wait=0` en
primera iteración, por lo que abre la cámara en background sin cambios funcionales.

**Archivos modificados:**
- `src/vision/camera.py` → `start()` no llama `_open_capture()` sync; agrega flag
  `_first_open` en `_capture_loop` para loguear "iniciada" vs "reconectada" correctamente.
- `src/controller/system.py` → `start_cameras()` simplificado: sin wrapper de threads
  (innecesario ahora que `cam.start()` es instantáneo). Removido import `threading`.

**Resultado:** UI aparece en ~300–600ms. Cámaras conectan en background mostrando
feed cuando están listas.

---

#### Cambio 3 — Mejoras al pipeline de visión (fiabilidad + rendimiento)

**Motivación:** Mejorar fiabilidad y reducir latencia del pipeline OpenCV sin salir
de OpenCV. Cámara fija, iluminación estable y específica (backlight), ese problema
ya está resuelto en hardware.

**3a. Vectorización de compare.py**
- `compare_missing_only()` reemplaza loop Python O(n×m) por matriz de distancias
  numpy calculada de una sola vez. Para 200 agujeros: 40.000 iteraciones Python →
  una operación numpy + 200 argmin vectorizados. ~10–30× más rápido por frame.
- Mismo algoritmo greedy, mismo comportamiento, más rápido.

**3b. MORPH_CLOSE en preprocess.py**
- Después del MORPH_OPEN (elimina ruido), se agrega MORPH_CLOSE (kernel 5×5, 1 iter).
- Rellena micro-gaps dentro de la máscara binaria de cada agujero causados por
  desgaste leve del punzón o micro-reflejos en el borde del contorno.
- Reduce falsos "missing" en chapas con agujeros levemente irregulares.

**3c. Centroide por momentos en detect_holes.py**
- Posición (x, y) del agujero calculada con `cv2.moments` en lugar de
  `minEnclosingCircle`. El centroide es más estable ante pixels outlier en el borde.
- El radio `r` sigue viniendo de `minEnclosingCircle` (correcto para edge_margin_px).

**3d. max_area en config.py**
- `max_area: None` agregado a DEFAULT_TOLERANCES. Por defecto deshabilitado (sin
  límite superior de área).
- Setear por modelo en `tolerancias.yaml` para rechazar contornos grandes (reflejos,
  suciedad en lente). Ejemplo: `max_area: 5500.0` para modelo_B (área mediana ~1100 px²).

**3e. EMA del ángulo de rotación en align_edge.py**
- `align_image_by_right_edge()` acepta `ema_state: dict` (propiedad del Inspector).
- Si Hough detecta líneas: actualiza EMA con alpha=0.25. Si no detecta: usa último
  ángulo suavizado conocido. Absorbe estimaciones ruidosas en frames con borde poco nítido.

**3f. Vectorización de inlier check + RANSAC en inspection.py**
- Inlier check de pre-shift: reemplaza loop Python por operación matricial numpy
  `(n_det, n_pat, 2)` → min distancia vectorizada.
- RANSAC `maxIters`: 2000 → 500. Suficiente con `confidence=0.99` para puntos bien matcheados.

**3g. Cache en inspector.py**
- `Inspector` cachea tolerancias, patrón y ROI por `(model, scanner_id)`.
- Elimina 3 lecturas de disco por frame (~30 I/O ops/seg con 2 scanners a 5fps).
- `invalidate(model, scanner_id)` fuerza recarga. Se llama automáticamente en
  `ScannerController.set_model()` cuando el operador cambia de modelo desde la UI.
- EMA state por scanner también vive en el Inspector.

---

---

### Sesión 2026-05-19 (continuación) — Tadeo + Claude

#### Cambio 4 — Visor de imágenes zoomable en modo servicio (RecordingTab)

**Motivación:** La imagen de frame/overlay en la tab "Grabación" se mostraba estática
en un `QLabel` escalado a tamaño fijo. No era posible hacer zoom ni pan para analizar
detalles del patrón de agujeros.

**Solución:** Nuevo widget `ZoomableImageView(QWidget)` que reemplaza el `QLabel`.

**Funcionalidades:**
- Rueda del mouse → zoom hacia el cursor (15% por tick, rango 5%–3000%)
- Click + drag → pan libre
- Doble click → fit automático (ajustar a ventana)
- Badge "ZZ%" en esquina superior derecha indicando zoom actual
- Botón "Ajustar" en la barra de navegación → equivale al doble click

**Archivos modificados:**
- `src/ui/service.py`:
  - Imports: `QPainter`, `QPointF`, `QRectF` agregados
  - Clase `ZoomableImageView` insertada antes de `RecordingTab`
  - `RecordingTab._img_label` (QLabel estático) reemplazado por `self._img_view`
  - `_show_frame()`: llama `self._img_view.set_pixmap(px)` en lugar de `setPixmap(px.scaled(...))`
  - `_on_start()`: llama `self._img_view.clear("Sin frames")` en lugar de `setText`
  - Botón "Ajustar" agregado al nav_row, conectado a `self._img_view.fit`

---

---

### Sesión 2026-05-20 — Tadeo + Claude

#### Contexto de la sesión
Sesión dividida en dos partes. La primera parte (resumen de sesión anterior) incluyó
múltiples mejoras al pipeline y la UI antes de quedar en el contexto de la sesión.
La segunda parte (en esta sesión) se enfocó en analizar grabaciones de la planta y
corregir detecciones falsas masivas en modelo_B.

**Commits de esta sesión:**
- `e8537fd` — Fix false detections en modelo_B: ROI ajustada, grid dy=20, matcher closest-first

---

#### Cambio 5 — Parámetros morfológicos configurables en preprocess.py

**Motivación:** `blur_ksize`, `open_ksize`, `close_ksize` estaban hardcodeados.
Necesario poder ajustar por modelo sin tocar código.

**Archivos modificados:**
- `src/pipeline/preprocess.py` → `preprocess_for_holes()` acepta `blur_ksize=5`,
  `open_ksize=3`, `close_ksize=5`. Auto-corrige blur_ksize par → impar. Valor 0 deshabilita
  la operación morfológica correspondiente.
- `src/utils/config.py` → `DEFAULT_TOLERANCES` agrega `blur_ksize`, `open_ksize`,
  `close_ksize`, `min_detection_ratio`, `max_extra`, `startup_selftest_enabled`,
  `selftest_timeout_s`, `max_inspection_hz`, `grid_min_spacing`.
- `src/patterns/pattern_build.py` → pasa los tres parámetros a `preprocess_for_holes()`.

---

#### Cambio 6 — Extra detections + métricas de calidad por frame

**Motivación:** El resultado de inspección no reportaba cuántos agujeros "de más"
se detectaban (spurious / reflejos), ni qué tan bien se detectó el patrón completo.

**Archivos modificados:**
- `src/pipeline/compare.py`:
  - `CompareReport` agrega campos `extra: int` y `extra_points: List[Tuple]`
  - `compare_missing_only()` acepta `max_extra=-1` (deshabilitado por defecto)
  - `nok = missing > max_missing OR (max_extra >= 0 AND extra > max_extra)`
- `src/pipeline/annotate.py`:
  - `draw_compare_overlay()` acepta `extra_points=()`
  - Dibuja diamantes naranjas (`cv2.MARKER_DIAMOND`) para cada extra
  - Texto de status coloreado (verde=OK, rojo=NOK, amarillo=UNCERTAIN)
- `src/inspection.py`:
  - `InspectionResult` agrega `detection_ratio: float = 1.0` y `alignment_ok: bool = True`
  - `FolderInspectionSummary` agrega `uncertain: int = 0`
  - `_draw_warnings()`: escribe texto amarillo de advertencia sobre el overlay
    cuando `detection_ratio < min_detection_ratio` o `alignment_ok=False`
  - Calcula `detection_ratio = len(holes) / n_expected_total`
- `src/main.py`:
  - `cmd_run_image()` muestra: status, expected, detected, missing, extra, detection_ratio, alignment_ok
  - `cmd_run_folder()` muestra por frame: `status/decision  streak=N  missing=M  extra=E  ratio=R%  [FLAGS]`
  - FLAGS: `DETECCION_BAJA` (ratio<50%), `ALIGN_FALLBACK`
  - Línea resumen: `avg_detection_ratio=X%  align_failures=N/total`

---

#### Cambio 7 — Métricas de calidad en ScannerController y Recorder

**Archivos modificados:**
- `src/controller/scanner_controller.py`:
  - Acumula `_total_detection_ratio` y `_align_fail_count` por sesión
  - `get_status()` retorna `avg_detection_ratio` y `align_fail_count`
  - `inject_result()` pasa `detection_ratio=1.0, alignment_ok=True` en modo simulación
- `src/metrics/recorder.py`:
  - `_init_db()`: migración de esquema con `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`
    para agregar columnas nuevas sin romper DBs existentes
  - `_INSERT` y `_snapshot()` incluyen `avg_detection_ratio`, `align_fail_count`

---

#### Cambio 8 — Escritura batch de salidas PLC (backlight y luces)

**Problema:** `_set_lights()` escribía 4 coils individuales en 4 transacciones Modbus.
Con el poller leyendo cada 50ms, los writes competían y la luz de fondo demoraba hasta 200ms.
Adicionalmente, el backlight se encendía DESPUÉS de arrancar los threads → el selftest
corría sobre un frame oscuro y fallaba.

**Solución:**
- `src/plc/client.py` → nuevo `write_coils_batch(offset, values)`: escribe N coils
  contiguos en UNA sola transacción Modbus usando `write_coils()`.
- `src/plc/io_map.py` → nuevo `write_batch(signals)`: detecta offsets contiguos y
  usa `write_coils_batch()`, con fallback a escrituras individuales si no son contiguos.
- `src/controller/scanner_controller.py`:
  - `_set_lights()` ahora usa `write_batch()` → 1 transacción en vez de 4
  - En `start()` modo AUTO: `io.write(backlight, True)` se llama **ANTES** de
    `_start_all_threads()` (antes era después) — backlight encendido desde el primer frame
  - Selftest deshabilitado por defecto (`startup_selftest_enabled: False` en config);
    tiene delay de 150ms al arrancar para evitar frame oscuro si se habilita
  - Limita rate de inspección con `max_inspection_hz` (usando `time.monotonic()`)

---

#### Cambio 9 — RecordingTab scrollable + imagen más grande

**Motivación:** La tab de Grabación en modo servicio no tenía scroll; la imagen
quedaba pequeña y no se podía ver bien el resultado del análisis.

**Archivos modificados:**
- `src/ui/service.py` → `RecordingTab._build_ui()`:
  - Contenido envuelto en `QScrollArea` (scrollable verticalmente)
  - `self._img_view.setMinimumHeight(640)` para que la imagen aparezca más grande

---

#### Cambio 10 — Análisis de grabaciones de planta (diagnóstico)

**Contexto:** Se analizaron 3 sesiones de grabaciones del scanner_1 (modelo_B)
almacenadas en `data/recordings/` de la máquina de planta:
- `20260512_194928`: 321 frames
- `20260512_203224`: 53 frames
- `20260512_203246`: 195 frames

**Hallazgos del primer análisis (con parámetros incorrectos):**
- Se corría `run-folder --model modelo_B` SIN `--scanner scanner_1` →
  cargaba el patrón viejo de `data/patterns/modelo_B/holes.json` (imagen 370×1080)
  y la ROI incorrecta `{"x":573, "y":0, "w":247, "h":720}` (de cámara de menor resolución).
- Con el scanner correcto: frame_0009 (mejor frame) daba `missing=25, extra=105`.

**Diagnóstico de las 3 causas raíz:**

**Causa 1 — ROI incorrecta (incluye backlight desnudo):**
- Cámara es 1920×1080. El backlight desnudo sin material aparece en:
  - Frame col 416–687 (lado izquierdo, 271px de ancho)
  - Frame col 1374–1645 (lado derecho, 271px de ancho)
- La ROI anterior (`x=482, w=1054`) capturaba ambas zonas brillantes.
- Con `polarity: bright`, el backlight desnudo aparece como agujeros.
- Estos falsos detectados corrompían la estimación de fase de la grilla, desplazando
  todas las posiciones esperadas y generando 100+ extras y 25+ faltantes.
- **Fix:** ROI nueva `{"x":710, "y":3, "w":650, "h":1077}` — excluye ambas zonas
  de backlight con ~23px de margen izquierdo y ~14px de margen derecho.

**Causa 2 — Grid dy=40 en vez de 20 (grilla escalonada):**
- La lámina microperforada tiene filas de agujeros alternadas cada ~20px en Y
  (similar a empaque hexagonal).
- `estimate_spacing(ys, min_spacing=30)` filtraba las diferencias ~20px y encontraba
  el período doble (40px), asignando el mismo (ci, cj) a dos agujeros distintos.
- `grid_compare_points` deduplicaba uno de los dos → solo 104 posiciones únicas de 152.
- **Fix:** Nuevo parámetro `grid_min_spacing: 15.0` en `tolerancias.yaml` bajo `modelo_B`.
  Con min_spacing=15, `estimate_spacing` encuentra dy=20 → 152 celdas únicas.
- `src/utils/config.py` → `DEFAULT_TOLERANCES` agrega `"grid_min_spacing": 30.0`
- `src/patterns/pattern_build.py` → pasa `grid_min_spacing` a `estimate_spacing()`

**Causa 3 — Matcher greedy procesa en orden de grilla, no por proximidad:**
- `compare_missing_only()` iteraba expected_points en orden secuencial.
- Un punto esperado lejano (ej. 24px) que aparecía ANTES en la lista "robaba"
  la detección de un punto esperado cercano (6px), dejándolo como "missing".
- Con dy=20 y tol_xy_px=28, dos filas adyacentes (20px) competían por el mismo detectado.
- **Fix:** `src/pipeline/compare.py` → `order = np.argsort(dist2.min(axis=1))`
  antes del loop greedy: se procesan primero los pares más cercanos.

**Resultados después de los 3 fixes:**
- Frame 0009 (referencia): `missing=1, extra=1` (antes: `missing=25, extra=105`) ✓
- `avg_detection_ratio` en carpeta 321 frames: 45% → 77%
- Los frames con material en movimiento (blur) siguen con ratio bajo: comportamiento CORRECTO
  (lámina moviéndose = agujeros borrosos = no inspeccionar esos frames en producción)
- `align_failures`: 25/321 → 1/321 (eliminar backlight resolvió casi todos los fallos de alineación)

---

#### Parámetros config actuales (tolerancias.yaml) después de esta sesión

```yaml
# Globales
threshold: 175
use_channel: r
polarity: bright
min_area: 80.0
circularity_min: 0.8
tol_xy_px: 22.0
max_inspection_hz: 15
consecutive_nok_frames: 9999    # CALIBRACIÓN: FAULT deshabilitado
continuous_position_threshold: 0.0

# modelo_B (scanner_1, microperforado)
min_area: 300.0
circularity_min: 0.75
tol_xy_px: 28.0
edge_margin_px: 25.0
pattern_edge_margin_px: 25.0
grid_max_missing: 30
grid_min_spacing: 15.0          # CLAVE: dy=20 en vez de 40 para grilla escalonada
consecutive_nok_frames: 40
continuous_position_threshold: 4.0

# ROI scanner_1/modelo_B
{"x": 710, "y": 3, "w": 650, "h": 1077}   # excluye backlight col 416-687 y 1374-1645
```

---

### Sesión 2026-05-21 — Tadeo + Claude

#### Contexto de la sesión
Sesión larga de diagnóstico y calibración del sistema en modelo_B (microperforado / scanner_1).
Se realizó análisis de la grabación `20260519_121741` (185 frames, material bueno en movimiento continuo).
Objetivo: eliminar falsos NOK, mejorar detección en blur, corregir errores de grilla.

**Commits de esta sesión:**
- `134cc0e` — Init backlight ON al conectar PLC (Y12/Y13 siempre visibles)
- `b5789fd` — Centrado de chapa: detección de bordes laterales y offset del patrón
- `4a160c9` — Etiquetado diferenciado de NOK por centrado vs agujeros
- `dc65e0e` — Overlay imagen completa (sin recorte ROI) + bordes en gris semitransparente
- `23ef8dc` — Fix grid phase estimation: 2D Y-scan + X re-estimación + sincronización de patrones
- `811430c` — Mejoras post-análisis: bbox filter, grid_max_missing, quality_ratio_min
- `8163012` — tol_xy_px modelo_B: 12→18px — reduce falsos raw NOK de 138 a 22
- `777b99e` — Fix detección blur: min_area modelo_B 300→250px²
- `ed8916a` — Fix borde de patrón y tolerancia: Y-clip + tol_xy_px 18→22

**Resultado final de la sesión:**
```
185/185 raw OK, 0 raw NOK, 0 temporal NOK
avg_detection_ratio = 100%, align_failures = 0/185
Missing en frames limpios = 0
```

---

#### Cambio 11 — Backlight siempre ON al iniciar

**Motivación:** Las cámaras no eran visibles al arrancar si el backlight (Y12/Y13) no
estaba encendido. Se quería que las salidas de backlight inicializaran siempre como ON
al conectar el sistema, independientemente del estado del PLC.

**Archivos modificados:**
- `src/controller/scanner_controller.py` → `initialize_lights()` escribe
  `io.write("{id}.backlight", True)` antes de configurar las luces de estado.
  El backlight queda ON desde el primer ciclo.

---

#### Cambio 12 — Medición de centrado de chapa (edge centering)

**Motivación:** Para MICROPERFORADO el patrón de punzonado siempre debe estar centrado
entre los bordes laterales de la chapa. Se quería medir el offset y etiquetar frames
fuera de tolerancia sin perder la inspección de agujeros.

**Implementación:**

**`src/pipeline/edge_centering.py`** (nuevo):
- `_detect_metal_edges(img_bgr)`: usa el percentil 20 por columna (perfil oscuro=metal,
  brillante=backlight). Localiza el primer y último píxel oscuro → borde izquierdo y derecho.
- `compute_centering(img_bgr, holes_xs, tol_px)` → `CenteringResult` con:
  `left_x`, `right_x`, `sheet_center_x`, `holes_center_x`, `offset_px`, `within_tol`.

**`src/pipeline/annotate.py`**:
- `_draw_transparent_line()`: blend alpha por pixel para líneas semitransparentes sin scipy.
- `draw_centering_overlay()`: dibuja bordes metálicos (gris semitransparente alpha=0.45),
  línea de centro de chapa (naranja discontinua), línea de centro de agujeros (blanca),
  flecha de offset, badge "NOK CENTRADO" cuando `tag_nok=True`.

**`src/inspection.py`**:
- `InspectionResult` agrega `centering: CenteringResult | None` y `centering_nok: bool`.
- `_inspect_bgr()` llama `compute_centering()` y combina con el resultado de agujeros:
  `final_status = "NOK" if (report.status == "NOK" or centering_nok) else "OK"`.

**`src/utils/config.py`**: agrega `"center_offset_tol_px": 0.0` a DEFAULT_TOLERANCES.

**`src/ui/operator.py`**: card "CENTRADO" en panel de métricas → muestra offset en px,
naranja cuando fuera de tolerancia.

**`src/ui/service.py`**: estadísticas de centrado al final del análisis de grabación.

**Etiquetado diferenciado (Cambio 13):**
- La UI y el overlay distinguen la causa del NOK:
  - "NOK AGUJEROS" → rojo
  - "NOK CENTRADO" → naranja
  - "NOK AGUJEROS + CENTRADO" → rojo con badge adicional

---

#### Cambio 13 — Overlay imagen completa sin recorte ROI

**Problema:** El overlay solo mostraba la ROI recortada. El operador no podía ver la
imagen completa de la cámara ni los bordes de la chapa.

**Fix en `src/inspection.py`** → `_inspect_bgr()`:
- Anotaciones se dibujan sobre `img` (ROI recortada) con coordenadas relativas a la ROI.
- El resultado se compone sobre `img_aligned` completa: si hay ROI, se hace paste en la
  posición `[roi.y:roi.y+roi.h, roi.x:roi.x+roi.w]`. Sin ROI: se usa directamente.
- El operador ve el encuadre completo de la cámara con las anotaciones correctamente
  posicionadas dentro de la zona de inspección.

---

#### Cambio 14 — Fix crítico: estimación de fase de grilla (grid_fitting.py)

**Problema raíz identificado en esta sesión:**
Los archivos de patrón y ROI a nivel modelo (`data/patterns/modelo_B/`) estaban
desactualizados respecto a los de `data/patterns/scanner_1/modelo_B/`:
- `holes.json` modelo-nivel: dx=50, 155 puntos (patrón viejo incorrecto)
- `roi.json` modelo-nivel: `{x:573, w:247}` (ROI vieja, muy estrecha)
El comando `run-folder` sin `--scanner` cargaba estos archivos obsoletos.

**Problema 2 — Fase X fija bloqueaba deriva lateral:**
El código tenía `origin_x = phase_ref_x` (fase fija) para evitar la "ambigüedad bimodal"
de grillas escalonadas. Pero para ESTA grilla, el offset escalonado está codificado en
los valores enteros de `ci` (ci par = filas pares, ci impar = filas impares). Por lo tanto
`x % dx = phase_x` para TODOS los agujeros → no hay distribución bimodal. Fijar la fase
impedía compensar derivas laterales de ±5-15px del material.

**Problema 3 — Escaneo Y en 1D daba falsos matches en frames de transición:**
El escaneo de fase Y buscaba la fase que maximizara coincidencias en Y únicamente.
En frames con blur/transición, agujeros de filas adyacentes podían "matchear" la Y
esperada sin estar en la X correcta → se elegía una fase Y incorrecta que colocaba
posiciones esperadas ~17px lejos de las reales.

**Fixes aplicados en `src/pipeline/grid_fitting.py`:**

**Fix 1 — X: re-estimar fase por frame:**
```python
# Escaneo X sobre [0, dx) igual que Y
for px_cand in np.arange(0.0, dx, 1.0):
    exp_xs = px_cand + ci_arr * dx
    ...
    count_x = int((diffs_x.min(axis=1) <= tol_x).sum())
origin_x = best_phase_x
```

**Fix 2 — Y: escaneo 2D (X + Y simultáneamente):**
```python
# Precomputa x_match con origin_x ya conocido
x_match = |det_xs - exp_xs| <= tol_x   # (n_det, n_cells)
for phase_candidate in [0..dy):
    y_match = |det_ys - exp_ys| <= tol_y  # (n_det, n_cells)
    both = x_match & y_match & valid
    count = both.any(axis=1).sum()
```
Así un agujero detectado solo cuenta si está dentro de tol en X E Y del mismo punto
esperado → elimina los falsos matches de filas adyacentes.

**Sincronización de archivos:**
- `data/patterns/modelo_B/holes.json` copiado desde `scanner_1/modelo_B/holes.json`
  (dx=28, dy=22, 258 puntos)
- `data/patterns/modelo_B/roi.json` copiado desde `scanner_1/modelo_B/roi.json`
  (`x=710, w=650`)

**Resultado:** Paso de `raw_ok=0/185` (con patrón viejo) a `raw_ok=162/185` con los fixes.

---

#### Cambio 15 — Mejoras post-análisis de grabación

**Análisis de la grabación 20260519_121741 (185 frames):**
- Detección media: ~383 agujeros/frame con params viejos (ratio 165%)
- Missing baseline: 2–15 en frames buenos
- 4 frames raw NOK transitorios por blur de movimiento

**Fixes:**

**`src/inspection.py`** — Filtro bbox antes de matching:
- Antes de llamar `compare_missing_only()`, los detectados se filtran al bounding box
  de los puntos esperados + `bbox_filter_margin_px` (configurable).
- Elimina agujeros reales del material fuera de la ventana del patrón, reduciendo el
  conteo de "extra" y el costo computacional del matching.

**`src/inspection.py`** — `capture_quality_degraded`:
- Nuevo campo `capture_quality_degraded: bool` en `InspectionResult`.
- Si `quality_ratio_min > 0` y `ratio < quality_ratio_min` (pero ≥ `min_detection_ratio`):
  se pone en `True`. No afecta el NOK. Visible en overlay ("CALIDAD DEGRADADA") y log.
- Útil para detectar blur de movimiento independientemente de la decisión de inspección.

**`config/tolerancias.yaml` — modelo_B:**
- `grid_max_missing: 30 → 35` (absorbe picos de blur sin comprometer detección de punzón roto)
- Nuevos parámetros: `bbox_filter_margin_px: 20.0`, `quality_ratio_min: 0.0` (deshabilitado)

---

#### Cambio 16 — tol_xy_px 28→12→18→22 (calibración iterativa)

**Historia de la tolerancia durante esta sesión:**

| Valor | Resultado | Problema identificado |
|-------|-----------|----------------------|
| 28.0 | raw_ok=162/185 | Matching ambiguo: tol=dx, zonas solapadas |
| 12.0 | raw_ok=0/185 | Patrón viejo cargado → 0 detecciones (bug ROI) |
| 12.0 (fix ROI) | raw_ok=34/185 | Fase Y 1D → posiciones esperadas ~17px off |
| 18.0 (fix fase) | raw_ok=163/185 | Agujeros con blur <300px² filtrados |
| 18.0 (fix area) | raw_ok=181/185 | Drift lateral en borde inferior >18px |
| 22.0 + Y-clip | **185/185** | ✓ |

**Razonamiento para tol=22:**
- Error real de centroide de detección: <5px
- Adjacent same-row holes: 28px de separación → zonas no se solapan en la práctica
- Necesario para absorber drift de borde + blur residual

---

#### Cambio 17 — min_area 300→250 para modelo_B (blur de movimiento)

**Diagnóstico:**
- frames con blur (0066, 0067, etc.): `detect_loose=287` vs `detect_strict=211`
- Histograma de áreas reveló: **50–52 blobs reales en rango 250–299px²** en frames con blur
- En frames limpios: prácticamente 0 blobs en ese rango (gap natural en 200–250px²)
- El blur de movimiento reduce el área aparente de los agujeros de ~350–450px² a 250–299px²

**Fix en `config/tolerancias.yaml`:** `min_area: 300.0 → 250.0` para modelo_B.

**Resultado:** frames con blur: 211 → 281 detecciones. raw_ok: 163 → 181/185.

**Scripts de diagnóstico creados:**
- `scripts/_debug_blur.py` — analiza circularidad/aspect-ratio de blobs rechazados
- `scripts/_debug_areas.py` — histograma de áreas por rango para encontrar umbral óptimo

---

#### Cambio 18 — Y-clip: recorte al rango Y de detectados (corte de patrón)

**Problema:** Los 4 frames raw NOK restantes tenían `missing=40–50` con errores
concentrados en la parte inferior del frame. "Cuando corta el patrón": cuando el borde
de la zona perforada de la chapa cruza la parte inferior del encuadre, el grid generaba
posiciones esperadas en una zona donde ya no hay agujeros reales → missing masivo.

**Fix en `src/inspection.py`:**
```python
if compare_points and detected_points and pattern.has_grid:
    det_ys = [y for _x, y in detected_points]
    dy_clip = float(pattern.dy) * 1.5   # 33px para dy=22
    y_clip_min = min(det_ys) - dy_clip
    y_clip_max = max(det_ys) + dy_clip
    compare_points = [(x, y) for x, y in compare_points
                      if y_clip_min <= y <= y_clip_max]
```
Las posiciones esperadas se recortan al rango Y de los agujeros detectados ± 1.5×dy.
Si no hay agujeros detectados en la zona inferior, esas filas del grid no se cuentan.

**Seguridad ante defecto (punzón roto):** El punzón roto elimina 1 agujero por fila,
no todas las filas. El rango Y de detectados cubre toda la altura → no se recorta nada.
Si eliminara una fila completa, el margen ±33px incluye la fila adyacente.

**Resultado combinado (Y-clip + tol 22):**
- **185/185 raw OK**, 0 NOK, avg_detection_ratio=100%, align_failures=0/185

---

#### Parámetros config modelo_B al cierre de esta sesión

```yaml
# modelo_B (microperforado / scanner_1)
polarity: bright
min_area: 250.0           # blur reduce area aparente; gap en 200-250px²
circularity_min: 0.75
aspect_ratio_max: 2.0
tol_xy_px: 22.0           # < dx=28; cubre drift de borde y blur residual
align_match_tol_px: 120.0
min_match_count: 5
edge_margin_px: 25.0
pattern_edge_margin_px: 25.0
grid_max_missing: 35
bbox_filter_margin_px: 20.0
quality_ratio_min: 0.0    # 0 = deshabilitado; activar cuando se calibre en planta
consecutive_nok_frames: 40
grid_min_spacing: 15.0
continuous_position_threshold: 4.0

# ROI scanner_1/modelo_B (sin cambios)
{"x": 710, "y": 3, "w": 650, "h": 1077}

# Patrón (reconstruido en sesión 2026-05-20, sincronizado hoy)
# 258 agujeros, dx=28.0, dy=22.0, phase=(4.0, 14.0)
```

---

### Sesión 2026-05-22 — Tadeo + Claude

#### Contexto de la sesión
Análisis de falsos missing en modelo_B/scanner_1. Grabación `20260519_121741` (185 frames).
Síntoma: cruces rojas en agujeros físicamente presentes, concentradas en borde derecho/inferior.
Causa raíz: fase global X/Y no compensa tilt/perspectiva/curvatura local del material.

Segunda parte: implementación del sistema de calidad de frame (blur/degradación) con política
"hold" en la decisión temporal — frames de baja calidad no incrementan ni resetean la racha NOK.

**Commits de esta sesión:**
- `142061f` — Calidad de frame: blur_score + política hold temporal
- `aed010f` — edge_margin_px 25→5 en modelo_B
- (este commit)

---

#### Cambio 19 — Corrección affine local post-fase-global (`grid_fitting.py`)

**Problema:** `grid_compare_points` estimaba UNA fase global X e Y para todo el frame.
El material puede tener tilt/perspectiva que desplaza los agujeros del borde derecho/inferior
~25-40px respecto al esperado → falsos missing en esa zona con tol_xy_px=22.

**Implementación:** Nueva función `_fit_affine_to_grid()` en `src/pipeline/grid_fitting.py`:
1. Usa las posiciones esperadas de la fase global como punto de partida.
2. Matchea detecciones a expected con tolerancia `tol_affine = tol_xy_px × 1.5` (=33px).
3. Ajusta affine 2D por mínimos cuadrados: `det_xy ≈ A @ [ci×dx, cj×dy] + b`.
4. Valida: escala 0.85–1.15, shear <0.15 por eje → rechaza fits imposibles.
5. Si pasa: usa posiciones corregidas. Si falla: fallback a fase global.

**Nuevo parámetro en `grid_compare_points`:** `tol_affine: float = 0.0` (default deshabilitado).
Habilitado vía `grid_affine_refinement: true` en `config/tolerancias.yaml` para modelo_B.

**Archivos modificados:**
- `src/pipeline/grid_fitting.py` → nueva `_fit_affine_to_grid()` + param `tol_affine` en `grid_compare_points`
- `src/inspection.py` → lee `grid_affine_refinement`, pasa `tol_affine` al grid
- `src/utils/config.py` → `DEFAULT_TOLERANCES` agrega `grid_affine_refinement: False`
- `config/tolerancias.yaml` → `grid_affine_refinement: true` en modelo_B

**Resultados en frame_0036 (peor caso):**
- Sin affine: missing=26, extra=24
- Con affine: missing=24, extra=22 (↓2 en ambos)
- Grabación completa: 185/185 raw OK mantenido ✓

**Limitación conocida:** 9 de los 24 missing en frame_0036 tienen un detectado a <22px
pero quedan sin match por "stealing" greedy (tol_xy_px=22 == dy=22 → zona de ambigüedad
vertical). Esto requiere Hungarian matching para resolver definitivamente (ver pendientes).

---

#### Cambio 20 — Overlay near-miss lines (`annotate.py`, `inspection.py`)

**Motivación:** Las cruces rojas y diamantes naranjas no mostraban la relación espacial entre
un expected sin match y el detected más cercano fuera de tolerancia. El operador no podía
evaluar visualmente cuánto falta para que matchee.

**Implementación:**
- `src/inspection.py` → calcula `near_miss_pairs`: lista de (expected, detected) donde
  `tol_xy_px < dist ≤ 2×tol_xy_px`. Se pasa a `draw_compare_overlay`.
- `src/pipeline/annotate.py` → nuevo param `near_miss_pairs` en `draw_compare_overlay`.
  Dibuja líneas cyan-amarillas delgadas entre cada expected sin match y su detectado más
  cercano (si cae en la zona 1×–2×tol). Dibujadas ANTES de los marcadores para que no
  tapen los círculos.

**Leyenda del overlay (ahora explícita en el código):**
- ⚪ círculo verde = agujero detectado correctamente matcheado
- ✕ cruz roja = posición esperada sin match dentro de tol_xy_px
- ◇ diamante naranja = detectado sin posición esperada asignada
- — línea cyan-amarilla = expected↔detected más cercano (fuera de tol, dentro de 2×tol)

---

#### Cambio 21 — Herramienta CSV de diagnóstico por carpeta (`scripts/run_folder_csv.py`)

**Nuevo script:** Exporta métricas por frame a CSV para análisis posterior.

```
python scripts/run_folder_csv.py <carpeta> [--model modelo_B] [--scanner scanner_1] [--output out.csv]
```

**Columnas:** frame, status, expected, detected, missing, extra, detection_ratio,
centering_offset_px, alignment_ok, missing_nearest_max_px, missing_nearest_med_px,
false_missing_count (detectado dentro de 2×tol pero fuera de tol).

---

#### Cambio 22 — Actualización comentario grid_max_missing (`config/tolerancias.yaml`)

**Motivación:** El valor 35 era conservador para la calibración inicial. Con affine refinement
los frames buenos tienen missing≈0-5, no 16-29 (que era con tol=12 sin affine).

**Sin cambio de valor** (sigue en 35 por seguridad). Actualizado el comentario:
- Frames buenos con affine: missing 0-5
- Blur de movimiento: missing 10-20 (estimado, pendiente validar en planta)
- Punzón roto: ~29 missing (pendiente validar)
- **Candidato: bajar a 20-25** después de validar con defecto real

---

#### Cambio 23 — Sistema de calidad de frame: blur_score + política "hold" temporal

**Motivación:** Frames con imagen degradada (blur de movimiento, inestabilidad óptica)
producían falsas alarmas NOK. Estos frames tienen evidencia visual débil y no deberían
tener el mismo peso que frames nítidos en la decisión temporal de FAULT.

**Principio:** Si el frame es LOW_QUALITY → "hold": no incrementar NI resetear la racha NOK.
Si hay demasiados frames LOW_QUALITY consecutivos (≥`low_quality_max_streak`) → resetear
racha para evitar que un sensor degradado bloquee permanentemente la detección de FAULT.

**Implementación:**

**`src/inspection.py`:**
- `InspectionResult` agrega:
  - `blur_score: float = 0.0` — varianza del Laplaciano sobre la ROI (mayor = más nítido)
  - `frame_quality: str = "GOOD"` — `"GOOD"` | `"LOW_QUALITY"`
- `_inspect_bgr()`:
  - Calcula `blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()` sobre el frame post-ROI
  - Clasifica `frame_quality = "LOW_QUALITY"` si `blur_score_min > 0` y `blur_score < blur_score_min`
  - Lee nuevo param `blur_score_min` (por defecto 0.0 = deshabilitado)
  - Pasa `frame_quality` a `_draw_warnings()` → badge "CALIDAD BAJA" en overlay
- `_draw_warnings()` agrega param `frame_quality: str = "GOOD"` → muestra "CALIDAD BAJA"
- `_apply_temporal_rule()`:
  - Nuevo param `low_quality_max_streak: int = 10`
  - Frames LOW_QUALITY: incrementa `lq_streak`, no toca `streak` (hold)
  - Si `lq_streak >= low_quality_max_streak`: resetea ambos streaks
  - `TemporalFrameResult` agrega campo `low_quality_streak: int = 0`
- `FolderInspectionSummary` agrega `low_quality: int = 0`, `max_low_quality_streak: int = 0`
- `inspect_folder()` pasa `low_quality_max_streak` a `_apply_temporal_rule()` y actualiza el summary

**`src/controller/scanner_controller.py`:**
- `__init__()`: carga `low_quality_max_streak` desde tolerancias; inicializa `_lq_streak = 0`
- `start()` y `start_simulate()`: resetean `_lq_streak = 0` junto con `_nok_streak`
- `_handle_result()`: aplica política "hold" en tiempo real:
  - Si `frame_quality == "LOW_QUALITY"`: incrementa `_lq_streak`, no modifica `_nok_streak`
    - Si `_lq_streak >= _low_quality_max_streak`: resetea ambos streaks
    - No actualiza contadores `ok_count` / `nok_count` para estos frames
  - Si `"GOOD"`: resetea `_lq_streak`, aplica lógica normal (NOK+=1 o reset)

**`src/utils/config.py`:**
- `DEFAULT_TOLERANCES` agrega:
  - `"blur_score_min": 0.0` — 0 = deshabilitado; >0 = umbral de varianza del Laplaciano
  - `"low_quality_max_streak": 10` — frames LOW_QUALITY consecutivos antes de resetear racha

**`scripts/_debug_blur_score.py`** (nuevo):
- Diagnóstico de calibración: muestra distribución del `blur_score` por frame en una carpeta.
- Incluye histograma y los 10 frames más borrosos. Ayuda a elegir `blur_score_min`.

**Nota sobre calibración del blur_score:**
- Para la grabación `20260519_121741` con backlight: blur_score en rango 4.1–6.3 en TODOS los frames.
- La imagen binaria (backlight muy contrastado) reduce la varianza del Laplaciano absoluta.
- Para esta grabación `blur_score_min = 0.0` (deshabilitado) es la configuración correcta.
- Calibrar en planta con frames de material en movimiento real (sin backlight temporizado).
- Valores esperados para material con blur real: < 100. Frames nítidos: >> 200.

**Resultado:**
- Grabación 20260519_121741: 185/185 OK, 0 temporal NOK, 0 frames LOW_QUALITY ✓
- Política hold correctamente wired en FSM del scanner y en inspect_folder()
- `blur_score_min = 0.0` en config global y modelo_B → deshabilitado hasta calibración

---

#### Cambio 24 — edge_margin_px 25→5 para modelo_B

**Problema:** `edge_margin_px=25` descartaba agujeros detectados cuyo centroide quedaba
dentro del margen de 25px del borde de la ROI. Para modelo_B (ROI h=1077px), los agujeros
de la última fila visible tienen su centroide cerca de y≈1034 — dentro del margen de 25px
respecto al borde inferior de la ROI (y=1077). Esos agujeros reales quedaban como "missing".

**Efecto:** 492 cruces acumuladas en borde inferior de la grabación (concentradas en y≈1034).
Con edge_margin_px=5 → los centroides válidos a partir de 5px del borde pasan el filtro.

**Decisión:** Cambiar solo para modelo_B. `pattern_edge_margin_px` se mantiene en 25.0
(afecta reconstrucción del patrón, no la detección runtime).

**Archivo modificado:**
- `config/tolerancias.yaml` → `edge_margin_px: 25.0 → 5.0` solo en sección `modelo_B`

**Resultados validados (grabación 20260519_121741, 185 frames):**

| Métrica | Antes (25px) | Después (5px) |
|---------|-------------|---------------|
| raw OK | 185/185 | 185/185 ✓ |
| temporal NOK | 0 | 0 ✓ |
| missing medio | 3.50 | 0.81 |
| missing máximo | 24 | 20 |
| frames sin missing | 58/185 | 160/185 |

Frames críticos verificados:
- frame_0036: missing 24→20
- frame_0064: missing=8
- frame_0065: missing=6
- frame_0093: missing=5
- frame_0177: missing=10

---

#### Cambio 25 — Márgenes laterales del patrón respecto a la chapa

**Motivación:** La métrica `offset_px` (diferencia de centros) no permitía saber cuánto espacio
real queda entre el patrón punzonado y cada borde de la chapa. Se quería conocer las dos
distancias independientes: margen izquierdo y margen derecho, para detectar si el patrón
está corrido hacia un lado aunque el offset total sea pequeño.

**Nuevas métricas en `CenteringResult`:**
- `pattern_left_x` — borde físico izquierdo del patrón detectado: `min(hole.x - hole.r)`
- `pattern_right_x` — borde físico derecho: `max(hole.x + hole.r)`
- `left_margin_px` — espacio entre borde izquierdo de chapa y borde izquierdo del patrón
- `right_margin_px` — espacio entre borde derecho del patrón y borde derecho de la chapa
- `margin_delta_px = left_margin_px - right_margin_px` (>0 = más margen a la izquierda = patrón corrido a la derecha)
- `offset_px = margin_delta_px / 2` (redefinido; antes era `holes_center_x - sheet_center_x`)

**Cambio de firma en `compute_centering()`:**
- Antes: `holes_xs: Sequence[float]` (solo coordenadas X)
- Ahora: `holes: Sequence` (objetos `Hole` con `.x` y `.r`) — permite calcular los extremos físicos del patrón

**Archivos modificados:**

`src/pipeline/edge_centering.py`:
- `CenteringResult` agrega 5 campos nuevos: `pattern_left_x`, `pattern_right_x`, `left_margin_px`, `right_margin_px`, `margin_delta_px`
- `compute_centering()` acepta `holes` (Hole objects) en lugar de `holes_xs`
- `offset_px` ahora es `margin_delta_px / 2` (más significativo físicamente)

`src/inspection.py`:
- Llamada a `compute_centering()` pasa `holes` directamente (antes `[h.x for h in holes]`)

`src/pipeline/annotate.py` — `draw_centering_overlay()`:
- Dibuja dos líneas punteadas amarillo-cyan para los extremos físicos del patrón (`pattern_left_x`, `pattern_right_x`)
- Texto inferior reemplazado: `Izq: XXpx  Der: YYpx` + `Offset: +/-ZZpx`
- Se eliminó el texto "Ancho: XXXpx" (redundante con la visualización)

`src/ui/operator.py` — card "CENTRADO":
- Muestra dos líneas: `I: XXpx  D: YYpx` + `Offset: +/-ZZpx`
- Font reducida a 11px para acomodar el contenido compacto

**Validación (grabación 20260519_121741, 185 frames):**
- 185/185 OK, 0 temporal NOK ✓
- Centering disponible en 185/185 frames
- Izq: mediana=176px (rango 163–188px)
- Der: mediana=158px (rango 137–163px)
- Offset: mediana=+9px (patrón levemente corrido a la derecha, consistente en todos los frames)

---

## Estado actual del sistema

| Componente | Estado |
|---|---|
| Solenoides Y10/Y11 | Bloqueados por software y UI. Re-habilitar cuando se implemente control automático. |
| Startup | ~300–600ms hasta UI visible (antes 2–4s) |
| Backlight Y12/Y13 | Siempre ON al iniciar (inicializa en `initialize_lights()`). |
| Pipeline de visión | Vectorizado, cacheado, CLOSE morfológico, centroide estable, matcher closest-first |
| Visor modo servicio | ZoomableImageView: zoom (rueda), pan (drag), fit (doble click / botón) + scroll |
| Overlay | Imagen completa del frame. Cruz roja=missing, diamante naranja=extra, línea cyan=near-miss |
| Extra detections | Detectadas y visibles (diamantes naranjas) en overlay; filtro bbox activo |
| Centrado de chapa | Márgenes Izq/Der + offset. `left_margin≈176px`, `right_margin≈158px`, `offset≈+9px` en grabación de referencia. `center_offset_tol_px=0` (sin NOK). |
| Detection ratio | Por frame y promedio de sesión. Flag `CALIDAD_DEGRADADA` configurable. |
| Frame quality | `blur_score` (Laplacian var) + `frame_quality` en InspectionResult. `blur_score_min=0.0` (deshabilitado). Política "hold" wired en FSM y inspect_folder(). |
| modelo_B — ROI | `x=710, w=650, y=3, h=1077` → excluye backlight desnudo en ambos lados |
| modelo_B — Grid | dx=28, dy=22, 258 células. Fase X+Y 2D + affine local post-fase. |
| modelo_B — Tolerancia | `tol_xy_px=22`, `min_area=250`, `grid_max_missing=35`, `bbox_filter_margin=20`, `edge_margin_px=5` |
| modelo_B — Affine refinement | `grid_affine_refinement: true`, `tol_affine=33px`, `min_matches=12` |
| modelo_B — Grabación 185f | **185/185 raw OK**, avg_ratio=104%, 0 NOK, 0 temporal NOK. missing medio=0.81, 160/185 frames sin missing. |
| FAULT automático | `consecutive_nok_frames: 40` en modelo_B. Global: 9999 (calibración). |
| Control automático pistones | Planificado, NO implementado. |
| Tests | Solo `tests/test_io_map.py`. Sin cobertura del pipeline de visión aún. |

---

## Pendientes / próximos pasos conocidos

### Alta prioridad (próxima sesión)
- **Validar en planta con material real:**
  - Frame estático sin defecto: verificar missing≤5 con affine activo
  - Frame con punzón roto: missing > grid_max_missing de forma sostenida → temporal NOK
  - Verificar que `consecutive_nok_frames=40` y `grid_max_missing=35` son los valores
    correctos para la velocidad real de la máquina
- **Calibrar `grid_max_missing`:**
  - Candidato: 20-25 (frames buenos con affine → 0-5 missing, margen amplio)
  - Validar que blur de movimiento no supere ese umbral en producción
  - Punzón roto agrega ~29 missing → debe estar sobre el umbral elegido
- **Hungarian matching (reemplazo del greedy):**
  - 9/24 missing en frame_0036 son "stolen": tol_xy_px=22=dy → dos expected compiten
    por el mismo detected cuando el agujero está entre dos filas adyacentes.
  - Greedy closest-first no puede resolver esto. Hungarian matching sí.
  - Impacto estimado: ↓9 missing en frame_0036 (24→15).
- **Calibrar `blur_score_min`:**
  - Capturar frames con blur real de movimiento y frames nítidos en producción
  - Correr `scripts/_debug_blur_score.py <carpeta>` para ver la distribución
  - Elegir umbral en p10-p25 de los frames borrosos (valor inicial estimado: ~50-100)
  - Para backlight siempre encendido: medir con material en movimiento a velocidad real
- **Activar `quality_ratio_min`:**
  - Calibrar en planta: medir el ratio promedio en operación normal vs blur de movimiento

### Media prioridad
- Activar `center_offset_tol_px` con valor real (medir cuántos px de offset se toleran)
- Implementar control automático de solenoides
- Agregar display de `avg_detection_ratio` en tab Métricas de la UI de servicio
- Medir px/mm para modelo_B (saber cuánto es `tol_xy_px=22px` en mm reales)

### Baja prioridad
- Tests unitarios para pipeline de visión (compare, detect, preprocess, grid_fitting)
- Modelo_A: revisar si tiene células duplicadas en grid (113 únicas de 117)
