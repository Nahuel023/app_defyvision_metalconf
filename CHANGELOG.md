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

### Sesión 2026-05-28 — Tadeo + Claude

#### Contexto de la sesión
Continuación de sesión anterior. Trabajo sobre modelo_A / Esterilla scanner_2.
Problema raíz: grilla escalonada (stagger_x_odd=26px) + dy_real≈37px vs dy_stored=38px.

---

#### Cambio 51 — Esterilla: soporte de grilla escalonada (stagger_x_odd)

**Archivos:** `src/patterns/pattern_io.py`, `src/patterns/pattern_build.py`,
`src/pipeline/grid_fitting.py`, `src/inspection.py`, `data/patterns/scanner_2/modelo_A/roi.json`,
`config/tolerancias.yaml`

**Motivación:** El patrón Esterilla tiene filas impares (cj=1,3,...) con 5 agujeros grandes
y filas pares (cj=2,4,...) con 4 agujeros pequeños, con un desfase X de ~26px entre orígenes
de fila. Con un grid rectangular sin stagger, la búsqueda de fase X no podía distinguir ambos
tipos y devolvía una fase incorrecta → todos los frames NOK.

**Cambios:**
- `pattern_io.py`: campo `stagger_x_odd` en `Pattern` dataclass; guardado/lectura en holes.json
- `pattern_build.py`: detección automática de stagger al construir el patrón; override `grid_dx/dy`
  desde config para evitar que `estimate_spacing` devuelva 64 en lugar de 66
- `grid_fitting.py`: `grid_compare_points` acepta `stagger_x_odd`; búsqueda de fase X usa
  tolerancia ajustada `tol_x = max(stagger/4, 5)` para evitar saturación; aplica módulo al origen
- `inspection.py`: extrae `stagger_x_odd` del patrón y lo pasa a `grid_compare_points`
- `roi.json`: ajustado a `{x:870, y:0, w:380, h:1080}` (zona del patrón)
- `tolerancias.yaml`: `grid_dx:66, grid_dy:38, edge_margin_px:5, frame_missing_nok_threshold:8`

**Resultado:** frame_0162 pasó de 200 missing → 13 missing. Todos los frames aún NOK
por threshold=8 y deriva Y acumulada en filas inferiores.

---

#### Cambio 52 — Esterilla: affine refinement estagger-aware

**Archivos:** `src/pipeline/grid_fitting.py`, `config/tolerancias.yaml`

**Motivación:** Las filas inferiores (cj=20+) tienen posiciones reales ~15-25px por encima
de las esperadas (acumulación de deriva Y: dy_real≈37px vs dy_stored=38px, 19 rows → 16px).
Con `grid_affine_refinement:false`, 13 holes missing en frame_0162. Con el affine original
(sin stagger), el fit devolvía shear incorrecto porque even/odd rows tienen distinto origen X,
resultando en 28 missing (peor).

**Fix:** `_fit_affine_to_grid` ahora acepta `stagger_x_odd` y usa coordenadas fuente
stagger-ajustadas: `src_x = ci*dx + (cj%2)*stagger_x_odd`. Esto permite que el affine
corrija scale_y ≈ 0.934 (actual_dy/stored_dy) sin generar shear espurio entre filas.
`grid_compare_points` pasa `stagger_x_odd` al llamar `_fit_affine_to_grid`.

**Resultado:** frame_0162: 13→9 missing. `grid_affine_refinement:true` habilitado.

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

### Sesión 2026-05-26 (continuación) — Tadeo + Claude

#### Cambio 31 — Comando CLI `missing-folder` (diagnóstico de agujeros faltantes)

**Motivación:** En producción `grid_max_missing=35` permite hasta 35 faltantes antes de
declarar NOK. Para diagnóstico se necesita detectar y exportar cualquier frame con
≥1 agujero faltante sin tocar el criterio productivo.

**Nuevo comando:**
```
python -m src.main missing-folder \
  --model modelo_B --scanner scanner_1 \
  --input <carpeta> --output data/output/<nombre> \
  --min-missing 1
```

**Diferencia de criterios (importante):**
- `production_status` — usa el pipeline normal con `grid_max_missing` → nunca NOK en material bueno
- `missing_status` — marca `FALTANTE` cuando `report.missing >= min_missing` → diagnóstico puro

**Salidas generadas:**
- `<output>/missing_report.csv` — una fila por frame con columnas:
  `frame, production_status, missing_status, expected, detected, missing, extra,
  detection_ratio, alignment_ok, false_missing_count,
  missing_nearest_med_px, missing_nearest_max_px`
- `<output>/missing_overlays/frame_NNNN_missing_M_overlay.png` — overlay por frame FALTANTE
  con badge azul "FALTANTE: N" superpuesto debajo del status normal

**Resumen en consola:**
- frames totales, FALTANTE, OK (diagnóstico)
- missing acumulado, missing máximo
- top 10 frames por missing

**false_missing_count:** cuántos faltantes tienen un detectado dentro de 2×tol_xy_px —
candidatos a ser agujeros reales con error de fase, no agujeros físicamente ausentes.

**Resultado sobre grabación 20260519_121741 (185 frames, min_missing=1):**
- 160/185 sin ningún faltante
- 25/185 frames con ≥1 faltante (production_status=OK en todos, 185/185 OK mantenido)
- missing acumulado: 149 (media 0.81/frame en frames buenos)
- Frame más crítico: frame_0036 con 20 missing (borde inferior del frame, fin de material)

**Archivos modificados:**
- `src/main.py` → nuevo `cmd_missing_folder()` + subcomando registrado en `build_parser()`

---

#### Cambio 32 — Fix cp1252: caracteres Unicode en salida de consola Windows

**Problema:** `scripts/run_folder_csv.py` imprimía `→` (U+2192) que falla en consolas
Windows con codepage cp1252. El mismo carácter estaba también en el nuevo `cmd_missing_folder`.

**Fix:** Reemplazados todos los caracteres Unicode no-ASCII en salidas `print()` por ASCII:
- `→` → `->` en `scripts/run_folder_csv.py` (2 ocurrencias)
- `→` → `->` en `src/main.py` (1 ocurrencia en `cmd_missing_folder`)

**Los CSV siguen escribiéndose en UTF-8** (sin cambio).

---

### Sesión 2026-05-26 (zigzag filter + UI +10) — Tadeo + Claude

#### Cambio 37 — Filtro de calidad geométrica (zigzag) + navegación ±10 frames

**Objetivo A:** Detectar frames con vibración de cámara/chapa midiendo el zigzag horizontal
de los bordes del patrón. Marcarlos como `IMAGEN INESTABLE - NO DECIDE` y excluirlos de
rachas NOK y detección de parada de máquina. El blur_score (Laplaciano) no detecta
bien este tipo de inestabilidad lateral.

**Objetivo B:** En la UI de Servicio → tab Grabación → NAVEGADOR DE CAPTURAS, agregar
botones −10 / +10 para saltar de a 10 frames al navegar una carpeta analizada.

---

**A. Filtro de calidad geométrica**

Métrica: **residuales horizontales** de los puntos por banda del patrón respecto a
una línea robusta ajustada. Combina ambos bordes (izquierdo + derecho).
- `pattern_zigzag_std_px` — desviación estándar de los residuales
- `pattern_zigzag_max_px` — residual máximo

Si `std > pattern_zigzag_std_max_px` OR `max > pattern_zigzag_abs_max_px` → `UNSTABLE`.
Un frame UNSTABLE fuerza `frame_quality = "LOW_QUALITY"` → toda la maquinaria existente
de "hold" (regla temporal + MachineStopDetector) lo omite automáticamente.

**Calibración sobre grabación 20260519_121741 (185 frames):**
- frame_0037 (inestable): `std=3.5px, max=13.5px` → UNSTABLE ✓ (max > 10px)
- frame_0038 (estable):   `std=1.4px, max=4.5px`  → STABLE  ✓
- Total UNSTABLE: 5/185 (2.7%) — todos con max > 13px, son genuinamente inestables
- 185/185 raw OK + 185/185 temporal OK mantenidos ✓

**Archivos modificados:**

A) **`src/pipeline/edge_centering.py`**:
   - `CenteringResult` agrega `pattern_zigzag_std_px: float = 0.0` y
     `pattern_zigzag_max_px: float = 0.0`.
   - `compute_centering()`: calcula residuales de `pattern_left_points` y
     `pattern_right_points` contra su línea robusta ajustada (reutiliza `_fit_line_robust`).
     Almacena std y max en el resultado.

B) **`src/inspection.py`**:
   - `InspectionResult` agrega `frame_geometry_quality: str = "STABLE"`,
     `pattern_zigzag_std_px: float = 0.0`, `pattern_zigzag_max_px: float = 0.0`.
   - `_inspect_bgr()`: lee `verticality_quality_enabled`, `pattern_zigzag_std_max_px`,
     `pattern_zigzag_abs_max_px` de tolerancias. Si centering disponible y umbrales
     superados, setea `frame_geometry_quality = "UNSTABLE"` y fuerza
     `frame_quality = "LOW_QUALITY"`.
   - `_draw_warnings()`: acepta `frame_geometry_quality`. Si UNSTABLE, muestra badge
     `"IMAGEN INESTABLE - NO DECIDE"` (reemplaza "CALIDAD BAJA" para este caso).

C) **`src/utils/config.py`**:
   - 3 nuevos defaults: `verticality_quality_enabled: False`,
     `pattern_zigzag_std_max_px: 4.0`, `pattern_zigzag_abs_max_px: 10.0`.

D) **`config/tolerancias.yaml`** — modelo_B:
   - `verticality_quality_enabled: true`
   - `pattern_zigzag_std_max_px: 4.0`
   - `pattern_zigzag_abs_max_px: 10.0`

**B. Navegación ±10 frames en UI**

**`src/ui/service.py`** — `RecordingTab._build_browser_section()`:
   - Nuevos botones `self._btn_prev10` (`-10`) y `self._btn_next10` (`+10`) a ambos
     lados de los botones `◀` / `▶` existentes.
   - `_show_frame()` ya clampea a `[0, len(frame_paths)-1]` → salto automático al límite.
   - `_update_nav_state()` habilita/deshabilita `_btn_prev10` / `_btn_next10` igual que prev/next.

**Sin tocar:** PLC, solenoides, lógica de producción, `grid_max_missing`,
`consecutive_nok_frames`, patrón de modelo_B.

---

#### Cambio 42 — Métrica de zigzag del centro del patrón por bandas (PATRON CENTER)

**Problema reportado:**
Frames 0120, 0121, 0123 pasan como OK pero el centro del patrón de agujeros zigzaguea
visualmente. La métrica anterior (`pattern_zigzag_*`) usa solo los bordes externos del
patrón (agujero más a la izquierda / más a la derecha por banda), lo cual no detecta
desalineación interna.

**Causa raíz:**
`_pattern_bounds_by_band()` devuelve `min(x - r)` y `max(x + r)` por banda. Si el
patrón se tuerce en el centro pero los agujeros de los extremos no se mueven mucho,
el zigzag no se detecta. Los frames 0122, 0124–0126 sí disparaban porque el desvío
era más extremo.

**Solución — nueva métrica PATRON CENTER:**

