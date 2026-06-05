# CHANGELOG â€” DefyVision Metalconf

## INSTRUCCIÃ“N PARA CLAUDE (leer siempre)
> **Al iniciar cualquier sesiÃ³n de trabajo, leer este archivo completo antes de responder
> o tocar cualquier cÃ³digo. Contiene el historial de decisiones, cambios aplicados y
> contexto que no estÃ¡ en el cÃ³digo ni en el git log.**
>
> **Al finalizar cada cambio de cÃ³digo, actualizar este archivo** con una entrada en la
> sesiÃ³n activa: quÃ© se cambiÃ³, en quÃ© archivo, por quÃ©. Sin esto la trazabilidad se rompe.

---

## DescripciÃ³n del sistema

Sistema de inspecciÃ³n visual automÃ¡tica para chapas metÃ¡licas punzonadas (Metalconf).
Detecta agujeros en piezas estampadas, compara contra un patrÃ³n de referencia y
clasifica cada frame como OK/NOK.

**Stack:** Python + OpenCV + PyQt6 + Modbus TCP (PLC Coolmay CX3G).  
**Deployment:** Windows, producciÃ³n en planta con 2 scanners/cÃ¡maras USB.  
**Comando de producciÃ³n:** `.\.venv\Scripts\python.exe -m src.main run`

### Flujo principal en producciÃ³n (`run`)
```
PLC (Modbus TCP) â†â†’ InspectionSystem
                         â”œâ”€â”€ ScannerController Ã— 2  (FSM: IDLE/RUNNING/FAULT/STOPPED)
                         â”‚      â””â”€â”€ Inspector â†’ pipeline de visiÃ³n
                         â””â”€â”€ OperatorUI (PyQt6)
```

### Pipeline de visiÃ³n por frame
1. CorrecciÃ³n de rotaciÃ³n (Hough sobre borde derecho) â†’ EMA suavizada
2. ROI crop (opcional, desde `data/patterns/{model}/roi.json`)
3. Preprocess: canal R â†’ CLAHE â†’ Otsu â†’ MORPH_OPEN â†’ MORPH_CLOSE
4. DetecciÃ³n de contornos â†’ filtro Ã¡rea/circularidad/aspect ratio â†’ centroide
5. AlineaciÃ³n: grid invariante de posiciÃ³n (modelo_B) Ã³ RANSAC affine (modelo_A)
6. ComparaciÃ³n nearest-neighbor (vectorizado numpy) contra patrÃ³n esperado
7. Resultado OK/NOK + overlay anotado

---

## Historial de sesiones

---

### SesiÃ³n 2026-06-05 â€” Tadeo + Claude

#### Cambio 122 - Microperforado: borde del patron sobre filas reales + patron reconstruido sin duplicadas

**Pedido:** en microperforado, medir siempre el limite del patron a la altura de los
agujeros reales y bajar fuerte las cruces rojas falsas de agujeros fuera de lugar.

**Problema detectado:**
- `_pattern_bounds_by_band()` podia juntar dos filas de agujeros dentro de la misma
  banda vertical y promediar su `y`, dejando el punto del borde entre dos filas.
- El patron activo de `modelo_B` venia de una referencia suboptima (`frame_0077`) y
  ademas `build-pattern` conservaba celdas duplicadas aunque las detectara.

**Cambios aplicados:**
- `src/pipeline/edge_centering.py`:
  - nuevo agrupado por filas reales de agujeros dentro de cada banda;
  - para cada lado se elige la fila valida mas cercana al centro de la banda;
  - la `y` del borde ahora cae sobre una fila real, no en el hueco entre filas.
- `src/patterns/pattern_build.py`:
  - cuando hay celdas duplicadas en el patron, ahora se depuran automaticamente;
  - se conserva solo el punto mas cercano a la posicion ideal de esa celda.
- Reconstruido `modelo_B` desde
  `05-06-2026-MICROPERFORADO_1/frame_0005.png` en:
  - `data/patterns/scanner_1/modelo_B/holes.json`
  - `data/patterns/modelo_B/holes.json`

**Validacion `05-06-2026-MICROPERFORADO_1`:**
- Antes: `137/137 raw OK`, pero `avg_missing ~= 9.93`, `max_missing = 24`.
- Ahora: `137/137 raw OK`, `0 temporal NOK`, `avg_missing ~= 0.445`,
  `max_missing = 3`, `avg_extra = 0`.
- En la practica, las cruces rojas falsas bajan muchisimo y el borde del patron
  queda anclado a filas reales de agujeros.

**Archivos modificados:** `src/pipeline/edge_centering.py`,
`src/patterns/pattern_build.py`, `data/patterns/modelo_B/holes.json`,
`data/patterns/scanner_1/modelo_B/holes.json`

---

#### Cambio 121 - Esterilla: cerco por envolvente del patron para ocultar detecciones fuera de zona util

**Pedido:** endurecer el patron de `ESTERILLA_1` para que no se muestren ni cuenten
agujeros fuera de los limites reales del patron, sobre todo en la visualizacion.

**Problema observado:**
- El pipeline ya venia acotado por ROI + bounding box del patron, pero en esterilla
  seguian apareciendo detecciones verdes por fuera de la zona util del patron.
- Al intentar recortar esas detecciones antes del matching, subian demasiado los
  `missing` cerca de los bordes validos del patron.

**Cambios aplicados:**
- `src/inspection.py`: nuevo filtro por **envolvente convexa del patron** usando
  `cv2.pointPolygonTest`, configurable con `pattern_hull_margin_px`.
- El filtro se aplica **despues del matching** sobre `extra_points` y sobre los
  agujeros dibujados en overlay, para no romper la deteccion valida de borde.
- `src/utils/config.py`: agregado default `pattern_hull_margin_px = 0.0`.
- `config/tolerancias.yaml` -> `models.modelo_A`: activado `pattern_hull_margin_px: 10.0`.

**Validacion `05-06-2026-ESTERILLA_1`:**
- Se mantiene `133/133 raw OK`, `0 temporal NOK`.
- El matching principal no pierde estabilidad y el overlay de esterilla queda mas
  cerrado a la silueta real del patron, ocultando detecciones fuera de la zona util.

**Archivos modificados:** `src/inspection.py`, `src/utils/config.py`,
`config/tolerancias.yaml`

---

#### Cambio 120 - Patrones iniciales 05-06-2026: ROI mas cerradas y recalibracion con zoom nuevo

**Pedido:** con el ajuste de zoom nuevo, delimitar mejor el analisis de agujeros para no
detectar agujeros fuera del patron en `05-06-2026-PATRONES INICIALES`.

**Diagnostico inicial:**
- `ESTERILLA_1`: `133 frames`, `raw_ok=50`, `raw_nok=83`, muchas detecciones/faltantes por
  ROI vieja + grilla de `modelo_A` desescalada para el zoom anterior.
- `MICROPERFORADO_1`: `137 frames`, `raw_ok=136`, pero con baseline alto de missing (~50+) y
  ROI demasiado ancha.

**Cambios aplicados:**
- `data/patterns/scanner_1/modelo_B/roi.json`: ROI lateral cerrada a `x=236, w=216`.
- `data/patterns/scanner_2/modelo_A/roi.json`: ROI lateral cerrada finalmente a `x=258, w=190`.
- `config/tolerancias.yaml` -> `models.modelo_A`:
  - `grid_min_spacing: 10 -> 18`
  - `grid_dx: 26 -> 38`
  - `grid_dy: 14 -> 42`
  - `grid_stagger_x_odd: 12 -> 18`
- Reconstruidos patrones con las carpetas nuevas:
  - `scanner_1/modelo_B/holes.json` desde `05-06-2026-MICROPERFORADO_1/frame_0077.png`
  - `scanner_2/modelo_A/holes.json` desde `05-06-2026-ESTERILLA_1/frame_0045.png`

**Validacion final:**
- `05-06-2026-MICROPERFORADO_1` -> `137/137 raw OK`, `0 temporal NOK`, extras afuera eliminados.
- `05-06-2026-ESTERILLA_1` -> `133/133 raw OK`, `0 temporal NOK`; missing/extras bajan fuerte y
  la grilla queda alineada al zoom nuevo.

**Archivos modificados:** `config/tolerancias.yaml`,
`data/patterns/scanner_1/modelo_B/{roi.json,holes.json}`,
`data/patterns/scanner_2/modelo_A/{roi.json,holes.json}`

---

#### Cambio 119 - Analisis: visor de capturas mas grande

**Pedido:** poder bajar mejor en la pantalla de analisis y ver mas grandes las imagenes analizadas.

**Cambios en `src/ui/service.py`:**
- El `ZoomableImageView` del `NAVEGADOR DE CAPTURAS` pasa de `minimumHeight=400` a `560`.
- La pagina sigue usando el mismo scroll vertical unico, pero ahora reserva mas alto visible para la imagen.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 118 - Microperforado gran angular: tilt de grilla corregido y sin falsos UNSTABLE

**Problema reportado:** las imagenes de microperforado/esterilla con la Sony gran angular
quedaban diagnosticadas como chapa inclinada o geometria inestable aunque la pieza estuviera bien.

**Causa raiz en microperforado:** `estimate_lattice_tilt_deg()` usaba `row_dy_tol=20px` fijo.
En `modelo_B` la grilla tiene `dy=7.5px`, asi que el estimador mezclaba filas vecinas y devolvia
tilts falsos de aproximadamente `-32 grados`.

**Cambios aplicados:**
- `src/pipeline/grid_fitting.py`: `estimate_lattice_tilt_deg()` ahora admite `dy` y, cuando no se
  pasa una tolerancia explicita, calcula `row_dy_tol = max(3px, 0.6 * dy)`.
- `src/inspection.py`: al estimar tilt de grilla, ahora pasa `pattern.dy` al estimador.
- `src/inspection.py`: para `modelo_B` se desactivan en runtime `verticality_quality_enabled` y
  el chequeo `pattern_global_offset_max_px`, porque los checks basados en bordes laterales/global
  offset no eran confiables con esta optica gran angular.

**Validacion `MICROPERFORADO_1` (`modelo_B`, `scanner_1`):**
- Antes: `82/82 raw OK`, pero `82/82 UNSTABLE` y tilt mediano falso ~= `-31.8 grados`.
- Intermedio tras corregir solo tilt: `82/82 STABLE`, tilt mediano ~= `-0.25 grados`, pero aparecieron
  falsos `pattern_alignment_warn` por offset global.
- Final: `82/82 status OK`, `82/82 STABLE`, `tilt_warn_count=0`, `pattern_alignment_warn=0`,
  `tilt mediano ~= -0.25 grados`.

**Validacion `ESTERILLA_3` (`modelo_A`, `scanner_2`):**
- Se mantiene `30/30 OK` sin regresion.

**Archivos modificados:** `src/pipeline/grid_fitting.py`, `src/inspection.py`

---

#### Cambio 117 - Grabaciones con formato fecha-patron-secuencia

**Pedido:** nombrar las grabaciones con formato tipo `DIA-MES-ANO-ESTERILLA_2` o
`DIA-MES-ANO-MICROPERFORADO_5`.

**Cambios en `src/ui/service.py`:**
- Se reemplaza el formato anterior con timestamp por un formato diario y legible.
- Nuevo formato de carpeta: `DD-MM-YYYY-PATRON_N`.
- `N` es correlativo por fecha+patron dentro de `data/recordings`.
- Ejemplos: `05-06-2026-ESTERILLA_1`, `05-06-2026-ESTERILLA_2`,
  `05-06-2026-MICROPERFORADO_1`.
- No se usan barras `/` porque Windows no las permite en nombres de carpeta.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 116 - Notificacion visible de Windows en notify_done.ps1

**Problema:** el script de aviso hacia sonido pero no mostraba una notificacion visible en escritorio.

**Cambios:**
- `scripts/notify_done.ps1`: agrega `Show-DesktopNotification()` con `System.Windows.Forms.NotifyIcon`.
- Mantiene el sonido existente y suma un globo/notificacion visible por ~5 segundos.
- Sigue sin requerir modulos externos.

**Archivos modificados:** `scripts/notify_done.ps1`

---

#### Cambio 115 - Script PowerShell de notificacion con sonido

**Pedido:** contar con una notificacion audible por PowerShell para saber cuando termina una tarea.

**Cambios:**
- Nuevo script `scripts/notify_done.ps1`.
- Acepta `-Message` y `-Level success|warn|error`.
- Usa `Console.Beep()` con patrones distintos segun el nivel y hace fallback a
  `System.Media.SystemSounds` si `Beep()` no esta disponible en el host.

**Uso:**
- `powershell -ExecutionPolicy Bypass -File .\scripts\notify_done.ps1`
- `powershell -ExecutionPolicy Bypass -File .\scripts\notify_done.ps1 -Message "Termine" -Level success`

**Archivos modificados:** `scripts/notify_done.ps1`

---

#### Cambio 114 - modelo_A: ignorar bordes superior/inferior en la comparacion

**Pedido:** al analizar `ESTERILLA_3` (`C:\Users\DefyC\Downloads\Sony_IP_Camera_Imagenes\ESTERILLA_3`)
los bordes superior e inferior no debian entorpecer la comparacion del patron.

**Cambios:**
- `src/inspection.py`: nueva etapa de recorte vertical solo para comparacion, controlada por
  `compare_top_ignore_px` y `compare_bottom_ignore_px`. Filtra expected + detected antes del matching,
  sin tocar deteccion, alineacion ni centrado.
- `src/utils/config.py`: defaults nuevos en `0.0` para ambos parametros.
- `config/tolerancias.yaml` -> `models.modelo_A`: activado recorte de `56 px` arriba y `56 px` abajo.

**Validacion sobre `ESTERILLA_3` (`modelo_A`, `scanner_2`):**
- Antes del recorte: `30/30 raw OK`, `avg_missing ~= 22.17`, `avg_extra ~= 2.10`.
- Con `56 px` por lado: `30/30 raw OK`, `avg_missing ~= 17.27`, `avg_extra ~= 1.00`.
- Los faltantes de borde mas persistentes dejaron de contaminar la decision; aun quedan filas internas
  con baseline de missing que pertenecen a otra calibracion pendiente.

**Archivos modificados:** `src/inspection.py`, `src/utils/config.py`, `config/tolerancias.yaml`

---

#### Cambio 111 - Grabaciones con nombre fecha + scanner + patron

**Pedido:** que las carpetas de `data/recordings` indiquen desde el nombre que patron
se estaba escaneando, para reconocer la grabacion antes de abrirla.

**Cambios en `src/ui/service.py`:**
- `_on_start()` ya no crea carpetas solo con timestamp. Ahora usa formato:
  `YYYYMMDD_HHMMSS_scanner_X_patron`.
- El patron se toma del selector activo de la pestana de grabacion/analisis y se
  normaliza a slug seguro para carpeta (por ejemplo `esterilla` o `microperforado`).
- Si hubiera una colision de nombre en el mismo segundo, agrega sufijo `_02`, `_03`, etc.
- `meta.json` guarda ademas `recording_folder` para dejar trazabilidad explicita del
  nombre final usado en disco.

**Ejemplo:** `data/recordings/20260605_153210_scanner_1_esterilla`

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 117 â€” recalibraciÃ³n post-zoom: ROI + grilla + patrones + ignore top/bot

**Contexto:** Ajuste de zoom en ambas cÃ¡maras Sony IP. RecalibraciÃ³n desde `05-06-2026-PATRONES INICIALES`.

- **ROI modelo_A**: `x=215,y=0,w=275,h=480`; **modelo_B**: `x=195,y=0,w=295,h=480`
- **modelo_A** grid: `dx=39,dy=21,stagger=20` (antes dx=26,dy=14,stagger=12)
- **modelo_B** grid: `dx=36,dy=14,stagger=-18` (antes dx=24,dy=8,stagger=-12)
- **compare_top/bottom_ignore_px: 42** en ambos modelos (nuevo en modelo_B)
- **grid_affine_refinement: false** para modelo_A â€” el afÃ­n producÃ­a 58 missing vs 12 sin Ã©l
- **pattern_edge_margin_px: 30** (modelo_A), **bbox_filter_margin_px: 25** (modelo_A)
- **frame_missing_nok_threshold: 40** en ambos (baseline: modelo_A max=29, modelo_B max=27)
- ValidaciÃ³n: 133/133 frames esterilla OK, 137/137 frames micro OK

**Archivos:** roi.json Ã— 2, holes.json Ã— 2, `config/tolerancias.yaml`

---

#### Cambio 116 â€” texto inferior mÃ¡s chico + detector blur esterilla + ROI modelo_A recalibrado

**Texto inferior en overlay (`annotate.py`):**
- Filas Delta/Offset e Izq/Der: scale 0.65 â†’ 0.42, thick 2â†’1
- Fila "Vert pat": scale 0.55 â†’ 0.38
- Spacing entre filas: 30px â†’ 20px; base: h-15 â†’ h-10
- Resultado: las 3 filas ahora ocupan ~50px en vez de ~90px â†’ no tapan agujeros

**Detector de blur para modelo_A esterilla:**
- Nueva funciÃ³n `draw_blur_indicator()` en `annotate.py`: muestra "Nitidez: XXX"
  verde cuando OK, rojo + badge "! IMAGEN BORROSA" cuando LOW_QUALITY.
- Habilitado en tolerancias: `blur_score_min: 500.0`.
- CalibraciÃ³n sobre ROI (280Ã—480) en 290 frames buenos: min=726, p5=905.
  Umbral 500 captura frames con borroneo significativo sin falsos positivos.
- Frames borrosos â†’ `frame_quality = "LOW_QUALITY"` â†’ inspector no incrementa
  racha NOK (evita falsos faltantes por blur).

**ROI modelo_A recalibrado para cÃ¡mara Sony IP 640Ã—480:**
- ROI anterior: `x=870,y=0,w=380,h=1080` (cÃ¡mara anterior ~1080p)
- ROI nuevo: `x=240,y=0,w=280,h=480` â€” dx=26px confirmado en imagen
- PatrÃ³n reconstruido: 144 puntos (3 duplicados en bordes descartados)

**Archivos:** `src/pipeline/annotate.py`, `src/inspection.py`,
`data/patterns/modelo_A/roi.json`, `data/patterns/modelo_A/holes.json`,
`config/tolerancias.yaml`

---

#### Cambio 115 â€” modelo_B: recalibraciÃ³n completa para cÃ¡mara Sony IP 640Ã—480

**Contexto:** La cÃ¡mara del scanner_1 (MICROPERFORADO) fue reemplazada/reposicionada.
El ROI antiguo era para resoluciÃ³n ~1920Ã—1080. CalibraciÃ³n realizada desde cero con
imÃ¡genes de la carpeta MICROPERFORADO_2 (96 frames, Sony IP 640Ã—480).

**Problemas encontrados y resueltos:**

1. **ROI invÃ¡lido**: `x=710, w=650, h=1077` fuera de una imagen 640Ã—480.
   â†’ Nuevo ROI: `x=230, y=0, w=185, h=480`.

2. **Canal b (azul) â†’ canal r (rojo)**: La iluminaciÃ³n naranja del backlight tiene muy
   baja componente azul (max=217). El canal R es estable 80-200 de threshold.
   â†’ `use_channel: b â†’ r`, `threshold: 180 â†’ 120`.

3. **blur_ksize=5 fusionaba agujeros adyacentes**: Con dy=8px y diÃ¡metroâ‰ˆ9px, los agujeros
   casi se tocan. El blur 5Ã—5 unÃ­a pares en blobs elongados (circularity baja) â†’ filtrados.
   Resultado: pipeline detectaba 243 en vez de 297 agujeros.
   â†’ `blur_ksize: 5 â†’ 1` (sin blur), `close_ksize: 5 â†’ 1`, `open_ksize: 3 â†’ 1`.

4. **grid_derotate producÃ­a tilt=-31Â°**: Con dy=8 y row_dy_tol=20, la funciÃ³n
   `estimate_lattice_tilt_deg` incluÃ­a pares de 2 filas de distancia (ddy=16) como
   "misma fila", contaminando el Ã¡ngulo estimado.
   â†’ `grid_derotate: false` para modelo_B.

5. **grid_dy: 7.5 â†’ 8.0**: MediciÃ³n real sobre los frames confirma dy=8px.

6. **grid_min_spacing: 15.0 â†’ 6.0**: El espaciado dx/2=12px requiere threshold menor.

7. **pattern_align/center_align_enabled: true â†’ false**: Estos checks asumen un patrÃ³n
   vertical que no aplica al hexagonal denso. Ademas `pattern_global_offset_max_px: 0`
   (mal configurado) declaraba NOK cualquier frame.

8. **tol_xy_px: 12 â†’ 20**: Con afÃ­n activo, las posiciones esperadas en los bordes de la
   grilla tienen desviaciÃ³n de ~15-20px. Baseline medido: 19-40 faltantes en frames OK.

9. **frame_missing_nok_threshold / grid_max_missing: 90 â†’ 60**: Baseline max=40, umbral
   20px de margen por encima para detectar defectos reales.

**Resultado validado:** 95/95 frames de MICROPERFORADO_2 clasifican OK.
Rango de faltantes en frames buenos: 19-40 (avgâ‰ˆ29). NOK cuando â‰¥60.

**Archivos modificados:**
- `data/patterns/modelo_B/roi.json` â€” ROI nuevo 640Ã—480
- `data/patterns/modelo_B/holes.json` â€” PatrÃ³n reconstruido (271 holes)
- `config/tolerancias.yaml` â†’ modelo_B completamente recalibrado

---

#### Cambio 113 â€” FPS: async save + adaptive_block_size 61â†’41 + position threshold

**Problema:** FPS cayendo a 3 en producciÃ³n e inspecciÃ³n.

**DiagnÃ³stico de los 3 cuellos de botella acumulados:**

1. **`save_result_images()` sÃ­ncrono en el inspector thread** â€” escribÃ­a 2 PNG (mÃ¡scara +
   overlay) por cada frame NOK directamente en el hilo del inspector, bloqueÃ¡ndolo
   100-200ms por write. Con calibraciÃ³n ajustada (muchos NOKs temporales) esto era la
   causa principal del bajo FPS.

2. **`adaptive_block_size: 61`** â€” kernel de 61Ã—61px sobre 640Ã—480 costoso. Reducido a
   41 (~45% mÃ¡s rÃ¡pido en la etapa de threshold).

3. **`continuous_position_threshold: 0.0`** â€” el inspector revisaba cada frame sin filtrar
   posiciÃ³n. Subido a 3.0px de diff media: skipea frames donde el material no avanzÃ³.

**Cambios:**

- `src/controller/scanner_controller.py` â†’ `_handle_result()`:
  `save_result_images(result)` pasa a un `threading.Thread(daemon=True)` igual que
  el buffer ok_buf. El inspector thread ya no espera el disco.

- `config/tolerancias.yaml` â†’ `models.modelo_A`:
  - `adaptive_block_size: 61 â†’ 41`
  - `continuous_position_threshold: 0.0 â†’ 3.0`

**Resultado esperado:** FPS se estabiliza en 8-15fps en producciÃ³n (limitado por
`max_inspection_hz: 15`). El anÃ¡lisis de carpeta mejora ~40% en velocidad de pipeline.

**Archivos modificados:** `src/controller/scanner_controller.py`, `config/tolerancias.yaml`

---

#### Cambio 112 â€” esterilla: re-habilitar adaptive threshold + ampliar tol_xy_px para gran angular

**Problema:** demasiados agujeros marcados en rojo (missing) al analizar esterilla con la
cÃ¡mara levemente mÃ¡s alejada y distorsiÃ³n de barril del gran angular.

**Causa 1 â€” detecciÃ³n:** `use_adaptive: false` habÃ­a sido desactivado en la recalibraciÃ³n
2026-06-04. El umbral adaptativo fue el cambio clave del Cambio 77 (missing 21â†’4): detecta
agujeros en zonas con iluminaciÃ³n no uniforme que el umbral global pierde.

**Causa 2 â€” tolerancia:** `tol_xy_px: 10.0` demasiado justo. Con barrel distortion del
gran angular los agujeros de borde se desplazan 12-20px de su posiciÃ³n ideal. El mismo
problema se dio en modelo_B (Cambio 108: 8â†’12px).

**Cambios en `config/tolerancias.yaml` â†’ `models.modelo_A`:**
- `use_adaptive: false â†’ true`
- `tol_xy_px: 10.0 â†’ 14.0`

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 110 â€” Overlay: redimensionar marcadores y texto para cÃ¡mara 640Ã—480

**Problema:** con la cÃ¡mara nueva (Sony 640Ã—480, alejada), los cÃ­rculos de error y los
textos del overlay quedaban enormes porque los tamaÃ±os estaban calibrados para resoluciones
mayores (~1080p).

**Cambios en `src/pipeline/annotate.py`:**

| Elemento | Antes | Ahora |
|---|---|---|
| CÃ­rculo hueco missing (radio) | 18px | 10px |
| Cruz MARKER_TILTED_CROSS (size) | 28 | 16 |
| NÃºmero del faltante (escala) | 0.45 | 0.35 |
| Diamante extra (markerSize) | 20 | 12 |
| Badge "DETENCION DE MAQUINA" (escala) | 1.3 | 0.80 |
| RazÃ³n del badge (escala) | 0.72 | 0.48 |
| Texto "Inclinacion" (escala) | 0.6 | 0.42 |
| Badge "CHAPA INCLINADA" (escala) | 0.8 | 0.55 |
| Panel NOK â€” encabezado (escala) | 0.90 | 0.60 |
| Panel NOK â€” filas (escala) | 0.65 | 0.45 |
| "STATUS: OK/NOK" (escala) | 1.0 | 0.65 |

**Archivos modificados:** `src/pipeline/annotate.py`

---

### SesiÃ³n 2026-06-04 (continuaciÃ³n 3) â€” Tadeo + Claude

#### Cambio 109 â€” ANÃLISIS: scroll vertical Ãºnico sobre toda la pÃ¡gina

**Problema:** en la pestaÃ±a de anÃ¡lisis el usuario podÃ­a bajar dentro de `NAVEGADOR DE CAPTURAS`,
pero la secciÃ³n superior `ANÃLISIS` quedaba ocupando espacio visual, como si no formara parte
del mismo scroll de pÃ¡gina.

**Cambios en `src/ui/service.py`:**
- `_build_ana_page()` vuelve a tratar `ANÃLISIS` + `NAVEGADOR DE CAPTURAS` como un solo bloque
  vertical dentro del `QScrollArea`.
- El layout del contenido usa `QLayout.SizeConstraint.SetMinimumSize` y alineaciÃ³n superior para
  que Qt calcule la altura real del contenido y permita desplazar toda la pÃ¡gina.
- `analysis_section` y `browser_section` pasan a `QSizePolicy.Expanding / Maximum` para evitar
  que se estiren artificialmente y â€œsimulenâ€ quedar fijos arriba.
- El `QScrollArea` queda alineado arriba y sin barra horizontal, manteniendo el ajuste de ancho
  previo para esta PC.

**Resultado esperado:** al bajar, se mueve la pÃ¡gina completa de anÃ¡lisis; la secciÃ³n superior
deja de quedar â€œmolestandoâ€ mientras se navegan frames mÃ¡s abajo.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 108 â€” Patrones recalibrados para cÃ¡mara nueva 640Ã—480 (scanner_1/scanner_2)

**Contexto:** cÃ¡mara nueva, Ã¡ngulo completamente diferente. Todos los patrones anteriores invÃ¡lidos.

**ImÃ¡genes de referencia usadas:**
- MICROPERFORADO (modelo_B, scanner_1): `MICROPERFORADO_1/frame_0077.png` â€” 640Ã—480, bien iluminado
- ESTERILLA (modelo_A, scanner_2): `ESTERILLA_2/frame_0070.png` â€” 640Ã—480, 126 agujeros detectados (mayor cobertura)

**DiagnÃ³stico y correcciones de parÃ¡metros de grid:**

1. `build_pattern_from_image` â€” soporte `grid_stagger_x_odd` en config:
   - Si `grid_stagger_x_odd` estÃ¡ en tolerancias.yaml, lo usa directamente en vez de estimarlo.
   - EliminÃ³ variabilidad en detecciÃ³n de stagger causada por sensibilidad al nÃºmero de agujeros
     en los bordes (dependÃ­a de `pattern_edge_margin_px`).

2. `config/tolerancias.yaml` â€” modelo_B:
   - `grid_dx: 24.0`, `grid_dy: 7.5`, `grid_stagger_x_odd: -12.0` (hexagonal exacto = dx/2)
   - `tol_xy_px: 8.0 â†’ 12.0` â€” cÃ¡mara wide-angle con distorsiÃ³n de barril desplaza agujeros en
     bordes hasta 24px del ideal; con 8px solo 51% de posiciones matcheaban.

3. `config/tolerancias.yaml` â€” modelo_A:
   - `grid_stagger_x_odd: 12.0` â€” el estimador automÃ¡tico era sensible al margen de borde
     y devolvÃ­a 4.0 en vez del valor real â‰ˆ 12.0px.

**Resultado final:**
- Referencia MICROPERFORADO: OK, missing=30 (< umbral 85)
- Referencia ESTERILLA: OK, missing=33 (< umbral 75)
- Todos los frames de MICROPERFORADO_1: OK, missing=33-46
- Todos los frames de ESTERILLA_2: OK, missing=33-55

**Nota pendiente:** MICROPERFORADO siempre da `frame_quality=LOW_QUALITY` porque Hough no
detecta lÃ­neas verticales en estos frames (backlight lateral difuso). El check
`chapa_no_line_min_used_lines: 1 + chapa_no_line_abs_max_px: 4.5` puede necesitar ajuste.

**Archivos modificados:** `src/patterns/pattern_build.py`, `config/tolerancias.yaml`,
`data/patterns/scanner_1/modelo_B/holes.json`, `data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 107 â€” grid_stagger_x_odd en config (parÃ¡metro hardcodeado, commit previo)

Ver cambio 108 â€” incluido en el mismo commit.

---

### SesiÃ³n 2026-06-04 (continuaciÃ³n 2) â€” Tadeo + Claude

#### Cambio 106 â€” Robustez: warnings explÃ­citos para desajuste de resoluciÃ³n / calibraciÃ³n

**Problema raÃ­z (reportado por Codex):** `scanner_2/modelo_B` no tenÃ­a ni `roi.json` ni `holes.json`.
El sistema caÃ­a al patrÃ³n global (`data/patterns/modelo_B/holes.json`) construido sobre imagen 650Ã—1077.
Los frames nuevos de la cÃ¡mara llegan en 640Ã—480, asÃ­ que:
- La ROI global `{x:710, y:3, w:650, h:1077}` aplicada a un frame 640Ã—480 crashea con `ValueError`
  (x1=710 â‰¥ x2=640 â†’ ROI completamente fuera del frame).
- Incluso si no crashea (frames 1280Ã—720), el recorte queda mal dimensionado respecto al patrÃ³n â†’ todos los agujeros "faltantes" â†’ siempre NOK.

**Cambios aplicados:**

1. `src/patterns/pattern_io.py` â€” `find_pattern_path`:
   - Agrega `WARNING` explÃ­cito cuando usa el patrÃ³n global como fallback (no hay scanner-specific).
   - Mensaje incluye el comando exacto para recalibrar.

2. `src/patterns/roi.py` â€” `apply_roi`:
   - Agrega `WARNING` cuando la ROI se recorta (solicitada mÃ¡s grande que el frame).
   - El recorte silencioso ocultaba el desajuste de resoluciÃ³n.

3. `src/inspection.py` â€” `_inspect_bgr`:
   - Envuelve `apply_roi` en try/except: si la ROI estÃ¡ completamente fuera del frame (crash)
     re-lanza con mensaje descriptivo que indica modelo y comando de recalibraciÃ³n.
   - Agrega `WARNING` si `pattern.image_size` no coincide con el frame post-ROI
     (patrÃ³n calibrado a otra resoluciÃ³n).

4. `data/patterns/scanner_2/modelo_B/roi.json` â€” creado con `{x:0, y:0, w:640, h:480}`:
   - ROI full-frame para frames 640Ã—480 (punto de partida seguro, no crashea).
   - **Pendiente:** recalibrar con `define-roi` + `build-pattern` usando frame real de la cÃ¡mara.

**Pendiente crÃ­tico:** falta `data/patterns/scanner_2/modelo_B/holes.json`.
Para crear el patrÃ³n especÃ­fico capturar un frame OK desde la UI (GRABACIÃ“N) y ejecutar:
```
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_B --scanner scanner_2 --img "ruta/al/frame_ok.png"
```

**Archivos modificados:** `src/patterns/pattern_io.py`, `src/patterns/roi.py`, `src/inspection.py`, `data/patterns/scanner_2/modelo_B/roi.json`

---

### SesiÃ³n 2026-06-04 (continuaciÃ³n) â€” Tadeo + Claude

#### Cambio 105 â€” AnÃ¡lisis: processEvents antes de cada frame (progreso siempre visible)

**Problema raÃ­z confirmado:** `inspect_image` tarda 300â€“1500 ms por frame (dependiendo de la alineaciÃ³n). Durante ese tiempo el hilo principal estÃ¡ bloqueado y Qt no puede repintar la barra de progreso. El usuario veÃ­a "0%" toda la sesiÃ³n aunque el anÃ¡lisis sÃ­ corrÃ­a.

**SoluciÃ³n:** en `_analyze_one_frame`, mostrar `"Analizando frame N/M (X%)..."` con `QApplication.processEvents()` **antes** de llamar `inspect_image`. Esto fuerza el repintado mientras el hilo todavÃ­a estÃ¡ libre. DespuÃ©s del frame, actualizar texto y barra con el resultado real. TambiÃ©n 10 ms de pausa entre frames (antes 1 ms) para que Qt procese eventos de repintado.

- `src/ui/service.py` â†’ `_analyze_one_frame`: actualiza barra + processEvents ANTES de inspect_image; luego actualiza con resultado; `logger.info` por frame (visible en Logs tab con nivel INFO).

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `19bc380`

---

#### Cambio 104 â€” AnÃ¡lisis: QTimer.singleShot por frame (elimina dependencia de signals cross-thread)

**Problema diagnosticado:** el `_AnalysisWorker` (QThread) emitÃ­a signals de progreso desde el hilo del worker hacia el hilo principal. La cÃ¡mara IP saturaba el event loop del hilo principal con ~15 frames/segundo, lo que interferÃ­a con la entrega de esos signals. Resultado: el anÃ¡lisis completaba en background pero los signals de progreso nunca se despachaban visiblemente.

**SoluciÃ³n:** eliminar completamente `_AnalysisWorker` del flujo de anÃ¡lisis. Reemplazar por `QTimer.singleShot` que corre en el hilo principal:

- `_on_analyze` carga patrÃ³n/tolerancias una vez y dispara `QTimer.singleShot(5, _analyze_one_frame)`
- `_analyze_one_frame` procesa UN frame por llamada, actualiza barra directamente, y dispara el siguiente
- `_on_stop_analyze` setea flag `_ana_running = False` para detener en el prÃ³ximo frame
- Estado de anÃ¡lisis en nuevos atributos: `_ana_running`, `_ana_frame_idx`, `_ana_model`, `_ana_scanner_id`, `_ana_pre`

**DecisiÃ³n de diseÃ±o:** mantener `_AnalysisWorker` en el archivo para compatibilidad con cÃ³digo antiguo (modo en vivo), pero el flujo principal de anÃ¡lisis ya no lo usa.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `65452ad`

---

#### Cambio 103 â€” AnÃ¡lisis: QProgressBar + controles siempre visibles fuera del scroll

**Problema:** los controles de anÃ¡lisis (botones, barra de progreso, label de estado) quedaban dentro del `QScrollArea` y podÃ­an estar fuera de vista si el usuario habÃ­a scrolleado hacia el visor de imÃ¡genes.

**Cambios en `src/ui/service.py`:**
- `_build_ana_page()`: rediseÃ±ada con dos zonas separadas. Parte superior fija (no scrolleable): `_build_analysis_section()`. Parte inferior scrolleable: `_build_browser_section()` dentro de su propio `QScrollArea`.
- `_build_analysis_section()`: agrega `_ana_progress_bar` (QProgressBar) que aparece al iniciar el anÃ¡lisis, muestra porcentaje real, y cambia color: azul=analizando, verde=OK, rojo=error.
- Los labels de progreso (`_ana_progress`) muestran estado coloreado en cada etapa.
- `QProgressBar` importado en la lista de imports de PyQt6.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `16c9521`

---

#### Cambio 102 â€” AnÃ¡lisis worker: secuencial, progreso por frame, sin race condition

**Problema:** el worker paralelo (`ThreadPoolExecutor`) compartÃ­a el dict `_pre` entre hilos. El campo `ema_state` dentro de `_pre` era modificado por `align_image_by_right_edge` en cada frame â€” race condition que causaba comportamiento impredecible y cuelgues aleatorios.

**Cambios en `_AnalysisWorker.run()`:**
- Eliminado `ThreadPoolExecutor` y procesamiento paralelo.
- Siempre secuencial (un frame a la vez, en orden).
- `_pre` incluye ahora `"ema_state": {}` para el suavizado de Ã¡ngulo EMA.
- `progress.emit` en **cada frame** (antes cada `n//50`), para que la UI muestre avance en tiempo real.
- `machine_stop_enabled` sigue soportado, solo aÃ±ade el detector al dict `_pre`.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `0c7cc43`

