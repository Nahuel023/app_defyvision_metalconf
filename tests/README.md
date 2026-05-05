# Tests del sistema — DEFYVISION Metalconf

Todos los tests son scripts manuales (sin pytest).
Ejecutar desde la raíz del proyecto con el .venv activado.

---

## FASE 1 — PLC + IO Map

```bash
python test_io_map.py
```
**Verifica:**
- Conexión al PLC (192.168.10.175:502)
- Listado de todas las señales disponibles
- Lectura de todas las entradas (X) y salidas (Y) por nombre semántico
- **Esperado:** Sin errores de conexión; valores 0 en entradas inactivas

---

## FASE 2 — Cámara USB

```bash
python tests/test_camera.py
```
**Verifica:**
- Apertura de cámara índice 0 y 1 (Logitech C920)
- Captura continua en hilo de fondo
- `get_frame()` devuelve ndarray BGR
- **Esperado:** Ventana OpenCV mostrando imagen en vivo; q para salir

---

## FASE 3 — Inspector (visión sobre frame live)

```bash
python tests/test_inspector.py --model modelo_A --camera 0
```
**Verifica:**
- `inspect_frame()` acepta ndarray BGR
- Pipeline completo (alineación, detección, comparación) sobre frame de cámara
- **Esperado:** Resultado OK/NOK impreso + ventana overlay

---

## FASE 4 — Scanner Controller (FSM completa)

```bash
python tests/test_scanner_controller.py
```
**Verifica:**
- FSM: IDLE → RUNNING (modo AUTO) → FAULT (forzado) → RESET → RUNNING → IDLE
- Detección de flanco HIGH→LOW en sensor PLC
- Callbacks `on_state_changed` y `on_result` llamados correctamente
- `consecutive_nok_frames` respetado
- **Esperado:** Log de transiciones de estado en consola

---

## FASE 5 — Sistema completo (sin UI)

```bash
python tests/test_system.py
```
**Verifica:**
- `InspectionSystem` instancia correctamente ambos scanners
- Conexión PLC compartida
- Ambas cámaras arrancan
- `shutdown()` limpia todos los recursos
- **Esperado:** Sin errores; log limpio de inicio y shutdown

---

## Sistema completo con UI

```bash
.\.venv\Scripts\python.exe -m src.main run
```
**Verifica end-to-end:**
- UI abre con feed de ambas cámaras
- Botón INICIAR → scanner pasa a RUNNING
- En modo AUTO: inspección se dispara por sensor PLC
- Resultado visible en overlay (2.5 s) y badge de estado
- En modo MANUAL: sin inspección, solo control del pistón
- FAULT al alcanzar racha NOK → botón RESET habilita reanudación
- Cierre de ventana → shutdown limpio del sistema