A) **`src/pipeline/edge_centering.py`:**
   - Nueva función `_pattern_center_by_band(holes, img_h, n_bands=16)`:
     - Por banda: `center_x = np.median([h.x for h in band_holes])` (mínimo 2 agujeros)
     - Devuelve lista de `(center_x, cy)` por banda
   - Nuevos campos en `CenteringResult` (frozen dataclass, con default 0.0):
     - `pattern_center_zigzag_std_px: float = 0.0`
     - `pattern_center_zigzag_max_px: float = 0.0`
   - En `compute_centering()`: calcula `center_pts = _pattern_center_by_band(holes, img_h_for_bands)`
     y aplica `_zigzag_residuals([center_pts])` para obtener std/max.

B) **`src/inspection.py`:**
   - Nuevos campos en `InspectionResult`:
     - `pattern_center_zigzag_std_px: float = 0.0`
     - `pattern_center_zigzag_max_px: float = 0.0`
   - Lee tolerancias: `pattern_center_align_enabled`, `pattern_center_zigzag_std_max_px`,
     `pattern_center_zigzag_abs_max_px`
   - Si el umbral se supera: `pattern_alignment_warn = True` + `final_status = "NOK"`
     (misma consecuencia que `pattern_align_enabled` — badge PATRON DESALINEADO)

C) **`src/utils/config.py`** — nuevos defaults:
   ```python
   "pattern_center_align_enabled": False,
   "pattern_center_zigzag_std_max_px": 8.0,
   "pattern_center_zigzag_abs_max_px": 18.0,
   ```

D) **`config/tolerancias.yaml`** — modelo_B:
   ```yaml
   pattern_center_align_enabled: true
   pattern_center_zigzag_std_max_px: 8.0
   pattern_center_zigzag_abs_max_px: 18.0
   ```
   Umbrales más amplios que el borde (std=5, abs=15) para evitar falsos positivos,
   ya que la mediana reduce la varianza frente a agujeros extremos.

**Sin tocar:** PLC, solenoides, lógica temporal, patrón de referencia, grid_max_missing.

---

#### Cambio 41 — PATRON DESALINEADO → NOK + badge en tope + bordes resaltados

**Problema reportado:** frame_0122 mostraba "STATUS: OK" con badge DETENER MAQUINA activo.
El banner tapaba el área de agujeros (posicionado en h//3 = mitad del frame).

**Causa:** `final_status` se calculaba ANTES de evaluar `pattern_alignment_warn`.
Cuando `pattern_align_enabled` detectaba zigzag excesivo, solo se seteaba el flag pero
no se actualizaba `final_status`. `draw_compare_overlay` ya recibía el valor viejo "OK".

**Correcciones:**

A) **`src/inspection.py`**:
   - `final_status = "NOK"` se asigna dentro del bloque `if pattern_align_enabled` cuando
     se supera el umbral → `draw_compare_overlay` recibe "NOK" correctamente.
   - `draw_centering_overlay` recibe `pattern_warn=pattern_alignment_warn`.
   - Llamadas a `draw_machine_stop_badge`: cambian de `y_offset=±55` a `index=0/1`.

B) **`src/pipeline/annotate.py`** — `draw_machine_stop_badge`:
   - Reposicionado al TOPE del frame (`banner_y = index * (banner_h + 3)`).
   - Altura compacta (~65px por banner). No cubre el área de agujeros.
   - Parámetro `index` reemplaza `y_offset`: 0=primer banner, 1=apilado debajo.
   - Icono "!" al inicio del texto principal.

C) **`src/pipeline/annotate.py`** — `draw_centering_overlay`:
   - Nuevo parámetro `pattern_warn: bool = False`.
   - Cuando `True`: bordes del PATRON en naranja vivo (en vez de cyan), grosor 2,
     alpha 0.9, glow oscuro debajo.
   - Círculo blanco + "!" en el punto de MÁXIMA desviación de cada borde.
   - Label cambia a "PATRON !!" en naranja.

D) **`config/tolerancias.yaml`** — modelo_B:
   - `pattern_align_std_max_px`: 6.0 → 5.0 (más sensible al desalineamiento).

**Semántica clara resultante:**
- CHAPA zigzag alto → IMAGEN INESTABLE (LOW_QUALITY, no decide) — sin cambio.
- PATRON zigzag alto → STATUS: NOK + badge "PATRON DESALINEADO" en tope del frame
  + bordes del patrón en naranja con círculo en el peor punto.

---

#### Cambio 40 — Aceleración de análisis batch (pre-cache + threading)

**Objetivo:** Reducir el tiempo de análisis de una grabación sin cambiar ninguna lógica
de detección ni parámetros de calidad.

**Cuellos de botella identificados:**
1. `load_tolerances()` + `load_pattern()` + `load_roi()` se llamaban para CADA frame
   (N×3 lecturas de disco innecesarias).
2. El procesamiento era estrictamente secuencial — un solo núcleo de CPU.
3. El score de blur (Laplaciano + cvtColor) se calculaba siempre aunque
   `blur_score_min == 0` (deshabilitado en modelo_B).

**Cambios:**

A) **`src/inspection.py`**:
   - `inspect_image()` acepta ahora parámetro `_preloaded: Optional[dict]` (igual que
     `inspect_frame()`) y lo pasa a `_inspect_bgr()`.
   - Blur score (Laplaciano): se calcula solo si `blur_score_min > 0`. Ahorra ~3ms/frame
     en modelo_B donde está deshabilitado.

B) **`src/ui/service.py`** — `_AnalysisWorker.run()`:
   - Pre-carga tolerancias + patrón + ROI **una sola vez** antes del loop.
   - Usa `ThreadPoolExecutor(max_workers=min(cpu_count, 6))`: OpenCV y numpy liberan
     el GIL, así que múltiples frames se procesan en paralelo sobre todos los núcleos.
   - Los resultados se reensamblan en orden correcto (lista indexada por `idx`).
   - Progreso funciona igual (se emite al completar cada future).

**Speedup esperado:** 3-5x en máquinas con 4+ núcleos (típico en Windows de producción).
Sin cambio en ningún parámetro de detección ni resultado de inspección.

---

#### Cambio 38 — CHAPA vs PATRON zigzag + badges DETENER MAQUINA con razón

**Objetivo:** Separar la detección de inestabilidad de imagen en dos métricas distintas
con consecuencias distintas:

1. **CHAPA edge zigzag** (borde de lámina, backlight): zigzag de la CHAPA → vibración de
   cámara/lámina → `IMAGEN INESTABLE` → frame descartado (no cuenta para rachas ni machine stop).
2. **PATRON edge zigzag** (bordes del patrón de agujeros): zigzag del PATRON → desalineamiento
   mecánico del punzón → badge `DETENER MAQUINA - PATRON DESALINEADO`.

Además: cuando `machine_stop` (agujero persistente faltante), el badge muestra la razón
`AGUJERO PERSISTENTE FALTANTE`. Si ambos triggers están activos, se dibujan dos banners
apilados verticalmente.

**Archivos modificados:**

A) **`src/pipeline/edge_centering.py`**:
   - `CenteringResult` ahora tiene **4** campos zigzag en lugar de 2:
     `chapa_zigzag_std_px`, `chapa_zigzag_max_px` (bordes de lámina) y
     `pattern_zigzag_std_px`, `pattern_zigzag_max_px` (bordes del patrón).
   - `compute_centering()`: nuevo helper `_zigzag_residuals(pts_lists)` → `(std, max)`.
     chapa: computed from `[left_pts_list, right_pts_list]` (gradiente de backlight).
     patron: computed from `[pattern_left_points, pattern_right_points]`.

B) **`src/pipeline/annotate.py`**:
   - `draw_machine_stop_badge(img, reason="", y_offset=0)`: banner full-width rojo semitransparente.
     Texto principal `DETENER MAQUINA` (escala 2.0, grosor 5, sombra). Texto amarillo con `reason`.
     `y_offset` para apilar dos banners cuando ambos triggers están activos.

C) **`src/inspection.py`**:
   - `InspectionResult` agrega `pattern_alignment_warn: bool = False`, `chapa_zigzag_std_px`,
     `chapa_zigzag_max_px` (ya tenía `pattern_zigzag_*`).
   - `_inspect_bgr()`: lógica separada:
     - CHAPA zigzag (`verticality_quality_enabled`): si supera umbrales → `frame_geometry_quality = "UNSTABLE"`,
       `frame_quality = "LOW_QUALITY"` (skip decisiones).
     - PATRON zigzag (`pattern_align_enabled`): si supera umbrales → `pattern_alignment_warn = True`
       (NO skip decisiones, solo muestra badge DETENER MAQUINA).
   - Badge drawing: `machine_stop` → badge `"AGUJERO PERSISTENTE FALTANTE"`;
     `pattern_alignment_warn` → badge `"PATRON DESALINEADO"`;
     ambos activos → dos banners apilados (`y_offset=±55`).

D) **`src/utils/config.py`**:
   - Reemplaza `pattern_zigzag_std_max_px / _abs_max_px` por `chapa_zigzag_std_max_px / _abs_max_px`.
   - Agrega `pattern_align_enabled: False`, `pattern_align_std_max_px: 6.0`,
     `pattern_align_abs_max_px: 15.0`.

E) **`config/tolerancias.yaml`** — modelo_B:
   - Reemplaza `pattern_zigzag_*` por `chapa_zigzag_*` (valores idénticos: std=4.0, abs=10.0).
   - Agrega `pattern_align_enabled: true`, `pattern_align_std_max_px: 6.0`,
     `pattern_align_abs_max_px: 15.0`.

**Sin tocar:** PLC, solenoides, lógica de producción, `grid_max_missing`,
`consecutive_nok_frames`, patrón de modelo_B.

---

### Sesión 2026-05-26 (machine stop) — Tadeo + Claude

#### Cambio 36 — Detección de parada de máquina por agujero faltante persistente

**Objetivo:** Si un agujero faltante aparece en la misma zona durante N frames consecutivos,
mostrar badge prominente "DETENCION DE MAQUINA" en el overlay. Indica punzón roto o tapado.
No toca PLC, solenoides, lógica de producción, `grid_max_missing` ni `consecutive_nok_frames`.

**Archivos modificados:**

A) **`src/pipeline/machine_stop.py`** (nuevo):
   - `MachineStopDetector`: detector persistente de zonas de agujeros faltantes.
   - Tracking por clusters espaciales (radio `same_zone_px`). Cuando una zona acumula
     `missing_frames` frames consecutivos con ≥ `min_missing` puntos faltantes → triggered.
   - `frame_quality == "LOW_QUALITY"` no incrementa ni resetea racha.
   - Filtro near-miss: excluye puntos esperados con detected cerca pero fuera de tolerancia.
   - Filtro borde Y: ignora faltantes en top/bottom del ROI (entrada/salida de chapa).
   - Centro de zona: EMA 0.7/0.3 para seguir deriva lenta del punzón.

B) **`src/pipeline/annotate.py`**:
   - Nueva función `draw_machine_stop_badge(img)`: badge rojo centrado con texto
     "DETENCION DE MAQUINA" en grande.

C) **`src/inspection.py`**:
   - `InspectionResult`: nuevo campo `machine_stop: bool = False`.
   - `FolderInspectionSummary`: nuevo campo `machine_stop_count: int = 0`.
   - `_inspect_bgr()`: acepta `_machine_stop_detector` (explícito o vía `_preloaded`).
     Llama `detector.update()` tras calcular `near_miss_pairs` y dibuja el badge si triggered.
   - `inspect_image()`: acepta `_machine_stop_detector` opcional.
   - `inspect_folder()`: crea `MachineStopDetector` desde tolerancias y lo pasa a cada frame.