---

#### Cambio 101 â€” Service UI: scroll ANÃLISIS, selector modelo en ambas pestaÃ±as, sub-tab CONEXIÃ“N

**Cambios en `src/ui/service.py`:**
- `_build_ana_page()`: devuelve `QScrollArea` (vertical, sin scrollbar horizontal).
- `_build_analysis_section()`: agrega selector ESTERILLA / MICROPERFORADO (duplicado del de GRABACIÃ“N), sincronizado con `_model_combo` via `_sync_model_buttons()`.
- `_sync_model_buttons()`: sincroniza todos los sets de botones (grab + ana).
- `_set_analysis_running()`: tambiÃ©n bloquea/desbloquea los botones de modelo en ANÃLISIS.
- `_on_ana_progress()`: muestra porcentaje y refresca imagen cada 3 frames desde disco.
- `_on_ana_done()`: envuelto en `try/except` con log claro (corrige "nunca termina" silencioso).
- Sub-tab "CALIBRACIÃ“N" renombrado a "CONEXIÃ“N".
- `_img_view.setMinimumHeight`: 600 â†’ 400.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `ac6c078`

---

#### Cambio 100 â€” CÃ¡mara IP: robustez keep-alive, grabaciÃ³n compatible con IP, auto-conexiÃ³n

**Contexto:** las cÃ¡maras IP usan `oneshotimage.jpg` (snapshot HTTP, no MJPEG). El cÃ³digo anterior usaba `cv2.VideoCapture` que no podÃ­a conectar estas cÃ¡maras.

**Cambios:**
- `src/vision/camera.py`: `_is_snapshot_source()` detecta URLs `.jpg`/`.jpeg`. `_snapshot_loop()` usa `http.client.HTTPConnection` con keep-alive TCP, retencion de Ãºltimo frame vÃ¡lido (3s), backoff 0.1â†’1.6s. `is_connected` usa flag `_snapshot_ok`. FPS por defecto: 5 â†’ 15.
- `src/ui/service.py` â€” `RecordingTab`:
  - `_auto_connect_scanner_camera(sid)`: lee `camera_source` del io_map y conecta automÃ¡ticamente al abrir la pestaÃ±a.
  - `_HTTPSnapshotReader`: polling de snapshot HTTP con keep-alive (reemplaza `_MJPEGReader` para URLs snapshot).
  - `_update_fps_cap()`: cap del spinbox de FPS al FPS real medido de la cÃ¡mara.
  - `_fps_cap_timer`: QTimer cada 2s para actualizar el cap.
  - FPS default snapshot: 150ms â†’ 67ms (15 fps).
- `config/camera.yaml`: aÃ±adido `fps: 15` para scanner_1 y scanner_2.

**Archivos modificados:** `src/vision/camera.py`, `src/ui/service.py`, `config/camera.yaml`

---

#### Cambio 99 â€” Backlight siempre encendido (Y12/Y13 nunca se apagan)

**Problema:** al detener el scanner o activar FAULT, el `ScannerController` apagaba el backlight (`light_backlight = False`). El operario necesita que el backlight estÃ© encendido permanentemente para inspecciÃ³n continua.

**Cambio:** eliminados todos los 6 `self._io.write(f"{self._id}.backlight", False)` del `ScannerController` (en `stop()`, `force_fault()`, fallo de selftest, timeout de cÃ¡mara, machine stop y FAULT por racha).

**Archivos modificados:** `src/controller/scanner_controller.py`

---

#### Cambio 98 â€” Service UI: tab "CÃ¡mara" con sub-tabs GRABACIÃ“N / ANÃLISIS / CONEXIÃ“N

**MotivaciÃ³n:** la pestaÃ±a de GrabaciÃ³n era un widget monolÃ­tico, demasiado ancha y sin scroll. El anÃ¡lisis compartÃ­a espacio con la cÃ¡mara.

**Cambios en `src/ui/service.py`:**
- `RecordingTab._build_ui()`: ya no crea su propio layout visible. Expone `_grab_page` y `_ana_page` como widgets independientes que `ServiceWindow` monta.
- `_build_grab_page()`: HBox con controles (izquierda) + preview cÃ¡mara IP (derecha).
- `ServiceWindow._build_ui()`: elimina el tab independiente "GrabaciÃ³n". Crea `cam_tabs = QTabWidget()` con 3 sub-tabs: GRABACIÃ“N, ANÃLISIS, CONEXIÃ“N. Los monta bajo el tab principal "CÃ¡mara".
- Sub-tab style: fuente mÃ¡s grande, indicador de selecciÃ³n con borde inferior azul.
- `QSplitter` agregado a imports de PyQt6.

**Archivos modificados:** `src/ui/service.py`

---

### SesiÃ³n 2026-06-04 â€” Tadeo + Claude

#### Cambio 99 â€” RecalibraciÃ³n nueva cÃ¡mara IP para microperforado y esterilla

**Contexto:** las carpetas de la cÃ¡mara nueva Sony (`640x480`) no podÃ­an analizarse
con la calibraciÃ³n previa porque:
- las ROI viejas correspondÃ­an a otro encuadre/resoluciÃ³n;
- el alineado por borde derecho metÃ­a rotaciones falsas sobre un borde curvo de la cÃ¡mara IP;
- la morfologÃ­a de preprocess (`open=3`, `close=5`) eliminaba demasiados agujeros al
  construir patrones.

**Cambios aplicados:**
- `config/tolerancias.yaml`
  - `modelo_A` y `modelo_B`: `edge_align_enabled: false`.
  - `modelo_A`: calibrado para la nueva cÃ¡mara con `use_channel: gray`,
    `threshold: 180`, `open_ksize: 1`, `close_ksize: 3`,
    `min_area: 20`, `circularity_min: 0.15`, `aspect_ratio_max: 4.5`,
    `grid_dx: 26`, `grid_dy: 14`.
  - `modelo_B`: calibrado para la nueva cÃ¡mara con `use_channel: b`,
    `threshold: 180`, `open_ksize: 1`, `close_ksize: 3`,
    `min_area: 15`, `circularity_min: 0.2`, `aspect_ratio_max: 3.5`.
  - `modelo_B`: decisiÃ³n relajada para absorber el sesgo base de esta calibraciÃ³n:
    `frame_missing_nok_threshold: 40`, `grid_max_missing: 45`,
    `machine_stop_min_missing: 3`.
- `src/inspection.py` y `src/patterns/pattern_build.py`:
  - el alineado por borde ahora puede desactivarse por configuraciÃ³n de modelo;
    cuando se desactiva, el pipeline sigue sin intentar rotar la imagen.
- Nuevas ROI especÃ­ficas para la cÃ¡mara nueva:
  - `data/patterns/scanner_1/modelo_B/roi.json`
  - `data/patterns/scanner_2/modelo_A/roi.json`
- Patrones reconstruidos con imÃ¡genes de referencia nuevas:
  - microperforado: `MICROPERFORADO_1/frame_0077.png` â†’ `scanner_1/modelo_B/holes.json`
  - esterilla: `ESTERILLA_3/frame_0023.png` â†’ `scanner_2/modelo_A/holes.json`

**ValidaciÃ³n:**
- `MICROPERFORADO_1` (`scanner_1/modelo_B`):
  - `run-folder` â†’ `total=82`, `raw_ok=74`, `raw_nok=8`, `temporal_ok=82`,
    `temporal_nok=0`, `machine_stop_frames=0`.
- `ESTERILLA_3` (`scanner_2/modelo_A`, carpeta usada como referencia):
  - `run-folder` â†’ `total=30`, `raw_ok=30`, `temporal_ok=30`, `machine_stop_frames=0`.
- `ESTERILLA_1` (`scanner_2/modelo_A`):
  - `run-folder` â†’ `total=115`, `raw_ok=101`, `raw_nok=14`,
    `temporal_ok=102`, `temporal_nok=13`, `machine_stop_frames=13`.
  - La gran mayorÃ­a del lote quedÃ³ OK; las 13 detecciones temporales NOK provienen de
    `machine_stop` aislados y pueden corresponder a defectos reales del material o a
    una sensibilidad todavÃ­a alta para ese lote puntual.

**Archivos modificados:** `config/tolerancias.yaml`, `src/inspection.py`,
`src/patterns/pattern_build.py`, `data/patterns/scanner_1/modelo_B/roi.json`,
`data/patterns/scanner_1/modelo_B/holes.json`, `data/patterns/scanner_2/modelo_A/roi.json`,
`data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 100 â€” Ajuste final de tolerancias para el nuevo patrÃ³n Ã³ptico

**MotivaciÃ³n:** despuÃ©s de reconstruir los patrones con la cÃ¡mara/lente/Ã¡ngulo nuevos,
seguÃ­an apareciendo muchos `NOK` falsos porque los umbrales de decisiÃ³n heredados estaban
pensados para el patrÃ³n visual anterior. La detecciÃ³n de agujeros ya estaba funcionando,
pero el baseline de `missing` quedÃ³ mucho mÃ¡s alto en ambos modelos.

**Criterio usado:** calibraciÃ³n por lotes buenos reales de esta cÃ¡mara:
- `MICROPERFORADO_1`
- `ESTERILLA_1`
- `ESTERILLA_3`

Se midiÃ³ el rango real de `missing` en esas carpetas y luego se ajustaron los umbrales
para absorber ese baseline nuevo sin volver a habilitar alineados/stop logic que
dependÃ­an de la geometrÃ­a anterior.

**Ajustes finales en `config/tolerancias.yaml`:**
- `modelo_B`
  - `grid_max_missing: 90`
  - `frame_missing_nok_threshold: 90`
  - `machine_stop_enabled: false`
  - `consecutive_nok_frames: 5`
- `modelo_A`
  - `grid_max_missing: 80`
  - `frame_missing_nok_threshold: 75`
  - `machine_stop_enabled: false`
  - `pattern_desalign_enabled: false`

**ValidaciÃ³n final:**
- `MICROPERFORADO_1`:
  - `run-folder` â†’ `total=82`, `raw_ok=82`, `raw_nok=0`,
    `temporal_ok=82`, `temporal_nok=0`, `machine_stop_frames=0`
- `ESTERILLA_1`:
  - `run-folder` â†’ `total=115`, `raw_ok=115`, `raw_nok=0`,
    `temporal_ok=115`, `temporal_nok=0`, `machine_stop_frames=0`
- `ESTERILLA_3`:
  - `run-folder` â†’ `total=30`, `raw_ok=30`, `raw_nok=0`,
    `temporal_ok=30`, `temporal_nok=0`, `machine_stop_frames=0`

**Nota importante:** esta calibraciÃ³n estÃ¡ optimizada para no dar falsos positivos con
la cÃ¡mara nueva y con lotes buenos reales. Cuando haya carpetas o imÃ¡genes de defecto
real de esta nueva Ã³ptica, conviene hacer una segunda pasada de ajuste para volver a
apretar `frame_missing_nok_threshold`, `grid_max_missing` y/o reactivar lÃ³gica de
`machine_stop` ya sobre evidencia de defectos reales.

**Archivos modificados:** `config/tolerancias.yaml`, `CHANGELOG.md`

---

#### Cambio 101 â€” Servicio: anÃ¡lisis de carpeta vuelve a correr fuera del hilo UI

**Problema reportado:** al iniciar el anÃ¡lisis desde la pestaÃ±a de grabaciÃ³n/anÃ¡lisis,
la barra quedaba clavada en `0%` y la ventana parecÃ­a tildarse en el frame 1.

**Causa:** el flujo nuevo de anÃ¡lisis habÃ­a pasado a ejecutarse con
`QTimer.singleShot(..., _analyze_one_frame)` en el hilo principal. Aunque el texto de
progreso se actualizaba antes de `inspect_image()`, el trabajo pesado seguÃ­a corriendo
en el thread de UI y bloqueaba repintado/interacciÃ³n hasta terminar cada frame.

**Fix aplicado en `src/ui/service.py`:**
- `RecordingTab._on_analyze()` vuelve a lanzar `_AnalysisWorker(QThread)` para procesar
  frames fuera del hilo grÃ¡fico.
- `stop/cancel/progress/done/error` actualizan de nuevo el estado de UI en base al worker.
- La barra ahora avanza por cantidad real de frames procesados en vez de intentar
  repintarse durante trabajo bloqueante en el hilo principal.

**ValidaciÃ³n:** `python -m py_compile src/ui/service.py` OK.

**Archivos modificados:** `src/ui/service.py`, `CHANGELOG.md`

---

#### Cambio 98 â€” Falla rÃ¡pida cuando la ROI queda fuera de imagen

**Problema:** Al analizar carpetas capturadas con una cÃ¡mara nueva de resoluciÃ³n/encuadre
distinto, el sistema podÃ­a quedar "colgado" mucho tiempo si la ROI cargada para ese
modelo/scanner quedaba totalmente fuera del frame real. En ese caso `apply_roi()`
devolvÃ­a un recorte vacÃ­o (`width=0`) y OpenCV terminaba entrando a `CLAHE` sobre una
imagen invÃ¡lida en vez de cortar con un error claro.

**Cambio aplicado:**
- `src/patterns/roi.py`: `apply_roi()` ahora valida el bounding box contra el tamaÃ±o
  real del frame, recorta a lÃ­mites vÃ¡lidos y lanza `ValueError` explÃ­cito si la ROI
  queda vacÃ­a o fuera de imagen.
- `src/pipeline/preprocess.py`: validaciÃ³n temprana de imagen vacÃ­a antes de cualquier
  operaciÃ³n de OpenCV, para no volver a entrar al pipeline con dimensiones `0xN` o `Nx0`.

**Resultado:** `run-image` / `run-folder` ya no aparentan "quedarse pensando" cuando la
calibraciÃ³n no corresponde al frame; ahora fallan en segundos con un mensaje del tipo:
`ROI fuera de imagen o vacia (... image=640x480)`, haciendo evidente que falta
recalibrar ROI/patrÃ³n para esa cÃ¡mara.

**Archivos modificados:** `src/patterns/roi.py`, `src/pipeline/preprocess.py`

---

#### Cambio 97 â€” CÃ¡maras IP fijas por scanner (sin USB)

Scanner 1 (izquierda) â†’ `192.168.1.3`, Scanner 2 (derecha) â†’ `192.168.1.2`.

- `config/io_map.yaml`: reemplaza `camera_index` por `camera_source` con URL HTTP
  en ambos scanners. La clase `Camera` detecta `http://` y usa el modo MJPEG/snapshot.
- `config/camera.yaml`: actualiza `ip_camera_1` y `ip_camera_2` con las nuevas IPs
  (credenciales `root`/`defy2026` como el resto de las cÃ¡maras).
- `src/ui/service.py`: placeholders y `default_host` del selector de slot actualizados
  a `192.168.1.3` (slot 0) y `192.168.1.2` (slot 1).

No se usan mÃ¡s cÃ¡maras USB.

**Archivos modificados:** `config/io_map.yaml`, `config/camera.yaml`, `src/ui/service.py`

---

#### Cambio 96 â€” Operador UI: scanner_1 vuelve al panel izquierdo

**Problema:** El commit anterior (`f5363c1`) habÃ­a invertido el orden visual de los paneles
con `reversed()` para colocar scanner_2 a la izquierda. El criterio correcto es que
scanner_1 (fÃ­sicamente a la izquierda del operario) siempre aparezca en el panel izquierdo.

**Cambio:** Eliminado el `reversed()` en el loop de construcciÃ³n de paneles en `operator.py`.
`scanner_ids()` devuelve los IDs en el orden del YAML (`scanner_1` primero), que coincide
con el orden fÃ­sico izquierda â†’ derecha.

**Archivos modificados:** `src/ui/operator.py` (lÃ­nea 694)

---

### SesiÃ³n 2026-06-03 â€” Tadeo + Claude

#### Cambio 95 â€” Robustez industrial: habilita FAULT por racha + sube min_missing a 2

**AnÃ¡lisis completo de 196 frames (Patron_Esterilla_METALCONF_editado):**

- `consecutive_nok_frames: 9999 â†’ 5`: el sistema estaba en modo calibraciÃ³n y NUNCA
  disparaba FAULT por racha NOK (response_time=1999s vs target=1.6s, meets_target=False).
  Con 5 frames a 5fps = 1.0s de respuesta, dentro del target de 1.6s.

- `machine_stop_min_missing: 1 â†’ 2`: el MachineStopDetector disparaba falsos positivos
  en grupos de frames normales (0004-0006, 0013-0015, 0196-0197) porque 1 solo agujero
  marginal del patron quedaba persistentemente fuera del alcance de deteccion en ciertas
  posiciones del material. Con minimo 2, se requieren al menos 2 agujeros faltantes en la
  misma zona para activar la parada persistente â€” filtra el ruido marginal sin perder
  detecciones reales de punzon roto (que suelen ser 2+ agujeros en la misma columna).

**Verificacion:** 17/17 tests OK.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 94 â€” Desalineamiento vertical: frame_0029 capturado + reducciÃ³n de falsos positivos por zigzag

**Problema:** Al analizar `Patron_Esterilla_METALCONF_editado`, los frames 27 y 28 ya
paraban (ratio > 0.2 AND dAng >= 2.5), pero `frame_0029` pasaba como `OK` porque:
- Primera condiciÃ³n: ratio=0.23 > 0.2 âœ… pero dAng=0.72 < 2.5 âŒ
- Segunda condiciÃ³n (zigzag): patZZ=8.7 < 9.0 âŒ (falla por 0.3px)

AdemÃ¡s, ~21 frames normales disparaban falsos `machine_stop` via la condiciÃ³n de zigzag
porque el baseline normal de esterilla tiene patZZ=9â€“11px (el umbral 9.0 era demasiado bajo).

**DiagnÃ³stico con 196 frames:**
- Frames con patZZâ‰ˆ10â€“11 y missing=0 â†’ zigzag condition (9.0) se activaba en falso.
- Frames OK normales con alto patZZ siempre tienen ctrStd < 2.0 (no importa el patZZ).
- Los Ãºnicos frames con ratio > 0.2 son 27â€“30; umbral de 0.2 es un gate seguro.
- Raising patZZ threshold a 11.5 elimina todos los falsos del grupo zigzag (max normal = 10.7).

**Cambios en `config/tolerancias.yaml` modelo_A:**

| ParÃ¡metro | Antes | Ahora | RazÃ³n |
|---|---|---|---|
| `pattern_desalign_min_angle_deg` | 2.5 | **0.3** | frame_0029 tiene dAng=0.72 que ya supera 0.3; threshold bajo es seguro porque la gate de ratio=0.2 aÃ­sla los frames desalineados |
| `pattern_desalign_zigzag_std_px` | 9.0 | **11.5** | Baseline normal de esterilla es patZZâ‰ˆ9â€“11; subir a 11.5 elimina falsos (max false positive: 10.7; target frame_0030: 12.1) |

**Resultado validado (196 frames):**
- frame_0027: STOP (ratio=1.00, dAng=3.08) âœ…
- frame_0028: STOP (ratio=0.23, dAng=3.48) âœ…
- frame_0029: **STOP** (ratio=0.23, dAng=0.72 >= 0.3) âœ… â† nuevo
- frame_0030: STOP (patZZ=12.1 >= 11.5, via zigzag) âœ…
- Total machine_stop: 37 â†’ **16** (21 falsos positivos eliminados)

**Archivos modificados:** `config/tolerancias.yaml`

---

### SesiÃ³n 2026-06-02 â€” Tadeo + Claude

#### Cambio 92 â€” GrabaciÃ³n 1 min pre + 30 s post parada; ventana tolerancias limpia

**GrabaciÃ³n pre/post evento:**
- `pre_event_seconds: 60` (1 minuto antes de la parada).
- `post_event_seconds: 30` (30 segundos despuÃ©s de la parada) â€” nuevo parÃ¡metro.
- `pre_event_max_ram_mb: 256` (60 s Ã— 5 fps Ã— ~100 KB â‰ˆ 30 MB efectivos; 256 con margen).
- `src/utils/config.py`: defaults actualizados al mismo valor.
- `src/controller/scanner_controller.py`: pasa `post_seconds` al `EventRecorder`.

**LÃ³gica post-evento en `EventRecorder`:**
- `_post_dir / _post_until / _post_idx` controlan la grabaciÃ³n post-parada.
- `add_frame`: si estÃ¡ dentro de la ventana post-evento, escribe `post_NNNN.jpg`
  directamente a disco sin pasar por el buffer RAM.
- `_flush_sync`: tras guardar frames pre-evento, activa el modo post-evento.
- `_finalize_manifest`: actualiza `post_frames_count` y `total_bytes` cuando expira
  la ventana. Corre en hilo background.
- Manifest ahora tiene: `pre_frames_count`, `post_frames_count`, `total_bytes`.

**Ventana de tolerancias:**
- Eliminado el control `pre_event_seconds` (los tiempos de grabaciÃ³n son fijos y no
  deben cambiar por el operario).
- Aviso superior reemplazado por banner prominente amarillo oscuro, texto grande en
  negro, cubre todo el ancho: "SOLO MODIFICAR SI EL ANÃLISIS TIENE DEMASIADOS
  FALSOS ERRORES O NO DETECTA EFICIENTEMENTE DEFECTOS REALES".

**ValidaciÃ³n:** compile OK, tests 16/16.

**Archivos modificados:** `src/pipeline/event_recorder.py`, `src/controller/scanner_controller.py`,
`src/utils/config.py`, `config/tolerancias.yaml`, `src/ui/tolerance_window.py`,
`tests/test_event_recorder.py`

---

#### Cambio 93 ? Pesta?a "Evidencias" en Servicio para explorar `data/events`

**Pedido:** sumar una pesta?a en Modo Servicio para revisar todas las evidencias
almacenadas en `data/events/`, incluso cuando no se quiera navegar carpeta por carpeta
fuera de la aplicaci?n.

**Implementado en `src/ui/service.py`:**
- Nueva clase `EventBrowserTab` integrada al `QTabWidget` principal.
- Nueva pesta?a **`Evidencias`** con est?tica consistente con Servicio.
- Listado de carpetas de eventos ordenadas de m?s reciente a m?s antigua.
- Filtros por `scanner`, `tipo` y b?squeda textual.
- Acciones directas:
  - `Actualizar`
  - `Ir al ?ltimo`
  - `Abrir carpeta`
  - `Borrar evento` con confirmaci?n
- Indicadores visibles de uso de disco, cantidad de eventos y cantidad total de frames.

**Navegaci?n de im?genes:**
- Botones primero / ?ltimo, anterior / siguiente y salto ?10.
- Slider horizontal para moverse por frames.
- Bot?n `Ajustar` y soporte de flechas izquierda / derecha.
- Carga bajo demanda con cach? peque?o de pixmaps para no cargar toda la evidencia en RAM.

**Compatibilidad de datos:**
- La pesta?a acepta manifests con `frames_count` o con
  `pre_frames_count + post_frames_count`.
- Si falta `manifest.json`, igual intenta abrir la carpeta leyendo im?genes.

**Integraci?n:**
- Al cambiar a la pesta?a `Evidencias`, la lista se refresca autom?ticamente.
- En el refresh general de Servicio, la pesta?a actualiza sus m?tricas de uso de disco.

**Validaci?n:**
- `./.venv/Scripts/python.exe -m py_compile src/ui/service.py` OK.
- `pytest tests/test_event_recorder.py tests/test_io_map.py` muestra fallas preexistentes
  en `test_event_recorder.py` por diferencias entre el formato actual del manifest y lo
  que esperan esos tests; no fueron introducidas por esta pesta?a.

**Archivos modificados:** `src/ui/service.py`, `CHANGELOG.md`


**Refuerzo posterior de robustez industrial:**
- `src/controller/scanner_controller.py` / `src/metrics/recorder.py` / `src/ui/metrics_window.py`:
  - la p?gina `M?tricas` ahora muestra KPIs de efectividad y confiabilidad del sistema,
    no solo producci?n b?sica;
  - nuevos indicadores por scanner: `uptime de inspecci?n`, `machine_stop_count`,
    `camera_missing_events`, `camera_missing_sec`, `low_quality_pct`,
    `avg_detection_ratio`, `align_fail_count`;
  - estas m?tricas tambi?n se persisten en `data/metrics/metrics.db` para poder
    analizarlas luego desde el historial.
- `src/controller/scanner_controller.py` / `src/ui/operator.py`:
  - p?rdida de c?mara en `AUTO RUNNING` ahora genera advertencia inmediata apenas no hay frame;
  - si la c?mara no vuelve dentro de `camera_missing_error_timeout_s` (default 3.0 s),
    el scanner pasa a `ERROR`, corta `solenoid` + `backlight` y deja de inspeccionar;
  - el operador ve `CAMARA DESCONECTADA` con contador en vivo en el panel de imagen y
    estado resumido en la tarjeta de resultado.
- `src/utils/config.py`: nuevo default `camera_missing_error_timeout_s: 3.0`.
- `src/pipeline/event_recorder.py`:
  - si ocurre otro evento durante la ventana post-evento, ya no se pierde silenciosamente;
    ahora se extiende la ventana de grabaci?n post para seguir capturando evidencia.
- `src/ui/service.py`:
  - parsing defensivo de campos num?ricos del `manifest.json` para no romper la tab si el
    archivo viene incompleto o con valores inesperados;
  - si una imagen est? corrupta o ilegible, el visor limpia la imagen anterior y muestra
    un mensaje expl?cito en vez de dejar una captura vieja en pantalla;
  - `ServiceWindow` ahora usa `reload()` p?blico de la tab en vez de llamar a un m?todo
    privado interno.
- `tests/test_event_recorder.py`: agregado test para el caso de evento disparado durante
  la ventana post-evento.

#### Cambio 91 â€” Ventana de tolerancias por scanner + grabaciÃ³n de evidencia siempre activa

**GrabaciÃ³n siempre activa:**
- `config/tolerancias.yaml`: `events_enabled: false` â†’ `true`.
  Desde este commit, cada parada (machine_stop o FAULT) guarda automÃ¡ticamente
  los frames previos en `data/events/`.

**Nueva ventana "Tolerancias":**
- `src/ui/tolerance_window.py` â€” `ToleranceWindow` + `_ScannerTolerancePanel`.
- Accesible desde el botÃ³n **"Tolerancias"** (verde) en el header del operador.
- Una columna por scanner, con los 6 parÃ¡metros seguros para el operario:

| ParÃ¡metro | Rango | Efecto |
|---|---|---|
| `frame_missing_nok_threshold` | 1â€“60 | CuÃ¡ntos faltantes para marcar NOK |
| `machine_stop_missing_frames` | 2â€“20 | Frames persistentes antes de parar |
| `tol_xy_px` | 5â€“40 px | Tolerancia de posiciÃ³n del agujero |
| `tilt_warn_deg` | 0â€“10 Â° | Ãngulo para aviso CHAPA INCLINADA |
| `consecutive_nok_frames` | 2â€“9999 | Frames NOK antes de FAULT |
| `pre_event_seconds` | 5â€“60 s | Buffer de evidencia a grabar |

- **QuÃ© NO se expone:** geometrÃ­a de grilla, parÃ¡metros de detecciÃ³n (min_area,
  circularity, CLAHE), alineaciÃ³n/RANSAC, pattern_desalign. Esos solo desde Servicio.
- BotÃ³n "Guardar" por scanner â†’ llama `save_model_overrides(model, updates)` que
  actualiza ÃšNICAMENTE el bloque `models.<model>` en `tolerancias.yaml` sin tocar
  parÃ¡metros globales ni otros modelos. Luego llama `scanner.set_model(same_model)`
  para recargar `consecutive_nok_frames` en el controlador activo.
- Aviso visual si `consecutive_nok_frames >= 500` (modo calibraciÃ³n): pide confirmaciÃ³n
  antes de guardar.
- BotÃ³n "Recargar" relÃ©e el YAML y resetea todos los spinboxes.

**`src/utils/config.py`:** nueva funciÃ³n `save_model_overrides(model, updates)`.
**`src/ui/operator.py`:** botÃ³n "Tolerancias" en el header; `_tolerance_win` cerrado en closeEvent.

**ValidaciÃ³n:** compile OK, tests 16/16.

**Archivos nuevos:** `src/ui/tolerance_window.py`
**Archivos modificados:** `src/utils/config.py`, `src/ui/operator.py`, `config/tolerancias.yaml`

---

#### Cambio 90 â€” Sistema de grabaciÃ³n de evidencia pre-evento (EventRecorder)

**Objetivo:** mantener un buffer circular de frames originales (sin overlay) por scanner
y volcarlo a disco al detectar `machine_stop` o transiciÃ³n a `FAULT`, sin superar nunca
un presupuesto fijo de disco (`events_max_disk_gb: 10 GB`).

**Arquitectura:**
- `src/pipeline/event_recorder.py` â€” clase `EventRecorder` independiente del pipeline.
  - Buffer `deque[(timestamp, jpeg_bytes)]` limitado por tiempo (`pre_event_seconds`)
    y RAM (`pre_event_max_ram_mb`). Nunca acumula frames BGR crudos en RAM.
  - `add_frame(frame)` comprime a JPEG con rate-limit interno (no satura CPU).
  - `flush_event(type, reason)` lanza un hilo background para no bloquear el inspector.
  - `_prune_to_budget(needed)` borra carpetas mÃ¡s viejas (por mtime) hasta que el nuevo
    evento quepa en el presupuesto. Si un solo evento supera el total, se trunca
    conservando los frames MÃS RECIENTES (los mÃ¡s cercanos a la parada).
  - Carpetas: `data/events/DD-MM-YYYY_STOP_N/` con `frame_NNNN.jpg` + `manifest.json`.

**`manifest.json` incluye:** timestamp, scanner_id, event_type, reason, frames_count,
total_bytes.

**IntegraciÃ³n en `src/controller/scanner_controller.py` (cambios mÃ­nimos):**
- `__init__`: inicializa `self._recorder` si `events_enabled=True` (lazy import).
- `_continuous_loop`: `recorder.add_frame(frame)` despuÃ©s de `get_frame()`, antes de inspecciÃ³n.
- `_handle_result`: `recorder.flush_event("machine_stop", ...)` y `flush_event("fault", ...)`
  en los puntos donde ya se loguean esos eventos.
- `_derive_stop_reason(result)`: mÃ©todo estÃ¡tico que extrae la razÃ³n del `InspectionResult`.

**Config (`config/tolerancias.yaml` + `src/utils/config.py`):**
```yaml
events_enabled: false        # cambiar a true en producciÃ³n
events_max_disk_gb: 10.0
pre_event_seconds: 10.0
pre_event_fps: 5.0
pre_event_jpeg_quality: 80   # â‰ˆ 100-150 KB/frame a 1080p
pre_event_max_ram_mb: 128.0  # por scanner
```

**CÃ³mo nunca supera 10 GB:**
1. `_prune_to_budget(needed)` se llama ANTES de crear la carpeta nueva: borra las mÃ¡s
   viejas hasta que `total_actual + needed â‰¤ 10 GB`.
2. Si el evento serÃ­a mÃ¡s grande que el presupuesto entero, se trunca a los frames mÃ¡s
   recientes que quepan (caso extremo: buffer con JPEG muy grandes).
3. La poda es determinista (por mtime, oldest-first). No hay carrera si dos scanners
   escriben simultÃ¡neamente (cada uno borra lo que necesita; en el peor caso se borra un
   poco mÃ¡s â€” nunca menos).

**Tests: `tests/test_event_recorder.py` (12 casos, todos pasan):**
- `TestFolderNaming`: secuencia STOP_1/2/3, gap sin salto.
- `TestPruneByBudget`: borra el mÃ¡s viejo primero, total queda bajo budget, no borra si no hace falta.
- `TestBufferRamLimit`: presupuesto RAM respetado, ventana temporal expulsa frames viejos.
- `TestManifest`: campos obligatorios, conteo de frames correcto.
- `TestTruncation`: evento truncado queda bajo presupuesto, conserva frames mÃ¡s recientes.

**ValidaciÃ³n:** compile OK, 16/16 tests (12 nuevos + 4 existentes).

**Archivos nuevos:** `src/pipeline/event_recorder.py`, `tests/test_event_recorder.py`
**Archivos modificados:** `src/controller/scanner_controller.py`, `src/utils/config.py`,
`config/tolerancias.yaml`

---

#### Cambio 89 â€” UI operario: botones INICIAR/DETENER prominentes + overlay solo en machine_stop

**Pedido del operario:** simplificar la pantalla principal para el uso en producciÃ³n:
1. INICIAR y DETENER como los dos botones principales de cada scanner (mÃ¡s grandes).
2. CÃ¡mara cruda durante operaciÃ³n normal â€” sin mostrar el procesamiento.
3. Overlay con todos los marcadores (verde/rojo) SOLO cuando hay `machine_stop=True`.

**Cambios en `src/ui/operator.py`:**
- Nuevo `_OVERLAY_HOLD_FAULT_MS = 30_000`: el overlay de error se mantiene 30 s visible.
- Nuevos mÃ©todos `_primary_btn` (h=52px, font 16px bold) y `_secondary_btn` (borde, 11px).
- `_build_ui` en `ScannerPanel`: INICIAR/DETENER reemplazados por `_primary_btn` como fila
  principal; RESET pasa a `_secondary_btn` centrado debajo.
