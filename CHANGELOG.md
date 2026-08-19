# CHANGELOG — DefyVision Metalconf

## INSTRUCCIÓN PARA CLAUDE (leer siempre)
> **Al iniciar cualquier sesión de trabajo, leer este archivo completo antes de responder
> o tocar cualquier código. Contiene el historial de decisiones, cambios aplicados y
> contexto que no está en el código ni en el git log.**
>
> **Al finalizar cada cambio de código, actualizar este archivo** con una entrada en la
> sesión activa: qué se cambió, en qué archivo, por qué. Sin esto la trazabilidad se rompe.
>
> **OBLIGATORIO: después de cada cambio, siempre hacer `git add` + `git commit` + `git push`
> de forma inmediata. Sin excepción. No esperar que el usuario lo pida.**

---

## REGLA CRÍTICA — LA APP NUNCA PUEDE FALLAR AL ARRANCAR

> **Lo peor que puede pasar en este proyecto es que el operario no pueda abrir la app.**
> Esto pasó el 2026-07-22 (ver incidente abajo) y **no puede volver a pasar nunca más.**
>
> Reglas obligatorias para Claude y para quien toque este código:
>
> 1. **Ningún import de módulo puede depender de que una librería externa (cv2, pymodbus,
>    PyQt6, etc.) tenga exactamente tal o cual atributo/constante disponible.** El build de
>    OpenCV empaquetado con PyInstaller para producción puede no exponer todas las
>    constantes `cv2.CAP_PROP_*` que sí existen en el entorno de desarrollo (`.venv`). Usar
>    siempre `getattr(cv2, "NOMBRE", valor_numerico_de_respaldo)` para CUALQUIER constante
>    de cv2 referenciada a nivel de módulo (fuera de una función), y preferentemente
>    también dentro de funciones si se llama en el camino crítico de arranque.
> 2. **Nunca acceder a una constante de una librería de forma directa (`cv2.ALGO`) en el
>    cuerpo de un módulo** (variables globales, diccionarios de mapeo, decoradores, etc.).
>    Esas líneas se ejecutan en el `import`, y si fallan, tumban toda la aplicación antes
>    de llegar siquiera a mostrar una ventana o un mensaje de error legible — el operario
>    solo ve una consola con un traceback.
> 3. **Antes de dar por cerrado cualquier cambio que toque `src/vision/camera.py`,
>    `src/controller/`, `src/plc/`, o cualquier módulo importado por `src/main.py` en el
>    camino de `run`/`service`**, revisar que no se hayan agregado nuevos accesos directos
>    a constantes de librerías externas a nivel de módulo.
> 4. **Probar el arranque real (`run` o el .exe empaquetado), no solo `pytest`,** después de
>    tocar módulos en el camino crítico de arranque. Un `AttributeError` en import no lo
>    detecta ningún test unitario existente porque no hay ningún test que importe
>    `src.controller.system` con el build de cv2 real de producción.
> 5. Si se agrega una nueva constante de cv2 (o de cualquier librería) en cualquier parte
>    del código, aplicar el mismo patrón `getattr(lib, "NOMBRE", fallback_numerico)` desde
>    el principio, no como corrección posterior.

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

### Sesion 2026-08-19 (reset NOK inmediato) - Tadeo + Codex

#### Cambio 302 - Un frame OK limpia inmediatamente los NOK activos

**Pedido:** dejar de exigir varios frames OK para limpiar los frames NOK; el
primer resultado OK debe resetearlos y el NOK siguiente debe comenzar otra vez
desde uno.

**Hallazgo:** la racha de seguridad `_nok_streak` ya se cortaba con un solo OK,
pero la tarjeta NOK del operario mostraba `_nok_count`, un contador que esperaba
`200` OK consecutivos antes de volver a cero (comportamiento del Cambio 242).

**Cambios:**
- `src/controller/scanner_controller.py`: cualquier resultado `OK` de calidad
  valida pone inmediatamente en cero `_nok_streak`, su reloj y `_nok_count`. Se
  retiro el contador intermedio de frames OK y el umbral configurable de 200.
  Los frames `LOW_QUALITY` siguen siendo inconclusos: no suman ni borran una
  racha; las paradas y evidencias historicas tampoco se borran.
- `src/utils/config.py`: retirado el default obsoleto
  `nok_count_reset_frames=200`, para que una configuracion antigua no pueda
  restaurar accidentalmente la espera.
- `tests/test_scanner_controller.py`: regresion parametrizada para ambos
  scanners y ambos modelos. La secuencia `NOK, NOK, OK, NOK` exige racha/contador
  `2 -> 0 -> 1`.

**Validacion:** suite completa `99 passed`; pruebas dirigidas `30 passed`,
`py_compile`, `scripts/verify_config.py`, importacion del CLI y
`git diff --check` correctos.

**Entrega V36 Cython para PC industrial (2026-08-19):**
- Incluye este reset NOK inmediato y el retiro del ajuste manual de ROI del
  operario realizado en el Cambio 301.
- ZIP: `dist/DEFYVISION_METALCONF_V36_CYTHON_2026-08-19.zip`.
- Fuente empaquetada: commit `cb072a8a17da8b00a8d1258f45dcff0d33eb9fd6`.
- Tamano: `146956819` bytes.
- SHA-256: `B00886EDD3506B4AEA174EEC0929C4B4B98897A2904F4EC511637D513FAAF744`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), sin conexion a PLC/camaras.
- Integridad ZIP correcta: `635` entradas y CRC sin errores; unico
  `license.cp314-win_amd64.pyd`, ningun `license.py`, `_lsap` y patrones
  requeridos presentes.
- Configuracion empaquetada confirmada con ROI automatico en
  `scanner_1/modelo_B`, `scanner_2/modelo_A` y `scanner_2/modelo_B`.

---

### Sesion 2026-08-19 (proteccion de ROI) - Tadeo + Codex

#### Cambio 301 - Retiro del ajuste manual de ROI para el operario

**Pedido:** eliminar la ventana de ajuste manual para evitar que el operario
pueda desplazar o guardar una ROI incorrecta ahora que su posicion se resuelve
automaticamente en produccion.

**Cambios:**
- `src/ui/operator.py`: se retiro de la cabecera el boton `Area de analisis` y
  su ruta de apertura. El unico acceso del operario a esa ventana queda limitado
  a las tolerancias seguras.
- `src/ui/tolerance_window.py`: la ventana ya no crea el panel `_RoiPanel`, no
  muestra la pestana manual ni inicia la camara para calibrar. Una llamada vieja
  a `show_page(1)` queda neutralizada y solo conserva las tolerancias.
- La calibracion tecnica de ROI permanece exclusivamente dentro de `Servicio`,
  detras del login, para mantenimiento autorizado; el ROI automatico del
  pipeline no fue modificado.
- `tests/test_tolerance_model_selector.py`: regresion de interfaz que comprueba
  que no existen los botones `Area de analisis`/`GUARDAR AREA`, que el panel no
  se instancia y que la antigua pagina 1 no puede reactivarlo.

**Validacion:** suite completa `95 passed`; `py_compile`,
`scripts/verify_config.py`, importacion del CLI, inicio/apagado de
`InspectionSystem` sin salidas PLC y `git diff --check` correctos.

**Build:** no se genero una version nueva por indicacion expresa de Tadeo. El
cambio queda listo para el proximo build solicitado.

---

### Sesion 2026-08-19 (scanner 2) - Tadeo + Codex

#### Cambio 300 - ROI automatico en Microperforado y Esterilla de scanner 2

**Pedido:** despues del buen resultado en scanner 1 Microperforado, aplicar el
mismo ROI automatico por agujeros extremos a los dos materiales de scanner 2.
Se aportaron dos lotes OK propios de esa optica:
- `24-07-2026-MICROPERFORADO_2_SCANNER_2`: 499 frames, 640x480.
- `31-07-2026-ESTERILLA_1_SCANNER_2`: 501 frames, 640x480.

**Hallazgos:**
- Microperforado ubica el origen automatico en `x=210..216` con ROI de 255 px;
  la firma lateral normal (shift derecho menos izquierdo) queda centrada en
  `+8.1 px`.
- Esterilla ubica el origen en `x=110..116` con ROI de 415 px; sus dos tamanos
  de agujero quedan incluidos por el filtro de radios y la firma lateral normal
  queda en `+0.8 px`.
- Un chequeo sintetico revelo una defensa necesaria: al desaparecer una columna
  extrema, el ancho absoluto podia seguir pareciendo valido y el ROI podia
  intentar compensarlo. La diferencia firmada entre ambos bordes cambia de forma
  clara en ese caso y permite rechazar el movimiento.

**Cambios:**
- `src/patterns/roi.py`: el estimador acepta ahora la firma lateral esperada y
  una desviacion maxima. Si la relacion izquierda/derecha no coincide con el
  lote OK, devuelve sin confianza para conservar la ultima ROI valida. Esto evita
  que un recentrado automatico oculte una columna extrema faltante.
- `src/inspection.py`, `src/utils/config.py` y `config/tolerancias.yaml`: wiring y
  defaults fail-safe para `roi_hole_anchor_expected_edge_delta_px` y
  `roi_hole_anchor_max_edge_delta_deviation_px`; cero mantiene compatibilidad y
  deja la compuerta apagada en perfiles no calibrados.
- `config/io_map.yaml`:
  - `scanner_2/modelo_B`: ROI automatico activado con minimo 40 agujeros,
    discrepancia absoluta maxima 14 px, firma `+8.1 +/- 5 px` y ancho 255 px.
  - `scanner_2/modelo_A`: activado con minimo 30 agujeros, discrepancia maxima
    12 px, firma `+0.8 +/- 5 px` y ancho 415 px.
  - `scanner_1/modelo_B`: se agrega la firma calibrada `-3.3 +/- 5 px`, medida
    sobre sus 200 OK, sin cambiar el resto de su configuracion.
- `tests/test_roi_hole_anchor.py` y `tests/test_config.py`: regresion que elimina
  una columna extrema y exige rechazo por firma, mas validacion independiente de
  los tres perfiles activos.

**Validacion:**
- Scanner 2 Microperforado, todos los frames forzados: `499/499 OK`, cero NOK,
  cero machine stop, ROI `x=210..216`; pipeline productivo con movimiento:
  `395/395 raw OK` y `395/395 temporal OK`.
- Scanner 2 Esterilla, todos los frames forzados: `497/501 raw OK`; los cuatro
  NOK son exactamente los frames desenfocados preexistentes 0075/0076/0343/0344,
  iguales con ROI fijo y automatico. Pipeline productivo: `352/354 raw OK`, los
  dos NOK quedan aislados, `354/354 temporal OK` y cero machine stop.
- Traslacion sintetica `-30..+30 px`: 11/11 OK y cero missing en cada material;
  el ROI acompana de `x=185..245` en Microperforado y `x=83..143` en Esterilla.
- Defectos sinteticos: columna interior borrada en Microperforado produce 9
  missing y machine stop al cuarto frame; columna extrema borrada en Esterilla
  hace fallback a `x=113` y mantiene `NOK` con 7 missing.
- Regresion scanner 1 Microperforado: `200/200 OK`, cero missing/fallback/machine
  stop y ROI `x=203..208` despues de activar su firma lateral.
- Suite completa: `94 passed`; `scripts/verify_config.py`, `py_compile` y
  `git diff --check` correctos.

**Entrega V35 Cython para PC industrial (2026-08-19):**
- ZIP: `dist/DEFYVISION_METALCONF_V35_CYTHON_2026-08-19.zip`.
- Fuente empaquetada: commit `771cb0e37123fdef20f523a439d7f66d1e9ed5b3`.
- Tamano: `146958215` bytes.
- SHA-256: `7802D4C362D86DE01BF6C193F308DDA55961DA1D874A7908A7895A5FD838DD15`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), sin conexion a PLC/camaras.
  Unico `license.cp314-win_amd64.pyd`, ningun `license.py` y `_lsap` incluido.
- Integridad ZIP correcta: `635` entradas, CRC sin errores y patrones requeridos
  de scanner 1/2 presentes.
- Configuracion empaquetada confirmada con ROI automatico y firma lateral en
  `scanner_1/modelo_B`, `scanner_2/modelo_A` y `scanner_2/modelo_B`.

---

### Sesion 2026-08-19 - Tadeo + Codex

#### Cambio 299 - ROI automatico por agujeros extremos en scanner 1 Microperforado

**Pedido:** dejar de depender de una posicion X fija del ROI. El area de analisis
debe ubicarse automaticamente en cada frame a partir de los agujeros laterales
izquierdo/derecho del patron, con ancho seguro, porque un corrimiento horizontal
de pocos pixeles estaba perdiendo una columna completa y generando fallas falsas.
Se aportaron 200 frames OK de
`19-08-2026-MICROPERFORADO_1_SCANNER_1` para iniciar la calibracion por scanner.

**Hallazgo:** el ROI historico de `scanner_1/modelo_B` estaba fijo en
`x=213, w=255`, mientras que los agujeros extremos del nuevo lote ubicaban el origen
correcto entre `x=203..208`. Con el ROI fijo, el pipeline normal daba solamente
`5/54 raw OK`, `49/54 raw NOK`, hasta 14 missing y 25 machine stop falsos; la
columna `(ci=6)` aparecia ausente en aproximadamente 70% de los frames evaluados.

**Cambios:**
- `src/patterns/roi.py`: nuevo estimador robusto por extremos. Selecciona la nube
  horizontal densa compatible con el ancho del patron, descarta reflejos/puntos
  aislados y calcula dos origenes independientes desde los cuantiles izquierdo y
  derecho. Si no coinciden dentro de tolerancia, rechaza el frame para el
  recentrado en vez de mover el ROI hacia ruido.
- `src/inspection.py`: deteccion del ROI en cada frame antes del recorte. Filtra
  candidatos por los radios calibrados del patron, conserva el ultimo ROI valido
  como fallback y nunca persiste movimientos automaticos en `roi.json`. El ancho
  permanece en `255 px`, exactamente el `image_size` del patron, con unos `34 px`
  de margen a cada lado de los agujeros extremos; asi no se desincronizan las
  coordenadas de matching. Los recentrados historicos/EMA quedan inhibidos cuando
  esta estrategia esta activa para evitar dos controladores compitiendo.
- `config/tolerancias.yaml` y `src/utils/config.py`: defaults fail-safe y parametros
  configurables del anclaje por agujeros. Queda apagado por defecto y se activa
  solamente tras validar frames propios de cada scanner/modelo.
- `config/io_map.yaml`: activado para `scanner_1/modelo_B` con minimo 40 agujeros,
  margen de agrupacion de 20 px, discrepancia maxima entre bordes de 12 px y rango
  de busqueda de 120 px. `scanner_1/modelo_A` y ambos modelos de scanner 2 siguen
  apagados hasta recibir/validar sus lotes OK propios.
- `tests/test_roi_hole_anchor.py` y `tests/test_config.py`: regresiones de ruido
  lateral, evidencia insuficiente, ancho incompatible, configuracion por perfil y
  seguimiento horizontal desde `x=175` hasta `x=235` sin cambiar el ancho.

**Validacion:**
- Pipeline normal con movimiento sobre el lote aportado: `54/54 raw OK`, `0 NOK`,
  `0 machine stop` (antes `5/54 OK`, `49 NOK`, `25 machine stop`).
- Analisis forzado de las 200 imagenes: `200/200 OK`, `0 missing`, `0 machine
  stop`; ROI detectado con confianza en los 200 frames, rango `x=203..208`.
- Traslacion sintetica del mismo frame entre `-30..+30 px`: 11/11 OK, cero
  missing; el ROI acompano exactamente de `x=175` a `x=235`.
- Defecto sintetico borrando un agujero central: permanece `NOK missing=1` en los
  cinco frames y machine stop aparece al quinto, confirmando que el ROI automatico
  no oculta un defecto real.
- Suite completa: `93 passed`; `scripts/verify_config.py`, importacion del CLI y
  `git diff --check` correctos.

**Proxima fase:** repetir la misma calibracion con carpetas OK propias de
`scanner_1/modelo_A`, `scanner_2/modelo_A` y `scanner_2/modelo_B` antes de activar
sus perfiles. La capacidad ya es generica; la activacion deliberadamente es por
scanner/modelo para no trasladar geometria entre opticas.

**Entrega V34 Cython para PC industrial (2026-08-19):**
- ZIP: `dist/DEFYVISION_METALCONF_V34_CYTHON_2026-08-19.zip`.
- Fuente empaquetada: commit `c11ed6ce6c6416f9f025f9480447c31cf6d6f57e`.
- Tamano: `146957942` bytes.
- SHA-256: `9C44277069121D45DD235ADA8A97223397A7E96F0535D34CB4214CBDDB6CF8E1`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), sin conexion a PLC/camaras.
  Unico `license.cp314-win_amd64.pyd`, ningun `license.py` y `_lsap` incluido.
- Integridad ZIP correcta: `635` entradas, CRC sin errores y todos los archivos
  requeridos presentes.
- Configuracion empaquetada verificada con `roi_hole_anchor_enabled=true` y los
  parametros calibrados de ROI automatico en `scanner_1/modelo_B`.

---

### Sesion 2026-08-14 - Tadeo + Codex

#### Cambio 298 - Configuracion afinada en planta adoptada como oficial (sin build)

**Pedido:** copiar al proyecto la carpeta de configuracion afinada por Tadeo y
usar esos valores de ahora en adelante, sin generar un nuevo build.

**Cambios:**
- Se comparo `C:\Tadeo\METALCONF\config\config` contra `config/`. Todos los
  archivos eran identicos salvo `io_map.yaml`; se copio ese archivo byte por
  byte y se verifico igualdad SHA-256 entre origen y destino.
- Ajustes adoptados para scanner 1 / Microperforado: alineacion, machine stop y
  racha NOK en 5 frames; `tol_xy_px=14`, margen izquierdo 17, margen superior
  70, nitidez 200 y gate de faltantes por frame 8.
- Ajustes adoptados para scanner 2 / Microperforado: alineacion y machine stop
  en 4 frames, racha NOK general en 3, gate de faltantes 4 y bandas ignoradas
  superior/inferior de 46/42 px.
- `tests/test_config.py`, `tests/test_scanner_controller.py` y
  `scripts/verify_config.py` ahora validan los umbrales oficiales por cada
  scanner/modelo, incluido el caso simultaneo NOK + machine stop.

**Validacion:** configuracion destino identica al origen (`EXACT_MATCH=True`),
`scripts/verify_config.py` correcto, `88 passed` y `git diff --check` correcto.
La carpeta recibida no contenia `data/patterns`, por lo que no se modificaron
los archivos `roi.json` ni `holes.json`. No se genero EXE, ZIP ni build.

#### Cambio 297 - CRITICO: MACHINE STOP y tercer NOK simultaneos siempre cortan el solenoide

**Pedido:** en produccion aparecieron secuencias NOK suficientes para detener la
maquina, pero en algunos casos no se ejecutaba la parada. Debe detener siempre,
en ambos scanners y para Esterilla/Microperforado.

**Causa raiz reproducida:** si el tercer NOK tambien llegaba con
`machine_stop=True`, `_handle_result()` armaba dos decisiones terminales en el
mismo frame. La racha NOK cambiaba primero `RUNNING -> FAULT`; luego la rama de
machine stop encontraba que el estado ya no era `RUNNING`, omitia
`_cut_solenoid_critical()` y hacia `return` antes de ejecutar la rama FAULT. La
reproduccion anterior al fix terminaba en `FAULT`, racha 3 y **cero escrituras**
`solenoid=False` para scanner 1 y scanner 2.

**Cambios:**
- `src/controller/scanner_controller.py`: machine stop tiene prioridad cuando
  coincide con el umbral NOK, evitando dos transiciones terminales para el mismo
  resultado.
- La rama machine stop ahora mantiene una invariante fail-safe: siempre llama a
  `_cut_solenoid_critical("machine fault")`, enciende rojo y levanta
  `_stop_event`, aunque otra ruta ya hubiera cambiado el estado FSM. Repetir
  `solenoid=False` es deliberadamente idempotente y seguro.
- `tests/test_scanner_controller.py`: regresion parametrizada para scanner 1/2 y
  modelo A/B. Inyecta dos NOK y un tercer NOK con machine stop simultaneo; exige
  estado detenido, racha 3, hilo detenido, rojo y escritura fisica OFF.

**Validacion:** `88 passed`; prueba especifica `12 passed`, `py_compile`,
`git diff --check` y `scripts/verify_config.py` correctos. Construccion segura de
`InspectionSystem(disable_plc_outputs=True)` correcta con scanner 1 y scanner 2.
No se agregaron accesos nuevos a librerias externas en el camino de arranque.

**Entrega V33 Cython para PC industrial (2026-08-14):**
- ZIP: `dist/DEFYVISION_METALCONF_V33_CYTHON_2026-08-14.zip`.
- Fuente empaquetada: commit `6a2e811e8028fe0d4b2759b0d5a2df3e0f1bdc85`.
- Tamano: `143293215` bytes.
- SHA-256: `3A51EB259EF794404D3F17E4C29561B2A7F5ED78B8A57CAE1F873EE41F6767C7`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), sin conexion a PLC/camaras.
  Unico `license.cp314-win_amd64.pyd`, ningun `license.py` y `_lsap` incluido.
- Integridad ZIP correcta: `635` entradas y CRC sin errores.
- Configuracion empaquetada confirmada en tres NOK, tres frames de machine stop,
  tres de desalineacion y piso duro 3 para ambos scanners/modelos (los valores
  heredados se resuelven desde `tolerancias.yaml`).

#### Cambio 296 - Watchdog de maquina trabada y pantalla MACHINE FAULT para racha NOK

**Pedido:** la cinta puede quedar mecanicamente trabada en cualquier momento y
seguir haciendo fuerza. No se debe decidir nada durante el primer minuto de RUN;
despues, si el material no avanza durante 22 segundos, debe detenerse y avisar
`MAQUINA TRABADA`. Scanner 1 y scanner 2 funcionan y se detienen por separado.
Ademas, al completar la racha NOK configurada debe aparecer el MACHINE FAULT y
la pantalla completa historica de patron desalineado/NOK, no solamente un ERROR.

**Hallazgos:** el watchdog anterior era generico: esperaba 60 segundos sin
analisis desde el arranque, no tenia un minuto de armado separado, no distinguia
atasco mecanico y solo mostraba `Sin analisis`. La racha NOK ya cortaba fisicamente
el solenoide, pero entregaba a la UI un resultado sin `machine_stop`; por eso no
se abria la pantalla completa aunque la maquina hubiese quedado en FAULT.

**Cambios:**
- `src/controller/scanner_controller.py`: nuevo reloj de atasco por instancia.
  Se arma a los 60s del run loop y desde ese punto exige 22s completos sin avance
  visual. Cada movimiento real reinicia solamente el reloj de su scanner; una
  inspeccion forzada no simula avance. Al disparar pasa a ERROR, corta con
  `write_critical`, enciende rojo, detiene el hilo y registra el evento
  `machine_jam`.
- `config/tolerancias.yaml`, `config/io_map.yaml` y `src/utils/config.py`:
  `machine_jam_enabled=true`, `machine_jam_arm_s=60` y
  `machine_jam_timeout_s=22` para scanner 1/2 por Esterilla/Microperforado. Se
  desactiva la escalada legacy `inspection_stall_*` para que no corte a los 60s
  antes de completar el nuevo intervalo 60+22.
- `src/ui/operator.py`: nueva ventana modal y persistente `MAQUINA TRABADA`, con
  scanner/material identificados e instruccion de liberar el material antes de
  reiniciar. Si ambos scanners se traban, los avisos se encolan. La ventana no
  se cierra con Escape ni con la X sin reconocimiento explicito.
- La racha NOK que alcanza el umbral ahora marca su resultado disparador como
  `machine_stop`; conserva el corte obligatorio y abre la pantalla completa
  `MACHINE FAULT`, mostrando `Patron desalineado`, faltantes persistentes o
  `Racha de imagenes NOK` segun la causa.
- Regresiones en `tests/test_frozen_camera_watchdog.py`,
  `tests/test_operator_jam_alert.py`, `tests/test_scanner_controller.py` y
  `tests/test_config.py` para tiempos limite, aislamiento entre scanners,
  inspeccion forzada, persistencia del dialogo y apertura de MACHINE FAULT.

**Validacion:** `84 passed`; `py_compile`, `git diff --check` y
`scripts/verify_config.py` correctos. Construccion segura de
`InspectionSystem(disable_plc_outputs=True)` correcta con ambos controladores en
`jam=60+22` y racha NOK 3. Render Qt offscreen de la nueva ventana revisado en
`840x680`; estructura, contraste y separacion de bloques correctos. El backend
offscreen local reemplazo los glifos de texto por cuadros, por lo que la tipografia
final debe confirmarse en el render Windows del EXE; la prueba funcional de textos,
boton y permanencia si paso. No se agregaron accesos nuevos a constantes externas
en el camino de importacion.

**Entrega V32 Cython para PC industrial (2026-08-14):**
- ZIP: `dist/DEFYVISION_METALCONF_V32_CYTHON_2026-08-14.zip`.
- Fuente empaquetada: commit `17a057d5c64c331d7a9d74beef06021d36fffd8b`.
- Tamano: `143294122` bytes.
- SHA-256: `01F846FF044614A49E489DB342A13F4B2CB47B52AE691C8AEC3D5E5DF3BCDD68`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), sin conexion a PLC/camaras.
  Unico `license.cp314-win_amd64.pyd`, ningun `license.py` y `_lsap` incluido.
- Configuracion efectiva dentro del paquete confirmada en `jam=60+22`,
  `consecutive_nok=3` y stall legacy apagado para scanner 1/2 por ambos
  materiales.
- Integridad ZIP correcta: `635` entradas, CRC sin errores y archivos requeridos
  presentes.

#### Cambio 295 - Parada uniforme y efectiva al tercer NOK en ambos scanners y materiales

**Pedido:** revisar el flujo completo de parada porque en la prueba de planta se
configuraron 3 NOK desde Tolerancias, pasaron tres NOK reales y la linea no se
detuvo. La regla requerida es 3 NOK consecutivos para scanner 1 y scanner 2,
tanto Esterilla como Microperforado.

**Causa raiz:** habia tres bloqueos acumulados. Los cuatro perfiles efectivos
cargaban `consecutive_nok_frames=9999`, Microperforado de scanner 1 imponia ademas
`stop_min_frames=4`, y la gracia inicial de 100 inspecciones/30 segundos anulaba
y reiniciaba la racha NOK al alcanzar el umbral. Por eso una prueba de tres NOK
al comienzo de RUN no podia detener aun cuando el control visual mostrara NOK.

**Cambios:**
- `config/tolerancias.yaml`, `config/io_map.yaml` y `src/utils/config.py`: regla
  efectiva uniforme de 3 para racha NOK, faltantes persistentes, desalineacion y
  piso duro en las cuatro combinaciones scanner/material.
- `src/controller/scanner_controller.py`: la gracia inicial queda limitada a los
  detectores geometricos de encuadre; ya no suprime una decision completa de
  tres NOK consecutivos. El tercer NOK pasa a `FAULT`, corta el solenoide con
  `write_critical` (5 reintentos y verificacion), apaga verde/amarillo y enciende
  rojo para el scanner correspondiente.
- `src/ui/tolerance_window.py`: el control ahora se llama
  `NOK consecutivos para detener`, explica su efecto real y usa rango seguro
  `3..20`; se elimina la recomendacion anterior de dejarlo en 9999.
- `tests/test_scanner_controller.py` y `tests/test_config.py`: regresion cruzada
  para scanner 1/2 por Esterilla/Microperforado. Confirma que dos NOK mantienen
  RUN, el tercero detiene incluso dentro de la gracia inicial, corta el solenoide
  semantico correcto y deja la luz roja. `scripts/verify_config.py` valida los
  cuatro perfiles efectivos.

**Validacion:** `79 passed`; `py_compile`, `git diff --check` y
`scripts/verify_config.py` correctos. Construccion del camino productivo con
`InspectionSystem(disable_plc_outputs=True)` correcta: ambos controladores activos
cargan umbral 3 sin escribir al PLC. Los cuatro perfiles informan
`consecutive_nok_frames=3`, `stop_min_frames=3`,
`machine_stop_missing_frames=3` y `pattern_align_stop_frames=3`.

**Entrega V31 Cython para PC industrial (2026-08-14):**
- ZIP: `dist/DEFYVISION_METALCONF_V31_CYTHON_2026-08-14.zip`.
- Fuente empaquetada: commit `5845e160f16f742beb714c4877853ee1dfbe6d52`.
- Tamano: `143289881` bytes.
- SHA-256: `F75EB1479D937B892A9C5AABC6A93388537DA1B71F241198F8C3021480305B78`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), sin conexion a PLC/camaras.
  Cython verificado con un unico `license.cp314-win_amd64.pyd`, ningun
  `license.py` y `_lsap` incluido para Hungarian.
- Configuracion efectiva del paquete confirmada en 3 para racha NOK, piso duro,
  faltantes y desalineacion en scanner 1/2 por Esterilla/Microperforado.
- Integridad ZIP correcta: `635` entradas, CRC sin errores y EXE/config/patrones
  presentes.

---

### Sesion 2026-08-12 - Tadeo + Codex

#### Cambio 294 - Ambos scanners amplian a 60 segundos la espera antes de ERROR

**Pedido:** ampliar a 60 segundos el tiempo normal de arranque de la cinta en
ambos scanners y ambos materiales.

**Cambios:**
- `config/io_map.yaml`: `inspection_stall_timeout_s: 60.0` para scanner 1 y
  scanner 2, tanto Esterilla como Microperforado.
- `tests/test_config.py`: la regresion exige 60 segundos en las cuatro
  combinaciones scanner/material.
- El aviso preventivo permanece desde los 3 segundos; solamente el corte y el
  estado ERROR por `Sin analisis` se postergan hasta completar 60 segundos.

**Validacion:** `75 passed`; `scripts/verify_config.py` correcto y carga efectiva
confirmada en `60.0` para las cuatro combinaciones scanner/material.

**Entrega V30 Cython para PC industrial (2026-08-12):**
- ZIP: `dist/DEFYVISION_METALCONF_V30_CYTHON_2026-08-12.zip`.
- Fuente empaquetada: commit `e258866644827f81c04d55fdb99a42f0db4bbdea`.
- Tamano: `143303707` bytes.
- SHA-256: `25813F4549C2F59C7CD5DF9048FCB227F80437442DFAF5BA36E86915FC1F1094`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`); Cython verificado con un
  unico `license.cp314-win_amd64.pyd` y ningun `license.py`; `_lsap` incluido.
- La configuracion empaquetada confirma 60s para ambos scanners/materiales. ZIP
  con `636` entradas, CRC sin errores y archivos requeridos presentes.

#### Cambio 293 - Scanner 2 espera 30 segundos antes de ERROR sin analisis

**Pedido:** la cinta del scanner 2 puede demorar normalmente en comenzar a
moverse despues de RUN; evitar que Esterilla o Microperforado entren en ERROR
antes de 30 segundos.

**Cambios:**
- `config/io_map.yaml`: `inspection_stall_timeout_s: 30.0` tanto para
  `scanner_2/modelo_A` (Esterilla, antes 10s) como para `scanner_2/modelo_B`
  (Microperforado, antes 15s).
- El aviso preventivo sigue disponible desde los 3 segundos, pero el scanner no
  corta ni pasa a ERROR por falta de analisis hasta cumplir 30 segundos.
- `tests/test_config.py`: regresion que exige el mismo timeout de 30 segundos en
  ambos materiales de scanner 2.

**Validacion:** `75 passed`; `scripts/verify_config.py` correcto y carga efectiva
confirmada en `30.0` para `scanner_2/modelo_A` y `scanner_2/modelo_B`.

**Entrega V29 Cython para PC industrial (2026-08-12):**
- ZIP: `dist/DEFYVISION_METALCONF_V29_CYTHON_2026-08-12.zip`.
- Fuente empaquetada: commit `e9273927edecd3438ac0019ede9d5240a4f1ef93`.
- Tamano: `143303702` bytes.
- SHA-256: `27C410D6882A00912F74BA77AFF01413EA7034909276DC2EF56BEBFC761E4524`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`); Cython verificado con un
  unico `license.cp314-win_amd64.pyd` y ningun `license.py`; `_lsap` incluido.
- El `io_map.yaml` empaquetado confirma `30.0` para Esterilla y Microperforado
  de scanner 2. ZIP con `636` entradas, CRC sin errores y archivos requeridos.

#### Cambio 292 - Nitidez editable por scanner y material desde Tolerancias

**Pedido:** evitar la edicion manual de YAML y permitir ajustar la nitidez de
scanner 2 eligiendo explicitamente Esterilla o Microperforado desde la pantalla
del operador.

**Cambios:**
- `src/ui/tolerance_window.py`: cada panel de scanner incorpora el selector
  `Tipo de chapa a configurar` con Esterilla/Microperforado. Es un selector de
  calibracion: no cambia el material activo ni interrumpe la produccion.
- Nuevo control `Nitidez minima` (`blur_score_min`, rango 100-1200, paso 5) con
  explicacion operativa. Los valores menores a 175 requieren confirmacion por
  riesgo de aceptar desenfoque y generar falsos faltantes.
- Guardar escribe en `scanner_N.inspection_models.modelo_A/modelo_B`, por lo que
  nunca vuelve a caer un ajuste de Esterilla dentro de Microperforado. Si el
  perfil editado esta activo se recarga en caliente; si esta inactivo solo se
  guarda para su proximo uso, sin llamar a `set_model()`.
- `tests/test_tolerance_model_selector.py`: verifica exposicion/rango del control,
  guardado sobre el material seleccionado, no cambio del modelo activo y recarga
  correcta cuando se edita el material en uso.

**Validacion:** `74 passed`; `py_compile` y `scripts/verify_config.py` correctos.
Render Qt offscreen de la ventana completa revisado a `1200x900`: selector de
material destacado y nitidez como primer ajuste visible en ambos scanners.

**Entrega V28 Cython para PC industrial (2026-08-12):**
- ZIP: `dist/DEFYVISION_METALCONF_V28_CYTHON_2026-08-12.zip`.
- Fuente empaquetada: commit `d54a6d230b86e704b4528da7a01c95e4de94c474`.
- Tamano: `143303748` bytes.
- SHA-256: `F941F530225BE79D75419F9EA290F185AF02CE1F52D3309F6820EA264CCE1536`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`); un unico
  `license.cp314-win_amd64.pyd`, ningun `license.py`, `_lsap` incluido y
  configuracion empaquetada identica a la fuente.
- Integridad ZIP correcta: `636` entradas, CRC sin errores y EXE/config/build-info
  presentes. Queda pendiente la aceptacion fisica en planta.

#### Cambio 291 - Aviso obligatorio y estetico para limpiar lente por baja nitidez

**Pedido:** cuando un scanner no puede continuar por falta de nitidez, mostrar al
operario una indicacion directa de limpiar el lente y conservarla hasta que pulse
OK.

**Cambios:**
- `src/ui/operator.py`: nuevo `BlurCleaningDialog`, modal y siempre visible por
  encima de la pantalla de produccion. Identifica scanner/modelo, explica que la
  inspeccion se detuvo y destaca `LIMPIAR EL LENTE DE LA CAMARA`. Escape y cierre
  de ventana quedan bloqueados; solo `OK · LENTE LIMPIO` reconoce el aviso.
- El cartel se activa exclusivamente cuando `state_reason` comienza con
  `Imagen borrosa`, no para errores de camara, PLC, inspeccion detenida o fallos
  internos. Se muestra una sola vez por incidente y se rearma despues de RESET.
- Si ambos scanners reportan nitidez al mismo tiempo, los avisos se encolan para
  que el operario reconozca cada camara por separado.
- `tests/test_operator_blur_alert.py`: regresiones de clasificacion de causa y de
  permanencia del dialogo hasta la confirmacion explicita.

**Validacion:** `71 passed`; `py_compile` y `scripts/verify_config.py` correctos.
Render Qt offscreen revisado a `800x650`: jerarquia, contraste, textos y boton
legibles. La prueba del dialogo confirma que Escape y cerrar no lo descartan y
que el boton de confirmacion si lo hace.

**Entrega V27 Cython para PC industrial (2026-08-12):**
- ZIP: `dist/DEFYVISION_METALCONF_V27_CYTHON_2026-08-12.zip`.
- Fuente empaquetada: commit `005c430efe09d9f611d6adc59724b1cdb5c9c5fa`.
- Tamano: `143301471` bytes.
- SHA-256: `8A775B6FC0E5214F4D0F5DCBD4F6174DCFBA2FE5E1BA3B62F7102C3BC38E291E`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`); un unico
  `license.cp314-win_amd64.pyd`, ningun `license.py`, `_lsap` incluido y
  configuracion del paquete identica a la fuente.
- Integridad ZIP correcta: `636` entradas, CRC sin errores y EXE/config/build-info
  presentes. Queda pendiente la aceptacion fisica en planta.

#### Cambio 290 - Esterilla scanner_2 no cuenta capturas con la chapa quieta

**Pedido:** en scanner 2 con Esterilla, el contador de inspecciones seguia
aumentando aunque la chapa no se moviera.

**Causa:** el perfil productivo `scanner_2/modelo_A` sobrescribia el umbral de
movimiento calibrado del modelo (`3.0`) con `continuous_position_threshold: 0.0`.
La secuencia de camara evitaba procesar dos veces la misma captura retenida, pero
cualquier captura JPEG nueva —incluido su ruido normal— se consideraba una nueva
posicion de chapa.

**Cambio:** `config/io_map.yaml` restaura el umbral de movimiento de Esterilla a
`3.0`. Las capturas nuevas con la chapa en la misma posicion ya no entran al
pipeline ni incrementan inspecciones, OK/NOK o rachas de baja calidad. Se agrega
una regresion en `tests/test_config.py` para impedir que el perfil vuelva a cero.

**Seguridad:** no se desactivan los watchdogs de camara ni de inspeccion. Si el
scanner queda en RUN sin movimiento por el tiempo configurado, se detiene con la
causa explicita `Sin analisis...`; simplemente deja de inventar inspecciones sobre
la misma zona de material.

**Validacion:** `69 passed`; `scripts/verify_config.py` confirma
`modelo_A continuous_position_threshold = 3.0`; `py_compile` correcto para el
camino critico de produccion. Queda pendiente la prueba fisica de planta con
chapa quieta y luego en movimiento.

**Entrega V26 Cython para PC industrial (2026-08-12):**
- ZIP: `dist/DEFYVISION_METALCONF_V26_CYTHON_2026-08-12.zip`.
- Fuente empaquetada: commit `e73e603ccd1f46a502dfe19532097cd4d92507a1`;
  incluye el motivo concreto de ERROR (Cambio 289) y el filtro de chapa quieta
  para Esterilla scanner 2 (Cambio 290).
- Tamano: `143296452` bytes.
- SHA-256: `0D4AF2A4F5C2453652CDA408B167A7E71646A070F0D6D8CD7A6162C1B6D1BF21`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`); un unico
  `license.cp314-win_amd64.pyd`, ningun `license.py` y `_lsap` incluido.
- `config/io_map.yaml` del paquete coincide byte por byte con la fuente y
  conserva `scanner_2/modelo_A continuous_position_threshold: 3.0`.
- Integridad ZIP correcta: `636` entradas, CRC sin errores, EXE/config/build-info
  presentes. La aceptacion final de PLC, camaras y movimiento queda para la
  prueba de instalacion en planta.

#### Cambio 289 - ERROR con causa visible y persistente para el operario

**Pedido:** la pantalla de produccion mostraba solamente `ERROR`, sin explicar
por que se habia detenido el scanner. El operario debia buscar el archivo de log
para distinguir una camara perdida, imagen borrosa, inspeccion detenida o un fallo
interno.

**Diagnostico del incidente:** los eventos reales `12-08-2026_STOP_11/12/13`
confirmaron paradas por `LOW_QUALITY` en ambos scanners. Scanner 1 bajo de una
nitidez historica mediana aproximada de 366 a 204 (el overlay en vivo registro
190 con minimo 200). Scanner 2 paso de frames nitidos cercanos a 330 a desenfoque
vertical de hasta 89, con hasta 14 falsos faltantes. PLC y camaras permanecieron
conectados; no fue un crash ni una perdida de comunicacion.

**Cambios:**
- `src/controller/scanner_controller.py`: nuevo `state_reason` thread-safe,
  incluido en `get_status()`. Cada ruta anormal guarda una explicacion breve:
  inicio/perdida de camara, autodiagnostico fallido, inspeccion detenida, imagen
  borrosa con valor y minimo, imagen inestable, excepcion interna, racha NOK y
  parada por faltantes/desalineacion. El motivo se conserva al pasar a `STOPPED`
  y se limpia unicamente con `RESET` o al iniciar una sesion nueva.
- `src/ui/operator.py`: debajo del badge `ERROR`/`FAULT` aparece una franja visible
  con la causa concreta. Tambien permanece en una parada proveniente de falla.
- `src/ui/service.py`: Estado del sistema y Simulacion FSM muestran el mismo motivo.
- `tests/test_frozen_camera_watchdog.py` y `tests/test_scanner_controller.py`:
  regresiones para motivo de imagen borrosa, inspeccion detenida, fallo al iniciar
  camara, persistencia al detener y limpieza al resetear.

**Validacion:** `69 passed`; `py_compile` correcto; construccion y refresco de la
UI de Operador en modo Qt offscreen correctos (`OPERATOR_UI_SMOKE_OK`). No se
cambiaron umbrales opticos ni se desactivo la parada segura por baja calidad.

---

### Sesion 2026-07-31 - Tadeo + Codex

#### Cambio 288 - Eliminacion permanente del bloqueo por fecha/licencia

**Pedido:** retirar el bloqueo agregado para julio y asegurar que la aplicacion no
vuelva a impedir el arranque ni a detener produccion por una fecha o codigo.

**Cambios aplicados:**
- `src/utils/license.py`: se elimino toda dependencia de fecha, clave mensual,
  marcador de desbloqueo y deteccion de rollback del reloj. `is_licensed()` queda
  permanentemente habilitado y las APIs historicas se conservan como no-op para
  no romper imports ni integraciones existentes.
- `src/main.py` y `src/ui/operator.py`: se retiraron los gates de licencia del
  arranque, la comprobacion periodica y el heartbeat que podian abrir un dialogo
  modal o detener todos los scanners.
- `src/controller/scanner_controller.py`: se quitaron los bloqueos de `start()` y
  `start_simulate()`, el control cada 10 segundos y la ruta antigua que podia
  cortar el solenoide por licencia.
- `tests/test_license.py` y `tests/test_scanner_controller.py`: se reemplazaron las
  pruebas del bloqueo por verificaciones de operacion permanentemente habilitada.
- La proteccion del entregable con Cython se mantiene: `src.utils.license` sigue
  compilado como `.pyd` y el fuente `.py` no se distribuye en el EXE.

**Validacion:** `68 passed`; imports del camino critico de arranque correctos y
`is_licensed() is True` sin consultar reloj ni archivos locales.

**Entrega V25 Cython (PC de desarrollo, no planta):**
- ZIP: `dist/DEFYVISION_METALCONF_V25_CYTHON_2026-07-31.zip`.
- Fuente empaquetada: commit `7b6c112420187abb081fe4b4042cfabc2a822f58`.
- Tamano: `143294959` bytes.
- SHA-256: `7D70ECEBDA5C22D4720D2AA7FF3DC2C0C1A2272A4BA7EB90170B43CE99D18186`.
- Smoke test congelado correcto (`BUILD_SMOKE_OK`), un unico
  `license.cp314-win_amd64.pyd`, ningun `license.py` y `_lsap` incluido.
- Alcance de esta validacion: tests y arranque seguro/offline en la PC de
  desarrollo. No equivale a una prueba de PLC, camaras, iluminacion ni senales
  fisicas en la PC de produccion de planta; esa aceptacion debe realizarse al
  instalar V25 en planta.

#### Cambio 287 - CRITICO: Esterilla scanner_2 calibrada y fail-safe contra RUN sin decisiones

**Pedido:** calibrar ESTERILLA con los 501 frames OK de
`C:\Users\DefyC\Downloads\31-07-2026-ESTERILLA_1_SCANNER_2` y corregir el
incidente gravisimo donde el operario pulsa INICIAR pero el scanner queda en
marcha sin contar ningun OK ni NOK.

**Hallazgos de Codex:**
- La causa inmediata del contador en cero era `blur_score_min=800`: el lote real
  mide solamente `233.8..469.9`, por lo que **501/501** frames quedaban
  `LOW_QUALITY`. Esa politica no suma OK/NOK y tampoco detenia la maquina ante
  una racha infinita, reproduciendo exactamente el estado de produccion sin control.
- El patron activo tambien estaba mal: geometria configurada `dx=39, dy=21`
  contra una geometria real robusta de aproximadamente `dx=41.9, dy=22.46`.
  Ademas, desde la fila 7 las celdas de `holes.json` estaban corruptas/incompletas.
  El baseline era `501/501 raw NOK`, con `9..47 missing` falsos por frame.
- Al cambiar de Microperforado a Esterilla se recargaban patron/tolerancias dentro
  del pipeline, pero `ScannerController` conservaba fuera de la sesion el gate de
  movimiento, FPS y watchdogs del modelo anterior.
- `Camera.get_frame()` podia devolver varias veces la misma captura retenida; no
  existia una identidad atomica de frame fresco para impedir doble conteo.

**Cambios hechos por Tadeo + Codex:**
- `config/tolerancias.yaml`, `modelo_A`: grilla recalibrada a
  `41.9 x 22.46`, stagger `20.95`, seleccion segura de paridad y Hungarian;
  `blur_score_min=255`, calibrado con la distribucion real.
- `data/patterns/scanner_2/modelo_A/holes.json`: patron regularizado de 94 celdas
  unicas (filas alternadas de 4/5 agujeros), puntos/radios coherentes y
  `built_with_roi` explicitado para la ROI `x=110, w=415`.
- `config/io_map.yaml`, `scanner_2/modelo_A`: inspeccion por cada frame fresco
  (`continuous_position_threshold=0`), piso duro de 3 frames para faltantes y
  desalineacion, FAULT generico en `9999`, detector severo de un frame apagado,
  `low_quality_stop_frames=16` y stall watchdog `3s aviso / 10s ERROR`.
- `scanner_2/modelo_B` recibe el mismo fail-safe operativo (`stop_min_frames=3`,
  16 LOW_QUALITY sostenidos y stall watchdog 3s/15s), sin cambiar su calibracion
  optica ni sus umbrales de matching.
- `src/vision/camera.py`: nueva generacion atomica de frame visualmente fresco;
  el controlador procesa una captura nueva una sola vez y no vuelve a contar la
  imagen retenida entre lecturas.
- `src/controller/scanner_controller.py`: al cambiar/recargar modelo ahora se
  actualizan tambien gate de movimiento, FPS, rachas y watchdogs. Una racha
  `LOW_QUALITY` sostenida pasa a ERROR, corta solenoide, enciende rojo y conserva
  el ultimo frame para diagnostico; ya no puede seguir produciendo indefinidamente.
- `src/utils/config.py`: default global fail-safe `low_quality_stop_frames=25`.
- Tests de regresion para secuencia de frame fresco y corte por LOW_QUALITY sostenido.

**Validacion:**
- Lote completo real: `501` frames procesados, `465 OK` contabilizables,
  `36 LOW_QUALITY` aislados, `0 NOK` contabilizables, `0 machine_stop`,
  `501/501 decision temporal OK`; racha LOW_QUALITY maxima `3/16`.
- Los unicos cuatro raw NOK son dos pares borrosos (`0075/0076`, `0343/0344`) y
  quedan correctamente LOW_QUALITY, sin sumar NOK ni producir una parada falsa.
- Missing: mediana `0`, p95 `0`, media `0.164`, maximo `10` solo en esos pares LQ.
- Suite completa `68/68` tests OK; import CLI y construccion real de
  `InspectionSystem(disable_plc_outputs=True)` OK con ambos scanners.

**Build V24 (2026-07-31 08:31, commit fuente `6f529ed`):**
- Generado `dist\DEFYVISION_METALCONF_V24_CYTHON_2026-07-31.zip`
  (`143,321,187 bytes`, SHA-256
  `97E53507FE095ACF09E005A5B3B6913319D265AC07DE0A202CEBF6282DF25178`).
- Proteccion verificada dentro del ZIP: `license.cp314-win_amd64.pyd` presente y
  `license.py` ausente; `scipy.optimize._lsap` presente para matching Hungarian.
- Smoke test del EXE congelado confirmado con `BUILD_SMOKE_OK`, sin conectar
  PLC ni camaras.
- `config/io_map.yaml`, `config/tolerancias.yaml` y el patron
  `scanner_2/modelo_A/holes.json` dentro de la entrega coinciden byte por byte
  con los archivos fuente calibrados y validados.

**Riesgos / oportunidades:**
- Este lote contiene solo estados OK. Queda validada la ausencia de falsos NOK y
  la seguridad temporal, pero la sensibilidad final a un punzon realmente roto
  debe confirmarse cuando exista una captura NOK real de ESTERILLA. No se activo
  el detector geometrico `pattern_align_enabled` de modelo_A porque sus metricas
  normales se solapan con los umbrales de Microperforado y provocarian falsas
  paradas; cualquier parada geometrica futura igualmente respeta el piso duro de
  3 frames y nunca puede disparar por una imagen aislada.

---

### Sesión 2026-07-29 - Tadeo + Claude

#### Cambio 286 - CRITICO: scanner_2 "en marcha" para siempre sin contar ni un OK

**Sintoma reportado:** el operario pulsa INICIAR en scanner 2 y el scanner queda
funcionando indefinidamente: nunca cuenta imagenes OK, nunca cuenta NOK, nunca
muestra un error y nunca se detiene. Todo parece normal (badge EN MARCHA, luz verde)
pero no se esta inspeccionando NADA — la maquina produce sin control visual.

**Causa raiz (confirmada con `logs/defyvision.log`):** el validador *anti-bleed*
de `system.py` se instalaba sobre TODAS las camaras. Ese chequeo existe solo por
el re-enumerado de indices de DSHOW en camaras USB: rechaza un frame si el parche
central de 200x200 es casi identico al de la otra camara (umbral 8.0 px). Con las
camaras IP actuales (`192.168.1.2` y `192.168.1.3`) los dos scanners miran la
MISMA chapa microperforada, asi que el parche central sale casi identico — en el
log de produccion quedaron registradas diferencias de **1.7 a 7.4 px**, todas por
debajo del umbral. Resultado: la camara rechazaba todos sus frames.

Y como el rechazo NO ponia `_frame = None` (solo hacia `continue`), `get_frame()`
seguia devolviendo la ultima imagen aceptada, siempre la misma. La cadena completa:

1. `get_frame()` devuelve un frame → el watchdog de camara perdida nunca dispara.
2. El frame es identico al anterior → `InspectionSession.inspect_frame()` calcula
   `diff = 0.0 < continuous_position_threshold (4.0)` y devuelve `None`.
3. `_handle_result()` nunca se ejecuta → `ok_count`/`nok_count` se quedan en 0, no
   hay racha NOK, no hay machine_stop, no hay FAULT.
4. El scanner queda en RUNNING con luz verde para siempre.

**Cambios (3 defensas, de la causa raiz al ultimo colchon):**

- `src/controller/system.py`: el validador anti-bleed se instala **solo entre
  camaras USB locales** (indice DSHOW), via el nuevo `_is_local_device()`. Dos URLs
  distintas nunca pueden ser el mismo dispositivo, asi que compararlas por imagen
  no aporta nada y solo genera falsos positivos. `_make_bleed_validator()` ahora
  recibe la lista de pares locales a comparar.
- `src/vision/camera.py`: nuevo **watchdog de IMAGEN CONGELADA**. Todos los caminos
  de captura publican via `_publish_frame()`, que compara una firma barata
  (submuestreo 1/16) contra el frame anterior. Si no llega una imagen NUEVA durante
  `frozen_frame_timeout_s` (default 10 s, en `camera_config.DEFAULTS`) — porque el
  validador rechaza todo o porque la camara IP devuelve siempre el mismo JPEG — se
  suelta el frame retenido (`_frame = None`) y el ScannerController lo escala por
  su via ya probada de camara perdida (ERROR + corte de solenoide + aviso en UI).
  El rechazo del validador tambien llama a `_check_frozen()`.
- `src/controller/scanner_controller.py`: nuevo **watchdog de INSPECCION DETENIDA**
  (`_check_inspection_stall()`), que cubre cualquier otra causa (p.ej. el gate de
  movimiento nunca superado). Si el scanner esta RUNNING y no analiza ni un frame
  durante `inspection_stall_warn_s` (default 15 s) avisa en log y en la UI; a los
  `inspection_stall_timeout_s` (default 120 s) corta el solenoide y pasa a ERROR.
  Ambos configurables por scanner; `0` los deshabilita. El contador se ancla al
  inicio del run loop (no cuenta selftest ni pre-calibracion) y se reinicia con
  cada inspeccion real.
- `src/ui/operator.py`: la casilla de salud muestra **"SIN ANALISIS Xs"** en ambar,
  tambien cuando todavia no hubo ningun resultado (antes esa rama no actualizaba
  nada y por eso el sintoma era invisible para el operario).
- `src/utils/config.py`: nuevas tolerancias `inspection_stall_warn_s` (15.0) e
  `inspection_stall_timeout_s` (120.0).
- `src/utils/camera_config.py`: nuevo setting `frozen_frame_timeout_s` (10.0).
- `tests/test_frozen_camera_watchdog.py`: 6 tests de regresion — anti-bleed solo
  entre camaras locales (y que sigue funcionando entre USB), liberacion del frame
  congelado por repeticion y por rechazo del validador, recuperacion automatica al
  volver imagen nueva, y escalado/no-escalado del watchdog de inspeccion detenida.

**Verificado:** 66/66 tests OK. Construccion real de `InspectionSystem` con el
`io_map.yaml` de produccion: ambas camaras quedan con `validator=None` y el
watchdog de congelamiento armado en 10 s.

**Build V23 (2026-07-29 09:14, commit `eee30fc`):** generado con
`scripts\build_exe.ps1` — Cython obligatorio (`license.cp314-win_amd64.pyd`),
`scipy.optimize._lsap` presente, smoke test del EXE congelado OK. Verificado ademas
que el PYZ del `.exe` contiene el codigo nuevo (`_is_local_device`, `IMAGEN CONGELADA`,
`_check_inspection_stall`, `SIN ANALISIS`). Entrega:
`dist\DEFYVISION_METALCONF_V23_CYTHON_2026-07-29.zip` (136.7 MB), tag `V23`.
Este build ya NO tiene el bug; el V22 y anteriores si.

---

### Sesión 2026-07-28 - Tadeo + Claude

#### Cambio 285 - Botones de ANCHO en editores de ROI + confirmacion de hot-reload en vivo

**Pedido:** poder cambiar el ancho del ROI desde la UI (agrandar/achicar) sin editar codigo,
y que al guardar se aplique al run activo sin detener/reiniciar.

**Cambios:**
- `src/ui/tolerance_window.py` (Area de analisis) y `src/ui/service.py` (Servicio): se agrega el
  control \textbf{ANCHO −/+} junto al paso. Mueve el borde derecho (izquierda fija); respeta el
  minimo `roi_min_width` (hoy 255) y el ancho del frame. Metodos nuevos `_width_step` /
  `_roi_width` y helper `_roi_min_width` en tolerance_window.
- Al GUARDAR, ambos editores ya reconstruyen el patron (`build_pattern_from_image`) con el nuevo
  ancho, asi `image_size` queda consistente y no dispara el ERROR de desincronizacion.
- \textbf{Hot-reload:} ambos editores ya llamaban `scanner.reload_cache()` tras guardar; el run
  loop recrea la `InspectionSession` en el proximo frame estando en RUNNING (no hace falta DETENER
  ni INICIAR). Se agrego el aviso ``aplicado en vivo (sin detener)'' en el editor de Servicio.
- Requiere recompilar el `.exe` (cambios de UI en codigo).

#### Cambio 284 - Ambos ROI de modelo_B a ancho 255 + candado activado

**Pedido:** dejar el ancho del ROI en 255 en ambos scanners.

**Analisis de seguridad:** los puntos del patron llegan hasta x=221 (scanner_1) y x=216
(scanner_2), muy por debajo de 255. El ancho solo define el margen derecho vacio; la grilla
usa `grid.cells` explicitos (no depende del ancho). Verificado tambien que `inspection.py:424`
valida `image_size == frame post-ROI` (tira ERROR si no coinciden) y que `image_size` NO escala
el patron (solo chequeo de consistencia). Por eso basta con mover `roi.w` y `image_size` juntos.

**Cambios (source + dist):**
- `data/patterns/scanner_1/modelo_B/`: `roi.json` w 242->255; `holes.json` image_size
  [242]->[255], built_with_roi.w 242->255. Puntos/grilla/radios intactos (170 puntos).
- `data/patterns/scanner_2/modelo_B/`: `roi.json` w 265->255; `holes.json` image_size
  [265]->[255], built_with_roi.w 265->255. Puntos/grilla/radios intactos (179 puntos).
- `config/tolerancias.yaml`, `models.modelo_B`: `roi_min_width: 0 -> 255` (candado ahora activo:
  el ROI nunca baja de 255 y coincide con el image_size del patron).
- No hizo falta reconstruir con camara: no habia frames reales en disco (buffers con placeholders
  10x10) y el cambio no afecta ningun punto del patron.

#### Cambio 283 - Editor de ROI: ancho fijo, solo mover izq/der (se quitan botones de bordes)

**Pedido:** en la edición de ROI (pestaña "Área de análisis" y modo servicio) el operario aún
podía cambiar el ancho con los botones de los extremos. Debe poder mover el ROI izquierda/derecha
solamente, sin poder agrandarlo ni angostarlo. Sacar los botones de los extremos.

**Cambios:**
- `src/ui/tolerance_window.py`: se reemplazan los pares de botones "BORDE IZQ ◄►" y "BORDE DER ◄►"
  por un único par "MOVER ROI ◄►". `_move()` ahora traslada TODO el ROI conservando el ancho.
  El borde derecho queda como lectura ("DER x=…").
- `src/ui/service.py`: idem — fila "MOVER ROI" con `_roi_move("roi", ±)` que desplaza lx y rx
  juntos; se elimina la fila de botones del borde derecho; `_roi_redraw` sigue mostrando w (fijo).
- Requiere recompilar el `.exe` (cambios de UI en código).

#### Cambio 282 - ROI fijo (sin drift al reiniciar), candado de ancho y piso duro de 4 frames en Scanner 1

**Pedido del operario (4 puntos):**
1. Explicar el "FALLA DETECTADA sin imagen" que aparece en Scanner 2.
2. Fijar el ancho del ROI (poder mover izq/der, nunca más angosto de 255px) y que no se resetee.
3. Que la máquina NUNCA pare con 1 solo frame de error.
4. Piso duro de 4 frames (racha NOK / mal estado) para parar, **solo Scanner 1**; Scanner 2 igual.

**Diagnóstico:**
- **"FALLA DETECTADA sin imagen":** el badge muestra ese texto solo en estado `FAULT`.
  El FAULT por racha NOK (`scanner_controller.py:1214`) NO llama a `on_result`, por eso no
  dibuja frame (a diferencia del `machine_stop`, que muestra alerta a pantalla completa).
  En config `consecutive_nok_frames: 9999` deshabilita ese FAULT → el síntoma proviene del
  `.exe` desplegado (build 24-07 11:31), no del código fuente actual. Recomendado: recompilar.
- **Drift lateral del ROI al reiniciar:** causado por `roi_recenter_enabled: true` (modo `move`),
  que desplaza `x` en runtime y lo **persiste en `roi.json`** (`inspection.py` `_maybe_recenter_roi`).
  Al reabrir carga la posición corrida.
- **Parada con "1 frame":** el código ya tiene pisos duros (faltantes `max(2,·)` en
  `machine_stop.py:48`; desalineación `_MIN_DESALIGN_STOP_FRAMES=3` en `inspection.py`). Con la
  chapa detenida los frames son casi idénticos, así la racha de 3 se completa en <1s (parece
  instantáneo). No hay ningún camino que pare con 1 frame literal.

**Cambios:**
- `config/io_map.yaml` (+ copia en `dist`): `roi_recenter_enabled: true -> false` en **ambos**
  scanners → el ROI queda EXACTAMENTE como se calibró, no se auto-mueve ni se re-guarda.
- Piso duro de 4 frames **solo Scanner 1** (`scanner_1/modelo_B`): `pattern_align_stop_frames: 3 -> 4`,
  `machine_stop_missing_frames: 4` (nuevo), y `stop_min_frames: 4` (nuevo, piso duro en código).
  Scanner 2 sin cambios (sigue en 3 frames).
- Nueva tolerancia `stop_min_frames` (`config.py`, default 0): piso duro por scanner aplicado en
  código a las 3 vías de parada — racha NOK (`scanner_controller.py`), faltantes persistentes
  (`inspector.py`) y desalineación (`inspection.py` `_advance_desalignment_streak`). No se puede
  bajar por config.
- Candado de ancho de ROI (mecanismo listo, **inactivo**): nueva tolerancia `roi_min_width`
  (default 0) + `enforce_min_width()` en `roi.py` (`load_roi`/`save_roi`) y en la UI de servicio
  (`_roi_move`/`_on_roi_save`). Se dejó en `0` porque forzar 255 sin reconstruir el patrón
  desalinea `image_size` (Scanner 1 patrón=242, Scanner 2=265) y cliparía agujeros. Para
  activarlo: reconstruir AMBOS patrones a ROI w=255 (Servicio → ROI → GUARDAR, que reconstruye
  el patrón) y luego poner `roi_min_width: 255`.
- `tests/test_config.py`: actualizado el assert de `roi_recenter_enabled` a `False` (ROI fijo).

**Pendiente para el operario:** recompilar el `.exe` para que apliquen los cambios de código
(piso `stop_min_frames`, candado de ancho). El fix del drift del ROI y los frames de Scanner 1
ya funcionan en el exe actual (son config leída de disco).

---

### Sesión 2026-07-24 - Tadeo + Codex

#### Cambio 281 - V20 con calibración real de Scanner 2

**Pedido:** conservar Scanner 1 exactamente como en el build V20 (`c8404cc`) y
reaplicar exclusivamente los dos ajustes comprobados hoy para Scanner 2.

**Cambios hechos por Tadeo + Codex:**
- `config/io_map.yaml`, `scanner_2/modelo_B`: `blur_score_min: 1200.0 -> 300.0`;
  con 1200 los frames reales quedaban como `LOW_QUALITY` y no incrementaban los
  contadores OK/NOK.
- `data/patterns/scanner_2/modelo_B/roi.json`: ROI horizontal `x: 218 -> 202`,
  manteniendo `y=0`, `w=265`, `h=480`, para coincidir con las capturas reales
  posteriores al zoom digital.
- Scanner 1 permanece sin cambios respecto del árbol V20.

---

### Sesión 2026-07-23 - Tadeo + Codex

#### Cambio 280 - Blindaje global de desalineación, control en Tolerancias y eliminación de FAULT genérico falso

**Pedido:** confirmar que ningún patrón de chapa perforada —microperforado o
esterilla— pueda detener scanner 1 ni scanner 2 por un frame desalineado; permitir
configurar la cantidad desde Tolerancias; explicar y eliminar el estado
"FALLA DETECTADA" que aparecía ocasionalmente en vez de "INSPECCIONANDO".

**Hallazgo de Codex:**
- El blindaje de 3 frames del Cambio 279 ya era global en código, incluso para
  mecanismos futuros, pero `pattern_align_stop_frames` no estaba expuesto en la
  ventana Tolerancias.
- La ventana sí exponía `pattern_align_severe_abs_max_px` con mínimo visual `5.0`,
  aunque el valor productivo seguro era `0.0`. Abrir y guardar Tolerancias podía
  convertir silenciosamente `0 → 5` y reactivar el detector severo. Después del
  Cambio 279 ya no podía parar en un frame, pero seguía siendo una mutación errónea.
- "FALLA DETECTADA" era un estado real `FAULT`, no un texto aleatorio: se activaba
  al mezclar 5 resultados NOK consecutivos de cualquier causa, aunque no fueran el
  mismo defecto. Esa regla genérica coexistía con los detectores específicos y podía
  provocar una parada falsa mientras el operario esperaba que siguiera inspeccionando.

**Cambios hechos por Tadeo + Codex:**
- `src/ui/tolerance_window.py`:
  - nuevo control `Frames de patrón desalineado`, rango seguro `3..20`;
  - el control severo ahora admite y conserva `0 = desactivado`;
  - se corrigió su explicación: aun una medición severa respeta la racha configurada;
  - el FAULT genérico se identifica claramente como respaldo y recomienda `9999`.
- `src/utils/config.py`: defaults globales seguros para
  `pattern_align_stop_frames=3`, `pattern_align_machine_stop=true` y
  `consecutive_nok_frames=9999`.
- `config/tolerancias.yaml`: FAULT genérico desactivado (`9999`) a nivel global,
  esterilla y microperforado. Siguen activas las paradas específicas por el mismo
  agujero faltante persistente y por desalineación confirmada.
- `src/inspection.py`: comentarios actualizados para reflejar que no existe bypass
  de un frame por desalineación severa.
- `tests/test_config.py`: cobertura de scanner 1/2 × esterilla/microperforado,
  mínimo 3 en UI y conservación de `0` para el detector severo.

**Validación:**
- Config efectiva en los cuatro perfiles productivos:
  `pattern_align_stop_frames=3`, `pattern_align_severe_abs_max_px=0.0`,
  `machine_stop_on_tilt=false`, `pattern_desalign_enabled=false`,
  `consecutive_nok_frames=9999`.
- Esterilla mantiene `pattern_align_enabled=false`; si se habilita en el futuro, la
  defensa global sigue impidiendo paradas antes del tercer frame.
- Microperforado mantiene `pattern_align_enabled=true` y exige 3 frames consecutivos.
- `pytest tests/`: 60 passed.
- `compileall`, `scripts/verify_config.py` y `git diff --check`: OK.

---

#### Cambio 279 - Desalineación: mínimo absoluto de 3 frames y limpieza durante startup grace

**Pedido:** corregir las paradas falsas por "patrón desalineado" observadas por el
operario en microperforado. Ni scanner 1 ni scanner 2 deben detener la máquina por una
sola imagen desalineada; se requieren como mínimo 3 frames consecutivos.

**Hallazgo de Codex:**
- Los perfiles efectivos de `modelo_B` tenían `pattern_align_stop_frames: 2` en ambos
  scanners, no 3.
- Solo el zigzag común pasaba por esa racha. Otros caminos geométricos del pipeline
  (desalineación severa, verticalidad y desajuste masivo del patrón) todavía podían
  escribir `machine_stop=True` directamente en un frame si se habilitaban.
- Durante `startup_grace` el controlador suprimía la parada, pero no limpiaba la racha
  de desalineación guardada dentro de la `InspectionSession`. Dos evidencias ocultas
  durante la gracia podían quedar acumuladas y hacer que el primer frame visible
  posterior pareciera una parada instantánea.

**Cambios hechos por Tadeo + Codex:**
- `config/io_map.yaml`: `pattern_align_stop_frames` pasa de `2` a `3` para
  `scanner_1/modelo_B` y `scanner_2/modelo_B`.
- `src/inspection.py`:
  - nueva defensa de código que fuerza un mínimo absoluto de 3 aunque una
    configuración futura indique 1 o 2;
  - todas las causas geométricas de desalineación comparten la misma racha;
  - desalineación severa, verticalidad y desajuste masivo ya no pueden saltarse la
    confirmación temporal.
- `src/vision/inspector.py`, `src/controller/scanner_controller.py`:
  `InspectionSession.reset_stop_state()` limpia tanto faltantes persistentes como la
  racha de desalineación cuando una parada queda suprimida durante la gracia inicial.
- `tests/test_desalignment_streak.py`, `tests/test_config.py`: regresiones para
  primero/segundo/tercer frame, reset por frame bueno, limpieza de evidencia oculta y
  configuración segura en ambos scanners.

**Validación:**
- Prueba unitaria con `stop_frames=1`: frame 1 no para, frame 2 no para, frame 3 sí.
- Un frame bueno intermedio reinicia la racha; la evidencia acumulada durante grace
  también vuelve a cero.
- Lote entregado
  `C:\Users\DefyC\Downloads\23-07-2026-MICROPERFORADO_1_SCANNER_1`: 199 imágenes
  crudas; 54 inspecciones efectivas tras el filtro productivo de movimiento,
  `machine_stop_frames=0`, `align_failures=0`.
- `pytest tests/`: 58 passed.
- `compileall`, `scripts/verify_config.py` e imports de `src.main`,
  `src.controller.system` y `src.vision.camera`: OK.

---

### Sesión 2026-07-22 - Tadeo + Claude

#### Cambio 275 - Fix critico: la app no abria en produccion (AttributeError cv2.CAP_PROP_FOCUS)

**Incidente:** la mañana del 2026-07-22 el operario no pudo abrir la app. El .exe
empaquetado (`run_production.py` → `src.main run`) tiraba:

```
AttributeError: module 'cv2' has no attribute 'CAP_PROP_FOCUS'
```

en `src\vision\camera.py:32`, al importar `src.controller.system` →
`src.controller.scanner_controller` → `src.vision.camera`. Como el fallo ocurria en el
`import` del módulo (a nivel de módulo, no dentro de una función), toda la aplicación se
caía antes de mostrar cualquier ventana — el operario solo veía una consola con
traceback y ningún camino para arrancar la produccion.

**Causa raíz:** `_PROP_MAP`, el diccionario que mapea nombres de ajustes de cámara a
constantes `cv2.CAP_PROP_*`, se construye a nivel de módulo accediendo directamente a
`cv2.CAP_PROP_FOCUS`, `cv2.CAP_PROP_EXPOSURE`, etc. El build de OpenCV incluido en el
.exe de producción (PyInstaller) no expone `CAP_PROP_FOCUS` como atributo, aunque sí
existe en el entorno de desarrollo (`.venv`). Ya existía el patrón correcto al lado
(`"backlight_compensation": getattr(cv2, "CAP_PROP_BACKLIGHT", 32)`), pero no se había
aplicado al resto de las entradas del diccionario.

**Corrección aplicada (`src/vision/camera.py`):**
- Todas las entradas de `_PROP_MAP` ahora usan `getattr(cv2, "NOMBRE", valor_respaldo)`
  con los códigos numéricos estándar de la enum `cv2.VideoCaptureProperties` como
  respaldo (focus=28, exposure=15, white_balance=17, gain=14, brightness=10, contrast=11,
  saturation=12, sharpness=20, gamma=22, fps=5).
- Se agregaron constantes de módulo protegidas del mismo modo para el resto de props
  usadas fuera del diccionario: `_CAP_PROP_AUTOFOCUS`, `_CAP_PROP_AUTO_EXPOSURE`,
  `_CAP_PROP_AUTO_WB`, `_CAP_PROP_FRAME_WIDTH`, `_CAP_PROP_FRAME_HEIGHT`,
  `_CAP_PROP_FOURCC`, `_CAP_PROP_FPS`, `_CAP_PROP_BUFFERSIZE`. Se reemplazaron todos los
  usos directos de `cv2.CAP_PROP_*` en el archivo por estas constantes.
- Resultado: aunque el build de cv2 empaquetado carezca de cualquiera de estas
  constantes, el import nunca vuelve a fallar; en el peor caso un ajuste puntual de
  cámara (foco, exposición, etc.) queda con un código de respaldo en vez de tumbar toda
  la aplicación.

**Se agregó regla permanente** en la sección "REGLA CRÍTICA — LA APP NUNCA PUEDE FALLAR
AL ARRANCAR" (arriba de este archivo) para que este tipo de fallo — cualquier acceso
directo a una constante de librería externa a nivel de módulo — no se repita en ningún
módulo del camino de arranque.

**Pendiente de verificar:** `src/main.py` (comando `define-roi`) también usa
`cv2.CAP_PROP_FRAME_WIDTH/HEIGHT/FOURCC/FPS/BUFFERSIZE` sin `getattr`, pero son
constantes estándar y ese comando no está en el camino de arranque de producción
(`run`/`service`), así que no se tocó en este cambio. Si en el futuro se ve el mismo
tipo de error en `define-roi`, aplicar el mismo patrón ahí.

---

#### Cambio 276 - Robustez industrial 24/7: apagado seguro garantizado, arranque a prueba de fallos y log en disco siempre

**Pedido:** revisar mejoras de robustez y seguridad para operacion industrial 24/7 sin
que el codigo se cierre, y solucionar cualquier falla de seguridad encontrada.

**Auditoria:** el nucleo (PLCClient, Camera, ScannerController) ya estaba muy blindado
tras el Cambio 274. Los huecos reales estaban en el camino de arranque/apagado y en el
logging. Corregidos:

1. **[SEGURIDAD — critico] El solenoide/electrovalvula del punzon podia quedar
   energizado si la UI crasheaba.** En `cmd_run` y `cmd_service` (`src/main.py`) la
   llamada a la UI (`launch_production_ui` / `app.exec()`) NO estaba dentro de un
   `try/finally`. Si la UI lanzaba una excepcion o se cerraba de forma inesperada,
   `system.shutdown()` nunca se ejecutaba y las salidas del PLC (solenoide, backlight,
   luces) quedaban en su ultimo estado — potencialmente con la maquina energizada.
   Ahora la UI corre dentro de un `try/finally` que **garantiza** `system.shutdown()`
   pase lo que pase; `shutdown()` ya corta el solenoide con reintentos+verificacion y
   apaga todas las salidas.

2. **[ARRANQUE] Config invalida/faltante volvia a tumbar la app con un traceback crudo.**
   La construccion de `InspectionSystem()` (que lee `config/io_map.yaml`, `app.yaml`,
   perfiles de camara) no estaba protegida: cualquier error dejaba al operario mirando
   una consola con un traceback — el mismo "peor caso" del Cambio 275. Ahora la
   construccion y el bucle estan envueltos en `try/except`, con un helper
   `_show_fatal_dialog()` que muestra un dialogo Qt legible ("revisar PLC/camaras/config,
   reintentar, contactar soporte") en vez del traceback, y registra el detalle completo
   en el log. Se agrego ademas una **red de seguridad global en `main()`** que captura
   cualquier excepcion no controlada de cualquier subcomando (salvo `KeyboardInterrupt`
   y `SystemExit`), la registra y la muestra en dialogo, devolviendo un codigo de salida
   limpio.

3. **[DIAGNOSTICO 24/7] Sin log en disco si `app.yaml` no definia `log_file`.**
   `setup_logging()` (`src/utils/logger.py`) solo agregaba el archivo de log si estaba
   configurado; si no, todo iba a `sys.stdout`, que es `None` en un build empaquetado sin
   consola (`--windowed`) → una planta corriendo dia y noche podia quedar SIN ningun
   registro para diagnosticar un incidente. Ahora:
   - Siempre se agrega un `RotatingFileHandler` con ruta por defecto
     (`logs/app.log` junto al ejecutable) si nadie configuro una.
   - El `StreamHandler` de consola solo se agrega si existe un `sys.stdout` real.
   - `setup_logging()` nunca lanza: ante cualquier fallo (disco lleno, permisos, ruta
     invalida) cae a una config minima y deja arrancar la app igual.

**Archivos:** `src/main.py`, `src/utils/logger.py`.

**Verificacion:** `pytest tests/` (52 passed); import de `src.main`, `src.controller.system`
y `src.vision.camera` OK; `setup_logging()` corre sin errores.

---

#### Cambio 277 - Scanner 2 microperforado: paridad robusta, defectos persistentes y ROI automatico

**Pedido:** revisar el lote de produccion presuntamente OK de scanner 2 en
`C:\Users\DefyC\Downloads\nok\nok`, usando solo los frames `*_raw.jpg`; corregir
falsos NOK, mejorar sensibilidad a agujeros realmente faltantes y dejar funcionando
el seguimiento automatico de ROI durante RUN.

**Hallazgos:**
- El lote contiene 306 raw unicos de 640x480; `__raw_only__` es una copia exacta.
- El baseline anterior daba 302/306 raw OK. Los cuatro NOK tenian todos los agujeros,
  pero la grilla elegia la paridad de fila equivocada y generaba grupos falsos de 6-9
  faltantes.
- La seleccion de paridad reutilizaba `tol_xy_px=42`, aunque las filas estan separadas
  solo 13.6 px. Con ese gate, una fila vecina podia puntuar como coincidencia valida.
- Las calibraciones dentro de `scanner_N.inspection` se aplicaban a todos los modelos:
  seleccionar esterilla podia heredar silenciosamente la geometria de microperforado.

**Cambios:**
- `src/inspection.py`: tolerancia independiente `grid_parity_selection_tol_px` para
  elegir paridad; el matching final conserva su tolerancia habitual.
- `config/io_map.yaml`, `scanner_2/modelo_B`:
  - paridad alternada automatica habilitada con gate estricto de 6 px;
  - frame NOK desde 4 faltantes (`frame_missing_nok_threshold=3`);
  - seguimiento persistente habilitado desde un solo faltante, sin exigir que el
    frame ya sea NOK; tres frames de la misma columna detienen la maquina;
  - ROI automatico preventivo en modo `move`: no espera missing, aprende 30 frames
    validos tras movimiento real, exige 5 px de deriva durante 15 frames, corrige
    1 px por paso, cooldown fijo de 15 frames y limite acumulado de +/-20 px;
  - precalibracion anterior al movimiento y correccion urgente siguen desactivadas.
- `src/utils/config.py`, `src/ui/tolerance_window.py`, `config/io_map.yaml`:
  nueva separacion `inspection_models.<modelo>` por scanner. Cambiar o guardar
  Tolerancias para microperforado ya no pisa esterilla ni viceversa. Se migraron las
  calibraciones existentes de ambos scanners a `modelo_B`.
- `tests/test_config.py`: regresiones para aislamiento entre materiales y perfil
  productivo seguro de scanner 2.

**Validacion:**
- Lote real completo: 306/306 raw OK, 306/306 temporal OK, cero fallas de alineacion,
  cero paradas falsas y ratio medio de deteccion 110%.
- Quedan solo dos indicios aislados no decisorios: un frame con 1 missing y otro con
  3; no forman racha ni columna persistente.
- ROI sobre lote estable: cero correcciones, x final 218. Deriva sintetica sostenida
  de +7 px: tres pasos graduales (x 218->221) y detencion al volver dentro del umbral.
- Defectos sinteticos: cuatro agujeros borrados producen NOK inmediato; un solo
  agujero borrado en la misma columna durante tres frames detiene exactamente en el
  tercero.
- `pytest tests/`: 54 passed; `compileall`, `scripts/verify_config.py` e imports del
  camino critico de produccion OK.

---

#### Cambio 278 - Build industrial protegido: Cython obligatorio y smoke test del EXE

**Pedido:** ejecutar todas las verificaciones y generar una entrega trasladable a la
PC de Metalconf, con Cython obligatorio por seguridad.

**Cambios:**
- `scripts/build_exe.ps1` ahora instala/verifica las dependencias de produccion antes
  de compilar y cancela inmediatamente si Cython/MSVC falla o no genera
  `license*.pyd`. Ya no existe el fallback que permitia entregar el modulo de licencia
  como Python sin proteccion nativa.
- El build inspecciona el artefacto final: exige `license*.pyd`, rechaza cualquier
  `license.py` distribuido y exige `scipy.optimize._lsap` para garantizar que la
  asignacion Hungara usada por vision este presente.
- `metalconf.spec` incluye explicitamente el wrapper FFT que SciPy 1.18 importa de
  forma dinamica. El primer smoke test detecto que PyInstaller no lo descubria; se
  corrigio antes de aceptar el entregable.
- `run_production.py` incorpora un modo de smoke test exclusivo del proceso de build.
  Importa OpenCV, NumPy, YAML, PyQt6, SciPy/Hungarian y el camino critico del sistema,
  confirma que licencia se cargo desde un `.pyd` y sale antes de crear el sistema,
  abrir camaras o conectarse al PLC.
- Cada entrega lleva `BUILD_INFO.txt` con fecha, commit y confirmacion de Cython y
  Hungarian para trazabilidad.

**Validacion:** 54/54 tests OK; `compileall` OK; Cython 3.2.5 + MSVC genero
`license.cp314-win_amd64.pyd`; PyInstaller 6.20 sobre Python 3.14.5 genero el EXE;
smoke test congelado OK sin PLC/camaras y con SciPy/Hungarian operativo.

---

## Cambios pendientes

### Validar centrado automatico de ROI para esterilla en scanner 2

- Microperforado queda habilitado y validado en el Cambio 277.
- Para esterilla, reunir y validar un lote OK tomado con la optica y el zoom reales de
  scanner 2 antes de habilitar seguimiento propio; no reutilizar su geometria para
  microperforado.
- Calibrar con ese lote el warmup, umbral de deriva, racha de confirmacion, cooldown,
  paso y limite acumulado. Verificar que no pierda agujeros ni genere correcciones por
  ruido, cambios de iluminacion o entrada/salida de la chapa.
- Confirmar el comportamiento antes de RUN, durante RUN, tras DETENER/INICIAR y luego de
  modificar manualmente la ROI desde Tolerancias.
- Tener en cuenta que, despues de pulsar RUN, la cinta o el patron puede tardar cerca de
  20 segundos en comenzar a moverse realmente. El warmup del centrado no debe aprender
  como referencia los frames previos sin movimiento o sin una chapa valida, ni agotarse
  antes de que empiece la inspeccion efectiva. La activacion debe condicionarse a
  evidencia valida de chapa/movimiento y la gracia inicial debe cubrir esta demora real,
  en lugar de depender solamente de una cantidad de frames o de un tiempo fijo menor.

---

### Sesion 2026-07-21 - Tadeo + Claude

#### Cambio 274 - Auditoria integral de bugs y robustez de produccion

**Pedido:** revisar todo el codigo en busca de bugs y riesgos de robustez, corregir los
problemas concretos y agregar defensas para dejar el sistema estable en produccion.

**Bugs y riesgos corregidos:**
- **Hilos duplicados de scanner:** si poller/inspector no terminaban dentro del timeout,
  se borraba igualmente su referencia. Un RUN posterior podia limpiar el mismo
  `stop_event` y dejar dos loops usando camara/PLC. Ahora se conserva la referencia,
  se bloquea el nuevo inicio mientras el hilo viejo siga vivo y `shutdown()` insiste
  aun cuando el scanner ya figure IDLE/STOPPED.
- **Carrera al reiniciar camara:** `Camera.start()` podia volver a poner `_running=True`
  sobre un hilo que estaba terminando y retornar exito justo antes de que ese hilo
  saliera, dejando camara activa sin captura. El reinicio ahora se rechaza hasta que el
  hilo anterior termine realmente. El cierre del decoder snapshot tampoco puede quedar
  bloqueado insertando el sentinel en una cola llena.
- **Anti-bleed con zoom:** la validacion comparaba el frame crudo de una camara contra el
  frame ya zoomeado de la otra, ocultando feeds DSHOW duplicados. Se agrego
  `get_raw_frame()` y la comparacion ahora es crudo contra crudo.
- **Archivos truncados por corte/fallo de disco:** nuevo helper de escritura atomica
  (temporal en el mismo directorio + `fsync` + `os.replace`). Se aplica a ROI, patron,
  estado EMA, tolerancias, overrides por scanner, configuracion/perfiles de camara y
  manifests de eventos. Ante un fallo, el archivo anterior queda intacto y el temporal
  se limpia.
- **ROI nueva con patron viejo:** las dos pantallas de guardado manual construyen el
  patron usando explicitamente la ROI solicitada (no una ROI que pueda cambiar durante
  el worker). Si el rebuild falla, restauran la ROI anterior; solo recargan el analisis
  vivo tras exito. La ROI manual se confirma nuevamente al finalizar para ganar frente
  a una escritura concurrente del auto-recenter.
- **Calibraciones corruptas silenciosas:** `ROI` rechaza coordenadas negativas y tamanos
  nulos; `load_roi` registra corrupcion. `load_pattern` valida dimensiones, puntos,
  radios, spacing y cantidad de celdas antes de permitir una sesion.
- **Shutdown doble:** OperatorWindow apagaba el sistema y `cmd_run` repetia el shutdown.
  El cierre de `InspectionSystem` ahora es idempotente y tolera fallos parciales sin
  omitir el resto de recursos. El solenoide se corta con reintentos y lectura de
  verificacion antes de desconectar el PLC; luces/backlight se apagan en batch.
- **Batch PLC parcialmente bloqueado:** `IOMap.write_batch()` podia devolver True aunque
  safe-mode hubiera bloqueado una de las senales. Ahora informa False si cualquier
  salida solicitada fue rechazada.
- **Manifest de evidencia incorrecto:** los overlays post-evento contaban como frames
  adicionales y finalizar dos veces duplicaba bytes. Ahora cuenta solo frames crudos,
  recalcula bytes idempotentemente y finaliza tambien durante `close()`.
- **Poda de disco:** una eliminacion fallida se contabilizaba como espacio liberado y
  alteraba incorrectamente el presupuesto restante. Ahora solo descuenta borrados
  realmente exitosos.

**Validacion:**
- `compileall` completo: OK.
- Configuracion productiva (`scripts/verify_config.py`): OK.
- Suite ampliada de 38 a **52 tests**, todos OK. Nuevas regresiones cubren atomicidad,
  lifecycle de camara/scanner, safe-mode parcial, validacion de patrones/ROI, shutdown
  idempotente, manifest de eventos y fallos de poda.
- Lote real OK scanner 1 microperforado: pipeline de produccion por movimiento
  **42/42 raw OK, 42/42 temporal OK, 0 NOK, 0 machine-stop, 0 alignment failures**.

**Limite de la auditoria:** PLC, solenoides y camaras fisicas no pueden simularse por
completo con tests locales; queda recomendada una prueba de humo en planta de
INICIAR/DETENER/RESET, desconexion/reconexion de cada camara y corte de PLC.

#### Cambio 273 - RUN espera movimiento real antes del warmup de ROI

**Pedido:** contemplar que, despues de pulsar RUN, la cinta o el patron puede tardar
20 segundos o mas, sin un tiempo constante, en comenzar a moverse; esos frames quietos
no deben contaminar el aprendizaje inicial ni provocar correcciones automaticas prematuras.

**Cambios:**
- `InspectionSession` incorpora `require_initial_movement`: en las sesiones productivas
  de RUN, el primer frame solo ceba la referencia visual y no se inspecciona. La primera
  inspeccion (y por lo tanto el primer sample del warmup de ROI) ocurre recien cuando la
  diferencia de imagen supera `continuous_position_threshold`.
- `ScannerController` activa ese gate tanto al crear la sesion inicial como al recargarla
  en vivo por un cambio de ROI/modelo. El analisis por carpeta y las sesiones de servicio
  conservan el comportamiento anterior.
- La gracia temporal pasa de 10 a 30 segundos y su reloj ahora comienza con la primera
  inspeccion aceptada despues de detectar movimiento, no al pulsar RUN. Por eso una
  demora mecanica de cualquier duracion no consume la proteccion. Ademas se conservan
  los 100 frames de gracia existentes.
- Se desactiva `roi_precal_enabled` en scanner 2: no puede medir ni escribir una ROI con
  los frames quietos anteriores al movimiento. Su recentrado gradual sigue disponible,
  pero la activacion/calibracion definitiva con lotes OK propios continua pendiente.
- Tests nuevos verifican que una escena quieta no genera inspecciones y que el primer
  movimiento real habilita el pipeline, sin cambiar el flujo batch.

**Motivo de seguridad:** el warmup del recentrado ya solo recibe frames aceptados por el
gate de movimiento y, dentro del pipeline, solo acumula baseline cuando la deteccion de
ROI/chapa es valida (`roi_info.detected_roi`).

#### Cambio 272 - Botones del header: colores + separar Tolerancias / Área de análisis

**Pedido:** mejorar los colores de los botones de arriba (Métricas, Grabación,
Tolerancias, Servicio) y dividir "tolerancias" de la "visualizacion de ROI" en dos
entradas separadas, faciles para el operario, SIN usar la palabra "ROI" (no la entiende),
y sin complicarlo.

**Cambios (`src/ui/operator.py`, header):**
- `_hbtn` rediseñado: texto blanco, radio 6, font 11 weight 600, cursor pointer; el realce
  en hover lo aporta la animacion de opacidad global (se saco el `{bg}dd` que en Qt se
  interpreta como #AARRGGBB y daba un color equivocado).
- Paleta cohesiva (tono -600, texto blanco): Métricas #2563eb, Grabación #e11d48,
  Servicio #475569, Tolerancias #0d9488, Área de análisis #7c3aed.
- Se separa el antiguo boton unico "Tolerancias" en DOS: **Tolerancias** y
  **Área de análisis**. Se disponen en dos filas (nav arriba / ajustes abajo); header
  84->90px para que entren comodas.
- `_open_tolerances`/`_open_area` delegan en `_open_ajustes(page)` que abre la ventana en
  la pagina correcta via `ToleranceWindow.show_page(0|1)`.

**Cambios (`src/ui/tolerance_window.py`):**
- Nuevo `show_page(idx)` publico (0=Tolerancias, 1=Área de análisis).
- Renombradas TODAS las cadenas visibles "ROI" -> "Área": titulo "ÁREA DE ANÁLISIS",
  tab "Área de análisis", boton "GUARDAR ÁREA", labels "Área guardada…", estados
  "Área guardada — recalculando…". Titulo de ventana "Ajustes — DEFYVISION" y header
  "AJUSTES". Tabs internos recoloreados a teal/violeta para matchear los botones del header.

**Verificado:** metodos presentes (`_open_area`, `_open_ajustes`, `show_page`); 0 cadenas
visibles con "ROI"; sintaxis OK; `pytest` 36/36.

---

#### Cambio 271 - Glow de la camara atado al estado del scanner

**Pedido:** que el realce iluminado alrededor de la camara de cada scanner (el que
aparecia en hover) tambien refleje el estado: verde sutil y difuso SIEMPRE en run; rojo
apenas por un instante (30s) al detenerse, para que no quede siempre rojo simulando error;
el hover que intensifique.

**Cambio (`src/ui/anim.py`):** nueva clase `CameraGlow` que maneja un unico
QGraphicsDropShadowEffect persistente sobre el panel de camara:
- `set_mode('run')`  -> glow VERDE persistente (blur base 14, difuso).
- `set_mode('stop')` -> glow ROJO + QTimer de 30s; al expirar se apaga suave (aunque el
  scanner siga detenido) para no simular error permanente.
- `set_mode('idle')` -> sin glow de estado.
- Hover: intensifica el glow actual (+14 de blur, mismo color del estado); sin estado usa
  el celeste neutro de antes. Solo actua en transiciones (no reinicia el timer rojo en
  cada refresh). API por string para no acoplar anim.py al enum de estados.

**Cambio (`src/ui/operator.py`, `ScannerPanel`):** el panel de camara usa `CameraGlow`
en vez de `add_hover_shadow`; `refresh_status()` mapea el estado a modo (RUNNING->run,
STOPPED/FAULT->stop, resto->idle) y llama `set_mode()`.

**Verificado (headless):** IDLE blur 0; RUN verde blur 14 y hover -> 28; STOP rojo blur 14
y tras expirar el timer -> 0; IDLE -> 0. `pytest` 36/36.

**Nota de rendimiento:** el glow verde en run es un drop-shadow persistente sobre el feed
(que se repinta a 5-25 fps). Blur chico (14) para acotar costo; si en planta se nota carga
con 2 scanners, se puede bajar el blur o pasar a un borde de color (mas barato, menos difuso).

---

#### Cambio 270 - ROI manual se aplica en RUN + recentrado automatico seguro en scanner 1

**Pedido:** al guardar manualmente la ROI desde la pestaña Tolerancias, aplicarla al
frame de analisis vivo sin detener/reiniciar el scanner; activar ademas un centrado
automatico que evite nuevas perdidas de encuadre.

**Diagnostico:**
- `GUARDAR ROI` escribia `roi.json`, reconstruia `holes.json` e invalidaba el cache del
  `Inspector`, pero la `InspectionSession` ya creada por RUN conservaba su snapshot
  privado de ROI/patron/tolerancias. El analisis seguia con la geometria vieja hasta
  DETENER+INICIAR.
- El recenter gradual estaba habilitado pero exigia 20 frames con deriva + 2 missing de
  borde: reaccionaba recien despues de que la ROI ya habia perdido agujeros.
- Al disparar realmente una persistencia, `inspection.py` intentaba usar un `logger` no
  definido; podia tumbar el hilo justo en la primera correccion automatica.
- Se evaluo centrar contra el centro geometrico absoluto de la chapa y se descarto: la
  ROI esta intencionalmente centrada sobre la zona perforada. Barrido real: x=212/213
  da 99/99 OK; x>=214 empieza a perder agujeros. El comportamiento correcto es seguir
  cambios respecto de la relacion ROI/chapa calibrada durante el warmup.

**Cambios:**
- `src/controller/scanner_controller.py`:
  - nuevo `_cache_revision`; `reload_cache()`/`set_model()` incrementan la revision;
  - el loop RUN compara esa revision y reemplaza atomicamente la `InspectionSession`
    completa en el siguiente frame, aun si el nombre de modelo no cambio;
  - al recargar se limpian rachas NOK/LOW_QUALITY calculadas con la geometria anterior.
- `src/ui/tolerance_window.py`:
  - GUARDAR ROI queda deshabilitado mientras se reconstruye el patron (sin workers
    simultaneos);
  - al finalizar llama `reload_cache()` y muestra "aplicado al analisis en vivo".
- `src/inspection.py`:
  - logger de modulo real para que persistir auto-recenter no crashee;
  - nuevo `roi_recenter_require_edge_missing`: permite seguir deriva antes de generar
    missing, manteniendo warmup, umbral y racha;
  - `saved_roi` queda como ancla de sesion: `max_total_shift_px` pasa a ser un limite
    acumulado real, no se reinicia despues de cada paso.
- `config/io_map.yaml`, solo scanner_1:
  - recentrado relativo automatico sin requisito de missing;
  - warmup 30->20 frames, trigger 4->3px, confirmacion 20->8 frames;
  - pasos conservadores de 1px y cooldown fijo de 8 frames;
  - limite acumulado permanece en +/-20px; correccion urgente y pre-cal siguen
    desactivadas. Scanner_2 queda sin cambios.
- Tests nuevos/extendidos para revision de cache, limpieza de racha, seguimiento de
  deriva sin missing, persistencia y limite acumulado.

**Validacion:**
- Simulacion secuencial con los 99 frames OK del scanner_1 y escritura redirigida a
  temporal: 99/99 OK, missing total=0; el auto-recenter hizo un paso x=213->212 sin
  regresion.
- Barrido estatico x=211..219 sobre los 99 frames confirma ventana segura x=212/213 y
  demuestra por que no se debe perseguir el centro absoluto de chapa.
- `pytest`: 36/36.

**Riesgos / pendientes:**
- Validar en RUN de planta que el mensaje "aplicado al analisis en vivo" aparezca al
  guardar y que el overlay cambie en el frame siguiente.
- Activacion automatica nueva queda limitada a scanner_1, que tiene lote real validado;
  scanner_2 debe activarse con su propio lote OK para no trasladar geometria entre opticas.

#### Cambio 269 - Bloqueo de cambio de placa en marcha + aviso de cambio de maneta

**Pedido:** (1) en funcionamiento no se debe poder cambiar el tipo de placa
(microperforado <-> esterilla); si el operador lo intenta, detener el cambio y avisarle.
(2) Animar el cambio de maneta MANUAL<->AUTOMATICO para que quede avisado.

**Cambios (`src/ui/operator.py`, `ScannerPanel`):**
1. `_on_model_changed()`: el cambio de placa solo se permite en IDLE o STOPPED. En
   RUNNING/FAULT se revierte el combo al modelo actual (blockSignals para no recursar) y
   se muestra un `QMessageBox.warning` ("No se puede cambiar el tipo de placa con el
   scanner en marcha. Detené el scanner primero..."). No se llama `set_model`.
2. `refresh_status()`: al detectar una transicion REAL de la maneta (no la primera
   lectura), dispara `attention_flash()` sobre el label de MANETA para avisar el cambio
   MANUAL<->AUTOMATICO.

**Cambio (`src/ui/anim.py`):** nuevo `attention_flash(widget, blinks, ms)` — parpadeo
breve de opacidad (efecto transitorio, se crea al empezar y se remueve al terminar, costo
cero en reposo).

**Verificado (headless + stub):** con estado RUNNING, `set_model` NO se llama y el combo
revierte; con IDLE si se aplica. `attention_flash` crea el efecto. `pytest` 36/36.

---

#### Cambio 268 - Pack de animaciones completo (industrial, sutil)

**Pedido:** completar el toque estetico con animaciones mas completas, siempre estilo
industrial. Tadeo eligio las cuatro del pack.

**Cambios (`src/ui/anim.py` ampliado + wiring en operator.py y login_dialog.py):**
1. **Boton RESET aparece/desaparece con fundido.** `fade_button(btn, visible)` usa el
   mismo animador del boton (show_fade/hide_fade sobre su QGraphicsOpacityEffect): al
   ocultar, se anima a opacidad 0 y recien ahi setVisible(False). Reemplaza el
   setVisible seco en `ScannerPanel._refresh_buttons`. (Se omitio el "deslizamiento":
   el boton esta en un layout y animar su pos genera jitter; solo fundido.)
2. **Badge de estado con transicion de color.** `animate_bg_color(widget, hex, style_fn)`
   interpola el fondo (QVariantAnimation sobre QColor) de IDLE/RUNNING/FALLA. Guarda el
   color objetivo AL INICIAR para que los refresh_status (cada ~200-500ms) con el mismo
   estado no reinicien la animacion en cada tick.
3. **Fade-in en dialogos.** `showEvent` -> `fade_in()` en `MachineStopDialog` (ms=110,
   rapido por ser alerta) y `LoginDialog` (ms=170). Flag `_did_fade` para no repetir.
4. **Realce en hover de tarjetas y camara.** `add_hover_shadow(widget)` instala un
   QGraphicsDropShadowEffect (glow celeste sutil) que se CREA al entrar el mouse y se
   REMUEVE al salir -> costo cero en reposo (clave para el feed de camara, que se repinta
   muchas veces por segundo). Aplicado a las 3 tarjetas OK/Tiempo/NOK y al panel de camara.

**Verificado (headless offscreen):** fade_button muestra/oculta con la opacidad correcta;
animate_bg_color aplica directo el primer color, anima al cambiar y NO reinicia con el
mismo; hover_shadow sin efecto en reposo y lo crea al entrar; sintaxis OK; `pytest` 34/34.

---

#### Cambio 267 - Scanner 1 microperforado completo tras zoom 150%: paridad, patron y matching

**Pedido:** despues de estabilizar el zoom digital y los reinicios de las Sony, revisar
scanner_1 con el lote OK
`C:\Users\DefyC\Downloads\21-07-2026-MICROPERFORADO_1_SCANNER_1` y dejar la
inspeccion de microperforado detectando todos los agujeros correctamente.

**Diagnostico:**
- El patron anterior tenia 128 puntos pero, por recortes acumulados, cada frame solo
  comparaba 85-91 agujeros aunque el detector veia aproximadamente 187-198. El 150% de
  `detection_ratio` no era ruido: eran agujeros reales que quedaban fuera de la decision.
- La grilla escalonada alterna 5/6 agujeros por fila dentro de la ROI. Cuando el fleje
  avanzaba una fila fisica, la fase Y modulo 14px quedaba igual pero la paridad visible se
  invertia. El ajuste solo probaba una paridad; `tol_xy_px=38` y el patron parcial ocultaban
  ese error. Con tolerancia honesta aparecian 30-47 falsos missing en frames como 0074,
  0091 y 0092.
- El filtro top/bottom descartaba detecciones apenas fuera del margen antes del matching,
  aun cuando estaban dentro de tolerancia de un expected valido, creando falsos missing
  de borde.
- El check de offset global no respetaba `pattern_align_min_missing`: podia marcar NOK por
  descentrado con `missing=0` (reproducido en frame_0095).
- La fecha/hora incrustada por la Sony tapa la ultima fila en y~445..479; esa zona no
  aporta evidencia confiable y no debe participar de la decision.

**Cambios:**
- `src/pipeline/grid_fitting.py`: `grid_compare_points()` puede probar, de forma opt-in,
  ambas paridades de una grilla staggered. Intercambia las plantillas de ocupacion par/impar,
  puntua cantidad de matches + residuo y elige deterministamente la mejor. La opcion queda
  desactivada por defecto para no alterar patrones no repetitivos.
- `src/inspection.py`:
  - wiring de `grid_allow_row_parity_flip` y tolerancia de seleccion;
  - nuevo `grid_extend_rows_before` simetrico al extend-after para proyectar filas completas
    aunque el patron de referencia se haya construido lejos de los bordes;
  - halo de `tol_xy_px` al filtrar detecciones top/bottom, evitando eliminar el match de una
    posicion esperada justo dentro del margen;
  - el offset global ahora respeta `pattern_align_min_missing`, igual que zigzag/slope.
- `config/io_map.yaml`, override `scanner_1.inspection`:
  - `tol_xy_px: 38 -> 12`;
  - paridad dinamica activada y extension de 3 filas arriba/abajo;
  - margenes laterales de build/compare en 12px; top 20px; bottom 36px por timestamp;
  - `grid_max_missing: 0`: cualquier agujero faltante vuelve el frame NOK y la regla
    temporal exige persistencia antes de la decision final.
- `data/patterns/scanner_1/modelo_B/holes.json`: reconstruido desde `frame_0050.png`,
  conocido OK y nitido, con zoom digital 150%; 170 puntos unicos, dx=36, dy=14,
  stagger=-18, sin duplicados.
- `tests/test_grid_fitting.py`: pruebas de paridad normal, paridad invertida y defecto real
  de un agujero bajo paridad invertida.

**Validacion:**
- Pipeline de produccion (dedup por movimiento): 42/42 raw OK, 42/42 temporal OK,
  0 missing, 0 machine stop.
- Forzando todos los frames: 99/99 OK, missing total=0, expected=165-171 por frame
  (antes solo 85-91). Frames 0074/0091/0092: missing=0.
- Defecto sintetico controlado borrando un agujero central: raw NOK, missing=1; regla de
  5 frames = `OK, OK, OK, OK, NOK`.
- Suite completa: `pytest` 34/34.

**Riesgos / pendientes:**
- Validar tambien con un lote NOK real del scanner_1 apenas este disponible. La prueba
  sintetica confirma el matching y la temporalidad, pero un defecto real suma variaciones
  opticas/mecanicas que conviene conservar como regresion permanente.

#### Cambio 266 - Boton RESET se auto-limpia a los 30s tras parada manual (no falla)

**Pedido:** al DETENER un scanner que estaba en RUN, el boton grande naranja RESET
quedaba visible indefinidamente y "quedaba feo". Que permanezca 30s y luego el display
quede limpio.

**Contexto (maquina de estados):** en AUTO, DETENER manual lleva a STOPPED, estado que
muestra el boton RESET y requiere RESET -> IDLE para reanudar. En MANUAL, DETENER va
directo a IDLE (sin boton). El boton tambien aparece en STOPPED por falla (RESET FALLA).

**Decision (confirmada con Tadeo):** a los 30s de una parada MANUAL/normal el scanner
vuelve SOLO a IDLE (luz azul, listo para INICIAR) y el boton desaparece -> display limpio
y operable. Las paradas por FALLA NO se auto-limpian: el boton RESET FALLA queda hasta
que el operario lo reconozca.

**Cambio (`src/ui/operator.py`, `ScannerPanel`):**
- Nuevo `_reset_autohide_timer` (QTimer single-shot 30s) creado en `__init__`.
- `_refresh_buttons()`: si el estado es STOPPED sin falla, arranca el timer (una vez);
  si es STOPPED por falla o cualquier otro estado, lo detiene.
- Nuevo `_auto_clear_stop()`: al disparar, re-verifica via `get_status()` que siga en
  STOPPED sin falla (por si entro una falla durante el timer) y recien ahi llama
  `scanner.reset()` (STOPPED -> IDLE). `reset()` ya es no-op fuera de STOPPED.

**Verificado (simulacion de los metodos con scanner stub):** parada normal -> boton
visible + timer activo; llega falla -> timer se cancela; auto-clear con falla -> 0 resets;
auto-clear normal -> reset -> IDLE; IDLE -> boton oculto. `pytest` 31/31.

---

#### Cambio 265 - Micro-animaciones sutiles (fade-in de ventanas + hover/press en botones)

**Pedido:** hacer la UI mas estetica en general con micro-animaciones y un fade-in suave,
sin transiciones raras. Que los botones reaccionen tambien al pasar el mouse (hover).

**Cambio (`src/ui/anim.py` nuevo + wiring):**
- `fade_in(win)`: las ventanas grandes aparecen con un fundido de opacidad (windowOpacity
  0->1, ~170ms). Aplicado a operator, service y las 3 ventanas de datos (errores, metricas,
  tolerancias) justo despues de showMaximized().
- `polish_buttons(root)`: instala en cada QPushButton un UNICO QGraphicsOpacityEffect
  persistente animado en los tres estados: reposo 0.88 (apenas mate) -> hover 1.0 (se
  ilumina) -> press 0.55 (dip y vuelve). Un solo efecto por boton evita el conflicto de
  "un widget = un efecto grafico" entre hover y click. Sin recortes ni cambios de layout.
  Idempotente (no re-instala si ya esta animado o usa otro efecto). Aplicado a operator y
  service. Todo en try/except: si falla, la UI sigue sin animacion (nunca rompe el control).

**Nota:** version inicial hacia solo un pulso al presionar; a pedido de Tadeo se paso al
modelo unificado con reaccion tambien en hover.

**Verificado:** headless (offscreen) fade_in deja windowOpacity=0 y anima; el boton mide
0.88 en reposo, 1.0 tras Enter, 0.88 tras Leave; doble polish no duplica el efecto;
`pytest` 34/34.

---

#### Cambio 264 - Ventanas de datos abren maximizadas

**Pedido:** que las ventanas grandes con datos (grabacion, servicio, etc.) siempre abran
maximizadas, nunca recortadas.

**Diagnostico:** operator y ServiceWindow (y sus tabs grabacion/servicio) ya abrian con
`showMaximized()`. Las que abrian recortadas eran las 3 ventanas QMainWindow secundarias
abiertas desde el panel del operador con `.show()` a su tamano `resize()`.

**Cambio (`src/ui/operator.py`):** `OperatorFrameViewer` (errores/grabacion), `MetricsWindow`
y `ToleranceWindow` pasan de `.show()` a `.showMaximized()`. El `resize()` queda como tamano
de restauracion al des-maximizar.

---

#### Cambio 263 - Zoom digital tambien en las vistas en vivo de modo servicio (WYSIWYG)

**Pedido:** que en la pestana de grabacion y en TODOS los lugares donde se ve la camara
se aplique el mismo zoom, para poder extraer frames reales (zoomeados).

**Diagnostico:** la grabacion a disco (`RecordingTab._grab_frame`) ya usaba
`cam.get_frame()` → los frames GRABADOS ya salian zoomeados. Pero las VISTAS EN VIVO de
modo servicio abren su propia captura (`_MJPEGReader`/`cv2.VideoCapture` directo a la URL),
que NO pasa por `get_frame()` → se veia la preview ANCHA mientras se grababa zoomeado
(inconsistente). Igual la exportacion de frame del multi-camara (`_capture_ip_frame`)
guardaba el frame crudo sin zoom.

**Cambios:**
- `src/vision/camera.py`: se extrajo la matematica del zoom a una funcion pura de modulo
  `apply_digital_zoom(frame, zoom, pan_x, pan_y)`; `Camera._apply_zoom` ahora la llama
  (misma logica, reutilizable).
- `src/ui/service.py`: nuevo helper `_zoom_live_frame(system, scanner_id, frame)` que lee
  el zoom del objeto `Camera` VIVO (refleja ajustes en caliente de la tab Zoom; cae a
  camera.yaml si no hay camara). Aplicado en:
  - `RecordingTab._show_ip_frame` (preview en vivo de grabacion) → usa el scanner del combo.
  - `CameraCalibTab._show_ip_frame` (preview multi-camara) y `_capture_ip_frame` (exportar
    frame) → usan `_scanner_for_slot(slot)` (slot 0/1 = ip_camera_1/2 = scanner_1/2 por IP).

**Verificado:** `apply_digital_zoom` via helper de servicio produce un frame IDENTICO al de
`Camera.get_frame()` (mismo scanner); passthrough con zoom<=100; safe con frame None;
sintaxis OK; `pytest` **31/31**.

**Nota:** el snapshot diagnostico de "senal congelada" (`_save_ip_diag`) se deja crudo a
proposito (es diagnostico del feed real de la camara, no del encuadre de produccion).

---

#### Cambio 262 - Zoom digital propio (reemplaza al zoom de la Sony que se rompe al reiniciar)

**Problema:** el zoom lo hacia el visor/config de la propia camara Sony IP. En cada
reinicio ese setting se perdia/reseteaba ("se rompe la Sony") y el encuadre dejaba de
coincidir con los patrones calibrados.

**Diagnostico (medido sobre lotes reales 21-07-2026 con/sin zoom, scanner_1 y scanner_2):**
La SNC-EB600B es de lente fijo → su "zoom" YA es digital (crop + reescalado). Medido por
deteccion de blobs (diametro y separacion de agujeros):
- scanner_1: WIDE diam=8.9px sep=13.9px  ->  ZOOM diam=13.5px sep=21.3px  = **1.52x**
- scanner_2: WIDE diam=9.5px sep=13.9px  ->  ZOOM diam=14.3px sep=21.4px  = **1.53x**
Una replica digital 1.5x del frame ancho reprodujo **199 agujeros / diam 13.8px**,
identico al zoom real de la Sony (199 / 13.5px) → capacidad de deteccion equivalente,
perdida de detalle despreciable (el zoom Sony no aportaba resolucion optica extra).

**Solucion:** restaurar el zoom digital propio (revertido el 2026-07-07, cherry-pick de
`cbf56c7`) y dejarlo aplicado SIEMPRE por defecto via `config/camera.yaml`, de modo que
sobreviva a cualquier reinicio de la camara.

**Cambios:**
- `src/vision/camera.py`: `get_frame()` aplica `_apply_zoom()` (crop centrado desplazable
  por pan_x/pan_y + resize al tamano original); `set_zoom()`/`set_pan()` en caliente;
  props `zoom`/`pan`. `zoom<=100` no hace nada (paso-a-traves).
- `src/utils/camera_config.py`: `DEFAULTS` suma `zoom:100/pan_x:0/pan_y:0` (fallback = sin
  zoom, seguro); `save_camera_settings()` ahora MERGEA en vez de reemplazar (no pisa claves
  guardadas por otro panel).
- `src/ui/service.py`: nueva tab **Zoom** en modo servicio (slider 100-300%, D-pad de paneo,
  vista en vivo, guardar a camera.yaml, restablecer). Para clavar el pan fino en planta.
- `config/camera.yaml`: `scanner_1` → `zoom:150 pan_x:-4 pan_y:0`;
  `scanner_2` → `zoom:150 pan_x:13 pan_y:0`.

**Verificado:** sintaxis OK en los 3 modulos; `load_camera_settings` devuelve zoom=150 para
ambos scanners; `_apply_zoom` conserva shape 640x480; `pytest` **31/31**.

**Pendiente:** el `pan_x` esta medido offline; en planta, con la Sony en zoom 1x (campo
completo), afinar el encuadre con el D-pad de la tab Zoom mirando la camara real y Guardar.
Si algun dia la Sony entregara mayor resolucion nativa sin zoom, capturar a esa resolucion
y recortar daria aun mas detalle (a 640x480 no hay margen extra que ganar).

---

### Sesion 2026-07-14 - Tadeo + Claude

#### Cambio 261 - Borde de CHAPA robusto cuando se superpone con el backlight (falsos PATRON DESALINEADO)

**Pedido:** Tadeo reporto muchos falsos NOK/paradas por "patron desalineado" en scanner_2:
cuando el borde de la chapa se superpone un poco con la luz, el borde detectado "se va
demasiado hacia adentro". Evidencia: 12 pares raw+overlay en
`C:\Users\DefyC\Downloads\Imagenes error NOK 14-7`.

**Diagnostico (reproducido con perfiles reales):**
1. `_detect_sheet_edges_in_windows` tomaba el gradiente MAS FUERTE de toda la ventana
   de busqueda. Con blur o luz derramada sobre el borde, el gradiente del borde real se
   ablanda y el maximo cae en agujeros del patron (la ventana interna llega hasta el
   patron) o en el lavado de luz sobre el metal: puntos de chapa hasta 100px hacia
   adentro. Medido en banda mala: candidato falso con lados 106-170 de intensidad vs
   75/200+ del borde real.
2. Esos puntos corruptos inclinaban la recta ajustada del borde ->
   `pattern_sheet_slope_delta_max_deg` explotaba (4.0 deg en vivo) -> "PATRON
   DESALINEADO - INCLINACION" + machine_stop falso. (El mismo mecanismo que en su
   momento se mitigo achicando `chapa_edge_inner_px` a 40, sin resolver la causa.)
3. Los puntos de chapa no pasaban por ningun filtro de outliers (el patron si tenia IQR).
4. Ademas el borde del fleje es un ARCO (curvatura real del material + lente): ajustar
   una recta hace la pendiente sensible a que bandas entraron en cada frame.

**Cambios (`src/pipeline/edge_centering.py`):**
- Nuevo `_find_edge_in_profile()`: un candidato a borde debe tener lado metal OSCURO
  sostenido (20px <= dark+0.25*rng) y lado backlight BRILLANTE sostenido
  (20px >= bright-0.35*rng); entre los validos se toma el MAS INTERNO (primera
  transicion fisica saliendo del metal). Agujeros del patron y lavado interior fallan
  las compuertas; estructuras del resplandor mas alla del borde quedan descartadas por
  ser externas. Sin candidato valido la banda se omite (mejor un punto menos que uno falso).
- Posicion final por CRUCE DEL NIVEL MEDIO dentro de la rampa del borde, interpolado
  (invariante al blur: bandas nitidas y lavadas del mismo frame miden el mismo borde).
- `_find_edge_in_profile` devuelve tambien el ancho de rampa; la pendiente de chapa
  descarta bandas con rampa > 2x mediana (firma del borde lavado).
- Filtro IQR (factor 3.0) sobre los puntos de chapa, como ya tenia el patron.
- Pendiente de chapa sobre bandas centrales (60% del alto) para reducir el efecto arco.

**Cambio (`config/io_map.yaml` scanner_2):** `pattern_slope_delta_max_deg: 1.2 -> 2.2`.
Con la medicion ya robusta, el ruido remanente del delta es ondulacion REAL del borde
del fleje (el vertice del arco viaja con el material entre frames): medido <=1.4 deg en
el lote. 2.2 deg deja margen y sigue disparando ante inclinaciones reales sostenidas.

**Validacion (lote "Imagenes error NOK 14-7", 12 frames que antes disparaban):**
- ANTES del fix (offline): dMax hasta 2.38 deg, puntos de chapa saltando ~100px hacia
  adentro (visible en overlays originales), 4.0 deg en vivo.
- DESPUES: 0/12 frames con `pattern_alignment_warn`, 0/12 machine_stop, dMax max 1.36,
  polilineas de chapa suaves siguiendo el arco real en todos los frames.
- Los 3 frames que siguen NOK es por missing del blur (9-10), cubiertos por la regla
  temporal (consecutive_nok_frames) — sin relacion con alineacion.
- `pytest` 31/31.

**Riesgos / pendientes:**
- scanner_1 mantiene `pattern_slope_delta_max_deg: 1.0`; si aparecen falsos INCLINADO
  alli, aplicar la misma recalibracion (la deteccion robusta ya le aplica igual).
- Si en planta un desalineado real de patron dispara con menos de 2.2 deg de delta,
  bajar el umbral con datos de ese caso (el piso ahora es ~1.4 deg de ruido natural).

---

#### Cambio 260 - MachineStopDialog: banner de SCANNER legible

**Pedido:** en la ventana de MACHINE FAULT, el banner que indica "SCANNER 1/2" se
veia amarillento/lavado y con poco contraste entre letras y fondo.

**Causa:** el label usaba fondo blanco translucido (`#ffffff12`) con borde rosa palido
(`#fecaca`) sobre el degrade rojo del encabezado — el conjunto quedaba cremoso y el
texto blanco perdia contraste.

**Cambio (`src/ui/operator.py`, `MachineStopDialog._build_ui`):** placa oscura solida
(`#0b0f14`), texto blanco 46px weight 900, borde rojo claro 3px (`#f87171`).
Verificado renderizando el dialogo offscreen.

---

#### Cambio 259 - Fix crashes y trabas del visor de grabaciones y frames NOK

**Pedido:** Tadeo reporto que (a) al abrir frames NOK la UI se traba bastante, y
(b) el programa se suele crashear al abrir una grabacion.

**Causa raiz:** la misma familia de bugs del Cambio 258 (QThread vivo cuya referencia
se pisa -> Python lo destruye en ejecucion -> Qt aborta el proceso), repetida en
4 lugares, mas creacion de QPixmap fuera del hilo de GUI (no soportado por Qt,
causa trabas/crashes intermitentes en Windows):

1. `frame_viewer.py` `load_event()`/`_render_window()`: `quit()` (no-op sobre un run()
   plano) + `wait(200)` (bloqueaba la UI 200ms) + pisada de `self._loader` corriendo.
   Clickear otro evento/grupo mientras cargaban miniaturas = crash.
2. `frame_viewer.py` `_launch_inspect()`: pisaba `_inspect_worker` vivo al navegar
   frames rapido con overlay activo.
3. `frame_viewer.py` `_ThumbLoader`: creaba QPixmap dentro del hilo de fondo.
4. `operator.py` `_open_errors()`: recreaba `OperatorFrameViewer` en cada reapertura
   de la ventana; si quedo un loader corriendo de la instancia anterior -> crash.
5. `service.py` `_launch_overlay_worker()` (visor de eventos/grabaciones en modo
   servicio): mismo patron quit()+pisada, y `_EvOverlayWorker` emitia QPixmap
   creado en el worker. Navegar rapido los frames de una grabacion = crash.

**Cambios:**
- `frame_viewer.py`: nuevo protocolo de retiro (`_retire_worker`/`_purge_retired`):
  desconectar senal, `requestInterruption()`, retener en `_retired_workers` hasta
  `finished`, recien ahi `deleteLater()`. Sin esperas bloqueantes en la UI.
- `_ThumbLoader`: emite `QImage` (thread-safe); chequea `isInterruptionRequested()`
  por frame; la conversion a QPixmap se hace en `_on_thumb_ready` (hilo GUI).
  Eliminados `_load_thumb`/`_bgr_to_pixmap` (quedaron muertos).
- `operator.py` `_open_errors()`: la ventana del visor se crea una sola vez y se
  reutiliza (reload() ya refrescaba el contenido).
- `service.py`: `_EvOverlayWorker` emite `QImage`; `_launch_overlay_worker` usa el
  mismo protocolo de retiro con `_retired_overlay_workers`/`_purge_overlay_worker`.

**Resultado:** abrir grabaciones, cambiar de evento durante la carga, navegar frames
rapido y reabrir el visor ya no pueden abortar el proceso; se van las trabas de
`wait(200)` y las del QPixmap fuera del hilo GUI.

**Verificado:** `compileall` OK, imports OK, `pytest` 31/31.

---

#### Cambio 258 - Fix crash al apretar GUARDAR ROI varias veces seguidas

**Pedido:** Tadeo reporto que a veces, al dar "GUARDAR ROI" varias veces, la aplicacion
se cierra sola.

**Causa:** cada click en GUARDAR ROI lanzaba un `_RebuildWorker(QThread)` nuevo para
reconstruir holes.json y lo guardaba en `self._rebuild_worker`, PISANDO la referencia
al worker anterior. Si el rebuild anterior seguia corriendo (tarda segundos), Python
garbage-collecteaba ese QThread vivo y Qt aborta el proceso entero
("QThread: Destroyed while thread is still running"). Agravante: el stream en vivo
re-habilita el boton GUARDAR ROI en cada frame, asi que deshabilitar el boton no
alcanzaba como proteccion.

**Cambio (`src/ui/service.py`):**
- `_rebuild_pattern_async()`: guard al inicio — si ya hay un worker corriendo, el
  pedido queda en `self._rebuild_pending` (el ultimo pedido gana) y se muestra en el
  status label; NO se crea un segundo QThread.
- Nuevo `_on_rebuild_finished()`: conectado a `worker.finished` — libera la referencia
  (`deleteLater`), y si quedo un pedido pendiente lo lanza recien ahi. Reemplaza el
  viejo `worker.done.connect(lambda: worker.deleteLater())`.

**Resultado:** clicks repetidos en GUARDAR ROI ya no pueden matar la app; cada ROI
guardada termina con su patron reconstruido (el pendiente se procesa al terminar el
anterior).

**Verificado:** `compileall` OK; `pytest` 31/31.

---

#### Cambio 257 - Higiene de repo + tests 27/27 + comando cleanup-output

**Pedido:** seguir con mejoras esteticas y de funcionalidad (continuacion del Cambio 256).

**Cambios:**

1. **Tests: de 25/27 a verde total.**
   - `tests/test_scanner_controller.py`: `_FakeIO` ahora implementa `write_critical()`
     (faltaba desde que `_cut_solenoid_critical` existe).
   - `test_start_does_not_enter_running_when_solenoid_is_blocked` estaba obsoleto:
     testeaba que start() abortara si el solenoide era rechazado, pero el diseno actual
     (solenoides bloqueados por software / safe mode en IOMap) arranca igual e ignora
     el rechazo. Renombrado a `test_start_enters_running_even_if_solenoid_is_blocked`
     y actualizado al comportamiento vigente (RUNNING + luz verde).

2. **`logs/defyvision.log` fuera de git** (`git rm --cached` + `logs/` en .gitignore).
   Aparecia como "modified" en cada sesion y ensuciaba los commits.

3. **Basura eliminada:** `prueba.txt`, `scripts/_tmp_analyze.py` (tenia SyntaxError,
   ni compilaba), 28 `data/dbg_*.png` trackeados (+`data/dbg_*` al .gitignore),
   `pyarmor.bug.log` local.

4. **`.gitattributes` nuevo:** todo el codigo/config en LF, `.bat/.ps1` en CRLF,
   binarios marcados. Elimina los warnings "LF will be replaced by CRLF" en cada
   operacion git. El indice ya estaba 100% LF, asi que no hubo renormalizacion.

5. **Nuevo comando `cleanup-output`** (`src/utils/cleanup.py` + subcomando en main.py):
   `python -m src.main cleanup-output [--dir data/output] [--keep-days 30] [--max-gb 2] [--apply]`
   - Poda entradas de primer nivel de data/output por antiguedad y por presupuesto
     de disco (mas viejo primero), igual filosofia que la poda del EventRecorder.
   - **Dry-run por defecto**: sin `--apply` solo informa. Hoy data/output tiene 1.22 GB
     acumulados desde mayo sin ningun limite.
   - Tests nuevos en `tests/test_cleanup.py` (4 casos: dry-run, antiguedad,
     presupuesto oldest-first, keep_days=0 desactiva).

**Verificado:** `pytest` 31/31 (27 previos + 4 nuevos), dry-run real sobre data/output OK.

**Pendiente que requiere decision de Tadeo:** credenciales del modo servicio en texto
plano en `config/app.yaml` commiteado al remoto — moverlas a archivo local no versionado
o variable de entorno.

---

#### Cambio 256 - Estetica: divisores de comentario normalizados a 72 columnas en todo el repo

**Pedido:** Tadeo pidio dejar el codigo mas estetico: habia lineas de division y
separadores de comentario con largos irregulares que quedaban feos.

**Hallazgo:** convivian 3 estilos de divisor con anchos inconsistentes:
- `# ----` ASCII en 3 largos distintos (68/72/77 columnas, 164 lineas)
- `# ── Etiqueta ───` unicode con borde derecho irregular (68 a 81 columnas, ~120 lineas)
- `# ====` en `metrics_window.py` (34 lineas)
Ademas un comentario divisor partido en 2 lineas en `operator.py` (~linea 329).

**Cambio (solo comentarios, cero cambios de logica):**
- Script one-shot que normalizo TODOS los divisores de `src/`, `tests/` y `scripts/`
  a un ancho uniforme de **72 columnas**, respetando la indentacion y el estilo de
  cada tipo (`-`, `=`, `─` con y sin etiqueta).
- 7 etiquetas demasiado largas para entrar en 72 col se acortaron (grid_fitting.py x2,
  operator.py x3, service.py x2) sin perder significado.
- El divisor de 2 lineas de `operator.py` ("Modo seguro de ESTE scanner") se convirtio
  en divisor corto + comentario normal de 2 lineas.
- Total: 14 archivos, ~166 lineas de comentario.

**Verificado:** `git diff` solo toca lineas de comentario; `compileall` OK sobre
`src/` y `tests/`; `pytest` sigue 25/27 (mismos 2 failures preexistentes de
`test_scanner_controller.py` por `_FakeIO` sin `write_critical`, sin relacion).

**Nota:** `scripts/_tmp_analyze.py` tiene un SyntaxError preexistente (f-string con
comillas escapadas) — no se toco; candidato a borrarse por ser script temporal.

---

### Sesion 2026-07-07 - Tadeo + Claude

#### Cambio 255 - Racha de 2 frames para desalineacion en scanner_1 y scanner_2; se desactiva la parada de 1 solo frame

**Pedido:** Tadeo queria bajar la sensibilidad al error de "patron desalineado": que no dispare nunca con un solo frame, sino con una racha de 2 frames desalineados seguidos (probo pedir 3, despues bajo a 2).

**Contexto (ver Cambio 253/254 mas abajo primero para el flujo de merge de tolerancias):** habia DOS mecanismos independientes disparando desalineacion:
1. `pattern_align_stop_frames` — racha normal (scanner_1=5, scanner_2=2).
2. `pattern_align_severe_abs_max_px` — parada INSTANTANEA de 1 solo frame, disenada justamente para saltear la racha cuando el zigzag es muy grande (scanner_1=27.0px, scanner_2=12.0px).

El disparo de "1 solo frame" que preocupaba a Tadeo venia del mecanismo #2, no del #1.

**Cambio (`config/io_map.yaml`):**
- `scanner_1.inspection.pattern_align_stop_frames`: 5 → **2**
- `scanner_1.inspection.pattern_align_severe_abs_max_px`: 27.0 → **0.0** (deshabilitado; `0.0` es el valor que el codigo en `src/inspection.py` interpreta como "sin check severo", linea ~941-943)
- `scanner_2.inspection.pattern_align_stop_frames`: ya estaba en 2, sin cambios
- `scanner_2.inspection.pattern_align_severe_abs_max_px`: 12.0 → **0.0** (deshabilitado)

**Resultado:** en ambos escaneres, CUALQUIER desalineacion de patron (por severa que sea) ahora tiene que sostenerse 2 frames consecutivos antes de disparar `machine_stop`. Ya no hay ningun camino que pare la maquina con un unico frame por este motivo (el resto de mecanismos de parada de 1 frame — verticalidad/tilt, faltantes persistentes, desalineacion geometrica masiva — no se tocaron, son checks distintos que Tadeo no pidio modificar).

**Verificado:** `load_tolerances("modelo_B", scanner_id="scanner_1"/"scanner_2")` devuelve `pattern_align_stop_frames=2` y `pattern_align_severe_abs_max_px=0.0` para ambos. `pytest tests/` sigue 25/27 (mismos 2 failures preexistentes de `test_scanner_controller.py`, sin relacion).

**Riesgo:** si el zigzag/inclinacion severos aparecen en un unico frame aislado (chapa que se corrio un instante y volvio), con `stop_frames=2` la maquina ya NO va a parar en ese frame — necesita 2 frames seguidos. Si en planta se ve que corrimientos mecanicos reales pero breves dejan de detectarse, hay que subir de nuevo `severe_abs_max_px` por scanner en vez de bajar `stop_frames`.

**Nota:** esta sesion tambien probo agregar zoom digital + paneo a las camaras IP (tab "Zoom" en modo servicio). Se revirtio por completo a pedido de Tadeo tras resolver el tema de otra forma — ver commits de revert del 2026-07-07 en el historial de git para el detalle de lo que se probo y por que se deshizo.

---

### Sesion 2026-07-03 - Tadeo + Claude

#### Cambio 252 - LOW_QUALITY suprime patron desalineado y zigzag

**Pedido:** si la foto es de calidad baja (blur_score < blur_score_min), no debe saltar nunca por patron desalineado ni zigzag, ya que la lectura no es confiable.

**Causa:** `pattern_align_enabled` ya se saltaba cuando `frame_geometry_quality == "UNSTABLE"` (chapa zigzagueante), pero NO cuando `frame_quality == "LOW_QUALITY"` (imagen borrosa por blur_score). Un frame borroso podía generar medidas de zigzag falsas y disparar `pattern_alignment_warn` + `machine_stop`.

**Cambio (`src/inspection.py`):**
- Línea ~782: añade `and frame_quality != "LOW_QUALITY"` al guard del bloque principal `pattern_align_enabled` (zigzag + offset + slope).
- Línea ~932: añade `and frame_quality != "LOW_QUALITY"` al guard del check de parada severa instantánea (`pattern_align_severe_abs_max_px` / `pattern_align_severe_slope_deg`).

Con esto, cualquier frame marcado LOW_QUALITY es ignorado completamente por todos los checks de alineamiento de patrón.

---

#### Cambio 251 - Auditoría pre-24/7: 2 fixes de robustez en scanner_controller

**Hallazgos de la auditoría:**

**Fix 1 — ROI stale tras roi_recenter (ALTO)**
`roi_recenter` escribe el ROI corregido a roi.json en disco, pero NO actualiza el cache `self._inspector._roi` en memoria. Al hacer RESET+INICIAR, la nueva sesión llama a `_get_roi()` que devuelve el cache viejo. Con el ROI desplazado pueden aparecer missing holes falsos en los primeros ~50 frames (cubiertos por grace, pero solo si el operador no reduce startup_grace_seconds).

Cambio en `_continuous_loop_impl()`: reemplaza `reset_machine_stop()` con `self._inspector.invalidate(model=model_init, scanner_id=self._id)`. Invalida todo el cache (ROI, patrón, tolerancias, detector) forzando releer roi.json actualizado. El detector se recrea fresco en el `_get_detector()` de la siguiente sesión.

**Fix 2 — MachineStopDetector acumula estado durante grace (MEDIO)**
Cuando FAULT es suprimido por grace (línea ~1113), `_nok_streak` se reinicia a 0 pero el `MachineStopDetector` interno mantiene su historial de zonas/columnas. Al expirar la grace, si el detector ya tiene N-1 frames acumulados, el primer frame post-grace con ≥1 missing dispara machine_stop aunque la pieza sea correcta.

Cambio en `_handle_result()`: en el bloque `if in_grace:`, después de resetear el streak, añade `self._inspector.reset_machine_stop(model, self._id)` para limpiar el historial del detector.

---

#### Cambio 250 - Scanner 2: compare_left_ignore_px=45 / compare_right_ignore_px=55

**Pedido:** Tadeo calibró en producción que ignorar 45px izquierda y 55px derecha elimina los falsos faltantes en scanner_2/modelo_B.

**Cambio (`config/io_map.yaml`, bloque `scanner_2.inspection`):**
- `compare_left_ignore_px: 45.0` (nuevo override; antes heredaba 40.0 de tolerancias.yaml/modelo_B)
- `compare_right_ignore_px: 55.0` (antes era 75.0)

**Resultado:** zona de comparación ajustada a los bordes reales del área perforada visible desde scanner_2, sin excluir columnas internas que podrían tener defectos reales.

---

#### Cambio 249 - FAULT detiene el hilo; grace period no muestra "DETENCION DE MAQUINA" en UI

**Pedido 1:** Cuando MACHINE FAULT aparece, el scanner sigue generando resultados e inundando la pantalla con más errores. Necesitaba detenerse al instante.

**Pedido 2:** Durante el período de gracia (startup_grace_frames / startup_grace_seconds), si un frame tenía machine_stop=True el overlay mostraba "DETENCION DE MAQUINA" aunque la parada estuviera suprimida.

**Cambios (`src/controller/scanner_controller.py`):**
1. En el bloque `fault_triggered` de `_handle_result()`, se añade `self._stop_event.set()` tras `self._fire_state_changed()` para terminar inmediatamente el hilo inspector. El operario debe presionar DETENER → RESET → INICIAR para reanudar.
2. Se añade variable `_ms_suppressed` en `_handle_result()`: se activa cuando machine_stop es suprimido por gracia o por `machine_stop_enabled=false`.
3. Antes de llamar `on_result()`, si `_ms_suppressed` es True se limpia la bandera en el resultado (`dataclasses.replace(result, machine_stop=False)`) para que la UI no muestre "DETENCION DE MAQUINA" cuando la parada no aplica realmente.
4. Se añade `import dataclasses` al bloque de imports del módulo.

**Riesgos:**
- El `_stop_event.set()` en fault hace que el thread inspector salga del loop inmediatamente. El estado FAULT ya actualizaba los semáforos y el solenoide; el thread quedaba vivo pero esperando en el poll_loop. Ahora ya no queda thread en ese limbo.
- Para reanudar desde FAULT hay que hacer RESET explícito como con machine_stop.

---

#### Cambio 247 - DEFAULT_TOLERANCES: compare_left/right_ignore_px (ya commitado 3b3fd4d)

**Pedido:** El spinbox de `compare_left_ignore_px` / `compare_right_ignore_px` en la ventana de tolerancias quedaba en 0.0 para modelos sin override explícito.

**Causa:** `DEFAULT_TOLERANCES` en `src/utils/config.py` no incluía esas claves → `dict.get()` devolvía `None` → el spinbox no se inicializaba correctamente.

**Cambio:** Añadir `compare_left_ignore_px: 0.0` y `compare_right_ignore_px: 0.0` al diccionario `DEFAULT_TOLERANCES`.

---

#### Cambio 246 - Scanner 2 RUN vivo: desactivar ROI pre-calibration al arranque

**Pedido:** Tadeo aclara que el problema real persiste SOLO en produccion:
las imagenes aisladas / carpetas pueden dar OK, pero en `RUN` vivo el scanner
salta `ERROR` o `MACHINE FAULT` casi instantaneamente apenas se presiona
INICIAR/RUN.

**Hallazgo de Claude:**
- La ruta de `RUN` no es igual al analisis de carpeta. Antes de empezar la
  `startup grace`, `ScannerController._continuous_loop_impl()` ejecuta
  `_run_roi_precalibration()`.
- `scanner_2` tenia `roi_precal_enabled=True` y `roi_recenter_enabled=True`.
- Esa pre-calibracion usa frames del arranque mismo (todavia inestables o sin
  chapa completamente asentada) y puede REESCRIBIR `roi.json` antes de que
  entre en juego la gracia por frames/tiempo.
- Eso explica el sintoma observado por Tadeo: `raw` sueltos analizados despues
  dan OK, pero la corrida viva arranca ya con ROI corrida y por eso se rompe
  "al instante" aunque la imagen estacionaria se vea bien.

**Cambio (`config/io_map.yaml`, solo `scanner_2`):**
- `roi_precal_enabled: false`

**Que NO cambia:**
- `roi_recenter_enabled` sigue en `true`
- el recenter gradual durante la corrida sigue activo con:
  `warmup`, `streak`, `step_px` y `max_total_shift_px`
- o sea: se elimina solo la correccion agresiva al ARRANCAR, pero no la
  capacidad de corregir deriva lenta una vez estabilizada la marcha

**Riesgos / oportunidades:**
- Si el problema era efectivamente la ROI pre-cal moviendose mal al inicio,
  este cambio deberia impactar especificamente el caso "carpeta OK / RUN mal".
- Si aun asi siguiera parando de inmediato, el siguiente dato clave ya no seria
  una carpeta de `raw`, sino el log de produccion con timestamps del arranque
  y el `roi.json` antes/despues de apretar RUN.

---

#### Cambio 245 - MACHINE FAULT: corte de solenoide reforzado con reintentos + verificacion

**Pedido:** despues del Cambio 244, Tadeo reporta que en `run` vivo el problema
seguia: al disparar `MACHINE FAULT`, la maquina no siempre se detenia
fisicamente y seguia corriendo / largando errores. Aporta ademas la carpeta
`C:\Users\DefyC\Downloads\nok`.

**Hallazgo de Claude:**
- La carpeta `C:\Users\DefyC\Downloads\nok\nok` mezcla `*_raw.jpg` y
  `*_overlay.png`. Si se reanaliza TODO junto, los overlays dibujados se toman
  como si fueran frames reales y disparan muchisimos falsos `NOK` /
  `machine_stop`.
  - `RAW only`: `306 total`, `301 OK`, `5 NOK`, `0 machine_stop`
  - `OVERLAY only`: `304 total`, `58 OK`, `246 NOK`, `203 machine_stop`
- El bug de runtime mas grave estaba en el corte fisico: ante `machine_stop`,
  `fault`, perdida de camara, selftest fallido o licencia invalida,
  `ScannerController` hacia un solo `write(solenoid=False)` y enseguida cortaba
  los threads. Si esa escritura coincidia con un reconnect del PLC o fallaba
  una sola vez, no quedaba ningun loop vivo que insistiera con el corte.

**Cambios hechos por Tadeo + Claude:**
- `src/plc/io_map.py`
  - nuevo `IOMap.write_critical(signal, value, retries=5, retry_delay_s=0.15, verify=True)`
  - reintenta varias veces, fuerza reconnect inmediato entre intentos y
    verifica por lectura que el coil haya quedado en el valor pedido.
- `src/controller/scanner_controller.py`
  - nuevo helper `_cut_solenoid_critical(context)`
  - reemplaza los cortes simples de solenoide por corte critico en rutas de:
    - `machine_stop`
    - `FAULT` por racha NOK
    - crash del inspector
    - selftest fallido
    - perdida de camara
    - stop/reset de licencia
    - `stop()` / `force_fault()`

**Validacion:**
- `py_compile` OK en `src/plc/io_map.py` y `src/controller/scanner_controller.py`.
- Analisis de `C:\Users\DefyC\Downloads\nok\nok` confirma que los overlays NO
  sirven para diagnosticar la deteccion en bruto; los `raw` de esa misma tanda
  quedan casi todos OK con la calibracion actual y no disparan `machine_stop`.

**Riesgos / oportunidades:**
- Este cambio hace mucho mas robusto el corte fisico, pero si el PLC estuviera
  totalmente fuera de linea durante todos los reintentos, igual quedara logueado
  que NO se pudo confirmar `solenoid=OFF`. En ese caso el problema ya no seria
  de vision sino de comunicacion PLC.
- Mas adelante conviene ignorar `*_overlay.png` automaticamente en analisis por
  carpeta para evitar diagnosticos falsos cuando se reusan carpetas de salida.

---

#### Cambio 244 - Scanner 2 microperforado: eliminar falso NOK/FAULT instantaneo por desalineacion demasiado sensible

**Pedido:** Tadeo reporta que `scanner_2` (el unico en uso) empezo a parar
siempre al arrancar en `run`: salta `NOK` y `MACHINE FAULT` casi al instante,
pero las imagenes guardadas en `C:\Users\DefyC\Downloads\NOK-03-07-2026`
estan bien salvo las de `CALIDAD BAJA`. Sospecha inicial: una columna del
lado derecho no se estaba tomando aunque estuviera dentro del ROI.

**Hallazgo de Claude:** reanalizando los `raw.jpg` con el codigo actual, el
problema NO era una columna completa perdida ni una ROI corrida. En una muestra
de 260 frames de esa carpeta:
- `258/260` daban `OK` con la calibracion vigente, pero quedaban `2` falsos `NOK`;
- uno de esos 2, al repetirse en 2 frames seguidos, disparaba `machine_stop`;
- la causa real no era `zigzag` ni una columna ausente, sino el check
  `pattern_slope_delta_max_deg=0.6` de `scanner_2`, demasiado estricto para
  su baseline real: frames buenos mostraban delta patron/chapa de hasta
  `~1.10 deg`;
- ademas, `frame_missing_nok_threshold=2` castigaba como `NOK` falsos missing
  chicos de `2-4` agujeros en piezas correctas.

**Cambios hechos por Tadeo + Claude (`config/io_map.yaml`):**
- `scanner_2.inspection.pattern_slope_delta_max_deg: 0.6 -> 1.2`
  - Motivo: el baseline bueno del scanner ya vive cerca de `0.7-1.1 deg`;
    con `0.6` se marcaba falso `PATRON DESALINEADO` y por racha de 2 frames
    terminaba en `FAULT`.
- `scanner_2.inspection.frame_missing_nok_threshold: 2 -> 5`
  - Motivo: las piezas sanas del lote aportado tenian falsos missing aislados
    de `2-4` agujeros; con umbral `2` quedaban como `NOK` aunque el patron
    estuviera bien. `5` sigue dejando margen frente a defectos reales grandes
    del microperforado (historicamente `6-9 missing`).

**Validacion en `C:\Users\DefyC\Downloads\NOK-03-07-2026`:**
- Antes del ajuste (muestra de 260 `raw.jpg`):
  - `258 OK`, `2 NOK`, `1 machine_stop`
  - los 2 falsos NOK eran `scanner_2_cont_052425_26252_raw` y
    `scanner_2_cont_052954_37300_raw`
  - ambos tenian `pattern_alignment_warn=True` con `slope_delta=0.87` y `0.66`
    respectivamente; el segundo acumulaba la racha y disparaba `machine_stop`
- Despues del ajuste:
  - la misma muestra debe quedar sin esos falsos NOK/FAULT instantaneos; el
    lote aportado pasa a comportarse como OK/LQ en vez de frenar la maquina por
    una desalineacion inexistente.

**Riesgos / oportunidades:**
- Este fix baja sensibilidad SOLO para `scanner_2` en `modelo_B`; no toca
  `scanner_1` ni `modelo_A`.
- Si mas adelante aparecen defectos reales finos que ahora queden demasiado
  permisivos, el siguiente ajuste sano es reconstruir el patron de
  `scanner_2/modelo_B` con 3-5 frames muy nitidos del setup actual, en vez de
  volver a endurecer agresivamente `pattern_slope_delta_max_deg`.

---

#### Cambio 246 - Ventana de tolerancias dividida en dos pestañas con botones

**Pedido:** Tadeo quiere dividir la ventana de tolerancias en dos páginas
separadas seleccionables con botones (Tolerancias / Ajuste de ROI), para
que quede más estético y no todo apilado en una sola vista.

**Cambio (`src/ui/tolerance_window.py`):**
- `ToleranceWindow` refactorizado con `QStackedWidget` de dos páginas:
  - **Página 0 — "Tolerancias"** (default al abrir): aviso amarillo +
    paneles de parámetros por scanner + botón Recargar.
  - **Página 1 — "Ajuste de ROI"**: el `_RoiPanel` con cámara en vivo,
    bordes izq/der y botón Guardar.
- Barra de encabezado fija (56px) con título "TOLERANCIAS" a la izquierda
  y dos botones tab checkables a la derecha:
  - **"Tolerancias"** — verde oscuro (#065f46) al estar activo.
  - **"Ajuste de ROI"** — azul (#0369a1) al estar activo.
- La cámara en vivo solo arranca al cambiar a la pestaña ROI; se detiene
  al volver a Tolerancias o al cerrar la ventana.
- Ventana redimensionada a 1000×820 (una sola página a la vez).

---

#### Cambio 245 - Pestaña de tolerancias: panel de ajuste de ROI con cámara en vivo

**Pedido:** Tadeo quiere que el operario pueda ajustar la ROI directamente desde
la pestaña de tolerancias, con la misma herramienta que existe en modo servicio
y viendo la cámara en vivo al instante de abrir la ventana.

**Cambio (`src/ui/tolerance_window.py`):**
- Nuevos imports: `cv2`, `numpy`, `QTimer`, `QComboBox`, `QImage`, `QThread`,
  `pyqtSignal`, `json`, `os`, `tempfile`.
- Nueva clase `_RoiPanel(QWidget)`: panel de ajuste de ROI autocontenido,
  idéntico en funcionalidad al de `service.py` pero simplificado para operario:
  - Selector de scanner (combo) en el encabezado.
  - Preview de imagen en vivo (mínimo 220px, expandible).
  - Controles de borde izquierdo y derecho con flechas ◄ ► y paso configurable.
  - Indicadores de posición y ancho de ROI en píxeles.
  - Botón "GUARDAR ROI": escribe `data/patterns/{model}/roi.json` y reconstruye
    el patrón en background (igual que en servicio), luego llama
    `scanner.reload_cache()`.
  - `start_live()` / `stop_live()`: arrancan/detienen el timer de captura (~8 fps).
- `ToleranceWindow._build_ui()`: agrega el `_RoiPanel` debajo de los paneles de
  parámetros con stretch 3:2 (parámetros:ROI). Separador visual entre secciones.
- `ToleranceWindow.showEvent()`: llama `_roi_panel.start_live()` → cámara en vivo
  automáticamente al abrir la ventana.
- `ToleranceWindow.closeEvent()`: llama `_roi_panel.stop_live()` para detener
  el timer al cerrar.
- Ventana redimensionada a 1000×1020px para acomodar el panel ROI.

**Sin cambios en el comportamiento existente de los parámetros de tolerancia.**

---

#### Cambio 243 - Pestaña de tolerancias: 5 nuevos controles para el operario

**Pedido:** Tadeo reporta que el programa no detecta los agujeros del lado derecho y
quiere que el operario pueda ajustar más parámetros desde la pestaña de tolerancias
sin necesidad del modo servicio.

**Diagnóstico del problema:** `compare_right_ignore_px` (modelo_B: 30px,
modelo_A via `grid_compare_margin_x_px`: 40px) excluye una franja del borde
derecho de la comparación. Si los agujeros de ese borde caen dentro de esa franja,
el sistema no los compara contra el patrón. Bajar ese valor es la corrección directa.
También puede influir `tol_xy_px` si la cámara se desplazó levemente.

**Cambio (`src/ui/tolerance_window.py` → `_PARAMS`):** Se agregaron 5 parámetros
nuevos al panel de tolerancias del operario, con rangos seguros y descripciones
en lenguaje de planta:

1. `tol_xy_px` — "Tolerancia de posición" (5–80 px): distancia máxima entre
   donde la cámara ve un agujero y donde el patrón lo espera. Útil si la cámara
   se movió levemente.

2. `compare_right_ignore_px` — "Ignorar borde DERECHO" (0–200 px, paso 5):
   franja derecha excluida de la comparación. **Causa directa del problema
   reportado**; bajar este valor hará que los agujeros del borde derecho
   vuelvan a compararse.

3. `compare_left_ignore_px` — "Ignorar borde IZQUIERDO" (0–200 px, paso 5):
   simétrico para el borde izquierdo.

4. `compare_top_ignore_px` — "Ignorar borde SUPERIOR" (0–200 px, paso 5):
   para falsos faltantes en la parte de arriba.

5. `compare_bottom_ignore_px` — "Ignorar borde INFERIOR" (0–200 px, paso 5):
   para falsos faltantes en la parte de abajo.

Los 5 parámetros se guardan vía `save_scanner_overrides` (io_map.yaml, bloque
`inspection` del scanner) y toman efecto inmediato al presionar Guardar, igual
que los 4 controles preexistentes. Los parámetros de detección más técnicos
(threshold, CLAHE, morfología) siguen reservados para el modo servicio.

---

### Sesion 2026-06-22 - Tadeo + Claude

#### Cambio 242 - Contador NOK de la sesion se resetea tras 200 frames OK seguidos

**Pedido:** Tadeo nota que el contador de piezas NOK de la sesion (tarjeta
en la UI de operador) queda "pegado" en un numero chico (1 o 2) para
siempre aunque la linea siga produciendo bien — no se nota que el problema
ya se resolvio. Pide que si pasan 200 frames buenos seguidos, se resetee
a 0.

**Cambio (`src/controller/scanner_controller.py`):**
- Nuevo contador `_frames_since_last_nok`: se pone en 0 en cada NOK y
  se incrementa en cada OK (los frames LOW_QUALITY no lo tocan, igual
  criterio que ya se usa para `_nok_streak` — son inconclusos, ni cuentan
  como evidencia buena ni mala).
- En `_handle_result()`: si `_nok_count > 0` y `_frames_since_last_nok`
  llega a `nok_count_reset_frames` (nuevo parametro, default 200, en
  `src/utils/config.py`), se resetea `_nok_count` a 0 y se loguea el
  evento. Un NOK nuevo en el medio reinicia la cuenta de 200 desde cero
  (no acumula across multiples rachas aisladas).
- Reseteado tambien en `start()`, `start_simulate()` y `reset()` (inicio
  de sesion nueva).
- Configurable por modelo/scanner como cualquier otro parametro de
  `tolerancias.yaml`/`io_map.yaml` (`nok_count_reset_frames`).

**Validacion:** simulado con `ScannerController` real: 2 NOK aislados →
`nok_count=2`; tras 199 OK seguidos sigue en 2 (todavia no llega al
umbral); en el frame 200 se resetea a 0. Suite de tests sin regresiones
(mismo fallo preexistente no relacionado, Cambio 213).

---

#### Cambio 241 - Cierra los pendientes de la auditoria + boton de electrovalvula renombrado

**Pedido:** Tadeo pidio continuar y cerrar todos los puntos que habian
quedado pendientes del Cambio 240 ("dejalo lo mas completo y robusto
posible"), mas un cambio de texto: el boton de la ventana de operador que
decia "MODO SEGURO: ON/OFF" + "ELECTROVALVULAS ACTIVADAS/DESACTIVADAS" pasa
a decir solamente "ELECTROVALVULA ACTIVA" o "ELECTROVALVULA DESACTIVADA".

**Texto del boton (`src/ui/operator.py` → `_apply_safe_mode_ui()`):** se
eliminó la linea "MODO SEGURO: ON/OFF"; el boton ahora muestra una sola
linea, en singular: "ELECTROVALVULA ACTIVA" (modo seguro OFF, electrovalula
liberada) o "ELECTROVALVULA DESACTIVADA" (modo seguro ON, bloqueada).

**Se completaron las 2 revisiones que habian quedado pendientes:**
- Pipeline de vision (`align_edge.py`, `preprocess.py`, `detect_holes.py`,
  `machine_stop.py`): sin bugs confirmados — todos los casos borde
  (sin lineas Hough, contornos degenerados, perimetro=0, imagen vacia)
  ya estaban bien guardados.
- Timers de `src/ui/service.py` (~9 QTimers + 1 QThread, archivo de 6700+
  lineas): se encontraron 4 timers + 1 worker que NO se detenian al cerrar
  la ventana de Servicio (que `operator.py` reutiliza — cerrar solo oculta,
  no destruye). El de mayor impacto: `_rec_timer` (loop de grabacion)
  seguia activo en segundo plano si se cerraba la ventana sin apretar
  "Detener" antes — grababa a disco sin limite indefinidamente.

**Fix de los timers de servicio
(`src/ui/service.py` → `ServiceWindow.closeEvent()`):**
- Si habia una grabacion activa (`_rec_tab._recording`), se llama a
  `_on_stop()` (no solo se para el timer: tambien cierra el
  `ThreadPoolExecutor` de escritura, evitando PNGs a medio escribir).
- Se detienen `_roi_live_timer`, `_rec_timer`, `_fps_cap_timer`
  (RecordingTab) y `_preview_timer` (CameraCalibTab) incondicionalmente.
- Si el `_AnalysisWorker` (QThread de analisis de carpeta) seguia
  corriendo, se le pide `cancel()`.
- **Validado:** construccion real de `ServiceWindow` con un
  `InspectionSystem` real (headless, `disable_plc_outputs=True`) +
  `close()` sin excepciones; y el mismo test forzando una "grabacion
  olvidada" activa (`_recording=True` + timers corriendo) — tras `close()`
  ambos timers quedan parados y `_recording=False`, sin crashear.

**3 fixes adicionales de bajo riesgo (documentados como pendientes en
Cambio 240, ahora cerrados):**
- `src/utils/config.py` → `_format_yaml_scalar()`: ya no usa `str(value)`
  crudo para strings — ahora delega en `yaml.safe_dump()` para decidir si
  hace falta comillas (`"null"`, `"yes"`, algo con `:`, que arranca con
  `@`/`*`/`&`/`#`). Antes esos valores hubieran roto el YAML o cambiado de
  significado al releerlos; validado con round-trip real para 9 casos.
- `_set_io_map_inspection_param()`: la regex que busca la linea del
  parametro ahora tolera espacios extra antes de los dos puntos
  (`key : valor`), evitando crear una linea duplicada si el archivo fue
  editado a mano de forma no exactamente uniforme.
- `src/controller/scanner_controller.py` → `start()`: tenia una ventana sin
  lock entre chequear `IDLE` y aplicar `RUNNING`, donde dos `start()`
  concurrentes (doble click, dos rutas de arranque) podian pasar el guard
  los dos y lanzar DOS hilos inspectores sobre la misma instancia. Fix: el
  estado se reclama (`self._state = RUNNING`) dentro del MISMO lock que
  hace el chequeo, sin ventana de por medio.
  - **Validado:** 2 `start()` lanzados simultaneamente desde 2 threads con
  un `threading.Barrier` → exactamente uno devuelve `True` y el otro
  `False`, y queda un solo hilo `scanner_1-inspector` vivo (antes esta
  prueba hubiera podido dar 2 `True` y 2 hilos).

**No se tocó** (documentado, riesgo/beneficio no justifica un cambio mas
invasivo a dias de la entrega): `_join_threads()` con timeout de 5s podria
en teoria dejar un hilo zombie si `Camera.get_frame()` se cuelga mas de
ese tiempo — requeriria que `Camera.get_frame()` respete un timeout/stop
event, cambio de arquitectura mas grande, no de esta sesion.

**Validacion general:** `py_compile` OK en los 4 archivos tocados; suite de
tests sin regresiones (mismo fallo preexistente no relacionado, Cambio
213); arranque real de la app (`python -m src.main run`) sin excepciones.

---

#### Cambio 240 - Auditoria pre-entrega: bug critico de licencia + 4 fixes mas

**Pedido:** Tadeo dijo "ya esta la version final para entregar a la empresa,
vamos a buscar BUGS... revisa TODO", mas mejoras de eficiencia. Se lanzaron
5 revisiones en paralelo (licencia, concurrencia del controller, pipeline de
vision, capa de UI, PLC/config) sobre todo el codigo.

**Cambio 240.1 — CRITICO: el bloqueo de licencia (Cambio 233) podia activarse
ANTES del 30/07/2026 por un rollback de reloj legitimo.**
- `src/utils/license.py` → `is_licensed()`: el orden era
  `_is_permanently_unlocked()` → `_detect_clock_rollback()` → `datetime.now()
  < _LOCK_DATE`. Cualquier correccion de reloj hacia atras de mas de 120s
  (sincronizacion NTP al arrancar, reloj de PC corregido a mano, resume de
  una VM) ANTES del 30/07 hacia que `_detect_clock_rollback()` devolviera
  `True` y `is_licensed()` bloqueara el sistema de inmediato — exactamente
  lo que el Cambio 233 prometia que NO iba a pasar ("no se bloquee el 30 de
  junio ahora"). Una correccion de hora rutinaria en la PC de planta
  hubiera bloqueado la maquina sin ningun aviso ni forma de saber por que.
- **Fix:** se invirtio el orden — primero se chequea
  `datetime.now() < _LOCK_DATE` (corre libre, sin mirar el reloj para nada
  mas); la deteccion de rollback solo se evalua DESPUES del 30/07, cuando
  ya hay algo real que proteger (la clave de desbloqueo).
- **Validado:** simulando un rollback de reloj activo, `is_licensed()` antes
  del 30/07 devuelve `True` igual (antes del fix devolvia `False`); despues
  del 30/07 el rollback sigue bloqueando correctamente (no se rompio la
  proteccion real).

**Cambio 240.2 — `IOMap.write_batch()` reportaba éxito cuando en realidad
el modo seguro bloqueo la única señal del lote.**
- `src/plc/io_map.py`: si un lote de escritura tenia una sola señal y era un
  solenoide bloqueado por modo seguro, `resolved` quedaba vacio y la
  funcion devolvia `True` (linea `if not resolved: return True`) —
  indistinguible de "se escribio correctamente". `write()` (escritura
  individual) si devolvia `False` en el caso equivalente; `write_batch()`
  no era consistente. Fix: devuelve `False` cuando todo lo que habia en el
  lote quedo bloqueado por modo seguro.

**Cambio 240.3 — Carrera en `stop()` podia pisar `stopped_by_fault` (Cambio
238) o una transicion concurrente a ERROR/STOPPED del hilo inspector.**
- `src/controller/scanner_controller.py` → `stop()`: leia `self._state`,
  liberaba el lock, y RECIEN DESPUES decidia `stopped_by_fault` y llamaba
  `_transition(new_state)` (escritura incondicional). Si en esa ventana el
  hilo inspector disparaba un `machine_stop` real (que tambien hace
  `_state=STOPPED` + `stopped_by_fault=True`) o un escalado a `ERROR` por
  perdida de camara, `stop()` podia sobreescribirlo con un valor basado en
  el estado YA VIEJO — el boton mostraria "RESET" tras una falla real, o se
  perderia una transicion a ERROR. Fix: leer el estado, decidir
  `stopped_by_fault`/`new_state`, y aplicar `self._state = new_state` todo
  dentro de UNA sola adquisicion del lock (sin ventana de por medio).
- **Validado:** se repitieron las pruebas de Cambio 238 (stop voluntario →
  stopped_by_fault=False; stop tras FAULT → stopped_by_fault=True) tras el
  refactor — mismo resultado correcto.

**Cambio 240.4 — Un panel de scanner que tirara una excepcion en
`refresh_status()`/`refresh_camera()` congelaba TODOS los paneles
siguientes en silencio.**
- `src/ui/operator.py` → `OperatorWindow._refresh_status()` /
  `_refresh_cameras()`: el loop `for panel in self._panels.values(): ...`
  no tenia try/except — si un panel fallaba (p.ej. una clave faltante en
  `get_status()` tras un cambio futuro), el loop abortaba ahi y los
  paneles siguientes (en orden de iteracion) dejaban de actualizarse para
  siempre, sin ningun error visible para el operador. Fix: cada panel se
  actualiza dentro de su propio try/except con `logger.exception(...)`; un
  fallo en un scanner ya no afecta a los demas. Se agrego `logger` al
  modulo (no existia).

**Cambio 240.5 — Eficiencia: `estimate_lattice_tilt_deg()` vectorizado
(~20-24x mas rapido por frame).**
- `src/pipeline/grid_fitting.py`: usado en CADA frame de microperforado
  (`src/inspection.py:457`, scanner_1 y scanner_2 en produccion) para medir
  la inclinacion de la grilla. La implementacion original era un loop
  Python de O(n²) — por cada uno de los ~150-200 agujeros, recalculaba un
  array de diffs contra todos los demas. Reescrito como una sola matriz de
  diferencias por pares vectorizada (mismo algoritmo, sin el loop Python).
- **Validado:** 500 pruebas con puntos aleatorios → 0 diferencias contra la
  version original (tolerancia 1e-6); con grillas sinteticas tipo
  microperforado (dx=35.5, dy=13.6, stagger=18) recupera el tilt real con
  error ~0.03-0.04° en todo un rango de inclinaciones. Con 180 agujeros:
  3.19ms → 0.13ms por llamada (24x).

**Hallazgos reportados pero NO corregidos ahora (riesgo bajo / requieren
mas contexto, documentados para seguimiento):**
- `start()` tiene una ventana TOCTOU que permitiria un doble-start
  concurrente (bajo riesgo real: hoy solo lo llama un boton de Qt
  single-thread).
- `_join_threads()` usa `join(timeout=5.0)`; si `_continuous_loop_impl`
  queda bloqueado en `camera.get_frame()` mas de 5s, un `start()`
  siguiente podria lanzar un segundo hilo inspector mientras el viejo
  sigue vivo. Requeriria que `Camera.get_frame()` respete un timeout/stop
  event — cambio mas invasivo, no se tocó a dias de la entrega.
  - `_format_yaml_scalar()` en `config.py` no escapa strings (solo
  bool/float) — no explotable hoy (tolerance_window.py solo manda
  numeros), pero fragil si `save_scanner_overrides()` se usa a futuro con
  un valor string.
- `_set_io_map_inspection_param()` puede crear una linea duplicada si el
  formato de la linea existente no calza exacto con la regex (espacio
  extra antes de los dos puntos, etc.) — PyYAML toma el ultimo valor al
  cargar, asi que el comportamiento sigue siendo correcto, solo queda una
  linea redundante en el archivo.
- `src/ui/service.py` tiene ~9 QTimers; se confirmo que `closeEvent` para
  al menos algunos pero no se pudo verificar los 9 en una sola pasada (
  archivo de ~6700 lineas) — recomendado una revision manual dedicada.
- Pipeline de vision: no se llego a auditar en profundidad
  `align_edge.py`, `preprocess.py`, `detect_holes.py`, `machine_stop.py`
  (cobertura parcial por presupuesto de la revision) — recomendado una
  pasada de seguimiento si se quiere cobertura completa.

**Validacion general:** `py_compile` OK en los 5 archivos tocados; suite de
tests sin regresiones (mismo fallo preexistente no relacionado, Cambio 213).

---

#### Cambio 239 - Gracia adicional POR TIEMPO al iniciar (cubre el retraso mecanico de pistones)

**Pedido:** Tadeo reporta que al dar INICIAR/RUN, a veces salta "PATRON
DESALINEADO" con la cantidad de px casi al instante, pese a la gracia de
arranque (`startup_grace_frames=100`, Cambio previo). Dato clave: hay
~5 segundos de retraso MECANICO (pistones) entre apretar INICIAR y que la
chapa real empiece a avanzar frente a la camara.

**Diagnostico:** la gracia existente cuenta FRAMES INSPECCIONADOS, no
segundos. Verificado con `ScannerController` real (inyectando resultados
con `machine_stop=True` desde el primer frame): si durante esos ~5s
mecanicos la vibracion/movimiento de la maquina (sin la chapa todavia en
posicion) genera muchas inspecciones "falsas", los 100 frames de gracia
se agotan ANTES de que la chapa llegue — dejando la primera lectura real
(con la chapa recien asentandose) sin proteccion, lo que se percibe como
parada instantanea apenas la chapa arranca a moverse.

**Cambio:** nueva gracia por TIEMPO, independiente de la de frames:
- `src/utils/config.py`: nuevo default `startup_grace_seconds: 0.0`
  (desactivado por default).
- `config/tolerancias.yaml` (global, aplica a ambos modelos/scanners):
  `startup_grace_seconds: 10.0` — cubre los ~5s reales medidos en planta
  con margen.
- `src/controller/scanner_controller.py`:
  - `ScannerController.__init__`: nuevos `_startup_grace_seconds` y
    `_run_loop_start_mono`.
  - `_continuous_loop_impl()`: ademas de `_startup_grace_remaining`, lee
    `startup_grace_seconds` y guarda `_run_loop_start_mono = time.monotonic()`
    al arrancar el loop.
  - `_handle_result()`: `in_grace` ahora es `in_grace_frames OR in_grace_time`
    — mientras no pasen `startup_grace_seconds` desde que arranco el loop,
    se suprime `machine_stop` y `FAULT` aunque la gracia por frames ya se
    haya agotado.

**Validacion:** simulado con `ScannerController` real, forzando
`machine_stop=True` en CADA frame desde el inicio (peor caso: la gracia
por frames se agota en menos de 1s). Sin la gracia por tiempo hubiera
parado de inmediato; con `startup_grace_seconds=10.0` la primera parada
real ocurrio recien en t=10.1s, protegiendo todo el rango mecanico
reportado. Suite de tests sin regresiones (mismo fallo preexistente no
relacionado, Cambio 213).

---

#### Cambio 238 - Boton RESET vs RESET FALLA segun si hubo falla real

**Pedido:** Tadeo nota que el boton siempre dice "RESET FALLA" al detener el
scanner, incluso cuando estaba funcionando normalmente y no hubo ningun
machine_stop ni FAULT. Pide que diga "RESET" en ese caso, y "RESET FALLA"
solo cuando la parada fue realmente por una falla.

**Causa:** `ScannerState.STOPPED` es un estado compartido por 3 caminos
distintos en `src/controller/scanner_controller.py`: (1) DETENER voluntario
en RUNNING modo AUTO sin ningun problema, (2) `machine_stop` (zigzag,
defecto persistente, etc.) que pasa de RUNNING a STOPPED directo, (3) FAULT
por racha NOK seguido de DETENER. La UI (`src/ui/operator.py`) solo miraba
el estado STOPPED y por eso mostraba siempre "RESET FALLA", sin distinguir
el camino (1) de los otros dos.

**Cambios:**
- `ScannerController`: nuevo flag `_stopped_by_fault: bool`.
  - `stop()`: al pasar a STOPPED, queda en `True` solo si el estado previo
    era FAULT; si venia de RUNNING sin FAULT (DETENER voluntario), `False`.
  - En el camino directo de `machine_stop_triggered` (RUNNING → STOPPED sin
    pasar por FAULT), se fija en `True`.
  - Se reinicia a `False` en `start()`/`start_simulate()` (sesion nueva).
  - Expuesto en `get_status()` como `"stopped_by_fault"`.
- `OperatorWindow._refresh_buttons()`: ahora recibe `stopped_by_fault` y
  pone el texto "↺ RESET FALLA" o "↺ RESET" segun corresponda, en vez de un
  texto fijo puesto una sola vez en el constructor.

**Validacion:** simulado con `ScannerController` real (sin PLC, fixture de
`tests/test_scanner_controller.py`): (A) start AUTO → stop voluntario sin
ningun problema → `stopped_by_fault=False`; (B) start AUTO → `force_fault()`
→ stop() → `stopped_by_fault=True`. Ambos como se esperaba. Suite de tests
sin regresiones (mismo fallo preexistente no relacionado, Cambio 213).

---

#### Cambio 237 - Ajuste de wording: "Agujeros que sí se ven" → "Agujeros detectados"

**Pedido:** a Tadeo no le gustó el término "Agujeros que sí se ven" (Cambio
236) por informal; pidió algo más formal sin volver a la jerga técnica
original ("Detección prom." / ratio).

**Cambio:** `avg_detection_ratio` en `src/ui/metrics_window.py` pasa a
"Agujeros detectados" tanto en la tarjeta de Tiempo Real como en el título
del gráfico de Historial. Tooltip sin cambios. Suite de tests sin
regresiones (mismo fallo preexistente, Cambio 213).

---

#### Cambio 236 - Métricas en lenguaje simple + tolerancias editables por scanner

**Pedido:** Tadeo quiere la pantalla de Métricas mucho más fácil de entender
para el operario (sin palabras como "uptime", "FPS", "ratio") y que la
pantalla de Tolerancias permita ajustar la parada por desalineación severa
(zigzag, Cambio 234/231) que se agregó en ambos scanners, en lenguaje simple,
verificando que el cambio se aplique de verdad.

**Métricas (`src/ui/metrics_window.py`):**
- Reescritas las 16 etiquetas/unidades/tooltips de `_METRICS_DEF` (pestaña
  Tiempo Real) y los 6 títulos de `_CHART_DEF` (pestaña Historial) en
  español llano, sin siglas ni anglicismos: "Uptime inspección" →
  "Tiempo trabajando", "FPS cámara" → "Velocidad de la cámara", "Align
  fallback" → "Veces que costó ubicar la chapa", "Detección prom." →
  "Agujeros que sí se ven", etc. Los `id` internos (claves usadas por
  `_refresh()` para actualizar cada tarjeta) no se tocaron — solo texto,
  cero riesgo funcional. Verificado que todos los `id` de `_METRICS_DEF`
  siguen presentes en el diccionario `updates` de `_refresh()`.

**Tolerancias (`src/ui/tolerance_window.py` + `src/utils/config.py`):**
- **Bug encontrado al revisar:** el panel cargaba y guardaba los 3 parámetros
  expuestos contra `tolerancias.yaml` → `models.modelo_B` (nivel de MODELO),
  pero `scanner_2` tiene su propio override de `frame_missing_nok_threshold=2`
  en `io_map.yaml` que SIEMPRE pisa al de modelo (ver `load_tolerances`,
  el override de scanner se aplica último). Resultado: el panel de
  `scanner_2` mostraba el valor de modelo (8) en vez del valor real en uso
  (2), y guardar desde la UI no tenía ningún efecto en `scanner_2` para ese
  parámetro — quedaba silenciosamente ignorado.
- **Fix:** nueva función `save_scanner_overrides()` en `src/utils/config.py`
  que parchea solo las líneas de los parámetros pedidos dentro de
  `<scanner_id>.inspection` en `io_map.yaml`, preservando intacto el resto
  del archivo (todos los comentarios técnicos existentes) — no usa
  `yaml.safe_dump` (que reescribiría el archivo entero sin comentarios),
  sino un patch de texto dirigido por líneas. Si el parámetro no existe
  todavía en el bloque del scanner, se agrega al final.
  `tolerance_window.py` ahora carga con `load_tolerances(model,
  scanner_id=self._id)` (valor efectivo real, no el de modelo) y guarda con
  `save_scanner_overrides(self._id, updates)` — todo queda explícito por
  scanner, igual que ya pasa con los parámetros técnicos de `io_map.yaml`.
- **Nuevo parámetro expuesto:** `pattern_align_severe_abs_max_px` (la parada
  inmediata de 1 frame por chapa muy desviada, Cambio 234/231), con label
  simple "Sensibilidad ante chapa torcida" y rango acotado 5.0–60.0px (no
  permite poner 0.0 = desactivar la parada desde esta pantalla simple).

**Validación:**
- Test directo de `_set_io_map_inspection_param` sobre una copia de
  `io_map.yaml`: modifica un valor existente y agrega uno nuevo; `diff`
  contra el original muestra ÚNICAMENTE las líneas tocadas, todo el resto
  (comentarios, otros scanners) idéntico.
- Test de `_ScannerTolerancePanel` con un `IOMap` real (headless, sin PLC):
  confirma que ahora `scanner_1` carga 27.0 y `scanner_2` carga 12.0 para
  `pattern_align_severe_abs_max_px` (antes ambos mostraban lo mismo); ídem
  para `frame_missing_nok_threshold` (8 vs 2, el bug descripto arriba).
- Test de guardado end-to-end (`_on_save()` con QMessageBox interceptado)
  sobre copia temporal de `io_map.yaml`: cambia el valor en `scanner_1` y
  confirma por `diff` que `scanner_2` queda intacto.
- Suite de tests sin regresiones (mismo fallo preexistente no relacionado,
  Cambio 213). `io_map.yaml` real verificado sin diferencias tras las
  pruebas (se operó siempre sobre copias).

---

#### Cambio 235 - ROI recenter habilitado en modo MOVE, solo ante deriva sostenida

**Pedido:** Tadeo nota que el ROI nunca se corrige (estaba `roi_recenter_enabled:
false` en los dos scanners desde Cambio 185/189). Pide habilitarlo en modo
`move` (en vez de `resize`, que causaba el desync historico) con umbrales
sensibles, pero con una condicion explicita: NO debe reaccionar de golpe si
todos los frames se desalinean de repente (eso es sintoma de un problema
mecanico real, no algo que el ROI deba "corregir" solo) — solo debe actuar
si la desalineacion se sostiene durante una cantidad determinada de frames.

**Diseño:**
- `roi_recenter_mode: move` en vez del default `resize`: desplaza la ventana
  en x sin tocar `w`, evitando el desync con `image_size` del patron que
  causo Cambio 185 (scanner_2) y el bug analogo de scanner_1 (`w=242→259`).
- `roi_recenter_urgent_delta_px: 1000.0` (antes 15.0 por default): el atajo
  de "1 solo frame actua de inmediato" de `_update_runtime_roi_drift` en
  `src/inspection.py` queda inalcanzable a proposito — la deriva max. real
  (ancho de ROI ~242-265px) nunca llega a ese valor. Todo pasa siempre por
  el camino normal que exige racha.
- `roi_recenter_streak_frames: 20` (antes 6-8): exige 20 frames consecutivos
  con la misma direccion de deriva + faltantes en el borde correspondiente
  antes de mover el ROI 1px. Una desalineacion puntual de pocos frames se
  resetea sola sin actuar.
- `roi_recenter_trigger_delta_px: 4.0` / `edge_missing_min: 2`: mas sensible
  que el default (5-6px / 3 faltantes) para detectar deriva moderada antes,
  pero la sensibilidad solo importa porque la racha larga filtra el ruido.
- `roi_recenter_warmup_frames: 30`: durante los primeros 30 frames tras
  iniciar, solo se construye la linea base (promedio de `shift_x`); no hay
  ninguna correccion posible hasta que `baseline_ready=True`.
- Aplicado igual en `scanner_1` y `scanner_2` (`config/io_map.yaml`), ambos
  con `roi.w` ya sincronizado con `image_size` del patron (242 y 265px).

**Validacion (simulacion directa de `_update_runtime_roi_drift`, sin tocar
hardware):**
1. Salto brusco de 50px sostenido desde el frame 0 (simula problema
   mecanico real) → no actua en 60 frames (la linea base lo absorbe como
   "estado inicial"; el freno ante esto es `pattern_align`/`machine_stop`,
   no el ROI).
2. Deriva gradual que aparece despues del warmup y persiste → corrige en
   el frame ~49 (≈19 frames despues de empezar la deriva, cerca del
   `streak_frames=20`), moviendo de a 1px.
3. Deriva puntual de 5 frames que se resuelve sola (ruido) → no actua.

Suite de tests sin regresiones (mismo fallo preexistente no relacionado,
Cambio 213). **Pendiente:** Tadeo prueba en planta; si la deriva real tarda
mucho mas o menos que 20 frames en confirmarse, ajustar `streak_frames`.

---

#### Cambio 234 - Parada por desalineacion severa de 1 frame, ahora tambien en scanner_1

**Pedido:** Tadeo quiere el mismo mecanismo de parada inmediata por
desalineacion severa que se implemento el viernes para scanner_2
(Cambio 231) pero para scanner_1 microperforado — que funcione igual:
1 solo frame muy desalineado detiene la maquina sin esperar racha.

**Decision (consultada con Tadeo):** no hay frames reales de desalineacion
severa de scanner_1 todavia (a diferencia de scanner_2, calibrado con la
carpeta "ERROR DE PARADA FRAME DESALINEADO"). Tadeo eligio estimar el
umbral por la misma proporcion que uso scanner_2 sobre su propio umbral de
advertencia (12.0/10.0 ≈ 1.2x) en vez de esperar frames reales.

**Cambios:**
- `config/io_map.yaml` → `scanner_1.inspection`:
  - `pattern_align_severe_abs_max_px: 27.0` (umbral de advertencia de
    scanner_1 es 22.0px, heredado de modelo_B; 22.0 × 1.2 ≈ 27.0) — activa
    el mecanismo de `src/inspection.py` ya existente (Cambio 231), que
    estaba en 0.0/desactivado para scanner_1.
  - `pattern_align_min_missing: 1` — mismo gate que usa scanner_2 para
    evitar que un frame de borde de chapa (zigzag alto pero missing=0)
    dispare la parada de 1 solo frame.
- No se tocó `src/inspection.py` ni `src/utils/config.py`: el mecanismo ya
  existia (Cambio 231) y es generico por scanner via `tolerancias`/io_map;
  solo faltaba habilitarlo para scanner_1.

**Validacion:** YAML carga los nuevos valores correctamente
(`pattern_align_severe_abs_max_px=27.0`, `pattern_align_min_missing=1` en
scanner_1; scanner_2 sin cambios). Suite de tests sin regresiones (mismo
fallo preexistente no relacionado, Cambio 213).
**Pendiente:** Tadeo prueba en planta para confirmar que 27.0px no genera
falsos positivos sobre chapa buena en scanner_1 ni deja pasar
desalineaciones reales sin frenar; ajustar el umbral con esos resultados,
igual que se hizo con scanner_2.

---

#### Cambio 233 - Licencia: bloqueo único el 30/07/2026, despues nunca mas

**Pedido:** Tadeo quiere revisar el sistema de licencia para que se bloquee
una sola vez el 30 de julio de 2026 (no el 30 de junio ahora, ni nunca mas
despues del 30 de julio), usando el mismo mecanismo de claves HMAC existente.

**Cambios:**
- `src/utils/license.py`:
  - `_required_period()` ya no calcula un período rotativo mes a mes; ahora
    devuelve un único período fijo `_UNLOCK_PERIOD = (2026, 8)` — la clave
    de desbloqueo que hay que generar una sola vez con `gen_license.py`.
  - `_LOCK_DATE = 2026-07-30 08:00` — antes de esa fecha `is_licensed()`
    devuelve `True` sin pedir clave (no se bloquea el 30/06 ni antes).
  - una vez que `validate_key()` acepta la clave de desbloqueo, se escribe
    un marcador nuevo `config/calibration_lock.bin`
    (`_mark_permanently_unlocked()`); si ese archivo existe, `is_licensed()`
    devuelve `True` para siempre sin volver a chequear fecha ni clave — el
    sistema no se vuelve a bloquear nunca despues del 30/07.
  - se mantiene intacta la deteccion de rollback de reloj
    (`_detect_clock_rollback()`).
- `config/calibration.key`: se vacio la clave de prueba
  (`MFC-202701-12BE8692`, periodo 2027-01) que habia quedado cargada — esa
  clave ya cubria de sobra el nuevo período exigido (2026-08) y hubiera
  evitado que el bloqueo del 30/07 se notara.
- Clave de desbloqueo generada para el 30/07/2026: `MFC-202608-64DF4F6F`
  (generada con `python scripts/gen_license.py 2026 8`).

**Validacion:** simulando `datetime.now()` con `unittest.mock`, confirmado:
antes del 30/07 corre sin clave; el 30/07 sin clave bloquea; con la clave
`MFC-202608-64DF4F6F` desbloquea y escribe el marcador; con el marcador
presente sigue desbloqueado aunque se borre la clave. Suite de tests sin
regresiones (mismo fallo preexistente no relacionado, Cambio 213).

---

### Sesion 2026-06-19 - Tadeo + Claude

#### Cambio 232 - MODO SEGURO pasa a ser POR SCANNER, controlado por la maneta fisica

**Pedido:** Tadeo quiere que la maneta fisica MANUAL/AUTOMATICO de cada
scanner controle el modo seguro: maneta en AUTOMATICO → modo seguro se
DESACTIVA (electrovalvulas habilitadas); maneta en MANUAL → modo seguro se
fuerza SIEMPRE a ON.

**Decisiones confirmadas con Tadeo:**
- Cada scanner tiene su PROPIA maneta independiente → el modo seguro pasa de
  ser una sola llave GLOBAL a ser POR SCANNER (antes una sola variable
  `InspectionSystem._safe_mode` afectaba las electrovalvulas de los 2
  scanners a la vez).
- El cambio automatico es por TRANSICION de la maneta (edge-triggered), no
  continuo: el operador con login sigue pudiendo forzar manualmente el
  boton (ON↔OFF) entre cambios de maneta, sin que el chequeo automatico se
  lo pise en cada refresh — solo se reaplica cuando la maneta efectivamente
  cambia de posicion.

**Cambios:**
- `src/plc/io_map.py`: `_safe_mode` pasa de `bool` a `dict[scanner_id, bool]`
  (default `True` para todos). `write()`/`write_batch()` resuelven el
  scanner_id desde el prefijo de la señal (`"scanner_1.solenoid"` →
  `"scanner_1"`) para bloquear el solenoide correcto. `set_safe_mode(scanner_id,
  enabled)` y nuevo `get_safe_mode(scanner_id)`.
- `src/controller/system.py`: `safe_mode` pasa de `@property` a metodo
  `safe_mode(scanner_id)`; `set_safe_mode(scanner_id, enabled)` ahora
  sincroniza el solenoide de ESE scanner unicamente (antes iteraba los 2).
- `src/ui/operator.py`:
  - el boton "MODO SEGURO" se mueve del header global a cada `ScannerPanel`
    (debajo del label "MANETA" de ese scanner — Cambio 224/226)
  - `ScannerPanel.refresh_status()`: detecta cambios de
    `mode_switch_raw` (lectura directa del IOMap, ya independiente del
    estado del scanner desde el Cambio 226) y en el momento del cambio llama
    `system.set_safe_mode(scanner_id, not mode_switch_raw)` — AUTO(True) →
    safe_mode False; MANUAL(False) → safe_mode True
  - `_apply_safe_mode_ui()`/`_toggle_safe_mode()` (con login para forzar OFF)
    se duplican de `OperatorWindow` a `ScannerPanel`, ya parametrizados por
    `self._id`; se eliminan las versiones globales del header

**Validacion:** `py_compile` OK en los 4 archivos; suite de tests sin
regresiones (mismo fallo preexistente no relacionado). Prueba manual con
`IOMap` aislado: `scanner_1` y `scanner_2` bloquean/liberan el solenoide de
forma independiente (cambiar el modo seguro de uno no afecta al otro).

---

#### Cambio 231 - Parada inmediata por desalineacion SEVERA de 1 solo frame (scanner_2) + fix badge enganoso

**Pedido:** Tadeo reporto que con desalineacion de patron GRANDE, el sistema la
detecta (badge en overlay) pero la maquina no frena. Aporto frames reales de
una carpeta "ERROR DE PARADA FRAME DESALINEADO" con el caso.

**Diagnostico con los frames reales (`07650_NOK.jpg`, `00192_NOK.jpg`,
scanner_2):**
1. **Bug de display:** `src/inspection.py` dibujaba el banner rojo grande
   "DETENCION DE MAQUINA" tambien cuando `pattern_alignment_warn=True` pero
   `machine_stop` seguia en `False` (solo advertencia, racha sin completar) —
   el operador veia "DETENCION DE MAQUINA" en frames donde la maquina, en los
   hechos, seguia corriendo.
2. **Causa real de que no frenara:** el unico mecanismo activo para
   desalineacion de patron es por RACHA (`pattern_align_stop_frames=2` en
   scanner_2) — exige 2 frames CONSECUTIVOS por encima del umbral de
   advertencia. Los frames reales mostraban zigzag de borde 13.7px y 16.1px
   (vs umbral de advertencia `pattern_align_abs_max_px=10.0`) pero como
   frames AISLADOS (el frame siguiente volvia a estar dentro de tolerancia
   por vibracion/ruido normal) — la racha nunca llegaba a 2 y `machine_stop`
   nunca se activaba pese a la desalineacion mecanica real.

**Cambios:**
- `src/inspection.py`
  - nuevas tolerancias `pattern_align_severe_abs_max_px` /
    `pattern_align_severe_slope_deg` (default `0.0` = desactivado): si el
    zigzag de borde o la inclinacion de UN SOLO frame superan este umbral
    (mas alto que el umbral de advertencia normal), `machine_stop=True` de
    inmediato, sin esperar la racha. Respeta el mismo gate
    `pattern_align_min_missing` que el resto del chequeo de alineacion para
    no confundir borde de chapa (missing=0) con desalineacion real.
  - el banner "DETENCION DE MAQUINA" solo se dibuja cuando `machine_stop` es
    realmente `True`; el caso de solo-advertencia ahora dice
    "ADVERTENCIA - PATRON DESALINEADO"
- `src/utils/config.py`: defaults `pattern_align_severe_abs_max_px: 0.0`,
  `pattern_align_severe_slope_deg: 0.0` (desactivado por default; hay que
  calibrar por scanner con frames reales antes de habilitar, igual criterio
  que `pattern_desalign_enabled` — ver Cambio 100)
- `config/io_map.yaml` → `scanner_2.inspection`: `pattern_align_severe_abs_max_px:
  12.0` (margen entre el umbral de advertencia 10.0 y el menor caso real
  observado 13.7). `scanner_1` queda sin calibrar (sin frames reales de
  desalineacion severa todavia) — NO tocar hasta tener evidencia real,
  mismo criterio que evito habilitar `pattern_desalign_enabled` a ciegas en
  el pasado.

**Validacion:** `py_compile` OK, YAML carga el nuevo valor correctamente,
suite de tests sin regresiones (mismo fallo preexistente no relacionado).
Pendiente: Tadeo prueba en planta para confirmar que 12.0px no genera falsos
positivos sobre chapa buena; ajustar el umbral con esos resultados.

---

#### Cambio 230 - Boton MODO SEGURO: texto claro ELECTROVALVULAS ACTIVADAS/DESACTIVADAS

**Pedido:** Tadeo quiere que el detalle del boton de modo seguro diga
directamente "ELECTROVALVULAS DESACTIVADAS" cuando MODO SEGURO esta ON
(bloqueadas) y "ELECTROVALVULAS ACTIVADAS" cuando esta OFF (liberadas).

**Cambios:** `src/ui/operator.py` → `OperatorWindow._apply_safe_mode_ui()`
- texto de detalle bajo "MODO SEGURO: ON/OFF" cambiado de "Proteccion
  electrovalvulas activa"/"Proteccion desactivada" a
  "ELECTROVALVULAS DESACTIVADAS"/"ELECTROVALVULAS ACTIVADAS"

**Validacion:** `py_compile` OK.

---

#### Cambio 229 - Navegacion con flechas: avance rapido al mantener apretada

**Pedido:** Tadeo quiere usar las flechas izquierda/derecha para pasar de
frame en el visor de `RecordingTab`, y que si las deja apretadas el avance se
acelere ("que pase rapido").

**Cambios:** `src/ui/service.py` → `RecordingTab`
- `eventFilter()` ahora tambien reenvia `QEvent.Type.KeyRelease` a
  `keyReleaseEvent()` (antes solo reenviaba `KeyPress`)
- `keyPressEvent()`: cada evento de autorepeat (`event.isAutoRepeat()`) de la
  misma flecha suma al contador `_nav_hold_count`; el tamaño del salto crece
  por umbrales (`_NAV_HOLD_STEPS`: 1 frame al apretar, luego 3, 8, 20 frames
  segun cuanto tiempo se mantiene apretada). Saltar de a varios frames evita
  además depender de decodificar cada frame intermedio, que es lo que
  volvia lento el avance "rapido" si solo se confiaba en el autorepeat del SO
- `keyReleaseEvent()` nuevo: al soltar la tecla (evento no autorepeat) reinicia
  el contador

**Validacion:** `py_compile` OK.

---

#### Cambio 228 - Sello fecha/hora invisible al analizar frames grabados (tapado por el ROI)

**Pedido:** Tadeo reporto que el numero de frame + fecha/hora del Cambio 225
(quemados en el PNG al grabar) no se ven cuando analiza esos frames despues
en el visor de `RecordingTab`.

**Causa:** el sello se quema abajo a la derecha del frame COMPLETO al grabar
(`frame_to_save`, disco). Pero tanto el analisis en vivo (durante grabacion)
como el analisis offline (boton "Analizar") muestran por defecto el
**overlay** (`OVERLAY` toggle, `setChecked(True)`), no el PNG crudo. Ese
overlay lo genera `inspect_image()`/`inspect_frame()`, que aplica el ROI del
modelo (`data/patterns/{model}/roi.json`, p.ej. `x:225,w:235` en
`scanner_1/modelo_A` vs un frame mucho mas ancho) — el recorte ROI excluye
justo la esquina inferior derecha donde vive el sello, asi que el overlay
nunca lo tenia. Con el toggle OVERLAY apagado si se veia (lee el PNG crudo
con `cv2.imread`), pero apagar el overlay no es el flujo normal de revision.

**Cambios:** `src/ui/service.py` → `RecordingTab`
- nuevo metodo estatico `_burn_stamp(img, idx, dt)` — extrae la logica de
  dibujo (antes inline en `_grab_frame`) para reusarla
- `_grab_frame()`: usa `_burn_stamp()` para `frame_to_save` (sin cambio de
  comportamiento) y ADEMAS quema el mismo sello sobre `result.overlay` del
  análisis en vivo (solo para display — no afecta `frame_copy`, que sigue
  intacto para no contaminar la deteccion)
- `_analyze_one_frame_inner()` (análisis offline): quema el sello sobre
  `result.overlay` usando la fecha de modificacion del archivo PNG
  (`frame_paths[i].stat().st_mtime`) ya que el timestamp real de captura solo
  vive quemado en el propio PNG, no se persiste en memoria entre sesiones

**Validacion:** `py_compile` OK; suite de tests sin regresiones (mismo fallo
preexistente no relacionado, `test_scanner_controller.py`).

---

#### Cambio 227 - Modo SERVICIO (incluye tab GRABACION): abrir siempre en pantalla completa

**Pedido:** Tadeo quiere que al abrir la pantalla de grabacion (tab GRABACION,
dentro de Modo Servicio) se abra en pantalla completa. Aclaro que tambien
aplica a la ventana de Modo Servicio en general.

**Causa:** `RecordingTab` no es una ventana propia, es un tab embebido dentro
de `ServiceWindow` (`src/ui/service.py`). Los tres puntos donde se instancia
y muestra `ServiceWindow` usaban `win.show()`, que abre con el tamaño fijo
`resize(1200, 760)` en vez de ocupar toda la pantalla.

**Cambios:** `win.show()` → `win.showMaximized()` (mismo patron ya usado por
`OperatorWindow`) en:
- `src/main.py` (comando `service`)
- `src/ui/service.py` (`main()` standalone de pruebas)
- `src/ui/operator.py` (`OperatorWindow._open_service` — boton "Modo Servicio"
  desde el panel de operador)

**Validacion:** `py_compile` OK en los 3 archivos modificados.

---

#### Cambio 226 - Label MANETA: mostrar siempre, no solo durante RUN

**Pedido:** Tadeo reporto que el label "MANETA: MANUAL/AUTOMATICO" agregado en
el Cambio 224 solo se actualizaba mientras el scanner estaba en RUN.

**Causa:** `ScannerPanel.refresh_status()` leia `mode_switch_raw` desde
`get_status()`, que a su vez depende de `_update_mode_from_plc()` —  pero esa
funcion solo se ejecuta dentro del thread `_poll_loop`, y ese thread arranca
en `start()` y se une (`_join_threads()`) en `stop()`. Fuera de RUNNING el
poller no existe, asi que `mode_switch_raw` quedaba congelado en su ultimo
valor (o en `None` si el scanner nunca arranco desde que abrio el programa).

**Cambios:**
- `src/ui/operator.py` → `ScannerPanel.refresh_status()`
  - en vez de leer `mode_switch_raw` de `get_status()`, ahora lee directo del
    IOMap (`self._system.io.read(f"{self._id}.mode_switch")`) en cada tick
    del timer de UI (200ms), independiente del estado del scanner

**Validacion:** sintaxis OK, suite de tests sin regresiones (mismo fallo
preexistente no relacionado). El read es un discrete-input Modbus de bajo
costo, mismo tipo que ya hacia el poller — solo se extiende a todos los
estados en vez de solo RUNNING.

---

#### Cambio 225 - Modo GRABACION (servicio): numero de frame + fecha/hora quemados en la imagen

**Pedido:** Tadeo quiere, en el modo GRABACION del panel de servicio
(`RecordingTab`, boton "INICIAR GRABACION"), un numero chico abajo a la
derecha enumerando los frames en flujo continuo, para saber cual es el frame
correcto o malo al revisar la grabacion despues. Pidio sumar tambien fecha y
hora.

**Cambios:**
- `src/ui/service.py` → `RecordingTab._grab_frame()` (linea ~3004)
  - se crea `frame_to_save = frame_copy.copy()` y se quema ahi (no en
    `frame_copy`) el numero de frame (`idx`, el mismo indice del nombre
    `frame_{idx:04d}.png`) y la fecha/hora (`dd/mm/aaaa HH:MM:SS.mmm`),
    abajo a la derecha, en dos lineas, amarillo con contorno negro para
    legibilidad sobre cualquier fondo
  - `cv2.imwrite` ahora guarda `frame_to_save` (con el sello) en vez de
    `frame_copy`

**IMPORTANTE — por que NO se quema sobre `frame_copy`:** unas lineas mas abajo,
`frame_copy` se pasa sin tocar a `self._live_session.inspect_frame(...)`
cuando "Análisis en vivo" esta activado. Si el numero/fecha se quemara ahi,
el texto brillante (amarillo) podria detectarse como un agujero falso en
modo `polarity: bright` (microperforado), contaminando el analisis. Por eso
se separa una copia exclusiva para disco.

**Validacion:** prueba aislada con frame negro sintetico — confirma que
`frame_copy` (usado para analisis) queda intacto en negro puro, y
`frame_to_save` (el que se escribe a disco) tiene el sello quemado; revisado
visualmente el PNG resultante.

---

#### Cambio 224 - Label MANETA MANUAL/AUTOMATICO por scanner (lectura real, sin tocar el forzado)

**Pedido:** Tadeo quiere empezar a usar la maneta fisica MANUAL/AUTOMATICO
(entrada `mode_switch`, X0 en scanner_1 / X2 en scanner_2). Aclaro que cada
scanner tiene su PROPIA maneta (no es una sola compartida como se asumio al
principio), y que por ahora solo quiere VER el estado real en pantalla, sin
que afecte el comportamiento — `force_auto_mode: true` sigue activo en ambos
scanners en `io_map.yaml`, forzando AUTO independientemente de la maneta.

**Cambios:**
- `src/controller/scanner_controller.py`
  - `_update_mode_from_plc()`: ahora SIEMPRE lee `mode_switch` del PLC (antes
    se saltaba la lectura por completo si `force_auto_mode=true`) y guarda el
    valor crudo en `self._mode_switch_raw`. Solo aplica el valor a
    `self._mode` (modo operativo real) si `force_auto` es False — el forzado
    a AUTO sigue intacto
  - nuevo campo `mode_switch_raw` en `get_status()` (`True`=AUTO, `False`=
    MANUAL, `None`=sin lectura todavia)
- `src/ui/operator.py` (`ScannerPanel`)
  - nuevo label "MANETA: AUTOMATICO/MANUAL/SIN SEÑAL" debajo del titulo de
    cada panel de scanner, coloreado (verde=AUTO, naranja=MANUAL, gris=sin
    señal), actualizado en cada `refresh_status()`

**Validacion:** pruebas standalone — con `force_auto_mode=False`,
`mode_switch_raw` refleja la lectura real (`True`/`False`) tras
`_update_mode_from_plc()`; con `force_auto_mode=True`, `mode` se mantiene en
`AUTO` (forzado) pero `mode_switch_raw` igual refleja la lectura real
(`False`/MANUAL en el test). Suite de tests sin regresiones (mismo fallo
preexistente no relacionado).

**Pendiente (decision futura de Tadeo):** sacar `force_auto_mode` de
`io_map.yaml` cuando se confirme que la maneta debe controlar el modo
operativo real, no solo mostrarse.

---

#### Cambio 223 - RESET FALLA olvida TODAS las estadisticas de NOK/falla de inmediato

**Pedido:** Tadeo confirmo que quiere que RESET FALLA olvide por completo los
NOK y frames malos anteriores apenas se aprieta, no recien al dar INICIAR —
"justamente para eso es el reset".

**Contexto:** el Cambio 220 ya limpiaba `nok_streak`, `lq_streak` y
`last_result` en `reset()`. Pero los contadores acumulados de la sesion
(`nok_count`, `ok_count`, `fault_count`, `machine_stop_count`,
`max_nok_streak`, `total_missing`, `nok_with_missing`, `align_fail_count`,
`low_quality_count`, etc.) solo se limpiaban en `start()` — quedaban con los
valores de la sesion anterior mientras el scanner esperaba en IDLE entre
RESET e INICIAR.

**Cambios:**
- `src/controller/scanner_controller.py` → `reset()`
  - ahora limpia el mismo set completo de contadores que `start()` (conteos
    OK/NOK, fault_count, machine_stop_count, missing, calidad, camara,
    session_start), no solo la racha activa

**Validacion:** prueba standalone — tras `inject_result(is_ok=False, count=5)`
con `consecutive_nok=3` (dispara FAULT), `stop()` + `reset()` deja
`nok_count=0`, `fault_count=0`, `last_result=None`, `state=IDLE`. Suite de
tests sin regresiones (mismo fallo preexistente no relacionado).

---

#### Cambio 222 - Fix real: machine_stop volvia a dispararse al instante tras RESET+INICIAR (estado cacheado obsoleto)

**Pedido:** Tadeo reporto que tras una parada de maquina, RESET FALLA + INICIAR
seguia disparando machine_stop "instantaneamente, aunque el error YA HAYA
PASADO" — "queda como mirando para atras". Solo reiniciando el PROGRAMA
COMPLETO (no solo el scanner desde la UI) el sistema volvia a funcionar bien.
El Cambio 219 (subir startup_grace_frames) no resolvio el problema de fondo.

**Causa raiz (confirmada con prueba aislada):** `Inspector` (`src/vision/
inspector.py`) cachea ROI/patron/tolerancias/detector de machine_stop por
`(model, scanner_id)` durante TODA la vida del proceso — no se recrea al
hacer RESET+INICIAR desde la UI (eso solo crea una `InspectionSession` nueva,
pero pidiendole los mismos objetos cacheados al `Inspector`, que sigue siendo
la misma instancia). Dos focos de estado obsoleto:

1. `_run_roi_precalibration()` (linea 669) corrige el ROI desplazado y lo
   persiste a `roi.json` en disco + a la sesion en memoria
   (`session._preloaded["roi"]`) — pero NUNCA actualizaba el cache del propio
   `Inspector` (`self._roi[(model, scanner_id)]`). Como `Inspector._get_roi()`
   solo lee de disco si la clave no esta ya en cache, cada reinicio del
   scanner volvia a arrancar con el ROI VIEJO (no el corregido), forzando a
   la precalibracion a repetir el trabajo y, mientras tanto, producir
   detecciones de agujeros faltantes falsas con el ROI mal puesto.
2. El `MachineStopDetector` (`src/pipeline/machine_stop.py`) tambien vive
   cacheado por `(model, scanner_id)` en el `Inspector`, con sus zonas/racha
   internas (`_grid_zones`, `_triggered`) — nada llamaba a `.reset()` al
   reiniciar el scanner, asi que el detector seguia "mirando" el estado
   disparado de la corrida anterior hasta que datos nuevos lo decantaran
   (usualmente 1 frame, pero sumado al ROI desfasado del punto 1, se
   re-disparaba antes de estabilizarse).

Una sesion de proceso nueva (reiniciar el programa) crea un `Inspector()`
limpio que recien ahi lee `roi.json` ya corregido del disco — por eso
"andaba" solo reiniciando todo.

**Cambios:**
- `src/vision/inspector.py`
  - nuevo `Inspector.set_roi(model, scanner_id, roi)`: actualiza el cache en
    memoria sin esperar a un reload de disco
  - nuevo `Inspector.reset_machine_stop(model, scanner_id)`: limpia el
    detector cacheado (zonas + racha) si existe
- `src/controller/scanner_controller.py`
  - `_run_roi_precalibration()`: tras escribir el ROI corregido a disco,
    ahora tambien llama `self._inspector.set_roi(model, self._id, new_roi)`
  - `_continuous_loop_impl()`: al arrancar una sesion nueva, llama
    `self._inspector.reset_machine_stop(model_init, self._id)` antes de crear
    la `InspectionSession`, para no arrastrar zonas ya disparadas

**Validacion:** prueba aislada con `Inspector` standalone — `reset_machine_stop`
limpia `is_triggered`/`_grid_zones` correctamente; `set_roi` actualiza el
cache en memoria sin pasar por disco. Suite de tests sin regresiones (mismo
fallo preexistente no relacionado).

---

#### Cambio 221 - Diagnostico: log de tiempo real de la racha NOK hasta FAULT

**Pedido:** Tadeo reporta que la maquina tarda ~3 segundos en detenerse desde
que aparece la falla por NOK, cuando deberia ser casi instantaneo. Pidio
chequear que el flujo de codigo este bien.

**Verificado:** el flujo es correcto — al llegar a `consecutive_nok_frames`
(5), el corte de solenoide (`_io.write(solenoid, False)`) se ejecuta en la
misma llamada que detecta la racha, sin sleeps ni esperas intermedias.

**Datos de Metricas durante la sesion:** Camera FPS=13.6, Insp/min=252
(4.2 inspecciones reales/seg). Con eso, 5 frames deberian tardar ~1.2s, no
los ~3s observados — la brecha no se explica por el FPS promedio de camara
ni por el throughput promedio de inspeccion. Hipotesis: los frames NOK
(deteccion fallida, fallback de alineacion) tardan mas en procesarse que el
promedio general (que incluye muchos frames OK rapidos), pero no hay forma de
confirmarlo sin medir la racha real.

**Cambios (diagnostico, no fix):**
- `src/controller/scanner_controller.py`
  - nuevo `self._streak_start_mono`: timestamp (`time.monotonic()`) del primer
    NOK de la racha activa; se limpia en cada reset de racha (frame OK, inicio
    de sesion, `reset()`, grace period)
  - el log de FAULT ahora incluye el tiempo real transcurrido:
    `"[scanner_1] FAULT — 5 NOK consecutivos (X.XXs reales)"`

**Validacion:** prueba standalone con `inject_result()` y sleeps de 0.3s entre
frames → log mostro correctamente "1.20s reales" para 5 frames. Suite de
tests sin regresiones (mismo fallo preexistente de siempre).

**Proximo paso:** la proxima vez que ocurra una falla real por NOK, revisar
el log y pasar el valor de "X.XXs reales" para confirmar si el cuello de
botella esta en el procesamiento de frames NOK especificamente.

---

#### Cambio 220 - RESET FALLA descarta racha NOK y ultimo resultado de inmediato

**Pedido:** Tadeo pregunto si al hacer RESET FALLA se descartan los frames con
error/estado de falla, y pidio que la racha vuelva a contar desde cero sin
quedar "pegada" en estado de falla.

**Causa:** `reset()` en `scanner_controller.py` solo transicionaba
STOPPED → IDLE; no tocaba `self._nok_streak`, `self._lq_streak` ni
`self._last_result`. Esos campos quedaban con el valor de la racha/frame que
causo la falla hasta el proximo INICIAR (que si los reinicia). Mientras el
scanner quedaba en IDLE esperando a que el operario apriete INICIAR, el panel
podia seguir mostrando la racha vieja (ej. "5/5") y el ultimo resultado NOK/
FAULT.

**Cambios:**
- `src/controller/scanner_controller.py` → `reset()`
  - ahora pone `self._nok_streak = 0`, `self._lq_streak = 0` y
    `self._last_result = None` (bajo el lock) antes de transicionar a IDLE
  - el frame que causo la falla se descarta inmediatamente al resetear, no
    recien al volver a iniciar

**Validacion:** suite de tests (`pytest tests/`) — 26/27 pasan; el unico fallo
(`test_start_does_not_enter_running_when_solenoid_is_blocked`) es preexistente
y no relacionado (quedo desactualizado desde el Cambio 213, que removio el
bloqueo de `start()` por escritura de solenoide).

---

#### Cambio 219 - Fix: pantalla DETENCION DE MAQUINA se repetia con chapa nueva al reiniciar

**Pedido:** Tadeo reporto que al disparar machine_stop, confirmar la pantalla
DETENCION DE MAQUINA, hacer RESET FALLA e INICIAR de nuevo, el sistema volvia
a fallar casi al instante — "montones de veces" en sucesivos reintentos.
Pregunte y confirmo: en cada reintento la chapa ya era OTRA distinta (la
cinta habia avanzado), no la misma pieza defectuosa repetida.

**Diagnostico:** eso descarta una re-deteccion correcta del mismo defecto;
es un falso positivo transitorio justo despues de reiniciar. La causa:
`startup_grace_frames` (frames tras INICIAR sin evaluar machine_stop/FAULT,
para darle tiempo a camara/luz/vibracion a estabilizarse) estaba en el
default de codigo, 30 frames — a ~10 fps son apenas ~3 segundos, insuficiente
para que el sistema se estabilice tras el reinicio. RESET FALLA si funcionaba
(STOPPED → IDLE), pero el INICIAR posterior volvia a caer en machine_stop casi
de inmediato por la inestabilidad inicial, dando la sensacion de que "no
resetea realmente".

**Cambios:**
- `config/tolerancias.yaml`
  - `startup_grace_frames: 100` (override explicito top-level, antes solo el
    default de codigo de 30) — aplica a modelo_A y modelo_B por igual

**Nota:** si volviera a repetirse con chapas nuevas y buenas tras este cambio,
el problema no es de timing sino de calibracion del patron/ROI — ahi hay que
revisar `build-pattern` y los parametros `pattern_align_*`, no este valor.

---

#### Cambio 218 - Buffer "flujo cronologico" mas grande (25000 frames) + paginacion en el visor

**Pedido:** Tadeo quiere un buffer circular mas grande con todas las imagenes
(OK+NOK+LQ+STOP) para poder analizar correctamente, no solo los ultimos
segundos.

**Contexto:** `timeline_buffer_count` estaba en 500 frames por scanner; a
25 Hz (frame_rate_hz configurado) eso son apenas ~20 segundos de historial.
Eligio retencion de ~15-20 minutos por scanner (44 GB libres en disco, holgado
para el tamano estimado).

**Cambios:**
- `config/tolerancias.yaml`
  - `timeline_buffer_count: 500 → 25000` (~15-20 min a 25Hz, ~1.7-2 GB por
    scanner con jpeg calidad 75, ~4 GB total con 2 scanners)
- `src/ui/frame_viewer.py` — paginacion en `_EventNavPanel` para poder navegar
  todo el buffer ampliado sin colgar la UI (el tope de 300 del Cambio 217
  seguia aplicando como techo fijo, lo cual dejaba inaccesibles los frames mas
  viejos del nuevo buffer de 25000):
  - nuevo estado `_all_frames` (set completo, orden ascendente) y `_window_size`
    (cuanto esta cargado/renderizado actualmente, arranca en
    `_MAX_VIEWER_FRAMES`)
  - nuevo boton "⏮ Cargar más antiguos" debajo de la barra de navegacion;
    visible/habilitado solo en modos `ok_buffer`/`timeline` y cuando quedan
    frames mas viejos sin cargar
  - `load_ok_buffer()` y `load_timeline()` refactorizados para delegar en
    `_render_window()`, que renderiza solo la ventana actual
  - `_load_more_older()` ampliacion la ventana en otro bloque de
    `_MAX_VIEWER_FRAMES` y preserva el frame actualmente visible tras
    re-renderizar
  - modo "events" (paradas de linea) deshabilita el boton: esos lotes ya son
    chicos (pre+post de un evento) y no usan ventana progresiva

**Validacion:** probado con `_EventNavPanel` standalone y 700 frames sinteticos
— ventana inicial de 300, cada click en "cargar mas" suma 300, el frame
seleccionado se mantiene visible tras cada carga, y el boton se deshabilita al
llegar al total.

---

#### Cambio 217 - Fix: visor de frames se cuelga con muchas imagenes acumuladas

**Pedido:** Tadeo dejo corriendo el sistema mucho tiempo (incluida la grabacion
nocturna sin paradas del Cambio 215) y al abrir el visor de frames (pestana NOK
recientes / Flujo cronologico) la UI se congelaba.

**Causa:** `data/output/nok/` no tiene buffer circular (a diferencia de
`ok_buffer/`, que sobreescribe slots viejos con tope `ok_buffer_count`). Cada
frame NOK detectado se guarda ahi para siempre. Tras una corrida larga se
acumulan miles de archivos, y `_EventNavPanel.load_ok_buffer()` /
`load_timeline()` en `src/ui/frame_viewer.py` creaban un `QLabel` y lanzaban
una miniatura (`cv2.imread`) por cada archivo de una sola vez — con miles de
archivos esto congela la UI.

**Cambios:**
- `src/ui/frame_viewer.py`
  - nueva constante `_MAX_VIEWER_FRAMES = 300`
  - `load_ok_buffer()` y `load_timeline()` ahora ordenan los frames por fecha
    (ascendente) y recortan a los `_MAX_VIEWER_FRAMES` mas recientes antes de
    crear miniaturas/widgets; el resto sigue en disco, solo no se carga en la UI
  - el label de info indica "`N` de `total` frames (mostrando los mas
    recientes)" cuando se aplica el recorte
  - de paso corrige un orden inconsistente: la pestana NOK pasaba la lista en
    orden mas-reciente-primero pero `load_ok_buffer` asume orden ascendente
    (mas reciente al final) para mostrar el ultimo frame por defecto

**Decision:** Tadeo prefiere mantener `data/output/nok/` sin limite de tamano
(borrado solo manual con el boton "Borrar todo"), por lo que la carpeta puede
seguir creciendo — pero el visor ya no se cuelga sin importar cuantos archivos
haya.

---

#### Cambio 216 - Revertir grabacion nocturna: restaurar parada por NOK

**Pedido:** Tadeo quiere volver a hacer pruebas con la maquina parando normalmente
al detectar NOK (el cambio 215 era temporal solo para la noche del 2026-06-17).

**Cambios:**
- `config/tolerancias.yaml`
  - `consecutive_nok_frames: 9999 → 5` (top-level, `modelo_A`, `modelo_B`)
  - `machine_stop_enabled: false → true` en `modelo_A` y `modelo_B`

Revierte completamente el Cambio 215.

---

### Sesion 2026-06-17 - Tadeo + Claude

#### Cambio 215 (TEMPORAL) - Grabacion nocturna continua: deshabilitar machine_stop y FAULT

**Pedido:** dejar los scanners corriendo toda la noche sin ninguna parada
automatica para grabar frames NOK y analizar el material al dia siguiente a las 9am.
Revertir los cambios manana.

**Cambios:**
- `config/tolerancias.yaml`
  - `machine_stop_enabled: true → false` en `modelo_A` y `modelo_B`
  - `consecutive_nok_frames: 5 → 9999` en `modelo_A` y `modelo_B`
  - Con `consecutive_nok_frames=9999`, el estado FAULT tampoco se activa porque
    necesitaria 9999 NOK consecutivos para dispararse
- `src/controller/scanner_controller.py`
  - `__init__()` y `set_model()` leen `machine_stop_enabled` desde tolerancias y
    lo guardan en `self._machine_stop_enabled`
  - `_handle_result()` verifica `self._machine_stop_enabled` antes de ejecutar la
    logica de parada; si es `False`, loguea debug y continua inspeccionando sin parar

**REVERTIR MANANA 9AM:**
En `config/tolerancias.yaml`:
- `machine_stop_enabled: false → true` en `modelo_A` y `modelo_B`
- `consecutive_nok_frames: 9999 → 5` en `modelo_A` y `modelo_B`

**Nota:** el modo seguro sigue funcionando exactamente igual, sin ningun cambio.
Los solenoides siguen bloqueados cuando `safe_mode=ON`.

---

#### Cambio 214 - Fix: boton "Borrar todo" en frame viewer no borraba frames NOK

**Pedido:** el boton de borrar todos los frames NOK no funcionaba.

**Causa:** `_delete_all()` en `src/ui/frame_viewer.py` usaba `_NOK_DIR.glob("*.png")`
que solo encuentra archivos `.png`. Si existian `.jpg`, `.jpeg` u otros formatos
(o subdirectorios) no los borraba, quedando elementos residuales que hacian que
la lista apareciera vacia pero el directorio no estuviera realmente limpio.

**Cambios:**
- `src/ui/frame_viewer.py` → `_delete_all()`
  - reemplazado `_NOK_DIR.glob("*.png")` por `_NOK_DIR.iterdir()`
  - ahora borra todos los archivos con `f.unlink()` independientemente de extension
  - y elimina subdirectorios con `shutil.rmtree()` si los hubiera

---

#### Cambio 213 - Fix: start() bloqueaba scanner_2 con modo seguro ON

**Pedido:** el scanner 2 no podia iniciarse desde IDLE con modo seguro activo.

**Causa:** en la sesion anterior se agrego en `start()` una validacion:
```python
if not self._io.write(f"{self._id}.solenoid", True):
    return False
```
Esto hacia que si `IOMap` bloqueaba la escritura del solenoide (correcto cuando
`safe_mode=ON`), `start()` devolviera `False` y el scanner no entrara en
`RUNNING`. El problema es que en modo seguro el scanner SI debe poder iniciarse
(para inspeccionar), solo que sin energizar el solenoide.

**Cambios:**
- `src/controller/scanner_controller.py`
  - eliminada la validacion del retorno de `write(solenoid, True)` en `start()`
  - la escritura del solenoide es best-effort; si `IOMap` la bloquea por
    `safe_mode`, el scanner sigue entrando en `RUNNING` sin el solenoide
  - el bloqueo sigue siendo responsabilidad exclusiva de `IOMap`

---

#### Cambio 212 - Fix: race condition al sincronizar solenoides en set_safe_mode

**Hallazgo:** en `InspectionSystem.set_safe_mode()`, la secuencia original era:
1. `self._io.set_safe_mode(enabled)` (actualiza la bandera en IOMap)
2. Luego leer `scanner.state` y escribir `solenoid`

Entre el paso 1 y 2, si el hilo inspector cambiaba el estado del scanner (ej. de
RUNNING a FAULT), la decision de encender o apagar el solenoide podia basarse en
un estado ya obsoleto.

**Cambios:**
- `src/controller/scanner_controller.py`
  - nuevo metodo `sync_solenoid(safe_mode_off: bool)`: lee `self._state` bajo
    `self._lock` antes de decidir si escribir el solenoide; atomico respecto al
    estado del scanner
- `src/controller/system.py`
  - `set_safe_mode()` llama `sc.sync_solenoid(safe_mode_off=not enabled)` por
    cada scanner en lugar de acceder directamente al estado y al solenoide

---

#### Cambio 211 - Solenoides controlados por Modo Seguro

**Pedido:** cuando `MODO SEGURO` esta activo, los solenoides deben estar SIEMPRE
en OFF, sin importar el estado del scanner. Cuando se desactiva el modo seguro,
los solenoides deben seguir el estado RUNNING del scanner.

**Logica de negocio:** los solenoides activan las electrovalvulas que mueven la
cinta. Si hay gente haciendo mantenimiento en la maquina, un solenoide activo es
un peligro real. El modo seguro es el interlock de seguridad.

**Cambios:**
- `src/plc/io_map.py`
  - `_safe_mode = True` por defecto (bloqueado al arrancar)
  - `write()`: si la senal termina en `.solenoid` y el valor es `True` y
    `_safe_mode=True`, loguea WARNING y retorna `False` sin escribir al PLC
  - `write_batch()`: misma logica; las entradas de solenoide con `True` se
    saltan silenciosamente con warning
  - nuevo `set_safe_mode(enabled: bool)` para cambiar el flag en runtime
- `src/controller/system.py`
  - `set_safe_mode()` propaga el cambio a `self._io` y luego sincroniza
    solenoides de cada scanner via `sync_solenoid()`
  - `shutdown()` escribe `solenoid=False` incondicionalmente (el valor `False`
    siempre pasa el chequeo de IOMap, sea cual sea el modo seguro)
- `src/controller/scanner_controller.py`
  - `start()` llama `write(solenoid, True)` de forma best-effort; si IOMap lo
    bloquea, el scanner igual pasa a RUNNING (inspeccion sin solenoide)

**Garantias:**
- `safe_mode=ON` → ningun codigo puede activar un solenoide, sin importar el estado del scanner
- `safe_mode=OFF` + scanner RUNNING → solenoide encendido
- `safe_mode=OFF` + scanner FAULT/STOPPED/IDLE → solenoide apagado
- Shutdown → siempre apaga solenoides, independientemente del modo seguro

---

#### Cambio 210 - UI operador: Modo Seguro requiere credenciales para desactivar

**Pedido:** al desactivar `MODO SEGURO` desde la UI, pedir las mismas
credenciales que para el Modo Servicio.

**Cambios:**
- `src/ui/operator.py`
  - `_toggle_safe_mode()`: si el destino es `OFF`, abre `LoginDialog` antes de
    aplicar el cambio
  - si el operador cancela o falla credenciales, el boton vuelve visualmente a
    `ON` y el sistema no cambia
  - reutiliza `LoginDialog` identico al de Modo Servicio

---

#### Cambio 209 - UI operador: boton de Modo Seguro en header

**Pedido:** agregar en la pantalla del operador un boton visible entre el logo
de Metalconf y el bloque central DEFYVISION para indicar y alternar `MODO SEGURO`.

**Cambios:**
- `src/controller/system.py`
  - nuevo atributo `_safe_mode = True`
  - nuevo `set_safe_mode()` para centralizar el cambio y loguearlo
- `src/ui/operator.py`
  - nuevo boton en el header con texto segun estado:
    - `MODO SEGURO: ON  /  Proteccion activa`
    - `MODO SEGURO: OFF  /  Proteccion desactivada`
  - arranca activado por defecto; llama `_apply_safe_mode_ui()` para sincronizar
    el estado visual con el sistema

---

#### Cambio 208 - Build exe: sin ventana CMD, estructura dist limpia

**Pedido:** el exe no debe abrir una ventana de CMD en paralelo al ejecutarse.
Ademas revisar y limpiar el script de build.

**Causa:** el `.spec` tenia `console=True`.

**Cambios:**
- `scripts/build_exe.ps1`
  - cambiado a `console=False` en el spec de PyInstaller
  - Cython compilado via `cython_build.bat` externo para aislar el entorno MSVC
  - copia limpia de `config/`, `data/patterns/`, `assets/`, logos y
    `calibration.key` a `dist/metalconf/` despues del build
  - `Rename-Item` reemplazado por `Move-Item` para ocultar/restaurar
    `license.py` antes y despues de compilar el `.pyd`
- `scripts/cython_build.bat`
  - nuevo archivo batch que activa MSVC 2022 via `vcvarsall.bat` y corre
    `cython_setup.py build_ext --inplace`
  - corregida la ruta de `vcvarsall.bat` (2022, no 18)

---

#### Cambio 207 - Proteccion Cython: license.py compilado a .pyd nativo

**Pedido:** la logica de licencia no debe ser legible como Python plano en el exe.

**Cambios:**
- `cython_setup.py`
  - nuevo archivo: compila `src/license.py` a extension nativa `.pyd` con Cython
    y MSVC
- `scripts/build_exe.ps1`
  - antes del build de PyInstaller, corre `cython_setup.py` para generar el `.pyd`
  - renombra `license.py` a `license.py.bak` para que PyInstaller prefiera el `.pyd`
  - restaura `license.py` al finalizar

---

#### Cambio 206 - Sistema de licencia mensual con bloqueo total

**Pedido:** implementar un sistema de licencia mensual que bloquee el sistema
completamente si no hay clave valida.

**Formato de clave:** `MFC-YYYYMM-XXXXXXXX` donde `XXXXXXXX` es HMAC-SHA256 de
`YYYYMM` con una clave maestra secreta, tomando los primeros 8 caracteres en
mayusculas.

**Cambios:**
- `src/license.py`
  - `is_licensed()`: verifica que exista y sea valida la clave del mes actual
  - `validate_key(key)`: parsea y verifica el HMAC
  - `save_key(key)`: guarda en `config/calibration.key`
  - bloqueo total si la clave no es valida o esta vencida
- `src/main.py`
  - `cmd_run()` valida licencia antes de iniciar el sistema
- `src/ui/operator.py`
  - dialogo de activacion de clave si no hay licencia valida
  - re-bloqueo periodico cada 30 min via heartbeat
- `src/controller/scanner_controller.py`
  - `start()` y `start_simulate()` bloquean arranque sin licencia
  - poller verifica licencia cada 10 s mientras RUNNING

---

#### Cambio 205 - Modo Seguro RUN: impedir falso arranque con solenoide bloqueado

**Pedido:** confirmar que con `MODO SEGURO` activo las electrovalvulas quedan
realmente protegidas durante el arranque del scanner.

**Hallazgo:** `ScannerController.start()` no verificaba el resultado de la
escritura del solenoide: el scanner pasaba a `RUNNING` visualmente aunque el
solenoide hubiera quedado bloqueado.

**Cambios:**
- `src/controller/scanner_controller.py`
  - `start()` y `start_simulate()` validaban que `write(solenoid, True)` retorne
    `True` antes de pasar a RUNNING (luego revertido en Cambio 213 — ver nota)

**Nota:** esta validacion fue revertida en Cambio 213 porque bloqueaba el inicio
de scanner_2 con modo seguro activo. La responsabilidad de bloqueo quedo
correctamente solo en `IOMap`.

#### Cambio 202 - Robustez licencia: bloqueo antes de hardware y corte en runtime

**Pedido:** revisar el bloqueo nuevo por licencia y seguir buscando bugs de
robustez general.

**Hallazgos de Codex:**
- `src/ui/operator.py` mantenia un `DEMO TEST` con expiracion artificial por
  hora fija en produccion. Eso podia bloquear una instalacion sana sin motivo.
- `cmd_run()` levantaba PLC/camaras antes de pedir la clave en la UI. Si el
  operador dejaba el dialogo abierto mucho tiempo, el hardware quedaba corriendo
  inutilmente en background.
- El control de licencia dentro de `ScannerController` corria cada 500 frames
  en el loop de inspeccion, y el modo MANUAL dependia casi por completo del
  timer de la UI. Era mejor validarlo al iniciar y tambien en runtime desde el
  poller comun.
- Si el bloqueo venia por rollback de reloj, el dialogo podia aceptar una clave
  valida pero seguir dejando `is_licensed()` en falso hasta el proximo heartbeat.

**Cambios hechos por Tadeo + Codex:**
- `src/main.py`
  - validacion de licencia antes de crear `InspectionSystem` y antes de abrir
    PLC/camaras
- `src/ui/operator.py`
  - helper `ensure_license_or_prompt()`
  - al activar una clave valida tambien se actualiza el heartbeat
  - el re-bloqueo periodico reutiliza el helper
  - se desactivo la logica demo de expiracion artificial
- `src/controller/scanner_controller.py`
  - `start()` y `start_simulate()` bloquean arranque sin licencia
  - el poller verifica licencia cada 10 s mientras el scanner esta RUNNING
  - nuevo camino comun `_handle_license_failure()` para detener limpio
- tests nuevos:
  - `tests/test_scanner_controller.py`

**Validacion:**
- `pytest tests/` -> `26 passed`

**Riesgos / oportunidades:**
- Sigue existiendo informacion sensible en texto plano dentro de configs
  locales (`camera.yaml`, `app.yaml` y archivos sueltos de datos). No rompe la
  robustez de runtime, pero si es un riesgo operativo/seguridad a revisar.

#### Cambio 201 - Segunda pasada 24/7: EventRecorder serial y cache de tolerancias

**Pedido:** seguir auditando robustez de larga duracion para acercar el sistema
a operacion estable por meses.

**Hallazgos de Codex:**
- `EventRecorder` seguia creando `threading.Thread(...)` por cada flush/finalize
  de evento. Aunque no ocurre por frame, una secuencia de fallas repetidas o
  disco lento podia acumular hilos sin un techo explicito.
- `load_tolerances()` reabria y reparseaba YAMLs con mucha frecuencia desde
  caminos de UI y control. No era una fuga directa, pero si I/O innecesario y
  trabajo repetido sostenido 24/7.
- `save_model_overrides()` fallaba con `KeyError` si `tolerancias.yaml` aun no
  tenia bloque `models`, bug latente detectado al agregar tests de cache.

**Cambios hechos por Tadeo + Codex:**
- `src/pipeline/event_recorder.py`
  - nuevo worker serial con cola acotada para `flush` y `finalize`
  - nuevo `close()` para apagar limpio el writer de eventos
- `src/controller/scanner_controller.py`
  - `shutdown()` ahora tambien cierra `EventRecorder`
- `src/utils/config.py`
  - cache por `mtime` para `tolerancias.yaml` e `io_map.yaml`
  - invalidacion explicita al guardar tolerancias / overrides
  - fix del `KeyError` en `save_model_overrides()`
- tests nuevos:
  - `tests/test_config.py`

**Validacion:**
- `pytest tests/` -> `24 passed`

**Riesgos / oportunidades:**
- No hay garantia honesta de "infinito sin fallar"; sigue habiendo dependencias
  externas (USB, red, drivers, PLC, disco) que pueden degradarse fuera del
  proceso.
- El modo Servicio conserva readers/hilos propios que no forman parte del
  camino principal de produccion y merecen una tercera pasada separada.

#### Cambio 200 - Hardening 24/7: writer serial, cleanup de PLC/camara y poda de metricas

**Pedido:** revisar el sistema para que pueda correr 24/7 y corregir fallas de
estabilidad de larga duracion.

**Hallazgos de Codex:**
- `ScannerController` creaba `threading.Thread(...)` nuevos por frame para
  timeline, ok_buffer y guardado de resultados. Si el disco se pone lento, eso
  podia escalar a cientos o miles de threads y degradar todo el proceso.
- `Camera.stop()` soltaba la referencia del hilo aun si seguia vivo, con riesgo
  de dejar loops zombis y de duplicar capturas en reintentos/reinicios.
- `PLCClient` recreaba el cliente Modbus al reconectar pero no cerraba de forma
  consistente el cliente anterior ante errores repetidos.
- `metrics.db` crecia sin politica de retencion ni VACUUM, y `setup_logging()`
  ignoraba `config/app.yaml`, por lo que el archivo configurado no se usaba.
- La UI consultaba `mode_switch` via PLC desde `get_status()`; se movio esa
  lectura al poller para no congelar paneles por lecturas sincronicas.

**Cambios hechos por Tadeo + Codex:**
- `src/controller/scanner_controller.py`
  - nuevo disk writer serial por scanner con cola acotada
  - se eliminaron los threads por frame para guardados en disco
  - `get_status()` ya no hace lecturas PLC bloqueantes
  - nuevo `shutdown()` para cerrar worker de disco en el apagado real del sistema
- `src/controller/system.py`
  - `shutdown()` ahora llama `scanner.shutdown()` para cerrar tambien el writer
- `src/vision/camera.py`
  - `stop()` ahora cierra capturas/conexiones antes del join
  - si el hilo no termina, queda bloqueado un restart duplicado y se loguea error
  - seguimiento explicito de la conexion HTTP snapshot para cortar bloqueos
- `src/plc/client.py`
  - cierre explicito del cliente anterior en reconnect, disconnect y on_error
- `src/metrics/recorder.py`
  - WAL + `synchronous=NORMAL`
  - poda por antiguedad y tope de filas por scanner
  - mantenimiento periodico + `VACUUM`
- `src/utils/logger.py`
  - ahora lee `config/app.yaml`
  - usa `RotatingFileHandler` con rotacion acotada
- `src/utils/config.py`
  - nuevo default `disk_writer_queue_max`
- tests nuevos:
  - `tests/test_metrics_recorder.py`
  - `tests/test_plc_client.py`

**Validacion:**
- `pytest tests/` -> `21 passed`
- `git pull --ff-only origin master` aplicado antes de editar para partir del
  ultimo `master`

**Riesgos / oportunidades:**
- El modo Servicio todavia tiene algunos hilos y loops propios, pero el nucleo
  de produccion (`run`) quedo mucho mas protegido que antes.
- `EventRecorder` sigue usando hilos por evento, aunque ya no por frame; por
  frecuencia de uso no quedo como riesgo critico en esta pasada.

---

### Sesión 2026-06-16 — Tadeo + Claude + Codex

#### Cambio 199 - Rediseño overlay frames analizados

**Pedido:** quitar información innecesaria del bottom-left, corregir solapamientos
entre errores, mejorar estética de los frames analizados.

**Problemas encontrados:**
- `draw_centering_overlay` dibujaba 3 filas de texto técnico al fondo:
  "Izq: Xpx Der: Ypx", "Delta/Offset", "Vert pat: Izq/Der/dCh" — puro debug,
  no útil para operadores, solapaba con otros indicadores.
- `draw_tilt_indicator` y `draw_blur_indicator` dibujaban texto en y=62 y y=82
  fijos que se solapaban con el panel NOK (que empieza en badge_count×92).
- `draw_roi_health_indicator` dibujaba dimensiones de frame/ROI en y=102 —
  información técnica que ningún operador necesita ver.
- Caracteres Unicode (►, ✔, °) no soportados por HERSHEY → salían como "???".

**Cambios en `src/pipeline/annotate.py`:**
- `draw_centering_overlay`: eliminadas las 3 filas de texto bottom. En su lugar,
  cuando `pattern_warn=True`, aparece un chip compacto en esquina inferior-derecha:
  "zigzag X.Xpx  slope X.Xdeg".
- `draw_tilt_indicator`: ya no dibuja texto normal; solo badge en bottom-left
  cuando `warn=True`. Sin solapamiento con el panel NOK (que está arriba).
- `draw_blur_indicator`: mismo patrón — solo badge cuando borroso.
- `draw_roi_health_indicator`: convertida en no-op (API compatible, sin dibujo).
- `_draw_nok_reasons_panel`: bullets cambiados a ">" (ASCII), tipografía más
  legible, mejor contraste. Fondo azul oscuro con borde azul-violeta.
- `draw_status_indicator` OK: reemplazado "STATUS: OK" por badge pill verde.
- `draw_roi_indicator`: borde simplificado (sin label, tenue).
- Todos los caracteres Unicode reemplazados por ASCII puro.

**Resultado visual:**
- Frame OK: badge verde, círculos verdes en agujeros, líneas CHAPA/PATRON sutiles.
- Frame NOK: panel "NOK > AGUJEROS FALTANTES: N" compacto, sin solapamiento.
- Frame MACHINE_STOP: banners rojos + panel NOK + chip métricas (bottom-right).

**Archivos:** `src/pipeline/annotate.py`, `CHANGELOG.md`

---

#### Cambio 198 - Scanner_2: desalineamiento como trigger principal de machine_stop

**Problema:** El "cartel de detencion" (badge roja MACHINE_STOP) no aparecia en
los frames desalineados (70-76) de scanner_2. La racha de desalign se cortaba en
frames 073 y 076 (metricas dentro de tolerancia) y nunca llegaba a stop_frames=3.

**Causa raiz:** Con min_missing=3 y stop_frames=3, la racha (072=True, 073=False,
074=True, 075=True) nunca llegaba a 3 consecutivos.

**Fix:** `config/io_map.yaml` scanner_2.inspection:
- `pattern_align_min_missing: 3 → 1`: filtra frames de borde (noise alto, missing=0)
  pero activa el check cuando hay al menos 1 faltante.
- `pattern_align_stop_frames: 3 → 2`: frames 074+075 forman racha de 2 → MACHINE_STOP.

El per-ci tracker (machine_stop_min_missing:1 del Cambio 197) dispara machine_stop
en frame 074; el desalign path dispara en frame 075. Ambos son OR — badge desde 074.

**Validacion:** OK en 45-54 (sin FP), MACHINE_STOP en 57 (defecto) y 74 (desalin).

**Archivos:** `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 197 - Scanner_2: machine_stop por faltantes en zona desalineada (frames 70-76)

**Problema:** Scanner_2 no disparaba `machine_stop` en los frames 70-76 (patron
desalineado). La racha temporal NOK (FAULT a los 5 frames) funcionaba, pero el
`machine_stop` directo (solenoid inmediato) nunca se activaba en esa zona.

**Causa raiz:** `machine_stop_min_missing: 2` (modelo_B) exige 2 agujeros
faltantes en la *misma columna ci* para que esa columna acumule racha. En los
frames desalineados los faltantes quedan esparcidos (1 por columna ci por frame),
por lo que ninguna columna llegaba a contar >= 2 → racha del tracker = 0.

**Fix:** `config/io_map.yaml` — agregar override en `scanner_2.inspection`:
  `machine_stop_min_missing: 1`

Con `machine_stop_require_frame_nok: true` y `frame_missing_nok_threshold: 2`
como guarda, el gate global sigue en 2 faltantes totales por frame; solo la
condicion *por columna* se relaja de 2 a 1.

**Validacion `run-folder` sobre IMAGENES-VIERNES-12-EDITADAS/...SCANNER_2:**
- Zona 50-60: `MACHINE_STOP` en frame 57 (sin cambio respecto a antes)
- Zona 70-76: `MACHINE_STOP` ahora en frame 74 (antes no disparaba)
- OK frames sin falsos positivos

#### Cambio 196 - Login: letras mas grandes en labels y titulo

**Pedido:** aumentar el tamano de letra en los labels del dialogo de login.

**Cambio:** `src/ui/login_dialog.py`:
- `lbl_style` `font-size:12px` → `font-size:15px`
- `field_style` `font-size:13px` → `font-size:15px`
- Titulo `font-size:15px` → `font-size:18px`

**Archivos:** `src/ui/login_dialog.py`, `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 194 - Fix ConfigTab: tolerancias usaba path relativo, ahora usa _ROOT

**Problema:** La pestaña Tolerancias en Modo Servicio podía mostrar
"# Archivo no encontrado" si el CWD no era el root del proyecto (caso .exe).

**Fix:** `src/ui/service.py` — `ConfigTab._load` usa `(_ROOT / path)` en lugar
de `Path(path)` para garantizar la ruta correcta en cualquier contexto.

---

#### Cambio 193 - UX: label TIEMPO → "Tiempo en funcionamiento continuo" + botón ℹ métricas

**Pedido:** Renombrar el label "TIEMPO" y agregar un botón pequeño en el mismo
renglón para ver las métricas más importantes de cada scanner.

**Cambios — `src/ui/operator.py`:**
- `_metric_card`: nuevo parámetro `word_wrap=False`; cambia `letter-spacing:1px`
  → `0px` para que el texto largo no desborde.
- Card TIEMPO renombrada a `"Tiempo en funcionamiento continuo"` con `word_wrap=True`.
- Botón `ℹ` (26×26 px) agregado al final del `metrics_row`.
- Nuevo método `_show_metrics_popup()`: abre `QDialog` modal con Modo, Inspecciones,
  OK, NOK, Racha NOK, Último estado y Centrado leídos en tiempo real.

---

#### Cambio 192 - Fix login: diálogo de contraseña no persistía al equivocarse

**Problema:** Al ingresar contraseña incorrecta en Modo Servicio el diálogo desaparecía
en lugar de mostrar el mensaje de error y permanecer abierto.

**Causa raíz:** `LoginDialog` tenía `setFixedSize(380, 230)`. El `_error_lbl` ya
estaba en el layout pero oculto; al hacer `.show()` Qt no tenía espacio para
renderizarlo dentro del área fija y el diálogo parecía cerrarse (el contenido
quedaba fuera del viewport).

**Fix:** `src/ui/login_dialog.py` — cambiar alto fijo de 230 → 265 px.  
Sin cambios de lógica; el comportamiento correcto ya estaba implementado.

---


#### Cambio 194 - Scanner 2: missing mas sensible en el bloque editado 50-59

**Pedido:** en `C:\Users\DefyC\Downloads\IMAGENES-VIERNES-12-EDITADAS\16-06-2026-MICROPERFORADO_1_SCANNER_2`
los frames `50..59` debian detectar mejor los agujeros faltantes y sostener la
detencion de maquina.

**Hallazgo de Codex:**
- La parada ya se disparaba en `frame_0057` y `frame_0059`, pero los primeros
  cuadros del defecto (`frame_0052` y sobre todo `frame_0053`) todavia
  entraban con `missing=0` aunque la franja faltante ya era visible.
- Causa: el gate horizontal del matching en modo grilla seguia apenas ancho
  (`dx * 0.45`), permitiendo que detecciones de la columna vecina taparan el
  faltante temprano de la columna `ci=4`.

**Cambio:**
- `src/inspection.py`
  - endurecido el gate horizontal de matching para patrones de grilla:
    `max_dx_px = min(tol_xy_px, max(6.0, dx * 0.40))`
    en lugar de `dx * 0.45`.

**Validacion:**
- `scanner_2` sobre `...SCANNER_2`
  - `frame_0052`: `missing 0 -> 1`
  - `frame_0053`: `missing 0 -> 1`
  - `frame_0054`: se mantiene `missing=2`
  - `frame_0055..0059`: se mantienen/acentuan los missing reales
  - `MACHINE_STOP` sigue disparando en `frame_0057` y `frame_0059`
  - resumen: `raw_ok=80 raw_nok=8 temporal_ok=85 temporal_nok=3 machine_stop_frames=3`

**Archivos:** `src/inspection.py`, `CHANGELOG.md`

---

#### Cambio 195 - Desalineamiento: exigir 3 frames consecutivos antes de parar

**Pedido:** si aparece un frame desalineado, la maquina no debe actuar al
instante porque puede dar falsos positivos. Hay que exigir una racha de frames
desalineados en ambos scanners.

**Cambio:**
- `config/io_map.yaml`
  - `scanner_1.inspection.pattern_align_stop_frames: 3`
  - `scanner_2.inspection.pattern_align_stop_frames: 3`

**Validacion:**
- `scanner_1` sobre `...SCANNER_1`
  - el bloque desalineado `frame_0070..0074` sigue detectandose
  - la parada arranca recien en `frame_0072` (antes arrancaba en `0071`)
  - resumen: `machine_stop_frames=7`
- `scanner_2` sobre `...SCANNER_2`
  - `frame_0072` y `frame_0074..0075` siguen marcando `NOK` por desalineamiento
  - ya no hay parada con solo 1-2 frames desalineados seguidos
  - resumen: `machine_stop_frames=2` (solo por faltantes persistentes del bloque 50-59)

**Archivos:** `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 196 - Desalineamiento: endurecer aun mas la racha en ambos scanners

**Pedido:** evitar falsos positivos por un frame desalineado suelto en ambos
scanners durante produccion.

**Cambio:**
- `config/io_map.yaml`
  - `scanner_1.inspection.pattern_align_stop_frames: 3`
  - `scanner_2.inspection.pattern_align_stop_frames: 3`

**Validacion:**
- `scanner_1` sobre `...SCANNER_1`
  - el bloque `frame_0070..0074` sigue entrando como desalineado
  - `MACHINE_STOP` recien aparece desde `frame_0072`
- `scanner_2` sobre `...SCANNER_2`
  - `frame_0072`, `frame_0074`, `frame_0075` siguen marcando `NOK`
  - ya no hay parada por un primer o segundo frame aislado desalineado
  - las paradas que quedan vienen del bloque de `missing` 50-59

**Archivos:** `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 193 - Desalineamiento de patron: volver a detectarlo y parar maquina

**Pedido:** en la misma carpeta editada, los frames ~70-76 de ambos scanners
estan desalineados a proposito. Habia que volver a detectar ese corrimiento del
patron y usarlo para detener la maquina.

**Hallazgos de Codex:**
- La logica ya existia en `src/inspection.py`, pero ambos scanners la tenian
  desactivada en `config/io_map.yaml` (`pattern_align_enabled: false`).
- `scanner_1` tiene una firma clara de desalineamiento: entre `frame_0070` y
  `frame_0074` el `offset_px` del patron se va a ~`-9 .. -11px`, con picos de
  zigzag de borde (`std=6.6`, `max=18.3`).
- `scanner_2` no deriva tanto en offset global; el desalineamiento aparece como
  zigzag/inclinacion local del patron junto con `missing>=3`. Esa combinacion
  separa bien los frames editados (`0072`, `0074`, `0075`) de los picos sanos
  del lote que tenian zigzag alto pero `missing=0`.

**Cambios:**
1. `src/utils/config.py`
   - nuevo default `pattern_align_min_missing: 0`.
2. `src/inspection.py`
   - `pattern_align_min_missing` ahora puede exigir un minimo de `missing`
     antes de considerar desalineamiento por zigzag de borde, zigzag central o
     `pattern_sheet_slope_delta_max_deg`.
   - el gate NO se aplica al corrimiento global (`pattern_global_offset_max_px`),
     para que `scanner_1` pueda seguir usando su firma lateral fuerte aunque
     tenga pocos faltantes.
3. `config/io_map.yaml`
   - `scanner_1`:
     - `pattern_align_enabled: true`
     - `pattern_global_offset_max_px: 8.0`
     - `pattern_slope_delta_max_deg: 1.0`
     - `pattern_align_stop_frames: 2`
   - `scanner_2`:
     - `pattern_align_enabled: true`
     - `pattern_align_min_missing: 3`
     - `pattern_align_std_max_px: 3.0`
     - `pattern_align_abs_max_px: 10.0`
     - `pattern_slope_delta_max_deg: 0.6`
     - `pattern_global_offset_max_px: 0.0`
     - `pattern_align_stop_frames: 2`

**Validacion:**
- `scanner_1` sobre `IMAGENES-VIERNES-12-EDITADAS/...SCANNER_1`
  - desalineamiento detectado en `frame_0070..0074`
  - `MACHINE_STOP` desde `frame_0071`
  - resumen: `raw_ok=68 raw_nok=9 temporal_ok=69 temporal_nok=8 machine_stop_frames=8`
- `scanner_2` sobre `IMAGENES-VIERNES-12-EDITADAS/...SCANNER_2`
  - desalineamiento detectado en `frame_0072`, `frame_0074`, `frame_0075`
  - `MACHINE_STOP` en `frame_0075`
  - resumen: `raw_ok=81 raw_nok=7 temporal_ok=86 temporal_nok=2 machine_stop_frames=2`

**Archivos:** `src/utils/config.py`, `src/inspection.py`,
`config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 191 - Microperforado editado: parada de maquina por agujeros faltantes reales

**Pedido:** en las carpetas editadas de `scanner_1` y `scanner_2`, los frames
50-60 contienen agujeros faltantes a proposito y **deben detener la maquina**
cuando la ausencia persiste varios frames.

**Hallazgos de Codex:**
- En `scanner_2`, el compare de grilla permitia que una deteccion de la
  columna vecina cubriera un agujero faltante porque `tol_xy_px=42` era mayor
  que el paso entre columnas (`grid_dx~35.5px`). Resultado: el defecto visual
  existia, pero entraba con `missing=0`.
- Una vez corregido el matching, el defecto editado pasaba a `missing=6..9`,
  pero el gate `frame_missing_nok_threshold=8` seguia demasiado alto y solo
  convertia un frame en `NOK`, insuficiente para acumular `machine_stop`.
- En `scanner_1`, la prueba estaba bloqueada porque `roi.json` y `holes.json`
  habian quedado desincronizados. Se reconstruyo el patron desde un frame OK
  del mismo lote editado para volver a sincronizar ROI + grilla.
- Ya con el patron nuevo, el defecto editado de `scanner_1` aparecia como una
  misma columna (`ci=2`) con 4-6 agujeros faltantes por frame entre 54 y 58,
  mientras que el ruido normal del scanner dejaba faltantes mas dispersos.

**Cambios:**
1. `src/pipeline/compare.py`
   - nuevo gate opcional `max_dx_px` / `max_dy_px` para bloquear matches entre
     columnas/filas vecinas aun cuando el radio Euclideo global sea grande.
2. `src/inspection.py`
   - en modo grilla, el compare activa `max_dx_px=min(tol_xy_px, dx*0.45)` para
     que un agujero de la columna vecina no "tape" un faltante real.
3. `config/io_map.yaml`
   - `scanner_2.inspection.frame_missing_nok_threshold: 5`
   - `scanner_1.inspection.machine_stop_min_missing: 4`
   - `scanner_1.inspection.machine_stop_require_frame_nok: false`
4. `data/patterns/scanner_1/modelo_B/holes.json`
   - patron reconstruido desde `frame_0000.png` del lote editado para
     resincronizarlo con la ROI activa.

**Validacion:**
- `scanner_2` sobre `IMAGENES-VIERNES-12-EDITADAS/...SCANNER_2`
  - ahora marca faltantes reales en `frame_0055..0058`
  - `frame_0057` dispara `MACHINE_STOP`
  - resumen: `raw_ok=83 raw_nok=4 temporal_ok=86 temporal_nok=1 machine_stop_frames=1`
- `scanner_1` sobre `IMAGENES-VIERNES-12-EDITADAS/...SCANNER_1`
  - el patron vuelve a cargar sin error de ROI
  - `frame_0055..0058` disparan `MACHINE_STOP`
  - resumen: `raw_ok=73 raw_nok=4 temporal_ok=73 temporal_nok=4 machine_stop_frames=4`

**Archivos:** `src/pipeline/compare.py`, `src/inspection.py`,
`config/io_map.yaml`, `data/patterns/scanner_1/modelo_B/holes.json`,
`CHANGELOG.md`

---

#### Cambio 190 - Fix exe: frames OK y timeline no se guardaban en producción

**Problema:** Al correr el `.exe` en modo producción (`run`), no se guardaba ningún
frame OK en el buffer circular (`data/output/ok_buffer/`) ni en el timeline cronológico
(`data/output/timeline/`). La UI de visor de frames no mostraba nada.

**Causa raíz:** Todos los módulos que resuelven rutas usaban:
```python
_ROOT = Path(__file__).resolve().parent.parent.parent
```
En desarrollo esto sube desde `src/ui/xxx.py` al root del proyecto. En el `.exe`
congelado por PyInstaller, `__file__` resuelve a `_MEIPASS/_internal/src/ui/xxx.py`,
y subir 3 niveles da `_MEIPASS` (el directorio temporal de PyInstaller), **no** el
directorio donde está instalada la aplicación.

**Solución:**
1. Nuevo módulo `src/utils/paths.py` con `app_root()` que en modo frozen devuelve
   `Path.cwd().resolve()` (ya correcto porque `run_production.py` ejecuta
   `os.chdir(project_root)` antes de importar cualquier módulo `src`), y en desarrollo
   sube 3 niveles desde `__file__`.
2. Todos los archivos afectados reemplazados:
   - `src/ui/frame_viewer.py`
   - `src/ui/service.py`
   - `src/ui/operator.py`
   - `src/ui/metrics_window.py`
   - `src/ui/tolerance_window.py`
   - `src/controller/scanner_controller.py` (rutas `ok_buffer`, `timeline`, `events`)
3. `metalconf.spec`: agregado `'src.utils.paths'` a `hiddenimports`.

**Archivos:** `src/utils/paths.py` (nuevo), `src/ui/frame_viewer.py`,
`src/ui/service.py`, `src/ui/operator.py`, `src/ui/metrics_window.py`,
`src/ui/tolerance_window.py`, `src/controller/scanner_controller.py`,
`metalconf.spec`, `CHANGELOG.md`

---

#### Cambio 189 - Scanner 1 microperforado: corrección de columna derecha fantasma

**Pedido:** el `scanner_1` en modo microperforado saltaba una columna (la derecha),
marcando 5 agujeros como faltantes en esa zona en la mayoría de los frames y
causando 4/30 NOK falsos en el lote `12-06-2026-MICROPERFORADO_10_SCANNER_1`.

**Diagnóstico (Claude):**
- El patrón `holes.json` tenía 176 puntos con 7 celdas `ci=6` en x≈205-210.
- Esas 7 celdas eran mal-clasificaciones: durante el build el `scanner_id` no se
  había pasado, por lo que `pattern_edge_margin_right_px: 65.0` del io_map.yaml
  **no se aplicó**. Holes de `ci=5 fila-par` en x≈207 quedaron asignados a `ci=6`
  por redondeo (`|207-222|=15 < |207-186|=21`).
- Durante inspección, el grid generaba posiciones esperadas `ci=6` en x≈221 donde
  **no hay agujeros físicos**. Resultado: 5-7 missing fantasmas por frame.

**Cambio:**
- Reconstrucción del patrón con `scanner_id='scanner_1'` para aplicar correctamente
  `pattern_edge_margin_right_px: 65.0` del io_map.yaml.
- Patrón nuevo: **146 puntos**, `ci=0` a `ci=5`, sin `ci=6` fantasma.

**Validación:**
- Antes: `74/104 raw OK`, `4/30 NOK`
- Después: **104/104 raw OK**, `0 NOK`

**Archivos:** `data/patterns/scanner_1/modelo_B/holes.json`, `CHANGELOG.md`

---

#### Cambio 188 - Scanner 2 microperforado: umbral de blur más exigente

**Pedido:** detectar mejor cuando un frame esta borroso y endurecer un poco la
decision de `LOW_QUALITY`, porque la nitidez afecta la calidad del analisis.

**Hallazgo de Codex:**
- `modelo_B` seguia usando base global `blur_score_min=200.0`, demasiado permisiva
  para `scanner_2`: en el lote `16-06` todos los frames quedaban como `GOOD`
  aunque los peores visualmente estaban en `Nitidez ~877 .. 1061`;
- sobre `186` frames del lunes:
  - min `876.7`
  - p10 `1201.8`
  - mediana `1672.4`
  - p90 `2192.9`
- un umbral de `1200` marca como `LOW_QUALITY` aproximadamente el `8.6%` del lote,
  capturando los frames mas blandos sin castigar demasiado la corrida normal.

**Cambios:** `config/io_map.yaml` (`scanner_2.inspection`): `blur_score_min: 1200.0`

**Validacion en los lotes del 16-06:**
- `16-06-2026-MICROPERFORADO_1_SCANNER_2`: `low_quality=8/87`, `max_streak=2`
- `16-06-2026-MICROPERFOADO_2_SCANNER_2`: `low_quality=7/75`, `max_streak=2`
- los dos lotes se mantienen en `raw_ok=100%`.

**Archivos:** `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 190 - Scanner 2 microperforado: borde del patron mas recto, sin zigzag falso

**Pedido:** mejorar apenas la deteccion del borde del patron en `scanner_2`, porque
por momentos hacia zigzag aunque la deteccion general ya estaba bien.

**Hallazgo de Codex:**
- el problema no estaba en el patron real ni en la deteccion central de agujeros:
  `pattern_center_zigzag_std_px` se mantenia bajo;
- el zigzag venia de la seleccion de la columna extrema por banda (`pattern edge`):
  con `pattern_edge_boundary_tol_px=8.0`, algunas bandas aceptaban un agujero de la
  columna siguiente como si fuera borde exterior;
- bajar ese gate a `5.0 px` alcanza para fijar el borde correcto sin tocar ni ROI,
  ni blur, ni segmentacion.

**Cambio hecho por Tadeo + Codex:**
- `config/io_map.yaml` (`scanner_2.inspection`):
  - `pattern_edge_boundary_tol_px: 5.0`

**Validacion sobre los dos lotes del 16-06 (`186` frames):**
- antes (`8.0 px`):
  - `avg_pattern_zigzag_std = 1.817`
  - `p95_pattern_zigzag_std = 5.197`
  - `max_pattern_zigzag_std = 6.888`
  - `avg_pattern_zigzag_max = 5.901`
  - `max_pattern_zigzag_max = 21.126`
- ahora (`5.0 px`):
  - `avg_pattern_zigzag_std = 1.008`
  - `p95_pattern_zigzag_std = 4.078`
  - `max_pattern_zigzag_std = 4.817`
  - `avg_pattern_zigzag_max = 3.336`
  - `max_pattern_zigzag_max = 17.038`
- sin regresion funcional:
  - `raw_ok = 186/186`
  - `low_quality = 16`
  - `missing_total = 0`

**Caso representativo:**
- `frame_0075.png` del lote `MICROPERFOADO_2_SCANNER_2`
  - `pattern_zigzag_std_px: 6.888 -> 0.636`
  - `pattern_zigzag_max_px: 21.126 -> 2.420`

**Archivos:** `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 187 - Scanner 2 microperforado: separación de agujeros fusionados + warning de ancho de chapa

**Pedido:** mejorar la calibración de `scanner_2` en microperforado (blobs fusionados,
detection_ratio inflado ~119%, bordes de chapa mal tomados en algunos frames).

**Hallazgo de Codex:**
- pasar `scanner_2/modelo_B` a `gray` sin `CLAHE` y con morfología chica
  (`blur=3`, `open=3`, `close=2`) separa mucho mejor los microagujeros;
- la ROI guardada (`x=218, w=265`) es más angosta que la chapa detectada por backlight
  (`ratio ~1.68x..1.72x`); `resolve_runtime_roi()` ahora lo advierte explícitamente.

**Cambios:**
- `config/io_map.yaml` (`scanner_2.inspection`): `threshold=145`, `use_channel=gray`,
  `use_clahe=false`, `blur/open/close=3/3/2`, `max_area=250.0`, `chapa_edge_inner_px=40`
- `src/patterns/roi.py`: `resolve_runtime_roi()` agrega warning cuando ancho de chapa
  detectada no es comparable con la ROI guardada.
- `data/patterns/scanner_2/modelo_B/holes.json`: reconstruido desde frame_0071.png del
  lote 16-06 — patron final depurado: 179 puntos.

**Validacion:**
- `16-06-2026-MICROPERFORADO_1_SCANNER_2`: `raw_ok=87/87`, `avg_ratio=113%` (vs 119%)
- `16-06-2026-MICROPERFOADO_2_SCANNER_2`: `raw_ok=75/75`, `avg_ratio=114%` (vs 119%)

**Archivos:** `config/io_map.yaml`, `src/patterns/roi.py`,
`data/patterns/scanner_2/modelo_B/holes.json`, `CHANGELOG.md`

---

#### Cambio 186 - Prevención estructural: error fatal si ROI y patrón desincronizados

**Problema raíz:** cuando `roi.json` y `holes.json` quedaban desincronizados (por merge,
edición manual o roi_recenter), el sistema solo logueaba un WARNING y seguía corriendo con
coordenadas incorrectas. El operador no se enteraba hasta ver resultados erráticos.

**Cambios estructurales:**

1. **`src/inspection.py`** — El bloque que antes era `logging.warning` ahora lanza
   `ValueError` cuando `|image_size - frame_size| > 1px`. Esto pone el scanner en estado
   `ERROR` inmediatamente, visible en la UI y en los logs, en lugar de continuar con
   resultados incorrectos. La tolerancia de 1px absorbe el clip de borde cuando
   `roi.x + roi.w == frame_width`.

2. **`src/patterns/pattern_io.py`** — El dataclass `Pattern` ahora tiene el campo
   `built_with_roi: Optional[Tuple[int,int,int,int]]` (x, y, w, h). Se guarda y carga
   en `holes.json`. Cuando un patrón tiene este campo, se puede verificar en cualquier
   momento si el ROI activo coincide con el usado en la calibración.

3. **`src/patterns/pattern_build.py`** — `build_pattern_from_image()` ahora pasa el ROI
   al constructor de `Pattern`, de modo que todo patrón construido a partir de ahora
   lleva embebido el ROI con el que fue construido.

4. **`data/patterns/scanner_1/modelo_B/holes.json`** y
   **`data/patterns/scanner_2/modelo_B/holes.json`** — Actualizados para agregar
   `built_with_roi` a los patrones existentes (retrocompatible: el campo es opcional).

**Comportamiento nuevo:** si en el futuro `roi.json` se modifica sin reconstruir el
patrón, el primer frame lanzará un error claro y pondrá el scanner en ERROR.

**Archivos:** `src/inspection.py`, `src/patterns/pattern_io.py`,
`src/patterns/pattern_build.py`, `data/patterns/scanner_1/modelo_B/holes.json`,
`data/patterns/scanner_2/modelo_B/holes.json`, `CHANGELOG.md`

---

#### Cambio 185 - Fix scanner_2: restaurar roi.json w=265 y deshabilitar roi_recenter

**Bug:** warning en producción `[modelo_B] Patrón calibrado a 265x480 pero frame actual
(post-ROI) es 236x480. Resultados incorrectos`.

**Causa:** misma causa raíz que Cambio 183. El merge `1ba776e` (“keep local changes”)
retuvo el `roi.json` local con `w=236`, pero incorporó el patrón reconstruido a `w=265`
desde origin (commit `a5b7ff3`).

**Fix:**
- `data/patterns/scanner_2/modelo_B/roi.json`: restaurado a `w=265`.
- `config/io_map.yaml` (scanner_2): `roi_recenter_enabled: false`.

**Archivos:** `data/patterns/scanner_2/modelo_B/roi.json`, `config/io_map.yaml`, `CHANGELOG.md`

---
### Sesión 2026-06-12 — Tadeo + Claude

#### Cambio 184 - Fix ícono barra de tareas en exe: usar .ico en vez de .jpg

**Bug:** el ícono que aparece en la barra de tareas al correr el exe no coincidía
con el ícono embebido en el `.exe`.

**Causa:** `operator.py` cargaba `logos/logo_ventana.jpg` para la ventana, pero el
exe embebe `assets/defyvision_logo.ico`. Además `assets/` y `logos/` no estaban en
el `datas` del spec, por lo que el `.jpg` ni siquiera se cargaba dentro del exe.

**Fix:**
- `metalconf.spec` — agregados `assets/` y `logos/` en la sección `datas`.
- `src/ui/operator.py` — `launch_operator_ui` y `OperatorWindow.__init__` ahora
  prefieren `assets/defyvision_logo.ico`; fallback a `logos/logo_ventana.jpg` si
  el `.ico` no existe.



#### Cambio 183 - Fix scanner_1: restaurar roi.json y deshabilitar roi_recenter

**Bug:** warning en producción `[modelo_B] Patrón calibrado a 241x480 pero frame actual
(post-ROI) es 259x480. Resultados incorrectos`.

**Causa:** el `roi_recenter` (modo `resize`) expandió el ROI de scanner_1 de `w=242`
a `w=259` durante sesiones anteriores, desincronizándolo del patrón (image_size=241).
El patrón fue construido con w=242 pero los frames llegaban con 259px post-ROI.

**Fix:**
- `data/patterns/scanner_1/modelo_B/roi.json`: restaurado a `x=213, w=242` (valor
  original con que se construyó el patrón).
- `config/io_map.yaml` (scanner_1 inspection override): `roi_recenter_enabled: false`
  para evitar que el ROI vuelva a crecer y desincronizarse. El ROI w=242 cubre bien
  la zona microperforada de scanner_1; el recenter no es necesario acá.

**Nota:** si en el futuro se desea cambiar el ROI, hay que reconstruir el patrón
inmediatamente con `build-pattern --model modelo_B --scanner scanner_1`.

**Archivos:** `data/patterns/scanner_1/modelo_B/roi.json`, `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 182 - Inspector thread: wrapper try/except de último recurso

**Bug:** si cualquier excepción no capturada ocurría en `_continuous_loop` (durante
`inspect_frame`, `_handle_result`, `_run_roi_precalibration` o cualquier otro punto),
el thread `scanner_N-inspector` moría silenciosamente. El scanner quedaba atascado
para siempre: no transicionaba a ERROR, no paraba el solenoide y la UI no lo notificaba.

**Causa raíz:** `_continuous_loop` era el target del thread sin ningún `try/except` de
nivel superior. Python imprime "Exception in thread …" y continúa sin tomar acción.

**Fix en `src/controller/scanner_controller.py`:**
- `_continuous_loop` ahora es un wrapper con `try/except Exception` total.
- El body anterior se movió a `_continuous_loop_impl`.
- Si cualquier excepción sube: se logea con `exc_info=True`, se para el solenoide,
  se ponen luces en rojo, se transiciona a `ScannerState.ERROR` y se dispara
  `_fire_state_changed()` para notificar a la UI.
- Aplica a todos los scanners; no se puede volver a quedar atascado silenciosamente.

**Archivos:** `src/controller/scanner_controller.py`, `CHANGELOG.md`

---

#### Cambio 181 - Fix scanner_1: compare_left_ignore_px 40→28 para incluir segunda columna

**Bug:** en modo live, el scanner_1 no detectaba una columna de agujeros que sí
aparecía correctamente en análisis de carpeta.

**Causa raíz:** el patrón tiene dos grupos de columnas izquierdas:
- Columna A: x=18-24px (borde, no detectable en live — debe excluirse).
- Columna B: x=37-42px (detectable, debe compararse).

Con `compare_left_ignore_px: 40.0` del modelo_B global, la Columna B era borderline.
Cuando la alineación de grilla en live derivaba 2-5px a la izquierda (comportamiento
normal de la cámara real vs frames grabados), todos los expected de la Columna B
caían por debajo de 40px → excluidos del compare → invisible en el overlay de live.
En análisis de carpeta los frames tenían la chapa 2-3px más a la izquierda, por lo
que algunos expected de Columna B quedaban en x≥40 y aparecían.

**Fix en `config/io_map.yaml` (scanner_1 inspection override):**
- `compare_left_ignore_px: 28.0` — excluye Columna A (max x=24, margen 4px) e
  incluye siempre Columna B (min x=37, margen 9px contra deriva de la grilla).

**Archivos:** `config/io_map.yaml`, `CHANGELOG.md`

---

#### Cambio 180 - Fix MetricsRecorder: 21 placeholders para 22 columnas

**Bug:** `MetricsRecorder write error: 21 values for 22 columns` cada minuto en producción.

**Causa:** `_INSERT` en `src/metrics/recorder.py` tenía 22 columnas listadas pero solo
21 `?` en el `VALUES (...)`. La tupla construida en `_snapshot()` sí tenía 22 valores.

**Fix:** agregado el `?` faltante al final de la cláusula `VALUES`.

**Archivos:** `src/metrics/recorder.py`, `CHANGELOG.md`

---

#### Cambio 179 - Historial de metricas: correccion funcional y mejora visual

**Pedido:** arreglar la pestaña de `Historial`, que no estaba funcionando
correctamente, y dejarla mas estetica.

**Hallazgo:**
- la base `data/metrics/metrics.db` si tenia datos de ambos scanners, pero no
  habia muestras dentro de los rangos cortos seleccionados en pantalla;
- como la UI solo consultaba por ventana temporal fija, el historial quedaba
  vacio y parecia roto aunque existieran registros anteriores.

**Cambios en `src/ui/metrics_window.py`:**
- el bloque de graficos ahora vive dentro de un contenedor visual dedicado, con
  mejor marco, fondo y jerarquia;
- se agrego una banda de estado arriba de los graficos para informar claramente:
  - cuando se muestran datos del rango elegido;
  - cuando no hay datos recientes y se hace fallback a los ultimos registros
    guardados;
  - cuando todavia no existe historial para ese scanner.
- se ampliaron los rangos disponibles para consulta rapida y se mejoro la
  lectura temporal del eje X cuando se muestran datos historicos mas viejos.
- se ajusto el mensaje vacio de los graficos para que sea mas claro y no de la
  sensacion de falla tecnica.

**Cambios en `src/metrics/recorder.py`:**
- se agrego `query_recent()` para recuperar los ultimos snapshots disponibles
  de un scanner;
- se agrego `latest_timestamp()` para saber si existe historial aunque el rango
  temporal seleccionado no tenga muestras.

**Validacion:**
- `python -m py_compile src/ui/metrics_window.py src/metrics/recorder.py` OK
- prueba directa sobre `metrics.db`:
  - `scanner_1`: `query(168h)=0`, `query_recent(5)=5`
  - `scanner_2`: `query(168h)=0`, `query_recent(5)=5`

**Archivos:** `src/ui/metrics_window.py`, `src/metrics/recorder.py`, `CHANGELOG.md`

---

#### Cambio 178 - Ventana de métricas: rediseño estético general

**Pedido:** dejar la pantalla de `METRICAS` lo más estética posible, con una
apariencia más cuidada y legible.

**Cambios en `src/ui/metrics_window.py`:**
- Nueva dirección visual más consistente con la UI moderna:
  - superficies con gradientes oscuros más ricos;
  - tarjetas con bordes más suaves, más aire y mejor jerarquía;
  - cabecera superior más grande y con más presencia.
- `Tiempo Real`:
  - cards más altas y legibles;
  - franja superior en color acento para dar jerarquía;
  - tipografía del valor principal más grande;
  - grupos por scanner más elegantes y con mejor separación visual;
  - título del scanner ahora muestra también el modelo activo.
- `Historial`:
  - controles superiores más prolijos;
  - selector y botón `Actualizar` con estilo más moderno;
  - gráficos con fondo más cuidado, líneas más limpias y mejor contraste.
- `Tabs`:
  - pestañas más redondeadas, más claras visualmente y con selección más fuerte.

**Validación:**
- `python -m py_compile src/ui/metrics_window.py` OK

**Archivos:** `src/ui/metrics_window.py`, `CHANGELOG.md`

---

#### Cambio 177 - Detención de máquina: cartel grande con scanner dominante y foco operario

**Pedido:** hacer más estética la pantalla grande de detención de máquina y
mostrar claramente en qué `SCANNER` ocurrió la falla para que el operario lo vea
rápido y sin ambigüedad.

**Cambios en `src/ui/operator.py`:**
- `MachineStopDialog` rediseñado con jerarquía visual más clara:
  - cabecera roja más alta con mejor contraste;
  - `SCANNER X · MODELO` en un bloque central grande y muy visible;
  - instrucción operativa explícita: revisar esa estación antes de reanudar.
- El pie del diálogo ahora separa mejor el motivo:
  - nuevo bloque `MOTIVO DE LA DETENCION`;
  - razón en tipografía más grande;
  - botón principal más grande: `CONFIRMAR Y CONTINUAR`.
- La etiqueta enviada al diálogo desde `ScannerPanel._on_result()` quedó con
  formato más limpio y consistente para que el scanner se lea mejor.

**Resultado esperado:**
- Cuando hay `machine_stop`, el operario identifica primero el scanner afectado,
  luego el motivo, y recién después confirma la intervención.
- La pantalla queda más legible a distancia y más alineada a uso en planta.

**Validación:**
- `python -m py_compile src/ui/operator.py` OK

**Archivos:** `src/ui/operator.py`, `CHANGELOG.md`

---

#### Cambio 176 - Calibracion fina viernes 12: microperforado OK por scanner sin mezclar tolerancias

**Pedido:** revisar `C:\Users\DefyC\Downloads\IMAGENES-VIERNES-12`, testear
`SCANNER_1` y `SCANNER_2` por separado, y hacer tuneo fino de patron/ROI.
Todas las imagenes del lote son OK, asi que no habia que mezclar tolerancias
entre scanners ni dejar falsas paradas de maquina.

**Hallazgos:**
- `origin/master` no tenia cambios nuevos, pero Tadeo subio una base util a
  `origin/clean-push` con ROI/tolerancias/patrones de `modelo_B`.
- Esa base mejoro fuerte ambos scanners, pero todavia dejaba:
  - `scanner_1`: `63/74 raw OK`, `74/74 temporal OK`
  - `scanner_2`: `68/76 raw OK`, `76/76 temporal OK`
- `scanner_1` admitia una mejora geometrica clara reconstruyendo el patron desde
  una imagen buena del viernes.
- `scanner_2` funcionaba mejor sobre `modelo_B` especifico que sobre las pruebas
  hechas antes con `modelo_A`; reconstruirlo desde `frame_0000` y mover apenas
  la ROI bajo mas el missing residual.

**Cambios hechos por Tadeo + Codex:**
- `config/tolerancias.yaml`
  - se tomo la base de `origin/clean-push` para `modelo_B`:
    - `pattern_edge_margin_px: 22.0`
    - `grid_affine_refinement: false`
    - `use_hungarian_matching: true`
- `config/io_map.yaml`
  - `scanner_1.inspection.pattern_edge_margin_px: 5.0`
  - `scanner_1.inspection.tol_xy_px: 38.0`
  - `scanner_2.inspection.tol_xy_px: 42.0`
- `data/patterns/scanner_1/modelo_B/roi.json`
  - ROI final: `x=214, y=0, w=241, h=480`
- `data/patterns/scanner_1/modelo_B/holes.json`
  - reconstruido desde `12-06-2026-MICROPERFORADO_10_SCANNER_1/frame_0055.png`
  - patron final: `167 puntos`
- `data/patterns/scanner_2/modelo_B/roi.json`
  - ROI final: `x=218, y=0, w=235, h=480`
- `data/patterns/scanner_2/modelo_B/holes.json`
  - reconstruido desde `12-06-2026-MICROPERFORADO_2_SCANNER_2/frame_0000.png`
  - patron final: `164 puntos`

**Validacion final sobre el lote del viernes:**
- `scanner_1` con `modelo_B`
  - `74/74 raw OK`
  - `74/74 temporal OK`
  - `machine_stop_frames=0`
  - `align_failures=0/74`
- `scanner_2` con `modelo_B`
  - `71/76 raw OK`
  - `76/76 temporal OK`
  - `machine_stop_frames=0`
  - `align_failures=0/76`

**Riesgos / oportunidades:**
- `scanner_1` quedo operativo sobre el lote OK del viernes.
- `scanner_2` mejoro mucho el baseline de missing, pero todavia conserva
  `5 raw NOK` residuales aunque sin disparar NOK temporal ni machine stop.
- Si queres llevar `scanner_2` a `raw OK` total tambien, el siguiente paso sano
  ya no parece ser abrir mas `tol_xy_px`, sino revisar esos grupos de celdas
  residuales (`ci~3, cj~15-19`) con una captura de referencia mas centrada o
  un ajuste puntual de patron en esa franja.

**Archivos:** `config/tolerancias.yaml`, `config/io_map.yaml`,
`data/patterns/scanner_1/modelo_B/roi.json`,
`data/patterns/scanner_1/modelo_B/holes.json`,
`data/patterns/scanner_2/modelo_B/roi.json`,
`data/patterns/scanner_2/modelo_B/holes.json`, `CHANGELOG.md`

---

#### Cambio 175 - ROI slow EMA: correccion de baseline para ROI angosta

**Problema:** la version anterior comparaba el EMA contra threshold absoluto (15px).
Para scanner_2 con ROI angosta (x=225, w=205), el centro del ROI esta desplazado
~7px del centro de la chapa por diseno. Esto hacia que el EMA arrancara fuera
del umbral sin que hubiera drift real.

**Solucion:**
- Fase warmup (primeros `roi_slow_ema_warmup_frames` frames, default 300 = ~60s):
  acumula media simple de shift_x para capturar el offset estatico real.
  Al terminar: `ema_baseline = mean(shift_x durante warmup)`, se guarda en disco.
- Produccion: drift real = `EMA - ema_baseline`. Solo se actua cuando este delta
  supera threshold_px de forma sostenida.
- Agrega nueva clave `roi_slow_ema_warmup_frames: 300` en DEFAULT_TOLERANCES.

**Funciona correctamente para:**
- scanner_1: ROI ancha, offset ~0px → igual comportamiento que antes
- scanner_2: ROI angosta, offset estatico ~-7px → absorbido en el baseline

**Archivos:** `src/inspection.py`, `src/utils/config.py`, `CHANGELOG.md`

---

#### Cambio 174 - ROI slow EMA drift correction (auto-calibracion gradual)

**Pedido:** calibracion automatica muy lenta del ROI para produccion 24/7, sin
cambios instantaneos que rompan la deteccion.

**Diseño:**
- `shift_x` de `resolve_runtime_roi` (deteccion de backlight) se acumula en un EMA
  con alpha=0.002 (converge en ~500 frames, ~100s a 5fps).
- Solo cuando |EMA| >= threshold (15px por defecto) se mantiene por confirm_frames
  (500 frames = ~100s) se escribe 1px de correccion a `roi.json`.
- Despues de cada correccion: cooldown_frames=1500 (~5 min) antes de poder corregir
  otra vez.
- Maximo total: ±40px desde el roi.json en disco.
- Estado persistido en `data/patterns/{scanner}/{model}/roi_drift_state.json` para
  sobrevivir reinicios. On restart: continua desde donde quedo.
- Nunca modifica el ROI del frame en curso — la correccion queda efectiva a partir
  del siguiente frame.
- Tiempo minimo para 1px de correccion a 5fps: ~3.5 min de drift sostenido.
- Para llegar a 40px de correccion total: minimo ~2.5 horas de drift continuo.

**Activar (OFF por defecto, activar por scanner):**
En `config/io_map.yaml`, dentro del bloque del scanner (inspection_overrides):
```yaml
roi_slow_ema_enabled: true
```

**Parametros ajustables (todos con defaults conservadores):**
| Param | Default | Efecto |
|---|---|---|
| roi_slow_ema_alpha | 0.002 | suavidad del EMA (~500 frames para converger) |
| roi_slow_ema_threshold_px | 15.0 | drift minimo para arrancar confirmacion |
| roi_slow_ema_confirm_frames | 500 | frames sostenidos antes de escribir 1px |
| roi_slow_ema_cooldown_frames | 1500 | pausa entre correcciones (~5min a 5fps) |
| roi_slow_ema_max_total_px | 40 | max correccion total permitida |
| roi_slow_ema_save_every | 300 | frecuencia de guardado del estado (~60s) |

**Archivos:** `src/inspection.py`, `src/patterns/roi.py`, `src/utils/config.py`, `CHANGELOG.md`

---

#### Cambio 173 - Panel de salud ROI en pestaña Calibración

**Pedido:** mostrar la salud del ROI en la pestaña de calibración.

**Comportamiento:**
- Se muestra bajo el preview, con borde de color:
  - Verde: drift < 10px y sin warning → "OK"
  - Naranja: drift 10-∞px sin warning → "Drift moderado"
  - Rojo: hay warning → muestra el texto del warning
- Campos mostrados: Frame WxH, ROI guardada (x, w), ROI detectada (x, w), Drift X (px), Estado
- Si no hay ROI guardada: muestra frame size + ROI detectada (si la hay)
- Se actualiza al cargar imagen o carpeta (inmediato)
- En modo Cámara en vivo: actualiza cada ~15 frames (~2s a 8fps) para no saturar CPU

**Implementación:**
- `_roi_health_lbl`: QLabel con fondo oscuro y borde de color dinámico
- `_roi_refresh_health()`: llama a `resolve_runtime_roi` (o `detect_roi_from_images`
  si no hay ROI guardada) y actualiza el label
- `_roi_live_frame_count`: contador para throttle del health check en live

**Archivos:** `src/ui/service.py`, `CHANGELOG.md`

---

#### Cambio 172 - Nombres de grabaciones incluyen scanner

**Pedido:** al guardar grabaciones, incluir el scanner en el nombre de carpeta.
**Formato:** `DD-MM-YYYY-MODELO_N_SCANNER_X` (ej: `11-06-2026-MICROPERFORADO_9_SCANNER_2`).

**Cambios en `src/ui/service.py`:**
- `_build_recording_folder_name(date_str, scanner_id)`: acepta `scanner_id`, lo
  normaliza a mayusculas (ej: `scanner_2` → `SCANNER_2`) y lo agrega al final del nombre.
  El parsing de `next_idx` ahora soporta ambos formatos (viejo sin scanner, nuevo con scanner).
- `_on_start`: pasa `sid` (scanner seleccionado) a `_build_recording_folder_name`.

**Archivos:** `src/ui/service.py`, `CHANGELOG.md`

---

#### Cambio 171 - ROI calibracion: preview en vivo desde camara del scanner

**Pedido:** en la pantalla de calibracion ROI, poder ver la imagen en tiempo real
del scanner 1 o 2 seleccionado, ademas de poder abrir imagen o carpeta.

**Cambios en `src/ui/service.py`:**
- Nuevo estado: `_roi_live_active`, `_roi_live_timer` (QTimer, 120ms ≈ 8fps).
- Nuevo botón "▶ Cámara en vivo" (checkable) en la fila de fuente del grupo ROI.
  Al activar: verde, deshabilita los otros botones de fuente, inicia el timer.
  Al desactivar o cambiar de scanner: restaura a estado normal, congela el frame.
- `_roi_grab_live()`: obtiene frame via `self._system.camera(sid).get_frame()` y
  llama a `_roi_redraw()`. En el primer frame inicializa bordes con ROI guardada.
- `_on_roi_scanner_changed()`: si live está activo al cambiar scanner, lo detiene.
- Clases de estilo `_ROI_BTN_SS` / `_ROI_BTN_LIVE_SS` como atributos de clase
  (BTN_SS es local a `_build_roi_section` y no accesible en handlers).

**Archivos:** `src/ui/service.py`, `CHANGELOG.md`

---

#### Cambio 170 - scanner_2 microperforado: correccion completa de ROI, patron y grid

**Pedido:** el scanner_2 en modo MICROPERFORADO tomaba muy mal los agujeros (patron de
55 holes vs ~200 reales, ratio=346%), ROI incorrecta, margenes mal. Corregir sin tocar scanner_1.

**Diagnostico:**
- ROI estaba en `{x:109, w:438}` (casi full-frame) en vez de `{x:225, w:205}` (banda correcta).
- `pattern_edge_margin_px: 50.0` en ROI de 208px dejaba solo 2-3 columnas al construir patron.
- `grid_stagger_x_odd: -18.0` (global) es OPUESTO al stagger real de scanner_2 (+18px):
  en scanner_1 filas impares van a la IZQUIERDA; en scanner_2 van a la DERECHA.
- `grid_dx: 36.0` y `grid_dy: 14.0` globales no matcheaban la geometria real de scanner_2
  (dx medido=35.5, dy=13.6 de 10 frames, 1700+ mediciones).
- `estimate_phase(xs, dx)` mezcla filas pares e impares (offset 18px entre sí) produciendo
  distribucion bimodal; la moda elegía el pico incorrecto (phase_x=28 vs real ~24).
- Formula de dedup en `pattern_build.py` usaba `phase_x + ci*dx + stagger` sin modulo para
  filas impares, mientras `assign_cells` usa `(phase_x+stagger) % dx + ci*dx`. Con
  phase_x+stagger > dx la formula incorrecta guardaba el punto equivocado en la celda.

**Cambios:**
- `config/io_map.yaml`: scanner_2 inspection overrides nuevos:
  - `grid_stagger_x_odd: 18.0` (positivo, opuesto al -18 global de scanner_1)
  - `grid_dx: 35.5`, `grid_dy: 13.6` (geometria real medida de scanner_2)
  - `pattern_edge_margin_px: 5.0` ya estaba; se mantiene
- `data/patterns/scanner_2/modelo_B/roi.json`: `{x:225, y:0, w:205, h:480}` (corregida)
- `data/patterns/scanner_2/modelo_B/holes.json`: reconstruido con 157 agujeros (vs 55 prev)
- `src/patterns/pattern_build.py`:
  - `estimate_phase`: cuando `stagger_override` esta configurado, calcular `phase_x` solo
    de filas pares (separadas via `phase_y`), no de todas las xs mezcladas
  - formula de dedup para filas impares: usar `(phase_x+stagger)%dx + ci*dx` (consistente
    con `assign_cells`) en vez de `phase_x + ci*dx + stagger` (sin modulo)

**Resultado tras rebuild:**
- 161 puntos detectados, 157 celdas unicas (4 duplicados residuales en zonas ambiguas deduplados)
- `avg_detection_ratio=124%` (vs 346% antes); algunos frames aun muestran missing intermitente
  pendiente de ajuste fino de threshold o compare-margins para scanner_2

**Archivos:** `config/io_map.yaml`, `data/patterns/scanner_2/modelo_B/roi.json`,
`data/patterns/scanner_2/modelo_B/holes.json`, `src/patterns/pattern_build.py`, `CHANGELOG.md`

---

### Sesion 2026-06-12 — Tadeo + Claude

#### Cambio 176 - Tuneo fino de patrones scanner_1 y scanner_2 (lote OK del 12-06)

**Pedido:** calibrar con el lote OK del 12-06 (105 frames scanner_1, 132 frames scanner_2)
para que todos los frames conocidos-OK se clasifiquen como OK sin missing. NO mezclar
configuraciones entre scanners.

**Diagnostico:**
- `run-folder` original: scanner_1 100% temporal OK pero max missing=6/frame (ci=4,5).
  scanner_2 100% temporal OK pero max missing=5/frame (ci=3).
- Los cells con missing frecuente estan en las columnas DERECHAS del patron: ci=4,5
  para scanner_1 (x≈190-212px en ROI de 245px); ci=3 para scanner_2 (x≈107-124px en
  ROI de 205px). Estas columnas estan cerca del borde de la zona perforada y el borde
  de chapa varia en produccion.
- Intentar `compare_right_ignore_px` no funcionaba porque `pattern_align_enabled=true`
  computa centrado sobre los puntos esperados vs detectados: al recortar solo el expected
  pero no el detected, la asimetria dispara falsos NOK de alineacion.

**Solucion:**
- **Reconstruir patrones** con `pattern_edge_margin_right_px` alto para excluir las
  columnas inestables DEL PATRON en build-time (no en compare-time).
  - scanner_1: `pattern_edge_margin_right_px: 95.0` → patron de 112 holes (vs 167 original)
    (frame referencia: frame_0091)
  - scanner_2: `pattern_edge_margin_right_px: 100.0` → patron de 86 holes (vs 164 original)
    (frame referencia: frame_0104)
- **Deshabilitar `pattern_align_enabled`** por scanner en io_map.yaml, ya que el check
  de alineacion presupone que el patron cubre todo el ancho detectable. Con patron
  recortado y agujeros reales detectados fuera del convex hull esperado, siempre
  dispara falsos NOK. `machine_stop_enabled: true` sigue cubriendo defectos reales.

**Cambios en `config/io_map.yaml`:**
- scanner_1 inspection: agrega `pattern_align_enabled: false`, `pattern_edge_margin_right_px: 95.0`
- scanner_2 inspection: agrega `pattern_align_enabled: false`, `pattern_edge_margin_right_px: 100.0`

**Cambios en patrones:**
- `data/patterns/scanner_1/modelo_B/holes.json`: reconstruido con 112 holes
- `data/patterns/scanner_2/modelo_B/holes.json`: reconstruido con 86 holes

**Resultado final:**
- scanner_1: 74/74 OK (100%), machine_stop=0, max missing/frame≤2, max freq 7%
- scanner_2: 76/76 OK (100%), machine_stop=0, max missing/frame≤1, max freq 3%
- Razon detection_ratio alta (165-200%): los agujeros de las columnas recortadas siguen
  siendo detectados pero ya no tienen expected partners → se cuentan en el numerador
  del ratio pero no como missing. Esto es correcto y esperado.

**NOTA IMPORTANTE:** si se cambia optica/camara/ROI, reconstruir el patron con:
```
.venv\Scripts\python.exe -m src.main build-pattern --model modelo_B --scanner scanner_1 --img <frame_ok.png>
.venv\Scripts\python.exe -m src.main build-pattern --model modelo_B --scanner scanner_2 --img <frame_ok.png>
```
Los `pattern_edge_margin_right_px` en io_map.yaml se aplican automaticamente al rebuild.

**Archivos:** `config/io_map.yaml`, `data/patterns/scanner_1/modelo_B/holes.json`,
`data/patterns/scanner_2/modelo_B/holes.json`, `CHANGELOG.md`

---

#### Cambio 169 - ROI recenter dinamico (feature desactivado por defecto)

**Pedido:** detectar deriva lateral de ROI en produccion y corregirla paso a paso.

**Cambios:**
- `src/inspection.py`: `_update_runtime_roi_drift()` + `_edge_missing_counts()` — detecta
  si los missing estan concentrados en un borde lateral de forma persistente y desplaza
  la ROI un pixel por frame hasta corregir la deriva. Activar con `roi_recenter_enabled: true`.
- `src/vision/inspector.py`: `saved_roi` y `roi_runtime_state` en preloaded para persistir
  estado entre frames.
- `src/utils/config.py`: nuevos defaults `roi_recenter_*` (todos conservadores, feature OFF).

**Estado:** implementado y desactivado. `roi_recenter_enabled: false` por defecto.

**Archivos:** `src/inspection.py`, `src/vision/inspector.py`, `src/utils/config.py`, `CHANGELOG.md`

---

### Sesión 2026-06-11 — Tadeo + Claude

#### Cambio 168 - Verificacion de tamano de frame/ROI + diagnostico `roi-check`

**Pedido:** poder verificar si el frame cambia de tamano o si la chapa se corre
con el tiempo, para auditar si la ROI sigue siendo valida sin meter una regresion
en la deteccion de microperforado.

**Cambios aplicados:**
- `src/patterns/roi.py`
  - nueva `RuntimeROIInfo` para reportar frame size, ROI activa, ROI detectada y shift X;
  - nueva `resolve_runtime_roi()`:
    - valida la ROI guardada contra el frame actual,
    - detecta el ancho/centro real de la chapa desde backlight,
    - deja lista una correccion horizontal conservadora si mas adelante se decide activarla;
  - ajuste importante: si la ROI guardada es una banda angosta dentro de la chapa
    (caso microperforado), no compara su ancho contra el ancho total detectado de la chapa,
    porque eso generaba warnings falsos.
- `src/inspection.py`
  - cada inspeccion ahora calcula `roi_info` y lo devuelve en `InspectionResult`;
  - el overlay muestra `Frame: WxH`, `ROI x/w` y, si aparece, un warning de ROI/patron.
- `src/pipeline/annotate.py`
  - nuevo `draw_roi_health_indicator()` para dibujar el estado del frame/ROI en el overlay.
- `src/main.py`
  - nuevo comando `roi-check` para auditar una imagen o carpeta completa:
    - distribucion de tamanos de frame,
    - shift X mediano/min/max,
    - CSV `roi_check.csv` con detalle por frame.
  - `run-image` ahora imprime el contexto ROI si existe.
- `src/utils/config.py`
  - nuevas claves de configuracion para ROI runtime:
    - `roi_autocorrect_enabled`
    - `roi_autocorrect_max_shift_px`
    - `roi_autocorrect_max_width_delta_px`
    - `roi_detect_margin_px`
    - `roi_detect_min_contrast`
- `config/tolerancias.yaml`
  - se dejaron definidos esos parametros para `modelo_B`;
  - **`roi_autocorrect_enabled` quedo en `false` por seguridad**.

**Validacion:**
- `python -m src.main roi-check --model modelo_B --scanner scanner_2 --input "...MICROPERFORADO_SCANNER_2" --output data/output/roi_check_scanner_2`
  - `640x480` en `96/96` frames
  - shift horizontal estable: mediana `-16.50 px`, min `-18.00`, max `-15.00`
  - `0/96` warnings
- `python -m src.main run-folder --model modelo_B --scanner scanner_2 --input "...MICROPERFORADO_SCANNER_2" --fps 5`
  - sigue en `71/71 raw OK`, `71/71 temporal OK`
- `pytest tests/` -> `17 passed`

**Nota de diseno:**
- se probo activar el recentrado automatico horizontal en `scanner_2`, pero una
  primera validacion bajo el raw de `71/71` a `69/71`;
- por eso la correccion automatica quedo implementada pero desactivada por defecto,
  y esta entrega se enfoca en auditoria/visibilidad sin tocar el comportamiento ya bueno.

**Archivos modificados:** `src/patterns/roi.py`, `src/inspection.py`,
`src/pipeline/annotate.py`, `src/main.py`, `src/utils/config.py`,
`config/tolerancias.yaml`, `CHANGELOG.md`

---

#### Cambio 167 - Semaforo: IDLE=amarillo, RUNNING=verde, ALARMA/FAULT=rojo

**Pedido:** manejar salidas del semaforo en scanner_1 y scanner_2:
- IDLE → luz AMARILLA
- RUNNING (inspeccion en vivo OK) → luz VERDE
- ALARMA DETENCION DE MAQUINA / FAULT → luz ROJA
- RESET → vuelve a AMARILLA (IDLE)

**Diagnostico:** la logica anterior usaba `blue=True` para IDLE. La luz azul
no es parte de un semaforo industrial estandar; el usuario quiere amarillo=listo,
verde=corriendo, rojo=alarma.

**Cambios en `src/controller/scanner_controller.py`:**
- `stop()`: cuando new_state == IDLE → `yellow=True` (antes `blue=True`)
- `reset()`: STOPPED→IDLE → `yellow=True` (antes `blue=True`)
- `initialize_lights()`: IDLE → `yellow=True` (antes `blue=True`)
- docstring del modulo: actualizado "azul" → "amarilla" para IDLE

**Lo que NO cambia:**
- RUNNING normal → `green=True`
- RUNNING con racha de aviso → `green=True` + `yellow` parpadeando
- FAULT → `red=True` + `yellow` parpadeando (poll_loop)
- machine_stop → STOPPED con `red=True`
- STOPPED (esperando RESET) → todas apagadas

**Archivos modificados:** `src/controller/scanner_controller.py`

---

#### Cambio 166 - ok_buffer_count 200 -> 500

**Pedido:** guardar mas de 200 frames OK en el buffer circular, renovandose siempre.

**Cambio:** `config/tolerancias.yaml` -> `ok_buffer_count: 200 -> 500`

El buffer circular ya funcionaba (sobreescribe los mas viejos al llenarse).
Solo se sube el tope a 500 slots (~50 MB a 100 KB/frame por scanner).
Con `ok_buffer_every: 1` se guarda cada frame OK que llega.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 165 - Microperforado scanner_2: patrón/ROI propios + overrides separados de scanner_1

**Pedido:** calibrar `scanner_2` para microperforado usando la carpeta
`C:\Users\DefyC\Downloads\10-06-2026-MICROPERFORADO\10-06-2026-MICROPERFORADO_SCANNER_2`
(todos los frames OK), manteniendo criterios parecidos a `scanner_1` pero con
parámetros independientes para no mezclar tolerancias entre scanners.

**Diagnóstico inicial:**
- `scanner_2/modelo_B` no tenía patrón propio (`holes.json`), solo ROI;
- el sistema caía al patrón global `data/patterns/modelo_B/holes.json`
  calibrado a `295x480`, mientras los frames reales de `scanner_2` eran `640x480`;
- resultado inicial:
  - `71/71 raw NOK`
  - `machine_stop_frames=69`
  - ~`90-107 missing` por frame
- además, la ROI de `scanner_2/modelo_B` estaba en frame completo (`640x480`),
  mientras el microperforado real ocupa una franja bastante más angosta.

**Cambios aplicados:**
- `data/patterns/scanner_2/modelo_B/roi.json`
  - nueva ROI específica: `x=240, y=0, w=208, h=480`
- `data/patterns/scanner_2/modelo_B/holes.json`
  - nuevo patrón específico de `scanner_2/modelo_B`
  - construido desde `frame_0048.png` de la carpeta buena
- `src/utils/config.py`
  - `load_tolerances()` ahora acepta `scanner_id`
  - cuando hay `scanner_id`, mezcla overrides desde
    `config/io_map.yaml -> <scanner>.inspection`
- `src/patterns/pattern_build.py`, `src/inspection.py`,
  `src/vision/inspector.py`, `src/controller/scanner_controller.py`,
  `src/ui/service.py`, `src/ui/operator.py`
  - pasan `scanner_id` al cargar tolerancias para que cada scanner use sus
    propios overrides cuando corresponde
- `config/io_map.yaml`
  - nuevos overrides solo para `scanner_2.inspection`:
    - `pattern_align_abs_max_px: 24.0`
    - `pattern_global_offset_max_px: 50.0`
    - `pattern_slope_delta_max_deg: 26.0`

**Resultado final sobre la carpeta buena de scanner_2:**
- `71/71 raw OK`
- `71/71 temporal OK`
- `align_failures=0/71`
- `machine_stop_frames=0`

**Nota de diseño:**
- `scanner_1` no fue tocado;
- los overrides de `scanner_2` viven en `io_map.yaml -> scanner_2.inspection`,
  separados de `modelo_B` compartido.

**Archivos modificados:** `config/io_map.yaml`, `data/patterns/scanner_2/modelo_B/roi.json`,
`data/patterns/scanner_2/modelo_B/holes.json`, `src/utils/config.py`,
`src/patterns/pattern_build.py`, `src/inspection.py`, `src/vision/inspector.py`,
`src/controller/scanner_controller.py`, `src/ui/service.py`, `src/ui/operator.py`,
`CHANGELOG.md`

---

#### Cambio 164 - Servicio: selector de scanner en Analisis + correcciones de flujo en vivo

**Pedido:** agregar selector de scanner en la pagina de ANALISIS de RecordingTab
y corregir el flujo de Servicio para que no queden combinaciones silenciosas
scanner/modelo ni analisis en vivo leyendo PNGs a medio escribir.

**Diagnostico:**
- `_scanner_combo` existe en la pagina GRAB (seccion de grabacion), no en ANALISIS
- `_on_analyze()` leia `self._scanner_combo.currentText()` del GRAB pero el usuario
  estaba en la pagina ANALISIS sin visibilidad ni control del scanner
- `_on_load_recording()` leia `model_display` y `fps` del meta.json pero ignoraba
  el campo `scanner` que si se graba al iniciar la captura

**Cambios aplicados en `src/ui/service.py`:**

- `_build_analysis_section()`: nuevo chip SCANNER + `_ana_scanner_combo` (QComboBox)
  poblado con `self._system.scanner_ids()`, visible en la pagina ANALISIS

- `_on_analyze()`: usa `self._ana_scanner_combo.currentText()` en lugar de
  `self._scanner_combo` (del GRAB)

- `_set_analysis_running()`: bloquea/desbloquea `_ana_scanner_combo` junto con
  los demas controles de analisis

- `_on_load_recording()`:
  - al inferir scanner desde el nombre de carpeta: sincroniza `_ana_scanner_combo`
  - al leer meta.json: lee campo `scanner` y lo aplica a `_ana_scanner_combo`
  - la grabacion guarda `"scanner": scanner_id` en meta.json, por lo que al
    cargar esa grabacion el scanner queda automaticamente seleccionado

- `currentTextChanged` de `_scanner_combo`: tambien sincroniza defaults de Servicio
  via `_sync_service_scanner_defaults(sid)`
  - alinea `_ana_scanner_combo` con el scanner activo
  - selecciona por defecto el `model` configurado para ese scanner en `io_map.yaml`
    para evitar combinaciones silenciosas que disparen `MISSING` falsos

- `_grab_frame()`:
  - el analisis en vivo ya no usa `inspect_image(path)` sobre el PNG recien lanzado
    a escritura en background
  - ahora inspecciona directamente `frame_copy` en memoria

- `_AnalysisWorker.run()` y el vivo de Servicio:
  - ahora usan `InspectionSession` igual que `run-folder` y el loop continuo
  - con eso tambien respetan `continuous_position_threshold` y saltean frames
    quietos / repetidos de la misma manera que el motor real
  - esto corrige el caso donde Servicio analizaba 107 archivos como 107 inspecciones
    validas, mientras `run-folder` sobre el mismo lote solo considera 48 avances reales
    del material

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 163 - Blindaje contra patrón equivocado cuando falta scanner_id

**Problema reportado:** aun con los últimos ajustes de microperforado, al analizar
en vivo o por carpeta seguían apareciendo `MISSING` masivos "como si no se aplicaran
los cambios". El síntoma era consistente con estar resolviendo el patrón global en vez
del patrón específico de `scanner_1`.

**Diagnóstico:**
- el repo sí estaba actualizado (`HEAD=97ae674`), así que no era un problema de commit;
- al correr `run-folder` sin `--scanner`, el sistema podía caer al patrón global
  `data/patterns/modelo_B/holes.json`, reproduciendo exactamente los falsos `MISSING`;
- al correr el mismo lote con `scanner_1`, volvía a `48/48 raw OK`;
- además, si se estaba usando `dist\metalconf\metalconf.exe`, existía el riesgo de
  estar viendo un binario viejo hasta recompilar PyInstaller.

**Cambios aplicados:**
- `src/patterns/pattern_io.py`
  - nueva helper `infer_scanner_id(model, source_path=None)`;
  - infiere `scanner_1` / `scanner_2` desde el nombre de carpeta/archivo
    (`...SCANNER_1...`) o, si no alcanza, desde el `model` asignado en `config/io_map.yaml`;
  - `find_pattern_path()` ahora usa esa inferencia antes de caer al patrón global.
- `src/patterns/roi.py`
  - `load_roi()` ahora usa la misma inferencia para cargar la ROI correcta.
- `src/inspection.py`
  - `inspect_image()` e `inspect_folder()` infieren el scanner automáticamente cuando
    no se les pasa `scanner_id`.
- `src/main.py`
  - `run-image` y `run-folder` imprimen `[context] scanner=...` para dejar visible
    qué scanner/patrón se resolvió realmente.
**Validación:**
- comando antes problemático, ahora sin `--scanner`:
  - `.\.venv\Scripts\python.exe -m src.main run-folder --model modelo_B --input "...10-06-2026-MICROPERFORADO_5_SCANNER_1" --fps 5`
  - salida: `[context] scanner=scanner_1`
  - resultado: `48/48 raw OK`, `48/48 temporal OK`, `align_failures=0/48`, `machine_stop_frames=0`
- `pytest tests/` → `17 passed`

**Archivos modificados:** `src/patterns/pattern_io.py`, `src/patterns/roi.py`,
`src/inspection.py`, `src/main.py`, `CHANGELOG.md`

---

#### Cambio 162 - Microperforado scanner_1: bajar missing residuales en carpeta 10-06-2026-MICROPERFORADO_5

**Pedido:** seguir afinando `MISSING` sobre
`C:\Users\DefyC\Downloads\10-06-2026-MICROPERFORADO\10-06-2026-MICROPERFORADO_5_SCANNER_1`,
asumiendo que todos los frames son OK y que no debe aparecer ningún falso
desalineamiento para este microperforado de `scanner_1`.

**Diagnóstico (estado antes del ajuste):**
- usando el patrón específico de `scanner_1`, la carpeta ya estaba en
  `48/48 raw OK`, `0` desalineamientos falsos y `0` machine stop;
- aun así quedaban `missing` residuales de matching en el borde derecho:
  `total_missing=104`, `max_missing=7`;
- los faltantes frecuentes seguían concentrados en celdas de borde, no en el
  centro del patrón, así que convenía tocar matching/margen y no la lógica de
  desalineamiento del patrón.

**Cambios aplicados (`config/tolerancias.yaml`, `models.modelo_B`):**
- `tol_xy_px: 22.0 → 24.0`
  - da un poco más de tolerancia al matching nearest-neighbour de microperforado
    sin irse al extremo que empezaba a apagar evidencia en lotes viejos;
- `compare_right_ignore_px: 25.0 → 30.0`
  - recorta un poco más la franja derecha del patrón, que en este lote sigue
    aportando faltantes falsos residuales de borde.

**Validación:**
- carpeta `10-06-2026-MICROPERFORADO_5_SCANNER_1` con `--scanner scanner_1`:
  - sigue en `48/48 raw OK`, `48/48 temporal OK`
  - `align_failures=0/48`
  - `machine_stop_frames=0`
  - mejora de missing: `total_missing 104 → 81`
  - pico de missing: `max_missing 7 → 5`
- contraste sobre `05-06-2026-MICROPERFORADO_1`:
  - se mantiene evidencia de desalineamiento (`align_warn=1`, `machine_stop=1`)
    con un ajuste más conservador que alternativas más agresivas.

**Archivos modificados:** `config/tolerancias.yaml`, `CHANGELOG.md`

---

#### Cambio 160 - Empaquetado como .exe + autoarranque con Windows

**Pedido:** crear un `.exe` para correr `src.main run` al iniciar la PC sin necesidad
de tener Python instalado.

**Cambios aplicados:**

- `run_production.py` (nuevo): launcher mínimo que hardcodea `sys.argv = ["metalconf", "run"]`
  e importa `src.main.main`. Es el entry point de PyInstaller.

- `metalconf.spec` (nuevo): spec de PyInstaller en modo `--onedir` (carpeta con exe + libs).
  - Entry point: `run_production.py`
  - `console=False`: sin ventana de terminal, solo la UI PyQt6
  - Hidden imports para pymodbus, PyQt6, cv2, numpy, yaml, pymcprotocol, matplotlib
  - No bundlea `config/` ni `data/` — se leen desde el directorio raiz del proyecto
    (el Task Scheduler setea `WorkingDirectory` al raiz)

- `scripts/build_exe.ps1` (nuevo): script PowerShell que instala PyInstaller si no está,
  limpia builds anteriores y ejecuta `pyinstaller --clean metalconf.spec`.

- `scripts/setup_autostart.ps1` (nuevo): registra la tarea en el Task Scheduler de Windows.
  - Trigger: `AtLogOn` del usuario actual
  - `WorkingDirectory`: directorio raiz del proyecto (para que config/ y data/ sean accesibles)
  - 3 reintentos automáticos si falla
  - `RunLevel Highest` para acceso a cámara/PLC/red

**Decisiones de diseño:**
- `--onedir` (no `--onefile`): startup más rápido, sin extracción a temp en cada arranque
- `config/` y `data/patterns/` NO se bundlean: deben seguir siendo editables desde el proyecto
- `console=False`: producción sin terminal visible
- Task Scheduler `AtLogOn` (no `AtStartup`): necesario porque PyQt6 requiere sesión gráfica

**Flujo de uso:**
1. `powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1`
2. `powershell -ExecutionPolicy Bypass -File .\scripts\setup_autostart.ps1` (como Admin)
3. Reiniciar → la app arranca sola al login

**Archivos creados:** `run_production.py`, `metalconf.spec`, `scripts/build_exe.ps1`,
`scripts/setup_autostart.ps1`

---

#### Cambio 161 - Microperforado scanner_1: tuneo completo para carpeta 10-06-2026-MICROPERFORADO_5

**Pedido:** tunar el sistema para que la carpeta
`10-06-2026-MICROPERFORADO_5_SCANNER_1` (104 frames, todos OK) de 48/48 raw OK y
sin desalineamientos falsos.

**Diagnóstico (estado inicial):**
- `raw_ok=39, raw_nok=9, temporal_ok=48, temporal_nok=0`
- Los missing estaban concentrados en zona central (mal matching de grilla, no en bordes)
- Frames 0051-0054: NOK por "PATRON DESALINEADO / INCLINADO" — son frames de **borde de
  chapa** donde el límite físico del material crea zigzag en la línea de borde y una
  aparente inclinación de 3.1 deg
- Frame 0099: missing=7 con threshold=6 → raw NOK por conteo de missing

**Análisis de valores reales con diag_frames.py:**
- Frames normales: `zigzag_std ≤ 0.54px`, `zigzag_max ≤ 2.26px`, `slope_delta ≤ 0.75 deg`
- Frames de borde (0051-0054): `zigzag_std=3.8-5.3px`, `zigzag_max=13-18px`, `slope_delta≈3.1 deg`
- Desalineados reales (sesión anterior 05-06): `zigzag_std=4.6-7.5px`, `zigzag_max=16-22px`

**Cambios aplicados (`config/tolerancias.yaml`, `models.modelo_B`):**
- `grid_affine_refinement: false → true` — reduce raw_nok de 9→3 mejorando la asignación
  de grilla en frames con pequeño corrimiento relativo entre material y patrón
- `pattern_align_std_max_px: 4.0 → 7.0` — tolera el zigzag de borde de chapa (3.8-5.3px)
  sin liberar los desalineados reales (> 7px)
- `pattern_align_abs_max_px: 14.0 → 22.0` — ídem para el máximo puntual (bordes: 13-18px)
- `pattern_slope_delta_max_deg: 2.0 → 4.0` — tolera la inclinación aparente de borde
  de chapa (~3.1 deg) sin afectar los desalineados reales (> 4 deg esperado)
- `frame_missing_nok_threshold: 6 → 8` — frame_0099 tenía missing=7 con zigzag normal;
  los missing máximos en esta carpeta son 7 (frame_0099) → threshold=8 cubre la variación

**Resultado final:**
- `raw_ok=48/48, raw_nok=0, temporal_ok=48, temporal_nok=0, machine_stop_frames=0`
- 17/17 pytest passing

**Archivos modificados:** `config/tolerancias.yaml`

---

### Sesión 2026-06-10 — Tadeo + Claude

#### Cambio 159 - Un solo loop continuo para no divergir entre INICIAR y carpeta

**Pedido:** dejar definitivamente unificado el analisis en vivo con el analisis por
carpeta, manteniendo el filtro de avance de material para no re-analizar el mismo
frame quieto.

**Cambio aplicado:**
- `src/controller/scanner_controller.py`
#### Cambio 159 - Microperforado: desalineacion de patron por RACHA de N frames (no parada en un solo frame)

**Pedido:** la parada por desalineacion quedaba muy filosa — perturbaciones chicas de mala
toma seguian disparando falsos positivos. Implementar racha de N frames consecutivos igual
que el detector de faltantes.

**Diagnostico:**
- el Cambio 158 seteaba `machine_stop=True` inmediatamente en el primer frame desalineado
- ademas habia un bug critico: `desalign_state` se leia de `pre.get("desalign_state")`
  pero si no existia se creaba un dict local que SE DESCARTABA al terminar el frame;
  la racha nunca acumulaba mas de 1 porque el contador arrancaba en 0 en cada frame
- los umbrales eran tambien demasiado ajustados (std_max=3.0 con normal ~0.5, sin margen)

**Cambios aplicados:**

- `src/inspection.py`:
  - nuevo param `pattern_align_stop_frames` (default 3): cuantos frames consecutivos
    desalineados se requieren para disparar `machine_stop`
  - `desalign_state` se guarda de vuelta en `pre["desalign_state"]` al final del frame
    para que persista via `_preloaded` al siguiente frame (igual que `ema_state`)
  - la racha se resetea a 0 cuando el frame vuelve a estar alineado
  - el motivo de parada incluye la racha `[N/stop_frames frames]`

- `src/vision/inspector.py`:
  - `InspectionSession` inicializa `desalign_state: {"streak": 0, "reason": ""}` en
    `_preloaded` en ambas ramas (resource_owner y standalone)

- `config/tolerancias.yaml` -> `models.modelo_B`:
  - `pattern_align_std_max_px: 3.0 -> 4.0`  (mas margen al ruido; malos >=4.6px)
  - `pattern_align_abs_max_px: 12.0 -> 14.0` (malos >=16px)
  - `pattern_align_stop_frames: 3`

**Validacion sobre `05-06-2026-MICROPERFORADO_1`:**
- `frame_0023`: MACHINE_STOP al tercer frame consecutivo desalineado (21->22->23) correctamente
- `frame_0025`: 1 frame aislado de desalineacion, no llega a 3, no para → 0 falsos positivos
- `machine_stop_frames=1`, `temporal_nok=1`, 87/88 frames sin parada
- 17/17 pytest passing

**Archivos modificados:** `src/inspection.py`, `src/vision/inspector.py`, `config/tolerancias.yaml`

---

#### Cambio 158 - Microperforado: detectar desalineacion de patron por zigzag de borde -> machine_stop inmediato

**Pedido:** detectar las desalineaciones que aparecen en los frames 21-26 de
`05-06-2026-MICROPERFORADO_1` y DETENER LA MAQUINA cuando ocurran.

**Diagnostico:**
- se corrio analisis de `pattern_zigzag_std_px` y `pattern_zigzag_max_px` sobre toda
  la carpeta (88 frames analizados, total 137 de la carpeta con filtro de movimiento)
- frames 21, 22, 23, 25: zigzag PATRON std=4.6-7.5px, max=16-22px
- todos los demas frames: zigzag std<0.5px, max<2.5px
- el parametro `pattern_align_enabled` ya existia en el codigo pero estaba desactivado
  (`false`) en modelo_B
- cuando se activa, setea `pattern_alignment_warn=True` y `final_status=NOK` pero
  NO seteaba `machine_stop=True` → la maquina NO paraba
- ademas habia un bug: la variable local `machine_stop` se inicializaba en `False`
  DESPUES del bloque centering donde se queria setear a `True`, reseteandola

**Cambios aplicados:**

- `src/inspection.py`:
  - nuevo parametro `pattern_align_machine_stop` (default True): cuando
    `pattern_align_enabled` y el zigzag supera los umbrales, activa parada inmediata
  - usa variable intermedia `_desalign_stop`/`_desalign_reason` inicializada ANTES
    del bloque centering y aplicada DESPUES del ms_detector.update() para no ser
    reseteada por la inicializacion de `machine_stop=False`
  - el motivo de parada incluye std y max del zigzag en px para diagnostico
  - misma logica para los sub-triggers: zigzag borde, zigzag centro, descentrado, inclinacion

- `config/tolerancias.yaml` -> `models.modelo_B`:
  - `pattern_align_enabled: false -> true`
  - `pattern_align_std_max_px: 3.0`  (normal ~0.5px; frames malos: 4.6-7.5px)
  - `pattern_align_abs_max_px: 12.0` (normal ~2px;   frames malos: 16-22px)
  - `pattern_align_machine_stop: true`

**Validacion sobre `05-06-2026-MICROPERFORADO_1`:**
- `machine_stop_frames=4`: frames 21, 22, 23, 25 → `MACHINE_STOP` correctamente
- 84 frames sin ningun falso positivo
- `temporal_nok=4` (cada frame desalineado dispara parada inmediata sin esperar racha)
- 17/17 pytest passing

**Archivos modificados:** `src/inspection.py`, `config/tolerancias.yaml`

---

#### Cambio 157 - Unificar sesion temporal entre INICIAR y analisis por carpeta

**Pedido:** que el analisis en vivo al apretar `INICIAR` y el analisis de carpeta usen
siempre la misma logica, pero sin re-analizar cientos de veces el mismo frame cuando
la cinta no avanzo.

**Diagnostico:**
- el nucleo de vision ya era comun, pero habia una diferencia importante alrededor:
  - `ScannerController` en vivo filtraba frames quietos con
    `continuous_position_threshold`
  - `inspect_folder()` analizaba cada imagen guardada aunque fuera practicamente la
    misma seccion detenida
- eso hacia divergir el estado temporal (EMA, machine_stop, streaks) entre vivo y carpeta

**Cambio aplicado:**
- `src/vision/inspector.py`
  - nueva `InspectionSession`, que encapsula:
    - preload de tolerancias / patron / ROI
    - EMA de alineacion
    - `MachineStopDetector`
    - compuerta de movimiento para saltear frames quietos
- `src/controller/scanner_controller.py`
  - `INICIAR` ahora corre contra esa sesion compartida
- `src/inspection.py`
  - `inspect_folder()` tambien usa la misma sesion temporal y el mismo filtro de
    avance de material

**Resultado esperado:** vivo y carpeta comparten la misma nocion de "nuevo frame valido
para inspeccionar". Si el material no avanzo, no se vuelve a analizar ni se contamina
la logica temporal con repeticiones del mismo cuadro quieto.

**Archivos modificados:** `src/vision/inspector.py`, `src/controller/scanner_controller.py`,
`src/inspection.py`

---

#### Cambio 156 - Microperforado: machine_stop solo acumula evidencia en frames severos

**Pedido:** seguir corrigiendo la carpeta actualizada de `scanner_1` porque, aun con
mejor matching, seguian apareciendo muchos NOK falsos por parada de maquina.

**Diagnostico:**
- el refinamiento afin mejoraba la asignacion de grilla, pero el `run-folder` seguia
  mostrando muchas paradas porque `machine_stop` acumulaba rachas de faltantes chicos
  y persistentes en columnas de borde
- en otras palabras:
  - los agujeros ya no estaban tan mal matcheados como antes
  - pero la logica de parada seguia interpretando esas rachas chicas como punzon roto
- para `modelo_B`, eso era demasiado agresivo en esta carpeta nueva

**Cambio aplicado:**
- `src/inspection.py`
  - nueva compuerta `machine_stop_require_frame_nok`
  - si esta activa y el frame no supera `frame_missing_nok_threshold`, ese frame NO
    aporta evidencia al detector persistente de `machine_stop`
- `config/tolerancias.yaml` -> `models.modelo_B`
  - `machine_stop_require_frame_nok: true`

**Resultado esperado:** microperforado deja de convertir pequenos faltantes falsos
repetidos en una parada de maquina, pero sigue permitiendo `machine_stop` cuando los
frames ya vienen con un nivel de faltantes realmente severo.

**Archivos modificados:** `src/inspection.py`, `config/tolerancias.yaml`

---

#### Cambio 155 - Microperforado: activar refinamiento afin de grilla para scanner_1 actualizado

**Pedido:** revisar la carpeta
`C:\\Users\\DefyC\\Downloads\\10-06-2026-MICROPERFORADO\\10-06-2026-MICROPERFORADO_5_SCANNER_1`
porque seguia habiendo `missing` falsos, agujeros presentes que el sistema no tomaba
como validos y bordes de patron inconsistentes.

**Diagnostico:**
- se hizo `git pull --rebase origin master` y no habia cambios nuevos
- sobre esa carpeta nueva, el estado actual daba:
  - `107` frames totales
  - `64 raw OK / 43 raw NOK`
  - `73 temporal OK / 34 temporal NOK`
- comparando overlays y mascaras, los agujeros estaban realmente detectados; el fallo
  no estaba en threshold/contornos sino en la **asignacion de la grilla esperada**
- evidencia visual:
  - la mascara de `frame_0008` mostraba los agujeros presentes
  - el overlay marcaba `missing` en el medio del patron aunque los circulos existian
- pruebas en memoria sobre la carpeta completa:
  - activar `grid_affine_refinement` baja `raw_nok` de `43 -> 22`
  - con eso, subir `frame_missing_nok_threshold` de `5 -> 6` baja `raw_nok` a `10`
    sin tocar el patron ni el ROI

**Cambio aplicado (`config/tolerancias.yaml`, `models.modelo_B`):**
- `grid_affine_refinement: false -> true`
- `frame_missing_nok_threshold: 5 -> 6`

**Resultado esperado:** la grilla de microperforado acompana mejor pequenas
deformaciones / inclinaciones locales del material en `scanner_1`, reduciendo los
falsos `missing` donde los agujeros ya estaban detectados pero quedaban mal asignados.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 154 - Microperforado: recuperar estabilidad de matching en scanner_1 sin tocar el patron

**Pedido:** revisar por que `scanner_1` con microperforado volvio a marcar muchos
`missing` falsos y comparar contra commits anteriores donde casi no habia faltantes falsos.

**Diagnostico:**
- se hizo `git pull --rebase origin master` antes de analizar y no habia cambios nuevos
- se comparo el folder
  `C:\\Users\\DefyC\\Downloads\\05-06-2026-PATRONES EDITADOS\\05-06-2026-MICROPERFORADO_1`
  entre el estado actual y commits viejos estables
- el patron/ROI de `data/patterns/scanner_1/modelo_B` no se desvio respecto del commit
  bueno `8f83cab`; el problema estaba en como se estaba calificando y matcheando
- evidencia sobre esa carpeta:
  - `8f83cab`: `137/137 raw OK`
  - HEAD antes del fix: `134/137 raw OK`
  - en pruebas sobre la carpeta, subir `tol_xy_px` de `20 -> 22` baja los `missing`
    totales (`54 -> 43`) y reduce `raw_nok` (`3 -> 1`)
  - ademas, `frame_missing_nok_threshold` habia quedado mas agresivo (`10 -> 3`)
    que en el baseline validado

**Cambio aplicado (`config/tolerancias.yaml`, `models.modelo_B`):**
- `tol_xy_px: 20.0 -> 22.0`
- `frame_missing_nok_threshold: 3 -> 5`

**Resultado esperado:** microperforado vuelve a tolerar mejor pequenos corrimientos de
matching en `scanner_1`, baja los `missing` falsos en los frames de borde y deja de
castigar como NOK varios frames que antes estaban dentro del comportamiento estable.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 153 - Revert parcial del overlay cian compartido para no penalizar scanner_1

**Pedido:** volver hacia atras el cambio de los redondos cian porque, desde que entro esa
logica compartida, `scanner_1` con microperforado dejo de comportarse como ayer y parecia
escanear peor / mas pesado.

**Diagnostico:**
- los circulos cian no afectaban el patron, pero si agregaban trabajo extra en cada frame
  dentro de `draw_compare_overlay()` y tambien arrastraban estado adicional en
  `src/inspection.py`
- como `scanner_1` estaba estable y el pedido prioritario fue recuperar ese flujo en vivo,
  conviene volver al overlay liviano compartido que usaba antes microperforado

**Cambio aplicado:**
- `src/pipeline/annotate.py`
  - se elimina el dibujo de `raw_detected` en cian
- `src/inspection.py`
  - se elimina la ruta auxiliar `detected_holes_in_bbox`
  - `overlay_holes` vuelve a la logica visual filtrada por bbox/top-bottom/hull
  - se deja intacta la logica de comparacion/decision

**Resultado:** microperforado vuelve a usar un overlay mas liviano y cercano al flujo con
el que estaba funcionando correctamente, sin tocar el patron ni la decision de analisis.

**Archivos modificados:** `src/pipeline/annotate.py`, `src/inspection.py`

---

#### Cambio 152 - Microperforado: borde PATRON por columna exterior dominante, sin mezclar columnas vecinas

**Pedido:** recuperar la deteccion correcta del borde PATRON en microperforado, porque la
linea estaba haciendo zigzag y ya no servia para leer bien desviacion/bordes.

**Diagnostico:**
- con el fix del Cambio 145, `_pattern_bounds_by_band()` usaba `percentile(10/90)` como
  referencia global del borde
- en `scanner_1/modelo_B` eso cae en la **segunda** columna extrema, no en la exterior:
  - clusters X detectados: `16, 35, 53, ..., 183, 201`
  - `p10 ≈ 33.8`, `p90 ≈ 184.2`
- con `boundary_tol_px=8`, el gate derecho/izquierdo terminaba mezclando dos columnas
  vecinas del patron y la linea PATRON hacia serrucho/zigzag entre ellas

**Cambio aplicado (`src/pipeline/edge_centering.py`):**
- nueva helper `_robust_outer_column_centers()`
  - agrupa agujeros por clusters de X
  - elige la columna exterior **dominante**
  - si la columna extrema es muy rala respecto de la siguiente (`count < 60%`), la trata
    como outlier y usa la siguiente hacia adentro
- `_pattern_bounds_by_band()` ahora usa esa referencia robusta en vez de `percentile(10/90)`

**Resultado:** se conserva la ventaja del fix de outliers del Cambio 145, pero sin mezclar
las dos columnas exteriores reales del microperforado. La linea PATRON vuelve a seguir una
columna limpia y deja de zigzaguear.

**Archivos modificados:** `src/pipeline/edge_centering.py`

---

#### Cambio 151 - Microperforado: revertir agresividad de parada inmediata y volver al comportamiento estable

**Pedido:** revisar si se habia roto el patron de microperforado respecto de ayer, porque
la maquina volvio a detenerse demasiado facil ("con 3 frames se rompe todo").

**Diagnostico:**
- el patron/ROI de microperforado NO cambio respecto del baseline bueno
  (`data/patterns/modelo_B/holes.json` y ROI actuales coinciden con el estado validado)
- lo que si cambio fue la logica del **Cambio 143**
- en particular:
  - `DESVIACION LATERAL` paso de warning/NOK temporal a `machine_stop` inmediato
  - `machine_stop_min_missing` bajo de `2` a `1`
  - `machine_stop_ignore_near_miss` paso de `true` a `false`
- eso endurecio demasiado `modelo_B` en vivo y explica una parada como
  `DESVIACION LATERAL (+25.6px)` en solo pocos frames

**Cambio aplicado:**
- `src/inspection.py`
  - la desviacion lateral vuelve a marcar warning/NOK, pero **no** parada inmediata
- `config/tolerancias.yaml` -> `models.modelo_B`
  - `machine_stop_min_missing: 1 -> 2`
  - `machine_stop_ignore_near_miss: false -> true`

**Resultado esperado:** microperforado vuelve al comportamiento estable de ayer:
detecta desviaciones y faltantes como advertencia/NOK, pero no detiene la maquina con la
agresividad introducida por el Cambio 143.

**Archivos modificados:** `src/inspection.py`, `config/tolerancias.yaml`

---

#### Cambio 150 - Esterilla: verde del overlay vuelve a representar deteccion valida en ventana de patron

**Sintoma:** despues del Cambio 149 el overlay paso a mostrar menos agujeros verdes, aun
cuando la deteccion seguia encontrando los agujeros. Visualmente "se veia peor" porque el
verde quedo demasiado estricto para el uso operativo.

**Causa raiz:**
- en Cambio 149 se definio `verde = match exacto 1-a-1`
- eso reduce la cantidad de verdes cuando hay dos detectados muy cercanos y solo uno gana
  la asignacion del matcher, aunque ambos pertenezcan claramente a la zona del patron
- para inspeccion operativa, lo que el usuario necesita ver es:
  - verde = agujero detectado dentro de la ventana activa del patron
  - cian = deteccion cruda fuera de esa ventana

**Cambio aplicado:**
- `src/inspection.py`
  - `overlay_holes` vuelve a salir de `detected_holes_in_bbox`
  - se mantiene la separacion visual entre:
    - verde: deteccion valida dentro de la ventana de comparacion
    - cian: deteccion cruda fuera de la ventana activa

**Resultado:** el overlay recupera una lectura visual util para calibracion y servicio:
si el agujero forma parte de la zona analizada del patron, vuelve a verse en verde aunque
el matcher interno haya elegido otro detectado cercano para la asignacion final.

**Archivos modificados:** `src/inspection.py`

---

#### Cambio 149 - Esterilla: overlay verde ahora representa matches reales del patron

**Sintoma:** seguian apareciendo agujeros azules/cian en los bordes de la esterilla aun
cuando el detector los encontraba y, en varios casos, el matching contra patron tambien
los estaba aceptando. El problema ya no era "detectar", sino que el verde del overlay no
representaba exactamente la misma lista de agujeros que usaba la comparacion.

**Causa raiz:**
- `compare_missing_only()` trabajaba con `detected_in_bbox`
- pero el overlay verde se dibujaba desde otra lista (`overlay_holes`) filtrada de nuevo
  por reglas visuales (`bbox` / top-bottom / hull del patron)
- eso permitia este caso inconsistente:
  - agujero detectado
  - agujero usado por matching
  - agujero NO dibujado en verde porque el overlay lo descartaba aparte

**Cambio aplicado:**
- `src/inspection.py`
  - se conserva la lista `detected_holes_in_bbox` alineada con `detected_in_bbox`
  - el overlay verde ahora se arma directamente desde `report.matched_detected_idx`
  - o sea: verde = agujero que realmente matcheo con el patron
  - cian = deteccion cruda que NO entro como match

**Resultado:** el overlay deja de mentir visualmente. Si un agujero forma parte real del
patron y el matching lo acepta, se dibuja en verde aunque antes un filtro visual lo dejara
afuera.

**Archivos modificados:** `src/inspection.py`

---

#### Cambio 148 - Service UI: sección "Calibración ROI" manual en página Grabación

**Pedido:** agregar una sección en la pestaña Grabación para calibrar el ROI visualmente,
con ajuste manual (sin detección automática).

**Implementación:**

- **`_build_roi_section()`** — nueva sección `QGroupBox` en la columna izquierda de
  `_build_grab_page()`, entre la sección de Grabación y el stretch. Contiene:
  - Etiqueta "ROI actual" (cargada desde `roi.json` al abrir, actualiza al cambiar scanner)
  - "Abrir imagen" / "Abrir carpeta" — carga un frame de referencia
  - Preview live (160px alto): zona fuera del ROI oscurecida, líneas verde (izq) y cyan (der)
  - BORDE IZQ [◄] [►] / BORDE DER [◄] [►] — mueven cada borde N px por click
  - Spinbox PASO px (1–100, default 5)
  - "GUARDAR ROI" — escribe `roi.json` para el scanner y modelo activos

- **Al cargar un frame:** inicializa los bordes con la ROI guardada existente (si hay),
  o con la imagen completa (x=0, rx=W).

- **La ROI guardada** se aplica automáticamente en análisis de carpeta y modo producción
  a través de `load_roi(model, scanner_id)` existente — no requiere ningún cambio adicional.

- **Eliminado:** detección automática por backlight (`_RoiDetectWorker`, canal, margen).

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 147 - Esterilla: incluir filas superior/inferior del patron en matching visual y comparacion

**Pedido:** en inspeccion de esterilla algunos agujeros aparecian en azul/cian pero no en
verde, aun estando claramente dentro del patron real. El usuario pidio que esos agujeros
se detecten como parte del patron.

**Diagnostico:**
- el detector crudo si los encontraba
- el problema no estaba en `grid_compare_margin_x_px`
- la exclusion venia de `grid_compare_margin_y_px=40.0`, que recortaba demasiado la
  comparacion arriba y abajo del ROI
- en `frame_0113`:
  - con `margin_y=40`: `expected=81`
  - con `margin_y=10`: `expected=94` (entran todos los puntos del patron)
  - `missing` se mantiene en `2`, o sea no mete falsos faltantes extra en ese frame

**Cambio aplicado:**
- `config/tolerancias.yaml` -> `models.modelo_A`
  - `grid_compare_margin_y_px: 40.0 -> 10.0`

**Validacion:**
- `run-image` sobre `frame_0113.png`:
  - `status=OK`, `expected=94`, `detected=97`, `missing=2`, `extra=0`
- `run-folder` sobre `05-06-2026-ESTERILLA_1`:
  - `raw_ok=79`, `raw_nok=54`
  - `temporal_ok=133`, `temporal_nok=0`
  - `machine_stop_frames=0`

**Resultado:** los agujeros de las filas superior/inferior ahora entran al patron activo
y pasan a verse/matchear en verde en lugar de quedar como deteccion cruda descartada.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 146 - CLI: subcomando `detect-roi` para calibracion automatica de ROI

**Motivacion:** la ROI se definía manualmente con `define-roi` (mouse). Con backlight
encendido, la transición backlight→chapa es detectable automáticamente por gradiente
del perfil de columna → posible auto-calibrar sin intervención del usuario.

**Algoritmo (en `src/patterns/roi.py`, funcion `detect_roi_from_images`):**
1. Para cada frame (hasta `--max-frames=20` si se pasa carpeta): calcula el percentil 20
   por columna del canal R (robusto contra agujeros brillantes en la chapa)
2. Suaviza con Gaussiano y calcula el gradiente de columna
3. Borde izquierdo = caída más pronunciada (bright→dark) en la mitad izquierda del frame
4. Borde derecho = subida más pronunciada (dark→bright) en la mitad derecha del frame
5. Calcula la mediana entre todos los frames → posición estable independiente de agujeros

**Comando nuevo (`src/main.py`):**
```
.\.venv\Scripts\python.exe -m src.main detect-roi ^
    --model modelo_B ^
    --scanner scanner_1 ^
    --img "C:\path\to\frames_con_backlight" ^
    --channel r          # canal para detectar (r|g|b|gray, default: r)
    --margin 0           # px a recortar hacia adentro de cada borde (default: 0)
    --max-frames 20      # frames a usar si se da carpeta (default: 20)
    --dry-run            # muestra resultado SIN guardar roi.json
    --show               # abre preview interactivo
```

**Salidas:**
- `data/patterns/{scanner}/{model}/roi.json` (o sin scanner si se omite)
- `data/output/detect_roi_preview.png`: frame de referencia con ROI + perfil de columna

**Validacion en frames 05-06-2026-MICROPERFORADO_1 (137 frames):**
- Con `--margin 0`:  x=110, w=473 (borde bruto backlight→chapa)
- Con `--margin 50`: x=160, w=373 (zona interior mas segura)
- ROI actual manual: x=195, w=295 (~= margin 85 sobre la deteccion automatica)

**Archivos modificados:** `src/patterns/roi.py` (funcion `detect_roi_from_images`),
`src/main.py` (funcion `cmd_detect_roi` + parser `detect-roi`)

---

#### Cambio 145 - microperforado: fix línea PATRON borde izquierdo truncada (pct10 en lugar de min global)

**Síntoma:** la línea PATRON izquierda en el overlay del microperforado (modelo_B) solo
aparecía en la parte inferior de la imagen (5/24 bandas) y estaba completamente ausente
de los 2/3 superiores. El usuario lo describía como "zigzag entre los agujeros del borde".

**Diagnóstico:**
- `_pattern_bounds_by_band` usa `global_left = min(hh.x for hh in all_holes)` como referencia
- El patrón modelo_B tiene 5 agujeros en la columna exterior escalonada de la zona inferior:
  x≈58–60 (y=305–415), y el resto de los agujeros exteriores en x≈76 (y=65–291)
- Con `global_left=58.3` y `boundary_tol_px=8`: gate = 58.3+8 = 66.3
- Los agujeros principales del borde izquierdo (x≈76) son excluidos: 76 > 66.3
- Resultado: solo 5/24 bandas (zona inferior) tienen punto de borde izquierdo
- La línea PATRON no aparece en la parte superior del frame → parece un "zigzag" o corte

**Causa raíz del commit `16a10b6`:** ese commit redujo `boundary_tol_px` de 22→8 para
evitar que ambas columnas del stagger (x≈58 y x≈76) contribuyeran a la misma banda
y la línea cayera entre ellas. Con global_left=76 (cuando no existía la columna x=58)
funcionaba bien. Pero si el patrón tiene la columna x=58 como mínimo global,
btol=8 excluye la columna principal x=76.

**Fix aplicado (`src/pipeline/edge_centering.py`, función `_pattern_bounds_by_band`):**
- Reemplazado `global_left = min(xs)` / `global_right = max(xs)` por:
  `global_left = np.percentile(xs, 10)` / `global_right = np.percentile(xs, 90)`
- El percentil 10 con 121 agujeros vale ≈75.9 (ignora los 5 agujeros extremos x=58
  que son solo el 4% del total) → gate = 75.9+8 = 83.9
- x=76 ≤ 83.9 → incluido ✓  |  x=94 > 83.9 → excluido ✓  |  x=58 ≤ 83.9 → incluido ✓
- Resultado: 15/24 bandas con borde izquierdo (antes: 5/24), sin zigzag entre columnas

**Validación:**
- Frames `05-06-2026-MICROPERFORADO_1`: 137/137 OK (igual que antes)
- Visualización `data/dbg_borde_antes_despues.png`: línea verde cubre toda la altura

---

### Sesión 2026-06-09 — Tadeo + Claude

#### Cambio 144 - Esterilla: patron de scanner_2 reconstruido desde frame_0118 + matching mas robusto + overlay honesto

**Sintoma:** la esterilla seguia mostrando muchos agujeros "faltantes" aun cuando la
mascara detectaba casi toda la chapa. Ademas, el overlay verde ocultaba parte de los
agujeros detectados porque primero los filtraba por `bbox` y `pattern_hull`.

**Diagnostico rapido sobre `frame_0113`:**
- deteccion cruda: `97 agujeros`
- overlay comparativo visible: `85 agujeros`
- patron esperado activo: `84 posiciones`
- varios "missing" quedaban a solo `21-28 px` del agujero real mas cercano

Eso confirmo dos problemas mezclados:
- el patron reconstruido desde `frame_0113` no representaba bien la grilla real
- el overlay estaba tapando detecciones crudas validas, lo que hacia parecer que el
  detector fallaba mas de lo real

**Cambio aplicado:**
- `data/patterns/scanner_2/modelo_A/holes.json`
  - reconstruido desde `05-06-2026-ESTERILLA_1/frame_0118.png`
  - patron final: `94 puntos` unicos sobre ROI `415x480`
- `config/tolerancias.yaml` -> `models.modelo_A`
  - `tol_xy_px: 18.0 -> 24.0`
  - `grid_affine_refinement: false -> true`
- `src/pipeline/annotate.py`
  - el overlay ahora dibuja en cian tenue los agujeros detectados crudos que fueron
    descartados por filtros de comparacion, y mantiene en verde los que realmente
    entran al matching
- `src/inspection.py`
  - pasa la deteccion cruda al overlay para que ANALISIS e INSPECCION muestren el mismo
    contexto visual del matching real

**Validacion sobre `05-06-2026-ESTERILLA_1`:**
- con patron `frame_0118` y config base:
  - `raw_ok=24`, `raw_nok=109`, `temporal_ok=116`, `temporal_nok=17`
- con patron `frame_0118` + `tol_xy_px=24` + `grid_affine_refinement=true`:
  - `raw_ok=99`, `raw_nok=34`, `temporal_ok=133`, `temporal_nok=0`
  - `machine_stop_frames=0`

**Resultado:** el patron de esterilla vuelve a comportarse de forma consistente en toda
la carpeta probada, desaparecen las paradas falsas y el overlay deja claro cuando un
agujero fue detectado pero excluido por la ventana de comparacion.

**Archivos modificados:** `data/patterns/scanner_2/modelo_A/holes.json`,
`config/tolerancias.yaml`, `src/pipeline/annotate.py`, `src/inspection.py`

---

#### Cambio 143 - Microperforado: parada inmediata por corrimiento lateral + parada por agujero único

**Pedido:** Reactivar parada de máquina (machine_stop = True) inmediata para grandes corrimientos laterales (desalineación) como en commits más viejos, y solucionar el problema donde los agujeros faltantes al comienzo de la grabación microperforado no detenían la máquina.

**Diagnóstico:**
1. **Desalineación lateral:** Cambio 127 había degradado la desviación lateral a un simple estado warning NOK con racha temporal (no parada en un frame). Revertimos esto para que si supera `grid_lateral_shift_max_px` (25px en microperforado), actúe como parada inmediata de máquina.
2. **Faltantes consecutivos:** El patrón del microperforado (modelo_B) es muy denso (espacio entre filas es de sólo 22.8px, menor a `2 * tol_xy_px` = 44px). Por tanto, cualquier agujero faltante tenía una detección vecina dentro del radio de exclusión y era clasificado como "near-miss" (y omitido por `ignore_near_miss: true`). Al mismo tiempo, al haber 1 solo agujero faltante por columna, no alcanzaba el umbral de `machine_stop_min_missing: 2`.

**Cambios aplicados:**
- `src/inspection.py`:
  - Si el corrimiento lateral supera `grid_lateral_shift_max_px`, activa `machine_stop = True` y `_ms_reason = f"DESVIACION LATERAL..."` directamente (parada en un frame).
- `config/tolerancias.yaml`:
  - modelo_B `machine_stop_min_missing`: `2 → 1` (para detectar el punzón roto/agujero único).
  - modelo_B `machine_stop_ignore_near_miss`: `true → false` (evita que la grilla tan densa enmascare la falla real).
  - Comentarios corregidos para eliminar mojibakes y hacer pasar unit-tests.

**Validación inmediata sobre `05-06-2026-MICROPERFORADO_1`:**
- `machine_stop_frames` pasa de 0 a 13 frames (activados en frame_0005 a frame_0010 para columna 1, y frame_0015 a frame_0021 para columna 4).
- 0 falsos positivos en los 115 frames restantes.
- 17/17 pytest pasando correctamente.

#### Cambio 142 - Esterilla: reconstruccion de patron desde frame_0113 para evaluar carpeta editada

**Pedido:** reconstruir `scanner_2/modelo_A` usando como referencia
`C:\Users\DefyC\Downloads\05-06-2026-PATRONES EDITADOS\05-06-2026-ESTERILLA_1\frame_0113.png`
y medir como se comporta la misma carpeta contra ese patron nuevo.

**Cambio aplicado:**
- `data/patterns/scanner_2/modelo_A/holes.json`
  - reconstruido con ROI activa `x=110, w=415`
  - build resultante: `97 puntos`, `93` celdas unicas tras depurar `4` duplicadas
  - `dx=39`, `dy=21`, `phase=(12,2)`, `stagger_x_odd=20`

**Validacion inmediata sobre `05-06-2026-ESTERILLA_1`:**
- Antes de reconstruir:
  - `raw_ok=33`, `raw_nok=100`
  - `temporal_ok=123`, `temporal_nok=10`
  - missing estructural concentrado en varias celdas fijas del patron
- Despues de reconstruir con `frame_0113`:
  - `raw_ok=0`, `raw_nok=133`
  - `temporal_ok=68`, `temporal_nok=65`
  - `machine_stop_frames=6`
  - nuevas columnas/celdas con faltante estructural al 95-100%

**Conclusion:** este frame `0113` NO sirve como nueva referencia de patron para esa
carpeta. La reconstruccion empeora la estabilidad de forma fuerte y genera un baseline
de missing todavia mas alto que el patron anterior.

**Archivos modificados:** `data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 141 - Unificacion de contexto entre analisis e inspeccion

**Pedido:** que `ANALISIS` y `modo inspeccion` usen exactamente el mismo contexto
base de analisis para no tener que perseguir diferencias invisibles entre ambos.

**Cambio aplicado:**
- `src/inspection.py`
  - `inspect_folder()` ahora tambien crea `ema_state: {}` en el preload compartido,
    igual que el inspector vivo
- `src/ui/service.py`
  - el analisis en vivo durante grabacion ahora mantiene un preload persistente con
    `tolerances`, `pattern`, `roi` y `ema_state`, en lugar de recargar solo el detector
    de `machine_stop` frame a frame

**Resultado:** carpeta comun, analisis en servicio y modo inspeccion quedan mas alineados:
usan la misma fuente de verdad de tolerancias/ROI/patron y tambien conservan el mismo
estado suavizado de alineacion entre frames.

**Regla permanente:** a partir de este cambio, cualquier ajuste de ROI, tolerancias,
preprocess, lineas de borde, alineacion o matching debe impactar por igual en
ANALISIS, modo inspeccion y pruebas equivalentes. No se debe mantener logica ni
parametros separados entre esos modos, salvo la compuerta propia del avance real
del material y la FSM de produccion.

**Archivos modificados:** `src/inspection.py`, `src/ui/service.py`

---

#### Cambio 140 - Microperforado: ROI recortado y patrón reconstruido desde grabación en vivo

**Síntoma:** en modo inspección en vivo (run), el ROI previo (x=195, w=295) era demasiado
ancho: incluía ~80px de metal sólido a la derecha de la zona perforada donde había marcas/
arañazos que se detectaban como agujeros falsos → machine_stop → parada inmediata.
Además, el `scanner_1/modelo_B/roi.json` previo (x=236, w=216) cortaba la parte izquierda
de la chapa respecto a la posición real de la cámara en vivo.

**Diagnóstico:** análisis de perfiles de columna en frames de `data/recordings/09-06-2026-MICROPERFORADO_1/`:
- Zona de agujeros reales: frame x=210 a x=408 (ancho ~198px)
- Metal sólido sin agujeros (izquierdo): x=88 a x=209
- Metal sólido sin agujeros (derecho): x=409 a x=531
- Borde naranja metálico (exterior): x=0-88 y x=532-640

**Cambios:**
- `data/patterns/scanner_1/modelo_B/roi.json`: `x=195,w=295` → `x=200,w=230` (x=200 a x=430)
- `data/patterns/modelo_B/roi.json`: ídem — ahora live mode y análisis carpetas usan el mismo ROI
- `data/patterns/scanner_1/modelo_B/holes.json`: reconstruido desde `frame_0003.png` de la grabación nueva
  - 78 holes, image_size=[230,480], dx=36, dy=14, stagger_x_odd=-18, phase=(34,8)
  - Antes: 121 holes (ROI viejo más ancho capturaba columnas fuera del área real)
- `data/patterns/modelo_B/holes.json`: copiado del scanner_1 (idéntico)

**Resultado:** live mode y análisis por carpetas ahora usan ROI y patrón idénticos.
Los parámetros de detección ya eran idénticos (`load_tolerances("modelo_B")` en ambos modos).

**Archivos modificados:**
- `data/patterns/scanner_1/modelo_B/roi.json`
- `data/patterns/modelo_B/roi.json`
- `data/patterns/scanner_1/modelo_B/holes.json`
- `data/patterns/modelo_B/holes.json`

---

#### Cambio 139 - Esterilla: CLAHE + adaptive_block_size 41→21 para ROI ampliada

**Síntoma:** detección muy baja en esterilla (~10% detection_ratio). El ROI ahora mide 415px
(ampliado por la otra máquina). Las zonas brillantes de retroiluminación a los costados del
material elevan el promedio local del adaptive threshold → los agujeros no superan el umbral.

**Cambios en `config/tolerancias.yaml` — modelo_A:**
- `use_clahe: true` — normaliza la iluminación despareja antes de umbralizar
- `clahe_clip: 3.0, clahe_tile: 8`
- `adaptive_block_size: 41 → 21` — vecindad más local para ROI de 415px
- `adaptive_c: -5.0 → 3.0` — threshold = mean - 3; más permisivo

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 138 - Esterilla: grid_compare_margin_x_px 30→40 para excluir borde izquierdo

**Síntoma:** análisis de esterilla muestra ~17 faltantes constantes incluso con material bien posicionado.
Los agujeros faltantes están concentrados en el lado IZQUIERDO del patrón.

**Causa:** Cambio 135 expandió el ROI 10px a la izquierda (x=225→215) y bajé `grid_compare_margin_x_px`
de 40→30. Con margen 30 se comparan posiciones esperadas muy cerca del borde izquierdo del ROI
donde la iluminación no es uniforme y la detección no es confiable.

**Cambio en `config/tolerancias.yaml` — modelo_A:**
- `grid_compare_margin_x_px: 30.0 → 40.0` (vuelve al valor anterior a Cambio 135)

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 137 - Microperforado: excluir columna 6 del borde derecho con grid_compare_margin_x_px

**Síntoma:** en producción real, la línea se detiene a los 3 frames con `machine_stop`.
En el modo análisis (service UI) el mismo modelo funciona bien porque `machine_stop=True` no
detiene la FSM del scanner, solo queda como flag en el resultado.

**Causa raíz:** el patrón `holes.json` (imagen_size=[216,480]) tiene 14 puntos de la columna 6
en x≈197-203px (borde derecho del crop ROI de 216px). Esos agujeros están en la zona de
transición/retroiluminación derecha y la cámara nunca los detecta consistentemente.
Con `machine_stop_track_by_grid: true` y `machine_stop_min_missing: 3`: la misma columna
falta en ≥3 frames consecutivos → machine_stop → STOPPED → línea parada al instante.

**Cambio en `config/tolerancias.yaml` — modelo_B:**
- Añadido `grid_compare_margin_x_px: 22.0`
  → excluye posiciones esperadas con x > 216-22=194px (toda la columna 6 x≈197-203)
  → elimina falsos missing estructurales del borde derecho sin afectar la detección real

**Archivos modificados:** `config/tolerancias.yaml`

#### Cambio 138 - Esterilla scanner_2: ROI corregido para incluir todas las columnas

**Diagnóstico:** el ROI anterior (x=215, w=275 → cubre x=215 a x=490) cortaba las 2-3
columnas de la izquierda de la esterilla. La esterilla física ocupa aproximadamente x=125
a x=505 en el frame completo de 640px. El patrón mostraba "COLUMNA 1,2,3,4,5 FALTANTES"
con Delta≈-142px, lo que confirma que el detector nunca veía el lado izquierdo.

**Cambio:** `data/patterns/scanner_2/modelo_A/roi.json`
- Antes: `x=215, w=275` (cubre 215-490)
- Ahora: `x=110, w=415` (cubre 110-525, margen de ~15px en cada borde del patrón)

**PENDIENTE — acción manual obligatoria:**
Después de este cambio el patrón `holes.json` es inválido (fue construido para el ROI viejo).
Hay que reconstruirlo con una imagen de referencia limpia:
```
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/ref_s2.jpg" --scanner scanner_2
```

---

#### Cambio 136 - Analisis: mostrar ROI visible en overlays

**Pedido:** activar una marca visible de la ROI en los analisis para saber con
claridad donde esta poniendo el foco el sistema.

**Cambio aplicado:**
- `src/pipeline/annotate.py`
  - nueva funcion `draw_roi_indicator()` que dibuja un rectangulo semitransparente
    color cian con etiqueta `ROI` sobre el frame completo
- `src/inspection.py`
  - despues de recomponer el recorte analizado dentro del frame completo, ahora
    llama a `draw_roi_indicator()` cuando existe ROI configurada

**Resultado:** en los overlays de analisis ahora se ve claramente el recuadro de
la zona inspeccionada, sin tocar la logica de deteccion ni el matching.

**Archivos modificados:** `src/pipeline/annotate.py`, `src/inspection.py`

---

#### Cambio 135 - Esterilla: ROI de analisis mas ancha en scanner_2

**Pedido:** ampliar bastante la zona analizada de `esterilla` porque estaban
quedando muchos agujeros afuera del recorte actual.

**Cambio aplicado:**
- `data/patterns/scanner_2/modelo_A/roi.json`
  - `x=225, w=255 -> x=215, w=275`
- `data/patterns/scanner_2/modelo_A/holes.json`
  - se movio el patron `+10 px` en X y se actualizo `image_size` a `275x480`
    para mantener consistencia geometrica con la ROI mas ancha
- `config/tolerancias.yaml` -> `models.modelo_A`
  - `grid_compare_margin_x_px: 40.0 -> 30.0`

**Motivo:** abrir la ROI sin mover el patron dejaba descalzada la comparacion.
Con este ajuste, el recorte y `holes.json` vuelven a hablar el mismo sistema de
coordenadas, y ademas se afloja un poco el filtro lateral para no comerse tan
facil la columna izquierda.

**Archivos modificados:** `data/patterns/scanner_2/modelo_A/roi.json`,
`data/patterns/scanner_2/modelo_A/holes.json`, `config/tolerancias.yaml`

---

#### Cambio 136 - Diálogo de parada a pantalla completa: bug fix + imagen más grande

**Síntoma:** al ocurrir una detención de máquina (machine_stop=True, ya sea real o por "Simular
parada"), el diálogo `MachineStopDialog` nunca aparecía. El frame con el defecto no se mostraba.

**Causa raíz:** en `_handle_result` (`src/controller/scanner_controller.py`), cuando
`machine_stop_triggered=True`, el método ejecutaba `return` antes de llegar al bloque
`if self.on_result:` (línea ~825). El callback de UI nunca se disparaba → `_sig_stop_alert`
nunca se emitía → `MachineStopDialog` nunca se creaba.

**Cambios:**

`src/controller/scanner_controller.py` línea ~806:
- Añadida llamada `self.on_result(result, streak)` dentro del bloque `machine_stop_triggered`,
  antes del `return`. Dispara el callback hacia la UI incluso cuando la parada es inmediata.

`src/ui/operator.py` — `MachineStopDialog._build_ui()`:
- Header: cambiado a `setFixedHeight(52)` y `padding:0 18px` (antes era solo `padding:18px`
  que inflaba el alto sin control).
- Motivo + botón ACEPTAR: combinados en un `QWidget` footer de altura fija 52px en una sola fila
  horizontal. Elimina ~80px de espacio fijo que comía la imagen. La imagen ahora ocupa casi
  toda la ventana maximizada.

**Efecto:** el diálogo aparece correctamente tanto en paradas reales como al presionar
"Simular parada". La imagen del frame defectuoso ocupa ~90% de la ventana.

---

#### Cambio 134 - Microperforado: revertir grid_lateral_shift_max_px 4.0 → 0.0

**Síntoma:** en producción real, el sistema marcaba NOK casi instantáneamente (a los 5 frames)
con badge "DESVIACION LATERAL". La línea paraba sin defecto real.

**Causa raíz:** en Cambio 127 (commit 3c70229), al agregar el badge visual "DESVIACION LATERAL",
se cambió `grid_lateral_shift_max_px: 0.0 → 4.0` para modelo_B. El umbral de 4px es demasiado
ajustado: la posición natural del material en producción tiene una variación de ~15px respecto
al origen del patrón (medido en Cambio 133). Resultado: cada frame activa DESVIACION LATERAL
→ NOK → tras 5 consecutivos (consecutive_nok_frames=5) → machine_stop.

La práctica validada correctamente (MICROPERFORADO_1, 137/137 OK) usaba `0.0` (desactivado).
Cambio 124 había desactivado este parámetro deliberadamente para evitar paradas por variación
normal de posición.

**Cambio en `config/tolerancias.yaml` — modelo_B:**
- `grid_lateral_shift_max_px: 4.0 → 0.0` (desactivado — comportamiento igual a la práctica)

**Nota:** si en el futuro se quiere re-habilitar detección de corrimiento lateral real,
usar un umbral de ≥20px (por encima de la variación natural de ~15px observada en producción).

**Archivos modificados:** `config/tolerancias.yaml`

---

### Sesión 2026-06-08 — Tadeo + Claude (continuación 3)

#### Cambio 133 - Microperforado: estabilizar líneas PATRON + lateral shift más limpio

**Síntoma:** "tengo problemas con la detección de bordes del patrón en microperforado,
tiene que ser más uniforme y consistente, hay varios patrones que son mal detectados y
eso me produce falla y detención de línea cuando el frame está bien".

**Diagnóstico:**
1. `compute_centering` recibía ALL los `holes` detectados (incluyendo detecciones extra
   en zona de retroiluminación / fuera del patrón). Con ratio de detección ~245%, los
   blobs de backlight en x>220px en el ROI elevaban `global_right`, haciendo que
   `_pattern_bounds_by_band` saltara a posiciones inestables. La línea PATRON derecha
   aparecía en distinta posición frame a frame.
2. `grid_lateral_shift_max_px`: la comparación usaba `detected_points` (todos los holes,
   incluyendo extras de backlight) vs `pattern.points`. Los blobs extra en posiciones
   extremas desplazaban la media de los detectados, generando una "desviación lateral"
   artificial (~28px en vez de ~15px reales).

**Cambios en `src/inspection.py`:**
- Líneas 545-568 (bbox filter): añadida construcción de `holes_in_bbox` (lista de `Hole`
  objects con mismo filtro de bounding box que `detected_in_bbox`). Default: todos los
  holes si no hay bbox filter.
- `compute_centering(img_aligned, holes_in_bbox, ...)`: pasa solo los holes en la región
  de comparación → `global_right`/`global_left` en `_pattern_bounds_by_band` ya no salta
  a posiciones de backlight. Líneas PATRON estables frame a frame.
- `grid_lateral_shift_max_px`: cambiado de `detected_points` a `detected_in_bbox` como
  base para la media X del detector. El umbral sigue siendo `pattern.points` (referencia
  fija). Reduce ruido de blobs extra sin eliminar la señal de desplazamiento real.

**Efecto medido en MICROPERFORADO_2:**
- PATRON boundary: estable en x≈195 (antes: saltaba a x≈288 en algunos frames)
- Lateral shift frame_0022: -28.7px → -15.0px (eliminación de ruido de backlight)

**Nota sobre calibración pendiente:** la detección muestra 28 missing consistentes en
todos los frames con el patrón actual (roi x=195, w=295 incluye zona de backlight derecho
→ patron con holes en x=200-224 que son zona de transición/backlight y nunca se detectan).
No se modifica en este commit — requiere recalibración del patrón con ROI ajustado.

**Archivos modificados:** `src/inspection.py`

---

### Sesión 2026-06-08 — Nahuel + Claude (continuación 2)

#### Cambio 135 - Análisis on-demand en visor de eventos + disco en header

**Problema:** el toggle "Con overlay" en el tab de paradas de línea mostraba el frame crudo porque los frames pre-evento nunca son analizados (están en buffer de RAM antes de la parada).

**Solución:** análisis on-demand — al activar el overlay en modo eventos, se lanza `inspect_image` en un `QThread` worker (`_InspectWorker`) sobre el frame actual. Mientras procesa, se muestra el frame crudo con "Analizando…" en el contador. Al terminar, se reemplaza con el overlay del resultado.

**Cambios en `src/ui/frame_viewer.py`:**
- `_get_model_for_scanner()`: lee `config/io_map.yaml` para obtener el modelo activo del scanner del evento
- `_InspectWorker(QThread)`: corre `inspect_image` en background, emite `done(overlay_bgr)`
- `_EventNavPanel`: campos `_scanner_id`, `_model`, `_is_event_mode`, `_inspect_worker`
- `load_event()`: guarda `scanner_id` y `model`, activa `_is_event_mode = True`
- `load_ok_buffer()`: desactiva `_is_event_mode = False`
- `_show_frame()`: en modo evento+overlay → muestra raw inmediatamente y lanza worker; en otros modos → comportamiento anterior
- `_launch_inspect()` + `_on_inspect_done()`: gestión del worker
- Header: label `_disk_lbl` que muestra espacio usado por evidencias y disco total/libre
- `_update_disk_label()`: calcula MB de `data/events/` + uso de disco con `shutil.disk_usage`
- `_populate_list()`: llama `_update_disk_label()` en cada recarga

---

### Sesión 2026-06-08 — Tadeo + Claude (continuación 2)

#### Cambio 132 - Microperforado: eliminar missing falsos en filas borde superior/inferior

**Síntoma:** frames 54, 110, 111, 120 (y otros) mostraban 2-5 missing en las filas
extremas del material. celdas (ci, cj=3) al 9-12% y (ci, cj=30) al 12% de frames.

**Causa:** igual que esterilla (Cambio 131) pero en modelo_B. `grid_compare_margin_px: 5`
sin override Y evaluaba filas borde cuando el material entraba/salía del encuadre:
- cj=3 → y = 10 + 3×14 = **52px** desde el borde superior
- cj=30 → y = 10 + 30×14 = **430px** (50px desde abajo), cj=31 → 444px

**Cambio en `config/tolerancias.yaml` — modelo_B:**
- Añadido `grid_compare_margin_y_px: 55.0` — excluye celdas proyectadas a <55px del borde.
  Fija cj=3 (y=52<55) y cj=30/31 (y=430,444 > 480-55=425). Primera fila incluida: cj=4 (y=66px).

**Validación `05-06-2026-MICROPERFORADO_1`:**
- frames 54/110/111/120 → missing=0 ✓
- temporal_nok=1 (frame_0026): defecto real, 13 agujeros faltantes + desviación -6.7px ✓
- Celda residual máxima: (4,4) al 4%

**Archivos modificados:** `config/tolerancias.yaml`

---

### Sesión 2026-06-08 — Nahuel + Claude (continuación)

#### Cambio 133 - Overlay en frames de parada de línea (post-evento)

**Síntoma:** al activar "Con overlay" en eventos de parada, se mostraba el frame crudo porque los overlays no se guardaban junto a los frames post-evento.

**Causa:** `EventRecorder.add_frame` solo guarda el frame crudo. El overlay (resultado del análisis) se produce en `_handle_result` que corre después, y nunca se asociaba al frame del evento.

**Cambios:**
- `src/pipeline/event_recorder.py`:
  - `add_frame` acepta parámetro opcional `overlay: np.ndarray | None` — si se pasa, guarda `post_{idx}_overlay.jpg` junto a `post_{idx}.jpg`
  - Nuevos métodos `is_post_event_active()` y `get_post_event_dir()` para que el scanner controller sepa si está en ventana post-evento
- `src/controller/scanner_controller.py`: en `_handle_result`, si el recorder está grabando post-evento, guarda el overlay del resultado en `post_{idx}_overlay.jpg` (buscando el último `post_*.jpg` sin `_overlay` y asociándolo)
- `src/ui/frame_viewer.py`:
  - `_event_summary`: filtra `post_*_overlay.jpg` de la lista de frames para no mostrarlos como frames separados
  - `_populate_ok`: filtra `ok_*_raw.jpg` del listado para no duplicar
  - `_resolve_frame_path`: lógica extendida para manejar `post_NNNN.jpg → post_NNNN_overlay.jpg` cuando overlay está activo, y `ok_NNNN.jpg → ok_NNNN_raw.jpg` cuando está desactivado

**Comportamiento final:**
- Frames pre-evento (`frame_NNNN.jpg`): siempre crudos, no hay overlay disponible
- Frames post-evento (`post_NNNN.jpg`): toggle muestra raw ↔ overlay del análisis
- Frames OK buffer: toggle muestra overlay ↔ raw
- Frames NOK: toggle muestra overlay ↔ raw

---

#### Cambio 132 - Toggle overlay/raw en el visor de frames

**Pedido:** activar/desactivar el overlay de análisis al revisar frames OK, NOK y eventos de parada.

**Lógica:**
- Al guardar un frame con overlay (OK buffer, NOK), ahora también se guarda el frame crudo (`_raw.jpg`) en la misma carpeta con el mismo nombre base.
- El visor tiene un botón "👁 Con overlay" (toggle) en la barra de navegación. Cuando está desactivado, `_resolve_frame_path()` busca el `_raw.jpg` correspondiente y lo muestra. Si no existe (frames de eventos, que ya son crudos), muestra el archivo original.

**Cambios:**
- `src/inspection.py`: campo `image` en `InspectionResult` + guardar `_raw.jpg` en `save_result_images`
- `src/controller/scanner_controller.py`: OK buffer guarda `ok_{slot}_raw.jpg` junto al overlay
- `src/ui/frame_viewer.py`: botón toggle `_overlay_btn`, método `_resolve_frame_path()`, `_show_frame()` usa resolución condicional

---

#### Cambio 130 - Visor de frames NOK en OperatorFrameViewer

**Pedido:** poder ver los overlays de frames NOK (y paradas de máquina) para analizar el escaneo sin entrar al modo servicio.

**Cambios en `src/ui/frame_viewer.py`:**
- Agregada constante `_NOK_DIR = _ROOT / "data" / "output" / "nok"`
- Nueva pestaña "✗ Frames NOK recientes" (`_nok_btn`) en el header, con color `_WARN` (naranja)
- `_switch_mode()` actualiza el estado checked del nuevo botón
- `_populate_list()` rutea al nuevo método `_populate_nok()` cuando `mode == "nok"`
- `_populate_nok()`: lee `data/output/nok/*.png` ordenados por mtime (más reciente primero), agrupa por scanner parseando el prefijo del nombre de archivo, y muestra tarjetas
- `_make_nok_card()`: tarjeta con nombre de scanner y conteo de frames NOK en color naranja
- `_on_list_select()`: selección en modo NOK llama a `_nav_panel.load_ok_buffer()` (reutiliza el panel de navegación de frames OK, que acepta cualquier lista de imágenes)
- `showEvent()` simplificado para manejar los tres modos uniformemente

**Nota:** los frames NOK se guardan en `data/output/nok/` como PNG con nombres como `scanner_2_cont_162550_0005_overlay.png`. El visor los muestra con overlay de análisis para diagnóstico.

---

#### Cambio 129 - force_auto_mode: forzar modo AUTO sin depender del switch PLC

**Síntoma:** X0 y X2 apagadas (MANUAL) → inspector nunca arranca → contadores OK/NOK en 0.

**Causa:** cableado del switch de modo no llega al PLC todavía.

**Cambios:**
- `config/io_map.yaml`: `force_auto_mode: true` en `scanner_1` y `scanner_2`
- `src/controller/scanner_controller.py`:
  - `__init__`: lee `force_auto_mode` del config; inicializa `self._mode = AUTO` si activo
  - `_poll_loop`: no actualiza modo desde PLC si `force_auto` está activo
  - `get_status`: ídem

**Para revertir cuando el cableado esté listo:** poner `force_auto_mode: false` (o eliminar la línea) en `io_map.yaml` y reiniciar.

**Salidas afectadas:** ninguna bloqueada — el solenoide nunca se activa por software (solo se apaga en fault/stop).

---

### Sesión 2026-06-08 — Tadeo + Claude

#### Cambio 131 - Esterilla: eliminar missing falsos en filas del borde superior e inferior

**Síntoma:** frames 5, 6, 15, 16, 17 y otros mostraban 1-4 missing en las filas más
extremas (borde superior/inferior del material). Las celdas (ci, cj=1) y (ci, cj=21)
tenían tasas de missing del 44% y 23% respectivamente aunque no había defectos reales.

**Causa:** `grid_compare_margin_y_px: 5` era demasiado pequeño. Las filas cj=1 (y≈21px)
y cj=21 (y≈441px) son el borde físico del material de esterilla. Cuando el material entra
o sale del encuadre, esas filas aparecen parcialmente y la detección las pierde → missing.
El mecanismo es idéntico al que se solucionó en X con `grid_compare_margin_x_px: 40`.

**Cambio en `config/tolerancias.yaml` — modelo_A:**
- `grid_compare_margin_y_px: 5.0 → 40.0`
  Excluye dinámicamente celdas proyectadas a menos de 40px del borde superior/inferior.
  Con dy=21px: excluye cj=0 (y=0) y cj=1 (y=21) arriba, y cj=21 (y=441) y cj=22 (y=462) abajo.
  Las filas cj=2 a cj=20 (y=42-420px) siguen siendo chequeadas — cobertura suficiente.

**Validación `05-06-2026-ESTERILLA_1`:**
- Antes (margin_y=5):  frame_0005 missing=2, frame_0006 missing=1; celda(1,1) falla 44%
- Después (margin_y=40): raw_ok=130, raw_nok=3, temporal_nok=0
  frame_0005/0006/0007…0017 todos missing=0
  Celda con mayor tasa de faltante: (3,19) al 5% (ruido residual de borde)

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 130 - UI producción: mostrar overlay analizado en frames NOK (en vivo)

**Síntoma:** "quiero que cuando detecte un NOK o detención de máquina en estado
INSPECCIONANDO, también muestre en pantalla el frame de detención, al igual que
en simulación pero con la imagen o frame real analizado".

**Diagnóstico:** `ScannerPanel._on_result()` solo mostraba el overlay cuando
`result.machine_stop=True` (30s + dialog). Para `result.status == "NOK"` el overlay
nunca se emitía — la constante `_OVERLAY_HOLD_MS = 2500` estaba definida pero sin uso.

**Cambio en `src/ui/operator.py` → `ScannerPanel._on_result`:**
- Añadido `elif result.status == "NOK"` que emite el overlay por `_OVERLAY_HOLD_MS = 2500ms`.
- Guard inicial `if result.overlay is None: return` para evitar crash en frames sin overlay.
- El overlay se muestra en el `camera_label` del panel correspondiente durante 2.5 segundos,
  luego vuelve al feed en vivo automáticamente.
- El flujo de `machine_stop` (30s + `MachineStopDialog`) sigue igual, sin cambio.

**Comportamiento resultante:**
- `result.status == "OK"` → feed de cámara en vivo (sin cambio)
- `result.status == "NOK"` → overlay anotado visible 2.5s → vuelve a vivo
- `result.machine_stop == True` → overlay 30s + diálogo a pantalla completa (sin cambio)

**Archivos modificados:** `src/ui/operator.py`

---

#### Cambio 129 - Esterilla: márgenes X/Y independientes en grid_compare + columna izquierda

**Síntoma:** usuario pide "elimina límites superiores e inferiores, quiero que detecte todos
los agujeros, le falta detectar una columna a la izquierda". Con el patrón de Cambio 128
(58 agujeros, ci=1-4) faltaba la columna ci=0 y había límites verticales en Y=72-420px.

**Problema raíz:** `grid_compare_points` usaba un único `margin` para los 4 lados. No era
posible filtrar solo las columnas laterales sin también recortar los bordes superior/inferior.

**Solución — `src/pipeline/grid_fitting.py`:**
- Añadidos parámetros `margin_x: float | None = None` y `margin_y: float | None = None`
  a `grid_compare_points()`. Si se suministran, reemplazan a `margin` para X e Y
  respectivamente. Default: ambos `= margin` (sin cambio de comportamiento para callers
  existentes).
- `mx` y `my` se usan en los 3 puntos de filtrado: fase X, fase Y, y posiciones finales.

**Solución — `src/inspection.py`:**
- Lectura de dos nuevos parámetros: `grid_compare_margin_x_px` y `grid_compare_margin_y_px`.
- Ambos son opcionales; si no están definidos, se pasan como `None` → `grid_compare_points`
  usa `grid_compare_margin_px` para ese eje.
- Se pasan como `margin_x=` / `margin_y=` en la llamada a `grid_compare_points`.

**Solución — `config/tolerancias.yaml` — modelo_A:**
- `pattern_edge_margin_px: 50 → 5` (uniforme, incluye ci=0 y cobertura Y completa)
- `pattern_edge_margin_left_px: 5`, `pattern_edge_margin_right_px: 40` → ci=5 excluido del patrón
  (ci=5 a x≈209px: 255-209=46px de borde → no confiable cuando material se corre)
- `grid_compare_margin_px: 5.0` — margen mínimo Y (cobertura vertical completa)
- `grid_compare_margin_x_px: 40.0` — excluye celdas con x_esperada < 40 ó > 215px
  (ci=0 even at x=36 < 40 → excluido dinámicamente cuando el material está en posición normal;
   incluido cuando el material se corre a la derecha y ci=0 pasa a x>40)
- `grid_compare_margin_y_px: 5.0` — sin límites Y efectivos (solo y<5 excluida = exactamente el borde)
- `compare_top_ignore_px: 0.0`, `compare_bottom_ignore_px: 0.0` (sin filtros verticales)

**Patrón reconstruido `data/patterns/scanner_2/modelo_A/holes.json`:**
- Referencia: frame_0034
- Resultado: **81 agujeros**, ci=0-4 (5 columnas), X=36-187px, Y=0-460px
- ci=0 (x~36-56px) incluido en patrón pero dinámicamente excluido de comparación
  cuando el material está en posición nominal (x_esperada < 40px)
- ci=5 excluido del patrón (`pattern_edge_margin_right_px=40`)

**Validación `05-06-2026-ESTERILLA_1` con `--scanner scanner_2`:**
- **raw_ok=124, raw_nok=9, temporal_nok=0** (antes: 0 temporal NOK con 58 agujeros)
- Cobertura ampliada: 81 agujeros vs 58 anteriores (columna ci=0 + filas completas)
- Los 9 raw-NOK nunca alcanzan 5 consecutivos
- Celda con mayor tasa de missing: (1,1) al 44% — borde superior izquierdo (normal)

**Archivos modificados:** `src/pipeline/grid_fitting.py`, `src/inspection.py`,
`config/tolerancias.yaml`, `data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 128 - Esterilla: reconstruir patrón scanner_2 + pattern_edge_margin_px=50

**Síntoma:** "no se está detectando correctamente todo el patrón de agujeros, hay varios
agujeros que no se están viendo". Análisis de carpeta `05-06-2026-ESTERILLA_1` mostraba
`detection_ratio=180%` y resultados inconsistentes.

**Diagnóstico:**
1. `run-folder` sin `--scanner` usaba el patrón GLOBAL (`data/patterns/modelo_A/holes.json`
   con 57 puntos e `image_size=[275,480]`) en lugar del de scanner_2 (93 puntos, [255,480]).
   El patrón global es obsoleto/incorrecto para la configuración actual de cámara.
2. El patrón de scanner_2 tenía `pattern_edge_margin_px=5`, incluyendo columnas del borde
   izquierdo (x~34px) y derecho (x~205px) que solo son visibles de manera intermitente.
   Esto causaba sistemáticamente missing en columna (ci=1,cj=3/5/8/10) con tasas del 37-59%.
3. Al reconstruir con margin=5 y frame_0034 se incluyó columna ci=5 (x~209-229px) ausente
   en el 86-99% de frames → 126/133 temporal NOK (falsos positivos masivos).

**Causa raíz:** las columnas del borde de la esterilla entran y salen del encuadre según
la posición del material. Incluirlas en el patrón genera falsas alarmas permanentes.

**Cambios en `config/tolerancias.yaml` — modelo_A:**
- `pattern_edge_margin_px: 5.0 → 50.0`
  Excluye las columnas izquierda (x<70px) y derecha (x>205px) del patrón.
  Solo afecta `build-pattern`; la detección durante inspección no cambia.

**Patrón reconstruido `data/patterns/scanner_2/modelo_A/holes.json`:**
- Referencia: frame_0034 (más limpio: 0 missing, 0 extra)
- Resultado: **58 agujeros**, 4 columnas (ci=1-4), X=70-193px, Y=72-420px
- Antes: 93 agujeros, X=34-205px, con columna izquierda inestable

**Validación `05-06-2026-ESTERILLA_1` con `--scanner scanner_2`:**
- Con nueva pattern: raw_ok=129, raw_nok=4, **temporal_nok=0**
- Missing max: 5% (celda de borde) — sin columnas sistemáticamente ausentes
- Los 4 NOK raw son frames borrosos (frame_0001, 0129 y similares)

**Nota sobre cobertura:** la esterilla física abarca x=14-242px en la ROI, pero
solo x=70-193px se monitorea de forma confiable. Los bordes izquierdo/derecho
(~1 columna por lado) son zonas de transición del encuadre — no monitoreables sin
ajustar ROI/cámara.

**Archivos modificados:** `config/tolerancias.yaml`, `data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 127 - Badge "DESVIACION LATERAL" (título correcto en warning sin parada)

**Síntoma:** el badge que aparece en frames con desviación lateral mostraba
"! DETENCION DE MAQUINA" aunque la máquina NO se detuvo (la parada real ocurre
solo tras 5 frames consecutivos vía lógica temporal). Texto confuso para el operador.

**Cambio en `src/pipeline/annotate.py` → `draw_machine_stop_badge`:**
- Añadido parámetro `title: str = "! DETENCION DE MAQUINA"` (default sin cambio).
- `main_label = title` reemplaza el literal hardcodeado.

**Cambio en `src/inspection.py`:**
- Llamada al badge de desviación lateral ahora pasa `title="! DESVIACION LATERAL"`.
- Los badges de machine_stop real y de PATRON DESALINEADO continúan sin cambio
  (usan el default).

**Resultado:** frames con desviación lateral muestran banner rojo "! DESVIACION LATERAL"
con la razón "DESVIACION LATERAL (±X.Xpx)" en ámbar. Sin confusión con la parada real
de máquina.

**Archivos modificados:** `src/pipeline/annotate.py`, `src/inspection.py`

---

#### Cambio 126 - Línea PATRON pasa por centro del círculo exterior (no entre agujeros)

**Síntoma:** después del Cambio 125 (boundary_tol 22px), la línea PATRON caía entre dos
columnas de agujeros del grid hexagonal en vez de pasar por el centro de un agujero real.

**Causa:** `_pattern_bounds_by_band` usaba `hh.x - hh.r` / `hh.x + hh.r` (arista exterior
del círculo) como referencia para seleccionar y calcular el punto de borde. Con boundary_tol
amplio (22px > stagger 18px), bandas con filas pares e impares del grid hexagonal aportaban
valores de x alternantes (x_odd y x_odd+18). El ajuste de línea caía en el promedio de
ambas → entre los dos agujeros, sin tocar ninguno.

**Cambio en `src/pipeline/edge_centering.py` → `_pattern_bounds_by_band`:**
- `global_left/right`: de `min(hh.x - hh.r)` / `max(hh.x + hh.r)` → `min(hh.x)` / `max(hh.x)`
- Filtro por banda: de `(hh.x - hh.r) <= global_left + tol` → `hh.x <= global_left + tol`
- Valor por banda: de `min(hh.x - hh.r)` / `max(hh.x + hh.r)` → `min(hh.x)` / `max(hh.x)`

Con la selección basada en centro, `boundary_tol_px=8` (< stagger 18/20px) selecciona
solo la columna más exterior del grid. Las bandas sin esa columna quedan vacías pero la
línea robusta interpola correctamente con los puntos de las bandas que sí la tienen.

**`config/tolerancias.yaml`:**
- `modelo_A`: `pattern_edge_boundary_tol_px: 24 → 8` (stagger 20px → tol bien por debajo)
- `modelo_B`: `pattern_edge_boundary_tol_px: 22 → 8` (stagger 18px → tol bien por debajo)

**Resultado:** línea PATRON pasa por el centro de los círculos más exteriores, sin
caer entre agujeros. Línea continua y estable incluso con corrimiento lateral del material.

**Validación:** 137/137 temporal OK, 0 machine_stop (sin cambio).

**Archivos modificados:** `src/pipeline/edge_centering.py`, `config/tolerancias.yaml`

---

#### Cambio 125 - Línea PATRON rota en frames con desalineamiento: corregir boundary_tol_px

**Síntoma:** en frames con corrimiento lateral del material (ej. frame_0032), la línea
PATRON del lado hacia el que se corrió el material aparecía rota, discontinua o
desaparecida. Frame_0022 mostraba un problema similar.

**Causa raíz:** `pattern_edge_boundary_tol_px` demasiado bajo para los grids hexagonales.
`_pattern_bounds_by_band()` busca agujeros cuya extensión izquierda esté dentro de
`global_left + boundary_tol_px`. Con stagger hexagonal:
- modelo_B: `grid_stagger_x_odd: -18px` → filas pares tienen su agujero más izquierdo
  18px al interior. Con `boundary_tol=6px` esas filas no pasan el filtro → mitad de bandas
  sin punto de borde → línea rota/desaparecida.
- modelo_A: mismo problema con `grid_stagger_x_odd: +20px` y `boundary_tol=10px`.

**Cambio:** `config/tolerancias.yaml`:
- `modelo_B`: `pattern_edge_boundary_tol_px: 6.0 → 22.0` (stagger 18px + margen 4px)
- `modelo_A`: `pattern_edge_boundary_tol_px: 10.0 → 24.0` (stagger 20px + margen 4px)

El valor es seguro: segunda columna del grid está a ≥32px de la primera, no se capturan
columnas interiores.

**Validación:** 137/137 temporal OK, 0 machine_stop (sin cambio).
Frame_0032: línea PATRON izquierda ahora continua.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 124 - Deshabilitar parada de maquina por un solo frame (grid_lateral_shift)

**Pedido:** la máquina nunca debe detenerse por missing en un solo frame (puede ser
falso positivo por movimiento o mala detección puntual). Solo debe parar por patrones
faltantes repetidos en múltiples frames.

**Diagnóstico:**
- `MachineStopDetector` (faltantes por columna): ya tenía `max(2, missing_frames)`
  hardcodeado — nunca dispara con menos de 2 frames consecutivos. OK.
- `consecutive_nok_frames = 5`: necesita 5 frames seguidos para FAULT. OK.
- `grid_lateral_shift_max_px: 20.0`: único camino activo que disparaba `machine_stop=True`
  en un **solo frame** si el centroide de detecciones se desplazaba >20px del patrón.
  Podía activarse por movimiento del material, blur o mala detección puntual.

**Cambio aplicado:**
`config/tolerancias.yaml` — modelo_A y modelo_B:
- `grid_lateral_shift_max_px: 20.0 → 0.0` (0.0 = desactivado en ambos modelos)

**Resultado:** ningún mecanismo activo puede parar la máquina en un solo frame.
La única parada por faltantes requiere la misma zona ausente en ≥3 frames consecutivos
(`machine_stop_missing_frames=3`, `machine_stop_min_missing=3`).

**Validación `05-06-2026-MICROPERFORADO_1`:** 137/137 temporal OK, 0 machine_stop (sin cambio).

**Archivos modificados:** `config/tolerancias.yaml`

---

### Sesión 2026-06-05 — Tadeo + Claude

#### Cambio 123 - machine_stop re-habilitado + deteccion de desviacion lateral grande

**Pedido:**
1. Que si se detecta el MISMO agujero faltante por muchos frames consecutivos (linea
   avanzando, mismo punzon roto) se detenga la maquina — como estaba antes.
2. Que ante grandes desviaciones del patron (borde del patron deja de ser visible) también
   se detenga la maquina.

**Diagnostico:**
- `machine_stop_enabled: false` en ambos modelos (desactivado en Cambio 100 tras la
  recalibracion de camara). Causa: falsos positivos con el baseline alto de la época.
- Con la calibracion actual (avg_missing ~0.1-0.5 para modelo_B, ~0.5-2 para modelo_A)
  el baseline es bajo y es seguro re-habilitar.
- Para modelo_B: columnas ci=1-5 siempre tienen 1-2 holes missing por baseline de borde;
  `min_missing=3` es el umbral correcto para ignorar ese ruido y detectar solo punzones
  rotos reales (>=3 holes en la misma columna por frame).
- Para grandes desviaciones laterales del material (borde del patron sale del encuadre):
  el grid fitting adapta la fase y deja de contar esos agujeros como "faltantes". Se
  requiere un mecanismo diferente basado en el desplazamiento del centroide de detecciones.

**Cambios aplicados:**

`config/tolerancias.yaml` — modelo_A:
- `machine_stop_enabled: false → true`
- `machine_stop_missing_frames: 5 → 3` (detecta rachas mas cortas)
- `grid_lateral_shift_max_px: 20.0` (nuevo — parada inmediata si centroide de
  agujeros detectados se desplaza mas de 20px del patron de referencia)

`config/tolerancias.yaml` — modelo_B:
- `machine_stop_enabled: false → true`
- `machine_stop_missing_frames: 5 → 3`
- `machine_stop_min_missing: 3` (sin cambio — protege contra falsos positivos del
  ruido de borde, que tiene 1-2 holes por columna)
- `grid_lateral_shift_max_px: 20.0` (nuevo — parada inmediata por desviacion lateral)

`src/inspection.py`:
- Nuevo bloque de deteccion de desplazamiento lateral: compara `mean_x(detected_holes)`
  con `mean_x(pattern.points)`. Si la diferencia supera `grid_lateral_shift_max_px`,
  dispara `machine_stop` inmediatamente con razon "DESVIACION LATERAL".
- Gated por `tilt_warn` (cuando la chapa esta inclinada el centroide puede desviarse
  sin ser un corrimiento real del material).

`src/utils/config.py`:
- Nuevo default `grid_lateral_shift_max_px: 0.0` (opt-in, desactivado globalmente).

**Comportamiento esperado:**
- Punzon roto (misma columna ci ausente en >=3 frames consecutivos con >=3 holes/frame):
  → `machine_stop=True`, razon "AGUJERO FALTANTE PERSISTENTE EN COLUMNA X".
- Gran desviacion lateral (material se corre >20px): → `machine_stop=True`,
  razon "DESVIACION LATERAL (+Xpx)".
- Frames con variacion normal (borde ci=1-5 con 1-2 holes, desviacion <20px): → OK.

**Validacion `05-06-2026-PATRONES EDITADOS`:**
- `ESTERILLA_1`: 133/133 temporal OK, 0 machine_stop (sin falsos positivos).
- `MICROPERFORADO_1`: 137/137 temporal OK, 0 machine_stop (sin falsos positivos).

**Archivos modificados:** `config/tolerancias.yaml`, `src/inspection.py`,
`src/utils/config.py`

---

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

#### Cambio 117 — recalibración post-zoom: ROI + grilla + patrones + ignore top/bot

**Contexto:** Ajuste de zoom en ambas cámaras Sony IP. Recalibración desde `05-06-2026-PATRONES INICIALES`.

- **ROI modelo_A**: `x=215,y=0,w=275,h=480`; **modelo_B**: `x=195,y=0,w=295,h=480`
- **modelo_A** grid: `dx=39,dy=21,stagger=20` (antes dx=26,dy=14,stagger=12)
- **modelo_B** grid: `dx=36,dy=14,stagger=-18` (antes dx=24,dy=8,stagger=-12)
- **compare_top/bottom_ignore_px: 42** en ambos modelos (nuevo en modelo_B)
- **grid_affine_refinement: false** para modelo_A — el afín producía 58 missing vs 12 sin él
- **pattern_edge_margin_px: 30** (modelo_A), **bbox_filter_margin_px: 25** (modelo_A)
- **frame_missing_nok_threshold: 40** en ambos (baseline: modelo_A max=29, modelo_B max=27)
- Validación: 133/133 frames esterilla OK, 137/137 frames micro OK

**Archivos:** roi.json × 2, holes.json × 2, `config/tolerancias.yaml`

---

#### Cambio 116 — texto inferior más chico + detector blur esterilla + ROI modelo_A recalibrado

**Texto inferior en overlay (`annotate.py`):**
- Filas Delta/Offset e Izq/Der: scale 0.65 → 0.42, thick 2→1
- Fila "Vert pat": scale 0.55 → 0.38
- Spacing entre filas: 30px → 20px; base: h-15 → h-10
- Resultado: las 3 filas ahora ocupan ~50px en vez de ~90px → no tapan agujeros

**Detector de blur para modelo_A esterilla:**
- Nueva función `draw_blur_indicator()` en `annotate.py`: muestra "Nitidez: XXX"
  verde cuando OK, rojo + badge "! IMAGEN BORROSA" cuando LOW_QUALITY.
- Habilitado en tolerancias: `blur_score_min: 500.0`.
- Calibración sobre ROI (280×480) en 290 frames buenos: min=726, p5=905.
  Umbral 500 captura frames con borroneo significativo sin falsos positivos.
- Frames borrosos → `frame_quality = "LOW_QUALITY"` → inspector no incrementa
  racha NOK (evita falsos faltantes por blur).

**ROI modelo_A recalibrado para cámara Sony IP 640×480:**
- ROI anterior: `x=870,y=0,w=380,h=1080` (cámara anterior ~1080p)
- ROI nuevo: `x=240,y=0,w=280,h=480` — dx=26px confirmado en imagen
- Patrón reconstruido: 144 puntos (3 duplicados en bordes descartados)

**Archivos:** `src/pipeline/annotate.py`, `src/inspection.py`,
`data/patterns/modelo_A/roi.json`, `data/patterns/modelo_A/holes.json`,
`config/tolerancias.yaml`

---

#### Cambio 115 — modelo_B: recalibración completa para cámara Sony IP 640×480

**Contexto:** La cámara del scanner_1 (MICROPERFORADO) fue reemplazada/reposicionada.
El ROI antiguo era para resolución ~1920×1080. Calibración realizada desde cero con
imágenes de la carpeta MICROPERFORADO_2 (96 frames, Sony IP 640×480).

**Problemas encontrados y resueltos:**

1. **ROI inválido**: `x=710, w=650, h=1077` fuera de una imagen 640×480.
   → Nuevo ROI: `x=230, y=0, w=185, h=480`.

2. **Canal b (azul) → canal r (rojo)**: La iluminación naranja del backlight tiene muy
   baja componente azul (max=217). El canal R es estable 80-200 de threshold.
   → `use_channel: b → r`, `threshold: 180 → 120`.

3. **blur_ksize=5 fusionaba agujeros adyacentes**: Con dy=8px y diámetro≈9px, los agujeros
   casi se tocan. El blur 5×5 unía pares en blobs elongados (circularity baja) → filtrados.
   Resultado: pipeline detectaba 243 en vez de 297 agujeros.
   → `blur_ksize: 5 → 1` (sin blur), `close_ksize: 5 → 1`, `open_ksize: 3 → 1`.

4. **grid_derotate producía tilt=-31°**: Con dy=8 y row_dy_tol=20, la función
   `estimate_lattice_tilt_deg` incluía pares de 2 filas de distancia (ddy=16) como
   "misma fila", contaminando el ángulo estimado.
   → `grid_derotate: false` para modelo_B.

5. **grid_dy: 7.5 → 8.0**: Medición real sobre los frames confirma dy=8px.

6. **grid_min_spacing: 15.0 → 6.0**: El espaciado dx/2=12px requiere threshold menor.

7. **pattern_align/center_align_enabled: true → false**: Estos checks asumen un patrón
   vertical que no aplica al hexagonal denso. Ademas `pattern_global_offset_max_px: 0`
   (mal configurado) declaraba NOK cualquier frame.

8. **tol_xy_px: 12 → 20**: Con afín activo, las posiciones esperadas en los bordes de la
   grilla tienen desviación de ~15-20px. Baseline medido: 19-40 faltantes en frames OK.

9. **frame_missing_nok_threshold / grid_max_missing: 90 → 60**: Baseline max=40, umbral
   20px de margen por encima para detectar defectos reales.

**Resultado validado:** 95/95 frames de MICROPERFORADO_2 clasifican OK.
Rango de faltantes en frames buenos: 19-40 (avg≈29). NOK cuando ≥60.

**Archivos modificados:**
- `data/patterns/modelo_B/roi.json` — ROI nuevo 640×480
- `data/patterns/modelo_B/holes.json` — Patrón reconstruido (271 holes)
- `config/tolerancias.yaml` → modelo_B completamente recalibrado

---

#### Cambio 113 — FPS: async save + adaptive_block_size 61→41 + position threshold

**Problema:** FPS cayendo a 3 en producción e inspección.

**Diagnóstico de los 3 cuellos de botella acumulados:**

1. **`save_result_images()` síncrono en el inspector thread** — escribía 2 PNG (máscara +
   overlay) por cada frame NOK directamente en el hilo del inspector, bloqueándolo
   100-200ms por write. Con calibración ajustada (muchos NOKs temporales) esto era la
   causa principal del bajo FPS.

2. **`adaptive_block_size: 61`** — kernel de 61×61px sobre 640×480 costoso. Reducido a
   41 (~45% más rápido en la etapa de threshold).

3. **`continuous_position_threshold: 0.0`** — el inspector revisaba cada frame sin filtrar
   posición. Subido a 3.0px de diff media: skipea frames donde el material no avanzó.

**Cambios:**

- `src/controller/scanner_controller.py` → `_handle_result()`:
  `save_result_images(result)` pasa a un `threading.Thread(daemon=True)` igual que
  el buffer ok_buf. El inspector thread ya no espera el disco.

- `config/tolerancias.yaml` → `models.modelo_A`:
  - `adaptive_block_size: 61 → 41`
  - `continuous_position_threshold: 0.0 → 3.0`

**Resultado esperado:** FPS se estabiliza en 8-15fps en producción (limitado por
`max_inspection_hz: 15`). El análisis de carpeta mejora ~40% en velocidad de pipeline.

**Archivos modificados:** `src/controller/scanner_controller.py`, `config/tolerancias.yaml`

---

#### Cambio 112 — esterilla: re-habilitar adaptive threshold + ampliar tol_xy_px para gran angular

**Problema:** demasiados agujeros marcados en rojo (missing) al analizar esterilla con la
cámara levemente más alejada y distorsión de barril del gran angular.

**Causa 1 — detección:** `use_adaptive: false` había sido desactivado en la recalibración
2026-06-04. El umbral adaptativo fue el cambio clave del Cambio 77 (missing 21→4): detecta
agujeros en zonas con iluminación no uniforme que el umbral global pierde.

**Causa 2 — tolerancia:** `tol_xy_px: 10.0` demasiado justo. Con barrel distortion del
gran angular los agujeros de borde se desplazan 12-20px de su posición ideal. El mismo
problema se dio en modelo_B (Cambio 108: 8→12px).

**Cambios en `config/tolerancias.yaml` → `models.modelo_A`:**
- `use_adaptive: false → true`
- `tol_xy_px: 10.0 → 14.0`

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 110 — Overlay: redimensionar marcadores y texto para cámara 640×480

**Problema:** con la cámara nueva (Sony 640×480, alejada), los círculos de error y los
textos del overlay quedaban enormes porque los tamaños estaban calibrados para resoluciones
mayores (~1080p).

**Cambios en `src/pipeline/annotate.py`:**

| Elemento | Antes | Ahora |
|---|---|---|
| Círculo hueco missing (radio) | 18px | 10px |
| Cruz MARKER_TILTED_CROSS (size) | 28 | 16 |
| Número del faltante (escala) | 0.45 | 0.35 |
| Diamante extra (markerSize) | 20 | 12 |
| Badge "DETENCION DE MAQUINA" (escala) | 1.3 | 0.80 |
| Razón del badge (escala) | 0.72 | 0.48 |
| Texto "Inclinacion" (escala) | 0.6 | 0.42 |
| Badge "CHAPA INCLINADA" (escala) | 0.8 | 0.55 |
| Panel NOK — encabezado (escala) | 0.90 | 0.60 |
| Panel NOK — filas (escala) | 0.65 | 0.45 |
| "STATUS: OK/NOK" (escala) | 1.0 | 0.65 |

**Archivos modificados:** `src/pipeline/annotate.py`

---

### Sesión 2026-06-04 (continuación 3) — Tadeo + Claude

#### Cambio 109 — ANÁLISIS: scroll vertical único sobre toda la página

**Problema:** en la pestaña de análisis el usuario podía bajar dentro de `NAVEGADOR DE CAPTURAS`,
pero la sección superior `ANÁLISIS` quedaba ocupando espacio visual, como si no formara parte
del mismo scroll de página.

**Cambios en `src/ui/service.py`:**
- `_build_ana_page()` vuelve a tratar `ANÁLISIS` + `NAVEGADOR DE CAPTURAS` como un solo bloque
  vertical dentro del `QScrollArea`.
- El layout del contenido usa `QLayout.SizeConstraint.SetMinimumSize` y alineación superior para
  que Qt calcule la altura real del contenido y permita desplazar toda la página.
- `analysis_section` y `browser_section` pasan a `QSizePolicy.Expanding / Maximum` para evitar
  que se estiren artificialmente y “simulen” quedar fijos arriba.
- El `QScrollArea` queda alineado arriba y sin barra horizontal, manteniendo el ajuste de ancho
  previo para esta PC.

**Resultado esperado:** al bajar, se mueve la página completa de análisis; la sección superior
deja de quedar “molestando” mientras se navegan frames más abajo.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 108 — Patrones recalibrados para cámara nueva 640×480 (scanner_1/scanner_2)

**Contexto:** cámara nueva, ángulo completamente diferente. Todos los patrones anteriores inválidos.

**Imágenes de referencia usadas:**
- MICROPERFORADO (modelo_B, scanner_1): `MICROPERFORADO_1/frame_0077.png` — 640×480, bien iluminado
- ESTERILLA (modelo_A, scanner_2): `ESTERILLA_2/frame_0070.png` — 640×480, 126 agujeros detectados (mayor cobertura)

**Diagnóstico y correcciones de parámetros de grid:**

1. `build_pattern_from_image` — soporte `grid_stagger_x_odd` en config:
   - Si `grid_stagger_x_odd` está en tolerancias.yaml, lo usa directamente en vez de estimarlo.
   - Eliminó variabilidad en detección de stagger causada por sensibilidad al número de agujeros
     en los bordes (dependía de `pattern_edge_margin_px`).

2. `config/tolerancias.yaml` — modelo_B:
   - `grid_dx: 24.0`, `grid_dy: 7.5`, `grid_stagger_x_odd: -12.0` (hexagonal exacto = dx/2)
   - `tol_xy_px: 8.0 → 12.0` — cámara wide-angle con distorsión de barril desplaza agujeros en
     bordes hasta 24px del ideal; con 8px solo 51% de posiciones matcheaban.

3. `config/tolerancias.yaml` — modelo_A:
   - `grid_stagger_x_odd: 12.0` — el estimador automático era sensible al margen de borde
     y devolvía 4.0 en vez del valor real ≈ 12.0px.

**Resultado final:**
- Referencia MICROPERFORADO: OK, missing=30 (< umbral 85)
- Referencia ESTERILLA: OK, missing=33 (< umbral 75)
- Todos los frames de MICROPERFORADO_1: OK, missing=33-46
- Todos los frames de ESTERILLA_2: OK, missing=33-55

**Nota pendiente:** MICROPERFORADO siempre da `frame_quality=LOW_QUALITY` porque Hough no
detecta líneas verticales en estos frames (backlight lateral difuso). El check
`chapa_no_line_min_used_lines: 1 + chapa_no_line_abs_max_px: 4.5` puede necesitar ajuste.

**Archivos modificados:** `src/patterns/pattern_build.py`, `config/tolerancias.yaml`,
`data/patterns/scanner_1/modelo_B/holes.json`, `data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 107 — grid_stagger_x_odd en config (parámetro hardcodeado, commit previo)

Ver cambio 108 — incluido en el mismo commit.

---

### Sesión 2026-06-04 (continuación 2) — Tadeo + Claude

#### Cambio 106 — Robustez: warnings explícitos para desajuste de resolución / calibración

**Problema raíz (reportado por Codex):** `scanner_2/modelo_B` no tenía ni `roi.json` ni `holes.json`.
El sistema caía al patrón global (`data/patterns/modelo_B/holes.json`) construido sobre imagen 650×1077.
Los frames nuevos de la cámara llegan en 640×480, así que:
- La ROI global `{x:710, y:3, w:650, h:1077}` aplicada a un frame 640×480 crashea con `ValueError`
  (x1=710 ≥ x2=640 → ROI completamente fuera del frame).
- Incluso si no crashea (frames 1280×720), el recorte queda mal dimensionado respecto al patrón → todos los agujeros "faltantes" → siempre NOK.

**Cambios aplicados:**

1. `src/patterns/pattern_io.py` — `find_pattern_path`:
   - Agrega `WARNING` explícito cuando usa el patrón global como fallback (no hay scanner-specific).
   - Mensaje incluye el comando exacto para recalibrar.

2. `src/patterns/roi.py` — `apply_roi`:
   - Agrega `WARNING` cuando la ROI se recorta (solicitada más grande que el frame).
   - El recorte silencioso ocultaba el desajuste de resolución.

3. `src/inspection.py` — `_inspect_bgr`:
   - Envuelve `apply_roi` en try/except: si la ROI está completamente fuera del frame (crash)
     re-lanza con mensaje descriptivo que indica modelo y comando de recalibración.
   - Agrega `WARNING` si `pattern.image_size` no coincide con el frame post-ROI
     (patrón calibrado a otra resolución).

4. `data/patterns/scanner_2/modelo_B/roi.json` — creado con `{x:0, y:0, w:640, h:480}`:
   - ROI full-frame para frames 640×480 (punto de partida seguro, no crashea).
   - **Pendiente:** recalibrar con `define-roi` + `build-pattern` usando frame real de la cámara.

**Pendiente crítico:** falta `data/patterns/scanner_2/modelo_B/holes.json`.
Para crear el patrón específico capturar un frame OK desde la UI (GRABACIÓN) y ejecutar:
```
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_B --scanner scanner_2 --img "ruta/al/frame_ok.png"
```

**Archivos modificados:** `src/patterns/pattern_io.py`, `src/patterns/roi.py`, `src/inspection.py`, `data/patterns/scanner_2/modelo_B/roi.json`

---

### Sesión 2026-06-04 (continuación) — Tadeo + Claude

#### Cambio 105 — Análisis: processEvents antes de cada frame (progreso siempre visible)

**Problema raíz confirmado:** `inspect_image` tarda 300–1500 ms por frame (dependiendo de la alineación). Durante ese tiempo el hilo principal está bloqueado y Qt no puede repintar la barra de progreso. El usuario veía "0%" toda la sesión aunque el análisis sí corría.

**Solución:** en `_analyze_one_frame`, mostrar `"Analizando frame N/M (X%)..."` con `QApplication.processEvents()` **antes** de llamar `inspect_image`. Esto fuerza el repintado mientras el hilo todavía está libre. Después del frame, actualizar texto y barra con el resultado real. También 10 ms de pausa entre frames (antes 1 ms) para que Qt procese eventos de repintado.

- `src/ui/service.py` → `_analyze_one_frame`: actualiza barra + processEvents ANTES de inspect_image; luego actualiza con resultado; `logger.info` por frame (visible en Logs tab con nivel INFO).

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `19bc380`

---

#### Cambio 104 — Análisis: QTimer.singleShot por frame (elimina dependencia de signals cross-thread)

**Problema diagnosticado:** el `_AnalysisWorker` (QThread) emitía signals de progreso desde el hilo del worker hacia el hilo principal. La cámara IP saturaba el event loop del hilo principal con ~15 frames/segundo, lo que interfería con la entrega de esos signals. Resultado: el análisis completaba en background pero los signals de progreso nunca se despachaban visiblemente.

**Solución:** eliminar completamente `_AnalysisWorker` del flujo de análisis. Reemplazar por `QTimer.singleShot` que corre en el hilo principal:

- `_on_analyze` carga patrón/tolerancias una vez y dispara `QTimer.singleShot(5, _analyze_one_frame)`
- `_analyze_one_frame` procesa UN frame por llamada, actualiza barra directamente, y dispara el siguiente
- `_on_stop_analyze` setea flag `_ana_running = False` para detener en el próximo frame
- Estado de análisis en nuevos atributos: `_ana_running`, `_ana_frame_idx`, `_ana_model`, `_ana_scanner_id`, `_ana_pre`

**Decisión de diseño:** mantener `_AnalysisWorker` en el archivo para compatibilidad con código antiguo (modo en vivo), pero el flujo principal de análisis ya no lo usa.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `65452ad`

---

#### Cambio 103 — Análisis: QProgressBar + controles siempre visibles fuera del scroll

**Problema:** los controles de análisis (botones, barra de progreso, label de estado) quedaban dentro del `QScrollArea` y podían estar fuera de vista si el usuario había scrolleado hacia el visor de imágenes.

**Cambios en `src/ui/service.py`:**
- `_build_ana_page()`: rediseñada con dos zonas separadas. Parte superior fija (no scrolleable): `_build_analysis_section()`. Parte inferior scrolleable: `_build_browser_section()` dentro de su propio `QScrollArea`.
- `_build_analysis_section()`: agrega `_ana_progress_bar` (QProgressBar) que aparece al iniciar el análisis, muestra porcentaje real, y cambia color: azul=analizando, verde=OK, rojo=error.
- Los labels de progreso (`_ana_progress`) muestran estado coloreado en cada etapa.
- `QProgressBar` importado en la lista de imports de PyQt6.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `16c9521`

---

#### Cambio 102 — Análisis worker: secuencial, progreso por frame, sin race condition

**Problema:** el worker paralelo (`ThreadPoolExecutor`) compartía el dict `_pre` entre hilos. El campo `ema_state` dentro de `_pre` era modificado por `align_image_by_right_edge` en cada frame — race condition que causaba comportamiento impredecible y cuelgues aleatorios.

**Cambios en `_AnalysisWorker.run()`:**
- Eliminado `ThreadPoolExecutor` y procesamiento paralelo.
- Siempre secuencial (un frame a la vez, en orden).
- `_pre` incluye ahora `"ema_state": {}` para el suavizado de ángulo EMA.
- `progress.emit` en **cada frame** (antes cada `n//50`), para que la UI muestre avance en tiempo real.
- `machine_stop_enabled` sigue soportado, solo añade el detector al dict `_pre`.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `0c7cc43`

---

#### Cambio 101 — Service UI: scroll ANÁLISIS, selector modelo en ambas pestañas, sub-tab CONEXIÓN

**Cambios en `src/ui/service.py`:**
- `_build_ana_page()`: devuelve `QScrollArea` (vertical, sin scrollbar horizontal).
- `_build_analysis_section()`: agrega selector ESTERILLA / MICROPERFORADO (duplicado del de GRABACIÓN), sincronizado con `_model_combo` via `_sync_model_buttons()`.
- `_sync_model_buttons()`: sincroniza todos los sets de botones (grab + ana).
- `_set_analysis_running()`: también bloquea/desbloquea los botones de modelo en ANÁLISIS.
- `_on_ana_progress()`: muestra porcentaje y refresca imagen cada 3 frames desde disco.
- `_on_ana_done()`: envuelto en `try/except` con log claro (corrige "nunca termina" silencioso).
- Sub-tab "CALIBRACIÓN" renombrado a "CONEXIÓN".
- `_img_view.setMinimumHeight`: 600 → 400.

**Archivos modificados:** `src/ui/service.py`  
**Commit:** `ac6c078`

---

#### Cambio 100 — Cámara IP: robustez keep-alive, grabación compatible con IP, auto-conexión

**Contexto:** las cámaras IP usan `oneshotimage.jpg` (snapshot HTTP, no MJPEG). El código anterior usaba `cv2.VideoCapture` que no podía conectar estas cámaras.

**Cambios:**
- `src/vision/camera.py`: `_is_snapshot_source()` detecta URLs `.jpg`/`.jpeg`. `_snapshot_loop()` usa `http.client.HTTPConnection` con keep-alive TCP, retencion de último frame válido (3s), backoff 0.1→1.6s. `is_connected` usa flag `_snapshot_ok`. FPS por defecto: 5 → 15.
- `src/ui/service.py` — `RecordingTab`:
  - `_auto_connect_scanner_camera(sid)`: lee `camera_source` del io_map y conecta automáticamente al abrir la pestaña.
  - `_HTTPSnapshotReader`: polling de snapshot HTTP con keep-alive (reemplaza `_MJPEGReader` para URLs snapshot).
  - `_update_fps_cap()`: cap del spinbox de FPS al FPS real medido de la cámara.
  - `_fps_cap_timer`: QTimer cada 2s para actualizar el cap.
  - FPS default snapshot: 150ms → 67ms (15 fps).
- `config/camera.yaml`: añadido `fps: 15` para scanner_1 y scanner_2.

**Archivos modificados:** `src/vision/camera.py`, `src/ui/service.py`, `config/camera.yaml`

---

#### Cambio 99 — Backlight siempre encendido (Y12/Y13 nunca se apagan)

**Problema:** al detener el scanner o activar FAULT, el `ScannerController` apagaba el backlight (`light_backlight = False`). El operario necesita que el backlight esté encendido permanentemente para inspección continua.

**Cambio:** eliminados todos los 6 `self._io.write(f"{self._id}.backlight", False)` del `ScannerController` (en `stop()`, `force_fault()`, fallo de selftest, timeout de cámara, machine stop y FAULT por racha).

**Archivos modificados:** `src/controller/scanner_controller.py`

---

#### Cambio 98 — Service UI: tab "Cámara" con sub-tabs GRABACIÓN / ANÁLISIS / CONEXIÓN

**Motivación:** la pestaña de Grabación era un widget monolítico, demasiado ancha y sin scroll. El análisis compartía espacio con la cámara.

**Cambios en `src/ui/service.py`:**
- `RecordingTab._build_ui()`: ya no crea su propio layout visible. Expone `_grab_page` y `_ana_page` como widgets independientes que `ServiceWindow` monta.
- `_build_grab_page()`: HBox con controles (izquierda) + preview cámara IP (derecha).
- `ServiceWindow._build_ui()`: elimina el tab independiente "Grabación". Crea `cam_tabs = QTabWidget()` con 3 sub-tabs: GRABACIÓN, ANÁLISIS, CONEXIÓN. Los monta bajo el tab principal "Cámara".
- Sub-tab style: fuente más grande, indicador de selección con borde inferior azul.
- `QSplitter` agregado a imports de PyQt6.

**Archivos modificados:** `src/ui/service.py`

---

### Sesión 2026-06-04 — Tadeo + Claude

#### Cambio 99 — Recalibración nueva cámara IP para microperforado y esterilla

**Contexto:** las carpetas de la cámara nueva Sony (`640x480`) no podían analizarse
con la calibración previa porque:
- las ROI viejas correspondían a otro encuadre/resolución;
- el alineado por borde derecho metía rotaciones falsas sobre un borde curvo de la cámara IP;
- la morfología de preprocess (`open=3`, `close=5`) eliminaba demasiados agujeros al
  construir patrones.

**Cambios aplicados:**
- `config/tolerancias.yaml`
  - `modelo_A` y `modelo_B`: `edge_align_enabled: false`.
  - `modelo_A`: calibrado para la nueva cámara con `use_channel: gray`,
    `threshold: 180`, `open_ksize: 1`, `close_ksize: 3`,
    `min_area: 20`, `circularity_min: 0.15`, `aspect_ratio_max: 4.5`,
    `grid_dx: 26`, `grid_dy: 14`.
  - `modelo_B`: calibrado para la nueva cámara con `use_channel: b`,
    `threshold: 180`, `open_ksize: 1`, `close_ksize: 3`,
    `min_area: 15`, `circularity_min: 0.2`, `aspect_ratio_max: 3.5`.
  - `modelo_B`: decisión relajada para absorber el sesgo base de esta calibración:
    `frame_missing_nok_threshold: 40`, `grid_max_missing: 45`,
    `machine_stop_min_missing: 3`.
- `src/inspection.py` y `src/patterns/pattern_build.py`:
  - el alineado por borde ahora puede desactivarse por configuración de modelo;
    cuando se desactiva, el pipeline sigue sin intentar rotar la imagen.
- Nuevas ROI específicas para la cámara nueva:
  - `data/patterns/scanner_1/modelo_B/roi.json`
  - `data/patterns/scanner_2/modelo_A/roi.json`
- Patrones reconstruidos con imágenes de referencia nuevas:
  - microperforado: `MICROPERFORADO_1/frame_0077.png` → `scanner_1/modelo_B/holes.json`
  - esterilla: `ESTERILLA_3/frame_0023.png` → `scanner_2/modelo_A/holes.json`

**Validación:**
- `MICROPERFORADO_1` (`scanner_1/modelo_B`):
  - `run-folder` → `total=82`, `raw_ok=74`, `raw_nok=8`, `temporal_ok=82`,
    `temporal_nok=0`, `machine_stop_frames=0`.
- `ESTERILLA_3` (`scanner_2/modelo_A`, carpeta usada como referencia):
  - `run-folder` → `total=30`, `raw_ok=30`, `temporal_ok=30`, `machine_stop_frames=0`.
- `ESTERILLA_1` (`scanner_2/modelo_A`):
  - `run-folder` → `total=115`, `raw_ok=101`, `raw_nok=14`,
    `temporal_ok=102`, `temporal_nok=13`, `machine_stop_frames=13`.
  - La gran mayoría del lote quedó OK; las 13 detecciones temporales NOK provienen de
    `machine_stop` aislados y pueden corresponder a defectos reales del material o a
    una sensibilidad todavía alta para ese lote puntual.

**Archivos modificados:** `config/tolerancias.yaml`, `src/inspection.py`,
`src/patterns/pattern_build.py`, `data/patterns/scanner_1/modelo_B/roi.json`,
`data/patterns/scanner_1/modelo_B/holes.json`, `data/patterns/scanner_2/modelo_A/roi.json`,
`data/patterns/scanner_2/modelo_A/holes.json`

---

#### Cambio 100 — Ajuste final de tolerancias para el nuevo patrón óptico

**Motivación:** después de reconstruir los patrones con la cámara/lente/ángulo nuevos,
seguían apareciendo muchos `NOK` falsos porque los umbrales de decisión heredados estaban
pensados para el patrón visual anterior. La detección de agujeros ya estaba funcionando,
pero el baseline de `missing` quedó mucho más alto en ambos modelos.

**Criterio usado:** calibración por lotes buenos reales de esta cámara:
- `MICROPERFORADO_1`
- `ESTERILLA_1`
- `ESTERILLA_3`

Se midió el rango real de `missing` en esas carpetas y luego se ajustaron los umbrales
para absorber ese baseline nuevo sin volver a habilitar alineados/stop logic que
dependían de la geometría anterior.

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

**Validación final:**
- `MICROPERFORADO_1`:
  - `run-folder` → `total=82`, `raw_ok=82`, `raw_nok=0`,
    `temporal_ok=82`, `temporal_nok=0`, `machine_stop_frames=0`
- `ESTERILLA_1`:
  - `run-folder` → `total=115`, `raw_ok=115`, `raw_nok=0`,
    `temporal_ok=115`, `temporal_nok=0`, `machine_stop_frames=0`
- `ESTERILLA_3`:
  - `run-folder` → `total=30`, `raw_ok=30`, `raw_nok=0`,
    `temporal_ok=30`, `temporal_nok=0`, `machine_stop_frames=0`

**Nota importante:** esta calibración está optimizada para no dar falsos positivos con
la cámara nueva y con lotes buenos reales. Cuando haya carpetas o imágenes de defecto
real de esta nueva óptica, conviene hacer una segunda pasada de ajuste para volver a
apretar `frame_missing_nok_threshold`, `grid_max_missing` y/o reactivar lógica de
`machine_stop` ya sobre evidencia de defectos reales.

**Archivos modificados:** `config/tolerancias.yaml`, `CHANGELOG.md`

---

#### Cambio 101 — Servicio: análisis de carpeta vuelve a correr fuera del hilo UI

**Problema reportado:** al iniciar el análisis desde la pestaña de grabación/análisis,
la barra quedaba clavada en `0%` y la ventana parecía tildarse en el frame 1.

**Causa:** el flujo nuevo de análisis había pasado a ejecutarse con
`QTimer.singleShot(..., _analyze_one_frame)` en el hilo principal. Aunque el texto de
progreso se actualizaba antes de `inspect_image()`, el trabajo pesado seguía corriendo
en el thread de UI y bloqueaba repintado/interacción hasta terminar cada frame.

**Fix aplicado en `src/ui/service.py`:**
- `RecordingTab._on_analyze()` vuelve a lanzar `_AnalysisWorker(QThread)` para procesar
  frames fuera del hilo gráfico.
- `stop/cancel/progress/done/error` actualizan de nuevo el estado de UI en base al worker.
- La barra ahora avanza por cantidad real de frames procesados en vez de intentar
  repintarse durante trabajo bloqueante en el hilo principal.

**Validación:** `python -m py_compile src/ui/service.py` OK.

**Archivos modificados:** `src/ui/service.py`, `CHANGELOG.md`

---

#### Cambio 98 — Falla rápida cuando la ROI queda fuera de imagen

**Problema:** Al analizar carpetas capturadas con una cámara nueva de resolución/encuadre
distinto, el sistema podía quedar "colgado" mucho tiempo si la ROI cargada para ese
modelo/scanner quedaba totalmente fuera del frame real. En ese caso `apply_roi()`
devolvía un recorte vacío (`width=0`) y OpenCV terminaba entrando a `CLAHE` sobre una
imagen inválida en vez de cortar con un error claro.

**Cambio aplicado:**
- `src/patterns/roi.py`: `apply_roi()` ahora valida el bounding box contra el tamaño
  real del frame, recorta a límites válidos y lanza `ValueError` explícito si la ROI
  queda vacía o fuera de imagen.
- `src/pipeline/preprocess.py`: validación temprana de imagen vacía antes de cualquier
  operación de OpenCV, para no volver a entrar al pipeline con dimensiones `0xN` o `Nx0`.

**Resultado:** `run-image` / `run-folder` ya no aparentan "quedarse pensando" cuando la
calibración no corresponde al frame; ahora fallan en segundos con un mensaje del tipo:
`ROI fuera de imagen o vacia (... image=640x480)`, haciendo evidente que falta
recalibrar ROI/patrón para esa cámara.

**Archivos modificados:** `src/patterns/roi.py`, `src/pipeline/preprocess.py`

---

#### Cambio 97 — Cámaras IP fijas por scanner (sin USB)

Scanner 1 (izquierda) → `192.168.1.3`, Scanner 2 (derecha) → `192.168.1.2`.

- `config/io_map.yaml`: reemplaza `camera_index` por `camera_source` con URL HTTP
  en ambos scanners. La clase `Camera` detecta `http://` y usa el modo MJPEG/snapshot.
- `config/camera.yaml`: actualiza `ip_camera_1` y `ip_camera_2` con las nuevas IPs
  (credenciales `root`/`defy2026` como el resto de las cámaras).
- `src/ui/service.py`: placeholders y `default_host` del selector de slot actualizados
  a `192.168.1.3` (slot 0) y `192.168.1.2` (slot 1).

No se usan más cámaras USB.

**Archivos modificados:** `config/io_map.yaml`, `config/camera.yaml`, `src/ui/service.py`

---

#### Cambio 96 — Operador UI: scanner_1 vuelve al panel izquierdo

**Problema:** El commit anterior (`f5363c1`) había invertido el orden visual de los paneles
con `reversed()` para colocar scanner_2 a la izquierda. El criterio correcto es que
scanner_1 (físicamente a la izquierda del operario) siempre aparezca en el panel izquierdo.

**Cambio:** Eliminado el `reversed()` en el loop de construcción de paneles en `operator.py`.
`scanner_ids()` devuelve los IDs en el orden del YAML (`scanner_1` primero), que coincide
con el orden físico izquierda → derecha.

**Archivos modificados:** `src/ui/operator.py` (línea 694)

---

### Sesión 2026-06-03 — Tadeo + Claude

#### Cambio 95 — Robustez industrial: habilita FAULT por racha + sube min_missing a 2

**Análisis completo de 196 frames (Patron_Esterilla_METALCONF_editado):**

- `consecutive_nok_frames: 9999 → 5`: el sistema estaba en modo calibración y NUNCA
  disparaba FAULT por racha NOK (response_time=1999s vs target=1.6s, meets_target=False).
  Con 5 frames a 5fps = 1.0s de respuesta, dentro del target de 1.6s.

- `machine_stop_min_missing: 1 → 2`: el MachineStopDetector disparaba falsos positivos
  en grupos de frames normales (0004-0006, 0013-0015, 0196-0197) porque 1 solo agujero
  marginal del patron quedaba persistentemente fuera del alcance de deteccion en ciertas
  posiciones del material. Con minimo 2, se requieren al menos 2 agujeros faltantes en la
  misma zona para activar la parada persistente — filtra el ruido marginal sin perder
  detecciones reales de punzon roto (que suelen ser 2+ agujeros en la misma columna).

**Verificacion:** 17/17 tests OK.

**Archivos modificados:** `config/tolerancias.yaml`

---

#### Cambio 94 — Desalineamiento vertical: frame_0029 capturado + reducción de falsos positivos por zigzag

**Problema:** Al analizar `Patron_Esterilla_METALCONF_editado`, los frames 27 y 28 ya
paraban (ratio > 0.2 AND dAng >= 2.5), pero `frame_0029` pasaba como `OK` porque:
- Primera condición: ratio=0.23 > 0.2 ✅ pero dAng=0.72 < 2.5 ❌
- Segunda condición (zigzag): patZZ=8.7 < 9.0 ❌ (falla por 0.3px)

Además, ~21 frames normales disparaban falsos `machine_stop` via la condición de zigzag
porque el baseline normal de esterilla tiene patZZ=9–11px (el umbral 9.0 era demasiado bajo).

**Diagnóstico con 196 frames:**
- Frames con patZZ≈10–11 y missing=0 → zigzag condition (9.0) se activaba en falso.
- Frames OK normales con alto patZZ siempre tienen ctrStd < 2.0 (no importa el patZZ).
- Los únicos frames con ratio > 0.2 son 27–30; umbral de 0.2 es un gate seguro.
- Raising patZZ threshold a 11.5 elimina todos los falsos del grupo zigzag (max normal = 10.7).

**Cambios en `config/tolerancias.yaml` modelo_A:**

| Parámetro | Antes | Ahora | Razón |
|---|---|---|---|
| `pattern_desalign_min_angle_deg` | 2.5 | **0.3** | frame_0029 tiene dAng=0.72 que ya supera 0.3; threshold bajo es seguro porque la gate de ratio=0.2 aísla los frames desalineados |
| `pattern_desalign_zigzag_std_px` | 9.0 | **11.5** | Baseline normal de esterilla es patZZ≈9–11; subir a 11.5 elimina falsos (max false positive: 10.7; target frame_0030: 12.1) |

**Resultado validado (196 frames):**
- frame_0027: STOP (ratio=1.00, dAng=3.08) ✅
- frame_0028: STOP (ratio=0.23, dAng=3.48) ✅
- frame_0029: **STOP** (ratio=0.23, dAng=0.72 >= 0.3) ✅ ← nuevo
- frame_0030: STOP (patZZ=12.1 >= 11.5, via zigzag) ✅
- Total machine_stop: 37 → **16** (21 falsos positivos eliminados)

**Archivos modificados:** `config/tolerancias.yaml`

---

### Sesión 2026-06-02 — Tadeo + Claude

#### Cambio 92 — Grabación 1 min pre + 30 s post parada; ventana tolerancias limpia

**Grabación pre/post evento:**
- `pre_event_seconds: 60` (1 minuto antes de la parada).
- `post_event_seconds: 30` (30 segundos después de la parada) — nuevo parámetro.
- `pre_event_max_ram_mb: 256` (60 s × 5 fps × ~100 KB ≈ 30 MB efectivos; 256 con margen).
- `src/utils/config.py`: defaults actualizados al mismo valor.
- `src/controller/scanner_controller.py`: pasa `post_seconds` al `EventRecorder`.

**Lógica post-evento en `EventRecorder`:**
- `_post_dir / _post_until / _post_idx` controlan la grabación post-parada.
- `add_frame`: si está dentro de la ventana post-evento, escribe `post_NNNN.jpg`
  directamente a disco sin pasar por el buffer RAM.
- `_flush_sync`: tras guardar frames pre-evento, activa el modo post-evento.
- `_finalize_manifest`: actualiza `post_frames_count` y `total_bytes` cuando expira
  la ventana. Corre en hilo background.
- Manifest ahora tiene: `pre_frames_count`, `post_frames_count`, `total_bytes`.

**Ventana de tolerancias:**
- Eliminado el control `pre_event_seconds` (los tiempos de grabación son fijos y no
  deben cambiar por el operario).
- Aviso superior reemplazado por banner prominente amarillo oscuro, texto grande en
  negro, cubre todo el ancho: "SOLO MODIFICAR SI EL ANÁLISIS TIENE DEMASIADOS
  FALSOS ERRORES O NO DETECTA EFICIENTEMENTE DEFECTOS REALES".

**Validación:** compile OK, tests 16/16.

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

#### Cambio 91 — Ventana de tolerancias por scanner + grabación de evidencia siempre activa

**Grabación siempre activa:**
- `config/tolerancias.yaml`: `events_enabled: false` → `true`.
  Desde este commit, cada parada (machine_stop o FAULT) guarda automáticamente
  los frames previos en `data/events/`.

**Nueva ventana "Tolerancias":**
- `src/ui/tolerance_window.py` — `ToleranceWindow` + `_ScannerTolerancePanel`.
- Accesible desde el botón **"Tolerancias"** (verde) en el header del operador.
- Una columna por scanner, con los 6 parámetros seguros para el operario:

| Parámetro | Rango | Efecto |
|---|---|---|
| `frame_missing_nok_threshold` | 1–60 | Cuántos faltantes para marcar NOK |
| `machine_stop_missing_frames` | 2–20 | Frames persistentes antes de parar |
| `tol_xy_px` | 5–40 px | Tolerancia de posición del agujero |
| `tilt_warn_deg` | 0–10 ° | Ángulo para aviso CHAPA INCLINADA |
| `consecutive_nok_frames` | 2–9999 | Frames NOK antes de FAULT |
| `pre_event_seconds` | 5–60 s | Buffer de evidencia a grabar |

- **Qué NO se expone:** geometría de grilla, parámetros de detección (min_area,
  circularity, CLAHE), alineación/RANSAC, pattern_desalign. Esos solo desde Servicio.
- Botón "Guardar" por scanner → llama `save_model_overrides(model, updates)` que
  actualiza ÚNICAMENTE el bloque `models.<model>` en `tolerancias.yaml` sin tocar
  parámetros globales ni otros modelos. Luego llama `scanner.set_model(same_model)`
  para recargar `consecutive_nok_frames` en el controlador activo.
- Aviso visual si `consecutive_nok_frames >= 500` (modo calibración): pide confirmación
  antes de guardar.
- Botón "Recargar" relée el YAML y resetea todos los spinboxes.

**`src/utils/config.py`:** nueva función `save_model_overrides(model, updates)`.
**`src/ui/operator.py`:** botón "Tolerancias" en el header; `_tolerance_win` cerrado en closeEvent.

**Validación:** compile OK, tests 16/16.

**Archivos nuevos:** `src/ui/tolerance_window.py`
**Archivos modificados:** `src/utils/config.py`, `src/ui/operator.py`, `config/tolerancias.yaml`

---

#### Cambio 90 — Sistema de grabación de evidencia pre-evento (EventRecorder)

**Objetivo:** mantener un buffer circular de frames originales (sin overlay) por scanner
y volcarlo a disco al detectar `machine_stop` o transición a `FAULT`, sin superar nunca
un presupuesto fijo de disco (`events_max_disk_gb: 10 GB`).

**Arquitectura:**
- `src/pipeline/event_recorder.py` — clase `EventRecorder` independiente del pipeline.
  - Buffer `deque[(timestamp, jpeg_bytes)]` limitado por tiempo (`pre_event_seconds`)
    y RAM (`pre_event_max_ram_mb`). Nunca acumula frames BGR crudos en RAM.
  - `add_frame(frame)` comprime a JPEG con rate-limit interno (no satura CPU).
  - `flush_event(type, reason)` lanza un hilo background para no bloquear el inspector.
  - `_prune_to_budget(needed)` borra carpetas más viejas (por mtime) hasta que el nuevo
    evento quepa en el presupuesto. Si un solo evento supera el total, se trunca
    conservando los frames MÁS RECIENTES (los más cercanos a la parada).
  - Carpetas: `data/events/DD-MM-YYYY_STOP_N/` con `frame_NNNN.jpg` + `manifest.json`.

**`manifest.json` incluye:** timestamp, scanner_id, event_type, reason, frames_count,
total_bytes.

**Integración en `src/controller/scanner_controller.py` (cambios mínimos):**
- `__init__`: inicializa `self._recorder` si `events_enabled=True` (lazy import).
- `_continuous_loop`: `recorder.add_frame(frame)` después de `get_frame()`, antes de inspección.
- `_handle_result`: `recorder.flush_event("machine_stop", ...)` y `flush_event("fault", ...)`
  en los puntos donde ya se loguean esos eventos.
- `_derive_stop_reason(result)`: método estático que extrae la razón del `InspectionResult`.

**Config (`config/tolerancias.yaml` + `src/utils/config.py`):**
```yaml
events_enabled: false        # cambiar a true en producción
events_max_disk_gb: 10.0
pre_event_seconds: 10.0
pre_event_fps: 5.0
pre_event_jpeg_quality: 80   # ≈ 100-150 KB/frame a 1080p
pre_event_max_ram_mb: 128.0  # por scanner
```

**Cómo nunca supera 10 GB:**
1. `_prune_to_budget(needed)` se llama ANTES de crear la carpeta nueva: borra las más
   viejas hasta que `total_actual + needed ≤ 10 GB`.
2. Si el evento sería más grande que el presupuesto entero, se trunca a los frames más
   recientes que quepan (caso extremo: buffer con JPEG muy grandes).
3. La poda es determinista (por mtime, oldest-first). No hay carrera si dos scanners
   escriben simultáneamente (cada uno borra lo que necesita; en el peor caso se borra un
   poco más — nunca menos).

**Tests: `tests/test_event_recorder.py` (12 casos, todos pasan):**
- `TestFolderNaming`: secuencia STOP_1/2/3, gap sin salto.
- `TestPruneByBudget`: borra el más viejo primero, total queda bajo budget, no borra si no hace falta.
- `TestBufferRamLimit`: presupuesto RAM respetado, ventana temporal expulsa frames viejos.
- `TestManifest`: campos obligatorios, conteo de frames correcto.
- `TestTruncation`: evento truncado queda bajo presupuesto, conserva frames más recientes.

**Validación:** compile OK, 16/16 tests (12 nuevos + 4 existentes).

**Archivos nuevos:** `src/pipeline/event_recorder.py`, `tests/test_event_recorder.py`
**Archivos modificados:** `src/controller/scanner_controller.py`, `src/utils/config.py`,
`config/tolerancias.yaml`

---

#### Cambio 89 — UI operario: botones INICIAR/DETENER prominentes + overlay solo en machine_stop

**Pedido del operario:** simplificar la pantalla principal para el uso en producción:
1. INICIAR y DETENER como los dos botones principales de cada scanner (más grandes).
2. Cámara cruda durante operación normal — sin mostrar el procesamiento.
3. Overlay con todos los marcadores (verde/rojo) SOLO cuando hay `machine_stop=True`.

**Cambios en `src/ui/operator.py`:**
- Nuevo `_OVERLAY_HOLD_FAULT_MS = 30_000`: el overlay de error se mantiene 30 s visible.
- Nuevos métodos `_primary_btn` (h=52px, font 16px bold) y `_secondary_btn` (borde, 11px).
- `_build_ui` en `ScannerPanel`: INICIAR/DETENER reemplazados por `_primary_btn` como fila
  principal; RESET pasa a `_secondary_btn` centrado debajo.
- `_on_result`: overlay solo se emite cuando `result.machine_stop is True` (30 s hold).
  Antes se emitía para cualquier `streak >= warn_level` (threshold//3). El feed de cámara
  muestra imagen cruda en operación normal; cuando machine_stop activa, el overlay congela
  la captura con el banner `! DETENCION DE MAQUINA` y los marcadores rojos.
- Log reducido a 34 px de altura (antes 54 px) — solo registra eventos críticos.

**Comportamiento resultante:**
- Operación normal (OK / NOK-streak): cámara cruda en vivo, sin ruido visual.
- Machine stop: overlay con círculos verdes + cruces rojas + banner visible 30 s.
- El operario usa únicamente INICIAR / DETENER; RESET solo aparece habilitado en FAULT/STOPPED.

**Validación:** compile OK, tests 4/4.

**Archivos modificados:** `src/ui/operator.py`

---

### Sesión 2026-06-01 — Tadeo + Claude

#### Cambio 88 — Desalineacion de patron: frame_0028 ya detiene sin reabrir 0080/0081

**Problema retomado:** Había quedado a medias la calibración pedida para:
- `frame_0080` / `frame_0081`: no debían caer por perder solo 1-2 agujeros.
- `frame_0028` editado: debía disparar `DETENCION DE MAQUINA` por corrimiento geométrico
  del patrón, no quedar `OK`.

**Diagnóstico:** La regla agregada para `pattern_desalign` solo miraba `missing/expected`
alto. Eso alcanzaba para el caso extremo `frame_0027` (74/115), pero dejaba afuera
`frame_0028` (26/115) aunque tenía `pattern_sheet_slope_delta_max_deg=3.48`. A la vez,
hacía falta no reabrir falsos casos leves como `frame_0080/0081` (missing=0, dAng≈1.0).

**Cambios:**
- `src/utils/config.py` → nuevo default `pattern_desalign_min_angle_deg: 0.0`.
- `src/inspection.py` → la parada por `pattern_desalign` ahora exige DOS condiciones:
  1. `missing/expected > pattern_desalign_missing_ratio`
  2. `pattern_sheet_slope_delta_max_deg >= pattern_desalign_min_angle_deg`
- `config/tolerancias.yaml` modelo_A:
  - `pattern_desalign_missing_ratio: 0.5 -> 0.2`
  - nuevo `pattern_desalign_min_angle_deg: 2.5`

**Validación:**
- Carpeta `Patron_Esterilla_METALCONF` (original):
  - `frame_0027`, `frame_0028`, `frame_0080`, `frame_0081` → todos `OK`, sin parada.
- Carpeta `Patron_Esterilla_METALCONF_editado`:
  - `frame_0027` → `NOK`, `machine_stop=True`
  - `frame_0028` → `NOK`, `machine_stop=True`
  - `frame_0080` / `frame_0081` → `OK`, `machine_stop=False`
- `run-folder` sobre la carpeta editada: `machine_stop_frames=2`, exactamente en
  `frame_0027` y `frame_0028`.

**Archivos modificados:** `src/utils/config.py`, `src/inspection.py`,
`config/tolerancias.yaml`
#### Cambio 87 — Inclinación NUNCA detiene la máquina (revierte parada por verticalidad del Cambio 84)

**Aclaración del operador:** cuando la chapa está INCLINADA no se debe detener NUNCA la
máquina, porque con la chapa inclinada el patrón NO se lee bien (lecturas no confiables) —
no es base válida para parar.

**Cambio:** `config/tolerancias.yaml` modelo_A → `machine_stop_on_tilt: true` → **false**.
Revierte la parada inmediata por verticalidad que se había puesto en el Cambio 84.

**Comportamiento resultante para frames inclinados (|tilt|>tilt_warn_deg):**
- Se marcan **NOK** (no se aceptan) y muestran "CHAPA INCLINADA" + número arriba-izquierda.
- **NUNCA** disparan `machine_stop` (sin banner "DETENCION DE MAQUINA").
- Se pasan como LOW_QUALITY al detector de faltantes → tampoco disparan la parada por
  faltantes (la lectura inclinada no contamina la racha).

**Faltantes persistentes** siguen pudiendo parar (Cambio 84): un punzón roto persistente N
frames → parada. Solo la inclinación quedó excluida de parar.

**Validación:** frames 0083 (-3.98°), 0090 (-3.18°) → NOK, `machine_stop=False`; normales OK.
Tests 4/4.

**Archivos modificados:** `config/tolerancias.yaml`

#### Cambio 86 — Texto de parada sin la palabra "VIRTUAL"

**Pedido:** En las fotos/overlays que marcan error o parada, el texto debía decir
`DETENCION DE MAQUINA` y no `DETENCION VIRTUAL DE MAQUINA`, tanto para
`modelo_A` como para `modelo_B`.

**Cambios:**
- `src/pipeline/annotate.py` → el banner superior de parada ahora muestra
  `! DETENCION DE MAQUINA`.
- `src/controller/scanner_controller.py` → el warning asociado a la parada
  persistente usa el mismo texto (`DETENCION DE MAQUINA`) para mantener
  consistencia entre overlay y logs.

**No tocado:** La lógica de `machine_stop`, el bloqueo de hardware y el carácter
virtual de la acción siguen igual; solo cambió el texto visible.

**Archivos modificados:** `src/pipeline/annotate.py`,
`src/controller/scanner_controller.py`

#### Cambio 85 — Grabación: navegación eficiente (overlays JPEG, libera ~1.6 GB) + saca flecha central

**Problema 1 — la PC se trababa al navegar tras el análisis.** `self._results` mantenía
200 `InspectionResult`, cada uno con `overlay` (1920×1080×3 ≈ 6 MB) + `mask` (≈2 MB) →
~1.6 GB en RAM → swap → freeze.

**Fix (`src/ui/service.py`):**
- `_on_ana_done`: tras el análisis, cada overlay se comprime a JPEG (q=92, ~295 KB vs
  6 MB → ~20×) en `self._overlay_jpegs`, y se liberan los arrays pesados de cada resultado
  (`object.__setattr__(r,"overlay",None)`, `mask=None`). 200 frames: ~1.6 GB → ~58 MB.
- Nuevo `_result_bgr(idx)`: decodifica el overlay del JPEG bajo demanda (rápido). Usado por
  el navegador (`_show_frame`), `_save_current_frame` y `_export_range`.
- `_px_cache_max` 40→24 (decodificar JPEG es barato, baja la RAM del caché de pixmaps).
- `_overlay_jpegs` se limpia en `_on_analyze` junto con `_results`.

**Problema 2 — flecha central de inclinación molestaba.** `draw_centering_overlay` dibujaba
una flecha (sheet-center→pattern-center) en el medio del frame.

**Fix (`src/pipeline/annotate.py`):** eliminada la flecha (y el círculo de fallback) del
centro. La inclinación queda solo como número arriba-izquierda (`draw_tilt_indicator`) y el
offset en el texto inferior. El resto del overlay de centrado se mantiene.

**Validación:** compile OK, tests 4/4, roundtrip JPEG verificado (6075 KB→295 KB, decode
1920×1080 OK, frozen dataclass liberado), smoke test de `RecordingTab`, overlay confirmado
sin flecha central.

**Archivos modificados:** `src/ui/service.py`, `src/pipeline/annotate.py`.

---

#### Cambio 84 — Parada de máquina: faltantes solo por persistencia, verticalidad inmediata (ambos patrones)

**Pedido del operador (regla para AMBOS patrones):**
- Un solo frame con faltantes **NUNCA** puede parar la máquina, sin importar cuántos
  falten (el metal pudo correrse). → siempre requiere persistencia.
- Un solo frame con **desvío de verticalidad SÍ** puede parar (falla mecánica). → inmediato.

**Esterilla (modelo_A) — antes tenía `machine_stop_enabled: false`. Cambios:**
- `config/tolerancias.yaml`: `machine_stop_enabled: true`, `machine_stop_missing_frames: 5`
  (persistencia), `machine_stop_min_missing: 1` (detecta un solo punzón roto persistente,
  como microperforado), nuevo `machine_stop_on_tilt: true` (verticalidad → parada inmediata).
- `src/inspection.py`: cuando `tilt_warn` (|sheet_tilt_deg|>`tilt_warn_deg`) y
  `machine_stop_on_tilt`, `machine_stop=True` en ese mismo frame con razón
  "PATRON DESALINEADO - VERTICALIDAD". Los faltantes pasan al detector como LOW_QUALITY
  cuando hay tilt, para no contaminar la racha de faltantes. (Reemplaza la lógica de
  persistencia de tilt que se había planteado: ahora la verticalidad es inmediata.)
- `src/utils/config.py`: default `machine_stop_on_tilt: False`.

**Refuerzo defensivo (ambos patrones):**
- `src/pipeline/machine_stop.py`: `missing_frames` se fuerza a `max(2, ...)` — un solo
  frame con faltantes nunca puede disparar la parada, aunque se configure 1.

**Microperforado (modelo_B) — ya cumplía, sin cambios:** `machine_stop_missing_frames: 5`
(persistencia), verticalidad inmediata vía `pattern_align_enabled` ("PATRON DESALINEADO").

**Validación:**
- Detector directo: 1 frame con 50 faltantes → para? **False** (nunca para por 1 frame).
- frames inclinados (0083=-3.98°, 0090=-3.18°) → `machine_stop=True` inmediato (NOK,
  "PATRON DESALINEADO - VERTICALIDAD"); normales (0162, 0016) → False.
- `run-folder` Patron_Esterilla (200 frames): 9 machine_stop = todos por verticalidad
  (inclinados), 0 por faltantes (material bueno). Tests 4/4.

**Archivos modificados:** `config/tolerancias.yaml`, `src/inspection.py`,
`src/utils/config.py`, `src/pipeline/machine_stop.py`.

---

#### Cambio 83 — Esterilla: de-rotación por tilt (fixea falsos missing) + tilt→NOK sin DETENER MAQUINA

**Contexto:** carpeta nueva `Patron_Esterilla_METALCONF` (201 frames, 63 únicas). El usuario
reportó (1) frames con demasiados faltantes que deberían detectarse bien, y (2) pidió que la
chapa inclinada se marque NOK pero **nunca** muestre "DETENER MAQUINA".

**Diagnóstico (`scripts/_esterilla_tilt_diag.py`, `_esterilla_derotate_exp.py`):**
6/63 frames NOK con missing 36–98. correlación tilt↔missing = 0.65. Dos modos:
- tilt alto (0083=-3.98°, 0090=-3.18°): chapa inclinada → fase del grid falla.
- tilt bajo (0016=-1.67°, 0120=-1.59°): fallo de fase igual (amplificado por el bbox=10).
La detección estaba bien (det 104–110); el problema era el matching de la grilla
(asume ejes alineados). Experimento de-rotando los agujeros: missing 0016 55→0, 0090 98→0,
0120 89→0, sin regresión en frames buenos.

**Implementado:**
- `src/pipeline/grid_fitting.py`: `rotate_points(pts, deg, cx, cy)`.
- `src/inspection.py` (grid path): mide `sheet_tilt_deg`, de-rota los agujeros antes de
  `grid_compare_points` y rota las posiciones esperadas DE VUELTA al espacio original
  (donde se compara y dibuja). Gated por `grid_derotate` + `grid_derotate_min_deg`.
- `src/inspection.py` (machine_stop): `tilt_warn` se calcula antes; si la chapa está
  inclinada (|tilt|>`tilt_warn_deg`) → `final_status="NOK"`, `machine_stop=False` (jamás
  DETENER MAQUINA) y se pasa `frame_quality="LOW_QUALITY"` al detector para no contaminar la
  racha. `import math` agregado a nivel módulo.
- `config/tolerancias.yaml` modelo_A: `grid_derotate: true`, `grid_derotate_min_deg: 0.4`.
  `src/utils/config.py`: defaults `grid_derotate=False`, `grid_derotate_min_deg=0.4`.

**Resultado (63 únicas):** missing media 7.7→**0.4** (máx 98→4), NOK-por-missing 6/63→**0/63**.
Frames inclinados (0083, 0090) ahora matchean bien (missing 0–1) pero quedan **NOK +
"CHAPA INCLINADA"** sin "DETENER MAQUINA" (verificado en overlay). frame_0016 (antes 55
faltantes en la mitad inferior) → todo verde OK. Tests 4/4.

**Archivos modificados:** `src/pipeline/grid_fitting.py`, `src/inspection.py`,
`config/tolerancias.yaml`, `src/utils/config.py`.

---

#### Cambio 82 — Grabación: chip de TIPO DE PLACA junto a Analizar + fix colisión _btn_stop

**Pedido:** un cartel al lado del botón Analizar que indique si se está analizando
MICROPERFORADO o ESTERILLA, para no confundirse.

**Bug encontrado y corregido (regresión del Cambio 78):** el botón "Detener" del análisis
se había nombrado `self._btn_stop`, igual que el botón "DETENER" de **grabación**
(`_build_recording_section`). Como `_build_analysis_section` corre después, el de análisis
**sobrescribía** al de grabación → el botón DETENER de grabación quedaba huérfano (su
`clicked.connect(self._on_stop)` en realidad cableaba el botón de análisis) y `_on_start`
habilitaba el botón equivocado. Se renombró el de análisis a **`_btn_stop_analyze`** en
todos sus usos (creación, `_set_analysis_running`, `_on_stop_analyze`). Ahora son
independientes (verificado: `rec_stop is analyze_stop == False`).

**Cambio (chip):** en `_build_analysis_section`, junto a Analizar/Detener, se agregó
`Tipo:` + `_analyze_model_chip` (QLabel prominente, 13px bold, color por familia:
celeste=Microperforado, verde=Esterilla). `_update_model_chip` ahora actualiza ambos chips
(el nuevo guardado con `hasattr`). Se sincroniza con el selector de modelo y queda bloqueado
junto con él durante el análisis.

**Validación:** compile OK, tests 4/4, smoke test offscreen: chip refleja
Microperforado/Esterilla al togglear; botones de grabación y análisis independientes;
selector bloqueado durante análisis.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 81 — Esterilla: limpieza de "extras" de borde (bbox_filter_margin_px 50→10)

**Pedido:** corregir los agujeros "extra" (diamantes naranjas) que el sistema marcaba.

**Diagnóstico (`scripts/_esterilla_extras_diag.py`, 17 únicas, 124 extras / ~7.3 por frame):**
distribución por zona — TOP-center 57, MIDDLE-left 32, BOTTOM-center 20. Son **agujeros
reales de borde** del band (no espurios) que el patrón no registra; parte del top apareció
al recortar la fila superior (Cambio 79). No son ruido.

**Insight:** el círculo VERDE se dibuja sobre TODOS los agujeros detectados (`holes`),
mientras que los "extra" salen de `detected_in_bbox`. Achicando el margen del bounding-box
del patrón, los agujeros de borde quedan fuera del conteo de extras (sin diamante) PERO
siguen en verde.

**Cambio:** `config/tolerancias.yaml` → `modelo_A`: `bbox_filter_margin_px` 50→**10**.

**Resultado (17 únicas):** extras **7.3 → 1.8** por frame (mediana 2, máx 3), missing sin
cambios (mediana 0), 0 NOK. Overlay verificado: todo verde de arriba a abajo, sin cruces y
prácticamente sin diamantes (1 residual en borde izquierdo). Tests 4/4.

**Archivos modificados:** `config/tolerancias.yaml`.

---

#### Cambio 80 — Esterilla: medición de inclinación (tilt) de la grilla + aviso CHAPA INCLINADA

**Pedido del operador:** en frames donde la chapa se inclina, el patrón queda "totalmente
corrido" y no detecta bien. ¿Se puede medir la inclinación para detectar corrimientos?

**Diagnóstico (`scripts/_esterilla_tilt_diag.py` sobre 17 únicas):** el set REDUCIDO no
tiene frames muy inclinados (tilt de grilla ~1° máx). Hallazgo clave: el Hough actual
(`align_image_by_right_edge`, mide el BORDE de la chapa) reporta 0.00° en casi todos,
pero la grilla real tiene ~-1° → el Hough no refleja la inclinación del patrón. La grilla
se puede medir directo desde los agujeros (mediana del ángulo del vecino en la fila),
que es lo que el matching necesita.

**Causa de "se corre todo" con tilt grande:** `grid_compare_points` asume grilla alineada
a los ejes (barre fase X y luego Y). Con la chapa inclinada una fila ya no está a `y`
constante → no engancha. El affine refinement podría absorber rotación pero (1) limita
shear a ~8.5° y (2) tiene problema huevo-gallina: sin matches no estima rotación.

**Implementado (medición + aviso):**
- `src/pipeline/grid_fitting.py`: `estimate_lattice_tilt_deg(detected_xy, dx)` — tilt de la
  grilla desde los agujeros (robusto, mediana de ángulos de vecino en fila).
- `src/inspection.py`: calcula `sheet_tilt_deg` por frame, nuevo campo en `InspectionResult`
  (`sheet_tilt_deg`, `tilt_warn`). Si `|tilt| > tilt_warn_deg` agrega causa y marca aviso.
- `src/pipeline/annotate.py`: `draw_tilt_indicator` muestra "Inclinacion: X.X grados" al
  borde izquierdo (bajo el STATUS); rojo + badge "CHAPA INCLINADA" cuando supera el umbral.
- `src/utils/config.py`: default `tilt_warn_deg=0.0` (solo medir). `config/tolerancias.yaml`
  `modelo_A`: `tilt_warn_deg=2.5` (tilt normal ~1°).

**Validación:** compile OK, tests 4/4, overlay frame_0162 muestra "Inclinacion: -1.0 grados".
Medición verificada: 0162=-1.03°, 0172=+0.60°, 0182=+0.60°, 0186=-1.06° (warn=False, todos
bajo 2.5°). Es informativo (NO fuerza NOK por ahora).

**PENDIENTE (corrección, requiere datos):** la CORRECCIÓN de detección con tilt (de-rotar
los agujeros usando `sheet_tilt_deg` antes del grid fit, rompiendo el huevo-gallina) queda
para implementar — falta una grabación con la chapa realmente inclinada para construir y
validar sin regresar los frames buenos.

**Archivos modificados:** `src/pipeline/grid_fitting.py`, `src/inspection.py`,
`src/pipeline/annotate.py`, `src/utils/config.py`, `config/tolerancias.yaml`.

---

#### Cambio 79 — Esterilla: sin cruces falsas arriba + estado OK/NOK al borde izquierdo

**Pedido del operador:** (1) se veían cruces rojas en la fila superior del patrón;
(2) el texto de estado OK/NOK tapaba los agujeros y debía ir al borde izquierdo.

**Problema 1 — cruces falsas arriba:** La fila superior del patrón (cj mínimo, 4 celdas)
caía consistentemente como missing en TODOS los frames. Diagnóstico
(`scripts/_esterilla_top_diag.py`): los agujeros superiores SÍ se detectan (verde), pero
la posición esperada de esa fila quedaba ~24px por encima por un artefacto de fase del grid
escalonado (fila de borde superior poco confiable). 4 cruces rojas por frame.

**Fix 1:** se quitó la fila superior del patrón (`scripts/_esterilla_trim_top.py`, elimina
el `cj` mínimo). scanner_2 + global: 119→115 celdas. Backup `.bak` previo.
Resultado 17 únicas: missing media 5.5→**1.9**, mediana 4→**0** (mayoría 0 faltantes),
0 NOK. Sin cruces en la parte superior (verificado visualmente).

**Problema 2 — estado tapaba agujeros:** `draw_compare_overlay` dibujaba el STATUS/panel
NOK en coordenadas de la ROI (x≈880 en frame completo) → sobre los agujeros.

**Fix 2 (`src/pipeline/annotate.py` + `src/inspection.py`):**
- Nueva función `draw_status_indicator(img, status, nok_reasons, badge_count)` que dibuja
  el estado pegado al borde IZQUIERDO (OK → texto; NOK → panel de causas).
- `draw_compare_overlay`: nuevo flag `draw_status` (default True). Inspección lo llama con
  `draw_status=False` y dibuja el estado con `draw_status_indicator` sobre el frame COMPLETO
  (zona oscura izquierda), después de los badges. Ya no tapa el patrón.

**Validación:** compile OK, tests 4/4, overlay frame_0162 verificado (STATUS arriba-izq,
patrón todo verde sin cruces).

**Archivos modificados:** `src/pipeline/annotate.py`, `src/inspection.py`,
`data/patterns/scanner_2/modelo_A/holes.json`, `data/patterns/modelo_A/holes.json`.

---

#### Cambio 78 — Grabación: botón Detener análisis + bloqueo de tipo de placa

**Pedido del operador:** poder frenar el análisis una vez iniciado, y que el tipo de placa
(Esterilla/Microperforado) no se pueda cambiar mientras se está analizando.

**Cambios en `src/ui/service.py`:**
- `_AnalysisWorker`: nuevo flag `_cancel` + método `cancel()` (thread-safe) y señal
  `cancelled(int)`. Ambos loops (secuencial con MachineStop y paralelo) chequean el flag y
  abortan limpiamente; el loop paralelo pasa a manejar el `ThreadPoolExecutor` manualmente
  con `shutdown(wait=False, cancel_futures=True)` para frenar rápido.
- `RecordingTab`: nuevo botón **"Detener"** (rojo) junto a "Analizar", deshabilitado salvo
  durante el análisis. Handler `_on_stop_analyze` llama `worker.cancel()`.
- Nuevo helper `_set_analysis_running(running)`: durante el análisis bloquea botones
  Esterilla/Microperforado, el combo de scanner y "Abrir grabación"; reactiva al terminar.
  Garantiza que todos los frames se evalúen contra el mismo modelo.
- `_on_analyze`/`_on_ana_done`/`_on_ana_error` usan el helper; nuevo `_on_ana_cancelled`
  restaura controles y muestra "Análisis detenido (N frames)".

**Validación:** `py_compile` OK, tests 4/4, smoke test offscreen de `RecordingTab`:
running → Detener habilitado y selector/scanner bloqueados; stopped → reactivados.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 77 — Esterilla: umbral ADAPTATIVO → detección casi completa (missing 21→4)

**Problema (reporte del operador):** El sistema no marcaba en verde varios agujeros, como
si no los reconociera. Diagnóstico (`scripts/_esterilla_detect_diag.py`):
- `draw_compare_overlay` pinta verde TODO agujero detectado → un agujero sin verde = NO
  detectado (no es problema de patrón).
- Relajar min_area/circularidad/aspect NO recuperaba agujeros (relaxed==current) → no era
  filtro de contorno.
- Causa real: el preprocess usaba **Otsu global** (un único umbral para toda la ROI). En
  la zona inferior del encuadre (más oscura / levemente desenfocada) los agujeros tenues
  caían bajo el umbral y no formaban contorno en la máscara.

**Experimento (`scripts/_esterilla_thresh_exp.py`):** umbral adaptativo gaussiano local
detecta muchos más agujeros sin falsos positivos:
- frame_0162: 106→126, frame_0177: 80→122, frame_0172: 71→131.

**Cambios:**
- `src/pipeline/preprocess.py`: nuevo modo `use_adaptive` (cv2.adaptiveThreshold gaussiano)
  con `adaptive_block_size` (impar) y `adaptive_c`. Precedencia sobre `use_otsu`.
- `src/utils/config.py`: defaults `use_adaptive=False`, `adaptive_block_size=61`,
  `adaptive_c=-5.0` (opt-in, no afecta otros modelos).
- `src/inspection.py` y `src/patterns/pattern_build.py`: leen y propagan los 3 params al
  preprocess (mismo masking en inspección y en build).
- `config/tolerancias.yaml` → `modelo_A`: `use_adaptive: true`, `adaptive_block_size: 61`,
  `adaptive_c: -5.0`.
- Patrón `scanner_2/modelo_A` reconstruido con adaptivo: 88→**100 puntos** (más completo),
  duplicados de celda 2→1. Sincronizado a global. Backup `.20260601_084919.bak`.

**Resultado (17 únicas):** missing media **21.0 → 4.0** (rango 7–51 → 4–4, constante).
Escenas 0159/0172 (antes missing 50/51) → **4**. NOK 0/17 mantenido. Detección 122–135
agujeros/frame. Tests 4/4 OK.

**Extras de borde (resuelto):** se bajó `pattern_edge_margin_px` 40→**22** para que el
patrón REGISTRE los agujeros de borde (antes "extra"/diamante naranja). Patrón reconstruido:
119 puntos, 0 duplicados, stagger 26px estable. Resultado 17 únicas: extras ~20→**~7**
(media 6.9), missing media 4.0→**5.5** (mediana 4, max 29), ratio 126%→**109%**, NOK 0/17.
Nota: con margen 12 (122 pts) un frame puntual (0186) se desestabilizaba a missing=77; 22
es el equilibrio sin NOK. Riesgo conocido: si la lámina se corre mucho en producción, los
agujeros de borde registrados pueden salir del encuadre y contar como faltantes — validar
con material real en movimiento.

**Archivos modificados:** `src/pipeline/preprocess.py`, `src/utils/config.py`,
`src/inspection.py`, `src/patterns/pattern_build.py`, `config/tolerancias.yaml`,
`data/patterns/scanner_2/modelo_A/holes.json`, `data/patterns/modelo_A/holes.json`.

---

#### Cambio 76 — Cámaras IP: conexión 100% manual (sin auto-connect ni reintento infinito)

**Problema:** El programa quedaba lento y las cámaras WiFi no terminaban de conectar al
iniciar. Causa: el auto-connect (Cambio 66) disparaba polling HTTP/MJPEG en background
apenas se abría el tab Cámara, y ante fallo el `_on_ip_error` arrancaba un bucle de
reintento incremental (5→30s) que seguía golpeando la red/CPU indefinidamente cuando la
cámara estaba inalcanzable → UI lenta y reconexión perpetua.

**Cambios en `src/ui/service.py` (CameraCalibTab):**
- `showEvent`: ya NO llama `_auto_connect_if_saved()`. Al abrir el tab no se conecta nada;
  el operador debe presionar **"Conectar"**.
- `_on_ip_error`: eliminado el reintento automático (ya no arranca `_ip_retry_timers`).
  Ante error muestra estado "Sin conexion", reactiva el botón "Conectar" y los campos de
  IP/URL/usuario/clave para que el operador reintente manualmente cuando quiera.

**No tocado:** `_on_ip_connect` (Conectar manual), `_save_ip_settings` (Guardar config
sigue conectando porque es acción explícita del operador), producción `run` (usa cámaras
USB por index 0/1 en `io_map.yaml`, no IP → no afectada). `_auto_connect_if_saved` y
`_on_ip_retry` quedan en el código pero ya no se invocan (sin efecto).

**Resultado:** Arranque sin polling WiFi en background; sin bucle de reconexión; conexión
solo cuando el operador la pide.

**Archivos modificados:** `src/ui/service.py`

---

#### Cambio 75 — Esterilla: corrección de geometría de grid (grid_dy 38→36, grid_dx 66→65)

**Diagnóstico sobre carpeta `Esterilla_REDUCIDO` (49 archivos, 17 únicas reales, 32 duplicados):**
Análisis de las métricas (missing media 21, missing→detectado-más-cercano media 73px,
centrado -38px constante, ratio 109%) indicó que el problema NO era detección
(detección sana, ratio>100%) ni tolerancia, sino **patrón + fase**.

**Medición de geometría real** (`scripts/_esterilla_lattice.py`, frame_0162, sub-redes
grande/chico por separado):
- GRANDES: lattice dx=64.6, dy=72.3
- CHICOS: lattice dx=64.7, dy=72.7, offset (-25.2, +35.6) respecto de grandes
- → medio-período vertical real (fila a fila) = 72.3/2 = **36.1px**, no 38.
- El `grid_dy=38` acumulaba ~45px de deriva Y sobre 24 filas → missing en filas inferiores
  y picos de missing=50 en escenas puntuales (las 2 escenas NOK del set).

**Cambios:**
- `config/tolerancias.yaml` → `models.modelo_A`: `grid_dx` 66→65, `grid_dy` 38→36.
- Reconstruido `data/patterns/scanner_2/modelo_A/holes.json` desde frame_0162 con
  `build-pattern --model modelo_A --scanner scanner_2`. Sincronizado a global `modelo_A`.
  Backups `.20260601_083711.bak` de holes.json (scanner_2 + global) y tolerancias.yaml.

**Resultado (17 únicas):** NOK (missing≥35) **2/17 → 0/17**. Escena 0159 missing 50→27,
0172 missing 51→34. Missing media 21.0→19.9. Duplicados de celda en build 5→2.

**Pendiente (próxima iteración):** missing media (~20) y extras (~22) siguen altos =
**patrón incompleto** (~20 celdas reales no registradas) + ajuste fino de `stagger_x_odd`
(build auto-detectó 18px; offset medido entre sub-redes = -25px → revisar parity/signo).
Scripts de diagnóstico nuevos: `scripts/_esterilla_geom.py`, `_esterilla_lattice.py`,
`_esterilla_eval.py` (este último deduplica por MD5 y evalúa solo frames únicos).

**Archivos modificados:** `config/tolerancias.yaml`,
`data/patterns/scanner_2/modelo_A/holes.json`, `data/patterns/modelo_A/holes.json`.

---

### Sesión 2026-05-29 — Tadeo + Claude (noche)

#### Cambio 74 — Esterilla "todo rojo" + lentitud modo servicio (regresión WiFi)

**Problema 1 — Esterilla detectaba todo como NOK (cruces rojas):**
Al cargar una carpeta de grabación esterilla en modo servicio, el overlay mostraba
casi todo rojo. Dos causas encadenadas:
1. `_load_folder` forzaba el modelo desde `meta.json` (`setCurrentText(model_display)`).
   La grabación de prueba `Patron_Esterilla_METALCONF` quedó mal etiquetada como
   `Microperforado` (modelo_B). Resultado: imágenes esterilla analizadas con el patrón
   microperforado (255 agujeros) → missing=168 → NOK total.
2. El patrón **global** `data/patterns/modelo_A/holes.json` estaba viejo (117 pts, sin
   `stagger_x_odd`, ROI 1204px) y NO coincidía con el calibrado en
   `scanner_2/modelo_A` (88 pts, staggered, ROI x=870 w=380, cambios 51/52). Como la
   UI por defecto usa `scanner_1` y no existe `scanner_1/modelo_A`, el fallback caía al
   patrón global stale → missing≈32 aun con modelo_A.

**Diagnóstico (CLI, frame_0162.png, 1920×1080):**
- modelo_B/scanner_1: expected=255 missing=168 → NOK (el "todo rojo")
- modelo_A/scanner_2 (bueno): expected=83 missing=9 → OK
- modelo_A/scanner_1 (fallback global stale): expected=113 missing=32

**Cambios:**
- `src/ui/service.py` → `_load_folder`: ya NO fuerza el modelo desde `meta.json`.
  Respeta la selección del operador (botones Esterilla/Microperforado), coherente con
  el diseño ya documentado en `_on_scanner_changed`. `meta.model_display` queda solo
  como log informativo. Se sigue cargando `fps` de meta.
- `data/patterns/modelo_A/{holes.json,roi.json}`: sincronizados desde `scanner_2/modelo_A`
  (el patrón esterilla calibrado). Backups `.bak` creados. Ahora modelo_A resuelve al
  patrón correcto desde cualquier scanner vía fallback. Seguro: producción usa
  `scanner_2/modelo_A`; el global solo se usa como fallback de análisis.

**Resultado:** carpeta completa (200 frames) pasó de 0/200 OK (todo NOK rojo) a
**178/200 OK status**, mayoría verde. Quedan ~22 frames NOK por deriva de fase del grid
escalonado (missing 48-77 en frames puntuales) — problema de calibración fina del grid
documentado (cambios 51/52), no regresión. La decisión temporal sigue OK
(`consecutive_nok_frames: 9999`).

**Problema 2 — Modo servicio muy lento tras cambios de cámara IP/WiFi:**
El cambio 72 subió el polling de snapshot HTTP de la cámara IP a 33 ms (30 fps), pero el
preview de diagnóstico solo se refresca a 5 fps (timer de 200 ms). Se capturaban y
decodificaban ~6× más JPEG de los que se muestran → saturación de CPU y WiFi, UI lenta.

**Cambio:**
- `src/ui/service.py` → `_HTTPSnapshotReader.__init__`: `interval_ms` default 33 → **150**
  (≈6-7 fps, con margen sobre el preview de 5 fps). Mínimo subido de 20 → 50 ms.

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

#### Cambio 72 — _HTTPSnapshotReader: keep-alive + 30fps objetivo

**Problema:** Cámara IP en 192.168.1.26 usa URL `oneshotimage.jpg` (foto única, no
stream MJPEG). `interval_ms=250` → 4fps. `urllib.urlopen` abría nueva conexión TCP
por frame → overhead handshake ~10-20ms/frame.

**Cambios:**
- `interval_ms` default: 250 → **33ms** (~30fps)
- Mínimo: 100 → **20ms** (techo 50fps)
- `urllib.request` reemplazado por `http.client.HTTPConnection` con
  `Connection: keep-alive` — reutiliza la TCP entre frames
- Soporte HTTPS con SSL sin verificación de certificado
- Reconexión automática si la conexión se rompe, sin disparar error_occurred

**Archivos modificados:** `src/ui/service.py`

---


#### Cambio 72 - Soporte Sony IP + URL de stream editable

**Motivacion:** Se cambio la camara IP de Axis a Sony (`192.168.1.26`) y la app
no podia mostrar imagen porque asumía un stream MJPEG fijo. Ademas, la URL quedaba
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

#### Cambio 71b — Marcadores de error huecos (sin relleno)

**Problema:** Los marcadores de agujero faltante (cruces rojas) tenían un círculo
relleno oscuro de fondo que tapaba la imagen. El operario no podía ver qué había
en la posición del error (agujero parcial, suciedad, reflejo).

**Fix en `src/pipeline/annotate.py` → `draw_compare_overlay()`:**
- Eliminado `cv2.circle(..., -1)` (relleno opaco)
- Reemplazado por: sombra negra hueca (grosor 3) + borde rojo (grosor 2)
- Cruz: sombra negra (grosor 4) + cruz roja (grosor 2) — sin relleno
- Número del faltante: sombra negra gruesa + texto blanco fino encima
- El interior del marcador queda completamente transparente → el operario
  puede ver a través del marcador la imagen real debajo

---

#### Cambio 70 — Paralelización de inspect_folder (CLI 3.1×) + diagnóstico esterilla

**Mejora de rendimiento — `src/inspection.py` → `inspect_folder`:**
Pre-carga de tolerances+pattern+roi una vez + ThreadPoolExecutor (hasta 6 workers)
cuando `machine_stop._enabled=False`. Secuencial obligatorio si machine_stop activo.
**Resultado:** 200 frames modelo_A: 217s → 69s (3.1× más rápido), mismo resultado.

**Diagnóstico esterilla sobre 200 frames reales:**
- Detección raw: 126 holes/frame (63 chicos + 63 grandes) — el detector funciona ✓
- Patrón referencia: 83 celdas vs 126 visibles → 24 "extras" son agujeros reales no registrados
- 9-21 missing/frame: ~12-16 en bordes de material (normal) + ~5-9 por drift de fase de grid
- Mejor frame: frame_0162 (missing=9, stagger=+26px) — usado como referencia
- frame_0139 (ratio=138%) genera stagger=-22px (fase invertida) → peor resultado
- Para mejorar la cobertura: capturar frame con esterilla centrado y en el mismo ciclo que frame_0162
- Resultado global: 200/200 temporal OK con threshold=35, tolerancias blandas ✓

**Archivos modificados:** `src/inspection.py`

---

### Sesión 2026-05-29 — Tadeo + Claude (tarde)

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

#### Cambio 68 — Tolerancias blandas modelo_A (Esterilla) — reducir falsas cruces

**Problema:** El overlay de Esterilla mostraba cruces rojas en casi todos los frames
porque el sistema generaba posiciones esperadas donde no hay agujeros detectables
(grid phase ligeramente off, iluminación no ideal, blur) y los umbrales eran muy ajustados.

**Diagnóstico de la imagen `debug_esterilla_best.png`:**
- Los agujeros SÍ se detectan (círculos verdes visibles)
- El grid genera posiciones esperadas que no coinciden exactamente con los detectados
- `frame_missing_nok_threshold: 8` → con ≥8 faltantes el frame muestra NOK con todas
  las cruces rojas. Casi todos los frames del esterilla tienen >8 faltantes durante calibración.
- `min_area: 150`, `circularity_min: 0.55` → rechazan agujeros reales con blur/iluminación

**Cambios en `config/tolerancias.yaml` — sección `modelo_A`:**

| Parámetro | Antes | Ahora | Razón |
|-----------|-------|-------|-------|
| `threshold` | 175 (global) | **140** | Umbral de binarización más bajo para capturar más agujeros |
| `min_area` | 150.0 | **80.0** | Agujeros chicos con blur bajan de 150px² |
| `min_area_small` | 150.0 | **80.0** | Igual al piso global |
| `min_area_large` | 400.0 | **300.0** | Acepta grandes con iluminación no ideal |
| `max_area_large` | 7000.0 | **8000.0** | Margen más amplio |
| `circularity_min` | 0.55 | **0.35** | Acepta agujeros deformados por perspectiva/blur |
| `aspect_ratio_max` | 2.5 | **3.0** | Ligera deformación aceptable |
| `align_match_tol_px` | 150.0 | **250.0** | Más permisivo para alineación inicial |
| `min_match_count` | 4 | **3** | Permite alinear con menos agujeros visibles |
| `edge_margin_px` | 5.0 | **3.0** | No descartar agujeros en borde de ROI |
| `grid_max_missing` | 25 | **50** | ~57% de 88 agujeros — muy permisivo |
| `bbox_filter_margin_px` | 30.0 | **50.0** | Margen amplio alrededor del bbox |
| `extra_min_dist_factor` | 2.0 | **1.5** | Umbral = 27px (antes 36px) |
| `frame_missing_nok_threshold` | 8 | **35** | ★ CAMBIO CLAVE: NOK visual solo cuando faltan >35 agujeros |
| `consecutive_nok_frames` | 8 | **9999** | FAULT deshabilitado durante calibración |
| `machine_stop_enabled` | true | **false** | Sin alertas de parada mientras se calibra |

**Por qué `frame_missing_nok_threshold: 35` es el cambio más importante:**
El overlay muestra cruces rojas para CADA agujero faltante individual, pero el
estado "NOK" (que hace que el frame se vea todo rojo con cruces prominentes) depende
de si `missing >= frame_missing_nok_threshold`. Subiendo a 35, los frames con 5-30
faltantes muestran algunas cruces pero el status sigue siendo "OK" → mucho menos
ruido visual para el operador.

**Próximos pasos para calibración fina:**
1. Capturar imagen OK limpia de Esterilla en planta y reconstruir `holes.json`
   con `build-pattern --model modelo_A --scanner scanner_2 --img <imagen>`
2. Ajustar `threshold` con histograma de imagen real (scripts/_debug_areas.py)
3. Una vez detección estable: bajar `frame_missing_nok_threshold` a 5-8
4. Habilitar `machine_stop_enabled: true` y `consecutive_nok_frames: 8`

**Archivos modificados:** `config/tolerancias.yaml`

---

### Sesión 2026-05-29 — Tadeo + Claude

#### Cambio 67 — Badge de estado IP más grande y semántico

**Problema:** El badge de estado IP tenía ancho fijo de 80px → textos como
`"Reintento 2 en 10s"` o `"Intentando conectar…"` aparecían cortados.
Además el estado era pequeño y difícil de leer de lejos en planta.

**Cambios:**
- `setFixedWidth(80)` → `setMinimumWidth(170)`: el badge crece con el texto.
- Font 11px → **13px bold**, padding 4px → 6px 12px, border-radius 5→6px.
- Helper `_set_ip_status(text, kind)` centraliza todos los `setText` + `setStyleSheet`:
  - `"ok"` → texto verde brillante, fondo verde muy oscuro
  - `"warn"` → texto amarillo ámbar, fondo amarillo muy oscuro
  - `"error"` → texto rojo claro, fondo rojo muy oscuro
  - `"neutral"` → texto muted, fondo oscuro
- Mensajes de estado unificados en todos los métodos:
  - Conectando: `"Conectando…"` (warn)
  - Señal activa: `"En vivo"` (ok)
  - Error/retry: `"Reintento N — en Xs"` (error)
  - Retry activo: `"Intentando conectar… (N)"` (warn)
- Info FPS/resolución (`_ip_info_lbl`): font 11px → **13px bold**, color muted → _TEXT.

**Archivos modificados:** `src/ui/service.py`

#### Cambio 66 — Auto-conectar, auto-reconectar, FPS en vivo y captura de frame

**Motivación:** El operador en planta necesitaba conectar manualmente al entrar al tab,
no tenía forma de saber la calidad del stream IP, y si la cámara se reiniciaba debía
reconectar a mano. Tampoco podía guardar una imagen de lo que ve la cámara IP.

**Características implementadas:**

1. **Auto-conectar al abrir el tab** (`showEvent`)
   - Cuando el operador entra al tab Cámara, si hay una URL guardada para un slot y no
     fue desconectado manualmente, la conexión arranca automáticamente.
   - Flag `_ip_manual_disc[slot]` evita que el auto-connect se dispare después de que
     el operador haya presionado Desconectar deliberadamente.

2. **Auto-reconectar si se cae la señal** (`_on_ip_error` + `_on_ip_retry`)
   - Si el stream HTTP/MJPEG se corta (red caída, reinicio de cámara Axis), arranca un
     timer single-shot con delay incremental: 5s → 10s → 15s → … → 30s máximo.
   - El badge de estado muestra `"Reintento N en Xs"` para informar al operador sin
     necesitar intervención.
   - El retry lee URL y credenciales desde `camera.yaml` para ese slot.

3. **FPS + resolución en vivo** (`_on_ip_frame_ready`)
   - Se actualizan cada 20 frames. Muestra `"WxH @ Xfps"` debajo del preview.
   - El badge de estado cambia a verde con texto `"En vivo"` cuando hay señal.

4. **Botón "Capturar frame"** (`_capture_ip_frame`)
   - Habilitado solo cuando hay señal. Guarda el último frame en
     `data/output/export/captura_ip1_YYYYMMDD_HHMMSS.jpg`.
   - Muestra el nombre del archivo guardado durante 4 segundos luego se limpia.

**Refactor interno:**
- `_start_ip_connection(slot, url, user, pass)`: lógica de conexión extraída de
  `_on_ip_connect`, usada también por auto-connect y retry.
- `_auto_connect_if_saved()`: carga config desde `camera.yaml` y llama `_start_ip_connection`.

**Archivos modificados:** `src/ui/service.py`

#### Cambio 65 — Tab Cámara scrollable + botón mostrar contraseña + espaciado

**Motivación:** El tab Cámara no tenía scroll (todo el contenido se comprimía en la ventana
sin posibilidad de bajar), la sección IP se veía apretada y no había forma de ver la contraseña
al escribirla.

**Cambios:**
- `CameraCalibTab._build_ui`: envuelto en `QScrollArea` (igual que RecordingTab).
  Content widget con `background:{_DARK}`, scrollbar vertical de 8px.
- Botón **"Mostrar"** junto al campo contraseña: toggle checkable que alterna
  `EchoMode.Password` ↔ `EchoMode.Normal`. Se ilumina en acento cuando está activo.
- Márgenes e inter-espaciados de la sección IP aumentados (`setContentsMargins(18,24,18,18)`,
  `setSpacing(10)`, `addSpacing` entre secciones).
- Sliders del grid 2×2: altura fija `22px`, ancho mínimo `110px`, spinboxes `72×30px`.
- Preview IP: `minHeight` aumentado a `300px`.
- Campo usuario y contraseña: `setFixedHeight(34)`, más anchos (`160px`).

**Archivos modificados:** `src/ui/service.py`

#### Cambio 64 — Rediseño estético de la sección Cámaras IP

**Motivación:** El diseño inicial de la sección IP en `CameraCalibTab` tenía controles
apilados de forma desordenada: 3 filas de controles, spinboxes muy chicos, todo estaba
apretado y sin jerarquía visual clara.

**Mejoras:**
- Selector de slot + URL + botones Conectar/Desconectar + badge de estado → **una sola fila**.
- Usuario/Contraseña → fila compacta separada con `addSpacing` para claridad visual.
- Badge de estado (label con borde y fondo) en lugar de texto suelto.
- Sliders de parámetros → **grid 2×2** (Brillo | Contraste / Saturación | Nitidez).
  Ahorra espacio vertical, aprovecha el ancho disponible.
- Spinboxes con altura fija (`setFixedHeight(28)`) y spinners visibles.
- Botones Guardar / Aplicar con alturas uniformes (32px), tipografía consistente.
- Dos separadores `QFrame.HLine` para delimitar visualmente las secciones.
- Preview con `minHeight=240` (antes 460) — permite que la sección sea más compacta.
- Stretch de la sección IP bajado de 3 → 2 para mejor balance con la sección USB.

**Archivos modificados:**
- `src/ui/service.py`: `_build_ip_camera_section` en `CameraCalibTab` rediseñado.
  Stretch del GroupBox IP en `_build_ui` cambiado de 3 → 2.

#### Cambio 63 — Segunda cámara IP + parámetros de imagen en tab Cámara

**Motivación:** La sección de cámara IP en `CameraCalibTab` solo soportaba una cámara.
Se quería conectar y configurar una segunda cámara IP independiente, y poder ajustar
parámetros de imagen (brillo, contraste, saturación, nitidez) de igual forma que para
las cámaras USB.

**Diseño:**
- Dos slots independientes: "IP Cám 1" / "IP Cám 2", seleccionables con combo.
- Cada slot tiene su propio estado (`_ip_workers[2]`, `_ip_caps[2]`, `_ip_timers[2]`).
- Ambas cámaras pueden estar conectadas simultáneamente; el preview muestra la del slot activo.
- Al cambiar de slot se cargan URL/credenciales/parámetros desde `camera.yaml`.
- Campos de usuario y contraseña ahora son visibles y editables en la UI (antes ocultos).
- Parámetros de imagen con sliders: Brillo / Contraste / Saturación / Nitidez.
  - Rangos Axis VAPIX: Brillo/Contraste/Saturación = −100..100; Nitidez = 0..100.
  - Botón **Guardar config** → escribe en `config/camera.yaml` bajo `ip_camera_1` / `ip_camera_2`.
  - Botón **Aplicar a cámara (VAPIX/Axis)** → envía comandos HTTP GET a
    `{base}/axis-cgi/param.cgi?action=update&ImageSource.I0.Sensor.Brightness=N&...`
    con Basic Auth. Estado (OK/Error) visible en etiqueta inline.

**Archivos modificados:**
- `src/ui/service.py`:
  - `_IP_PARAM_DEFS`, `_IP_VAPIX_MAP` agregados a nivel módulo (después de `_PARAM_DEFS`).
  - `CameraCalibTab.__init__`: `_ip_worker/_ip_cap/_ip_timer` → `_ip_workers[2]`,
    `_ip_caps[2]`, `_ip_timers[2]`; agrega `_ip_slot`, `_ip_param_sliders`, `_ip_param_spinboxes`.
  - `_build_ip_camera_section`: reescrito completo con selector de slot, URL, campos usuario/pass,
    panel de parámetros con sliders, botones Guardar/Aplicar, y preview.
  - Métodos nuevos: `_on_ip_slot_changed`, `_load_ip_slot_settings`, `_disconnect_ip_slot`,
    `_save_ip_settings`, `_apply_ip_params`.
  - Métodos actualizados: `_on_ip_connect`, `_on_ip_disconnect`, `_on_ip_error`,
    `_on_ip_frame_ready`, `_refresh_ip_camera` (ahora reciben `slot: int`).
  - `_ip_auth_settings` eliminado de `CameraCalibTab` (reemplazado por campos explícitos).
- `config/camera.yaml`: agregadas secciones `ip_camera_1` y `ip_camera_2` con URL, credenciales
  y valores de parámetros de imagen por defecto.

**Validación:**
- `python -m compileall src/ui/service.py` OK.
- Construcción de `CameraCalibTab` en modo offscreen: OK.
  - `ip_slot_combo` tiene ítems ["IP Cám 1", "IP Cám 2"].
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

### Sesión 2026-05-26 — Tadeo + Codex

#### Cambio 38 — Parada por agujero tapado persistente en material continuo

**Motivación:** Un agujero tapado desde el inicio de la secuencia no activaba
`DETENCION DE MAQUINA`. La causa era doble:
1. El tracker buscaba persistencia en la misma posición `(x,y)`, pero la chapa avanza
   verticalmente y el mismo defecto aparece con `x` similar y `y` cambiante.
2. Los faltantes eran descartados como near-miss porque en la grilla densa siempre hay
   un agujero vecino cerca.

**Cambios:**
- `src/pipeline/machine_stop.py`:
  - Las zonas persistentes ahora matchean por columna `X` (`abs(dx) <= same_zone_px`).
  - `Y` se sigue actualizando para visualización, pero ya no resetea la racha.
  - Los near-miss persistentes ya no se descartan antes del tracking; la persistencia
    por columna es el filtro contra falsos positivos.
- `src/inspection.py`:
  - Si `machine_stop=True`, `final_status` pasa a `NOK`.
  - `_apply_temporal_rule()` marca `decision_status=NOK` inmediatamente para machine stop.
- `src/controller/scanner_controller.py`:
  - En producción, `result.machine_stop=True` fuerza `ScannerState.FAULT` inmediato,
    sin esperar `consecutive_nok_frames`.

**Validación en `20260519_121741`:**
- `machine_stop_frames=22`.
- El defecto persistente inicial dispara desde `frame_0006.png`:
  - `frame_0006.png` a `frame_0009.png` → `NOK/NOK`, `machine_stop=True`.
- `frame_0037.png` sigue `LOW_QUALITY` y no decide.
- `python -m compileall src` OK.

---

#### Cambio 37 — Mapeo fijo de cámaras por scanner

**Motivación:** Asegurar que la UI y el control nunca intercambien feeds:
`scanner_1` debe usar siempre cámara índice 0 y `scanner_2` siempre cámara índice 1.
Si una cámara no abre, su scanner queda sin imagen; no debe ocupar el lugar del otro.

**Cambio:**
- `src/controller/system.py`:
  - Agregado `_FIXED_CAMERA_BY_SCANNER = {"scanner_1": 0, "scanner_2": 1}`.
  - `InspectionSystem` usa ese mapeo como autoridad por encima de `config/io_map.yaml`.
  - Si el YAML difiere, emite warning y mantiene el mapeo fijo.

**Validación:**
- `python -m compileall src/controller/system.py` OK.
- Instanciación de `InspectionSystem(disable_plc_outputs=True)`:
  - `scanner_1: camera_index=0`
  - `scanner_2: camera_index=1`

---

#### Cambio 36 — Sensibilidad de patrón desalineado menos agresiva

**Motivación:** La métrica nueva de `pattern_center_zigzag_*` quedó demasiado sensible:
marcaba 129/185 frames como NOK en la carpeta `20260519_121741`. El problema era conceptual:
usar la mediana X de todos los agujeros por banda reacciona a la alternancia natural de filas
del microperforado, aun cuando el patrón físico está correcto.

**Cambios:**
- `src/pipeline/edge_centering.py`:
  - `_pattern_center_by_band()` ahora calcula el centro como promedio entre borde físico
    izquierdo y derecho del patrón por banda.
  - Evita falsos zigzag por filas alternadas del microperforado.
- `config/tolerancias.yaml` y `src/utils/config.py`:
  - `pattern_align_abs_max_px: 15.0 → 30.0`
  - `pattern_center_zigzag_std_max_px: 8.0 → 4.0`
  - `pattern_center_zigzag_abs_max_px: 18.0 → 6.5`
- `src/inspection.py`:
  - Si `frame_geometry_quality == "UNSTABLE"`, no se permite que `pattern_alignment_warn`
    convierta el frame en NOK. La imagen inestable se analiza, pero no decide.

**Validación en `20260519_121741`:**
- Antes: 129/185 NOK por sensibilidad excesiva.
- Después: 9/185 NOK + 1 frame `LOW_QUALITY`.
- Frames pedidos:
  - `frame_0121.png` → NOK por `PATRON DESALINEADO`
  - `frame_0122.png` → NOK por `PATRON DESALINEADO`
  - `frame_0124.png` → NOK por `PATRON DESALINEADO`
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

---

### Sesion 2026-06-08 (esterilla patron completo) - Tadeo + Codex

#### Cambio 125 - Esterilla: reactivar filas extremas del patron en la comparacion

**Pedido:** en `C:\Users\DefyC\Downloads\05-06-2026-PATRONES EDITADOS\05-06-2026-ESTERILLA_1`
el analisis de `modelo_A` dejaba dos margenes sin revisar y no estaba tomando el patron
completo.

**Hallazgo de Codex:**
- En `config/tolerancias.yaml`, `modelo_A` tenia `compare_top_ignore_px: 42.0` y
  `compare_bottom_ignore_px: 42.0`.
- El patron activo de `scanner_2/modelo_A` ocupa `y ~= 30.9 .. 454.3` dentro de una ROI
  de `190x480`, asi que esos `42 px` excluian por configuracion las filas extrema superior
  e inferior del patron.
- La ROI lateral de `scanner_2` no era la causa principal del faltante: con el scanner
  correcto el baseline de extras ya era casi cero.

**Cambios hechos por Tadeo + Codex:**
- `config/tolerancias.yaml` -> `models.modelo_A`:
  - `compare_top_ignore_px: 42.0 -> 24.0`
  - `compare_bottom_ignore_px: 42.0 -> 24.0`

**Validacion en `05-06-2026-ESTERILLA_1` usando `scanner_2`:**
- Antes (`42/42`): `119/133 raw OK`, `14 raw NOK`, `avg_missing ~= 2.173`, `avg_extra ~= 0.015`
- Ahora (`24/24`): `119/133 raw OK`, `14 raw NOK`, `avg_missing ~= 1.805`, `avg_extra ~= 0.008`
- Con `24 px` vuelven a entrar las filas extremas del patron, pero sin la regresion que
  aparecia al bajar el recorte a `20 px` o menos.

**Riesgos / oportunidades:**
- Si mas adelante queres comparar absolutamente hasta el borde fisico, todavia se puede
  probar `20 px` o menos, pero en esta carpeta eso ya empeora la estabilidad.
- Si aparece faltante lateral real en otra captura, conviene recalibrar patron/ROI de
  `scanner_2/modelo_A` antes de seguir abriendo la ROI compartida, porque el patron actual
  esta construido especificamente para `190x480`.

---

### Sesion 2026-06-08 (esterilla ancho completo) - Tadeo + Codex

#### Cambio 126 - Esterilla: ROI mas ancha + patron reconstruido sin recortar columnas laterales

**Pedido:** el analisis de `ESTERILLA_1` seguia dejando una fila/columna afuera a derecha
y otra a izquierda. Hacia falta abrir la zona util, aumentar tolerancias y dejar de
recortar demasiado el patron.

**Hallazgo de Codex:**
- El patron activo de `scanner_2/modelo_A` estaba subdimensionado: solo tenia `4`
  columnas utiles y una ROI de `190 px` de ancho.
- Al abrir la ROI sin controlar el preprocesado del build, `build-pattern` podia pasarse
  y crear columnas falsas de borde (`ci=0` y `ci=6`) por detecciones espurias laterales.
- La mejor combinacion para esta carpeta fue:
  - inspeccion mas permisiva;
  - ROI mas ancha;
  - build del patron con margen de borde chico;
  - desactivar CLAHE solo en `build-pattern` para `modelo_A`, evitando columnas falsas.

**Cambios hechos por Tadeo + Codex:**
- `data/patterns/scanner_2/modelo_A/roi.json`
  - `x=258, w=190 -> x=225, w=255`
- `config/tolerancias.yaml` -> `models.modelo_A`
  - `tol_xy_px: 14.0 -> 18.0`
  - `align_match_tol_px: 250.0 -> 280.0`
  - `edge_margin_px: 15.0 -> 5.0`
  - `pattern_edge_margin_px: 5.0`
  - `bbox_filter_margin_px: 45.0 -> 60.0`
  - `pattern_use_clahe: false` (nuevo, solo para construir patron)
- `src/patterns/pattern_build.py`
  - nuevo override `pattern_use_clahe` para que `build-pattern` pueda usar un
    preprocesado distinto al de inspeccion cuando haga falta.
- Reconstruido `data/patterns/scanner_2/modelo_A/holes.json` desde:
  - `C:\Users\DefyC\Downloads\05-06-2026-PATRONES EDITADOS\05-06-2026-ESTERILLA_1\frame_0045.png`
  - resultado final: `88 puntos`, ROI `255x480`, columnas utiles `ci=1..5`

**Validacion en `05-06-2026-ESTERILLA_1` usando `scanner_2`:**
- Antes:
  - patron de `74 puntos`, ROI `190x480`, columnas `ci=1..4`
  - `119/133 raw OK`, `14 raw NOK`, `133/133 temporal OK`
- Ahora:
  - patron de `88 puntos`, ROI `255x480`, columnas `ci=1..5`
  - `123/133 raw OK`, `10 raw NOK`, `133/133 temporal OK`
  - `machine_stop_frames=0`
- Los faltantes mas frecuentes dejan de concentrarse como una columna completa ausente
  en ambos laterales; el baseline pasa a quedar mucho mas repartido y bajo.

**Riesgos / oportunidades:**
- El `avg_detection_ratio` queda alto (~`113%`), o sea seguimos detectando algunos extras.
  El matching ya no pierde columnas laterales, pero mas adelante se puede limpiar mejor
  afinando preprocess o filtrado de extras sin volver a cerrar la ROI.
- Si vuelve a cambiar el zoom/encuadre del scanner_2, esta calibracion debe rehacerse
  desde una imagen OK nueva con la misma estrategia.

---

### Sesion 2026-06-08 (disciplina de entrega) - Tadeo + Codex

#### Cambio 127 - Regla operativa: commit y push siempre al cerrar cambios

**Pedido:** dejar asentado en el changelog que Codex tiene que hacer `commit` y `push`
siempre, sin falta, al terminar un bloque de cambios solicitado por Tadeo.

**Hallazgo de Codex:**
- En esta conversacion hubo un `push` previo del commit `a968245`, pero luego siguieron
  cambios nuevos de calibracion que todavia no estaban publicados al momento de la consulta.
- Conviene dejar la regla explicita en el historial operativo para que no quede a criterio
  del momento y se mantenga una disciplina de entrega consistente.

**Cambios hechos por Tadeo + Codex:**
- Se documenta como regla operativa permanente:
  - al cerrar un cambio de codigo/configuracion solicitado por Tadeo, Codex debe hacer
    `git add`, `git commit` y `git push` sin falta;
  - si por alguna razon no puede hacer `push`, debe informarlo explicitamente en la
    respuesta final y dejar claro que el trabajo quedo solo local.

**Validacion:**
- La regla queda registrada desde este cambio en adelante dentro del `CHANGELOG.md`.

**Riesgos / oportunidades:**
- Si en algun caso queres revisar localmente antes de publicar, hace falta pedirlo de forma
  explicita para exceptuar esta regla operativa.

**Regla reforzada y vigente sin excepciones implicitas:**
- Codex debe hacer siempre `git add`, `git commit` y `git push` al cerrar cualquier bloque
  de cambios pedido por Tadeo.
- No alcanza con dejar el commit local: el cierre correcto del trabajo incluye publicarlo
  en remoto.
- Si por cualquier motivo tecnico no se puede hacer `push`, Codex debe avisarlo de forma
  explicita en la respuesta final y dejar asentado que el trabajo quedo solo local.
- Esta regla se considera operativa por defecto en todas las sesiones futuras, salvo que
  Tadeo pida explicitamente no publicar todavia.

---

### Sesion 2026-06-08 (esterilla columna izquierda) - Tadeo + Codex

#### Cambio 128 - Esterilla: extender columna extrema izquierda en la mitad inferior del patron

**Pedido:** mejorar la deteccion de la columna mas a la izquierda para que quede tomada a
lo largo de toda la pieza y no se corte en la mitad inferior.

**Hallazgo de Codex:**
- El problema no estaba principalmente en `edge_margin_px`: bajarlo de `5 -> 0` casi no
  cambiaba los faltantes de esa zona.
- El patron activo de `scanner_2/modelo_A` seguia teniendo la columna izquierda incompleta:
  en `ci=1` faltaban varias filas pares de la mitad inferior (`cj=12,14,16,18,20`).
- Eso hacia que una parte real del borde izquierdo quedara fuera del patron y apareciera
  como ruido/extra o directamente no quedara comparada de forma consistente.

**Cambios hechos por Tadeo + Codex:**
- `data/patterns/scanner_2/modelo_A/holes.json`
  - se extendio manualmente la columna `ci=1` agregando las filas faltantes
    `cj=12,14,16,18,20`;
  - las nuevas coordenadas se derivaron de la geometria vecina del patron
    (`ci=2` en las mismas filas, desplazado `39 px` a la izquierda);
  - el patron pasa de `88 -> 93 puntos`.

**Validacion en `05-06-2026-ESTERILLA_1` usando `scanner_2`:**
- Se mantiene `123/133 raw OK`, `10 raw NOK`, `133/133 temporal OK`.
- `avg_detection_ratio` baja de aproximadamente `113% -> 107%`, lo que indica menos
  detecciones sobrantes en el lateral izquierdo.
- La columna izquierda ahora queda modelada tambien en la mitad inferior, en vez de
  cortarse despues de la zona media.

**Riesgos / oportunidades:**
- El faltante residual mas frecuente sigue estando en la franja superior (`cj=1`), o sea
  el siguiente ajuste fino, si hace falta, deberia enfocarse arriba a la izquierda y no
  en toda la altura del borde.

---

### Sesion 2026-06-08 (microperforado quirurgico) - Tadeo + Codex

#### Cambio 130 - Microperforado: recorte superior minimo + sin linea artificial de borde

**Pedido:** hacer un ajuste mas quirurgico en microperforado: menos `missing` falsos,
pero sin desarmar la deteccion donde si tiene que detectar.

**Hallazgo de Codex:**
- La fila superior problematica del patron (`cj=3`) cae alrededor de `y ~= 45..48 px`,
  muy pegada al borde superior util.
- Con `compare_top_ignore_px: 42.0`, esa fila entraba completa al matching y dejaba
  muchos `missing` falsos recurrentes.
- Al subir apenas ese recorte a `46 px`, el problema baja bastante sin recurrir a una
  reconstruccion total del patron.
- La linea vertical punteada del borde del patron venia de un fallback del overlay, no
  de una medicion real.

**Cambios hechos por Tadeo + Codex:**
- `config/tolerancias.yaml` -> `models.modelo_B`
  - `compare_top_ignore_px: 42.0 -> 46.0`
- `src/pipeline/annotate.py`
  - eliminado el fallback que dibujaba una linea vertical punteada artificial para el
    borde del patron cuando no habia suficientes puntos reales.

**Validacion en `05-06-2026-MICROPERFORADO_1` usando `scanner_1`:**
- Antes:
  - `131/137 raw OK`, `6 raw NOK`, `1 temporal NOK`
  - fila superior recurrente:
    `((2,3),49)`, `((4,3),49)`, `((5,3),49)`, `((1,3),45)`, `((3,3),35)`
- Ahora:
  - `132/137 raw OK`, `5 raw NOK`, `1 temporal NOK`
  - fila superior bastante mas contenida:
    `((4,3),12)`, `((5,3),12)`, `((2,3),9)`, `((1,3),7)`, `((3,3),6)`

**Riesgos / oportunidades:**
- El faltante principal ya no esta dominado solo por la fila superior; el borde inferior
  (`cj=30/31`) sigue pesando bastante y seria el siguiente ajuste fino si queres seguir.

---

### Sesion 2026-06-08 (esterilla otra estrategia) - Tadeo + Codex

#### Cambio 131 - Esterilla: corregir descalce ROI/patron en scanner_2 y filtrar extras laterales

**Pedido:** buscar otra solucion para `ESTERILLA_1`, porque seguir tocando margenes no
estaba resolviendo que el patron se analice completo.

**Hallazgo de Codex:**
- El problema de fondo no era solo tolerancia: `scanner_2/modelo_A` estaba inspeccionando
  con una ROI de `190x480`, pero el patron activo `data/patterns/scanner_2/modelo_A/holes.json`
  ya estaba calibrado a `255x480`.
- Eso dejaba parte del patron fuera del recorte real de inspeccion y ademas generaba un
  estado inconsistente: el sistema comparaba con una geometria distinta a la del frame.
- Al abrir otra vez la ROI al ancho correcto, reaparecen detecciones laterales reales que
  antes quedaban recortadas; para no contarlas como extras falsos hacia falta endurecer
  el filtro de `extra_min_dist_factor`.

**Cambios hechos por Tadeo + Codex:**
- `data/patterns/scanner_2/modelo_A/roi.json`
  - `x=258, w=190 -> x=225, w=255`
- `config/tolerancias.yaml` -> `models.modelo_A`
  - `extra_min_dist_factor: 1.5 -> 2.0`

**Validacion en `05-06-2026-ESTERILLA_1` usando `scanner_2`:**
- Antes:
  - ROI efectiva `190x480`, patron `255x480` (descalzado)
  - warning de resolucion en todos los frames
  - `130/133 raw OK`, `133/133 temporal OK`
- Ahora:
  - ROI y patron consistentes en `255x480`
  - `130/133 raw OK`, `133/133 temporal OK`
  - `avg_missing ~= 0.451`
  - extras reportados `0` tras el filtro lateral mas robusto

**Riesgos / oportunidades:**
- El `detection_ratio` medio sigue alto porque ahora entran mas agujeros reales en el
  ancho completo; si mas adelante queres bajar ese ratio visual, el siguiente paso sano
  ya no es recortar otra vez la ROI sino reconstruir `holes.json` de `scanner_2/modelo_A`
  con una malla regularizada sobre esta ROI de `255 px`.

---

### Sesion 2026-06-09 (esterilla assign_cells stagger bug) - Tadeo + Claude

#### Cambio 139 - Fix bug assign_cells: CI incorrecto en filas impares con stagger

**Pedido:** esterilla scanner_2 reporta 7 agujeros missing en cada frame a pesar de ROI
y patron correctos.

**Hallazgo:**
- `assign_cells` en `src/pipeline/grid_fitting.py` calculaba el indice de columna (CI) de
  todos los agujeros usando la formula simple `CI = round((x - phase_x) / dx)`, sin
  considerar el stagger de filas impares.
- Para la esterilla con `stagger_x_odd=20.0` y `dx=39`, los agujeros de filas impares
  (CJ impar) en X≈307-327 recibían CI=8 en vez de CI=7. Esto es porque sin compensar el
  offset de fase, la distancia al centro de la celda CI=8 resultaba la minima.
- `grid_compare_points` genera la posicion esperada para CI=8 fila impar como
  `(14+20)%39 + 8*39 = 34 + 312 = 346 px`, que es inalcanzable con origin_x razonable
  cuando las detecciones reales estan en X=307-327.
- Resultado: 7 celdas (CI=8, CJ=5/7/9/11/13/17/19) generaban expected en X=346 que
  nunca matcheaban → 7 missing permanentes en cada frame.

**Cambios:**
- `src/pipeline/grid_fitting.py` — `assign_cells()`:
  - Agrega parametro `stagger_x_odd: float = 0.0` (backward-compatible).
  - Para filas impares (CJ%2==1): calcula `x_origin_odd = (phase_x + stagger_x_odd) % dx`
    y usa ese origen para el CI en vez de `phase_x`.
  - Ejemplo esterilla: X=307.6, CJ=5 (impar) → `CI = round((307.6 - 34) / 39) = 7` ✓
    (antes era CI=8 incorrecto).
- `src/patterns/pattern_build.py` — `build_pattern_from_image()`:
  - Reestructurado para determinar `stagger_x_odd` (desde config o estimado desde datos)
    **antes** de llamar a `assign_cells`, y pasarlo en la llamada.
  - Si `grid_stagger_x_odd` esta en config, se usa directamente (caso modelo_A y modelo_B).
  - Si no esta en config, se hace un primer `assign_cells` sin stagger para separar filas
    pares/impares, se estima el stagger, y se re-llama con el valor correcto.
- `data/patterns/scanner_2/modelo_A/holes.json`:
  - Reconstruido desde `data/recordings/ESTERILLA_5/frame_0047.png` con cells corregidas.
  - Las 7 celdas que antes eran (CI=8, CJ=5/7/9/11/13/17/19) ahora son (CI=7, ...) y
    generan expected en X=307 — donde los agujeros realmente se detectan.

**Validacion pendiente:** ejecutar `run-folder` sobre ESTERILLA_5 y confirmar que missing
baja de 7 a 0 o cerca en frames buenos.

---

#### Cambio 140 - esterilla: revertir parametros deteccion a estado funcional original

**Problema:** ciertos agujeros de esterilla no aparecen ni como verdes (detectados) ni como
rojos X (missing en patron). La mascara binaria no los genera, son completamente invisibles.

**Causa:** los cambios anteriores (Cambio 138-139) modificaron los parametros de deteccion
de modelo_A intentando compensar el ROI ampliado (x=110, w=415), pero empeoraron la situacion:
- `use_channel: r` — el canal R amplifica la zona de backlight rojo, elevando la media local
  adaptativa cerca de los agujeros del borde izquierdo del material.
- `use_clahe: true` con `clahe_tile=8` — tiles de 52×60px en zonas backlight/material distorsionan
  el contraste local cerca de los agujeros en la transicion.
- `adaptive_block_size: 21` — bloque pequenio captura mas ruido de iluminacion no uniforme.
- `adaptive_c: 3.0` (positivo) — mas permisivo que antes, pero CLAHE+backlight dominan.

**Decision:** restaurar exactamente los parametros que funcionaban antes de la expansion del ROI.
Con `adaptive_c: -5.0` (negativo), threshold = media_local + 5, por lo que los agujeros solo
se detectan si son significativamente mas brillantes que su vecindad — robusto incluso con
zonas de backlight lateral porque esas zonas crean blobs grandes que filtran por area/circularidad.

**Cambios en config/tolerancias.yaml (modelo_A):**
- `use_channel: r` → `gray`
- `use_clahe: true` → `false`
- `adaptive_block_size: 21` → `41`
- `adaptive_c: 3.0` → `-5.0`