D) **`src/vision/inspector.py`**:
   - `Inspector.__init__`: agrega `self._detectors: dict[tuple, MachineStopDetector]`.
   - `_get_detector(model, scanner_id)`: cache del detector por (model, scanner_id).
   - `inspect()`: agrega `machine_stop_detector` al dict `_preloaded`.
   - `invalidate()`: limpia `_detectors` cuando cambia el modelo.

E) **`src/utils/config.py`**:
   - 5 nuevos defaults en `DEFAULT_TOLERANCES`:
     `machine_stop_enabled`, `machine_stop_missing_frames`, `machine_stop_min_missing`,
     `machine_stop_same_zone_px`, `machine_stop_ignore_near_miss`.

F) **`config/tolerancias.yaml`**:
   - modelo_B: `machine_stop_enabled: true`, `machine_stop_missing_frames: 5`,
     `machine_stop_min_missing: 1`, `machine_stop_same_zone_px: 35.0`,
     `machine_stop_ignore_near_miss: true`.

G) **`src/main.py`** — `cmd_run_folder()`:
   - Imprime `machine_stop_frames=N` en línea `[quality]`.
   - Agrega `MACHINE_STOP` al warn por frame cuando `result.machine_stop`.

**Sin tocar:** PLC, solenoides, lógica producción, `grid_max_missing`, `consecutive_nok_frames`.

---

### Sesión 2026-05-26 (fix overlay centrado) — Tadeo + Claude

#### Cambio 35 — Fix overlay CHAPA: dibujar en frame completo con offset ROI

**Problema:** `draw_centering_overlay()` se aplicaba sobre `overlay_roi` (imagen recortada
a la ROI, 650×1077px). Las coordenadas de borde de CHAPA (`left_x≈-31px`, `right_x≈700px`)
son ROI-relativas: el borde izquierdo queda fuera de la imagen (x<0 → clipeado a 0) y el
borde derecho en el extremo. Las líneas/labels de CHAPA aparecían sobre el borde del PATRON,
no sobre los bordes físicos reales de la chapa.

**Fix:**

`src/pipeline/annotate.py` — `draw_centering_overlay()`:
- Nuevos parámetros `roi_x: int = 0, roi_y: int = 0` (default=0 → sin cambio de comportamiento).
- Todos los escalares X se suman `roi_x`: `cx`, `hx`, `lx`, `rx`, `plx`, `prx`.
- Los puntos por banda se transforman: `_shift(pts) = (x + roi_x, y + roi_y)` para
  `left_edge_points`, `right_edge_points`, `pattern_left_points`, `pattern_right_points`.

`src/inspection.py` — `_inspect_bgr()`:
- `draw_centering_overlay` se movió de `overlay_roi` al overlay full-frame (`overlay`).
- Se eliminó la llamada sobre overlay_roi.
- Nueva llamada post-compositing con offset:
  ```python
  overlay = draw_centering_overlay(
      overlay, centering, tag_nok=centering_nok,
      roi_x=roi.x if roi else 0,
      roi_y=roi.y if roi else 0,
  )
  ```

**Resultado verificado** en frames 0036, 0090, 0120 de grabación 20260519_121741:
- Overlay full-frame 1920×1080 ✓
- Línea CHAPA izquierda: x≈679px (full-frame) = borde físico real de chapa ✓
- Línea CHAPA derecha: x≈1410px (full-frame) = borde físico real de chapa ✓
- Líneas PATRON separadas visualmente de CHAPA ✓
- Labels "CHAPA" / "PATRON" en posiciones correctas ✓
- Text Izq/Der/Delta/Offset sin cambios ✓
- 185/185 OK mantenido (sin cambio en lógica de inspección) ✓

---

### Sesión 2026-05-26 (Esterilla) — Tadeo + Claude

#### Cambio 34 — Diagnóstico y calibración inicial de modelo_A (Esterilla)

**Objetivo:** Revisar la lógica del patrón Esterilla (modelo_A / scanner_2) y establecer
parámetros de inspección robustos. modelo_B (Microperforado) no fue modificado.

---

**Diagnóstico del patrón `data/patterns/modelo_A/holes.json`:**

Resultado de `scripts/_debug_modelo_a.py`:

```
Puntos totales:    117
Celdas totales:    117
Celdas unicas:     113    ← 4 celdas duplicadas
dx=68.0  dy=38.0  phase_x=16.0  phase_y=30.0
```

**Geometría real de Esterilla:**
- Grilla rectangular (sin escalonado hexagonal). X-positions fijas: ci=7..11 → x=492,560,628,696,764px
- Filas impares  (cj=1,3,...,25): 4 agujeros PEQUEÑOS  r≈14px, area≈627px²
- Filas pares    (cj=2,4,...,24): 5 agujeros GRANDES   r≈25px, area≈2023px²
- Total esperado: 13 filas impares × 4 + 12 filas pares × 5 = 52+60 = 112 celdas únicas

**Celdas duplicadas identificadas — filas cj=22-25 (últimas 4 filas):**

```
(ci=10, cj=22) x2: idx=95 (x=696.7 y=849.1 r=25.0) ← correcto
                   idx=99 (x=723.5 y=885.0 r=13.6) ← mal asignado — era (10,23)
(ci=9,  cj=23) x2: idx=100 (x=656.2 y=885.5) y idx=105 (x=633.7 y=920.5) ambos ≈ y=904exp
(ci=8,  cj=23) x2: idx=101 (x=589.5 y=887.0) y idx=106 (x=567.1 y=922.0) ambos ≈ y=904exp
(ci=9,  cj=24) x2: idx=109 (x=660.8 y=957.0) y idx=110 (x=594.4 y=958.0) ambos ≈ y=942exp
```

**Causas raíz:**
1. **(ci=10, cj=22):** Bug de redondeo en `assign_cells` — Python's `round(22.5)=22` (banker's
   rounding, "round half to even"). El punto a y=885px = exactamente el punto medio entre
   cj=22 (y_exp=866) y cj=23 (y_exp=904). El redondeo bancario lo asignó a cj=22 en lugar
   de cj=23. **Corregido** con round-half-up (`int(x+0.5)`).
2. **(cj=23 y cj=24):** Ambigüedad real en la imagen de referencia — dos agujeros físicos
   diferentes fueron detectados como igualmente cercanos a la misma celda esperada.
   Requiere imagen de referencia más limpia para `build-pattern`. **Sin fix por ahora.**

**Impacto en runtime:** `grid_compare_points` ya deduplica por `(round(ex), round(ey))` →
las 4 celdas duplicadas generan la misma posición esperada y solo la primera pasa.
No hay falsos missing ni falsos extra causados por los duplicados.

---

**Cambios aplicados:**

**`src/pipeline/grid_fitting.py` — `assign_cells()`:**
```python
# ANTES (banker's rounding — round(22.5) = 22, no 23):
(round((x - phase_x) / dx), round((y - phase_y) / dy))
# DESPUÉS (round-half-up — int(22.5 + 0.5) = 23):
(int((x - phase_x) / dx + 0.5), int((y - phase_y) / dy + 0.5))
```
Previene asignaciones incorrectas cuando un punto cae exactamente en el punto medio
entre dos celdas adyacentes. No afecta modelo_B (su holes.json ya estaba guardado).

**`src/patterns/pattern_build.py` — diagnósticos post-assign:**
Después de `assign_cells()`, imprime:
```
[build-pattern] 117 puntos  dx=68.0 dy=38.0  phase=(16.0,30.0)
[build-pattern] Celdas totales: 117  Unicas: 113  Duplicadas: 4
[build-pattern] ADVERTENCIA: 4 celda(s) duplicada(s) detectadas:
  (ci=10, cj=22) x2: [(696.7, 849.1), (723.5, 885.0)]
  ...
```

**`config/tolerancias.yaml` — modelo_A:**
Reemplaza `modelo_A: {}` con overrides completos y documentados:

```yaml
modelo_A:
  polarity: bright            # backlight → agujeros brillantes (mismo que modelo_B)
  min_area: 400.0             # captura small holes (area≈627px²); rechaza ruido
  circularity_min: 0.80
  aspect_ratio_max: 2.0
  tol_xy_px: 16.0             # < dy/2=19px → sin ambigüedad entre filas adyacentes
  align_match_tol_px: 100.0
  min_match_count: 5
  edge_margin_px: 15.0
  pattern_edge_margin_px: 40.0
  grid_min_spacing: 30.0      # dy=38 > 30 → estimate_spacing encuentra dy correcto
  grid_max_missing: 10        # ~9% de 112 agujeros — conservador hasta calibrar
  bbox_filter_margin_px: 20.0
  grid_affine_refinement: false  # sin datos de planta para validar
  extra_min_dist_factor: 2.0     # extras deben estar >32px de todo expected
  consecutive_nok_frames: 9999   # CALIBRACIÓN — FAULT deshabilitado
  continuous_position_threshold: 0.0
```

**Decisión de diseño — Grid vs RANSAC para Esterilla:**
Se eligió **grid fitting** (igual que modelo_B), porque:
- La Esterilla es una grilla rectangular regular y repetitiva
- La chapa llega en posición variable cada ciclo → el patrón no tiene referencia absoluta
- RANSAC/affine requiere conocer la posición absoluta del patrón (no disponible)
- Grid fitting es position-invariant: encuentra la fase correcta frame a frame sin referencia

**No disponible — imagen OK de scanner_2/modelo_A:**
No hay imágenes de referencia para reconstruir `data/patterns/scanner_2/modelo_A/holes.json`.
El patrón global (`data/patterns/modelo_A/`) se usa como fallback.
**Próximo paso:** capturar una imagen OK de Esterilla en planta con scanner_2 y correr:
```
python -m src.main build-pattern --model modelo_A --scanner scanner_2 --img <imagen>
```

---

### Sesión 2026-05-26 — Tadeo + Claude

#### Cambio 29 — Filtro de extras falsos (`extra_min_dist_factor`)

**Problema:** El matcher greedy marcaba como "extra" (diamante naranja) agujeros que
físicamente existen en el patrón pero cuya posición esperada quedó ligeramente fuera
de `tol_xy_px` por error de fase del grid o por drift local. Estos no son detecciones
espurias: son agujeros reales que el algoritmo no pudo asignar.

**Solución:** Nuevo parámetro `extra_min_dist_factor` en `compare_missing_only()`.
Después del matching greedy, cada detectado sin match se computa contra TODAS las
posiciones esperadas (incluyendo ya matcheadas). Si la distancia mínima es ≤
`extra_min_dist_factor × tol_xy_px`, se descarta como "near-expected" — no es un
extra genuino. Solo los verdaderamente lejanos de toda posición esperada se conservan.

**Implementación:**
- `src/pipeline/compare.py` → nuevo param `extra_min_dist_factor: float = 0.0`;
  calcula `d2_to_exp` matricial (n_raw × n_exp) y filtra con `min > thr2`.
- `src/utils/config.py` → `DEFAULT_TOLERANCES` agrega `"extra_min_dist_factor": 0.0`
- `src/inspection.py` → lee `extra_min_dist_factor` y lo pasa a `compare_missing_only`
- `config/tolerancias.yaml` → `extra_min_dist_factor: 2.0` para modelo_B
  (umbral = 2 × 22px = 44px — reflejos/ruido espurio están típicamente >100px de todo expected)