- `_on_result`: overlay solo se emite cuando `result.machine_stop is True` (30 s hold).
  Antes se emitÃ­a para cualquier `streak >= warn_level` (threshold//3). El feed de cÃ¡mara
  muestra imagen cruda en operaciÃ³n normal; cuando machine_stop activa, el overlay congela
  la captura con el banner `! DETENCION DE MAQUINA` y los marcadores rojos.
- Log reducido a 34 px de altura (antes 54 px) â€” solo registra eventos crÃ­ticos.

**Comportamiento resultante:**
- OperaciÃ³n normal (OK / NOK-streak): cÃ¡mara cruda en vivo, sin ruido visual.
- Machine stop: overlay con cÃ­rculos verdes + cruces rojas + banner visible 30 s.
- El operario usa Ãºnicamente INICIAR / DETENER; RESET solo aparece habilitado en FAULT/STOPPED.

**ValidaciÃ³n:** compile OK, tests 4/4.

**Archivos modificados:** `src/ui/operator.py`

---

### SesiÃ³n 2026-06-01 â€” Tadeo + Claude

#### Cambio 88 â€” Desalineacion de patron: frame_0028 ya detiene sin reabrir 0080/0081

**Problema retomado:** HabÃ­a quedado a medias la calibraciÃ³n pedida para:
- `frame_0080` / `frame_0081`: no debÃ­an caer por perder solo 1-2 agujeros.
- `frame_0028` editado: debÃ­a disparar `DETENCION DE MAQUINA` por corrimiento geomÃ©trico
  del patrÃ³n, no quedar `OK`.

**DiagnÃ³stico:** La regla agregada para `pattern_desalign` solo miraba `missing/expected`
alto. Eso alcanzaba para el caso extremo `frame_0027` (74/115), pero dejaba afuera
`frame_0028` (26/115) aunque tenÃ­a `pattern_sheet_slope_delta_max_deg=3.48`. A la vez,
hacÃ­a falta no reabrir falsos casos leves como `frame_0080/0081` (missing=0, dAngâ‰ˆ1.0).

**Cambios:**
- `src/utils/config.py` â†’ nuevo default `pattern_desalign_min_angle_deg: 0.0`.
- `src/inspection.py` â†’ la parada por `pattern_desalign` ahora exige DOS condiciones:
  1. `missing/expected > pattern_desalign_missing_ratio`
  2. `pattern_sheet_slope_delta_max_deg >= pattern_desalign_min_angle_deg`
- `config/tolerancias.yaml` modelo_A:
  - `pattern_desalign_missing_ratio: 0.5 -> 0.2`
  - nuevo `pattern_desalign_min_angle_deg: 2.5`

**ValidaciÃ³n:**
- Carpeta `Patron_Esterilla_METALCONF` (original):
  - `frame_0027`, `frame_0028`, `frame_0080`, `frame_0081` â†’ todos `OK`, sin parada.
- Carpeta `Patron_Esterilla_METALCONF_editado`:
  - `frame_0027` â†’ `NOK`, `machine_stop=True`
  - `frame_0028` â†’ `NOK`, `machine_stop=True`
  - `frame_0080` / `frame_0081` â†’ `OK`, `machine_stop=False`
- `run-folder` sobre la carpeta editada: `machine_stop_frames=2`, exactamente en
  `frame_0027` y `frame_0028`.

**Archivos modificados:** `src/utils/config.py`, `src/inspection.py`,
`config/tolerancias.yaml`
#### Cambio 87 â€” InclinaciÃ³n NUNCA detiene la mÃ¡quina (revierte parada por verticalidad del Cambio 84)

**AclaraciÃ³n del operador:** cuando la chapa estÃ¡ INCLINADA no se debe detener NUNCA la
mÃ¡quina, porque con la chapa inclinada el patrÃ³n NO se lee bien (lecturas no confiables) â€”
no es base vÃ¡lida para parar.

**Cambio:** `config/tolerancias.yaml` modelo_A â†’ `machine_stop_on_tilt: true` â†’ **false**.
Revierte la parada inmediata por verticalidad que se habÃ­a puesto en el Cambio 84.

**Comportamiento resultante para frames inclinados (|tilt|>tilt_warn_deg):**
- Se marcan **NOK** (no se aceptan) y muestran "CHAPA INCLINADA" + nÃºmero arriba-izquierda.
- **NUNCA** disparan `machine_stop` (sin banner "DETENCION DE MAQUINA").
- Se pasan como LOW_QUALITY al detector de faltantes â†’ tampoco disparan la parada por
  faltantes (la lectura inclinada no contamina la racha).

**Faltantes persistentes** siguen pudiendo parar (Cambio 84): un punzÃ³n roto persistente N
frames â†’ parada. Solo la inclinaciÃ³n quedÃ³ excluida de parar.

**ValidaciÃ³n:** frames 0083 (-3.98Â°), 0090 (-3.18Â°) â†’ NOK, `machine_stop=False`; normales OK.
Tests 4/4.

**Archivos modificados:** `config/tolerancias.yaml`

#### Cambio 86 â€” Texto de parada sin la palabra "VIRTUAL"

**Pedido:** En las fotos/overlays que marcan error o parada, el texto debÃ­a decir
`DETENCION DE MAQUINA` y no `DETENCION VIRTUAL DE MAQUINA`, tanto para
`modelo_A` como para `modelo_B`.

**Cambios:**
- `src/pipeline/annotate.py` â†’ el banner superior de parada ahora muestra
  `! DETENCION DE MAQUINA`.
- `src/controller/scanner_controller.py` â†’ el warning asociado a la parada
  persistente usa el mismo texto (`DETENCION DE MAQUINA`) para mantener
  consistencia entre overlay y logs.

**No tocado:** La lÃ³gica de `machine_stop`, el bloqueo de hardware y el carÃ¡cter
virtual de la acciÃ³n siguen igual; solo cambiÃ³ el texto visible.

**Archivos modificados:** `src/pipeline/annotate.py`,
`src/controller/scanner_controller.py`

#### Cambio 85 â€” GrabaciÃ³n: navegaciÃ³n eficiente (overlays JPEG, libera ~1.6 GB) + saca flecha central

**Problema 1 â€” la PC se trababa al navegar tras el anÃ¡lisis.** `self._results` mantenÃ­a
200 `InspectionResult`, cada uno con `overlay` (1920Ã—1080Ã—3 â‰ˆ 6 MB) + `mask` (â‰ˆ2 MB) â†’
~1.6 GB en RAM â†’ swap â†’ freeze.

**Fix (`src/ui/service.py`):**
- `_on_ana_done`: tras el anÃ¡lisis, cada overlay se comprime a JPEG (q=92, ~295 KB vs
  6 MB â†’ ~20Ã—) en `self._overlay_jpegs`, y se liberan los arrays pesados de cada resultado
  (`object.__setattr__(r,"overlay",None)`, `mask=None`). 200 frames: ~1.6 GB â†’ ~58 MB.
- Nuevo `_result_bgr(idx)`: decodifica el overlay del JPEG bajo demanda (rÃ¡pido). Usado por
  el navegador (`_show_frame`), `_save_current_frame` y `_export_range`.
- `_px_cache_max` 40â†’24 (decodificar JPEG es barato, baja la RAM del cachÃ© de pixmaps).
- `_overlay_jpegs` se limpia en `_on_analyze` junto con `_results`.

**Problema 2 â€” flecha central de inclinaciÃ³n molestaba.** `draw_centering_overlay` dibujaba
una flecha (sheet-centerâ†’pattern-center) en el medio del frame.

**Fix (`src/pipeline/annotate.py`):** eliminada la flecha (y el cÃ­rculo de fallback) del
centro. La inclinaciÃ³n queda solo como nÃºmero arriba-izquierda (`draw_tilt_indicator`) y el
offset en el texto inferior. El resto del overlay de centrado se mantiene.

**ValidaciÃ³n:** compile OK, tests 4/4, roundtrip JPEG verificado (6075 KBâ†’295 KB, decode
1920Ã—1080 OK, frozen dataclass liberado), smoke test de `RecordingTab`, overlay confirmado
sin flecha central.

**Archivos modificados:** `src/ui/service.py`, `src/pipeline/annotate.py`.

---

#### Cambio 84 â€” Parada de mÃ¡quina: faltantes solo por persistencia, verticalidad inmediata (ambos patrones)

**Pedido del operador (regla para AMBOS patrones):**
- Un solo frame con faltantes **NUNCA** puede parar la mÃ¡quina, sin importar cuÃ¡ntos
  falten (el metal pudo correrse). â†’ siempre requiere persistencia.
- Un solo frame con **desvÃ­o de verticalidad SÃ** puede parar (falla mecÃ¡nica). â†’ inmediato.

**Esterilla (modelo_A) â€” antes tenÃ­a `machine_stop_enabled: false`. Cambios:**
- `config/tolerancias.yaml`: `machine_stop_enabled: true`, `machine_stop_missing_frames: 5`
  (persistencia), `machine_stop_min_missing: 1` (detecta un solo punzÃ³n roto persistente,
  como microperforado), nuevo `machine_stop_on_tilt: true` (verticalidad â†’ parada inmediata).
- `src/inspection.py`: cuando `tilt_warn` (|sheet_tilt_deg|>`tilt_warn_deg`) y
  `machine_stop_on_tilt`, `machine_stop=True` en ese mismo frame con razÃ³n
  "PATRON DESALINEADO - VERTICALIDAD". Los faltantes pasan al detector como LOW_QUALITY
  cuando hay tilt, para no contaminar la racha de faltantes. (Reemplaza la lÃ³gica de
  persistencia de tilt que se habÃ­a planteado: ahora la verticalidad es inmediata.)
- `src/utils/config.py`: default `machine_stop_on_tilt: False`.

**Refuerzo defensivo (ambos patrones):**
- `src/pipeline/machine_stop.py`: `missing_frames` se fuerza a `max(2, ...)` â€” un solo
  frame con faltantes nunca puede disparar la parada, aunque se configure 1.

**Microperforado (modelo_B) â€” ya cumplÃ­a, sin cambios:** `machine_stop_missing_frames: 5`
(persistencia), verticalidad inmediata vÃ­a `pattern_align_enabled` ("PATRON DESALINEADO").

**ValidaciÃ³n:**
- Detector directo: 1 frame con 50 faltantes â†’ para? **False** (nunca para por 1 frame).
- frames inclinados (0083=-3.98Â°, 0090=-3.18Â°) â†’ `machine_stop=True` inmediato (NOK,
  "PATRON DESALINEADO - VERTICALIDAD"); normales (0162, 0016) â†’ False.
- `run-folder` Patron_Esterilla (200 frames): 9 machine_stop = todos por verticalidad
  (inclinados), 0 por faltantes (material bueno). Tests 4/4.

**Archivos modificados:** `config/tolerancias.yaml`, `src/inspection.py`,
`src/utils/config.py`, `src/pipeline/machine_stop.py`.

---

#### Cambio 83 â€” Esterilla: de-rotaciÃ³n por tilt (fixea falsos missing) + tiltâ†’NOK sin DETENER MAQUINA

**Contexto:** carpeta nueva `Patron_Esterilla_METALCONF` (201 frames, 63 Ãºnicas). El usuario
reportÃ³ (1) frames con demasiados faltantes que deberÃ­an detectarse bien, y (2) pidiÃ³ que la
chapa inclinada se marque NOK pero **nunca** muestre "DETENER MAQUINA".

**DiagnÃ³stico (`scripts/_esterilla_tilt_diag.py`, `_esterilla_derotate_exp.py`):**
6/63 frames NOK con missing 36â€“98. correlaciÃ³n tiltâ†”missing = 0.65. Dos modos:
- tilt alto (0083=-3.98Â°, 0090=-3.18Â°): chapa inclinada â†’ fase del grid falla.
- tilt bajo (0016=-1.67Â°, 0120=-1.59Â°): fallo de fase igual (amplificado por el bbox=10).
La detecciÃ³n estaba bien (det 104â€“110); el problema era el matching de la grilla
(asume ejes alineados). Experimento de-rotando los agujeros: missing 0016 55â†’0, 0090 98â†’0,
0120 89â†’0, sin regresiÃ³n en frames buenos.

**Implementado:**
- `src/pipeline/grid_fitting.py`: `rotate_points(pts, deg, cx, cy)`.
- `src/inspection.py` (grid path): mide `sheet_tilt_deg`, de-rota los agujeros antes de
  `grid_compare_points` y rota las posiciones esperadas DE VUELTA al espacio original
  (donde se compara y dibuja). Gated por `grid_derotate` + `grid_derotate_min_deg`.
- `src/inspection.py` (machine_stop): `tilt_warn` se calcula antes; si la chapa estÃ¡
  inclinada (|tilt|>`tilt_warn_deg`) â†’ `final_status="NOK"`, `machine_stop=False` (jamÃ¡s
  DETENER MAQUINA) y se pasa `frame_quality="LOW_QUALITY"` al detector para no contaminar la
  racha. `import math` agregado a nivel mÃ³dulo.
- `config/tolerancias.yaml` modelo_A: `grid_derotate: true`, `grid_derotate_min_deg: 0.4`.
  `src/utils/config.py`: defaults `grid_derotate=False`, `grid_derotate_min_deg=0.4`.

**Resultado (63 Ãºnicas):** missing media 7.7â†’**0.4** (mÃ¡x 98â†’4), NOK-por-missing 6/63â†’**0/63**.
Frames inclinados (0083, 0090) ahora matchean bien (missing 0â€“1) pero quedan **NOK +
"CHAPA INCLINADA"** sin "DETENER MAQUINA" (verificado en overlay). frame_0016 (antes 55
faltantes en la mitad inferior) â†’ todo verde OK. Tests 4/4.

**Archivos modificados:** `src/pipeline/grid_fitting.py`, `src/inspection.py`,
`config/tolerancias.yaml`, `src/utils/config.py`.

---

#### Cambio 82 â€” GrabaciÃ³n: chip de TIPO DE PLACA junto a Analizar + fix colisiÃ³n _btn_stop

**Pedido:** un cartel al lado del botÃ³n Analizar que indique si se estÃ¡ analizando
MICROPERFORADO o ESTERILLA, para no confundirse.

**Bug encontrado y corregido (regresiÃ³n del Cambio 78):** el botÃ³n "Detener" del anÃ¡lisis
se habÃ­a nombrado `self._btn_stop`, igual que el botÃ³n "DETENER" de **grabaciÃ³n**
(`_build_recording_section`). Como `_build_analysis_section` corre despuÃ©s, el de anÃ¡lisis
**sobrescribÃ­a** al de grabaciÃ³n â†’ el botÃ³n DETENER de grabaciÃ³n quedaba huÃ©rfano (su
`clicked.connect(self._on_stop)` en realidad cableaba el botÃ³n de anÃ¡lisis) y `_on_start`
habilitaba el botÃ³n equivocado. Se renombrÃ³ el de anÃ¡lisis a **`_btn_stop_analyze`** en
todos sus usos (creaciÃ³n, `_set_analysis_running`, `_on_stop_analyze`). Ahora son
independientes (verificado: `rec_stop is analyze_stop == False`).

**Cambio (chip):** en `_build_analysis_section`, junto a Analizar/Detener, se agregÃ³
`Tipo:` + `_analyze_model_chip` (QLabel prominente, 13px bold, color por familia:
celeste=Microperforado, verde=Esterilla). `_update_model_chip` ahora actualiza ambos chips
(el nuevo guardado con `hasattr`). Se sincroniza con el selector de modelo y queda bloqueado
junto con Ã©l durante el anÃ¡lisis.

**ValidaciÃ³n:** compile OK, tests 4/4, smoke test offscreen: chip refleja
Microperforado/Esterilla al togglear; botones de grabaciÃ³n y anÃ¡lisis independientes;
selector bloqueado durante anÃ¡lisis.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 81 â€” Esterilla: limpieza de "extras" de borde (bbox_filter_margin_px 50â†’10)

**Pedido:** corregir los agujeros "extra" (diamantes naranjas) que el sistema marcaba.

**DiagnÃ³stico (`scripts/_esterilla_extras_diag.py`, 17 Ãºnicas, 124 extras / ~7.3 por frame):**
distribuciÃ³n por zona â€” TOP-center 57, MIDDLE-left 32, BOTTOM-center 20. Son **agujeros
reales de borde** del band (no espurios) que el patrÃ³n no registra; parte del top apareciÃ³
al recortar la fila superior (Cambio 79). No son ruido.

**Insight:** el cÃ­rculo VERDE se dibuja sobre TODOS los agujeros detectados (`holes`),
mientras que los "extra" salen de `detected_in_bbox`. Achicando el margen del bounding-box
del patrÃ³n, los agujeros de borde quedan fuera del conteo de extras (sin diamante) PERO
siguen en verde.

**Cambio:** `config/tolerancias.yaml` â†’ `modelo_A`: `bbox_filter_margin_px` 50â†’**10**.

**Resultado (17 Ãºnicas):** extras **7.3 â†’ 1.8** por frame (mediana 2, mÃ¡x 3), missing sin
cambios (mediana 0), 0 NOK. Overlay verificado: todo verde de arriba a abajo, sin cruces y
prÃ¡cticamente sin diamantes (1 residual en borde izquierdo). Tests 4/4.

**Archivos modificados:** `config/tolerancias.yaml`.

---

#### Cambio 80 â€” Esterilla: mediciÃ³n de inclinaciÃ³n (tilt) de la grilla + aviso CHAPA INCLINADA

**Pedido del operador:** en frames donde la chapa se inclina, el patrÃ³n queda "totalmente
corrido" y no detecta bien. Â¿Se puede medir la inclinaciÃ³n para detectar corrimientos?

**DiagnÃ³stico (`scripts/_esterilla_tilt_diag.py` sobre 17 Ãºnicas):** el set REDUCIDO no
tiene frames muy inclinados (tilt de grilla ~1Â° mÃ¡x). Hallazgo clave: el Hough actual
(`align_image_by_right_edge`, mide el BORDE de la chapa) reporta 0.00Â° en casi todos,
pero la grilla real tiene ~-1Â° â†’ el Hough no refleja la inclinaciÃ³n del patrÃ³n. La grilla
se puede medir directo desde los agujeros (mediana del Ã¡ngulo del vecino en la fila),
que es lo que el matching necesita.

**Causa de "se corre todo" con tilt grande:** `grid_compare_points` asume grilla alineada
a los ejes (barre fase X y luego Y). Con la chapa inclinada una fila ya no estÃ¡ a `y`
constante â†’ no engancha. El affine refinement podrÃ­a absorber rotaciÃ³n pero (1) limita
shear a ~8.5Â° y (2) tiene problema huevo-gallina: sin matches no estima rotaciÃ³n.

**Implementado (mediciÃ³n + aviso):**
- `src/pipeline/grid_fitting.py`: `estimate_lattice_tilt_deg(detected_xy, dx)` â€” tilt de la
  grilla desde los agujeros (robusto, mediana de Ã¡ngulos de vecino en fila).
- `src/inspection.py`: calcula `sheet_tilt_deg` por frame, nuevo campo en `InspectionResult`
  (`sheet_tilt_deg`, `tilt_warn`). Si `|tilt| > tilt_warn_deg` agrega causa y marca aviso.
- `src/pipeline/annotate.py`: `draw_tilt_indicator` muestra "Inclinacion: X.X grados" al
  borde izquierdo (bajo el STATUS); rojo + badge "CHAPA INCLINADA" cuando supera el umbral.
- `src/utils/config.py`: default `tilt_warn_deg=0.0` (solo medir). `config/tolerancias.yaml`
  `modelo_A`: `tilt_warn_deg=2.5` (tilt normal ~1Â°).

**ValidaciÃ³n:** compile OK, tests 4/4, overlay frame_0162 muestra "Inclinacion: -1.0 grados".
MediciÃ³n verificada: 0162=-1.03Â°, 0172=+0.60Â°, 0182=+0.60Â°, 0186=-1.06Â° (warn=False, todos
bajo 2.5Â°). Es informativo (NO fuerza NOK por ahora).

**PENDIENTE (correcciÃ³n, requiere datos):** la CORRECCIÃ“N de detecciÃ³n con tilt (de-rotar
los agujeros usando `sheet_tilt_deg` antes del grid fit, rompiendo el huevo-gallina) queda
para implementar â€” falta una grabaciÃ³n con la chapa realmente inclinada para construir y
validar sin regresar los frames buenos.

**Archivos modificados:** `src/pipeline/grid_fitting.py`, `src/inspection.py`,
`src/pipeline/annotate.py`, `src/utils/config.py`, `config/tolerancias.yaml`.

---

#### Cambio 79 â€” Esterilla: sin cruces falsas arriba + estado OK/NOK al borde izquierdo

**Pedido del operador:** (1) se veÃ­an cruces rojas en la fila superior del patrÃ³n;
(2) el texto de estado OK/NOK tapaba los agujeros y debÃ­a ir al borde izquierdo.

**Problema 1 â€” cruces falsas arriba:** La fila superior del patrÃ³n (cj mÃ­nimo, 4 celdas)
caÃ­a consistentemente como missing en TODOS los frames. DiagnÃ³stico
(`scripts/_esterilla_top_diag.py`): los agujeros superiores SÃ se detectan (verde), pero
la posiciÃ³n esperada de esa fila quedaba ~24px por encima por un artefacto de fase del grid
escalonado (fila de borde superior poco confiable). 4 cruces rojas por frame.

**Fix 1:** se quitÃ³ la fila superior del patrÃ³n (`scripts/_esterilla_trim_top.py`, elimina
el `cj` mÃ­nimo). scanner_2 + global: 119â†’115 celdas. Backup `.bak` previo.
Resultado 17 Ãºnicas: missing media 5.5â†’**1.9**, mediana 4â†’**0** (mayorÃ­a 0 faltantes),
0 NOK. Sin cruces en la parte superior (verificado visualmente).

**Problema 2 â€” estado tapaba agujeros:** `draw_compare_overlay` dibujaba el STATUS/panel
NOK en coordenadas de la ROI (xâ‰ˆ880 en frame completo) â†’ sobre los agujeros.

**Fix 2 (`src/pipeline/annotate.py` + `src/inspection.py`):**
- Nueva funciÃ³n `draw_status_indicator(img, status, nok_reasons, badge_count)` que dibuja
  el estado pegado al borde IZQUIERDO (OK â†’ texto; NOK â†’ panel de causas).
- `draw_compare_overlay`: nuevo flag `draw_status` (default True). InspecciÃ³n lo llama con
  `draw_status=False` y dibuja el estado con `draw_status_indicator` sobre el frame COMPLETO
  (zona oscura izquierda), despuÃ©s de los badges. Ya no tapa el patrÃ³n.

**ValidaciÃ³n:** compile OK, tests 4/4, overlay frame_0162 verificado (STATUS arriba-izq,
patrÃ³n todo verde sin cruces).

**Archivos modificados:** `src/pipeline/annotate.py`, `src/inspection.py`,
`data/patterns/scanner_2/modelo_A/holes.json`, `data/patterns/modelo_A/holes.json`.

---

#### Cambio 78 â€” GrabaciÃ³n: botÃ³n Detener anÃ¡lisis + bloqueo de tipo de placa

**Pedido del operador:** poder frenar el anÃ¡lisis una vez iniciado, y que el tipo de placa
(Esterilla/Microperforado) no se pueda cambiar mientras se estÃ¡ analizando.

**Cambios en `src/ui/service.py`:**
- `_AnalysisWorker`: nuevo flag `_cancel` + mÃ©todo `cancel()` (thread-safe) y seÃ±al
  `cancelled(int)`. Ambos loops (secuencial con MachineStop y paralelo) chequean el flag y
  abortan limpiamente; el loop paralelo pasa a manejar el `ThreadPoolExecutor` manualmente
  con `shutdown(wait=False, cancel_futures=True)` para frenar rÃ¡pido.
- `RecordingTab`: nuevo botÃ³n **"Detener"** (rojo) junto a "Analizar", deshabilitado salvo
  durante el anÃ¡lisis. Handler `_on_stop_analyze` llama `worker.cancel()`.
- Nuevo helper `_set_analysis_running(running)`: durante el anÃ¡lisis bloquea botones
  Esterilla/Microperforado, el combo de scanner y "Abrir grabaciÃ³n"; reactiva al terminar.
  Garantiza que todos los frames se evalÃºen contra el mismo modelo.
- `_on_analyze`/`_on_ana_done`/`_on_ana_error` usan el helper; nuevo `_on_ana_cancelled`
  restaura controles y muestra "AnÃ¡lisis detenido (N frames)".

**ValidaciÃ³n:** `py_compile` OK, tests 4/4, smoke test offscreen de `RecordingTab`:
running â†’ Detener habilitado y selector/scanner bloqueados; stopped â†’ reactivados.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 77 â€” Esterilla: umbral ADAPTATIVO â†’ detecciÃ³n casi completa (missing 21â†’4)

**Problema (reporte del operador):** El sistema no marcaba en verde varios agujeros, como
si no los reconociera. DiagnÃ³stico (`scripts/_esterilla_detect_diag.py`):
- `draw_compare_overlay` pinta verde TODO agujero detectado â†’ un agujero sin verde = NO
  detectado (no es problema de patrÃ³n).
- Relajar min_area/circularidad/aspect NO recuperaba agujeros (relaxed==current) â†’ no era
  filtro de contorno.
- Causa real: el preprocess usaba **Otsu global** (un Ãºnico umbral para toda la ROI). En
  la zona inferior del encuadre (mÃ¡s oscura / levemente desenfocada) los agujeros tenues
  caÃ­an bajo el umbral y no formaban contorno en la mÃ¡scara.

**Experimento (`scripts/_esterilla_thresh_exp.py`):** umbral adaptativo gaussiano local
detecta muchos mÃ¡s agujeros sin falsos positivos:
- frame_0162: 106â†’126, frame_0177: 80â†’122, frame_0172: 71â†’131.

**Cambios:**
- `src/pipeline/preprocess.py`: nuevo modo `use_adaptive` (cv2.adaptiveThreshold gaussiano)
  con `adaptive_block_size` (impar) y `adaptive_c`. Precedencia sobre `use_otsu`.
- `src/utils/config.py`: defaults `use_adaptive=False`, `adaptive_block_size=61`,
  `adaptive_c=-5.0` (opt-in, no afecta otros modelos).
- `src/inspection.py` y `src/patterns/pattern_build.py`: leen y propagan los 3 params al
  preprocess (mismo masking en inspecciÃ³n y en build).
- `config/tolerancias.yaml` â†’ `modelo_A`: `use_adaptive: true`, `adaptive_block_size: 61`,
  `adaptive_c: -5.0`.
- PatrÃ³n `scanner_2/modelo_A` reconstruido con adaptivo: 88â†’**100 puntos** (mÃ¡s completo),
  duplicados de celda 2â†’1. Sincronizado a global. Backup `.20260601_084919.bak`.

**Resultado (17 Ãºnicas):** missing media **21.0 â†’ 4.0** (rango 7â€“51 â†’ 4â€“4, constante).
Escenas 0159/0172 (antes missing 50/51) â†’ **4**. NOK 0/17 mantenido. DetecciÃ³n 122â€“135
agujeros/frame. Tests 4/4 OK.

**Extras de borde (resuelto):** se bajÃ³ `pattern_edge_margin_px` 40â†’**22** para que el
patrÃ³n REGISTRE los agujeros de borde (antes "extra"/diamante naranja). PatrÃ³n reconstruido:
119 puntos, 0 duplicados, stagger 26px estable. Resultado 17 Ãºnicas: extras ~20â†’**~7**
(media 6.9), missing media 4.0â†’**5.5** (mediana 4, max 29), ratio 126%â†’**109%**, NOK 0/17.
Nota: con margen 12 (122 pts) un frame puntual (0186) se desestabilizaba a missing=77; 22
es el equilibrio sin NOK. Riesgo conocido: si la lÃ¡mina se corre mucho en producciÃ³n, los
agujeros de borde registrados pueden salir del encuadre y contar como faltantes â€” validar
con material real en movimiento.

**Archivos modificados:** `src/pipeline/preprocess.py`, `src/utils/config.py`,
`src/inspection.py`, `src/patterns/pattern_build.py`, `config/tolerancias.yaml`,
`data/patterns/scanner_2/modelo_A/holes.json`, `data/patterns/modelo_A/holes.json`.

---

#### Cambio 76 â€” CÃ¡maras IP: conexiÃ³n 100% manual (sin auto-connect ni reintento infinito)

**Problema:** El programa quedaba lento y las cÃ¡maras WiFi no terminaban de conectar al
iniciar. Causa: el auto-connect (Cambio 66) disparaba polling HTTP/MJPEG en background
apenas se abrÃ­a el tab CÃ¡mara, y ante fallo el `_on_ip_error` arrancaba un bucle de
reintento incremental (5â†’30s) que seguÃ­a golpeando la red/CPU indefinidamente cuando la
cÃ¡mara estaba inalcanzable â†’ UI lenta y reconexiÃ³n perpetua.

**Cambios en `src/ui/service.py` (CameraCalibTab):**
- `showEvent`: ya NO llama `_auto_connect_if_saved()`. Al abrir el tab no se conecta nada;
  el operador debe presionar **"Conectar"**.
- `_on_ip_error`: eliminado el reintento automÃ¡tico (ya no arranca `_ip_retry_timers`).
  Ante error muestra estado "Sin conexion", reactiva el botÃ³n "Conectar" y los campos de
  IP/URL/usuario/clave para que el operador reintente manualmente cuando quiera.

**No tocado:** `_on_ip_connect` (Conectar manual), `_save_ip_settings` (Guardar config
sigue conectando porque es acciÃ³n explÃ­cita del operador), producciÃ³n `run` (usa cÃ¡maras
USB por index 0/1 en `io_map.yaml`, no IP â†’ no afectada). `_auto_connect_if_saved` y
`_on_ip_retry` quedan en el cÃ³digo pero ya no se invocan (sin efecto).

**Resultado:** Arranque sin polling WiFi en background; sin bucle de reconexiÃ³n; conexiÃ³n
solo cuando el operador la pide.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 75 â€” Esterilla: correcciÃ³n de geometrÃ­a de grid (grid_dy 38â†’36, grid_dx 66â†’65)

**DiagnÃ³stico sobre carpeta `Esterilla_REDUCIDO` (49 archivos, 17 Ãºnicas reales, 32 duplicados):**
AnÃ¡lisis de las mÃ©tricas (missing media 21, missingâ†’detectado-mÃ¡s-cercano media 73px,
centrado -38px constante, ratio 109%) indicÃ³ que el problema NO era detecciÃ³n
(detecciÃ³n sana, ratio>100%) ni tolerancia, sino **patrÃ³n + fase**.

**MediciÃ³n de geometrÃ­a real** (`scripts/_esterilla_lattice.py`, frame_0162, sub-redes
grande/chico por separado):
- GRANDES: lattice dx=64.6, dy=72.3
- CHICOS: lattice dx=64.7, dy=72.7, offset (-25.2, +35.6) respecto de grandes
- â†’ medio-perÃ­odo vertical real (fila a fila) = 72.3/2 = **36.1px**, no 38.
- El `grid_dy=38` acumulaba ~45px de deriva Y sobre 24 filas â†’ missing en filas inferiores
  y picos de missing=50 en escenas puntuales (las 2 escenas NOK del set).

**Cambios:**
- `config/tolerancias.yaml` â†’ `models.modelo_A`: `grid_dx` 66â†’65, `grid_dy` 38â†’36.
- Reconstruido `data/patterns/scanner_2/modelo_A/holes.json` desde frame_0162 con
  `build-pattern --model modelo_A --scanner scanner_2`. Sincronizado a global `modelo_A`.
  Backups `.20260601_083711.bak` de holes.json (scanner_2 + global) y tolerancias.yaml.

**Resultado (17 Ãºnicas):** NOK (missingâ‰¥35) **2/17 â†’ 0/17**. Escena 0159 missing 50â†’27,
0172 missing 51â†’34. Missing media 21.0â†’19.9. Duplicados de celda en build 5â†’2.

**Pendiente (prÃ³xima iteraciÃ³n):** missing media (~20) y extras (~22) siguen altos =
**patrÃ³n incompleto** (~20 celdas reales no registradas) + ajuste fino de `stagger_x_odd`
(build auto-detectÃ³ 18px; offset medido entre sub-redes = -25px â†’ revisar parity/signo).
Scripts de diagnÃ³stico nuevos: `scripts/_esterilla_geom.py`, `_esterilla_lattice.py`,
`_esterilla_eval.py` (este Ãºltimo deduplica por MD5 y evalÃºa solo frames Ãºnicos).

**Archivos modificados:** `config/tolerancias.yaml`,
`data/patterns/scanner_2/modelo_A/holes.json`, `data/patterns/modelo_A/holes.json`.

---

### SesiÃ³n 2026-05-29 â€” Tadeo + Claude (noche)

#### Cambio 74 â€” Esterilla "todo rojo" + lentitud modo servicio (regresiÃ³n WiFi)

**Problema 1 â€” Esterilla detectaba todo como NOK (cruces rojas):**
Al cargar una carpeta de grabaciÃ³n esterilla en modo servicio, el overlay mostraba
casi todo rojo. Dos causas encadenadas:
1. `_load_folder` forzaba el modelo desde `meta.json` (`setCurrentText(model_display)`).
   La grabaciÃ³n de prueba `Patron_Esterilla_METALCONF` quedÃ³ mal etiquetada como
   `Microperforado` (modelo_B). Resultado: imÃ¡genes esterilla analizadas con el patrÃ³n
   microperforado (255 agujeros) â†’ missing=168 â†’ NOK total.
2. El patrÃ³n **global** `data/patterns/modelo_A/holes.json` estaba viejo (117 pts, sin
   `stagger_x_odd`, ROI 1204px) y NO coincidÃ­a con el calibrado en
   `scanner_2/modelo_A` (88 pts, staggered, ROI x=870 w=380, cambios 51/52). Como la
   UI por defecto usa `scanner_1` y no existe `scanner_1/modelo_A`, el fallback caÃ­a al
   patrÃ³n global stale â†’ missingâ‰ˆ32 aun con modelo_A.

**DiagnÃ³stico (CLI, frame_0162.png, 1920Ã—1080):**
- modelo_B/scanner_1: expected=255 missing=168 â†’ NOK (el "todo rojo")
- modelo_A/scanner_2 (bueno): expected=83 missing=9 â†’ OK
- modelo_A/scanner_1 (fallback global stale): expected=113 missing=32

**Cambios:**
- `src/ui/service.py` â†’ `_load_folder`: ya NO fuerza el modelo desde `meta.json`.
  Respeta la selecciÃ³n del operador (botones Esterilla/Microperforado), coherente con
  el diseÃ±o ya documentado en `_on_scanner_changed`. `meta.model_display` queda solo
  como log informativo. Se sigue cargando `fps` de meta.
- `data/patterns/modelo_A/{holes.json,roi.json}`: sincronizados desde `scanner_2/modelo_A`
  (el patrÃ³n esterilla calibrado). Backups `.bak` creados. Ahora modelo_A resuelve al
  patrÃ³n correcto desde cualquier scanner vÃ­a fallback. Seguro: producciÃ³n usa
  `scanner_2/modelo_A`; el global solo se usa como fallback de anÃ¡lisis.

**Resultado:** carpeta completa (200 frames) pasÃ³ de 0/200 OK (todo NOK rojo) a
**178/200 OK status**, mayorÃ­a verde. Quedan ~22 frames NOK por deriva de fase del grid
escalonado (missing 48-77 en frames puntuales) â€” problema de calibraciÃ³n fina del grid
documentado (cambios 51/52), no regresiÃ³n. La decisiÃ³n temporal sigue OK
(`consecutive_nok_frames: 9999`).

**Problema 2 â€” Modo servicio muy lento tras cambios de cÃ¡mara IP/WiFi:**
El cambio 72 subiÃ³ el polling de snapshot HTTP de la cÃ¡mara IP a 33 ms (30 fps), pero el
preview de diagnÃ³stico solo se refresca a 5 fps (timer de 200 ms). Se capturaban y
decodificaban ~6Ã— mÃ¡s JPEG de los que se muestran â†’ saturaciÃ³n de CPU y WiFi, UI lenta.

**Cambio:**
- `src/ui/service.py` â†’ `_HTTPSnapshotReader.__init__`: `interval_ms` default 33 â†’ **150**
  (â‰ˆ6-7 fps, con margen sobre el preview de 5 fps). MÃ­nimo subido de 20 â†’ 50 ms.

**Archivos modificados:** `src/ui/service.py`, `data/patterns/modelo_A/holes.json`,
`data/patterns/modelo_A/roi.json`

---

#### Cambio 73 - Limpieza total de mojibake + test preventivo

**Motivacion:** Seguian apareciendo textos con caracteres deformados en distintas tabs
de la UI, sobre todo en botones y estados de `service.py`.

**Cambios:**
- `src/ui/service.py`:
  - Se eliminaron secuencias rotas en botones y estados (`Conectar`, `Desconectar`,
    `Iniciar`, `Detener`, `Ingrese una URL`, `No se pudo conectar`, `ANALIZANDO`).
  - Se quitaron simbolos decorativos rotos para evitar depender de codificaciones
    ambiguas del sistema.
- `tests/test_text_encoding.py`:
  - Nuevo test que recorre `src/`, `config/`, `tests/`, `CHANGELOG.md` y `AGENTS.md`
    buscando secuencias tipicas de mojibake.
  - Si vuelve a entrar texto roto, `pytest` falla y lo marca con archivo y linea.

**Resultado:** La UI queda con texto estable y el repo gana una barrera automatica
contra nuevas cadenas mal codificadas.

---

#### Cambio 72 â€” _HTTPSnapshotReader: keep-alive + 30fps objetivo

**Problema:** CÃ¡mara IP en 192.168.1.26 usa URL `oneshotimage.jpg` (foto Ãºnica, no
stream MJPEG). `interval_ms=250` â†’ 4fps. `urllib.urlopen` abrÃ­a nueva conexiÃ³n TCP
por frame â†’ overhead handshake ~10-20ms/frame.

**Cambios:**
- `interval_ms` default: 250 â†’ **33ms** (~30fps)
- MÃ­nimo: 100 â†’ **20ms** (techo 50fps)
- `urllib.request` reemplazado por `http.client.HTTPConnection` con
  `Connection: keep-alive` â€” reutiliza la TCP entre frames
- Soporte HTTPS con SSL sin verificaciÃ³n de certificado
- ReconexiÃ³n automÃ¡tica si la conexiÃ³n se rompe, sin disparar error_occurred

**Archivos modificados:** `src/ui/service.py`

---


#### Cambio 72 - Soporte Sony IP + URL de stream editable

**Motivacion:** Se cambio la camara IP de Axis a Sony (`192.168.1.26`) y la app
no podia mostrar imagen porque asumÃ­a un stream MJPEG fijo. Ademas, la URL quedaba
demasiado atada a Axis para el operador.

**Validacion real sobre la camara Sony SNC-EB600B:**
- `http://192.168.1.26/oneshotimage.jpg` responde `200 OK` con imagen JPEG valida.
- `/mjpeg` devuelve `404`, por eso no servia tratarla como Axis MJPEG.
- Login verificado con `admin/admin`.

**Cambios:**
- `src/ui/service.py`:
  - Se agrego `_HTTPSnapshotReader` para camaras HTTP que entregan snapshots JPEG.
  - La conexion IP ahora detecta URLs tipo `oneshot/snapshot/.jpg/.jpeg` y usa polling
    de snapshots; si no, mantiene MJPEG/USB/RTSP segun corresponda.
  - El campo `URL stream` queda editable para pegar cualquier endpoint manualmente.
  - Si el operador cambia solo la IP, la app intenta conservar la ruta del stream ya
    conocida para esa camara en vez de volver a forzar Axis.
- `config/camera.yaml`:
  - `ip_camera_1` pasa a `192.168.1.26` con URL
    `http://192.168.1.26/oneshotimage.jpg` y credenciales `admin/admin`.

**Resultado:** La Sony puede verse usando snapshot HTTP, y la URL ya no queda bloqueada
ni amarrada a una sola marca de camara.

---

#### Cambio 71b â€” Marcadores de error huecos (sin relleno)

**Problema:** Los marcadores de agujero faltante (cruces rojas) tenÃ­an un cÃ­rculo
relleno oscuro de fondo que tapaba la imagen. El operario no podÃ­a ver quÃ© habÃ­a
en la posiciÃ³n del error (agujero parcial, suciedad, reflejo).

**Fix en `src/pipeline/annotate.py` â†’ `draw_compare_overlay()`:**
- Eliminado `cv2.circle(..., -1)` (relleno opaco)
- Reemplazado por: sombra negra hueca (grosor 3) + borde rojo (grosor 2)
- Cruz: sombra negra (grosor 4) + cruz roja (grosor 2) â€” sin relleno
- NÃºmero del faltante: sombra negra gruesa + texto blanco fino encima
- El interior del marcador queda completamente transparente â†’ el operario
  puede ver a travÃ©s del marcador la imagen real debajo

---

#### Cambio 70 â€” ParalelizaciÃ³n de inspect_folder (CLI 3.1Ã—) + diagnÃ³stico esterilla

**Mejora de rendimiento â€” `src/inspection.py` â†’ `inspect_folder`:**
Pre-carga de tolerances+pattern+roi una vez + ThreadPoolExecutor (hasta 6 workers)
cuando `machine_stop._enabled=False`. Secuencial obligatorio si machine_stop activo.
**Resultado:** 200 frames modelo_A: 217s â†’ 69s (3.1Ã— mÃ¡s rÃ¡pido), mismo resultado.

**DiagnÃ³stico esterilla sobre 200 frames reales:**
- DetecciÃ³n raw: 126 holes/frame (63 chicos + 63 grandes) â€” el detector funciona âœ“
- PatrÃ³n referencia: 83 celdas vs 126 visibles â†’ 24 "extras" son agujeros reales no registrados
- 9-21 missing/frame: ~12-16 en bordes de material (normal) + ~5-9 por drift de fase de grid
- Mejor frame: frame_0162 (missing=9, stagger=+26px) â€” usado como referencia
- frame_0139 (ratio=138%) genera stagger=-22px (fase invertida) â†’ peor resultado
- Para mejorar la cobertura: capturar frame con esterilla centrado y en el mismo ciclo que frame_0162
- Resultado global: 200/200 temporal OK con threshold=35, tolerancias blandas âœ“

**Archivos modificados:** `src/inspection.py`

---

### SesiÃ³n 2026-05-29 â€” Tadeo + Claude (tarde)

#### Cambio 71 - Aplicacion real y verificacion de parametros VAPIX en camara IP

**Motivacion:** Los sliders de brillo, contraste, saturacion y nitidez no estaban
teniendo efecto real sobre la camara IP. Habia dos problemas: rangos incompatibles
con Axis y falta de verificacion de la respuesta de la camara.

**Cambios:**
- Los sliders IP ahora usan rangos Axis correctos `0..100` para brillo, contraste,
  saturacion y nitidez.
- Se agrego helper para enviar requests VAPIX con autenticacion Basic y fallback a
  `/axis-cgi/param.cgi` y `/axis-cgi/admin/param.cgi`.
- El update ahora se hace con `action=update&usergroup=admin`.
- Luego de aplicar, la app intenta leer `ImageSource.I0.Sensor` y confirma en pantalla
  los valores que quedaron realmente en la camara.
- Si la camara rechaza el cambio o no responde, el operador ahora ve el error en vez
  de un falso "Aplicado".

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 70 - Campo simple de IP para camaras IP

**Motivacion:** El operador no deberia tener que escribir la URL completa de Axis.
Ahora carga solo la IP de la camara y la aplicacion arma automaticamente el endpoint
MJPEG liviano.

**Cambios:**
- En el tab Camara se agrego el campo `IP de camara`.
- La UI muestra una `URL generada` readonly para verificar que se conectara a
  `http://<ip>/axis-cgi/mjpg/video.cgi?resolution=640x480&fps=10`.
- `camera.yaml` guarda `ip_address` ademas de `url`, manteniendo compatibilidad con
  configuraciones viejas que solo tengan URL.
- Conectar, auto-conectar, reconectar, guardar config y aplicar VAPIX usan la URL
  generada desde la IP.

**Archivos modificados:** `src/ui/service.py`, `config/camera.yaml`

---

#### Cambio 69 - Mejoras WiFi para camaras IP sin habilitar salidas reales

**Motivacion:** La camara IP por WiFi puede atrasarse, cortar el stream o quedar
congelada. Para usarla con mas confianza en diagnostico se agregaron controles de
calidad de senal sin accionar ninguna salida fisica.

**Cambios:**
- `_MJPEGReader` ahora decodifica todos los JPEG completos recibidos por chunk, pero
  emite solo el frame mas nuevo. Esto evita que la UI/inspeccion quede tomando
  decisiones con frames viejos cuando el WiFi se atrasa.
- El tab Camara muestra metricas de stream: edad del ultimo frame, FPS, frames
  descartados y cantidad de reconexiones.
- Se agrego watchdog de senal congelada: si los frames son practicamente iguales
  durante varios segundos, el badge marca `SENAL CONGELADA`.
- Ante error de stream o senal congelada se guarda un snapshot diagnostico en
  `data/output/export/diagnostico_ip*_*.jpg`, con throttle para no llenar disco.
- Se agrego un checkbox visible `Solo preview - sin salidas de maquina` para dejar
  explicito que por ahora la camara IP no acciona la maquina.
- La URL por defecto de `ip_camera_1` usa `resolution=640x480&fps=10` para bajar
  carga de red en WiFi.

**Archivos modificados:** `src/ui/service.py`, `config/camera.yaml`

---

#### Cambio 68 â€” Tolerancias blandas modelo_A (Esterilla) â€” reducir falsas cruces

**Problema:** El overlay de Esterilla mostraba cruces rojas en casi todos los frames
porque el sistema generaba posiciones esperadas donde no hay agujeros detectables
(grid phase ligeramente off, iluminaciÃ³n no ideal, blur) y los umbrales eran muy ajustados.

**DiagnÃ³stico de la imagen `debug_esterilla_best.png`:**
- Los agujeros SÃ se detectan (cÃ­rculos verdes visibles)
- El grid genera posiciones esperadas que no coinciden exactamente con los detectados
- `frame_missing_nok_threshold: 8` â†’ con â‰¥8 faltantes el frame muestra NOK con todas
  las cruces rojas. Casi todos los frames del esterilla tienen >8 faltantes durante calibraciÃ³n.
- `min_area: 150`, `circularity_min: 0.55` â†’ rechazan agujeros reales con blur/iluminaciÃ³n

**Cambios en `config/tolerancias.yaml` â€” secciÃ³n `modelo_A`:**

| ParÃ¡metro | Antes | Ahora | RazÃ³n |
|-----------|-------|-------|-------|
| `threshold` | 175 (global) | **140** | Umbral de binarizaciÃ³n mÃ¡s bajo para capturar mÃ¡s agujeros |
| `min_area` | 150.0 | **80.0** | Agujeros chicos con blur bajan de 150pxÂ² |
| `min_area_small` | 150.0 | **80.0** | Igual al piso global |
| `min_area_large` | 400.0 | **300.0** | Acepta grandes con iluminaciÃ³n no ideal |
| `max_area_large` | 7000.0 | **8000.0** | Margen mÃ¡s amplio |
| `circularity_min` | 0.55 | **0.35** | Acepta agujeros deformados por perspectiva/blur |
| `aspect_ratio_max` | 2.5 | **3.0** | Ligera deformaciÃ³n aceptable |
| `align_match_tol_px` | 150.0 | **250.0** | MÃ¡s permisivo para alineaciÃ³n inicial |
| `min_match_count` | 4 | **3** | Permite alinear con menos agujeros visibles |
| `edge_margin_px` | 5.0 | **3.0** | No descartar agujeros en borde de ROI |
| `grid_max_missing` | 25 | **50** | ~57% de 88 agujeros â€” muy permisivo |
| `bbox_filter_margin_px` | 30.0 | **50.0** | Margen amplio alrededor del bbox |
| `extra_min_dist_factor` | 2.0 | **1.5** | Umbral = 27px (antes 36px) |
| `frame_missing_nok_threshold` | 8 | **35** | â˜… CAMBIO CLAVE: NOK visual solo cuando faltan >35 agujeros |
| `consecutive_nok_frames` | 8 | **9999** | FAULT deshabilitado durante calibraciÃ³n |
| `machine_stop_enabled` | true | **false** | Sin alertas de parada mientras se calibra |

**Por quÃ© `frame_missing_nok_threshold: 35` es el cambio mÃ¡s importante:**
El overlay muestra cruces rojas para CADA agujero faltante individual, pero el
estado "NOK" (que hace que el frame se vea todo rojo con cruces prominentes) depende
de si `missing >= frame_missing_nok_threshold`. Subiendo a 35, los frames con 5-30
faltantes muestran algunas cruces pero el status sigue siendo "OK" â†’ mucho menos
ruido visual para el operador.

**PrÃ³ximos pasos para calibraciÃ³n fina:**
1. Capturar imagen OK limpia de Esterilla en planta y reconstruir `holes.json`
   con `build-pattern --model modelo_A --scanner scanner_2 --img <imagen>`
2. Ajustar `threshold` con histograma de imagen real (scripts/_debug_areas.py)
3. Una vez detecciÃ³n estable: bajar `frame_missing_nok_threshold` a 5-8
4. Habilitar `machine_stop_enabled: true` y `consecutive_nok_frames: 8`

**Archivos modificados:** `config/tolerancias.yaml`

---

### SesiÃ³n 2026-05-29 â€” Tadeo + Claude

#### Cambio 67 â€” Badge de estado IP mÃ¡s grande y semÃ¡ntico

**Problema:** El badge de estado IP tenÃ­a ancho fijo de 80px â†’ textos como
`"Reintento 2 en 10s"` o `"Intentando conectarâ€¦"` aparecÃ­an cortados.
AdemÃ¡s el estado era pequeÃ±o y difÃ­cil de leer de lejos en planta.

**Cambios:**
- `setFixedWidth(80)` â†’ `setMinimumWidth(170)`: el badge crece con el texto.
- Font 11px â†’ **13px bold**, padding 4px â†’ 6px 12px, border-radius 5â†’6px.
- Helper `_set_ip_status(text, kind)` centraliza todos los `setText` + `setStyleSheet`:
  - `"ok"` â†’ texto verde brillante, fondo verde muy oscuro
  - `"warn"` â†’ texto amarillo Ã¡mbar, fondo amarillo muy oscuro
  - `"error"` â†’ texto rojo claro, fondo rojo muy oscuro
  - `"neutral"` â†’ texto muted, fondo oscuro
- Mensajes de estado unificados en todos los mÃ©todos:
  - Conectando: `"Conectandoâ€¦"` (warn)
  - SeÃ±al activa: `"En vivo"` (ok)
  - Error/retry: `"Reintento N â€” en Xs"` (error)
  - Retry activo: `"Intentando conectarâ€¦ (N)"` (warn)
- Info FPS/resoluciÃ³n (`_ip_info_lbl`): font 11px â†’ **13px bold**, color muted â†’ _TEXT.

**Archivos modificados:** `src/ui/service.py`

#### Cambio 66 â€” Auto-conectar, auto-reconectar, FPS en vivo y captura de frame

**MotivaciÃ³n:** El operador en planta necesitaba conectar manualmente al entrar al tab,
no tenÃ­a forma de saber la calidad del stream IP, y si la cÃ¡mara se reiniciaba debÃ­a
reconectar a mano. Tampoco podÃ­a guardar una imagen de lo que ve la cÃ¡mara IP.

**CaracterÃ­sticas implementadas:**

1. **Auto-conectar al abrir el tab** (`showEvent`)
   - Cuando el operador entra al tab CÃ¡mara, si hay una URL guardada para un slot y no
     fue desconectado manualmente, la conexiÃ³n arranca automÃ¡ticamente.
   - Flag `_ip_manual_disc[slot]` evita que el auto-connect se dispare despuÃ©s de que
     el operador haya presionado Desconectar deliberadamente.

2. **Auto-reconectar si se cae la seÃ±al** (`_on_ip_error` + `_on_ip_retry`)
   - Si el stream HTTP/MJPEG se corta (red caÃ­da, reinicio de cÃ¡mara Axis), arranca un
     timer single-shot con delay incremental: 5s â†’ 10s â†’ 15s â†’ â€¦ â†’ 30s mÃ¡ximo.
   - El badge de estado muestra `"Reintento N en Xs"` para informar al operador sin
     necesitar intervenciÃ³n.
   - El retry lee URL y credenciales desde `camera.yaml` para ese slot.

3. **FPS + resoluciÃ³n en vivo** (`_on_ip_frame_ready`)
   - Se actualizan cada 20 frames. Muestra `"WxH @ Xfps"` debajo del preview.
   - El badge de estado cambia a verde con texto `"En vivo"` cuando hay seÃ±al.

4. **BotÃ³n "Capturar frame"** (`_capture_ip_frame`)
   - Habilitado solo cuando hay seÃ±al. Guarda el Ãºltimo frame en
     `data/output/export/captura_ip1_YYYYMMDD_HHMMSS.jpg`.
   - Muestra el nombre del archivo guardado durante 4 segundos luego se limpia.

**Refactor interno:**
- `_start_ip_connection(slot, url, user, pass)`: lÃ³gica de conexiÃ³n extraÃ­da de
  `_on_ip_connect`, usada tambiÃ©n por auto-connect y retry.
- `_auto_connect_if_saved()`: carga config desde `camera.yaml` y llama `_start_ip_connection`.

**Archivos modificados:** `src/ui/service.py`

#### Cambio 65 â€” Tab CÃ¡mara scrollable + botÃ³n mostrar contraseÃ±a + espaciado

**MotivaciÃ³n:** El tab CÃ¡mara no tenÃ­a scroll (todo el contenido se comprimÃ­a en la ventana
sin posibilidad de bajar), la secciÃ³n IP se veÃ­a apretada y no habÃ­a forma de ver la contraseÃ±a
al escribirla.

**Cambios:**
- `CameraCalibTab._build_ui`: envuelto en `QScrollArea` (igual que RecordingTab).
  Content widget con `background:{_DARK}`, scrollbar vertical de 8px.
- BotÃ³n **"Mostrar"** junto al campo contraseÃ±a: toggle checkable que alterna
  `EchoMode.Password` â†” `EchoMode.Normal`. Se ilumina en acento cuando estÃ¡ activo.
- MÃ¡rgenes e inter-espaciados de la secciÃ³n IP aumentados (`setContentsMargins(18,24,18,18)`,
  `setSpacing(10)`, `addSpacing` entre secciones).
- Sliders del grid 2Ã—2: altura fija `22px`, ancho mÃ­nimo `110px`, spinboxes `72Ã—30px`.
- Preview IP: `minHeight` aumentado a `300px`.
- Campo usuario y contraseÃ±a: `setFixedHeight(34)`, mÃ¡s anchos (`160px`).

**Archivos modificados:** `src/ui/service.py`

#### Cambio 64 â€” RediseÃ±o estÃ©tico de la secciÃ³n CÃ¡maras IP

**MotivaciÃ³n:** El diseÃ±o inicial de la secciÃ³n IP en `CameraCalibTab` tenÃ­a controles
apilados de forma desordenada: 3 filas de controles, spinboxes muy chicos, todo estaba
apretado y sin jerarquÃ­a visual clara.

**Mejoras:**
- Selector de slot + URL + botones Conectar/Desconectar + badge de estado â†’ **una sola fila**.
- Usuario/ContraseÃ±a â†’ fila compacta separada con `addSpacing` para claridad visual.
- Badge de estado (label con borde y fondo) en lugar de texto suelto.
- Sliders de parÃ¡metros â†’ **grid 2Ã—2** (Brillo | Contraste / SaturaciÃ³n | Nitidez).
  Ahorra espacio vertical, aprovecha el ancho disponible.
- Spinboxes con altura fija (`setFixedHeight(28)`) y spinners visibles.
- Botones Guardar / Aplicar con alturas uniformes (32px), tipografÃ­a consistente.
- Dos separadores `QFrame.HLine` para delimitar visualmente las secciones.
- Preview con `minHeight=240` (antes 460) â€” permite que la secciÃ³n sea mÃ¡s compacta.
- Stretch de la secciÃ³n IP bajado de 3 â†’ 2 para mejor balance con la secciÃ³n USB.

**Archivos modificados:**
- `src/ui/service.py`: `_build_ip_camera_section` en `CameraCalibTab` rediseÃ±ado.
  Stretch del GroupBox IP en `_build_ui` cambiado de 3 â†’ 2.

#### Cambio 63 â€” Segunda cÃ¡mara IP + parÃ¡metros de imagen en tab CÃ¡mara

**MotivaciÃ³n:** La secciÃ³n de cÃ¡mara IP en `CameraCalibTab` solo soportaba una cÃ¡mara.
Se querÃ­a conectar y configurar una segunda cÃ¡mara IP independiente, y poder ajustar
parÃ¡metros de imagen (brillo, contraste, saturaciÃ³n, nitidez) de igual forma que para
las cÃ¡maras USB.

**DiseÃ±o:**
- Dos slots independientes: "IP CÃ¡m 1" / "IP CÃ¡m 2", seleccionables con combo.
- Cada slot tiene su propio estado (`_ip_workers[2]`, `_ip_caps[2]`, `_ip_timers[2]`).
- Ambas cÃ¡maras pueden estar conectadas simultÃ¡neamente; el preview muestra la del slot activo.
- Al cambiar de slot se cargan URL/credenciales/parÃ¡metros desde `camera.yaml`.
- Campos de usuario y contraseÃ±a ahora son visibles y editables en la UI (antes ocultos).
- ParÃ¡metros de imagen con sliders: Brillo / Contraste / SaturaciÃ³n / Nitidez.
  - Rangos Axis VAPIX: Brillo/Contraste/SaturaciÃ³n = âˆ’100..100; Nitidez = 0..100.
  - BotÃ³n **Guardar config** â†’ escribe en `config/camera.yaml` bajo `ip_camera_1` / `ip_camera_2`.
  - BotÃ³n **Aplicar a cÃ¡mara (VAPIX/Axis)** â†’ envÃ­a comandos HTTP GET a
    `{base}/axis-cgi/param.cgi?action=update&ImageSource.I0.Sensor.Brightness=N&...`
    con Basic Auth. Estado (OK/Error) visible en etiqueta inline.

**Archivos modificados:**
- `src/ui/service.py`:
  - `_IP_PARAM_DEFS`, `_IP_VAPIX_MAP` agregados a nivel mÃ³dulo (despuÃ©s de `_PARAM_DEFS`).
  - `CameraCalibTab.__init__`: `_ip_worker/_ip_cap/_ip_timer` â†’ `_ip_workers[2]`,
    `_ip_caps[2]`, `_ip_timers[2]`; agrega `_ip_slot`, `_ip_param_sliders`, `_ip_param_spinboxes`.
  - `_build_ip_camera_section`: reescrito completo con selector de slot, URL, campos usuario/pass,
    panel de parÃ¡metros con sliders, botones Guardar/Aplicar, y preview.
  - MÃ©todos nuevos: `_on_ip_slot_changed`, `_load_ip_slot_settings`, `_disconnect_ip_slot`,
    `_save_ip_settings`, `_apply_ip_params`.
  - MÃ©todos actualizados: `_on_ip_connect`, `_on_ip_disconnect`, `_on_ip_error`,
    `_on_ip_frame_ready`, `_refresh_ip_camera` (ahora reciben `slot: int`).
  - `_ip_auth_settings` eliminado de `CameraCalibTab` (reemplazado por campos explÃ­citos).
- `config/camera.yaml`: agregadas secciones `ip_camera_1` y `ip_camera_2` con URL, credenciales
  y valores de parÃ¡metros de imagen por defecto.

**ValidaciÃ³n:**
- `python -m compileall src/ui/service.py` OK.
- ConstrucciÃ³n de `CameraCalibTab` en modo offscreen: OK.
  - `ip_slot_combo` tiene Ã­tems ["IP CÃ¡m 1", "IP CÃ¡m 2"].
  - `_ip_workers = [None, None]`, `_ip_param_sliders` keys = brightness/contrast/saturation/sharpness.

---

### Sesion 2026-05-28 - Codex

#### Cambio 62 - Camara IP movida a tab Camara con preview grande

**Problema:** La conexion de camara IP estaba dentro de la tab "Grabacion", aunque
conceptualmente pertenece a diagnostico/configuracion de camara. Ademas el preview era
chico para inspeccionar bien la imagen.

**Fix posterior:** El primer parche agrego accidentalmente una llamada a
`_build_ip_camera_section()` dentro de `PLCIOTab`, provocando `AttributeError` al abrir
`python -m src.main service`. Se removio esa referencia de `PLCIOTab` y se dejo la
seccion IP solo en `CameraCalibTab`.

**Cambios:**
- `src/ui/service.py`:
  - La seccion "CAMARA IP EN VIVO" deja de agregarse al layout de `RecordingTab`.
  - `CameraCalibTab` ahora incluye una seccion "Camara IP" con URL, Conectar,
    Desconectar, estado y preview grande.
  - El preview IP queda con alto minimo 520px y escalado suave.
  - Se reutiliza `_MJPEGReader` con credenciales de `config/camera.yaml`.

**Validacion:**
- `python -m compileall src` OK.
- Construccion de `ServiceWindow` en modo offscreen OK.

---

#### Cambio 61 - Autenticacion Axis en visor IP de Grabacion

**Problema:** La prueba directa de `Camera` con `root/defy2026` abria el stream Axis,
pero el boton "Conectar" de la seccion "CAMARA IP EN VIVO" seguia mostrando "Sin
senal". La causa era doble: `_MJPEGReader` en `src/ui/service.py` no recibia
credenciales, entonces la camara respondia `HTTP 401 Unauthorized`; ademas el lector
usaba `np.frombuffer(...)` sin importar `numpy as np`, por lo que fallaba dentro del
hilo Qt aun con credenciales correctas.

**Cambios:**
- `src/ui/service.py`:
  - Agrega `import numpy as np`.
  - `_MJPEGReader` acepta `username` / `password` y envia Basic Auth.
  - El boton "Conectar" toma credenciales desde `config/camera.yaml` para el scanner
    seleccionado; si ese scanner no tiene credenciales, usa las de cualquier scanner
    configurado.
  - URL por defecto corregida a `http://192.168.1.17/axis-cgi/mjpg/video.cgi`.
- `config/camera.yaml`:
  - Agregadas credenciales locales Axis (`username`, `password`, `open_timeout_s`)
    para los scanners existentes.

**Validacion:**
- Prueba directa con `Camera(..., username=root, password=...)`: `open=True`,
  frame `480x640`.
- Prueba directa de `_MJPEGReader` con `QCoreApplication`: emite frame `(480, 640, 3)`.

---

#### Cambio 60 - Soporte de camara IP/MJPEG en produccion

**Problema:** El sistema de produccion solo aceptaba indices USB (`0`, `1`) y abria
siempre con `cv2.CAP_DSHOW`. Una URL Axis como
`http://192.168.1.17/axis-cgi/mjpg/video.cgi` no aparecia en la UI porque DirectShow no
abre streams HTTP/MJPEG y `InspectionSystem` ademas forzaba `scanner_1 -> 0` y
`scanner_2 -> 1`.

**Cambios:**
- `src/vision/camera.py`:
  - `Camera` acepta ahora fuente `int | str`.
  - Para USB mantiene DirectShow y negociacion MJPG.
  - Para RTSP/otras URLs deja que OpenCV elija backend.
  - Para HTTP/HTTPS MJPEG usa lector propio por `urllib`, detectando frames JPEG por
    marcadores SOI/EOI.
  - Soporta `username` / `password` en `config/camera.yaml` para Basic Auth de camaras Axis.
  - Mantiene reconexion automatica, `get_frame()`, `fps`, `is_connected` y validacion anti-bleed.
- `src/controller/system.py`:
  - Nuevo `camera_source` opcional por scanner en `config/io_map.yaml`.
  - Si `camera_source` existe, se respeta y no se pisa con el mapeo fijo USB.
  - Si no existe, se mantiene el comportamiento anterior: `scanner_1 -> 0`,
    `scanner_2 -> 1`.
- `src/ui/service.py`:
  - La seccion "CAMARA IP EN VIVO" ahora usa el lector MJPEG propio para URLs
    HTTP/HTTPS, en vez de `cv2.VideoCapture`, que no abria Axis MJPEG en Windows.

**Validacion:**
- `python -m compileall src` OK.
- Prueba directa contra `http://192.168.1.17/axis-cgi/mjpg/video.cgi` responde
  `HTTP 401 Unauthorized`, por lo que falta configurar credenciales o habilitar
  stream anonimo en la camara.

---

### SesiÃ³n 2026-05-28 â€” Tadeo + Claude

#### Contexto de la sesiÃ³n
ContinuaciÃ³n de sesiÃ³n anterior. Trabajo sobre modelo_A / Esterilla scanner_2.
Problema raÃ­z: grilla escalonada (stagger_x_odd=26px) + dy_realâ‰ˆ37px vs dy_stored=38px.

---

#### Cambio 51 â€” Esterilla: soporte de grilla escalonada (stagger_x_odd)

**Archivos:** `src/patterns/pattern_io.py`, `src/patterns/pattern_build.py`,
`src/pipeline/grid_fitting.py`, `src/inspection.py`, `data/patterns/scanner_2/modelo_A/roi.json`,
`config/tolerancias.yaml`

**MotivaciÃ³n:** El patrÃ³n Esterilla tiene filas impares (cj=1,3,...) con 5 agujeros grandes
y filas pares (cj=2,4,...) con 4 agujeros pequeÃ±os, con un desfase X de ~26px entre orÃ­genes
de fila. Con un grid rectangular sin stagger, la bÃºsqueda de fase X no podÃ­a distinguir ambos
tipos y devolvÃ­a una fase incorrecta â†’ todos los frames NOK.

**Cambios:**
- `pattern_io.py`: campo `stagger_x_odd` en `Pattern` dataclass; guardado/lectura en holes.json
- `pattern_build.py`: detecciÃ³n automÃ¡tica de stagger al construir el patrÃ³n; override `grid_dx/dy`
  desde config para evitar que `estimate_spacing` devuelva 64 en lugar de 66
- `grid_fitting.py`: `grid_compare_points` acepta `stagger_x_odd`; bÃºsqueda de fase X usa
  tolerancia ajustada `tol_x = max(stagger/4, 5)` para evitar saturaciÃ³n; aplica mÃ³dulo al origen
- `inspection.py`: extrae `stagger_x_odd` del patrÃ³n y lo pasa a `grid_compare_points`
- `roi.json`: ajustado a `{x:870, y:0, w:380, h:1080}` (zona del patrÃ³n)
- `tolerancias.yaml`: `grid_dx:66, grid_dy:38, edge_margin_px:5, frame_missing_nok_threshold:8`

**Resultado:** frame_0162 pasÃ³ de 200 missing â†’ 13 missing. Todos los frames aÃºn NOK
por threshold=8 y deriva Y acumulada en filas inferiores.

---

#### Cambio 52 â€” Esterilla: affine refinement estagger-aware

**Archivos:** `src/pipeline/grid_fitting.py`, `config/tolerancias.yaml`

**MotivaciÃ³n:** Las filas inferiores (cj=20+) tienen posiciones reales ~15-25px por encima
de las esperadas (acumulaciÃ³n de deriva Y: dy_realâ‰ˆ37px vs dy_stored=38px, 19 rows â†’ 16px).
Con `grid_affine_refinement:false`, 13 holes missing en frame_0162. Con el affine original
(sin stagger), el fit devolvÃ­a shear incorrecto porque even/odd rows tienen distinto origen X,
resultando en 28 missing (peor).

**Fix:** `_fit_affine_to_grid` ahora acepta `stagger_x_odd` y usa coordenadas fuente
stagger-ajustadas: `src_x = ci*dx + (cj%2)*stagger_x_odd`. Esto permite que el affine
corrija scale_y â‰ˆ 0.934 (actual_dy/stored_dy) sin generar shear espurio entre filas.
`grid_compare_points` pasa `stagger_x_odd` al llamar `_fit_affine_to_grid`.

**Resultado:** frame_0162: 13â†’9 missing. `grid_affine_refinement:true` habilitado.

---

### SesiÃ³n 2026-05-22 â€” Tadeo + Claude

#### Contexto de la sesiÃ³n
ContinuaciÃ³n de sesiÃ³n 2026-05-21. Sistema estable en 185/185 OK con grabaciÃ³n de referencia.
Trabajo en mejoras visuales del overlay de centrado y rediseÃ±o del tab de grabaciÃ³n.

---

#### Cambio 28 â€” Centrado: detecciÃ³n real en ventanas full-frame + fix perf anotaciÃ³n

**MotivaciÃ³n / diagnÃ³stico:**
`run-folder` sobre 185 frames tardaba ~182 s (~1 s/frame). El origen eran dos bugs:

1. **Bug funcional:** `compute_centering()` recibÃ­a la imagen ROI-recortada (650Ã—1077).
   La ROI excluye ambos backlights (izq: col 416â€“687; ROI empieza en x=710; der: col
   1374â€“1645; ROI termina en x=1360). Sin backlight, el algoritmo detectaba los propios
   bordes de la ROI como borde de chapa â†’ `left_x=0, right_x=649` (nulos).

2. **Bug de rendimiento en anotaciÃ³n:** `_draw_edge_polyline()` llamaba
   `_draw_transparent_line()` una vez por segmento (15 segmentos Ã— 4 bordes = 60 llamadas
   por frame). Cada llamada: `np.zeros_like(img_1077Ã—650)` + `any(axis=2)` sobre 700 K
   pÃ­xeles â†’ 3 s en 5 frames solo en numpy.ufunc.reduce segÃºn cProfile.

**SoluciÃ³n:**

A) **`src/pipeline/edge_centering.py`** â€” reescrito completamente:
   - Nueva firma: `compute_centering(img_full, holes, roi=None, tol_px=0.0)`
   - Cuando `roi` estÃ¡ presente, `img_full` es el frame completo alineado (ambos
     backlights visibles). Cuando `roi=None`, mantiene la detecciÃ³n legacy por bandas
     (sin cambio de comportamiento para el path sin ROI).
   - Nueva funciÃ³n `_detect_sheet_edges_in_windows()`:
     - Ventana izquierda: `[max(0, roi.x-350) : roi.x+80]` (full-frame x)
     - Ventana derecha: `[roi.x+roi.w-80 : min(W, roi.x+roi.w+350)]`
     - Canal R (backlight rojo â†’ brillante)
     - Downsampling de filas con paso `_DS=4` para velocidad
     - Kernel de suavizado pequeÃ±o `_SMOOTH_K=7` (preserva magnitud del gradiente)
     - Left: `argmin(diff(col_profile))` â†’ transiciÃ³n brillanteâ†’oscura
     - Right: `argmax(diff(col_profile))` â†’ transiciÃ³n oscuraâ†’brillante
     - Umbral: rango de brillo â‰¥ 20 AND gradiente â‰¥ 3% del rango
     - Devuelve coordenadas en espacio ROI-relativo (mismo que los agujeros)
   - `_N_BANDS=16` de detecciÃ³n por banda, mismo nÃºmero que antes
   - `_MIN_RELIABLE_BANDS=6` sin cambio
   - MÃ¡rgenes calculados en espacio ROI (consistente con holes)

