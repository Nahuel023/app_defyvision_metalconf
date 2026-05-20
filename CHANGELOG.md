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

## Estado actual del sistema

| Componente | Estado |
|---|---|
| Solenoides Y10/Y11 | Bloqueados por software y UI. Re-habilitar cuando se implemente control automático. |
| Startup | ~300–600ms hasta UI visible (antes 2–4s) |
| Backlight | Se enciende ANTES de arrancar threads → sin delay en primer frame |
| Pipeline de visión | Vectorizado, cacheado, CLOSE morfológico, centroide estable, matcher closest-first |
| Visor modo servicio | ZoomableImageView: zoom (rueda), pan (drag), fit (doble click / botón) + scroll |
| Extra detections | Detectadas y visibles (diamantes naranjas) en overlay; max_extra configurable |
| Detection ratio | Se muestra por frame y promedio de sesión; advertencia si < min_detection_ratio |
| modelo_B — ROI | `x=710, w=650` → excluye backlight desnudo en ambos lados |
| modelo_B — Grid | `dy=20` (min_spacing=15), 152 celdas únicas → matching correcto |
| modelo_B — Frame ref | `missing=1, extra=1` en frame_0009 (antes missing=25, extra=105) |
| FAULT automático | `consecutive_nok_frames: 9999` (deshabilitado temporalmente para calibración) |
| Control automático pistones | Planificado, NO implementado |
| Tests | Solo `tests/test_io_map.py`. Sin cobertura del pipeline de visión aún. |

## Pendientes / próximos pasos conocidos

### Alta prioridad (próxima sesión)
- **Validar modelo_B en planta con material real y punzón conocido** — verificar que:
  - Frame con material estático: missing ≈ 0-3, extra ≈ 0-3
  - Frame con punzón roto: missing >> grid_max_missing=30 → NOK en streak
  - consecutive_nok_frames=40 es apropiado para la velocidad real de la máquina
- **Elegir un frame de referencia mejor para modelo_B** — frame_0009 muestra solo
  parte de la lámina (sheet entrando al campo de visión). Idealmente: grabar con
  la lámina estática y completamente dentro del ROI para tener todos los agujeros.
- **Calibrar `grid_max_missing`** — actualmente 30 (de 152 = 20%). Ajustar según cuántos
  agujeros puede fallar el punzón antes de que sea una pieza NOK real.

### Media prioridad
- Calibrar y habilitar `consecutive_nok_frames` con valor real (bajarlo de 9999)
- Implementar control automático de solenoides (activar/desactivar pistón según OK/NOK)
- Agregar display de `avg_detection_ratio` en tab Métricas de la UI de servicio
- Medir px/mm para modelo_B (saber a qué distancia real corresponde tol_xy_px=28px)

### Baja prioridad
- Tests unitarios para pipeline de visión (compare, detect, preprocess, grid_fitting)
- Modelo_A: también tiene 4 celdas duplicadas en grid (113 únicas de 117) — revisar
  si afecta a la inspección real o es cosmético
- Considerar Hungarian matching en lugar de greedy-closest-first para casos extremos