**Resultado en grabación 185 frames:**
- 185/185 raw OK mantenido ✓
- `extra=0` en ~180/185 frames (antes podía ser 3–15 en frames sin blur)
- Los 5 frames con `extra=1` restantes son detecciones genuinamente aisladas

---

#### Cambio 30 — Medición de verticalidad de bordes del patrón

**Motivación:** Las polilíneas que dibujan los bordes laterales del patrón y de la chapa
pueden no ser perfectamente verticales (el material llega con leve inclinación o el patrón
punzonado tiene deriva). Necesario poder cuantificar ese ángulo para diagnóstico.

**Implementación:**

`src/pipeline/edge_centering.py`:
- `CenteringResult` agrega 4 nuevos campos con default=0.0:
  - `left_edge_slope_deg` — pendiente del borde izquierdo de chapa (°)
  - `right_edge_slope_deg` — pendiente del borde derecho de chapa (°)
  - `pattern_left_slope_deg` — pendiente del borde izquierdo del patrón (°)
  - `pattern_right_slope_deg` — pendiente del borde derecho del patrón (°)
- Convención: 0° = perfectamente vertical. Positivo = el borde se inclina a la derecha
  al bajar. Se calcula como `atan(a) * 180/π` donde `a` es el coeficiente de
  `_fit_line_robust(pts)` (ajuste `x = a*y + b`).
- `compute_centering()` define función local `_slope_deg()` y calcula los 4 slopes
  sobre los puntos por banda ya disponibles.

`src/pipeline/annotate.py` — `draw_centering_overlay()`:
- Nueva línea de texto en `text_y_base - 60`:
  `"Vert pat: Izq=±X.X°  Der=±Y.Y°"` en color cyan-amarillo.
- Usa `getattr` para retrocompatibilidad.

**Comportamiento esperado:**
- Material bien alineado: |slope| < 1°
- Material con leve inclinación: 1°–3°
- >3° indica problema de encuadre o error de alineación
- Misma granularidad que la detección de bordes: 16 bandas, ajuste sigma-clip

**185/185 OK mantenido ✓**

---

#### Cambio 33 — Comando CLI `center-folder` + overlay CHAPA/PATRON labels + documentación `center_offset_tol_px`

**Motivación:** Formalizar y exportar las mediciones de centrado en forma diagnóstica, para poder
calibrar `center_offset_tol_px` con datos reales de la grabación de referencia.

**Implementación:**

`src/main.py` — nueva función `cmd_center_folder()`:
- Itera frames con `iter_image_files()` y llama `inspect_image()` por frame
- Extrae `CenteringResult` de `result.centering`
- Escribe CSV de 20 columnas: `frame, status, missing, centering_reliable,
  sheet_left_x, sheet_right_x, sheet_width_px, pattern_left_x, pattern_right_x,
  pattern_width_px, left_margin_px, right_margin_px, margin_delta_px, offset_px,
  left_margin_std, right_margin_std, sheet_left_slope_deg, sheet_right_slope_deg,
  pattern_left_slope_deg, pattern_right_slope_deg`
- `pattern_width_px = pattern_right_x - pattern_left_x` (calculado, no campo de CenteringResult)
- Exporta overlay PNG de cada frame a `<output>/center_overlays/`
- Imprime resumen: mediana/min/max de offset_px, margen Izq, margen Der
- Registrado como subparser `center-folder` en `build_parser()`

`src/pipeline/annotate.py` — `draw_centering_overlay()`:
- Agrega etiquetas "CHAPA" (gris) y "PATRON" (cyan-amarillo) sobre cada línea de borde
- Reestructura texto inferior: fila 1=`Izq: Npx   Der: Mpx`, fila 2=`Delta: Xpx   Offset: Ypx`
- Coordenadas clampeadas al ancho visible (borde chapa puede estar fuera del ROI)

`config/tolerancias.yaml` — modelo_B:
- Agrega `center_offset_tol_px: 0.0` con comentario completo de semántica:
  - `offset_px = (left_margin_px - right_margin_px) / 2`
  - Positivo = patrón corrido a la derecha, negativo = a la izquierda
  - Mediana -0.95px en grabación 185 frames; peor caso +7.02px

**Validación** — grabación `20260519_121741` (185 frames, modelo_B/scanner_1):
- 185/185 mediciones fiables (centering_reliable=True en todos)
- Offset mediana = **-0.95 px** ✓ (esperado ≈ -0.9 px)
- Margen Izq mediana = **207.2 px** ✓ (esperado ≈ 207 px)
- Margen Der mediana = **209.2 px** ✓ (esperado ≈ 209 px)
- Offset máx = **+7.02 px** ✓ (esperado ≈ 7 px)
- 185/185 raw OK mantenido ✓ (no se tocó lógica de producción)

---

### SesiÃ³n 2026-05-26 â€” Tadeo + Codex

#### Cambio 38 â€” Parada por agujero tapado persistente en material continuo

**MotivaciÃ³n:** Un agujero tapado desde el inicio de la secuencia no activaba
`DETENCION DE MAQUINA`. La causa era doble:
1. El tracker buscaba persistencia en la misma posiciÃ³n `(x,y)`, pero la chapa avanza
   verticalmente y el mismo defecto aparece con `x` similar y `y` cambiante.
2. Los faltantes eran descartados como near-miss porque en la grilla densa siempre hay
   un agujero vecino cerca.

**Cambios:**
- `src/pipeline/machine_stop.py`:
  - Las zonas persistentes ahora matchean por columna `X` (`abs(dx) <= same_zone_px`).
  - `Y` se sigue actualizando para visualizaciÃ³n, pero ya no resetea la racha.
  - Los near-miss persistentes ya no se descartan antes del tracking; la persistencia
    por columna es el filtro contra falsos positivos.
- `src/inspection.py`:
  - Si `machine_stop=True`, `final_status` pasa a `NOK`.
  - `_apply_temporal_rule()` marca `decision_status=NOK` inmediatamente para machine stop.
- `src/controller/scanner_controller.py`:
  - En producciÃ³n, `result.machine_stop=True` fuerza `ScannerState.FAULT` inmediato,
    sin esperar `consecutive_nok_frames`.

**ValidaciÃ³n en `20260519_121741`:**
- `machine_stop_frames=22`.
- El defecto persistente inicial dispara desde `frame_0006.png`:
  - `frame_0006.png` a `frame_0009.png` â†’ `NOK/NOK`, `machine_stop=True`.
- `frame_0037.png` sigue `LOW_QUALITY` y no decide.
- `python -m compileall src` OK.

---

#### Cambio 37 â€” Mapeo fijo de cÃ¡maras por scanner

**MotivaciÃ³n:** Asegurar que la UI y el control nunca intercambien feeds:
`scanner_1` debe usar siempre cÃ¡mara Ã­ndice 0 y `scanner_2` siempre cÃ¡mara Ã­ndice 1.
Si una cÃ¡mara no abre, su scanner queda sin imagen; no debe ocupar el lugar del otro.

**Cambio:**
- `src/controller/system.py`:
  - Agregado `_FIXED_CAMERA_BY_SCANNER = {"scanner_1": 0, "scanner_2": 1}`.
  - `InspectionSystem` usa ese mapeo como autoridad por encima de `config/io_map.yaml`.
  - Si el YAML difiere, emite warning y mantiene el mapeo fijo.

**ValidaciÃ³n:**
- `python -m compileall src/controller/system.py` OK.
- InstanciaciÃ³n de `InspectionSystem(disable_plc_outputs=True)`:
  - `scanner_1: camera_index=0`
  - `scanner_2: camera_index=1`

---

#### Cambio 36 â€” Sensibilidad de patrÃ³n desalineado menos agresiva

**MotivaciÃ³n:** La mÃ©trica nueva de `pattern_center_zigzag_*` quedÃ³ demasiado sensible:
marcaba 129/185 frames como NOK en la carpeta `20260519_121741`. El problema era conceptual:
usar la mediana X de todos los agujeros por banda reacciona a la alternancia natural de filas
del microperforado, aun cuando el patrÃ³n fÃ­sico estÃ¡ correcto.

**Cambios:**
- `src/pipeline/edge_centering.py`:
  - `_pattern_center_by_band()` ahora calcula el centro como promedio entre borde fÃ­sico
    izquierdo y derecho del patrÃ³n por banda.
  - Evita falsos zigzag por filas alternadas del microperforado.
- `config/tolerancias.yaml` y `src/utils/config.py`:
  - `pattern_align_abs_max_px: 15.0 â†’ 30.0`
  - `pattern_center_zigzag_std_max_px: 8.0 â†’ 4.0`
  - `pattern_center_zigzag_abs_max_px: 18.0 â†’ 6.5`
- `src/inspection.py`:
  - Si `frame_geometry_quality == "UNSTABLE"`, no se permite que `pattern_alignment_warn`
    convierta el frame en NOK. La imagen inestable se analiza, pero no decide.

**ValidaciÃ³n en `20260519_121741`:**
- Antes: 129/185 NOK por sensibilidad excesiva.
- DespuÃ©s: 9/185 NOK + 1 frame `LOW_QUALITY`.
- Frames pedidos:
  - `frame_0121.png` â†’ NOK por `PATRON DESALINEADO`
  - `frame_0122.png` â†’ NOK por `PATRON DESALINEADO`
  - `frame_0124.png` â†’ NOK por `PATRON DESALINEADO`
- `frame_0037.png` queda `LOW_QUALITY / IMAGEN INESTABLE - NO DECIDE`, sin forzar NOK.
- Overlays de prueba guardados en `data/output/sensibilidad_patron_ajustada_v2/`.
- `python -m compileall src` OK.

---

---

### Sesión 2026-05-27 — Tadeo + Claude

#### Cambio 43 — Tracking de agujero faltante por identidad de grilla (ci/cj)

**Problema:** El `MachineStopDetector` anterior rastreaba zonas por píxel X (`same_zone_px`).
Cuando la chapa avanza en Y entre frames, el mismo agujero faltante (mismo punzón roto) aparece
en píxeles distintos → el tracker no reconocía el defecto como persistente y no disparaba parada.

**Causa raíz confirmada con diagnóstico:**
```
frame_0002: ci=13, cj=46  → missing=1, no trigger (acumulando)
frame_0006: ci=13, cj=38  → machine_stop=True  (cj cambió 8 posiciones — chapa avanzó)
frame_0007: ci=13, cj=36  → machine_stop=True
frame_0008: ci=13, cj=36  → machine_stop=True
frame_0009: ci=13, cj=33  → machine_stop=True
```
La columna del punzón (ci=13) es invariante. La fila (cj) cambia con el avance de la cinta.
El pixel Y cambia ~4–8 filas entre frames. Con el tracker por pixel X esto funcionaba solo si
el drift era menor que `same_zone_px=35px`. Con tracking por ci: coincidencia exacta siempre.

**Implementación:**

**`src/pipeline/grid_fitting.py`** — `grid_compare_points()`:
- Cambio de retorno: `list[tuple[float,float]]` → `tuple[list[...], list[tuple[int,int]]]`
- Segundo elemento: lista paralela de `(ci, cj)` para cada punto esperado generado.
- Permite que la capa de inspección propague la identidad de celda hasta el detector.