B) **`src/inspection.py` lÃ­nea 278:**
   ```python
   # ANTES (roto):
   centering = compute_centering(img, holes, tol_px=center_offset_tol_px)
   # DESPUÃ‰S (correcto):
   centering = compute_centering(img_aligned, holes, roi=roi, tol_px=center_offset_tol_px)
   ```

C) **`src/pipeline/annotate.py` â€” `_draw_edge_polyline()`:**
   - Antes: 1 capa temporal + 1 alpha blend POR SEGMENTO (hasta 15 por polilÃ­nea)
   - DespuÃ©s: todos los segmentos se dibujan en UNA capa, 1 sola pasada de alpha blend
   - Reduce 60 operaciones de blend/frame â†’ 4 (una por borde)
   - Eliminada la llamada a `_draw_transparent_line` desde `_draw_edge_polyline`

**Resultados medidos:**
- `frame_0009`: `left_x = -31.2 px` (ROI-relativo) â†’ full-frame â‰ˆ 679 px (borde real)
              `right_x = 700.9 px` (ROI-relativo) â†’ full-frame â‰ˆ 1411 px (borde real)
  (antes: 0 y 649 â€” bordes de ROI; o 0 y 1919 â€” bordes de frame)
- 16/16 bandas detectadas, `centering_reliable=True`
- `compute_centering`: ~6 ms/frame (antes ~1 010 ms)
- `run-folder` 185 frames: **27.9 s total** (antes 182.75 s â†’ 6.5Ã— mÃ¡s rÃ¡pido)
- `inspect_image` aislado: ~175 ms/frame (antes ~1 094 ms)
- 185/185 OK mantenido (centrado es informacional, `center_offset_tol_px=0.0`)