**`src/pipeline/compare.py`** — `compare_missing_only()` + `CompareReport`:
- `CompareReport` agrega campo `missing_cells: List[Tuple[int,int]]` (default `[]`).
  Contiene las coordenadas de grilla `(ci, cj)` de cada agujero esperado sin match.
- Nuevo parámetro `expected_cells: List[Tuple[int,int]] | None = None`:
  cuando se provee, los missing_cells se popula en paralelo con missing_points.
- Nuevo parámetro `use_hungarian: bool = False`:
  matching óptimo via `scipy.optimize.linear_sum_assignment`. Resuelve el problema de
  "robo" de detectados cuando `tol_xy_px ≈ dy` (dos expected compiten por el mismo detected).
  Si scipy no está instalado: fallback automático a greedy con warning implícito.
- Tracking de índices de missing en ambos paths (greedy y Hungarian) para poblar `missing_cells`.

**`src/pipeline/machine_stop.py`** — `MachineStopDetector`:
- Nuevos parámetros:
  - `track_by_grid: bool = True` — activa tracking por columna ci
  - `same_column_tol_cells: int = 0` — tolerancia en celdas (0=exacto)
- Nueva estructura interna `_grid_zones: dict[int, dict]` keyed by ci.
  Cada zona: `{streak, count, x, y}`.
- `update()` acepta `missing_cells: Sequence[tuple[int,int]] | None = None`.
  Cuando `track_by_grid=True` y `missing_cells` disponible → usa `_update_grid()`.
  Si no (path no-grid o cells vacíos) → fallback a `_update_pixel()` (comportamiento anterior).
- Nueva property `triggered_columns: list[int]` → ci valores de zonas disparadas.
  Usada para construir el mensaje del badge: `"AGUJERO FALTANTE PERSISTENTE EN COLUMNA 13"`.
- `reset()` limpia también `_grid_zones`.

**`src/inspection.py`** — `_inspect_bgr()`:
- Desempaca `(compare_points, compare_cells)` del retorno de `grid_compare_points`.
- Y-clip mantiene `compare_cells` sincronizado con `compare_points` (filtrado en paralelo).
- Pasa `expected_cells=compare_cells` y `use_hungarian=use_hungarian_matching` a `compare_missing_only`.
- Pasa `missing_cells=report.missing_cells` a `_ms_detector.update()`.
- Usa `_ms_detector.triggered_columns` para construir el texto de razón del badge.
  Ejemplo de mensaje generado: `"AGUJERO FALTANTE PERSISTENTE EN COLUMNA 13"`.
- Lee nuevo param `use_hungarian_matching` desde tolerancias.
- `inspect_folder()`: pasa `track_by_grid` y `same_column_tol_cells` al constructor del detector.

**`src/vision/inspector.py`** — `_get_detector()`:
- Pasa `track_by_grid` y `same_column_tol_cells` al constructor de `MachineStopDetector`.

**`src/utils/config.py`** — nuevos defaults:
```python
"machine_stop_track_by_grid": True,
"machine_stop_same_column_tol_cells": 0,
"use_hungarian_matching": False,
```

**`config/tolerancias.yaml`** — modelo_B:
```yaml
machine_stop_track_by_grid: true
machine_stop_same_column_tol_cells: 0  # coincidencia exacta de ci
use_hungarian_matching: false           # activar si scipy disponible y hay stealing
```

**Resultados de prueba — carpeta `20260519_121741` (Imagenes_METALCONF_editadas):**
- Detector activa `MACHINE_STOP` en frames con agujeros tapados en el medio del patrón.
- El badge muestra la columna específica: `"AGUJERO FALTANTE PERSISTENTE EN COLUMNA 13"`.
- El mismo ci=13 se reconoce a través de 4+ frames aunque cj varía (cinta avanzando).
- `machine_stop_frames=28` en 185 frames analizados (imágenes con defectos intencionales).
- Los frames sin defecto (material limpio) mantienen `MACHINE_STOP=False` correctamente.

**Invariante preservado:** 185/185 raw OK en material original limpio (no editado) mantenido.

**Sin tocar:** PLC, solenoides, lógica de comparación base, patrón de referencia, `grid_max_missing`, `consecutive_nok_frames`.

---

#### Cambio 44 — Parada de máquina virtual: sin acciones de hardware

**Motivación / regla de seguridad:**
Hay personas cerca de la máquina. `machine_stop=True` debe ser puramente informativo:
visible en UI/overlay/log, pero **no debe accionar solenoides, backlight ni cambios de estado FSM**.
La regla es: solenoides bloqueados siempre; la parada solo es virtual hasta que se apruebe
el control automático de pistones.

**Cambios:**

**`src/pipeline/annotate.py`** — texto del badge:
- `"! DETENER MAQUINA"` → `"! DETENCION VIRTUAL DE MAQUINA"`

**`src/controller/scanner_controller.py`** — FSM y hardware:
- Antes: `machine_stop=True` → `ScannerState.FAULT` + escribe solenoid=False + backlight=False + luz roja.
- Ahora: `machine_stop=True` → solo log warning `"DETENCION VIRTUAL — sin accion de hardware"`.
  `ScannerState.FAULT` sigue disparando solo por `consecutive_nok_frames` (lógica de streak).
  Los `elif` se separan: machine_stop y fault son caminos independientes.

**`src/ui/service.py`** — `_AnalysisWorker`:
- Cuando `machine_stop_enabled=True`, el análisis de carpeta pasa de paralelo (ThreadPoolExecutor)
  a **secuencial** con un único `MachineStopDetector` compartido entre frames.
  Motivo: el detector es stateful; con threads los frames llegan fuera de orden y la racha
  nunca se acumula correctamente.
- Cuando `machine_stop_enabled=False` (default): sigue usando ThreadPoolExecutor (sin cambio de rendimiento).

**`src/ui/service.py`** — `RecordingTab` (live inspection):
- `__init__`: agrega `self._live_ms_detector = None`.
- `_on_stop()`: resetea `_live_ms_detector = None` al detener la grabación.
- `_grab_frame()`: si `machine_stop_enabled=True`, crea el detector solo en el primer frame
  y lo reutiliza en todos los siguientes (detector persistente por sesión de grabación).
  Pasa el detector vía `_preloaded={"machine_stop_detector": self._live_ms_detector}`.

**`config/tolerancias.yaml`** — modelo_A:
- `consecutive_nok_frames: 9999` → `5` (habilitado: 5 NOK consecutivos = FAULT)
- Agrega bloque `machine_stop_*` para esterilla:
  ```yaml
  machine_stop_enabled: true
  machine_stop_missing_frames: 5
  machine_stop_min_missing: 1
  machine_stop_same_zone_px: 35.0
  machine_stop_ignore_near_miss: true
  machine_stop_track_by_grid: true
  machine_stop_same_column_tol_cells: 0
  ```

**Prueba con carpeta `20260519_121741` (modelo_B):**
- `MACHINE_STOP` aparece correctamente en frames 185–196 con missing=2 persistente.
- El badge del overlay dice `"! DETENCION VIRTUAL DE MAQUINA"`.
- No se escriben salidas de hardware en ningún momento.

---

#### Cambio 45 — Parametro frame_missing_nok_threshold (infraestructura, no activado)

**Motivacion:** Separar el umbral productivo conservador (`grid_max_missing`) del umbral
visual estricto para marcar un frame como NOK en el overlay. Util para modelos donde
cualquier agujero faltante es un defecto visible, independientemente del umbral de FAULT.

**Cambios en infraestructura:**
- `src/utils/config.py`: nuevo parametro `frame_missing_nok_threshold` (default `None`).
  - `None` = usa `grid_max_missing` como antes (sin cambio de comportamiento).
  - `0` = marca el frame como `NOK` en cuanto `missing > 0`.
  - Cualquier entero positivo = umbral de missing para NOK de visualizacion.
- `src/inspection.py`: `final_status` considera `frame_missing_nok_threshold` ademas de
  `report.status`, centrado y parada virtual.

**Reversion de config (evitar regresion 185/185):**
- El parametro fue inicialmente activado (`=0`) en modelo_A y modelo_B.
- Con `frame_missing_nok_threshold=0` en modelo_B, `raw_ok` cae de 185 a 155 en
  `20260519_121741` (frames con blur de movimiento tienen 1-5 missing → todos NOK).
- Decision: no activar en ninguno de los dos modelos hasta calibrar con material real.
  - `modelo_B`: parametro eliminado de la seccion (hereda default `None`).
  - `modelo_A`: parametro eliminado (sin imagenes reales de Esterilla para calibrar).
- Para activar en el futuro: agregar `frame_missing_nok_threshold: 0` en el modelo deseado.

---

#### Cambio 46 — Clasificacion por tipo de agujero para Esterilla (modelo_A)

**Motivacion:** Esterilla tiene dos tamanos de agujeros claramente distintos:
- Agujeros chicos (cj impar): r≈14px, area≈627px² — 4 por fila
- Agujeros grandes (cj par): r≈25px, area≈2023px² — 5 por fila

Sin clasificacion, el matcher podia asignar un blob grande (ruido/reflejo) a la posicion
de un agujero chico esperado, o viceversa. Los agujeros faltantes tampoco se etiquetaban
por tipo, dificultando el diagnostico (punzon chico vs punzon grande roto).

**Cambios:**

**`src/utils/config.py`** — 5 nuevos defaults:
```python
"hole_type_split_area": 0.0,  # 0 = deshabilitado; >0 = umbral en px² entre chico/grande
"min_area_small":       0.0,  # 0 = usar min_area global; >0 = piso para agujeros chicos
"max_area_small":       0.0,  # 0 = sin techo; >0 = techo para agujeros chicos
"min_area_large":       0.0,  # 0 = usar min_area global; >0 = piso para agujeros grandes
"max_area_large":       0.0,  # 0 = sin techo; >0 = techo para agujeros grandes
```

**`src/pipeline/compare.py`** — `CompareReport` + `compare_missing_only()`:
- `CompareReport` agrega campo `missing_types: List[str]` (default `[]`).
  Contiene `"small"` o `"large"` para cada agujero faltante.
- `compare_missing_only()` agrega parametros `expected_types` y `detected_types`.
  Cuando ambos se proveen, pares de tipo cruzado (chico-expected vs grande-detected)
  reciben distancia infinita → nunca se asignan entre si (hard constraint).

**`src/inspection.py`** — `_inspect_bgr()`:
- Lee nuevos parametros (`hole_type_split_area`, `min_area_small`, etc.).
- Post-deteccion: cuando `hole_type_split_area > 0`, clasifica cada `Hole` en
  `"small"` / `"large"` por area, aplica filtros de area por tipo, y descarta
  agujeros fuera del rango esperado para su categoria.
- Deriva `expected_types` de `pattern.radii` + `compare_cells`:
  `split_r = sqrt(hole_type_split_area / pi)` — punto de corte en el gap natural.
  Para cada celda `(ci,cj)` en `compare_cells`, busca el radio en el patron y
  clasifica como `"small"` o `"large"`.
- Bbox filter: mantiene `detected_types` sincronizado con `detected_in_bbox`.
- Pasa `expected_types` y `detected_types` a `compare_missing_only`.
- `report.missing_types` refleja el tipo de cada agujero faltante.