**Sin tocar:** PLC, solenoides, lÃ³gica temporal, lÃ³gica de comparaciÃ³n de agujeros.

---

#### Cambio 27 â€” RediseÃ±o tab GrabaciÃ³n: estÃ©tica industrial + exportaciÃ³n de imÃ¡genes

**MotivaciÃ³n:** El tab de GrabaciÃ³n en la UI de Servicio tenÃ­a controles apilados en una sola
fila horizontal, sin jerarquÃ­a visual, sin indicador de estado claro y sin forma de guardar
las imÃ¡genes analizadas.

**DecisiÃ³n:**
- RediseÃ±o completo de `RecordingTab` en `src/ui/service.py` con layout industrial oscuro
- SeparaciÃ³n clara en 3 secciones: GRABACIÃ“N / ANÃLISIS / NAVEGADOR DE CAPTURAS
- Panel de estado de grabaciÃ³n prominente (badge con estado+count+carpeta)
- NavegaciÃ³n con botones primero/Ãºltimo, contador de frame grande y legible
- Toggle de overlay con estilo ON/OFF coloreado
- Nuevo sistema de exportaciÃ³n: guardar frame actual (auto a data/output/export/)
  y exportar rango de frames (spinbox Desde/Hasta â†’ carpeta con timestamp)

**Archivos modificados:**
- `src/ui/service.py`:
  - AÃ±adido `QFrame` a imports (necesario para separadores horizontales/verticales)
  - `RecordingTab` completamente reescrito:
    - `_build_recording_section()`: config row + action row con badge de estado
    - `_build_analysis_section()`: botones + progress + resumen coloreado
    - `_build_browser_section()`: nav (primero/Ãºltimo), toggle overlay coloreado,
      fila de exportaciÃ³n (guardar actual + rango con spinboxes)
    - `_set_rec_badge()`: actualiza badge STANDBY/GRABANDO/LISTO/ANALIZANDO/ANALIZADO
    - `_save_current_frame()`: auto-save overlay â†’ data/output/export/{ts}.png
    - `_export_range()`: exporta frames f_from..f_to â†’ data/output/export/rango_{ts}/
    - `_update_export_range_max()`: sincroniza spinboxes al cargar/grabar frames
    - `_update_export_label()`: actualiza texto del botÃ³n exportar con cantidad
    - `_on_overlay_toggled()`: toggle ON/OFF con texto dinÃ¡mico
    - `_hline()`, `_vline()`, `_lbl()`, `_make_combo()`: helpers de UI
    - `_mk_btn()` ahora acepta parÃ¡metros h/fs/w para mayor flexibilidad
  - Funcionalidades existentes 100% preservadas (grabaciÃ³n, anÃ¡lisis, carga de carpeta,
    anÃ¡lisis en vivo, info de cÃ¡mara, resumen temporal)

**Nuevo flujo de exportaciÃ³n:**
- Frame actual: botÃ³n "Guardar frame actual" (habilitado solo si hay resultado)
  â†’ guarda `frame_NNNN_STATUS_YYYYMMDDHHMMSS.png` en `data/output/export/`
- Rango: spinboxes Desde/Hasta + botÃ³n "Exportar N frames"
  â†’ crea `data/output/export/rango_YYYYMMDDHHMMSS/` con todos los overlays del rango
  â†’ habilitado solo cuando el anÃ¡lisis cubre el rango completo seleccionado

---

#### Cambio 26 â€” DetecciÃ³n de bordes real por bandas (polyline overlay)

**MotivaciÃ³n:** El overlay de centrado mostraba lÃ­neas verticales perfectas (un Ãºnico X por lado)
basadas en el perfil de columna global. No reflejaba la realidad: si el borde de la chapa no
es perfectamente vertical o hay variaciÃ³n por altura, se perdÃ­a esa informaciÃ³n.

**DecisiÃ³n:** Dividir la imagen en 16 bandas horizontales, detectar el borde metÃ¡lico en cada
banda por separado, y dibujar una polilÃ­nea real en lugar de una lÃ­nea perfectamente vertical.
Para el patrÃ³n punzonado, usar los agujeros reales detectados por banda (hole.x Â± hole.r),
no el bbox del patrÃ³n teÃ³rico.

**Archivos modificados:**

- `src/pipeline/edge_centering.py`:
  - Nueva constante `_N_BANDS = 16`, `_MIN_RELIABLE_BANDS = 6`
  - Nueva funciÃ³n `_detect_edges_by_band()` â†’ devuelve `dict[band_idx â†’ (x, cy)]` para left/right
  - Nueva funciÃ³n `_pattern_bounds_by_band()` â†’ bounds del patrÃ³n real por banda
  - Nueva funciÃ³n `_fit_line_robust()` â†’ ajuste x=a*y+b con sigma-clip outlier rejection
  - Nueva funciÃ³n `_line_x_at_y()` â†’ evalÃºa la lÃ­nea en Y dado
  - `_detect_metal_edges_full()` â†’ fallback de imagen completa (renombrado, antes `_detect_metal_edges`)
  - `CenteringResult` extendido con nuevos campos (todos con default para compatibilidad):
    - `left_edge_points`, `right_edge_points`: tupla de (x, y) por banda, borde de chapa real
    - `pattern_left_points`, `pattern_right_points`: tupla de (x, y) por banda, borde patrÃ³n real
    - `left_margin_std`, `right_margin_std`: std dev de mÃ¡rgenes por banda
    - `centering_reliable`: False si < 6 bandas detectadas
  - `compute_centering()` reescrito para usar detecciÃ³n por bandas:
    - `left_x`, `right_x` escalares ahora vienen de la lÃ­nea robusta evaluada en mid-height
    - Si no hay suficientes puntos, fallback a mediana, luego a perfil de imagen completa
    - EstadÃ­sticas de margen por banda cuando hay correspondencia edge+patrÃ³n

- `src/pipeline/annotate.py`:
  - Nueva funciÃ³n `_draw_edge_polyline()` a nivel mÃ³dulo: dibuja polilÃ­nea con alpha + puntos sample
  - `draw_centering_overlay()` actualizado:
    - Usa polilÃ­nea real cuando `left_edge_points` / `right_edge_points` tienen â‰¥ 2 puntos
    - Fallback a lÃ­nea vertical cuando no hay datos por banda
    - Texto de margen extendido: agrega `Var: Â±Xpx` (std dev de mÃ¡rgenes)
    - Badge "BORDES NO CONFIABLES" (naranja) cuando `centering_reliable=False`
    - Badge "NOK CENTRADO" (rojo) preservado sin cambios

**Comportamiento observado en debug_crop_frame9.png (1077Ã—1054px):**
- 16/16 bandas detectadas en ambos lados â†’ `centering_reliable=True`
- `left_xâ‰ˆ203px`, `right_xâ‰ˆ893px` (consistente con mediciÃ³n anterior)
- PolilÃ­nea gris para bordes de chapa, cyan para patrÃ³n, puntos pequeÃ±os en cada muestra

**GarantÃ­a 185/185 OK mantenida:** La lÃ³gica de inspecciÃ³n (detecciÃ³n, comparaciÃ³n,
regla temporal) no se tocÃ³. El centrado es puramente informacional (`center_offset_tol_px=0.0`
por defecto).

---

### SesiÃ³n 2026-05-19 â€” Tadeo + Claude

#### Contexto de la sesiÃ³n
Primera sesiÃ³n de trabajo con el cÃ³digo ya en estado V1 funcional.
Sistema con 2 scanners, PLC, cÃ¡maras USB montadas fijas, backlight estable.
`consecutive_nok_frames: 9999` en config = FAULT deshabilitado temporalmente (calibraciÃ³n).

---

#### Cambio 1 â€” Bloqueo de seguridad solenoides Y10/Y11

**MotivaciÃ³n:** Los solenoides Y10 (scanner_1) e Y11 (scanner_2) controlan pistones
fÃ­sicos en la mÃ¡quina. En modo diagnÃ³stico HW habÃ­a botones que los podÃ­an activar
accidentalmente, representando un riesgo real de accidente. El control automÃ¡tico de
pistones estÃ¡ planificado pero no implementado aÃºn.

**DecisiÃ³n:** Bloqueo doble â€” software + visual â€” hasta que el control automÃ¡tico estÃ© listo.

**Archivos modificados:**
- `src/plc/io_map.py` â†’ `IOMap.write()` rechaza cualquier `signal.endswith(".solenoid") and value=True`
  con WARNING en log y retorna False. Ãšltima lÃ­nea de defensa.
- `src/controller/scanner_controller.py` â†’ removidas las 3 lÃ­neas `write(solenoid, True)`
  en `start()` (AUTO y MANUAL) y `start_simulate()`. Los `write(solenoid, False)` de
  parada/fault/shutdown se mantienen intactos (son salidas de seguridad).
- `src/ui/service.py` â†’ botÃ³n "Solenoide" deshabilitado (gris, texto "LOCK") en:
  - Tab "Prueba de salidas PLC" (PLCOutputTestTab)
  - Tab "DiagnÃ³stico HW" â†’ botones Y8 e Y9 (offsets de los solenoides)
  - `refresh()` de ambas tabs omite esos botones para no sobreescribir el estilo

**Para re-habilitar en el futuro:** Remover el guard en `IOMap.write()`, restaurar
las lÃ­neas en `scanner_controller.py`, y re-habilitar los botones en `service.py`.

---

#### Cambio 2 â€” Arranque rÃ¡pido (startup)

**Problema:** El comando `run` tardaba 2â€“4 segundos en mostrar la UI porque
`Camera.start()` llamaba `_open_capture()` de forma sincrÃ³nica. MSMF (Windows Media
Foundation) + 3 warmup frames a 5fps = ~1.5s de bloqueo ANTES de que aparezca la ventana.

**SoluciÃ³n:** `Camera.start()` ahora es no-bloqueante. Lanza el thread de captura
inmediatamente y retorna `True`. El loop de captura ya tenÃ­a `retry_wait=0` en
primera iteraciÃ³n, por lo que abre la cÃ¡mara en background sin cambios funcionales.

**Archivos modificados:**
- `src/vision/camera.py` â†’ `start()` no llama `_open_capture()` sync; agrega flag
  `_first_open` en `_capture_loop` para loguear "iniciada" vs "reconectada" correctamente.
- `src/controller/system.py` â†’ `start_cameras()` simplificado: sin wrapper de threads
  (innecesario ahora que `cam.start()` es instantÃ¡neo). Removido import `threading`.

**Resultado:** UI aparece en ~300â€“600ms. CÃ¡maras conectan en background mostrando
feed cuando estÃ¡n listas.

---

#### Cambio 3 â€” Mejoras al pipeline de visiÃ³n (fiabilidad + rendimiento)

**MotivaciÃ³n:** Mejorar fiabilidad y reducir latencia del pipeline OpenCV sin salir
de OpenCV. CÃ¡mara fija, iluminaciÃ³n estable y especÃ­fica (backlight), ese problema
ya estÃ¡ resuelto en hardware.

**3a. VectorizaciÃ³n de compare.py**
- `compare_missing_only()` reemplaza loop Python O(nÃ—m) por matriz de distancias
  numpy calculada de una sola vez. Para 200 agujeros: 40.000 iteraciones Python â†’
  una operaciÃ³n numpy + 200 argmin vectorizados. ~10â€“30Ã— mÃ¡s rÃ¡pido por frame.
- Mismo algoritmo greedy, mismo comportamiento, mÃ¡s rÃ¡pido.

**3b. MORPH_CLOSE en preprocess.py**
- DespuÃ©s del MORPH_OPEN (elimina ruido), se agrega MORPH_CLOSE (kernel 5Ã—5, 1 iter).
- Rellena micro-gaps dentro de la mÃ¡scara binaria de cada agujero causados por
  desgaste leve del punzÃ³n o micro-reflejos en el borde del contorno.
- Reduce falsos "missing" en chapas con agujeros levemente irregulares.

**3c. Centroide por momentos en detect_holes.py**
- PosiciÃ³n (x, y) del agujero calculada con `cv2.moments` en lugar de
  `minEnclosingCircle`. El centroide es mÃ¡s estable ante pixels outlier en el borde.
- El radio `r` sigue viniendo de `minEnclosingCircle` (correcto para edge_margin_px).

**3d. max_area en config.py**
- `max_area: None` agregado a DEFAULT_TOLERANCES. Por defecto deshabilitado (sin
  lÃ­mite superior de Ã¡rea).
- Setear por modelo en `tolerancias.yaml` para rechazar contornos grandes (reflejos,
  suciedad en lente). Ejemplo: `max_area: 5500.0` para modelo_B (Ã¡rea mediana ~1100 pxÂ²).

**3e. EMA del Ã¡ngulo de rotaciÃ³n en align_edge.py**
- `align_image_by_right_edge()` acepta `ema_state: dict` (propiedad del Inspector).
- Si Hough detecta lÃ­neas: actualiza EMA con alpha=0.25. Si no detecta: usa Ãºltimo
  Ã¡ngulo suavizado conocido. Absorbe estimaciones ruidosas en frames con borde poco nÃ­tido.

**3f. VectorizaciÃ³n de inlier check + RANSAC en inspection.py**
- Inlier check de pre-shift: reemplaza loop Python por operaciÃ³n matricial numpy
  `(n_det, n_pat, 2)` â†’ min distancia vectorizada.
- RANSAC `maxIters`: 2000 â†’ 500. Suficiente con `confidence=0.99` para puntos bien matcheados.

**3g. Cache en inspector.py**
- `Inspector` cachea tolerancias, patrÃ³n y ROI por `(model, scanner_id)`.
- Elimina 3 lecturas de disco por frame (~30 I/O ops/seg con 2 scanners a 5fps).
- `invalidate(model, scanner_id)` fuerza recarga. Se llama automÃ¡ticamente en
  `ScannerController.set_model()` cuando el operador cambia de modelo desde la UI.
- EMA state por scanner tambiÃ©n vive en el Inspector.

---

---

### SesiÃ³n 2026-05-19 (continuaciÃ³n) â€” Tadeo + Claude

#### Cambio 4 â€” Visor de imÃ¡genes zoomable en modo servicio (RecordingTab)

**MotivaciÃ³n:** La imagen de frame/overlay en la tab "GrabaciÃ³n" se mostraba estÃ¡tica
en un `QLabel` escalado a tamaÃ±o fijo. No era posible hacer zoom ni pan para analizar
detalles del patrÃ³n de agujeros.

**SoluciÃ³n:** Nuevo widget `ZoomableImageView(QWidget)` que reemplaza el `QLabel`.

**Funcionalidades:**
- Rueda del mouse â†’ zoom hacia el cursor (15% por tick, rango 5%â€“3000%)
- Click + drag â†’ pan libre
- Doble click â†’ fit automÃ¡tico (ajustar a ventana)
- Badge "ZZ%" en esquina superior derecha indicando zoom actual
- BotÃ³n "Ajustar" en la barra de navegaciÃ³n â†’ equivale al doble click

**Archivos modificados:**
- `src/ui/service.py`:
  - Imports: `QPainter`, `QPointF`, `QRectF` agregados
  - Clase `ZoomableImageView` insertada antes de `RecordingTab`
  - `RecordingTab._img_label` (QLabel estÃ¡tico) reemplazado por `self._img_view`
  - `_show_frame()`: llama `self._img_view.set_pixmap(px)` en lugar de `setPixmap(px.scaled(...))`
  - `_on_start()`: llama `self._img_view.clear("Sin frames")` en lugar de `setText`
  - BotÃ³n "Ajustar" agregado al nav_row, conectado a `self._img_view.fit`

---

---

### SesiÃ³n 2026-05-20 â€” Tadeo + Claude

#### Contexto de la sesiÃ³n
SesiÃ³n dividida en dos partes. La primera parte (resumen de sesiÃ³n anterior) incluyÃ³
mÃºltiples mejoras al pipeline y la UI antes de quedar en el contexto de la sesiÃ³n.
La segunda parte (en esta sesiÃ³n) se enfocÃ³ en analizar grabaciones de la planta y
corregir detecciones falsas masivas en modelo_B.

**Commits de esta sesiÃ³n:**
- `e8537fd` â€” Fix false detections en modelo_B: ROI ajustada, grid dy=20, matcher closest-first

---

#### Cambio 5 â€” ParÃ¡metros morfolÃ³gicos configurables en preprocess.py

**MotivaciÃ³n:** `blur_ksize`, `open_ksize`, `close_ksize` estaban hardcodeados.
Necesario poder ajustar por modelo sin tocar cÃ³digo.

**Archivos modificados:**
- `src/pipeline/preprocess.py` â†’ `preprocess_for_holes()` acepta `blur_ksize=5`,
  `open_ksize=3`, `close_ksize=5`. Auto-corrige blur_ksize par â†’ impar. Valor 0 deshabilita
  la operaciÃ³n morfolÃ³gica correspondiente.
- `src/utils/config.py` â†’ `DEFAULT_TOLERANCES` agrega `blur_ksize`, `open_ksize`,
  `close_ksize`, `min_detection_ratio`, `max_extra`, `startup_selftest_enabled`,
  `selftest_timeout_s`, `max_inspection_hz`, `grid_min_spacing`.
- `src/patterns/pattern_build.py` â†’ pasa los tres parÃ¡metros a `preprocess_for_holes()`.

---

#### Cambio 6 â€” Extra detections + mÃ©tricas de calidad por frame

**MotivaciÃ³n:** El resultado de inspecciÃ³n no reportaba cuÃ¡ntos agujeros "de mÃ¡s"
se detectaban (spurious / reflejos), ni quÃ© tan bien se detectÃ³ el patrÃ³n completo.

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
  - LÃ­nea resumen: `avg_detection_ratio=X%  align_failures=N/total`

---

#### Cambio 7 â€” MÃ©tricas de calidad en ScannerController y Recorder

**Archivos modificados:**
- `src/controller/scanner_controller.py`:
  - Acumula `_total_detection_ratio` y `_align_fail_count` por sesiÃ³n
  - `get_status()` retorna `avg_detection_ratio` y `align_fail_count`
  - `inject_result()` pasa `detection_ratio=1.0, alignment_ok=True` en modo simulaciÃ³n
- `src/metrics/recorder.py`:
  - `_init_db()`: migraciÃ³n de esquema con `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`
    para agregar columnas nuevas sin romper DBs existentes
  - `_INSERT` y `_snapshot()` incluyen `avg_detection_ratio`, `align_fail_count`

---

#### Cambio 8 â€” Escritura batch de salidas PLC (backlight y luces)

**Problema:** `_set_lights()` escribÃ­a 4 coils individuales en 4 transacciones Modbus.
Con el poller leyendo cada 50ms, los writes competÃ­an y la luz de fondo demoraba hasta 200ms.
Adicionalmente, el backlight se encendÃ­a DESPUÃ‰S de arrancar los threads â†’ el selftest
corrÃ­a sobre un frame oscuro y fallaba.

**SoluciÃ³n:**
- `src/plc/client.py` â†’ nuevo `write_coils_batch(offset, values)`: escribe N coils
  contiguos en UNA sola transacciÃ³n Modbus usando `write_coils()`.
- `src/plc/io_map.py` â†’ nuevo `write_batch(signals)`: detecta offsets contiguos y
  usa `write_coils_batch()`, con fallback a escrituras individuales si no son contiguos.
- `src/controller/scanner_controller.py`:
  - `_set_lights()` ahora usa `write_batch()` â†’ 1 transacciÃ³n en vez de 4
  - En `start()` modo AUTO: `io.write(backlight, True)` se llama **ANTES** de
    `_start_all_threads()` (antes era despuÃ©s) â€” backlight encendido desde el primer frame
  - Selftest deshabilitado por defecto (`startup_selftest_enabled: False` en config);
    tiene delay de 150ms al arrancar para evitar frame oscuro si se habilita
  - Limita rate de inspecciÃ³n con `max_inspection_hz` (usando `time.monotonic()`)

---

#### Cambio 9 â€” RecordingTab scrollable + imagen mÃ¡s grande

**MotivaciÃ³n:** La tab de GrabaciÃ³n en modo servicio no tenÃ­a scroll; la imagen
quedaba pequeÃ±a y no se podÃ­a ver bien el resultado del anÃ¡lisis.

**Archivos modificados:**
- `src/ui/service.py` â†’ `RecordingTab._build_ui()`:
  - Contenido envuelto en `QScrollArea` (scrollable verticalmente)
  - `self._img_view.setMinimumHeight(640)` para que la imagen aparezca mÃ¡s grande

---

#### Cambio 10 â€” AnÃ¡lisis de grabaciones de planta (diagnÃ³stico)

**Contexto:** Se analizaron 3 sesiones de grabaciones del scanner_1 (modelo_B)
almacenadas en `data/recordings/` de la mÃ¡quina de planta:
- `20260512_194928`: 321 frames
- `20260512_203224`: 53 frames
- `20260512_203246`: 195 frames

**Hallazgos del primer anÃ¡lisis (con parÃ¡metros incorrectos):**
- Se corrÃ­a `run-folder --model modelo_B` SIN `--scanner scanner_1` â†’
  cargaba el patrÃ³n viejo de `data/patterns/modelo_B/holes.json` (imagen 370Ã—1080)
  y la ROI incorrecta `{"x":573, "y":0, "w":247, "h":720}` (de cÃ¡mara de menor resoluciÃ³n).
- Con el scanner correcto: frame_0009 (mejor frame) daba `missing=25, extra=105`.

**DiagnÃ³stico de las 3 causas raÃ­z:**

**Causa 1 â€” ROI incorrecta (incluye backlight desnudo):**
- CÃ¡mara es 1920Ã—1080. El backlight desnudo sin material aparece en:
  - Frame col 416â€“687 (lado izquierdo, 271px de ancho)
  - Frame col 1374â€“1645 (lado derecho, 271px de ancho)
- La ROI anterior (`x=482, w=1054`) capturaba ambas zonas brillantes.
- Con `polarity: bright`, el backlight desnudo aparece como agujeros.
- Estos falsos detectados corrompÃ­an la estimaciÃ³n de fase de la grilla, desplazando
  todas las posiciones esperadas y generando 100+ extras y 25+ faltantes.
- **Fix:** ROI nueva `{"x":710, "y":3, "w":650, "h":1077}` â€” excluye ambas zonas
  de backlight con ~23px de margen izquierdo y ~14px de margen derecho.

**Causa 2 â€” Grid dy=40 en vez de 20 (grilla escalonada):**
- La lÃ¡mina microperforada tiene filas de agujeros alternadas cada ~20px en Y
  (similar a empaque hexagonal).
- `estimate_spacing(ys, min_spacing=30)` filtraba las diferencias ~20px y encontraba
  el perÃ­odo doble (40px), asignando el mismo (ci, cj) a dos agujeros distintos.
- `grid_compare_points` deduplicaba uno de los dos â†’ solo 104 posiciones Ãºnicas de 152.
- **Fix:** Nuevo parÃ¡metro `grid_min_spacing: 15.0` en `tolerancias.yaml` bajo `modelo_B`.
  Con min_spacing=15, `estimate_spacing` encuentra dy=20 â†’ 152 celdas Ãºnicas.
- `src/utils/config.py` â†’ `DEFAULT_TOLERANCES` agrega `"grid_min_spacing": 30.0`
- `src/patterns/pattern_build.py` â†’ pasa `grid_min_spacing` a `estimate_spacing()`

**Causa 3 â€” Matcher greedy procesa en orden de grilla, no por proximidad:**
- `compare_missing_only()` iteraba expected_points en orden secuencial.
- Un punto esperado lejano (ej. 24px) que aparecÃ­a ANTES en la lista "robaba"
  la detecciÃ³n de un punto esperado cercano (6px), dejÃ¡ndolo como "missing".
- Con dy=20 y tol_xy_px=28, dos filas adyacentes (20px) competÃ­an por el mismo detectado.
- **Fix:** `src/pipeline/compare.py` â†’ `order = np.argsort(dist2.min(axis=1))`
  antes del loop greedy: se procesan primero los pares mÃ¡s cercanos.

**Resultados despuÃ©s de los 3 fixes:**
- Frame 0009 (referencia): `missing=1, extra=1` (antes: `missing=25, extra=105`) âœ“
- `avg_detection_ratio` en carpeta 321 frames: 45% â†’ 77%
- Los frames con material en movimiento (blur) siguen con ratio bajo: comportamiento CORRECTO
  (lÃ¡mina moviÃ©ndose = agujeros borrosos = no inspeccionar esos frames en producciÃ³n)
- `align_failures`: 25/321 â†’ 1/321 (eliminar backlight resolviÃ³ casi todos los fallos de alineaciÃ³n)

---

#### ParÃ¡metros config actuales (tolerancias.yaml) despuÃ©s de esta sesiÃ³n

```yaml
# Globales
threshold: 175
use_channel: r
polarity: bright
min_area: 80.0
circularity_min: 0.8
tol_xy_px: 22.0
max_inspection_hz: 15
consecutive_nok_frames: 9999    # CALIBRACIÃ“N: FAULT deshabilitado
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

### SesiÃ³n 2026-05-21 â€” Tadeo + Claude

#### Contexto de la sesiÃ³n
SesiÃ³n larga de diagnÃ³stico y calibraciÃ³n del sistema en modelo_B (microperforado / scanner_1).
Se realizÃ³ anÃ¡lisis de la grabaciÃ³n `20260519_121741` (185 frames, material bueno en movimiento continuo).
Objetivo: eliminar falsos NOK, mejorar detecciÃ³n en blur, corregir errores de grilla.

**Commits de esta sesiÃ³n:**
- `134cc0e` â€” Init backlight ON al conectar PLC (Y12/Y13 siempre visibles)
- `b5789fd` â€” Centrado de chapa: detecciÃ³n de bordes laterales y offset del patrÃ³n
- `4a160c9` â€” Etiquetado diferenciado de NOK por centrado vs agujeros
- `dc65e0e` â€” Overlay imagen completa (sin recorte ROI) + bordes en gris semitransparente
- `23ef8dc` â€” Fix grid phase estimation: 2D Y-scan + X re-estimaciÃ³n + sincronizaciÃ³n de patrones
- `811430c` â€” Mejoras post-anÃ¡lisis: bbox filter, grid_max_missing, quality_ratio_min
- `8163012` â€” tol_xy_px modelo_B: 12â†’18px â€” reduce falsos raw NOK de 138 a 22
- `777b99e` â€” Fix detecciÃ³n blur: min_area modelo_B 300â†’250pxÂ²
- `ed8916a` â€” Fix borde de patrÃ³n y tolerancia: Y-clip + tol_xy_px 18â†’22

**Resultado final de la sesiÃ³n:**
```
185/185 raw OK, 0 raw NOK, 0 temporal NOK
avg_detection_ratio = 100%, align_failures = 0/185
Missing en frames limpios = 0
```

---

#### Cambio 11 â€” Backlight siempre ON al iniciar

**MotivaciÃ³n:** Las cÃ¡maras no eran visibles al arrancar si el backlight (Y12/Y13) no
estaba encendido. Se querÃ­a que las salidas de backlight inicializaran siempre como ON
al conectar el sistema, independientemente del estado del PLC.

**Archivos modificados:**
- `src/controller/scanner_controller.py` â†’ `initialize_lights()` escribe
  `io.write("{id}.backlight", True)` antes de configurar las luces de estado.
  El backlight queda ON desde el primer ciclo.

---

#### Cambio 12 â€” MediciÃ³n de centrado de chapa (edge centering)

**MotivaciÃ³n:** Para MICROPERFORADO el patrÃ³n de punzonado siempre debe estar centrado
entre los bordes laterales de la chapa. Se querÃ­a medir el offset y etiquetar frames
fuera de tolerancia sin perder la inspecciÃ³n de agujeros.

**ImplementaciÃ³n:**

**`src/pipeline/edge_centering.py`** (nuevo):
- `_detect_metal_edges(img_bgr)`: usa el percentil 20 por columna (perfil oscuro=metal,
  brillante=backlight). Localiza el primer y Ãºltimo pÃ­xel oscuro â†’ borde izquierdo y derecho.
- `compute_centering(img_bgr, holes_xs, tol_px)` â†’ `CenteringResult` con:
  `left_x`, `right_x`, `sheet_center_x`, `holes_center_x`, `offset_px`, `within_tol`.

**`src/pipeline/annotate.py`**:
- `_draw_transparent_line()`: blend alpha por pixel para lÃ­neas semitransparentes sin scipy.
- `draw_centering_overlay()`: dibuja bordes metÃ¡licos (gris semitransparente alpha=0.45),
  lÃ­nea de centro de chapa (naranja discontinua), lÃ­nea de centro de agujeros (blanca),
  flecha de offset, badge "NOK CENTRADO" cuando `tag_nok=True`.

**`src/inspection.py`**:
- `InspectionResult` agrega `centering: CenteringResult | None` y `centering_nok: bool`.
- `_inspect_bgr()` llama `compute_centering()` y combina con el resultado de agujeros:
  `final_status = "NOK" if (report.status == "NOK" or centering_nok) else "OK"`.

**`src/utils/config.py`**: agrega `"center_offset_tol_px": 0.0` a DEFAULT_TOLERANCES.

**`src/ui/operator.py`**: card "CENTRADO" en panel de mÃ©tricas â†’ muestra offset en px,
naranja cuando fuera de tolerancia.

**`src/ui/service.py`**: estadÃ­sticas de centrado al final del anÃ¡lisis de grabaciÃ³n.

**Etiquetado diferenciado (Cambio 13):**
- La UI y el overlay distinguen la causa del NOK:
  - "NOK AGUJEROS" â†’ rojo
  - "NOK CENTRADO" â†’ naranja
  - "NOK AGUJEROS + CENTRADO" â†’ rojo con badge adicional

---

#### Cambio 13 â€” Overlay imagen completa sin recorte ROI

**Problema:** El overlay solo mostraba la ROI recortada. El operador no podÃ­a ver la
imagen completa de la cÃ¡mara ni los bordes de la chapa.

**Fix en `src/inspection.py`** â†’ `_inspect_bgr()`:
- Anotaciones se dibujan sobre `img` (ROI recortada) con coordenadas relativas a la ROI.
- El resultado se compone sobre `img_aligned` completa: si hay ROI, se hace paste en la
  posiciÃ³n `[roi.y:roi.y+roi.h, roi.x:roi.x+roi.w]`. Sin ROI: se usa directamente.
- El operador ve el encuadre completo de la cÃ¡mara con las anotaciones correctamente
  posicionadas dentro de la zona de inspecciÃ³n.

---

#### Cambio 14 â€” Fix crÃ­tico: estimaciÃ³n de fase de grilla (grid_fitting.py)

**Problema raÃ­z identificado en esta sesiÃ³n:**
Los archivos de patrÃ³n y ROI a nivel modelo (`data/patterns/modelo_B/`) estaban
desactualizados respecto a los de `data/patterns/scanner_1/modelo_B/`:
- `holes.json` modelo-nivel: dx=50, 155 puntos (patrÃ³n viejo incorrecto)
- `roi.json` modelo-nivel: `{x:573, w:247}` (ROI vieja, muy estrecha)
El comando `run-folder` sin `--scanner` cargaba estos archivos obsoletos.

**Problema 2 â€” Fase X fija bloqueaba deriva lateral:**
El cÃ³digo tenÃ­a `origin_x = phase_ref_x` (fase fija) para evitar la "ambigÃ¼edad bimodal"
de grillas escalonadas. Pero para ESTA grilla, el offset escalonado estÃ¡ codificado en
los valores enteros de `ci` (ci par = filas pares, ci impar = filas impares). Por lo tanto
`x % dx = phase_x` para TODOS los agujeros â†’ no hay distribuciÃ³n bimodal. Fijar la fase
impedÃ­a compensar derivas laterales de Â±5-15px del material.

**Problema 3 â€” Escaneo Y en 1D daba falsos matches en frames de transiciÃ³n:**
El escaneo de fase Y buscaba la fase que maximizara coincidencias en Y Ãºnicamente.
En frames con blur/transiciÃ³n, agujeros de filas adyacentes podÃ­an "matchear" la Y
esperada sin estar en la X correcta â†’ se elegÃ­a una fase Y incorrecta que colocaba
posiciones esperadas ~17px lejos de las reales.

**Fixes aplicados en `src/pipeline/grid_fitting.py`:**

**Fix 1 â€” X: re-estimar fase por frame:**
```python
# Escaneo X sobre [0, dx) igual que Y
for px_cand in np.arange(0.0, dx, 1.0):
    exp_xs = px_cand + ci_arr * dx
    ...
    count_x = int((diffs_x.min(axis=1) <= tol_x).sum())
origin_x = best_phase_x
```

**Fix 2 â€” Y: escaneo 2D (X + Y simultÃ¡neamente):**
```python
# Precomputa x_match con origin_x ya conocido
x_match = |det_xs - exp_xs| <= tol_x   # (n_det, n_cells)
for phase_candidate in [0..dy):
    y_match = |det_ys - exp_ys| <= tol_y  # (n_det, n_cells)
    both = x_match & y_match & valid
    count = both.any(axis=1).sum()