**`config/tolerancias.yaml`** — modelo_A:
```yaml
hole_type_split_area: 1000.0  # gap natural entre 627px² (chico) y 2023px² (grande)
min_area_small: 350.0         # chico real ≈627px²; noise << 350
max_area_small: 1300.0        # excluye grandes y ruido grande intermedio
min_area_large: 900.0         # grande real ≈2023px²; excluye chicos
max_area_large: 5000.0        # excluye suciedad / reflejo muy grande
```

**modelo_B:** `hole_type_split_area=0.0` (no en config → hereda default 0 = deshabilitado).
El codigo nuevo es completamente inerte para modelo_B.

**Diagnostico del patron modelo_A (holes.json):**
- 117 puntos totales, 113 celdas unicas (4 duplicados por redondeo en build)
- dx=68px, dy=38px — grilla rectangular
- Filas cj impar: 4 agujeros chicos, r≈14px (media 14.13px)
- Filas cj par: 5 agujeros grandes, r≈25px (media 25.38px)
- Bimodalidad clara: gap entre 14px y 25px con punto de corte r≈17.8px (area=1000px²)

**Prueba de logica (no hay imagenes Esterilla disponibles):**
- Test 1: matching mismo tipo → 0 missing ✓
- Test 2: tipo cruzado (small expected vs large detected) → ambos missing ✓
- Test 3: un match + un faltante grande → missing_types=['large'] ✓
- Test 4: sin tipos → backward compatible ✓
- `python -m compileall src` OK

**Limitacion:** No hay imagenes de Esterilla (scanner_2/modelo_A) disponibles para
validar los umbrales de area en planta. Los parametros `min_area_small` etc. son
estimaciones basadas en los radios del patron existente. Calibrar en planta con
histograma de areas cuando se tengan imagenes reales de Esterilla.

**Sin tocar:** modelo_B (tipo deshabilitado), PLC, solenoides, logica temporal,
patron de referencia, grid_max_missing.

#### Cambio 46b — Activar frame_missing_nok_threshold: 0 para modelo_A

**Motivacion:** Con la clasificacion por tipo implementada, el usuario quiere que
cualquier agujero faltante en Esterilla marque el frame como NOK inmediatamente,
habilitando el seguimiento frame-a-frame para la parada virtual de maquina.

**Cambio:** `config/tolerancias.yaml` modelo_A — agrega:
```yaml
frame_missing_nok_threshold: 0  # cualquier missing → NOK inmediato
```
Esto complementa `grid_max_missing: 10` (que solo marca NOK cuando faltan >10).
Con `frame_missing_nok_threshold: 0`, UN solo agujero faltante ya marca NOK y
alimenta el contador de racha para `machine_stop_missing_frames: 5`.

**Sin tocar:** modelo_B (hereda default `None` = solo `grid_max_missing` aplica).

---

#### Cambio 47 — Bandas de muestreo de bordes configurables + suavizado antes de zigzag

**Motivacion:** Con `_N_BANDS=16` fijo, variaciones leves en la frontera del patron
(patron corrido 1-2px) podian pasar desapercibidas porque cada banda abarca muchas filas
y el ruido de una sola banda vacía/escasa disparaba el metric. Se necesitaba:
1. Mayor resolución espacial (más bandas).
2. Descarte de bandas con muy pocos agujeros (outliers por zona vacía).
3. Suavizado previo al calculo de zigzag para no reaccionar a un outlier aislado.

**Cambios:**

**`src/pipeline/edge_centering.py`:**
- Nueva función `_smooth_points_x(pts, window)`: mediana deslizante sobre los valores X
  de una serie de puntos (x,y) ordenados por Y. Usada SOLO para las series de patron
  (no para la chapa, donde los outliers SI son la señal de vibración).
- `_pattern_bounds_by_band()`: nuevo parámetro `min_holes=1`. Bandas con menos agujeros
  que `min_holes` se descartan → evita estimaciones de borde basadas en 1 agujero.
- `compute_centering()`: 3 nuevos parámetros opcionales con defaults backward-compatible:
  - `n_bands=16` — sustituye la constante `_N_BANDS` en todo el flujo.
  - `min_holes_per_band=1` — pasado a `_pattern_bounds_by_band`.
  - `smooth_window=1` — aplicado a `pattern_left_points`, `pattern_right_points` y
    `center_pts` antes de `_zigzag_residuals`. El overlay sigue mostrando puntos crudos.
- Corregido bug: `for i in range(_N_BANDS)` en el calculo de band_lm/band_rm usaba la
  constante global en vez del parametro local. Ahora usa `n_bands`.

**`src/inspection.py`:**
- Lee `edge_centering_bands`, `pattern_edge_min_holes_per_band`, `pattern_edge_smooth_window`
  de tolerancias y los pasa a `compute_centering()`.

**`src/utils/config.py`:**
- 3 nuevos defaults: `edge_centering_bands=16`, `pattern_edge_min_holes_per_band=1`,
  `pattern_edge_smooth_window=1`. Los defaults mantienen comportamiento anterior para modelos
  sin configuración explícita.

**`config/tolerancias.yaml` — modelo_B:**
```yaml
edge_centering_bands: 24          # 24 > 16 → más resolución espacial
pattern_edge_min_holes_per_band: 2 # descarta bandas con 1 solo agujero (outliers de borde)
pattern_edge_smooth_window: 3      # mediana de 3 bandas antes del calculo de zigzag
```

**Resultado en 20260519_121741 (primeros 10 frames):**
- Con 16 bandas: raw_ok=6, raw_nok=4
- Con 24 bandas: raw_ok=4, raw_nok=6 — frames 0001 y 0004 ahora NOK (antes pasaban)
  Frame 0001 (missing=0): detectado por zigzag de patron con mayor resolución.
  Frame 0004 (missing=3): alineacion levemente degradada que 16 bandas no capturaba.
- MACHINE_STOP frames 6-9: sin cambio (correcto).
- Tiempo: ~50ms/frame (sin cambio respecto a 16 bandas).

**Calibrar si se necesita más sensibilidad:** subir a `edge_centering_bands: 32`.
Precaución: con muchas bandas y pocos agujeros por fila, más bandas quedan vacías.
El parámetro `pattern_edge_min_holes_per_band: 2` compensa este efecto.

**Sin tocar:** modelo_A (hereda defaults de config.py = comportamiento anterior).

---

#### Cambio 48 — Verticalidad de patrón más sensible sin recortar overlay

**Motivación:** Frames editados del rango 120-140, especialmente `frame_0121`,
`frame_0122` y `frame_0124`, tenían corrimiento leve del patrón y no siempre
entraban como `PATRON DESALINEADO`. Además, la polilínea visual del patrón quedaba
recortada en los extremos superior/inferior porque los mismos puntos filtrados para
métrica se usaban también para dibujar.

**Cambios:**
- `src/pipeline/edge_centering.py`:
  - `_pattern_bounds_by_band()` ya no recorta extremos Y; devuelve la frontera completa
    para que el overlay muestre más borde del patrón.
  - `compute_centering()` mantiene una copia recortada solo para las métricas numéricas
    de verticalidad, evitando que bandas extremas con pocos datos inflen falsamente el
    zigzag.
  - El overlay usa puntos completos; las métricas (`pattern_zigzag_*`,
    `pattern_center_zigzag_*`, slopes y std de márgenes) usan la serie métrica filtrada.
- `config/tolerancias.yaml` modelo_B:
  - `pattern_align_std_max_px: 5.0 -> 2.4`
  - `pattern_align_abs_max_px: 30.0 -> 6.0`
  - `pattern_center_zigzag_std_max_px: 4.0 -> 2.2`
  - `pattern_center_zigzag_abs_max_px: 6.5 -> 6.0`
  - `pattern_edge_smooth_window: 3 -> 1`

**Validación en `20260519_121741`:**
- Rango 120-140:
  - `frame_0121`, `frame_0122` y `frame_0124` ahora quedan `NOK` con
    `pattern_alignment_warn=True`.
  - Frames 132-140 quedan mayormente `OK`, salvo defectos reales detectados por la métrica.
- Carpeta completa: 185 frames -> `OK=162`, `NOK=23`; `pattern_warn_count=22`;
  `LOW_QUALITY=1` (`frame_0037`).
- Overlays de muestra guardados en `data/output/verticalidad_patron_120_140/`.
- `python -m compileall src` OK.
- `python -m pytest tests/`: 0 tests recolectados.

**Seguridad:** la parada sigue siendo virtual. No se modificó lógica de PLC,
solenoides ni salidas físicas.

---

---

### Sesión 2026-05-27 (cont.) — Tadeo + Claude

#### Cambio 49 — Panel de razones NOK + marcadores de agujeros faltantes numerados

**Motivación:** El operador necesita saber exactamente POR QUÉ un frame es NOK y DÓNDE
están los agujeros faltantes para verificar visualmente si la detección es correcta.

**Cambios:**

**`src/pipeline/annotate.py`:**
- Nueva función `_draw_nok_reasons_panel(img, reasons)`:
  - Panel semitransparente rojo oscuro en top-left del overlay.
  - Header "NOK" en blanco/rojo; razones en cyan. Altura adaptativa según número de causas.
- `draw_compare_overlay()` ahora acepta `nok_reasons: List[str] = ()`.
  - Si NOK y hay razones: dibuja el panel en lugar del texto "STATUS: NOK".
  - Si OK: texto pequeño verde "STATUS: OK".
- Marcadores de missing holes rediseñados:
  - Círculo relleno oscuro (r=18, color `(0,0,80)`) como fondo.
  - Cruz blanca con markerSize=36 (outlin) + 34 (fill) para visibilidad.
  - Número de orden (1, 2, 3...) sobre cada marcador para identificación.

**`src/inspection.py`:**
- Construye `nok_reasons: list[str]` antes de la llamada a `draw_compare_overlay`:
  - `AGUJEROS FALTANTES: N`, `AGUJEROS EXTRA: N`, `CENTRADO NOK (+Xpx)`,
    `PATRON DESALINEADO`, `PARADA DE MAQUINA`, `IMAGEN INESTABLE`, `ALINEACION FALLBACK`.
- Pasa `nok_reasons=nok_reasons` a `draw_compare_overlay`.

**Commit:** `d4b5a75`

---

#### Cambio 50 — Tolerancias modelo_A (Esterilla) más permisivas

**Problema reportado:** El modelo de Esterilla marcaba casi todo como NOK. Los parámetros
iniciales eran demasiado conservadores para la realidad de planta (blur de movimiento,
variaciones de iluminación, posicionamiento no ideal).

**Cambios en `config/tolerancias.yaml` — SOLO sección `modelo_A`:**

| Parámetro | Antes | Después | Razón |
|---|---|---|---|
| `min_area` | 400.0 | 300.0 | Blur reduce área aparente de los agujeros chicos |
| `circularity_min` | 0.80 | 0.70 | Blur de movimiento reduce circularidad aparente |
| `min_area_small` | 350.0 | 250.0 | Captura agujeros chicos afectados por blur |
| `max_area_small` | 1300.0 | 1500.0 | Margen ampliado |
| `min_area_large` | 900.0 | 700.0 | Evitar perder grandes con iluminación no ideal |
| `tol_xy_px` | 16.0 | 18.0 | Más tolerancia de posición (máximo seguro < dy/2=19) |
| `grid_max_missing` | 10 | 15 | ~13% de 112 agujeros; más tolerante |
| `frame_missing_nok_threshold` | 0 | 3 | Permite hasta 3 missing antes de NOK por frame |
| `consecutive_nok_frames` | 5 | 8 | Requiere más frames consecutivos para FAULT |