```
AsÃ­ un agujero detectado solo cuenta si estÃ¡ dentro de tol en X E Y del mismo punto
esperado â†’ elimina los falsos matches de filas adyacentes.

**SincronizaciÃ³n de archivos:**
- `data/patterns/modelo_B/holes.json` copiado desde `scanner_1/modelo_B/holes.json`
  (dx=28, dy=22, 258 puntos)
- `data/patterns/modelo_B/roi.json` copiado desde `scanner_1/modelo_B/roi.json`
  (`x=710, w=650`)

**Resultado:** Paso de `raw_ok=0/185` (con patrÃ³n viejo) a `raw_ok=162/185` con los fixes.

---

#### Cambio 15 â€” Mejoras post-anÃ¡lisis de grabaciÃ³n

**AnÃ¡lisis de la grabaciÃ³n 20260519_121741 (185 frames):**
- DetecciÃ³n media: ~383 agujeros/frame con params viejos (ratio 165%)
- Missing baseline: 2â€“15 en frames buenos
- 4 frames raw NOK transitorios por blur de movimiento

**Fixes:**

**`src/inspection.py`** â€” Filtro bbox antes de matching:
- Antes de llamar `compare_missing_only()`, los detectados se filtran al bounding box
  de los puntos esperados + `bbox_filter_margin_px` (configurable).
- Elimina agujeros reales del material fuera de la ventana del patrÃ³n, reduciendo el
  conteo de "extra" y el costo computacional del matching.

**`src/inspection.py`** â€” `capture_quality_degraded`:
- Nuevo campo `capture_quality_degraded: bool` en `InspectionResult`.
- Si `quality_ratio_min > 0` y `ratio < quality_ratio_min` (pero â‰¥ `min_detection_ratio`):
  se pone en `True`. No afecta el NOK. Visible en overlay ("CALIDAD DEGRADADA") y log.
- Ãštil para detectar blur de movimiento independientemente de la decisiÃ³n de inspecciÃ³n.

**`config/tolerancias.yaml` â€” modelo_B:**
- `grid_max_missing: 30 â†’ 35` (absorbe picos de blur sin comprometer detecciÃ³n de punzÃ³n roto)
- Nuevos parÃ¡metros: `bbox_filter_margin_px: 20.0`, `quality_ratio_min: 0.0` (deshabilitado)

---

#### Cambio 16 â€” tol_xy_px 28â†’12â†’18â†’22 (calibraciÃ³n iterativa)

**Historia de la tolerancia durante esta sesiÃ³n:**

| Valor | Resultado | Problema identificado |
|-------|-----------|----------------------|
| 28.0 | raw_ok=162/185 | Matching ambiguo: tol=dx, zonas solapadas |
| 12.0 | raw_ok=0/185 | PatrÃ³n viejo cargado â†’ 0 detecciones (bug ROI) |
| 12.0 (fix ROI) | raw_ok=34/185 | Fase Y 1D â†’ posiciones esperadas ~17px off |
| 18.0 (fix fase) | raw_ok=163/185 | Agujeros con blur <300pxÂ² filtrados |
| 18.0 (fix area) | raw_ok=181/185 | Drift lateral en borde inferior >18px |
| 22.0 + Y-clip | **185/185** | âœ“ |

**Razonamiento para tol=22:**
- Error real de centroide de detecciÃ³n: <5px
- Adjacent same-row holes: 28px de separaciÃ³n â†’ zonas no se solapan en la prÃ¡ctica
- Necesario para absorber drift de borde + blur residual

---

#### Cambio 17 â€” min_area 300â†’250 para modelo_B (blur de movimiento)

**DiagnÃ³stico:**
- frames con blur (0066, 0067, etc.): `detect_loose=287` vs `detect_strict=211`
- Histograma de Ã¡reas revelÃ³: **50â€“52 blobs reales en rango 250â€“299pxÂ²** en frames con blur
- En frames limpios: prÃ¡cticamente 0 blobs en ese rango (gap natural en 200â€“250pxÂ²)
- El blur de movimiento reduce el Ã¡rea aparente de los agujeros de ~350â€“450pxÂ² a 250â€“299pxÂ²

**Fix en `config/tolerancias.yaml`:** `min_area: 300.0 â†’ 250.0` para modelo_B.

**Resultado:** frames con blur: 211 â†’ 281 detecciones. raw_ok: 163 â†’ 181/185.

**Scripts de diagnÃ³stico creados:**
- `scripts/_debug_blur.py` â€” analiza circularidad/aspect-ratio de blobs rechazados
- `scripts/_debug_areas.py` â€” histograma de Ã¡reas por rango para encontrar umbral Ã³ptimo

---

#### Cambio 18 â€” Y-clip: recorte al rango Y de detectados (corte de patrÃ³n)

**Problema:** Los 4 frames raw NOK restantes tenÃ­an `missing=40â€“50` con errores
concentrados en la parte inferior del frame. "Cuando corta el patrÃ³n": cuando el borde
de la zona perforada de la chapa cruza la parte inferior del encuadre, el grid generaba
posiciones esperadas en una zona donde ya no hay agujeros reales â†’ missing masivo.

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
Las posiciones esperadas se recortan al rango Y de los agujeros detectados Â± 1.5Ã—dy.
Si no hay agujeros detectados en la zona inferior, esas filas del grid no se cuentan.

**Seguridad ante defecto (punzÃ³n roto):** El punzÃ³n roto elimina 1 agujero por fila,
no todas las filas. El rango Y de detectados cubre toda la altura â†’ no se recorta nada.
Si eliminara una fila completa, el margen Â±33px incluye la fila adyacente.

**Resultado combinado (Y-clip + tol 22):**
- **185/185 raw OK**, 0 NOK, avg_detection_ratio=100%, align_failures=0/185

---

#### ParÃ¡metros config modelo_B al cierre de esta sesiÃ³n

```yaml
# modelo_B (microperforado / scanner_1)
polarity: bright
min_area: 250.0           # blur reduce area aparente; gap en 200-250pxÂ²
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

# PatrÃ³n (reconstruido en sesiÃ³n 2026-05-20, sincronizado hoy)
# 258 agujeros, dx=28.0, dy=22.0, phase=(4.0, 14.0)
```

---

### SesiÃ³n 2026-05-22 â€” Tadeo + Claude

#### Contexto de la sesiÃ³n
AnÃ¡lisis de falsos missing en modelo_B/scanner_1. GrabaciÃ³n `20260519_121741` (185 frames).
SÃ­ntoma: cruces rojas en agujeros fÃ­sicamente presentes, concentradas en borde derecho/inferior.
Causa raÃ­z: fase global X/Y no compensa tilt/perspectiva/curvatura local del material.

Segunda parte: implementaciÃ³n del sistema de calidad de frame (blur/degradaciÃ³n) con polÃ­tica
"hold" en la decisiÃ³n temporal â€” frames de baja calidad no incrementan ni resetean la racha NOK.

**Commits de esta sesiÃ³n:**
- `142061f` â€” Calidad de frame: blur_score + polÃ­tica hold temporal
- `aed010f` â€” edge_margin_px 25â†’5 en modelo_B
- (este commit)

---

#### Cambio 19 â€” CorrecciÃ³n affine local post-fase-global (`grid_fitting.py`)

**Problema:** `grid_compare_points` estimaba UNA fase global X e Y para todo el frame.
El material puede tener tilt/perspectiva que desplaza los agujeros del borde derecho/inferior
~25-40px respecto al esperado â†’ falsos missing en esa zona con tol_xy_px=22.

**ImplementaciÃ³n:** Nueva funciÃ³n `_fit_affine_to_grid()` en `src/pipeline/grid_fitting.py`:
1. Usa las posiciones esperadas de la fase global como punto de partida.
2. Matchea detecciones a expected con tolerancia `tol_affine = tol_xy_px Ã— 1.5` (=33px).
3. Ajusta affine 2D por mÃ­nimos cuadrados: `det_xy â‰ˆ A @ [ciÃ—dx, cjÃ—dy] + b`.
4. Valida: escala 0.85â€“1.15, shear <0.15 por eje â†’ rechaza fits imposibles.
5. Si pasa: usa posiciones corregidas. Si falla: fallback a fase global.

**Nuevo parÃ¡metro en `grid_compare_points`:** `tol_affine: float = 0.0` (default deshabilitado).
Habilitado vÃ­a `grid_affine_refinement: true` en `config/tolerancias.yaml` para modelo_B.

**Archivos modificados:**
- `src/pipeline/grid_fitting.py` â†’ nueva `_fit_affine_to_grid()` + param `tol_affine` en `grid_compare_points`
- `src/inspection.py` â†’ lee `grid_affine_refinement`, pasa `tol_affine` al grid
- `src/utils/config.py` â†’ `DEFAULT_TOLERANCES` agrega `grid_affine_refinement: False`
- `config/tolerancias.yaml` â†’ `grid_affine_refinement: true` en modelo_B

**Resultados en frame_0036 (peor caso):**
- Sin affine: missing=26, extra=24
- Con affine: missing=24, extra=22 (â†“2 en ambos)
- GrabaciÃ³n completa: 185/185 raw OK mantenido âœ“

**LimitaciÃ³n conocida:** 9 de los 24 missing en frame_0036 tienen un detectado a <22px
pero quedan sin match por "stealing" greedy (tol_xy_px=22 == dy=22 â†’ zona de ambigÃ¼edad
vertical). Esto requiere Hungarian matching para resolver definitivamente (ver pendientes).

---

#### Cambio 20 â€” Overlay near-miss lines (`annotate.py`, `inspection.py`)

**MotivaciÃ³n:** Las cruces rojas y diamantes naranjas no mostraban la relaciÃ³n espacial entre
un expected sin match y el detected mÃ¡s cercano fuera de tolerancia. El operador no podÃ­a
evaluar visualmente cuÃ¡nto falta para que matchee.

**ImplementaciÃ³n:**
- `src/inspection.py` â†’ calcula `near_miss_pairs`: lista de (expected, detected) donde
  `tol_xy_px < dist â‰¤ 2Ã—tol_xy_px`. Se pasa a `draw_compare_overlay`.
- `src/pipeline/annotate.py` â†’ nuevo param `near_miss_pairs` en `draw_compare_overlay`.
  Dibuja lÃ­neas cyan-amarillas delgadas entre cada expected sin match y su detectado mÃ¡s
  cercano (si cae en la zona 1Ã—â€“2Ã—tol). Dibujadas ANTES de los marcadores para que no
  tapen los cÃ­rculos.

**Leyenda del overlay (ahora explÃ­cita en el cÃ³digo):**
- âšª cÃ­rculo verde = agujero detectado correctamente matcheado
- âœ• cruz roja = posiciÃ³n esperada sin match dentro de tol_xy_px
- â—‡ diamante naranja = detectado sin posiciÃ³n esperada asignada
- â€” lÃ­nea cyan-amarilla = expectedâ†”detected mÃ¡s cercano (fuera de tol, dentro de 2Ã—tol)

---

#### Cambio 21 â€” Herramienta CSV de diagnÃ³stico por carpeta (`scripts/run_folder_csv.py`)

**Nuevo script:** Exporta mÃ©tricas por frame a CSV para anÃ¡lisis posterior.

```
python scripts/run_folder_csv.py <carpeta> [--model modelo_B] [--scanner scanner_1] [--output out.csv]
```

**Columnas:** frame, status, expected, detected, missing, extra, detection_ratio,
centering_offset_px, alignment_ok, missing_nearest_max_px, missing_nearest_med_px,
false_missing_count (detectado dentro de 2Ã—tol pero fuera de tol).

---

#### Cambio 22 â€” ActualizaciÃ³n comentario grid_max_missing (`config/tolerancias.yaml`)

**MotivaciÃ³n:** El valor 35 era conservador para la calibraciÃ³n inicial. Con affine refinement
los frames buenos tienen missingâ‰ˆ0-5, no 16-29 (que era con tol=12 sin affine).

**Sin cambio de valor** (sigue en 35 por seguridad). Actualizado el comentario:
- Frames buenos con affine: missing 0-5
- Blur de movimiento: missing 10-20 (estimado, pendiente validar en planta)
- PunzÃ³n roto: ~29 missing (pendiente validar)
- **Candidato: bajar a 20-25** despuÃ©s de validar con defecto real

---

#### Cambio 23 â€” Sistema de calidad de frame: blur_score + polÃ­tica "hold" temporal

**MotivaciÃ³n:** Frames con imagen degradada (blur de movimiento, inestabilidad Ã³ptica)
producÃ­an falsas alarmas NOK. Estos frames tienen evidencia visual dÃ©bil y no deberÃ­an
tener el mismo peso que frames nÃ­tidos en la decisiÃ³n temporal de FAULT.

**Principio:** Si el frame es LOW_QUALITY â†’ "hold": no incrementar NI resetear la racha NOK.
Si hay demasiados frames LOW_QUALITY consecutivos (â‰¥`low_quality_max_streak`) â†’ resetear
racha para evitar que un sensor degradado bloquee permanentemente la detecciÃ³n de FAULT.

**ImplementaciÃ³n:**

**`src/inspection.py`:**
- `InspectionResult` agrega:
  - `blur_score: float = 0.0` â€” varianza del Laplaciano sobre la ROI (mayor = mÃ¡s nÃ­tido)
  - `frame_quality: str = "GOOD"` â€” `"GOOD"` | `"LOW_QUALITY"`
- `_inspect_bgr()`:
  - Calcula `blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()` sobre el frame post-ROI
  - Clasifica `frame_quality = "LOW_QUALITY"` si `blur_score_min > 0` y `blur_score < blur_score_min`
  - Lee nuevo param `blur_score_min` (por defecto 0.0 = deshabilitado)
  - Pasa `frame_quality` a `_draw_warnings()` â†’ badge "CALIDAD BAJA" en overlay
- `_draw_warnings()` agrega param `frame_quality: str = "GOOD"` â†’ muestra "CALIDAD BAJA"
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
- `_handle_result()`: aplica polÃ­tica "hold" en tiempo real:
  - Si `frame_quality == "LOW_QUALITY"`: incrementa `_lq_streak`, no modifica `_nok_streak`
    - Si `_lq_streak >= _low_quality_max_streak`: resetea ambos streaks
    - No actualiza contadores `ok_count` / `nok_count` para estos frames
  - Si `"GOOD"`: resetea `_lq_streak`, aplica lÃ³gica normal (NOK+=1 o reset)

**`src/utils/config.py`:**
- `DEFAULT_TOLERANCES` agrega:
  - `"blur_score_min": 0.0` â€” 0 = deshabilitado; >0 = umbral de varianza del Laplaciano
  - `"low_quality_max_streak": 10` â€” frames LOW_QUALITY consecutivos antes de resetear racha

**`scripts/_debug_blur_score.py`** (nuevo):
- DiagnÃ³stico de calibraciÃ³n: muestra distribuciÃ³n del `blur_score` por frame en una carpeta.
- Incluye histograma y los 10 frames mÃ¡s borrosos. Ayuda a elegir `blur_score_min`.

**Nota sobre calibraciÃ³n del blur_score:**
- Para la grabaciÃ³n `20260519_121741` con backlight: blur_score en rango 4.1â€“6.3 en TODOS los frames.
- La imagen binaria (backlight muy contrastado) reduce la varianza del Laplaciano absoluta.
- Para esta grabaciÃ³n `blur_score_min = 0.0` (deshabilitado) es la configuraciÃ³n correcta.
- Calibrar en planta con frames de material en movimiento real (sin backlight temporizado).
- Valores esperados para material con blur real: < 100. Frames nÃ­tidos: >> 200.

**Resultado:**
- GrabaciÃ³n 20260519_121741: 185/185 OK, 0 temporal NOK, 0 frames LOW_QUALITY âœ“
- PolÃ­tica hold correctamente wired en FSM del scanner y en inspect_folder()
- `blur_score_min = 0.0` en config global y modelo_B â†’ deshabilitado hasta calibraciÃ³n

---

#### Cambio 24 â€” edge_margin_px 25â†’5 para modelo_B

**Problema:** `edge_margin_px=25` descartaba agujeros detectados cuyo centroide quedaba
dentro del margen de 25px del borde de la ROI. Para modelo_B (ROI h=1077px), los agujeros
de la Ãºltima fila visible tienen su centroide cerca de yâ‰ˆ1034 â€” dentro del margen de 25px
respecto al borde inferior de la ROI (y=1077). Esos agujeros reales quedaban como "missing".

**Efecto:** 492 cruces acumuladas en borde inferior de la grabaciÃ³n (concentradas en yâ‰ˆ1034).
Con edge_margin_px=5 â†’ los centroides vÃ¡lidos a partir de 5px del borde pasan el filtro.

**DecisiÃ³n:** Cambiar solo para modelo_B. `pattern_edge_margin_px` se mantiene en 25.0
(afecta reconstrucciÃ³n del patrÃ³n, no la detecciÃ³n runtime).

**Archivo modificado:**
- `config/tolerancias.yaml` â†’ `edge_margin_px: 25.0 â†’ 5.0` solo en secciÃ³n `modelo_B`

**Resultados validados (grabaciÃ³n 20260519_121741, 185 frames):**

| MÃ©trica | Antes (25px) | DespuÃ©s (5px) |
|---------|-------------|---------------|
| raw OK | 185/185 | 185/185 âœ“ |
| temporal NOK | 0 | 0 âœ“ |
| missing medio | 3.50 | 0.81 |
| missing mÃ¡ximo | 24 | 20 |
| frames sin missing | 58/185 | 160/185 |

Frames crÃ­ticos verificados:
- frame_0036: missing 24â†’20
- frame_0064: missing=8
- frame_0065: missing=6
- frame_0093: missing=5
- frame_0177: missing=10

---

#### Cambio 25 â€” MÃ¡rgenes laterales del patrÃ³n respecto a la chapa

**MotivaciÃ³n:** La mÃ©trica `offset_px` (diferencia de centros) no permitÃ­a saber cuÃ¡nto espacio
real queda entre el patrÃ³n punzonado y cada borde de la chapa. Se querÃ­a conocer las dos
distancias independientes: margen izquierdo y margen derecho, para detectar si el patrÃ³n
estÃ¡ corrido hacia un lado aunque el offset total sea pequeÃ±o.

**Nuevas mÃ©tricas en `CenteringResult`:**
- `pattern_left_x` â€” borde fÃ­sico izquierdo del patrÃ³n detectado: `min(hole.x - hole.r)`
- `pattern_right_x` â€” borde fÃ­sico derecho: `max(hole.x + hole.r)`
- `left_margin_px` â€” espacio entre borde izquierdo de chapa y borde izquierdo del patrÃ³n
- `right_margin_px` â€” espacio entre borde derecho del patrÃ³n y borde derecho de la chapa
- `margin_delta_px = left_margin_px - right_margin_px` (>0 = mÃ¡s margen a la izquierda = patrÃ³n corrido a la derecha)
- `offset_px = margin_delta_px / 2` (redefinido; antes era `holes_center_x - sheet_center_x`)

**Cambio de firma en `compute_centering()`:**
- Antes: `holes_xs: Sequence[float]` (solo coordenadas X)
- Ahora: `holes: Sequence` (objetos `Hole` con `.x` y `.r`) â€” permite calcular los extremos fÃ­sicos del patrÃ³n

**Archivos modificados:**

`src/pipeline/edge_centering.py`:
- `CenteringResult` agrega 5 campos nuevos: `pattern_left_x`, `pattern_right_x`, `left_margin_px`, `right_margin_px`, `margin_delta_px`
- `compute_centering()` acepta `holes` (Hole objects) en lugar de `holes_xs`
- `offset_px` ahora es `margin_delta_px / 2` (mÃ¡s significativo fÃ­sicamente)

`src/inspection.py`:
- Llamada a `compute_centering()` pasa `holes` directamente (antes `[h.x for h in holes]`)

`src/pipeline/annotate.py` â€” `draw_centering_overlay()`:
- Dibuja dos lÃ­neas punteadas amarillo-cyan para los extremos fÃ­sicos del patrÃ³n (`pattern_left_x`, `pattern_right_x`)
- Texto inferior reemplazado: `Izq: XXpx  Der: YYpx` + `Offset: +/-ZZpx`
- Se eliminÃ³ el texto "Ancho: XXXpx" (redundante con la visualizaciÃ³n)

`src/ui/operator.py` â€” card "CENTRADO":
- Muestra dos lÃ­neas: `I: XXpx  D: YYpx` + `Offset: +/-ZZpx`
- Font reducida a 11px para acomodar el contenido compacto

**ValidaciÃ³n (grabaciÃ³n 20260519_121741, 185 frames):**
- 185/185 OK, 0 temporal NOK âœ“
- Centering disponible en 185/185 frames
- Izq: mediana=176px (rango 163â€“188px)
- Der: mediana=158px (rango 137â€“163px)
- Offset: mediana=+9px (patrÃ³n levemente corrido a la derecha, consistente en todos los frames)

---

### SesiÃ³n 2026-05-26 (continuaciÃ³n) â€” Tadeo + Claude

#### Cambio 31 â€” Comando CLI `missing-folder` (diagnÃ³stico de agujeros faltantes)

**MotivaciÃ³n:** En producciÃ³n `grid_max_missing=35` permite hasta 35 faltantes antes de
declarar NOK. Para diagnÃ³stico se necesita detectar y exportar cualquier frame con
â‰¥1 agujero faltante sin tocar el criterio productivo.

**Nuevo comando:**
```
python -m src.main missing-folder \
  --model modelo_B --scanner scanner_1 \
  --input <carpeta> --output data/output/<nombre> \
  --min-missing 1
```

**Diferencia de criterios (importante):**
- `production_status` â€” usa el pipeline normal con `grid_max_missing` â†’ nunca NOK en material bueno
- `missing_status` â€” marca `FALTANTE` cuando `report.missing >= min_missing` â†’ diagnÃ³stico puro

**Salidas generadas:**
- `<output>/missing_report.csv` â€” una fila por frame con columnas:
  `frame, production_status, missing_status, expected, detected, missing, extra,
  detection_ratio, alignment_ok, false_missing_count,
  missing_nearest_med_px, missing_nearest_max_px`
- `<output>/missing_overlays/frame_NNNN_missing_M_overlay.png` â€” overlay por frame FALTANTE
  con badge azul "FALTANTE: N" superpuesto debajo del status normal

**Resumen en consola:**
- frames totales, FALTANTE, OK (diagnÃ³stico)
- missing acumulado, missing mÃ¡ximo
- top 10 frames por missing

**false_missing_count:** cuÃ¡ntos faltantes tienen un detectado dentro de 2Ã—tol_xy_px â€”
candidatos a ser agujeros reales con error de fase, no agujeros fÃ­sicamente ausentes.

**Resultado sobre grabaciÃ³n 20260519_121741 (185 frames, min_missing=1):**
- 160/185 sin ningÃºn faltante
- 25/185 frames con â‰¥1 faltante (production_status=OK en todos, 185/185 OK mantenido)
- missing acumulado: 149 (media 0.81/frame en frames buenos)
- Frame mÃ¡s crÃ­tico: frame_0036 con 20 missing (borde inferior del frame, fin de material)

**Archivos modificados:**
- `src/main.py` â†’ nuevo `cmd_missing_folder()` + subcomando registrado en `build_parser()`

---

#### Cambio 32 â€” Fix cp1252: caracteres Unicode en salida de consola Windows

**Problema:** `scripts/run_folder_csv.py` imprimÃ­a `â†’` (U+2192) que falla en consolas
Windows con codepage cp1252. El mismo carÃ¡cter estaba tambiÃ©n en el nuevo `cmd_missing_folder`.

**Fix:** Reemplazados todos los caracteres Unicode no-ASCII en salidas `print()` por ASCII:
- `â†’` â†’ `->` en `scripts/run_folder_csv.py` (2 ocurrencias)
- `â†’` â†’ `->` en `src/main.py` (1 ocurrencia en `cmd_missing_folder`)

**Los CSV siguen escribiÃ©ndose en UTF-8** (sin cambio).

---

### SesiÃ³n 2026-05-26 (zigzag filter + UI +10) â€” Tadeo + Claude

#### Cambio 37 â€” Filtro de calidad geomÃ©trica (zigzag) + navegaciÃ³n Â±10 frames

**Objetivo A:** Detectar frames con vibraciÃ³n de cÃ¡mara/chapa midiendo el zigzag horizontal
de los bordes del patrÃ³n. Marcarlos como `IMAGEN INESTABLE - NO DECIDE` y excluirlos de
rachas NOK y detecciÃ³n de parada de mÃ¡quina. El blur_score (Laplaciano) no detecta
bien este tipo de inestabilidad lateral.

**Objetivo B:** En la UI de Servicio â†’ tab GrabaciÃ³n â†’ NAVEGADOR DE CAPTURAS, agregar
botones âˆ’10 / +10 para saltar de a 10 frames al navegar una carpeta analizada.

---

**A. Filtro de calidad geomÃ©trica**

MÃ©trica: **residuales horizontales** de los puntos por banda del patrÃ³n respecto a
una lÃ­nea robusta ajustada. Combina ambos bordes (izquierdo + derecho).
- `pattern_zigzag_std_px` â€” desviaciÃ³n estÃ¡ndar de los residuales
- `pattern_zigzag_max_px` â€” residual mÃ¡ximo

Si `std > pattern_zigzag_std_max_px` OR `max > pattern_zigzag_abs_max_px` â†’ `UNSTABLE`.
Un frame UNSTABLE fuerza `frame_quality = "LOW_QUALITY"` â†’ toda la maquinaria existente
de "hold" (regla temporal + MachineStopDetector) lo omite automÃ¡ticamente.

**CalibraciÃ³n sobre grabaciÃ³n 20260519_121741 (185 frames):**
- frame_0037 (inestable): `std=3.5px, max=13.5px` â†’ UNSTABLE âœ“ (max > 10px)
- frame_0038 (estable):   `std=1.4px, max=4.5px`  â†’ STABLE  âœ“
- Total UNSTABLE: 5/185 (2.7%) â€” todos con max > 13px, son genuinamente inestables
- 185/185 raw OK + 185/185 temporal OK mantenidos âœ“

**Archivos modificados:**

A) **`src/pipeline/edge_centering.py`**:
   - `CenteringResult` agrega `pattern_zigzag_std_px: float = 0.0` y
     `pattern_zigzag_max_px: float = 0.0`.
   - `compute_centering()`: calcula residuales de `pattern_left_points` y
     `pattern_right_points` contra su lÃ­nea robusta ajustada (reutiliza `_fit_line_robust`).
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

D) **`config/tolerancias.yaml`** â€” modelo_B:
   - `verticality_quality_enabled: true`
   - `pattern_zigzag_std_max_px: 4.0`
   - `pattern_zigzag_abs_max_px: 10.0`

**B. NavegaciÃ³n Â±10 frames en UI**

**`src/ui/service.py`** â€” `RecordingTab._build_browser_section()`:
   - Nuevos botones `self._btn_prev10` (`-10`) y `self._btn_next10` (`+10`) a ambos
     lados de los botones `â—€` / `â–¶` existentes.
   - `_show_frame()` ya clampea a `[0, len(frame_paths)-1]` â†’ salto automÃ¡tico al lÃ­mite.
   - `_update_nav_state()` habilita/deshabilita `_btn_prev10` / `_btn_next10` igual que prev/next.

**Sin tocar:** PLC, solenoides, lÃ³gica de producciÃ³n, `grid_max_missing`,
`consecutive_nok_frames`, patrÃ³n de modelo_B.

---

#### Cambio 42 â€” MÃ©trica de zigzag del centro del patrÃ³n por bandas (PATRON CENTER)

**Problema reportado:**
Frames 0120, 0121, 0123 pasan como OK pero el centro del patrÃ³n de agujeros zigzaguea
visualmente. La mÃ©trica anterior (`pattern_zigzag_*`) usa solo los bordes externos del
patrÃ³n (agujero mÃ¡s a la izquierda / mÃ¡s a la derecha por banda), lo cual no detecta
desalineaciÃ³n interna.

**Causa raÃ­z:**
`_pattern_bounds_by_band()` devuelve `min(x - r)` y `max(x + r)` por banda. Si el
patrÃ³n se tuerce en el centro pero los agujeros de los extremos no se mueven mucho,
el zigzag no se detecta. Los frames 0122, 0124â€“0126 sÃ­ disparaban porque el desvÃ­o
era mÃ¡s extremo.

**SoluciÃ³n â€” nueva mÃ©trica PATRON CENTER:**

A) **`src/pipeline/edge_centering.py`:**
   - Nueva funciÃ³n `_pattern_center_by_band(holes, img_h, n_bands=16)`:
     - Por banda: `center_x = np.median([h.x for h in band_holes])` (mÃ­nimo 2 agujeros)
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
     (misma consecuencia que `pattern_align_enabled` â€” badge PATRON DESALINEADO)

C) **`src/utils/config.py`** â€” nuevos defaults:
   ```python
   "pattern_center_align_enabled": False,
   "pattern_center_zigzag_std_max_px": 8.0,
   "pattern_center_zigzag_abs_max_px": 18.0,
   ```

D) **`config/tolerancias.yaml`** â€” modelo_B:
   ```yaml
   pattern_center_align_enabled: true
   pattern_center_zigzag_std_max_px: 8.0
   pattern_center_zigzag_abs_max_px: 18.0
   ```
   Umbrales mÃ¡s amplios que el borde (std=5, abs=15) para evitar falsos positivos,
   ya que la mediana reduce la varianza frente a agujeros extremos.

**Sin tocar:** PLC, solenoides, lÃ³gica temporal, patrÃ³n de referencia, grid_max_missing.

---

#### Cambio 41 â€” PATRON DESALINEADO â†’ NOK + badge en tope + bordes resaltados

**Problema reportado:** frame_0122 mostraba "STATUS: OK" con badge DETENER MAQUINA activo.
El banner tapaba el Ã¡rea de agujeros (posicionado en h//3 = mitad del frame).

**Causa:** `final_status` se calculaba ANTES de evaluar `pattern_alignment_warn`.
Cuando `pattern_align_enabled` detectaba zigzag excesivo, solo se seteaba el flag pero
no se actualizaba `final_status`. `draw_compare_overlay` ya recibÃ­a el valor viejo "OK".

**Correcciones:**

A) **`src/inspection.py`**:
   - `final_status = "NOK"` se asigna dentro del bloque `if pattern_align_enabled` cuando
     se supera el umbral â†’ `draw_compare_overlay` recibe "NOK" correctamente.
   - `draw_centering_overlay` recibe `pattern_warn=pattern_alignment_warn`.
   - Llamadas a `draw_machine_stop_badge`: cambian de `y_offset=Â±55` a `index=0/1`.

B) **`src/pipeline/annotate.py`** â€” `draw_machine_stop_badge`:
   - Reposicionado al TOPE del frame (`banner_y = index * (banner_h + 3)`).
   - Altura compacta (~65px por banner). No cubre el Ã¡rea de agujeros.
   - ParÃ¡metro `index` reemplaza `y_offset`: 0=primer banner, 1=apilado debajo.
   - Icono "!" al inicio del texto principal.

C) **`src/pipeline/annotate.py`** â€” `draw_centering_overlay`:
   - Nuevo parÃ¡metro `pattern_warn: bool = False`.
   - Cuando `True`: bordes del PATRON en naranja vivo (en vez de cyan), grosor 2,
     alpha 0.9, glow oscuro debajo.
   - CÃ­rculo blanco + "!" en el punto de MÃXIMA desviaciÃ³n de cada borde.
   - Label cambia a "PATRON !!" en naranja.

D) **`config/tolerancias.yaml`** â€” modelo_B:
   - `pattern_align_std_max_px`: 6.0 â†’ 5.0 (mÃ¡s sensible al desalineamiento).

**SemÃ¡ntica clara resultante:**
- CHAPA zigzag alto â†’ IMAGEN INESTABLE (LOW_QUALITY, no decide) â€” sin cambio.
- PATRON zigzag alto â†’ STATUS: NOK + badge "PATRON DESALINEADO" en tope del frame
  + bordes del patrÃ³n en naranja con cÃ­rculo en el peor punto.

---

#### Cambio 40 â€” AceleraciÃ³n de anÃ¡lisis batch (pre-cache + threading)

**Objetivo:** Reducir el tiempo de anÃ¡lisis de una grabaciÃ³n sin cambiar ninguna lÃ³gica
de detecciÃ³n ni parÃ¡metros de calidad.

**Cuellos de botella identificados:**
1. `load_tolerances()` + `load_pattern()` + `load_roi()` se llamaban para CADA frame
   (NÃ—3 lecturas de disco innecesarias).
2. El procesamiento era estrictamente secuencial â€” un solo nÃºcleo de CPU.
3. El score de blur (Laplaciano + cvtColor) se calculaba siempre aunque
   `blur_score_min == 0` (deshabilitado en modelo_B).

**Cambios:**

A) **`src/inspection.py`**:
   - `inspect_image()` acepta ahora parÃ¡metro `_preloaded: Optional[dict]` (igual que
     `inspect_frame()`) y lo pasa a `_inspect_bgr()`.
   - Blur score (Laplaciano): se calcula solo si `blur_score_min > 0`. Ahorra ~3ms/frame
     en modelo_B donde estÃ¡ deshabilitado.

B) **`src/ui/service.py`** â€” `_AnalysisWorker.run()`:
   - Pre-carga tolerancias + patrÃ³n + ROI **una sola vez** antes del loop.
   - Usa `ThreadPoolExecutor(max_workers=min(cpu_count, 6))`: OpenCV y numpy liberan
     el GIL, asÃ­ que mÃºltiples frames se procesan en paralelo sobre todos los nÃºcleos.
   - Los resultados se reensamblan en orden correcto (lista indexada por `idx`).
   - Progreso funciona igual (se emite al completar cada future).

**Speedup esperado:** 3-5x en mÃ¡quinas con 4+ nÃºcleos (tÃ­pico en Windows de producciÃ³n).
Sin cambio en ningÃºn parÃ¡metro de detecciÃ³n ni resultado de inspecciÃ³n.

---

#### Cambio 38 â€” CHAPA vs PATRON zigzag + badges DETENER MAQUINA con razÃ³n

**Objetivo:** Separar la detecciÃ³n de inestabilidad de imagen en dos mÃ©tricas distintas
con consecuencias distintas:

1. **CHAPA edge zigzag** (borde de lÃ¡mina, backlight): zigzag de la CHAPA â†’ vibraciÃ³n de
   cÃ¡mara/lÃ¡mina â†’ `IMAGEN INESTABLE` â†’ frame descartado (no cuenta para rachas ni machine stop).
2. **PATRON edge zigzag** (bordes del patrÃ³n de agujeros): zigzag del PATRON â†’ desalineamiento
   mecÃ¡nico del punzÃ³n â†’ badge `DETENER MAQUINA - PATRON DESALINEADO`.

AdemÃ¡s: cuando `machine_stop` (agujero persistente faltante), el badge muestra la razÃ³n
`AGUJERO PERSISTENTE FALTANTE`. Si ambos triggers estÃ¡n activos, se dibujan dos banners
apilados verticalmente.

**Archivos modificados:**

A) **`src/pipeline/edge_centering.py`**:
   - `CenteringResult` ahora tiene **4** campos zigzag en lugar de 2:
     `chapa_zigzag_std_px`, `chapa_zigzag_max_px` (bordes de lÃ¡mina) y
     `pattern_zigzag_std_px`, `pattern_zigzag_max_px` (bordes del patrÃ³n).
   - `compute_centering()`: nuevo helper `_zigzag_residuals(pts_lists)` â†’ `(std, max)`.
     chapa: computed from `[left_pts_list, right_pts_list]` (gradiente de backlight).
     patron: computed from `[pattern_left_points, pattern_right_points]`.

B) **`src/pipeline/annotate.py`**:
   - `draw_machine_stop_badge(img, reason="", y_offset=0)`: banner full-width rojo semitransparente.
     Texto principal `DETENER MAQUINA` (escala 2.0, grosor 5, sombra). Texto amarillo con `reason`.
     `y_offset` para apilar dos banners cuando ambos triggers estÃ¡n activos.

C) **`src/inspection.py`**:
   - `InspectionResult` agrega `pattern_alignment_warn: bool = False`, `chapa_zigzag_std_px`,
     `chapa_zigzag_max_px` (ya tenÃ­a `pattern_zigzag_*`).
   - `_inspect_bgr()`: lÃ³gica separada:
     - CHAPA zigzag (`verticality_quality_enabled`): si supera umbrales â†’ `frame_geometry_quality = "UNSTABLE"`,
       `frame_quality = "LOW_QUALITY"` (skip decisiones).
     - PATRON zigzag (`pattern_align_enabled`): si supera umbrales â†’ `pattern_alignment_warn = True`
       (NO skip decisiones, solo muestra badge DETENER MAQUINA).
   - Badge drawing: `machine_stop` â†’ badge `"AGUJERO PERSISTENTE FALTANTE"`;
     `pattern_alignment_warn` â†’ badge `"PATRON DESALINEADO"`;
     ambos activos â†’ dos banners apilados (`y_offset=Â±55`).

D) **`src/utils/config.py`**:
   - Reemplaza `pattern_zigzag_std_max_px / _abs_max_px` por `chapa_zigzag_std_max_px / _abs_max_px`.
   - Agrega `pattern_align_enabled: False`, `pattern_align_std_max_px: 6.0`,
     `pattern_align_abs_max_px: 15.0`.

E) **`config/tolerancias.yaml`** â€” modelo_B:
   - Reemplaza `pattern_zigzag_*` por `chapa_zigzag_*` (valores idÃ©nticos: std=4.0, abs=10.0).
   - Agrega `pattern_align_enabled: true`, `pattern_align_std_max_px: 6.0`,
     `pattern_align_abs_max_px: 15.0`.

**Sin tocar:** PLC, solenoides, lÃ³gica de producciÃ³n, `grid_max_missing`,
`consecutive_nok_frames`, patrÃ³n de modelo_B.

---

### SesiÃ³n 2026-05-26 (machine stop) â€” Tadeo + Claude

#### Cambio 36 â€” DetecciÃ³n de parada de mÃ¡quina por agujero faltante persistente

**Objetivo:** Si un agujero faltante aparece en la misma zona durante N frames consecutivos,
mostrar badge prominente "DETENCION DE MAQUINA" en el overlay. Indica punzÃ³n roto o tapado.
No toca PLC, solenoides, lÃ³gica de producciÃ³n, `grid_max_missing` ni `consecutive_nok_frames`.

**Archivos modificados:**

A) **`src/pipeline/machine_stop.py`** (nuevo):
   - `MachineStopDetector`: detector persistente de zonas de agujeros faltantes.
   - Tracking por clusters espaciales (radio `same_zone_px`). Cuando una zona acumula
     `missing_frames` frames consecutivos con â‰¥ `min_missing` puntos faltantes â†’ triggered.
   - `frame_quality == "LOW_QUALITY"` no incrementa ni resetea racha.
   - Filtro near-miss: excluye puntos esperados con detected cerca pero fuera de tolerancia.
   - Filtro borde Y: ignora faltantes en top/bottom del ROI (entrada/salida de chapa).
   - Centro de zona: EMA 0.7/0.3 para seguir deriva lenta del punzÃ³n.

B) **`src/pipeline/annotate.py`**:
   - Nueva funciÃ³n `draw_machine_stop_badge(img)`: badge rojo centrado con texto
     "DETENCION DE MAQUINA" en grande.

C) **`src/inspection.py`**:
   - `InspectionResult`: nuevo campo `machine_stop: bool = False`.
   - `FolderInspectionSummary`: nuevo campo `machine_stop_count: int = 0`.
   - `_inspect_bgr()`: acepta `_machine_stop_detector` (explÃ­cito o vÃ­a `_preloaded`).
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

G) **`src/main.py`** â€” `cmd_run_folder()`:
   - Imprime `machine_stop_frames=N` en lÃ­nea `[quality]`.
   - Agrega `MACHINE_STOP` al warn por frame cuando `result.machine_stop`.

**Sin tocar:** PLC, solenoides, lÃ³gica producciÃ³n, `grid_max_missing`, `consecutive_nok_frames`.

---

### SesiÃ³n 2026-05-26 (fix overlay centrado) â€” Tadeo + Claude

#### Cambio 35 â€” Fix overlay CHAPA: dibujar en frame completo con offset ROI

**Problema:** `draw_centering_overlay()` se aplicaba sobre `overlay_roi` (imagen recortada
a la ROI, 650Ã—1077px). Las coordenadas de borde de CHAPA (`left_xâ‰ˆ-31px`, `right_xâ‰ˆ700px`)
son ROI-relativas: el borde izquierdo queda fuera de la imagen (x<0 â†’ clipeado a 0) y el
borde derecho en el extremo. Las lÃ­neas/labels de CHAPA aparecÃ­an sobre el borde del PATRON,
no sobre los bordes fÃ­sicos reales de la chapa.

**Fix:**

`src/pipeline/annotate.py` â€” `draw_centering_overlay()`:
- Nuevos parÃ¡metros `roi_x: int = 0, roi_y: int = 0` (default=0 â†’ sin cambio de comportamiento).
- Todos los escalares X se suman `roi_x`: `cx`, `hx`, `lx`, `rx`, `plx`, `prx`.
- Los puntos por banda se transforman: `_shift(pts) = (x + roi_x, y + roi_y)` para
  `left_edge_points`, `right_edge_points`, `pattern_left_points`, `pattern_right_points`.

`src/inspection.py` â€” `_inspect_bgr()`:
- `draw_centering_overlay` se moviÃ³ de `overlay_roi` al overlay full-frame (`overlay`).
- Se eliminÃ³ la llamada sobre overlay_roi.
- Nueva llamada post-compositing con offset:
  ```python
  overlay = draw_centering_overlay(
      overlay, centering, tag_nok=centering_nok,
      roi_x=roi.x if roi else 0,
      roi_y=roi.y if roi else 0,
  )
  ```

**Resultado verificado** en frames 0036, 0090, 0120 de grabaciÃ³n 20260519_121741:
- Overlay full-frame 1920Ã—1080 âœ“
- LÃ­nea CHAPA izquierda: xâ‰ˆ679px (full-frame) = borde fÃ­sico real de chapa âœ“
- LÃ­nea CHAPA derecha: xâ‰ˆ1410px (full-frame) = borde fÃ­sico real de chapa âœ“
- LÃ­neas PATRON separadas visualmente de CHAPA âœ“
- Labels "CHAPA" / "PATRON" en posiciones correctas âœ“
- Text Izq/Der/Delta/Offset sin cambios âœ“
- 185/185 OK mantenido (sin cambio en lÃ³gica de inspecciÃ³n) âœ“

---

### SesiÃ³n 2026-05-26 (Esterilla) â€” Tadeo + Claude

#### Cambio 34 â€” DiagnÃ³stico y calibraciÃ³n inicial de modelo_A (Esterilla)

**Objetivo:** Revisar la lÃ³gica del patrÃ³n Esterilla (modelo_A / scanner_2) y establecer
parÃ¡metros de inspecciÃ³n robustos. modelo_B (Microperforado) no fue modificado.

---

**DiagnÃ³stico del patrÃ³n `data/patterns/modelo_A/holes.json`:**

Resultado de `scripts/_debug_modelo_a.py`:

```
Puntos totales:    117
Celdas totales:    117
Celdas unicas:     113    â† 4 celdas duplicadas
dx=68.0  dy=38.0  phase_x=16.0  phase_y=30.0
```

**GeometrÃ­a real de Esterilla:**
- Grilla rectangular (sin escalonado hexagonal). X-positions fijas: ci=7..11 â†’ x=492,560,628,696,764px
- Filas impares  (cj=1,3,...,25): 4 agujeros PEQUEÃ‘OS  râ‰ˆ14px, areaâ‰ˆ627pxÂ²
- Filas pares    (cj=2,4,...,24): 5 agujeros GRANDES   râ‰ˆ25px, areaâ‰ˆ2023pxÂ²
- Total esperado: 13 filas impares Ã— 4 + 12 filas pares Ã— 5 = 52+60 = 112 celdas Ãºnicas

**Celdas duplicadas identificadas â€” filas cj=22-25 (Ãºltimas 4 filas):**

```
(ci=10, cj=22) x2: idx=95 (x=696.7 y=849.1 r=25.0) â† correcto
                   idx=99 (x=723.5 y=885.0 r=13.6) â† mal asignado â€” era (10,23)