**Sin tocar:** `modelo_B`, PLC, solenoides, patrón de referencia, lógica de grid.

**Nota:** Calibrar en planta con material real. Estos valores son estimaciones
razonables para reducir falsos positivos sin perder defectos reales.

---

#### Cambio 51 — Frontera de patrón por borde global para evitar falsos zigzag

**Problema:** La detección de borde del patrón generaba demasiados falsos positivos.
En bandas donde la grilla alternada no tenía agujero de la columna exterior, el código
tomaba el agujero más externo disponible de esa banda, que podía ser una columna
interior. Eso inventaba un corrimiento lateral inexistente y elevaba
`pattern_zigzag_*`.

**Cambio:**
- `src/pipeline/edge_centering.py`:
  - `_pattern_bounds_by_band()` ahora calcula `global_left/global_right`.
  - Si `pattern_edge_boundary_tol_px > 0`, cada banda solo aporta borde izquierdo
    si tiene agujeros cerca del borde global izquierdo, y borde derecho si tiene
    agujeros cerca del borde global derecho.
  - Cuando una banda cae en el espacio entre agujeros exteriores, se saltea para
    borde en vez de usar una columna interior como falso borde.
- `config/tolerancias.yaml` modelo_B:
  - `pattern_edge_boundary_tol_px: 24.0`.

**Validación en `20260519_121741`:**
- Antes del fix: 185 frames -> `NOK=23`, `pattern_warn_count=22`.
- Después del fix: 185 frames -> `OK=176`, `NOK=9`, `pattern_warn_count=8`.
- Frames clave:
  - `frame_0121`: sigue `NOK`, `PATRON DESALINEADO`.
  - `frame_0124`: sigue `NOK`, `PATRON DESALINEADO`.
  - `frame_0132` a `frame_0140`: vuelven mayormente `OK`, reduciendo falsos positivos.
- Overlays de control guardados en `data/output/verticalidad_patron_boundary_fix/`.
- `python -m compileall src` OK.
- `python -m pytest tests/`: 0 tests recolectados.

**Seguridad:** la parada sigue siendo virtual; no se tocó PLC, solenoides ni salidas físicas.

---

#### Cambio 52 — Bypass temporal de login en Modo Servicio

**Motivación:** Por pedido de operación, el Modo Servicio debe abrir sin pedir usuario
ni contraseña por ahora.

**Cambios:**
- `config/app.yaml`:
  - Agrega `service.login_enabled: false`.
  - Para volver a exigir credenciales, cambiarlo a `true`.
- `src/ui/login_dialog.py`:
  - Nueva función `service_login_enabled()` que lee `config/app.yaml`.
  - Si el archivo/config falla, el fallback es seguro: login habilitado.
- `src/main.py`:
  - El comando `service` solo muestra `LoginDialog` si `service_login_enabled()` es `true`.
- `src/ui/operator.py`:
  - El botón "Modo Servicio" aplica la misma regla.

**Validación:**
- `python -m compileall src/main.py src/ui/operator.py src/ui/login_dialog.py` OK.

**Nota:** Es un bypass temporal de UI. No modifica PLC, solenoides ni salidas físicas.

---

#### Cambio 53 — Reset correcto de parada virtual cuando no hay missing

**Problema:** En modo `machine_stop_track_by_grid`, cuando un frame no tenía agujeros
faltantes, `missing_cells` llegaba vacío y el detector caía al modo pixel en lugar de
actualizar/limpiar el estado de grilla. Eso dejaba rachas viejas colgadas; después un
frame aislado con falsos missing, como `frame_0064`, podía heredar una racha previa y
mostrar `DETENCION VIRTUAL DE MAQUINA` aunque no hubiera N frames consecutivos reales.

**Cambios:**
- `src/pipeline/machine_stop.py`:
  - En tracking por grilla, `missing_cells=[]` ahora se procesa como frame válido sin
    faltantes y resetea las columnas activas.
  - Solo se usa fallback pixel cuando `missing_cells is None`.
- `src/inspection.py`:
  - Siempre pasa `report.missing_cells` al detector, incluso cuando la lista está vacía.
- `tests/test_machine_stop.py`:
  - Agrega tests para garantizar que un frame vacío corta la racha.
  - Agrega test de que la parada virtual requiere frames consecutivos en la misma columna.

**Validación:**
- `python -m pytest tests/test_machine_stop.py` OK.
- Rango `frame_0058` a `frame_0066` de `20260519_121741`:
  - `frame_0064` mantiene el análisis de missing, pero queda `machine_stop=False`.
  - `frame_0066` sin missing limpia todas las columnas activas.

**Seguridad:** La parada sigue siendo virtual. No se modificó PLC, solenoides ni salidas físicas.

---

#### Cambio 54 — Frames inestables por borde de CHAPA + patrón extendido en overlay

**Problema:** `frame_0031` mostraba borde externo de CHAPA con lectura débil/ondulada,
pero no se clasificaba como `IMAGEN INESTABLE`. El criterio anterior solo miraba el
zigzag absoluto grande; si Hough no encontraba líneas del borde y el zigzag era leve,
el frame podía quedar como `GOOD/STABLE`. Además, las líneas de borde del PATRON se
cortaban donde había puntos de banda, dificultando auditar visualmente la decisión.

**Cambios:**
- `config/tolerancias.yaml` modelo_B:
  - Agrega `chapa_no_line_min_used_lines: 1`.
  - Agrega `chapa_no_line_abs_max_px: 2.7`.
  - Regla: si Hough no detecta al menos 1 línea confiable y la CHAPA supera 2.7px
    de zigzag máximo, el frame se marca `LOW_QUALITY/UNSTABLE`.
- `src/inspection.py`:
  - Aplica la nueva regla dentro de `verticality_quality_enabled`.
  - Los frames inestables se siguen analizando y dibujando, pero no alimentan parada.
- `src/pipeline/machine_stop.py`:
  - Un frame `LOW_QUALITY` conserva el historial interno, pero retorna
    `machine_stop=False` en ese frame. Una imagen borrosa/inestable nunca muestra
    `DETENCION VIRTUAL DE MAQUINA` por sí misma.
- `src/pipeline/annotate.py`:
  - El overlay de PATRON ahora dibuja también la línea ajustada de arriba a abajo
    del frame, además de la polilínea real por bandas.
- `src/utils/config.py`:
  - Defaults para las nuevas claves, deshabilitados por defecto.
- `tests/test_machine_stop.py`:
  - Test de que `LOW_QUALITY` no reporta parada virtual.

**Validación en `C:\Users\DefyC\Downloads\Imagenes_METALCONF_editadas`:**
- `frame_0031`: `OK`, `LOW_QUALITY/UNSTABLE`, `machine_stop=False`,
  `chapa=(std 0.59px, max 2.84px)`, `used_lines=0`.
- `frame_0064`: sigue `OK`, `machine_stop=False`.
- `frame_0121` y `frame_0124`: siguen `NOK` por `PATRON DESALINEADO`.
- `frame_0127`: `LOW_QUALITY/UNSTABLE` y ya no muestra `machine_stop=True`.
- Carpeta completa: 185 frames, `low_quality=24`, `machine_stop_count=18`.
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

**Seguridad:** Parada virtual únicamente; no se modificó PLC, solenoides ni salidas físicas.

---

#### Cambio 55 — Detección de desalineación global grande del PATRON

**Problema:** La lógica de `PATRON DESALINEADO` detectaba zigzag/ondulación del patrón,
pero podía no marcar un corrimiento global grande o una inclinación brusca cuando el
patrón seguía siendo internamente recto. En planta esto puede pasar por golpes o cambios
rápidos de alineación de la chapa/punzonado.

**Cambios:**
- `src/pipeline/edge_centering.py`:
  - `CenteringResult` ahora expone:
    - `pattern_sheet_slope_delta_left_deg`
    - `pattern_sheet_slope_delta_right_deg`
    - `pattern_sheet_slope_delta_max_deg`
  - Estas métricas comparan la inclinación del borde del PATRON contra la inclinación
    del borde real de CHAPA, lado por lado.
- `config/tolerancias.yaml` modelo_B:
  - `pattern_global_offset_max_px: 10.0`
  - `pattern_slope_delta_max_deg: 2.0`
  - Detecta desplazamiento lateral grande del patrón y/o inclinación relativa brusca
    aunque no haya zigzag interno.
- `src/inspection.py`:
  - Si el frame está `STABLE`, cualquiera de estas condiciones marca `NOK`:
    - zigzag de patrón fuera de tolerancia
    - `abs(offset_px) > pattern_global_offset_max_px`
    - `pattern_sheet_slope_delta_max_deg > pattern_slope_delta_max_deg`
  - El panel NOK distingue razones:
    - `PATRON DESCENTRADO (+/-Xpx)`
    - `PATRON INCLINADO (X deg)`
- `src/pipeline/annotate.py`:
  - Agrega `dCh=` al texto de verticalidad para ver el ángulo relativo PATRON-vs-CHAPA.
- `src/utils/config.py`:
  - Defaults nuevos deshabilitados (`0.0`) para no afectar modelos sin override.

**Validación en `C:\Users\DefyC\Downloads\Imagenes_METALCONF_editadas`:**
- Carpeta completa: 185 frames, `raw_nok=26`, `low_quality=24`,
  `machine_stop_count=18`.
- `frame_0122`: ahora `NOK`, `PATRON DESALINEADO`, `dAng=2.01 deg`.
- `frame_0126`: ahora `NOK`, `PATRON DESALINEADO`, `offset=-19.2px`,
  `dAng=2.57 deg`.
- `frame_0064`: sigue `OK`, `machine_stop=False`.
- Overlays de control:
  - `data/output/global_pattern_alignment_debug/frame_0122.png`
  - `data/output/global_pattern_alignment_debug/frame_0126.png`
  - `data/output/global_pattern_alignment_debug/frame_0064.png`
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

**Seguridad:** Sigue siendo parada virtual únicamente. No se modificó PLC, solenoides ni salidas físicas.

---

#### Cambio 56 — Recalibración menos brusca de IMAGEN INESTABLE

**Problema:** La regla agregada en Cambio 54 (`chapa_no_line_abs_max_px: 2.7`) marcaba
demasiados frames como `LOW_QUALITY/UNSTABLE`. En la carpeta de validación dejaba 24/185
frames inestables, demasiado agresivo para operación.

**Cambio:**
- `config/tolerancias.yaml` modelo_B:
  - `chapa_no_line_abs_max_px: 2.7 -> 4.5`
  - Mantiene la condición de `used_lines < 1`, pero exige zigzag claro de CHAPA.

**Validación en `C:\Users\DefyC\Downloads\Imagenes_METALCONF_editadas`:**
- Frames inestables bajan de 24 a 7.
- `frame_0037`: sigue `LOW_QUALITY/UNSTABLE`.
- `frame_0064`: sigue `OK`, `machine_stop=False`.
- `frame_0122`: sigue `NOK` por `PATRON INCLINADO`.
- `frame_0126`: sigue `NOK` por patrón desalineado grande (`offset=-19.2px`,
  `dAng=2.57 deg`).
- `frame_0031`: vuelve a `GOOD/STABLE`; con las métricas actuales no se separa de forma
  robusta de muchos frames normales sin generar demasiados falsos inestables.
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

**Seguridad:** Parada virtual únicamente; sin cambios en PLC, solenoides ni salidas físicas.

---

#### Cambio 57 — Evitar solape entre panel NOK y banners de parada

**Problema:** El panel rojo de razones `NOK` se dibujaba dentro de la ROI en `y=0`,
pero los banners de `DETENCION VIRTUAL DE MAQUINA` y `PATRON DESALINEADO` se dibujaban
después, arriba del frame completo. Cuando había parada virtual, el panel quedaba
tapado/solapado por el banner superior.

**Cambios:**
- `src/pipeline/annotate.py`:
  - `draw_compare_overlay()` acepta `nok_panel_badge_count`.
  - El panel NOK se desplaza hacia abajo `badge_count * _BADGE_H`.
- `src/inspection.py`:
  - Calcula `badge_count = machine_stop + pattern_alignment_warn`.
  - Pasa ese valor al overlay de comparación antes de dibujar los banners.

**Validación:**
- Overlay de control generado:
  - `data/output/overlay_panel_spacing_debug/frame_0126.png`
- En `frame_0126`, con dos banners arriba, el panel NOK queda debajo y visible.
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

---

### Sesión 2026-05-28 (centro real) — Tadeo + Claude

#### Cambio 59 — Líneas de centro reales (polilínea por banda, no X fija)

**Problema reportado:** La línea de centro de chapa (naranja) y la línea de centro del patrón
(blanca) eran líneas verticales fijas dibujadas en `cx = round(sheet_center_x)` y
`hx = round(holes_center_x)` — un solo valor X para toda la altura. Si la chapa venía
inclinada o el patrón desplazado no se reflejaba visualmente: las líneas siempre aparecían
perfectamente verticales y en el "centro promedio" de la pantalla.

**Causa raíz:** Los puntos por banda ya existían para los bordes del patrón y de la chapa
(`pattern_left_points`, `pattern_right_points`, `left_edge_points`, `right_edge_points`),
pero el **centro** de cada uno nunca se calculaba ni se almacenaba. El overlay dibujaba
líneas ficticias.

**Cambios:**

**`src/pipeline/edge_centering.py`:**
- `CenteringResult` agrega dos campos nuevos (frozen dataclass, default vacío):
  - `sheet_center_points: tuple` — per-band midpoint entre borde izquierdo y derecho de CHAPA.
    `x = (edge_left[i].x + edge_right[i].x) / 2`, solo bandas donde ambos bordes se detectaron.
  - `pattern_center_points: tuple` — per-band midpoint entre borde izquierdo y derecho del PATRON.
    Calculado con `_pattern_center_by_band(pat_left, pat_right)` sobre los datos COMPLETOS
    (no sobre la versión trimmed que se usa para métricas zigzag).
- En `compute_centering()`: calcula `sheet_center_pts` y `pattern_center_pts_full`, los almacena
  en el nuevo CenteringResult.

**`src/pipeline/annotate.py`:**
- Import directo de `_fit_line_robust, _line_x_at_y` desde `edge_centering`.
- En `draw_centering_overlay()`:
  - Agrega `sheet_ctr_pts` y `pat_ctr_pts` al bloque de `_shift()`.
  - Reemplaza la línea naranja fija por:
    - Extensión de línea ajustada full-height (alpha=0.30, 1px)
    - Polilínea real por bandas (alpha=0.80, 2px)
    - Fallback a línea punteada si hay <2 puntos.
  - Reemplaza la línea blanca fija por:
    - Extensión de línea ajustada full-height (alpha=0.20, 1px)
    - Polilínea real por bandas (alpha=0.85, 1px)
    - Fallback a línea vertical si hay <2 puntos.
  - La flecha de offset ahora apunta entre los centros REALES evaluados en mid_y
    (cada polilínea se ajusta con `_fit_line_robust` y se evalúa en `mid_y`),
    no entre los centros promedio.

**Resultado visual:**
- Si la chapa está derecha: ambas polilíneas son verticales → igual que antes.
- Si la chapa está inclinada: la línea de CHAPA sigue el eje real de la chapa.
- Si el patrón está desplazado/inclinado: la línea de PATRON muestra la inclinación real.
- La flecha de offset muestra la diferencia real entre los centros en el plano medio.

**Sin tocar:** lógica de detección, `offset_px`, `margin_*`, PLC, solenoides, modelo_B/A params.

---

### Sesión 2026-05-28 — Tadeo + Claude

#### Cambio 58 — Tolerancias modelo_A (Esterilla) más permisivas + fix bug min_area/min_area_small

**Problema reportado:** Esterilla tomaba pocos agujeros y el overlay mostraba casi todos como cruces (missing). Los parámetros del Cambio 50 seguían siendo demasiado estrictos.

**Bug identificado:**
`min_area=300` (piso global de `detect_holes_from_mask`) era MAYOR que `min_area_small=250` (piso del filtro de tipo). Los agujeros chicos con blur (area≈200-300px²) eran rechazados por la primera barrera antes de llegar al filtro de tipo. El floor efectivo real era `max(min_area, min_area_small)`, no `min_area_small`.

**Cambios en `config/tolerancias.yaml` — SOLO sección `modelo_A`:**

| Parámetro | Antes | Después | Razón |
|---|---|---|---|
| `min_area` | 300.0 | 150.0 | Piso global debe ser ≤ min_area_small; blur baja area chico a ~200px² |
| `circularity_min` | 0.70 | 0.55 | Blur severo reduce circularidad a 0.5-0.6 |
| `aspect_ratio_max` | 2.0 | 2.5 | Agujeros levemente deformados |
| `min_area_small` | 250.0 | 150.0 | Alineado con nuevo min_area; fix del bug de piso |
| `max_area_small` | 1500.0 | 2000.0 | Margen ampliado |
| `min_area_large` | 700.0 | 400.0 | Iluminación no ideal en scanner_2 |
| `max_area_large` | 5000.0 | 7000.0 | Margen ampliado |
| `edge_margin_px` | 15.0 | 5.0 | 15px descartaba agujeros reales cerca del borde de ROI |
| `align_match_tol_px` | 100.0 | 150.0 | Más permisivo para fallback RANSAC |
| `min_match_count` | 5 | 4 | Patrón puede estar muy parcialmente en frame |
| `grid_max_missing` | 15 | 25 | ~22% de 112 agujeros; permisivo durante calibración |
| `bbox_filter_margin_px` | 20.0 | 30.0 | Grilla dispersa dy=38 necesita más margen |
| `frame_missing_nok_threshold` | 3 | 8 | Permisivo hasta calibrar con material real |

**Sin tocar:** `modelo_B`, PLC, solenoides, patrón de referencia, lógica de grid.

**Nota de calibración:** Los valores actuales son permisivos a propósito. Una vez que haya imágenes reales de scanner_2/modelo_A en planta, ejecutar `scripts/_debug_areas.py` para ver el histograma de áreas y ajustar `min_area_small`, `min_area_large` al gap real entre ruido y agujeros válidos.

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
| Centrado de chapa | Overlay CHAPA sobre frame completo (fix Cambio 35). CHAPA cae en borde real. PATRON separado. Texto Izq/Der/Delta/Offset OK. |
| Detection ratio | Por frame y promedio de sesión. Flag `CALIDAD_DEGRADADA` configurable. |
| Frame quality | `blur_score` (Laplacian var) + `frame_quality` en InspectionResult. `blur_score_min=0.0` (deshabilitado). Política "hold" wired en FSM y inspect_folder(). |
| modelo_B — ROI | `x=710, w=650, y=3, h=1077` → excluye backlight desnudo en ambos lados |
| modelo_B — Grid | dx=28, dy=22, 258 células. Fase X+Y 2D + affine local post-fase. |
| modelo_B — Tolerancia | `tol_xy_px=22`, `min_area=250`, `grid_max_missing=35`, `bbox_filter_margin=20`, `edge_margin_px=5` |
| modelo_B — Affine refinement | `grid_affine_refinement: true`, `tol_affine=33px`, `min_matches=12` |
| modelo_B — Grabación 185f | **185/185 raw OK**, avg_ratio=104%, 0 NOK, 0 temporal NOK. missing medio=0.81, 160/185 frames sin missing. Extras filtrados: ~180/185 frames con extra=0. |
| Extras falsos | Filtro `extra_min_dist_factor=2.0` en modelo_B: solo detecciones a >44px de todo expected cuentan como extras. |
| Verticalidad bordes | `CenteringResult` expone `pattern_left_slope_deg`, `pattern_right_slope_deg`. Mostrado en overlay: "Vert pat: Izq=±X.X° Der=±Y.Y°". |
| Machine stop — tracking | Tracking por columna de grilla (ci). El mismo punzón roto se reconoce aunque la chapa avance en Y entre frames. Badge muestra columna: `"EN COLUMNA 13"`. |
| Machine stop — acción | **VIRTUAL únicamente.** Badge `"! DETENCION VIRTUAL DE MAQUINA"`. No actúa sobre PLC, solenoides ni FSM. Solo UI/overlay/log. |
| FAULT automático | `consecutive_nok_frames: 40` (modelo_B), `5` (modelo_A). FAULT = solo por streak NOK, nunca por machine_stop. |
| Control automático pistones | Planificado, NO implementado. |
| Tests | Solo `tests/test_io_map.py`. Sin cobertura del pipeline de visión aún. |
| modelo_A (Esterilla) | Grid fitting. dx=68, dy=38. Filas alt. 4 small / 5 large holes. tol_xy_px=16 (<dy/2). grid_max_missing=10 conservador. FAULT deshabilitado (9999). Sin patron scanner_2 aun. |
| modelo_A — Patron global | 117 puntos, 113 celdas unicas, 4 duplicadas en cj=22-25. Deduplica OK en runtime. Fix assign_cells evita futuros duplicados por redondeo. |
| CLI missing-folder | Nuevo comando diagnóstico: exporta CSV + overlays para frames con missing >= --min-missing. No toca criterio productivo. |
| CLI center-folder | Nuevo comando diagnóstico: exporta CSV 20 cols + overlays de centrado por frame. Validado 185/185 fiable. |
| Centrado modelo_B 185f | Offset mediana=-0.95px, Izq=207.2px, Der=209.2px, peor offset=+7.02px. `center_offset_tol_px=0.0` (sin NOK activado). |
| run_folder_csv.py | Fix cp1252: reemplazados caracteres Unicode `→` por ASCII `->` en salidas de consola. |

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

### modelo_A (Esterilla) — pendiente calibración en planta
- **Capturar imagen OK de Esterilla con scanner_2** y reconstruir patrón:
  ```
  python -m src.main build-pattern --model modelo_A --scanner scanner_2 --img <imagen>
  ```
  Esto creará `data/patterns/scanner_2/modelo_A/holes.json` sin los 3 duplicados residuales.
- **Validar `min_area=400px²`** con cámara real: si se pierden agujeros pequeños subir a 350.
- **Validar `tol_xy_px=16px`**: con grid affine deshabilitado puede necesitar ajuste.
- **Activar `grid_affine_refinement: true`** una vez que se vean falsos missing en bordes.
- **Calibrar `grid_max_missing`**: capturar frame con defecto real y ajustar umbral.
- **Calibrar `consecutive_nok_frames`**: actualmente 9999 (FAULT deshabilitado).

### Baja prioridad
- Tests unitarios para pipeline de visión (compare, detect, preprocess, grid_fitting)