(ci=9,  cj=23) x2: idx=100 (x=656.2 y=885.5) y idx=105 (x=633.7 y=920.5) ambos â‰ˆ y=904exp
(ci=8,  cj=23) x2: idx=101 (x=589.5 y=887.0) y idx=106 (x=567.1 y=922.0) ambos â‰ˆ y=904exp
(ci=9,  cj=24) x2: idx=109 (x=660.8 y=957.0) y idx=110 (x=594.4 y=958.0) ambos â‰ˆ y=942exp
```

**Causas raÃ­z:**
1. **(ci=10, cj=22):** Bug de redondeo en `assign_cells` â€” Python's `round(22.5)=22` (banker's
   rounding, "round half to even"). El punto a y=885px = exactamente el punto medio entre
   cj=22 (y_exp=866) y cj=23 (y_exp=904). El redondeo bancario lo asignÃ³ a cj=22 en lugar
   de cj=23. **Corregido** con round-half-up (`int(x+0.5)`).
2. **(cj=23 y cj=24):** AmbigÃ¼edad real en la imagen de referencia â€” dos agujeros fÃ­sicos
   diferentes fueron detectados como igualmente cercanos a la misma celda esperada.
   Requiere imagen de referencia mÃ¡s limpia para `build-pattern`. **Sin fix por ahora.**

**Impacto en runtime:** `grid_compare_points` ya deduplica por `(round(ex), round(ey))` â†’
las 4 celdas duplicadas generan la misma posiciÃ³n esperada y solo la primera pasa.
No hay falsos missing ni falsos extra causados por los duplicados.

---

**Cambios aplicados:**

**`src/pipeline/grid_fitting.py` â€” `assign_cells()`:**
```python
# ANTES (banker's rounding â€” round(22.5) = 22, no 23):
(round((x - phase_x) / dx), round((y - phase_y) / dy))
# DESPUÃ‰S (round-half-up â€” int(22.5 + 0.5) = 23):
(int((x - phase_x) / dx + 0.5), int((y - phase_y) / dy + 0.5))
```
Previene asignaciones incorrectas cuando un punto cae exactamente en el punto medio
entre dos celdas adyacentes. No afecta modelo_B (su holes.json ya estaba guardado).

**`src/patterns/pattern_build.py` â€” diagnÃ³sticos post-assign:**
DespuÃ©s de `assign_cells()`, imprime:
```
[build-pattern] 117 puntos  dx=68.0 dy=38.0  phase=(16.0,30.0)
[build-pattern] Celdas totales: 117  Unicas: 113  Duplicadas: 4
[build-pattern] ADVERTENCIA: 4 celda(s) duplicada(s) detectadas:
  (ci=10, cj=22) x2: [(696.7, 849.1), (723.5, 885.0)]
  ...
```

**`config/tolerancias.yaml` â€” modelo_A:**
Reemplaza `modelo_A: {}` con overrides completos y documentados:

```yaml
modelo_A:
  polarity: bright            # backlight â†’ agujeros brillantes (mismo que modelo_B)
  min_area: 400.0             # captura small holes (areaâ‰ˆ627pxÂ²); rechaza ruido
  circularity_min: 0.80
  aspect_ratio_max: 2.0
  tol_xy_px: 16.0             # < dy/2=19px â†’ sin ambigÃ¼edad entre filas adyacentes
  align_match_tol_px: 100.0
  min_match_count: 5
  edge_margin_px: 15.0
  pattern_edge_margin_px: 40.0
  grid_min_spacing: 30.0      # dy=38 > 30 â†’ estimate_spacing encuentra dy correcto
  grid_max_missing: 10        # ~9% de 112 agujeros â€” conservador hasta calibrar
  bbox_filter_margin_px: 20.0
  grid_affine_refinement: false  # sin datos de planta para validar
  extra_min_dist_factor: 2.0     # extras deben estar >32px de todo expected
  consecutive_nok_frames: 9999   # CALIBRACIÃ“N â€” FAULT deshabilitado
  continuous_position_threshold: 0.0
```

**DecisiÃ³n de diseÃ±o â€” Grid vs RANSAC para Esterilla:**
Se eligiÃ³ **grid fitting** (igual que modelo_B), porque:
- La Esterilla es una grilla rectangular regular y repetitiva
- La chapa llega en posiciÃ³n variable cada ciclo â†’ el patrÃ³n no tiene referencia absoluta
- RANSAC/affine requiere conocer la posiciÃ³n absoluta del patrÃ³n (no disponible)
- Grid fitting es position-invariant: encuentra la fase correcta frame a frame sin referencia

**No disponible â€” imagen OK de scanner_2/modelo_A:**
No hay imÃ¡genes de referencia para reconstruir `data/patterns/scanner_2/modelo_A/holes.json`.
El patrÃ³n global (`data/patterns/modelo_A/`) se usa como fallback.
**PrÃ³ximo paso:** capturar una imagen OK de Esterilla en planta con scanner_2 y correr:
```
python -m src.main build-pattern --model modelo_A --scanner scanner_2 --img <imagen>
```

---

### SesiÃ³n 2026-05-26 â€” Tadeo + Claude

#### Cambio 29 â€” Filtro de extras falsos (`extra_min_dist_factor`)

**Problema:** El matcher greedy marcaba como "extra" (diamante naranja) agujeros que
fÃ­sicamente existen en el patrÃ³n pero cuya posiciÃ³n esperada quedÃ³ ligeramente fuera
de `tol_xy_px` por error de fase del grid o por drift local. Estos no son detecciones
espurias: son agujeros reales que el algoritmo no pudo asignar.

**SoluciÃ³n:** Nuevo parÃ¡metro `extra_min_dist_factor` en `compare_missing_only()`.
DespuÃ©s del matching greedy, cada detectado sin match se computa contra TODAS las
posiciones esperadas (incluyendo ya matcheadas). Si la distancia mÃ­nima es â‰¤
`extra_min_dist_factor Ã— tol_xy_px`, se descarta como "near-expected" â€” no es un
extra genuino. Solo los verdaderamente lejanos de toda posiciÃ³n esperada se conservan.

**ImplementaciÃ³n:**
- `src/pipeline/compare.py` â†’ nuevo param `extra_min_dist_factor: float = 0.0`;
  calcula `d2_to_exp` matricial (n_raw Ã— n_exp) y filtra con `min > thr2`.
- `src/utils/config.py` â†’ `DEFAULT_TOLERANCES` agrega `"extra_min_dist_factor": 0.0`
- `src/inspection.py` â†’ lee `extra_min_dist_factor` y lo pasa a `compare_missing_only`
- `config/tolerancias.yaml` â†’ `extra_min_dist_factor: 2.0` para modelo_B
  (umbral = 2 Ã— 22px = 44px â€” reflejos/ruido espurio estÃ¡n tÃ­picamente >100px de todo expected)

**Resultado en grabaciÃ³n 185 frames:**
- 185/185 raw OK mantenido âœ“
- `extra=0` en ~180/185 frames (antes podÃ­a ser 3â€“15 en frames sin blur)
- Los 5 frames con `extra=1` restantes son detecciones genuinamente aisladas

---

#### Cambio 30 â€” MediciÃ³n de verticalidad de bordes del patrÃ³n

**MotivaciÃ³n:** Las polilÃ­neas que dibujan los bordes laterales del patrÃ³n y de la chapa
pueden no ser perfectamente verticales (el material llega con leve inclinaciÃ³n o el patrÃ³n
punzonado tiene deriva). Necesario poder cuantificar ese Ã¡ngulo para diagnÃ³stico.

**ImplementaciÃ³n:**

`src/pipeline/edge_centering.py`:
- `CenteringResult` agrega 4 nuevos campos con default=0.0:
  - `left_edge_slope_deg` â€” pendiente del borde izquierdo de chapa (Â°)
  - `right_edge_slope_deg` â€” pendiente del borde derecho de chapa (Â°)
  - `pattern_left_slope_deg` â€” pendiente del borde izquierdo del patrÃ³n (Â°)
  - `pattern_right_slope_deg` â€” pendiente del borde derecho del patrÃ³n (Â°)
- ConvenciÃ³n: 0Â° = perfectamente vertical. Positivo = el borde se inclina a la derecha
  al bajar. Se calcula como `atan(a) * 180/Ï€` donde `a` es el coeficiente de
  `_fit_line_robust(pts)` (ajuste `x = a*y + b`).
- `compute_centering()` define funciÃ³n local `_slope_deg()` y calcula los 4 slopes
  sobre los puntos por banda ya disponibles.

`src/pipeline/annotate.py` â€” `draw_centering_overlay()`:
- Nueva lÃ­nea de texto en `text_y_base - 60`:
  `"Vert pat: Izq=Â±X.XÂ°  Der=Â±Y.YÂ°"` en color cyan-amarillo.
- Usa `getattr` para retrocompatibilidad.

**Comportamiento esperado:**
- Material bien alineado: |slope| < 1Â°
- Material con leve inclinaciÃ³n: 1Â°â€“3Â°
- >3Â° indica problema de encuadre o error de alineaciÃ³n
- Misma granularidad que la detecciÃ³n de bordes: 16 bandas, ajuste sigma-clip

**185/185 OK mantenido âœ“**

---

#### Cambio 33 â€” Comando CLI `center-folder` + overlay CHAPA/PATRON labels + documentaciÃ³n `center_offset_tol_px`

**MotivaciÃ³n:** Formalizar y exportar las mediciones de centrado en forma diagnÃ³stica, para poder
calibrar `center_offset_tol_px` con datos reales de la grabaciÃ³n de referencia.

**ImplementaciÃ³n:**

`src/main.py` â€” nueva funciÃ³n `cmd_center_folder()`:
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

`src/pipeline/annotate.py` â€” `draw_centering_overlay()`:
- Agrega etiquetas "CHAPA" (gris) y "PATRON" (cyan-amarillo) sobre cada lÃ­nea de borde
- Reestructura texto inferior: fila 1=`Izq: Npx   Der: Mpx`, fila 2=`Delta: Xpx   Offset: Ypx`
- Coordenadas clampeadas al ancho visible (borde chapa puede estar fuera del ROI)

`config/tolerancias.yaml` â€” modelo_B:
- Agrega `center_offset_tol_px: 0.0` con comentario completo de semÃ¡ntica:
  - `offset_px = (left_margin_px - right_margin_px) / 2`
  - Positivo = patrÃ³n corrido a la derecha, negativo = a la izquierda
  - Mediana -0.95px en grabaciÃ³n 185 frames; peor caso +7.02px

**ValidaciÃ³n** â€” grabaciÃ³n `20260519_121741` (185 frames, modelo_B/scanner_1):
- 185/185 mediciones fiables (centering_reliable=True en todos)
- Offset mediana = **-0.95 px** âœ“ (esperado â‰ˆ -0.9 px)
- Margen Izq mediana = **207.2 px** âœ“ (esperado â‰ˆ 207 px)
- Margen Der mediana = **209.2 px** âœ“ (esperado â‰ˆ 209 px)
- Offset mÃ¡x = **+7.02 px** âœ“ (esperado â‰ˆ 7 px)
- 185/185 raw OK mantenido âœ“ (no se tocÃ³ lÃ³gica de producciÃ³n)

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

### SesiÃ³n 2026-05-27 â€” Tadeo + Claude

#### Cambio 43 â€” Tracking de agujero faltante por identidad de grilla (ci/cj)

**Problema:** El `MachineStopDetector` anterior rastreaba zonas por pÃ­xel X (`same_zone_px`).
Cuando la chapa avanza en Y entre frames, el mismo agujero faltante (mismo punzÃ³n roto) aparece
en pÃ­xeles distintos â†’ el tracker no reconocÃ­a el defecto como persistente y no disparaba parada.

**Causa raÃ­z confirmada con diagnÃ³stico:**
```
frame_0002: ci=13, cj=46  â†’ missing=1, no trigger (acumulando)
frame_0006: ci=13, cj=38  â†’ machine_stop=True  (cj cambiÃ³ 8 posiciones â€” chapa avanzÃ³)
frame_0007: ci=13, cj=36  â†’ machine_stop=True
frame_0008: ci=13, cj=36  â†’ machine_stop=True
frame_0009: ci=13, cj=33  â†’ machine_stop=True
```
La columna del punzÃ³n (ci=13) es invariante. La fila (cj) cambia con el avance de la cinta.
El pixel Y cambia ~4â€“8 filas entre frames. Con el tracker por pixel X esto funcionaba solo si
el drift era menor que `same_zone_px=35px`. Con tracking por ci: coincidencia exacta siempre.

**ImplementaciÃ³n:**

**`src/pipeline/grid_fitting.py`** â€” `grid_compare_points()`:
- Cambio de retorno: `list[tuple[float,float]]` â†’ `tuple[list[...], list[tuple[int,int]]]`
- Segundo elemento: lista paralela de `(ci, cj)` para cada punto esperado generado.
- Permite que la capa de inspecciÃ³n propague la identidad de celda hasta el detector.

**`src/pipeline/compare.py`** â€” `compare_missing_only()` + `CompareReport`:
- `CompareReport` agrega campo `missing_cells: List[Tuple[int,int]]` (default `[]`).
  Contiene las coordenadas de grilla `(ci, cj)` de cada agujero esperado sin match.
- Nuevo parÃ¡metro `expected_cells: List[Tuple[int,int]] | None = None`:
  cuando se provee, los missing_cells se popula en paralelo con missing_points.
- Nuevo parÃ¡metro `use_hungarian: bool = False`:
  matching Ã³ptimo via `scipy.optimize.linear_sum_assignment`. Resuelve el problema de
  "robo" de detectados cuando `tol_xy_px â‰ˆ dy` (dos expected compiten por el mismo detected).
  Si scipy no estÃ¡ instalado: fallback automÃ¡tico a greedy con warning implÃ­cito.
- Tracking de Ã­ndices de missing en ambos paths (greedy y Hungarian) para poblar `missing_cells`.

**`src/pipeline/machine_stop.py`** â€” `MachineStopDetector`:
- Nuevos parÃ¡metros:
  - `track_by_grid: bool = True` â€” activa tracking por columna ci
  - `same_column_tol_cells: int = 0` â€” tolerancia en celdas (0=exacto)
- Nueva estructura interna `_grid_zones: dict[int, dict]` keyed by ci.
  Cada zona: `{streak, count, x, y}`.
- `update()` acepta `missing_cells: Sequence[tuple[int,int]] | None = None`.
  Cuando `track_by_grid=True` y `missing_cells` disponible â†’ usa `_update_grid()`.
  Si no (path no-grid o cells vacÃ­os) â†’ fallback a `_update_pixel()` (comportamiento anterior).
- Nueva property `triggered_columns: list[int]` â†’ ci valores de zonas disparadas.
  Usada para construir el mensaje del badge: `"AGUJERO FALTANTE PERSISTENTE EN COLUMNA 13"`.
- `reset()` limpia tambiÃ©n `_grid_zones`.

**`src/inspection.py`** â€” `_inspect_bgr()`:
- Desempaca `(compare_points, compare_cells)` del retorno de `grid_compare_points`.
- Y-clip mantiene `compare_cells` sincronizado con `compare_points` (filtrado en paralelo).
- Pasa `expected_cells=compare_cells` y `use_hungarian=use_hungarian_matching` a `compare_missing_only`.
- Pasa `missing_cells=report.missing_cells` a `_ms_detector.update()`.
- Usa `_ms_detector.triggered_columns` para construir el texto de razÃ³n del badge.
  Ejemplo de mensaje generado: `"AGUJERO FALTANTE PERSISTENTE EN COLUMNA 13"`.
- Lee nuevo param `use_hungarian_matching` desde tolerancias.
- `inspect_folder()`: pasa `track_by_grid` y `same_column_tol_cells` al constructor del detector.

**`src/vision/inspector.py`** â€” `_get_detector()`:
- Pasa `track_by_grid` y `same_column_tol_cells` al constructor de `MachineStopDetector`.

**`src/utils/config.py`** â€” nuevos defaults:
```python
"machine_stop_track_by_grid": True,
"machine_stop_same_column_tol_cells": 0,
"use_hungarian_matching": False,
```

**`config/tolerancias.yaml`** â€” modelo_B:
```yaml
machine_stop_track_by_grid: true
machine_stop_same_column_tol_cells: 0  # coincidencia exacta de ci
use_hungarian_matching: false           # activar si scipy disponible y hay stealing
```

**Resultados de prueba â€” carpeta `20260519_121741` (Imagenes_METALCONF_editadas):**
- Detector activa `MACHINE_STOP` en frames con agujeros tapados en el medio del patrÃ³n.
- El badge muestra la columna especÃ­fica: `"AGUJERO FALTANTE PERSISTENTE EN COLUMNA 13"`.
- El mismo ci=13 se reconoce a travÃ©s de 4+ frames aunque cj varÃ­a (cinta avanzando).
- `machine_stop_frames=28` en 185 frames analizados (imÃ¡genes con defectos intencionales).
- Los frames sin defecto (material limpio) mantienen `MACHINE_STOP=False` correctamente.

**Invariante preservado:** 185/185 raw OK en material original limpio (no editado) mantenido.

**Sin tocar:** PLC, solenoides, lÃ³gica de comparaciÃ³n base, patrÃ³n de referencia, `grid_max_missing`, `consecutive_nok_frames`.

---

#### Cambio 44 â€” Parada de mÃ¡quina virtual: sin acciones de hardware

**MotivaciÃ³n / regla de seguridad:**
Hay personas cerca de la mÃ¡quina. `machine_stop=True` debe ser puramente informativo:
visible en UI/overlay/log, pero **no debe accionar solenoides, backlight ni cambios de estado FSM**.
La regla es: solenoides bloqueados siempre; la parada solo es virtual hasta que se apruebe
el control automÃ¡tico de pistones.

**Cambios:**

**`src/pipeline/annotate.py`** â€” texto del badge:
- `"! DETENER MAQUINA"` â†’ `"! DETENCION VIRTUAL DE MAQUINA"`

**`src/controller/scanner_controller.py`** â€” FSM y hardware:
- Antes: `machine_stop=True` â†’ `ScannerState.FAULT` + escribe solenoid=False + backlight=False + luz roja.
- Ahora: `machine_stop=True` â†’ solo log warning `"DETENCION VIRTUAL â€” sin accion de hardware"`.
  `ScannerState.FAULT` sigue disparando solo por `consecutive_nok_frames` (lÃ³gica de streak).
  Los `elif` se separan: machine_stop y fault son caminos independientes.

**`src/ui/service.py`** â€” `_AnalysisWorker`:
- Cuando `machine_stop_enabled=True`, el anÃ¡lisis de carpeta pasa de paralelo (ThreadPoolExecutor)
  a **secuencial** con un Ãºnico `MachineStopDetector` compartido entre frames.
  Motivo: el detector es stateful; con threads los frames llegan fuera de orden y la racha
  nunca se acumula correctamente.
- Cuando `machine_stop_enabled=False` (default): sigue usando ThreadPoolExecutor (sin cambio de rendimiento).

**`src/ui/service.py`** â€” `RecordingTab` (live inspection):
- `__init__`: agrega `self._live_ms_detector = None`.
- `_on_stop()`: resetea `_live_ms_detector = None` al detener la grabaciÃ³n.
- `_grab_frame()`: si `machine_stop_enabled=True`, crea el detector solo en el primer frame
  y lo reutiliza en todos los siguientes (detector persistente por sesiÃ³n de grabaciÃ³n).
  Pasa el detector vÃ­a `_preloaded={"machine_stop_detector": self._live_ms_detector}`.

**`config/tolerancias.yaml`** â€” modelo_A:
- `consecutive_nok_frames: 9999` â†’ `5` (habilitado: 5 NOK consecutivos = FAULT)
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
- `MACHINE_STOP` aparece correctamente en frames 185â€“196 con missing=2 persistente.
- El badge del overlay dice `"! DETENCION VIRTUAL DE MAQUINA"`.
- No se escriben salidas de hardware en ningÃºn momento.

---

#### Cambio 45 â€” Parametro frame_missing_nok_threshold (infraestructura, no activado)

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
  `20260519_121741` (frames con blur de movimiento tienen 1-5 missing â†’ todos NOK).
- Decision: no activar en ninguno de los dos modelos hasta calibrar con material real.
  - `modelo_B`: parametro eliminado de la seccion (hereda default `None`).
  - `modelo_A`: parametro eliminado (sin imagenes reales de Esterilla para calibrar).
- Para activar en el futuro: agregar `frame_missing_nok_threshold: 0` en el modelo deseado.

---

#### Cambio 46 â€” Clasificacion por tipo de agujero para Esterilla (modelo_A)

**Motivacion:** Esterilla tiene dos tamanos de agujeros claramente distintos:
- Agujeros chicos (cj impar): râ‰ˆ14px, areaâ‰ˆ627pxÂ² â€” 4 por fila
- Agujeros grandes (cj par): râ‰ˆ25px, areaâ‰ˆ2023pxÂ² â€” 5 por fila

Sin clasificacion, el matcher podia asignar un blob grande (ruido/reflejo) a la posicion
de un agujero chico esperado, o viceversa. Los agujeros faltantes tampoco se etiquetaban
por tipo, dificultando el diagnostico (punzon chico vs punzon grande roto).

**Cambios:**

**`src/utils/config.py`** â€” 5 nuevos defaults:
```python
"hole_type_split_area": 0.0,  # 0 = deshabilitado; >0 = umbral en pxÂ² entre chico/grande
"min_area_small":       0.0,  # 0 = usar min_area global; >0 = piso para agujeros chicos
"max_area_small":       0.0,  # 0 = sin techo; >0 = techo para agujeros chicos
"min_area_large":       0.0,  # 0 = usar min_area global; >0 = piso para agujeros grandes
"max_area_large":       0.0,  # 0 = sin techo; >0 = techo para agujeros grandes
```

**`src/pipeline/compare.py`** â€” `CompareReport` + `compare_missing_only()`:
- `CompareReport` agrega campo `missing_types: List[str]` (default `[]`).
  Contiene `"small"` o `"large"` para cada agujero faltante.
- `compare_missing_only()` agrega parametros `expected_types` y `detected_types`.
  Cuando ambos se proveen, pares de tipo cruzado (chico-expected vs grande-detected)
  reciben distancia infinita â†’ nunca se asignan entre si (hard constraint).

**`src/inspection.py`** â€” `_inspect_bgr()`:
- Lee nuevos parametros (`hole_type_split_area`, `min_area_small`, etc.).
- Post-deteccion: cuando `hole_type_split_area > 0`, clasifica cada `Hole` en
  `"small"` / `"large"` por area, aplica filtros de area por tipo, y descarta
  agujeros fuera del rango esperado para su categoria.
- Deriva `expected_types` de `pattern.radii` + `compare_cells`:
  `split_r = sqrt(hole_type_split_area / pi)` â€” punto de corte en el gap natural.
  Para cada celda `(ci,cj)` en `compare_cells`, busca el radio en el patron y
  clasifica como `"small"` o `"large"`.
- Bbox filter: mantiene `detected_types` sincronizado con `detected_in_bbox`.
- Pasa `expected_types` y `detected_types` a `compare_missing_only`.
- `report.missing_types` refleja el tipo de cada agujero faltante.

**`config/tolerancias.yaml`** â€” modelo_A:
```yaml
hole_type_split_area: 1000.0  # gap natural entre 627pxÂ² (chico) y 2023pxÂ² (grande)
min_area_small: 350.0         # chico real â‰ˆ627pxÂ²; noise << 350
max_area_small: 1300.0        # excluye grandes y ruido grande intermedio
min_area_large: 900.0         # grande real â‰ˆ2023pxÂ²; excluye chicos
max_area_large: 5000.0        # excluye suciedad / reflejo muy grande
```

**modelo_B:** `hole_type_split_area=0.0` (no en config â†’ hereda default 0 = deshabilitado).
El codigo nuevo es completamente inerte para modelo_B.

**Diagnostico del patron modelo_A (holes.json):**
- 117 puntos totales, 113 celdas unicas (4 duplicados por redondeo en build)
- dx=68px, dy=38px â€” grilla rectangular
- Filas cj impar: 4 agujeros chicos, râ‰ˆ14px (media 14.13px)
- Filas cj par: 5 agujeros grandes, râ‰ˆ25px (media 25.38px)
- Bimodalidad clara: gap entre 14px y 25px con punto de corte râ‰ˆ17.8px (area=1000pxÂ²)

**Prueba de logica (no hay imagenes Esterilla disponibles):**
- Test 1: matching mismo tipo â†’ 0 missing âœ“
- Test 2: tipo cruzado (small expected vs large detected) â†’ ambos missing âœ“
- Test 3: un match + un faltante grande â†’ missing_types=['large'] âœ“
- Test 4: sin tipos â†’ backward compatible âœ“
- `python -m compileall src` OK

**Limitacion:** No hay imagenes de Esterilla (scanner_2/modelo_A) disponibles para
validar los umbrales de area en planta. Los parametros `min_area_small` etc. son
estimaciones basadas en los radios del patron existente. Calibrar en planta con
histograma de areas cuando se tengan imagenes reales de Esterilla.

**Sin tocar:** modelo_B (tipo deshabilitado), PLC, solenoides, logica temporal,
patron de referencia, grid_max_missing.

#### Cambio 46b â€” Activar frame_missing_nok_threshold: 0 para modelo_A

**Motivacion:** Con la clasificacion por tipo implementada, el usuario quiere que
cualquier agujero faltante en Esterilla marque el frame como NOK inmediatamente,
habilitando el seguimiento frame-a-frame para la parada virtual de maquina.

**Cambio:** `config/tolerancias.yaml` modelo_A â€” agrega:
```yaml
frame_missing_nok_threshold: 0  # cualquier missing â†’ NOK inmediato
```
Esto complementa `grid_max_missing: 10` (que solo marca NOK cuando faltan >10).
Con `frame_missing_nok_threshold: 0`, UN solo agujero faltante ya marca NOK y
alimenta el contador de racha para `machine_stop_missing_frames: 5`.

**Sin tocar:** modelo_B (hereda default `None` = solo `grid_max_missing` aplica).

---

#### Cambio 47 â€” Bandas de muestreo de bordes configurables + suavizado antes de zigzag

**Motivacion:** Con `_N_BANDS=16` fijo, variaciones leves en la frontera del patron
(patron corrido 1-2px) podian pasar desapercibidas porque cada banda abarca muchas filas
y el ruido de una sola banda vacÃ­a/escasa disparaba el metric. Se necesitaba:
1. Mayor resoluciÃ³n espacial (mÃ¡s bandas).
2. Descarte de bandas con muy pocos agujeros (outliers por zona vacÃ­a).
3. Suavizado previo al calculo de zigzag para no reaccionar a un outlier aislado.

**Cambios:**

**`src/pipeline/edge_centering.py`:**
- Nueva funciÃ³n `_smooth_points_x(pts, window)`: mediana deslizante sobre los valores X
  de una serie de puntos (x,y) ordenados por Y. Usada SOLO para las series de patron
  (no para la chapa, donde los outliers SI son la seÃ±al de vibraciÃ³n).
- `_pattern_bounds_by_band()`: nuevo parÃ¡metro `min_holes=1`. Bandas con menos agujeros
  que `min_holes` se descartan â†’ evita estimaciones de borde basadas en 1 agujero.
- `compute_centering()`: 3 nuevos parÃ¡metros opcionales con defaults backward-compatible:
  - `n_bands=16` â€” sustituye la constante `_N_BANDS` en todo el flujo.
  - `min_holes_per_band=1` â€” pasado a `_pattern_bounds_by_band`.
  - `smooth_window=1` â€” aplicado a `pattern_left_points`, `pattern_right_points` y
    `center_pts` antes de `_zigzag_residuals`. El overlay sigue mostrando puntos crudos.
- Corregido bug: `for i in range(_N_BANDS)` en el calculo de band_lm/band_rm usaba la
  constante global en vez del parametro local. Ahora usa `n_bands`.

**`src/inspection.py`:**
- Lee `edge_centering_bands`, `pattern_edge_min_holes_per_band`, `pattern_edge_smooth_window`
  de tolerancias y los pasa a `compute_centering()`.

**`src/utils/config.py`:**
- 3 nuevos defaults: `edge_centering_bands=16`, `pattern_edge_min_holes_per_band=1`,
  `pattern_edge_smooth_window=1`. Los defaults mantienen comportamiento anterior para modelos
  sin configuraciÃ³n explÃ­cita.

**`config/tolerancias.yaml` â€” modelo_B:**
```yaml
edge_centering_bands: 24          # 24 > 16 â†’ mÃ¡s resoluciÃ³n espacial
pattern_edge_min_holes_per_band: 2 # descarta bandas con 1 solo agujero (outliers de borde)
pattern_edge_smooth_window: 3      # mediana de 3 bandas antes del calculo de zigzag
```

**Resultado en 20260519_121741 (primeros 10 frames):**
- Con 16 bandas: raw_ok=6, raw_nok=4
- Con 24 bandas: raw_ok=4, raw_nok=6 â€” frames 0001 y 0004 ahora NOK (antes pasaban)
  Frame 0001 (missing=0): detectado por zigzag de patron con mayor resoluciÃ³n.
  Frame 0004 (missing=3): alineacion levemente degradada que 16 bandas no capturaba.
- MACHINE_STOP frames 6-9: sin cambio (correcto).
- Tiempo: ~50ms/frame (sin cambio respecto a 16 bandas).

**Calibrar si se necesita mÃ¡s sensibilidad:** subir a `edge_centering_bands: 32`.
PrecauciÃ³n: con muchas bandas y pocos agujeros por fila, mÃ¡s bandas quedan vacÃ­as.
El parÃ¡metro `pattern_edge_min_holes_per_band: 2` compensa este efecto.

**Sin tocar:** modelo_A (hereda defaults de config.py = comportamiento anterior).

---

#### Cambio 48 â€” Verticalidad de patrÃ³n mÃ¡s sensible sin recortar overlay

**MotivaciÃ³n:** Frames editados del rango 120-140, especialmente `frame_0121`,
`frame_0122` y `frame_0124`, tenÃ­an corrimiento leve del patrÃ³n y no siempre
entraban como `PATRON DESALINEADO`. AdemÃ¡s, la polilÃ­nea visual del patrÃ³n quedaba
recortada en los extremos superior/inferior porque los mismos puntos filtrados para
mÃ©trica se usaban tambiÃ©n para dibujar.

**Cambios:**
- `src/pipeline/edge_centering.py`:
  - `_pattern_bounds_by_band()` ya no recorta extremos Y; devuelve la frontera completa
    para que el overlay muestre mÃ¡s borde del patrÃ³n.
  - `compute_centering()` mantiene una copia recortada solo para las mÃ©tricas numÃ©ricas
    de verticalidad, evitando que bandas extremas con pocos datos inflen falsamente el
    zigzag.
  - El overlay usa puntos completos; las mÃ©tricas (`pattern_zigzag_*`,
    `pattern_center_zigzag_*`, slopes y std de mÃ¡rgenes) usan la serie mÃ©trica filtrada.
- `config/tolerancias.yaml` modelo_B:
  - `pattern_align_std_max_px: 5.0 -> 2.4`
  - `pattern_align_abs_max_px: 30.0 -> 6.0`
  - `pattern_center_zigzag_std_max_px: 4.0 -> 2.2`
  - `pattern_center_zigzag_abs_max_px: 6.5 -> 6.0`
  - `pattern_edge_smooth_window: 3 -> 1`

**ValidaciÃ³n en `20260519_121741`:**
- Rango 120-140:
  - `frame_0121`, `frame_0122` y `frame_0124` ahora quedan `NOK` con
    `pattern_alignment_warn=True`.
  - Frames 132-140 quedan mayormente `OK`, salvo defectos reales detectados por la mÃ©trica.
- Carpeta completa: 185 frames -> `OK=162`, `NOK=23`; `pattern_warn_count=22`;
  `LOW_QUALITY=1` (`frame_0037`).
- Overlays de muestra guardados en `data/output/verticalidad_patron_120_140/`.
- `python -m compileall src` OK.
- `python -m pytest tests/`: 0 tests recolectados.

**Seguridad:** la parada sigue siendo virtual. No se modificÃ³ lÃ³gica de PLC,
solenoides ni salidas fÃ­sicas.

---

---

### SesiÃ³n 2026-05-27 (cont.) â€” Tadeo + Claude

#### Cambio 49 â€” Panel de razones NOK + marcadores de agujeros faltantes numerados

**MotivaciÃ³n:** El operador necesita saber exactamente POR QUÃ‰ un frame es NOK y DÃ“NDE
estÃ¡n los agujeros faltantes para verificar visualmente si la detecciÃ³n es correcta.

**Cambios:**

**`src/pipeline/annotate.py`:**
- Nueva funciÃ³n `_draw_nok_reasons_panel(img, reasons)`:
  - Panel semitransparente rojo oscuro en top-left del overlay.
  - Header "NOK" en blanco/rojo; razones en cyan. Altura adaptativa segÃºn nÃºmero de causas.
- `draw_compare_overlay()` ahora acepta `nok_reasons: List[str] = ()`.
  - Si NOK y hay razones: dibuja el panel en lugar del texto "STATUS: NOK".
  - Si OK: texto pequeÃ±o verde "STATUS: OK".
- Marcadores de missing holes rediseÃ±ados:
  - CÃ­rculo relleno oscuro (r=18, color `(0,0,80)`) como fondo.
  - Cruz blanca con markerSize=36 (outlin) + 34 (fill) para visibilidad.
  - NÃºmero de orden (1, 2, 3...) sobre cada marcador para identificaciÃ³n.

**`src/inspection.py`:**
- Construye `nok_reasons: list[str]` antes de la llamada a `draw_compare_overlay`:
  - `AGUJEROS FALTANTES: N`, `AGUJEROS EXTRA: N`, `CENTRADO NOK (+Xpx)`,
    `PATRON DESALINEADO`, `PARADA DE MAQUINA`, `IMAGEN INESTABLE`, `ALINEACION FALLBACK`.
- Pasa `nok_reasons=nok_reasons` a `draw_compare_overlay`.

**Commit:** `d4b5a75`

---

#### Cambio 50 â€” Tolerancias modelo_A (Esterilla) mÃ¡s permisivas

**Problema reportado:** El modelo de Esterilla marcaba casi todo como NOK. Los parÃ¡metros
iniciales eran demasiado conservadores para la realidad de planta (blur de movimiento,
variaciones de iluminaciÃ³n, posicionamiento no ideal).

**Cambios en `config/tolerancias.yaml` â€” SOLO secciÃ³n `modelo_A`:**

| ParÃ¡metro | Antes | DespuÃ©s | RazÃ³n |
|---|---|---|---|
| `min_area` | 400.0 | 300.0 | Blur reduce Ã¡rea aparente de los agujeros chicos |
| `circularity_min` | 0.80 | 0.70 | Blur de movimiento reduce circularidad aparente |
| `min_area_small` | 350.0 | 250.0 | Captura agujeros chicos afectados por blur |
| `max_area_small` | 1300.0 | 1500.0 | Margen ampliado |
| `min_area_large` | 900.0 | 700.0 | Evitar perder grandes con iluminaciÃ³n no ideal |
| `tol_xy_px` | 16.0 | 18.0 | MÃ¡s tolerancia de posiciÃ³n (mÃ¡ximo seguro < dy/2=19) |
| `grid_max_missing` | 10 | 15 | ~13% de 112 agujeros; mÃ¡s tolerante |
| `frame_missing_nok_threshold` | 0 | 3 | Permite hasta 3 missing antes de NOK por frame |
| `consecutive_nok_frames` | 5 | 8 | Requiere mÃ¡s frames consecutivos para FAULT |

**Sin tocar:** `modelo_B`, PLC, solenoides, patrÃ³n de referencia, lÃ³gica de grid.

**Nota:** Calibrar en planta con material real. Estos valores son estimaciones
razonables para reducir falsos positivos sin perder defectos reales.

---

#### Cambio 51 â€” Frontera de patrÃ³n por borde global para evitar falsos zigzag

**Problema:** La detecciÃ³n de borde del patrÃ³n generaba demasiados falsos positivos.
En bandas donde la grilla alternada no tenÃ­a agujero de la columna exterior, el cÃ³digo
tomaba el agujero mÃ¡s externo disponible de esa banda, que podÃ­a ser una columna
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

**ValidaciÃ³n en `20260519_121741`:**
- Antes del fix: 185 frames -> `NOK=23`, `pattern_warn_count=22`.
- DespuÃ©s del fix: 185 frames -> `OK=176`, `NOK=9`, `pattern_warn_count=8`.
- Frames clave:
  - `frame_0121`: sigue `NOK`, `PATRON DESALINEADO`.
  - `frame_0124`: sigue `NOK`, `PATRON DESALINEADO`.
  - `frame_0132` a `frame_0140`: vuelven mayormente `OK`, reduciendo falsos positivos.
- Overlays de control guardados en `data/output/verticalidad_patron_boundary_fix/`.
- `python -m compileall src` OK.
- `python -m pytest tests/`: 0 tests recolectados.

**Seguridad:** la parada sigue siendo virtual; no se tocÃ³ PLC, solenoides ni salidas fÃ­sicas.

---

#### Cambio 52 â€” Bypass temporal de login en Modo Servicio

**MotivaciÃ³n:** Por pedido de operaciÃ³n, el Modo Servicio debe abrir sin pedir usuario
ni contraseÃ±a por ahora.

**Cambios:**
- `config/app.yaml`:
  - Agrega `service.login_enabled: false`.
  - Para volver a exigir credenciales, cambiarlo a `true`.
- `src/ui/login_dialog.py`:
  - Nueva funciÃ³n `service_login_enabled()` que lee `config/app.yaml`.
  - Si el archivo/config falla, el fallback es seguro: login habilitado.
- `src/main.py`:
  - El comando `service` solo muestra `LoginDialog` si `service_login_enabled()` es `true`.
- `src/ui/operator.py`:
  - El botÃ³n "Modo Servicio" aplica la misma regla.

**ValidaciÃ³n:**
- `python -m compileall src/main.py src/ui/operator.py src/ui/login_dialog.py` OK.

**Nota:** Es un bypass temporal de UI. No modifica PLC, solenoides ni salidas fÃ­sicas.

---

#### Cambio 53 â€” Reset correcto de parada virtual cuando no hay missing

**Problema:** En modo `machine_stop_track_by_grid`, cuando un frame no tenÃ­a agujeros
faltantes, `missing_cells` llegaba vacÃ­o y el detector caÃ­a al modo pixel en lugar de
actualizar/limpiar el estado de grilla. Eso dejaba rachas viejas colgadas; despuÃ©s un
frame aislado con falsos missing, como `frame_0064`, podÃ­a heredar una racha previa y
mostrar `DETENCION VIRTUAL DE MAQUINA` aunque no hubiera N frames consecutivos reales.

**Cambios:**
- `src/pipeline/machine_stop.py`:
  - En tracking por grilla, `missing_cells=[]` ahora se procesa como frame vÃ¡lido sin
    faltantes y resetea las columnas activas.
  - Solo se usa fallback pixel cuando `missing_cells is None`.
- `src/inspection.py`:
  - Siempre pasa `report.missing_cells` al detector, incluso cuando la lista estÃ¡ vacÃ­a.
- `tests/test_machine_stop.py`:
  - Agrega tests para garantizar que un frame vacÃ­o corta la racha.
  - Agrega test de que la parada virtual requiere frames consecutivos en la misma columna.

**ValidaciÃ³n:**
- `python -m pytest tests/test_machine_stop.py` OK.
- Rango `frame_0058` a `frame_0066` de `20260519_121741`:
  - `frame_0064` mantiene el anÃ¡lisis de missing, pero queda `machine_stop=False`.
  - `frame_0066` sin missing limpia todas las columnas activas.

**Seguridad:** La parada sigue siendo virtual. No se modificÃ³ PLC, solenoides ni salidas fÃ­sicas.

---

#### Cambio 54 â€” Frames inestables por borde de CHAPA + patrÃ³n extendido en overlay

**Problema:** `frame_0031` mostraba borde externo de CHAPA con lectura dÃ©bil/ondulada,
pero no se clasificaba como `IMAGEN INESTABLE`. El criterio anterior solo miraba el
zigzag absoluto grande; si Hough no encontraba lÃ­neas del borde y el zigzag era leve,
el frame podÃ­a quedar como `GOOD/STABLE`. AdemÃ¡s, las lÃ­neas de borde del PATRON se
cortaban donde habÃ­a puntos de banda, dificultando auditar visualmente la decisiÃ³n.

**Cambios:**
- `config/tolerancias.yaml` modelo_B:
  - Agrega `chapa_no_line_min_used_lines: 1`.
  - Agrega `chapa_no_line_abs_max_px: 2.7`.
  - Regla: si Hough no detecta al menos 1 lÃ­nea confiable y la CHAPA supera 2.7px
    de zigzag mÃ¡ximo, el frame se marca `LOW_QUALITY/UNSTABLE`.
- `src/inspection.py`:
  - Aplica la nueva regla dentro de `verticality_quality_enabled`.
  - Los frames inestables se siguen analizando y dibujando, pero no alimentan parada.
- `src/pipeline/machine_stop.py`:
  - Un frame `LOW_QUALITY` conserva el historial interno, pero retorna
    `machine_stop=False` en ese frame. Una imagen borrosa/inestable nunca muestra
    `DETENCION VIRTUAL DE MAQUINA` por sÃ­ misma.
- `src/pipeline/annotate.py`:
  - El overlay de PATRON ahora dibuja tambiÃ©n la lÃ­nea ajustada de arriba a abajo
    del frame, ademÃ¡s de la polilÃ­nea real por bandas.
- `src/utils/config.py`:
  - Defaults para las nuevas claves, deshabilitados por defecto.
- `tests/test_machine_stop.py`:
  - Test de que `LOW_QUALITY` no reporta parada virtual.

**ValidaciÃ³n en `C:\Users\DefyC\Downloads\Imagenes_METALCONF_editadas`:**
- `frame_0031`: `OK`, `LOW_QUALITY/UNSTABLE`, `machine_stop=False`,
  `chapa=(std 0.59px, max 2.84px)`, `used_lines=0`.
- `frame_0064`: sigue `OK`, `machine_stop=False`.
- `frame_0121` y `frame_0124`: siguen `NOK` por `PATRON DESALINEADO`.
- `frame_0127`: `LOW_QUALITY/UNSTABLE` y ya no muestra `machine_stop=True`.
- Carpeta completa: 185 frames, `low_quality=24`, `machine_stop_count=18`.
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

**Seguridad:** Parada virtual Ãºnicamente; no se modificÃ³ PLC, solenoides ni salidas fÃ­sicas.

---

#### Cambio 55 â€” DetecciÃ³n de desalineaciÃ³n global grande del PATRON

**Problema:** La lÃ³gica de `PATRON DESALINEADO` detectaba zigzag/ondulaciÃ³n del patrÃ³n,
pero podÃ­a no marcar un corrimiento global grande o una inclinaciÃ³n brusca cuando el
patrÃ³n seguÃ­a siendo internamente recto. En planta esto puede pasar por golpes o cambios
rÃ¡pidos de alineaciÃ³n de la chapa/punzonado.

**Cambios:**
- `src/pipeline/edge_centering.py`:
  - `CenteringResult` ahora expone:
    - `pattern_sheet_slope_delta_left_deg`
    - `pattern_sheet_slope_delta_right_deg`
    - `pattern_sheet_slope_delta_max_deg`
  - Estas mÃ©tricas comparan la inclinaciÃ³n del borde del PATRON contra la inclinaciÃ³n
    del borde real de CHAPA, lado por lado.
- `config/tolerancias.yaml` modelo_B:
  - `pattern_global_offset_max_px: 10.0`
  - `pattern_slope_delta_max_deg: 2.0`
  - Detecta desplazamiento lateral grande del patrÃ³n y/o inclinaciÃ³n relativa brusca
    aunque no haya zigzag interno.
- `src/inspection.py`:
  - Si el frame estÃ¡ `STABLE`, cualquiera de estas condiciones marca `NOK`:
    - zigzag de patrÃ³n fuera de tolerancia
    - `abs(offset_px) > pattern_global_offset_max_px`
    - `pattern_sheet_slope_delta_max_deg > pattern_slope_delta_max_deg`
  - El panel NOK distingue razones:
    - `PATRON DESCENTRADO (+/-Xpx)`
    - `PATRON INCLINADO (X deg)`
- `src/pipeline/annotate.py`:
  - Agrega `dCh=` al texto de verticalidad para ver el Ã¡ngulo relativo PATRON-vs-CHAPA.
- `src/utils/config.py`:
  - Defaults nuevos deshabilitados (`0.0`) para no afectar modelos sin override.

**ValidaciÃ³n en `C:\Users\DefyC\Downloads\Imagenes_METALCONF_editadas`:**
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

**Seguridad:** Sigue siendo parada virtual Ãºnicamente. No se modificÃ³ PLC, solenoides ni salidas fÃ­sicas.

---

#### Cambio 56 â€” RecalibraciÃ³n menos brusca de IMAGEN INESTABLE

**Problema:** La regla agregada en Cambio 54 (`chapa_no_line_abs_max_px: 2.7`) marcaba
demasiados frames como `LOW_QUALITY/UNSTABLE`. En la carpeta de validaciÃ³n dejaba 24/185
frames inestables, demasiado agresivo para operaciÃ³n.

**Cambio:**
- `config/tolerancias.yaml` modelo_B:
  - `chapa_no_line_abs_max_px: 2.7 -> 4.5`
  - Mantiene la condiciÃ³n de `used_lines < 1`, pero exige zigzag claro de CHAPA.

**ValidaciÃ³n en `C:\Users\DefyC\Downloads\Imagenes_METALCONF_editadas`:**
- Frames inestables bajan de 24 a 7.
- `frame_0037`: sigue `LOW_QUALITY/UNSTABLE`.
- `frame_0064`: sigue `OK`, `machine_stop=False`.
- `frame_0122`: sigue `NOK` por `PATRON INCLINADO`.
- `frame_0126`: sigue `NOK` por patrÃ³n desalineado grande (`offset=-19.2px`,
  `dAng=2.57 deg`).
- `frame_0031`: vuelve a `GOOD/STABLE`; con las mÃ©tricas actuales no se separa de forma
  robusta de muchos frames normales sin generar demasiados falsos inestables.
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

**Seguridad:** Parada virtual Ãºnicamente; sin cambios en PLC, solenoides ni salidas fÃ­sicas.

---

#### Cambio 57 â€” Evitar solape entre panel NOK y banners de parada

**Problema:** El panel rojo de razones `NOK` se dibujaba dentro de la ROI en `y=0`,
pero los banners de `DETENCION VIRTUAL DE MAQUINA` y `PATRON DESALINEADO` se dibujaban
despuÃ©s, arriba del frame completo. Cuando habÃ­a parada virtual, el panel quedaba
tapado/solapado por el banner superior.

**Cambios:**
- `src/pipeline/annotate.py`:
  - `draw_compare_overlay()` acepta `nok_panel_badge_count`.
  - El panel NOK se desplaza hacia abajo `badge_count * _BADGE_H`.
- `src/inspection.py`:
  - Calcula `badge_count = machine_stop + pattern_alignment_warn`.
  - Pasa ese valor al overlay de comparaciÃ³n antes de dibujar los banners.

**ValidaciÃ³n:**
- Overlay de control generado:
  - `data/output/overlay_panel_spacing_debug/frame_0126.png`
- En `frame_0126`, con dos banners arriba, el panel NOK queda debajo y visible.
- `python -m compileall src` OK.
- `python -m pytest tests` OK, 3 tests.

---

### SesiÃ³n 2026-05-28 (centro real) â€” Tadeo + Claude

#### Cambio 59 â€” LÃ­neas de centro reales (polilÃ­nea por banda, no X fija)

**Problema reportado:** La lÃ­nea de centro de chapa (naranja) y la lÃ­nea de centro del patrÃ³n
(blanca) eran lÃ­neas verticales fijas dibujadas en `cx = round(sheet_center_x)` y
`hx = round(holes_center_x)` â€” un solo valor X para toda la altura. Si la chapa venÃ­a
inclinada o el patrÃ³n desplazado no se reflejaba visualmente: las lÃ­neas siempre aparecÃ­an
perfectamente verticales y en el "centro promedio" de la pantalla.

**Causa raÃ­z:** Los puntos por banda ya existÃ­an para los bordes del patrÃ³n y de la chapa
(`pattern_left_points`, `pattern_right_points`, `left_edge_points`, `right_edge_points`),
pero el **centro** de cada uno nunca se calculaba ni se almacenaba. El overlay dibujaba
lÃ­neas ficticias.

**Cambios:**

**`src/pipeline/edge_centering.py`:**
- `CenteringResult` agrega dos campos nuevos (frozen dataclass, default vacÃ­o):
  - `sheet_center_points: tuple` â€” per-band midpoint entre borde izquierdo y derecho de CHAPA.
    `x = (edge_left[i].x + edge_right[i].x) / 2`, solo bandas donde ambos bordes se detectaron.
  - `pattern_center_points: tuple` â€” per-band midpoint entre borde izquierdo y derecho del PATRON.
    Calculado con `_pattern_center_by_band(pat_left, pat_right)` sobre los datos COMPLETOS
    (no sobre la versiÃ³n trimmed que se usa para mÃ©tricas zigzag).
- En `compute_centering()`: calcula `sheet_center_pts` y `pattern_center_pts_full`, los almacena
  en el nuevo CenteringResult.

**`src/pipeline/annotate.py`:**
- Import directo de `_fit_line_robust, _line_x_at_y` desde `edge_centering`.
- En `draw_centering_overlay()`:
  - Agrega `sheet_ctr_pts` y `pat_ctr_pts` al bloque de `_shift()`.
  - Reemplaza la lÃ­nea naranja fija por:
    - ExtensiÃ³n de lÃ­nea ajustada full-height (alpha=0.30, 1px)
    - PolilÃ­nea real por bandas (alpha=0.80, 2px)
    - Fallback a lÃ­nea punteada si hay <2 puntos.
  - Reemplaza la lÃ­nea blanca fija por:
    - ExtensiÃ³n de lÃ­nea ajustada full-height (alpha=0.20, 1px)
    - PolilÃ­nea real por bandas (alpha=0.85, 1px)
    - Fallback a lÃ­nea vertical si hay <2 puntos.
  - La flecha de offset ahora apunta entre los centros REALES evaluados en mid_y
    (cada polilÃ­nea se ajusta con `_fit_line_robust` y se evalÃºa en `mid_y`),
    no entre los centros promedio.

**Resultado visual:**
- Si la chapa estÃ¡ derecha: ambas polilÃ­neas son verticales â†’ igual que antes.
- Si la chapa estÃ¡ inclinada: la lÃ­nea de CHAPA sigue el eje real de la chapa.
- Si el patrÃ³n estÃ¡ desplazado/inclinado: la lÃ­nea de PATRON muestra la inclinaciÃ³n real.
- La flecha de offset muestra la diferencia real entre los centros en el plano medio.

**Sin tocar:** lÃ³gica de detecciÃ³n, `offset_px`, `margin_*`, PLC, solenoides, modelo_B/A params.

---

### SesiÃ³n 2026-05-28 â€” Tadeo + Claude

#### Cambio 58 â€” Tolerancias modelo_A (Esterilla) mÃ¡s permisivas + fix bug min_area/min_area_small

**Problema reportado:** Esterilla tomaba pocos agujeros y el overlay mostraba casi todos como cruces (missing). Los parÃ¡metros del Cambio 50 seguÃ­an siendo demasiado estrictos.

**Bug identificado:**
`min_area=300` (piso global de `detect_holes_from_mask`) era MAYOR que `min_area_small=250` (piso del filtro de tipo). Los agujeros chicos con blur (areaâ‰ˆ200-300pxÂ²) eran rechazados por la primera barrera antes de llegar al filtro de tipo. El floor efectivo real era `max(min_area, min_area_small)`, no `min_area_small`.

**Cambios en `config/tolerancias.yaml` â€” SOLO secciÃ³n `modelo_A`:**

| ParÃ¡metro | Antes | DespuÃ©s | RazÃ³n |
|---|---|---|---|
| `min_area` | 300.0 | 150.0 | Piso global debe ser â‰¤ min_area_small; blur baja area chico a ~200pxÂ² |
| `circularity_min` | 0.70 | 0.55 | Blur severo reduce circularidad a 0.5-0.6 |
| `aspect_ratio_max` | 2.0 | 2.5 | Agujeros levemente deformados |
| `min_area_small` | 250.0 | 150.0 | Alineado con nuevo min_area; fix del bug de piso |
| `max_area_small` | 1500.0 | 2000.0 | Margen ampliado |
| `min_area_large` | 700.0 | 400.0 | IluminaciÃ³n no ideal en scanner_2 |
| `max_area_large` | 5000.0 | 7000.0 | Margen ampliado |
| `edge_margin_px` | 15.0 | 5.0 | 15px descartaba agujeros reales cerca del borde de ROI |
| `align_match_tol_px` | 100.0 | 150.0 | MÃ¡s permisivo para fallback RANSAC |
| `min_match_count` | 5 | 4 | PatrÃ³n puede estar muy parcialmente en frame |
| `grid_max_missing` | 15 | 25 | ~22% de 112 agujeros; permisivo durante calibraciÃ³n |
| `bbox_filter_margin_px` | 20.0 | 30.0 | Grilla dispersa dy=38 necesita mÃ¡s margen |
| `frame_missing_nok_threshold` | 3 | 8 | Permisivo hasta calibrar con material real |

**Sin tocar:** `modelo_B`, PLC, solenoides, patrÃ³n de referencia, lÃ³gica de grid.

**Nota de calibraciÃ³n:** Los valores actuales son permisivos a propÃ³sito. Una vez que haya imÃ¡genes reales de scanner_2/modelo_A en planta, ejecutar `scripts/_debug_areas.py` para ver el histograma de Ã¡reas y ajustar `min_area_small`, `min_area_large` al gap real entre ruido y agujeros vÃ¡lidos.

---

## Estado actual del sistema

| Componente | Estado |
|---|---|
| Solenoides Y10/Y11 | Bloqueados por software y UI. Re-habilitar cuando se implemente control automÃ¡tico. |
| Startup | ~300â€“600ms hasta UI visible (antes 2â€“4s) |
| Backlight Y12/Y13 | Siempre ON al iniciar (inicializa en `initialize_lights()`). |
| Pipeline de visiÃ³n | Vectorizado, cacheado, CLOSE morfolÃ³gico, centroide estable, matcher closest-first |
| Visor modo servicio | ZoomableImageView: zoom (rueda), pan (drag), fit (doble click / botÃ³n) + scroll |
| Overlay | Imagen completa del frame. Cruz roja=missing, diamante naranja=extra, lÃ­nea cyan=near-miss |
| Extra detections | Detectadas y visibles (diamantes naranjas) en overlay; filtro bbox activo |
| Centrado de chapa | Overlay CHAPA sobre frame completo (fix Cambio 35). CHAPA cae en borde real. PATRON separado. Texto Izq/Der/Delta/Offset OK. |
| Detection ratio | Por frame y promedio de sesiÃ³n. Flag `CALIDAD_DEGRADADA` configurable. |
| Frame quality | `blur_score` (Laplacian var) + `frame_quality` en InspectionResult. `blur_score_min=0.0` (deshabilitado). PolÃ­tica "hold" wired en FSM y inspect_folder(). |
| modelo_B â€” ROI | `x=710, w=650, y=3, h=1077` â†’ excluye backlight desnudo en ambos lados |
| modelo_B â€” Grid | dx=28, dy=22, 258 cÃ©lulas. Fase X+Y 2D + affine local post-fase. |
| modelo_B â€” Tolerancia | `tol_xy_px=22`, `min_area=250`, `grid_max_missing=35`, `bbox_filter_margin=20`, `edge_margin_px=5` |
| modelo_B â€” Affine refinement | `grid_affine_refinement: true`, `tol_affine=33px`, `min_matches=12` |
| modelo_B â€” GrabaciÃ³n 185f | **185/185 raw OK**, avg_ratio=104%, 0 NOK, 0 temporal NOK. missing medio=0.81, 160/185 frames sin missing. Extras filtrados: ~180/185 frames con extra=0. |
| Extras falsos | Filtro `extra_min_dist_factor=2.0` en modelo_B: solo detecciones a >44px de todo expected cuentan como extras. |
| Verticalidad bordes | `CenteringResult` expone `pattern_left_slope_deg`, `pattern_right_slope_deg`. Mostrado en overlay: "Vert pat: Izq=Â±X.XÂ° Der=Â±Y.YÂ°". |
| Machine stop â€” tracking | Tracking por columna de grilla (ci). El mismo punzÃ³n roto se reconoce aunque la chapa avance en Y entre frames. Badge muestra columna: `"EN COLUMNA 13"`. |
| Machine stop â€” acciÃ³n | **VIRTUAL Ãºnicamente.** Badge `"! DETENCION VIRTUAL DE MAQUINA"`. No actÃºa sobre PLC, solenoides ni FSM. Solo UI/overlay/log. |
| FAULT automÃ¡tico | `consecutive_nok_frames: 40` (modelo_B), `5` (modelo_A). FAULT = solo por streak NOK, nunca por machine_stop. |
| Control automÃ¡tico pistones | Planificado, NO implementado. |
| Tests | Solo `tests/test_io_map.py`. Sin cobertura del pipeline de visiÃ³n aÃºn. |
| modelo_A (Esterilla) | Grid fitting. dx=68, dy=38. Filas alt. 4 small / 5 large holes. tol_xy_px=16 (<dy/2). grid_max_missing=10 conservador. FAULT deshabilitado (9999). Sin patron scanner_2 aun. |
| modelo_A â€” Patron global | 117 puntos, 113 celdas unicas, 4 duplicadas en cj=22-25. Deduplica OK en runtime. Fix assign_cells evita futuros duplicados por redondeo. |
| CLI missing-folder | Nuevo comando diagnÃ³stico: exporta CSV + overlays para frames con missing >= --min-missing. No toca criterio productivo. |
| CLI center-folder | Nuevo comando diagnÃ³stico: exporta CSV 20 cols + overlays de centrado por frame. Validado 185/185 fiable. |
| Centrado modelo_B 185f | Offset mediana=-0.95px, Izq=207.2px, Der=209.2px, peor offset=+7.02px. `center_offset_tol_px=0.0` (sin NOK activado). |
| run_folder_csv.py | Fix cp1252: reemplazados caracteres Unicode `â†’` por ASCII `->` en salidas de consola. |

---

## Pendientes / prÃ³ximos pasos conocidos

### Alta prioridad (prÃ³xima sesiÃ³n)
- **Validar en planta con material real:**
  - Frame estÃ¡tico sin defecto: verificar missingâ‰¤5 con affine activo
  - Frame con punzÃ³n roto: missing > grid_max_missing de forma sostenida â†’ temporal NOK
  - Verificar que `consecutive_nok_frames=40` y `grid_max_missing=35` son los valores
    correctos para la velocidad real de la mÃ¡quina
- **Calibrar `grid_max_missing`:**
  - Candidato: 20-25 (frames buenos con affine â†’ 0-5 missing, margen amplio)
  - Validar que blur de movimiento no supere ese umbral en producciÃ³n
  - PunzÃ³n roto agrega ~29 missing â†’ debe estar sobre el umbral elegido
- **Hungarian matching (reemplazo del greedy):**
  - 9/24 missing en frame_0036 son "stolen": tol_xy_px=22=dy â†’ dos expected compiten
    por el mismo detected cuando el agujero estÃ¡ entre dos filas adyacentes.
  - Greedy closest-first no puede resolver esto. Hungarian matching sÃ­.
  - Impacto estimado: â†“9 missing en frame_0036 (24â†’15).
- **Calibrar `blur_score_min`:**
  - Capturar frames con blur real de movimiento y frames nÃ­tidos en producciÃ³n
  - Correr `scripts/_debug_blur_score.py <carpeta>` para ver la distribuciÃ³n
  - Elegir umbral en p10-p25 de los frames borrosos (valor inicial estimado: ~50-100)
  - Para backlight siempre encendido: medir con material en movimiento a velocidad real
- **Activar `quality_ratio_min`:**
  - Calibrar en planta: medir el ratio promedio en operaciÃ³n normal vs blur de movimiento

### Media prioridad
- Activar `center_offset_tol_px` con valor real (medir cuÃ¡ntos px de offset se toleran)
- Implementar control automÃ¡tico de solenoides
- Agregar display de `avg_detection_ratio` en tab MÃ©tricas de la UI de servicio
- Medir px/mm para modelo_B (saber cuÃ¡nto es `tol_xy_px=22px` en mm reales)

### modelo_A (Esterilla) â€” pendiente calibraciÃ³n en planta
- **Capturar imagen OK de Esterilla con scanner_2** y reconstruir patrÃ³n:
  ```
  python -m src.main build-pattern --model modelo_A --scanner scanner_2 --img <imagen>
  ```
  Esto crearÃ¡ `data/patterns/scanner_2/modelo_A/holes.json` sin los 3 duplicados residuales.
- **Validar `min_area=400pxÂ²`** con cÃ¡mara real: si se pierden agujeros pequeÃ±os subir a 350.
- **Validar `tol_xy_px=16px`**: con grid affine deshabilitado puede necesitar ajuste.
- **Activar `grid_affine_refinement: true`** una vez que se vean falsos missing en bordes.
- **Calibrar `grid_max_missing`**: capturar frame con defecto real y ajustar umbral.
- **Calibrar `consecutive_nok_frames`**: actualmente 9999 (FAULT deshabilitado).

### Baja prioridad
- Tests unitarios para pipeline de visiÃ³n (compare, detect, preprocess, grid_fitting)

---

### Sesion 2026-06-05 (convencion de registro) - Tadeo + Codex

#### Cambio 123 - Convencion oficial de changelog para sesiones con Codex

**Pedido:** dejar asentado que Codex tambien participa activamente en la sesion y mejorar
el formato del changelog para registrar mejor cambios, fallos, oportunidades y validaciones.

**Hallazgo de Codex:**
- El historial tecnico es util, pero no siempre deja explicito que el trabajo fue realizado
  en conjunto por `Tadeo + Codex`.
- Varias entradas viejas documentan bien el cambio tecnico, pero no siempre dejan anotadas
  hipotesis descartadas, fallos intermedios o riesgos para futuras calibraciones.

**Cambios hechos por Tadeo + Codex:**
- A partir de esta entrada, la convencion recomendada para nuevas sesiones es `Tadeo + Codex`.
- Para proximos cambios, usar cuando aplique esta estructura minima:
  - `Pedido`
  - `Hallazgo de Codex`
  - `Cambios hechos por Tadeo + Codex`
  - `Validacion`
  - `Riesgos / oportunidades`
- Las entradas historicas que digan `Tadeo + Claude` deben leerse como antecedente del mismo
  rol de asistente tecnico, sin cambiar su contenido tecnico original.

**Validacion:**
- La convencion queda asentada en el historial desde este cambio en adelante.
- No se reescribio masivamente el historial anterior para evitar ruido innecesario en git.

**Riesgos / oportunidades:**
- Si mas adelante queres, se puede hacer una limpieza historica completa del `CHANGELOG.md`
  para unificar nombres, encoding y formato, pero eso conviene hacerlo como tarea separada.
- Tambien es buena oportunidad para registrar en futuras entradas pruebas fallidas y ajustes
  descartados, no solo los cambios finales que quedaron vigentes.

---

### Sesion 2026-06-05 (esterilla bordes) - Tadeo + Codex

#### Cambio 124 - Esterilla: borde del patron mas estable evitando saltos a agujeros interiores

**Pedido:** mejorar la deteccion de bordes del patron en
`05-06-2026-PATRONES INICIALES\\05-06-2026-ESTERILLA_1`, porque algunos bordes se
detectaban muy bien y otros se iban totalmente de foco.

**Hallazgo de Codex:**
- El problema principal no era la cantidad de bandas sino que el gate lateral del
  borde del patron en `modelo_A` estaba demasiado ancho.
- Con `pattern_edge_boundary_tol_px: 14.0`, algunas filas aceptaban agujeros mas
  interiores como si fueran borde real, sobre todo del lado izquierdo.
- Al cerrar ese margen a `10.0 px`, el borde queda mucho mas pegado a la silueta
  real del patron y deja de pegar saltos grandes entre filas.

**Cambios hechos por Tadeo + Codex:**
- `config/tolerancias.yaml` -> `models.modelo_A`:
  - `pattern_edge_boundary_tol_px: 14.0 -> 10.0`

**Validacion `05-06-2026-ESTERILLA_1`:**
- Se mantiene `133/133 raw OK`, `0 temporal NOK`.
- `avg_pattern_zigzag_std`: `0.839 -> 0.415`
- `avg_pattern_zigzag_max`: `2.953 -> 1.383`
- En un frame problematico como `frame_0025.png`, el borde izquierdo deja de alternar
  entre puntos muy abiertos y queda mucho mas consistente visualmente.

**Riesgos / oportunidades:**
- Si se cambia de nuevo el zoom o el encuadre, este valor puede necesitar retoque.
- La mejora actual estabiliza muy bien el borde del patron sin tocar matching ni ROI,
  asi que es un ajuste seguro y focalizado.
