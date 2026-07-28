"""
FSM de un scanner individual.

Estados:
  IDLE    → azul encendida, esperando START del operador
  RUNNING → verde/amarillo; electroválvula activa
             MANUAL: solo electroválvula (sin backlight ni inspección)
             AUTO:   inspección continua + electroválvula + backlight
  FAULT   → roja+amarilla parpadeando; electroválvula detenida; espera DETENER
  STOPPED → todas las luces apagadas; espera RESET para volver a IDLE
  ERROR   → fallo de hardware (cámara o PLC)

Transiciones:
  IDLE    --[INICIAR MANUAL]--> RUNNING (solo solenoide)
  IDLE    --[INICIAR AUTO]---> RUNNING (completo)
  RUNNING --[DETENER MANUAL]--> IDLE
  RUNNING --[DETENER AUTO]---> STOPPED
  RUNNING --[streak NOK >= umbral]--> FAULT
  FAULT   --[DETENER]--> STOPPED
  STOPPED --[RESET]--> IDLE

Threads:
  _poller_thread    — lee PLC cada poll_interval_ms; gestiona blink en FAULT
  _inspector_thread — modo AUTO: inspecciona por diferencia de posición
"""

import dataclasses
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import cv2
import numpy as np

from pathlib import Path

from src.inspection import InspectionResult, save_result_images
from src.plc.io_map import IOMap
from src.utils.config import load_tolerances
from src.utils.paths import app_root
from src.utils.state import OperationMode, ScannerState
from src.vision.camera import Camera
from src.vision.inspector import Inspector, InspectionSession

logger = logging.getLogger(__name__)


class ScannerController:
    def __init__(self, scanner_id: str, io: IOMap, camera: Camera) -> None:
        self._id        = scanner_id
        self._io        = io
        self._camera    = camera
        self._inspector = Inspector()

        cfg      = io.scanner_config(scanner_id)
        insp_cfg = cfg.get("inspection", {})
        model    = cfg.get("model", "")
        tols     = load_tolerances(model, scanner_id=scanner_id)

        self._consecutive_nok = max(
            int(tols.get("stop_min_frames", 0)),   # piso DURO por scanner (ej. 5 en scanner_1)
            int(insp_cfg.get("consecutive_nok_frames",
                             tols["consecutive_nok_frames"])),
        )
        self._low_quality_max_streak = int(tols.get("low_quality_max_streak", 10))
        # Si el contador de NOK de la sesion queda "pegado" en un numero chico
        # (p.ej. 1 o 2 piezas NOK aisladas al principio) y la linea sigue
        # produciendo buenas, tras esta cantidad de frames OK seguidos se
        # resetea a 0 — evita que el operador vea un NOK viejo colgado en
        # pantalla por horas cuando la produccion esta corriendo limpia.
        self._nok_count_reset_frames = int(tols.get("nok_count_reset_frames", 200))
        self._machine_stop_enabled = bool(tols.get("machine_stop_enabled", True))
        self._save_nok      = bool(insp_cfg.get("save_nok_frames", True))
        self._save_ok       = bool(insp_cfg.get("save_ok_frames",  False))
        self._poll_interval = io.plc_config.get("poll_interval_ms", 50) / 1000.0
        self._cont_pos_thr  = float(
            tols.get("continuous_position_threshold", 8.0)
        )
        _max_hz = float(tols.get("max_inspection_hz", 0))
        self._min_insp_interval = (1.0 / _max_hz) if _max_hz > 0 else 0.0
        self._force_auto    = bool(cfg.get("force_auto_mode", False))

        self._state      = ScannerState.IDLE
        self._mode       = OperationMode.AUTO if self._force_auto else OperationMode.MANUAL
        self._mode_switch_raw: Optional[bool] = None  # ultima lectura cruda de la maneta (None=sin leer aun)
        self._nok_streak = 0
        self._lq_streak  = 0
        self._frames_since_last_nok = 0
        self._last_result: Optional[InspectionResult] = None
        self._streak_start_mono: Optional[float] = None  # time.monotonic() del 1er NOK de la racha activa

        # Métricas de sesión (resetean en start())
        self._total_inspections: int       = 0
        self._ok_count:          int       = 0
        self._nok_count:         int       = 0
        self._session_start: Optional[datetime] = None
        self._max_nok_streak:    int       = 0
        self._fault_count:       int       = 0
        self._machine_stop_count: int      = 0
        # True si el STOPPED actual vino de una falla real (machine_stop o FAULT
        # por racha NOK), no de un DETENER voluntario sin ningun problema. Usado
        # por la UI para decidir si el boton dice "RESET" o "RESET FALLA".
        self._stopped_by_fault:  bool      = False
        self._startup_grace_remaining: int = 0
        # Gracia adicional por tiempo desde que arranca el loop (independiente de
        # cuantos frames se procesen) — ver _continuous_loop_impl/_handle_result.
        self._startup_grace_seconds: float  = 0.0
        self._run_loop_start_mono: float    = 0.0
        self._total_missing:     int       = 0
        self._nok_with_missing:  int       = 0
        self._last_position_diff: float    = 0.0
        self._total_detection_ratio: float = 0.0
        self._align_fail_count:  int       = 0
        self._low_quality_count: int       = 0
        self._camera_missing_since: Optional[float] = None
        self._camera_missing_warned: bool = False
        self._camera_missing_timeout_s: float = float(
            tols.get("camera_missing_error_timeout_s", 3.0)
        )
        self._camera_missing_total_s: float = 0.0
        self._camera_missing_events: int = 0

        # Buffer circular de frames OK en disco (best-effort, prioridad baja)
        self._ok_buf_enabled  = bool(tols.get("ok_buffer_enabled", True))
        self._ok_buf_max      = max(10, int(tols.get("ok_buffer_count", 200)))
        self._ok_buf_every    = max(1,  int(tols.get("ok_buffer_every", 3)))
        self._ok_buf_quality  = int(tols.get("ok_buffer_jpeg_quality", 75))
        self._ok_buf_dir      = app_root() / "data/output/ok_buffer" / scanner_id
        self._ok_seen: int    = 0   # frames OK vistos (para throttle)
        self._ok_write: int   = 0   # posición de escritura en el pool

        # Buffer cronológico — todos los frames en orden de inspección
        self._tl_enabled  = bool(tols.get("timeline_buffer_enabled", True))
        self._tl_max      = max(10, int(tols.get("timeline_buffer_count", 500)))
        self._tl_quality  = int(tols.get("ok_buffer_jpeg_quality", 75))
        self._tl_dir      = app_root() / "data/output/timeline" / scanner_id
        self._tl_write: int = 0   # posición de escritura circular

        self._disk_queue: "queue.Queue[tuple[str, Callable[[], None]] | None]" = queue.Queue(
            maxsize=max(32, int(tols.get("disk_writer_queue_max", 256)))
        )
        self._disk_stop_event = threading.Event()
        self._disk_thread = threading.Thread(
            target=self._disk_worker_loop,
            daemon=True,
            name=f"{self._id}-disk-writer",
        )
        self._disk_thread.start()
        self._disk_drop_counts: dict[str, int] = {}

        self._lock          = threading.Lock()
        # Cada InspectionSession conserva su propio snapshot de ROI/patrón/
        # tolerancias. Esta revisión obliga al loop RUN a reemplazar ese snapshot
        # en el siguiente frame cuando la UI guarda una calibración nueva.
        self._cache_revision = 0
        self._force_inspect = threading.Event()
        self._stop_event    = threading.Event()

        self._poller_thread:   Optional[threading.Thread] = None
        self._inspector_thread: Optional[threading.Thread] = None

        # Buffer circular de evidencia (opcional — gated por events_enabled)
        self._recorder = None
        if tols.get("events_enabled", False):
            try:
                from src.pipeline.event_recorder import EventRecorder
                self._recorder = EventRecorder(
                    scanner_id=scanner_id,
                    events_dir=app_root() / "data/events",
                    max_disk_gb=float(tols.get("events_max_disk_gb", 10.0)),
                    pre_seconds=float(tols.get("pre_event_seconds", 60.0)),
                    post_seconds=float(tols.get("post_event_seconds", 30.0)),
                    fps=float(tols.get("pre_event_fps", 5.0)),
                    jpeg_quality=int(tols.get("pre_event_jpeg_quality", 80)),
                    max_ram_mb=float(tols.get("pre_event_max_ram_mb", 256.0)),
                )
                logger.info(
                    f"[{scanner_id}] EventRecorder activo — "
                    f"{tols.get('pre_event_seconds', 60)}s pre / "
                    f"{tols.get('post_event_seconds', 30)}s post"
                )
            except Exception as exc:
                logger.error(f"[{scanner_id}] no se pudo inicializar EventRecorder: {exc}")

        # Callbacks opcionales para la UI (llamados fuera de locks)
        self.on_state_changed: Optional[Callable[[ScannerState, OperationMode], None]] = None
        self.on_result: Optional[Callable[[InspectionResult, int], None]] = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """IDLE → RUNNING.

        MANUAL: activa solo la electroválvula. Sin backlight ni inspección.
        AUTO:   activa electroválvula + backlight + hilo de inspección continua.
        """
        if not self._license_allows_operation():
            logger.error(f"[{self._id}] inicio bloqueado por licencia invalida")
            return False
        if not self._workers_ready_for_start():
            logger.error(
                "[%s] inicio bloqueado: todavia hay hilos de la sesion anterior activos",
                self._id,
            )
            return False
        self._update_mode_from_plc()
        with self._lock:
            if self._state != ScannerState.IDLE:
                logger.warning(f"[{self._id}] start() ignorado, estado={self._state.value}")
                return False
            mode = self._mode
            # Reclamar el slot de inicio dentro del MISMO lock que chequea IDLE:
            # un start() concurrente (doble click, dos rutas de arranque) ve el
            # estado ya cambiado y aborta arriba, en vez de pasar el guard
            # tambien y lanzar un segundo hilo inspector sobre esta instancia.
            self._state = ScannerState.RUNNING

        if mode == OperationMode.AUTO:
            if not self._camera.is_running:
                if not self._camera.start():
                    self._transition(ScannerState.ERROR)
                    return False

        with self._lock:
            self._nok_streak              = 0
            self._lq_streak               = 0
            self._frames_since_last_nok   = 0
            self._streak_start_mono       = None
            self._total_inspections       = 0
            self._ok_count                = 0
            self._nok_count               = 0
            self._session_start           = datetime.now()
            self._max_nok_streak          = 0
            self._fault_count             = 0
            self._machine_stop_count      = 0
            self._stopped_by_fault        = False
            self._total_missing           = 0
            self._nok_with_missing        = 0
            self._last_position_diff      = 0.0
            self._total_detection_ratio   = 0.0
            self._align_fail_count        = 0
            self._low_quality_count       = 0
            self._camera_missing_since    = None
            self._camera_missing_warned   = False
            self._camera_missing_total_s  = 0.0
            self._camera_missing_events   = 0
            self._stop_event.clear()
            self._force_inspect.clear()

        if mode == OperationMode.AUTO:
            self._io.write(f"{self._id}.backlight", True)
            self._start_all_threads()
        else:
            self._start_poller_thread()

        # El estado ya quedo en RUNNING desde el lock de arriba; solo falta
        # avisar a los listeners (UI) ahora que camara/hilos estan en marcha.
        self._fire_state_changed()
        self._io.write(f"{self._id}.solenoid", True)  # bloqueado por IOMap si safe_mode=ON
        self._set_lights(green=True)
        logger.info(f"[{self._id}] iniciado ({mode.value})")
        return True

    def stop(self) -> None:
        """Detiene el scanner.

        MANUAL RUNNING → IDLE  (azul; listo para INICIAR de nuevo).
        AUTO RUNNING   → STOPPED (luces apagadas; espera RESET → IDLE).
        FAULT          → STOPPED (ídem).
        """
        # Decidir y aplicar el nuevo estado en UNA sola adquisicion del lock:
        # si se lee el estado, se libera el lock y DESPUES se llama a
        # _transition(new_state), una transicion concurrente disparada por el
        # hilo inspector (machine_stop, o un escalado a ERROR por perdida de
        # camara) podria intercalarse y quedar pisada por esta escritura
        # "vieja" basada en un estado que ya no es el actual.
        with self._lock:
            if self._state in (ScannerState.IDLE, ScannerState.STOPPED):
                return
            state = self._state
            mode  = self._mode

            # FAULT/ERROR siempre → STOPPED; AUTO RUNNING → STOPPED; MANUAL RUNNING → IDLE
            if state in (ScannerState.FAULT, ScannerState.ERROR) or mode == OperationMode.AUTO:
                new_state = ScannerState.STOPPED
            else:
                new_state = ScannerState.IDLE

            if new_state == ScannerState.STOPPED:
                # Si ya venia de FAULT (racha NOK) o ERROR (crash real), es una
                # parada por falla real. Si venia de RUNNING sin FAULT/ERROR, fue
                # un DETENER voluntario del operador sin ningun problema detectado.
                self._stopped_by_fault = (state in (ScannerState.FAULT, ScannerState.ERROR))

            self._state = new_state

        self._fire_state_changed()
        self._cut_solenoid_critical("detener scanner")
        # backlight permanece encendido siempre

        if new_state == ScannerState.IDLE:
            self._set_lights(blue=True)
        else:
            self._set_lights()   # todas apagadas en STOPPED

        self._stop_event.set()
        self._join_threads()
        logger.info(f"[{self._id}] detenido → {new_state.value}")

    def shutdown(self) -> None:
        self.stop()
        # stop() es no-op si ya estaba IDLE/STOPPED, pero puede quedar un hilo
        # terminando despues de un timeout anterior. Shutdown siempre insiste.
        self._stop_event.set()
        self._join_threads()
        if self._recorder is not None:
            try:
                self._recorder.close()
            except Exception as exc:
                logger.error(f"[{self._id}] error cerrando EventRecorder: {exc}")
        self._stop_disk_worker()

    def reset(self) -> bool:
        """STOPPED → IDLE + azul. Requiere INICIAR para reanudar.

        Olvida por completo los NOK y frames malos de la sesión que terminó:
        racha, último resultado/overlay de la falla y todas las estadísticas
        acumuladas (conteos OK/NOK, faults, machine_stops, missing, calidad).
        No espera a INICIAR — el reset es el momento en que el sistema
        "empieza de cero"."""
        with self._lock:
            if self._state != ScannerState.STOPPED:
                return False
            self._nok_streak               = 0
            self._lq_streak                = 0
            self._frames_since_last_nok    = 0
            self._streak_start_mono        = None
            self._last_result               = None
            self._total_inspections        = 0
            self._ok_count                  = 0
            self._nok_count                 = 0
            self._session_start             = datetime.now()
            self._max_nok_streak            = 0
            self._fault_count               = 0
            self._machine_stop_count        = 0
            self._total_missing             = 0
            self._nok_with_missing          = 0
            self._last_position_diff        = 0.0
            self._total_detection_ratio     = 0.0
            self._align_fail_count          = 0
            self._low_quality_count         = 0
            self._camera_missing_since      = None
            self._camera_missing_warned     = False
            self._camera_missing_total_s    = 0.0
            self._camera_missing_events     = 0

        self._transition(ScannerState.IDLE)
        self._set_lights(blue=True)
        logger.info(f"[{self._id}] reset → IDLE")
        return True

    def force_inspect(self) -> bool:
        """Fuerza una inspección inmediata (solo en AUTO RUNNING)."""
        with self._lock:
            if self._state != ScannerState.RUNNING or self._mode != OperationMode.AUTO:
                logger.warning(
                    f"[{self._id}] force_inspect ignorado, estado={self._state.value}"
                )
                return False
        self._force_inspect.set()
        logger.info(f"[{self._id}] inspección forzada")
        return True

    def start_simulate(self) -> bool:
        """IDLE → RUNNING en modo AUTO sin requerir cámara. Solo para pruebas en servicio."""
        if not self._license_allows_operation():
            logger.error(f"[{self._id}] simulacion bloqueada por licencia invalida")
            return False
        if not self._workers_ready_for_start():
            logger.error("[%s] simulacion bloqueada: hilos anteriores activos", self._id)
            return False
        with self._lock:
            if self._state != ScannerState.IDLE:
                return False
            self._mode                    = OperationMode.AUTO
            self._nok_streak              = 0
            self._lq_streak               = 0
            self._frames_since_last_nok   = 0
            self._streak_start_mono       = None
            self._total_inspections       = 0
            self._ok_count                = 0
            self._nok_count               = 0
            self._session_start           = datetime.now()
            self._max_nok_streak          = 0
            self._fault_count             = 0
            self._machine_stop_count      = 0
            self._stopped_by_fault        = False
            self._total_missing           = 0
            self._nok_with_missing        = 0
            self._last_position_diff      = 0.0
            self._total_detection_ratio   = 0.0
            self._align_fail_count        = 0
            self._low_quality_count       = 0
            self._camera_missing_since    = None
            self._camera_missing_warned   = False
            self._camera_missing_total_s  = 0.0
            self._camera_missing_events   = 0
            self._stop_event.clear()
            self._force_inspect.clear()

        self._io.write(f"{self._id}.backlight", True)
        if not self._io.write(f"{self._id}.solenoid", True):
            logger.warning(
                f"[{self._id}] simulacion bloqueada: no se pudo energizar solenoide "
                "(modo seguro activo o error PLC)"
            )
            self._set_lights(blue=True)
            return False

        self._start_poller_thread()
        self._transition(ScannerState.RUNNING)
        self._set_lights(green=True)
        logger.info(f"[{self._id}] iniciado (simulación AUTO)")
        return True

    def force_fault(self) -> bool:
        """Fuerza directamente el estado FAULT sin pasar por la racha NOK. Solo pruebas."""
        with self._lock:
            if self._state != ScannerState.RUNNING:
                return False
            self._state = ScannerState.FAULT
            self._fault_count += 1

        logger.warning(f"[{self._id}] FAULT forzado (simulación)")
        self._cut_solenoid_critical("fault forzado")
        # backlight permanece encendido siempre
        self._set_lights(red=True)   # poll_loop toma el blink
        self._fire_state_changed()
        return True

    def inject_machine_stop(self, reason: str = "SIMULACION") -> None:
        """Inyecta un machine_stop sintético sin cambiar el estado FSM.

        Útil para probar el overlay de parada y la grabación de evidencia
        sin necesidad de un defecto real. Funciona en cualquier estado.
        """
        import numpy as _np
        import cv2 as _cv2
        from pathlib import Path as _Path
        from src.inspection import InspectionResult
        from src.pipeline.compare import CompareReport

        with self._lock:
            model = self._io.scanner_config(self._id).get("model", "")

        # Overlay sintético con banner de simulación
        h, w = 480, 640
        overlay = _np.zeros((h, w, 3), dtype=_np.uint8)
        overlay[:] = (12, 8, 30)
        _cv2.rectangle(overlay, (8, 8), (w - 8, h - 8), (180, 30, 30), 2)
        for y, (txt, fs, clr) in enumerate([
            ("! SIMULACION DE PARADA !",  0.85, (50, 50, 220)),
            (reason,                      0.65, (160, 160, 160)),
            ("Esta imagen es sintetica",  0.55, (80,  80,  80)),
        ]):
            _cv2.putText(overlay, txt, (40, 160 + y * 70),
                         _cv2.FONT_HERSHEY_SIMPLEX, fs, clr, 2)

        report = CompareReport(
            expected=100, detected=85, missing=15, status="NOK",
            missing_points=[(float(i * 15), float(i * 10)) for i in range(15)],
            matched_detected_idx=list(range(85)),
        )
        mask = _np.zeros((h, w), dtype=_np.uint8)
        result = InspectionResult(
            model=model,
            image_path=_Path("_sim_stop"),
            status="NOK",
            report=report,
            holes=[],
            mask=mask,
            overlay=overlay,
            angle_deg=0.0,
            used_lines=0,
            shift_xy=None,
            detection_ratio=0.85,
            alignment_ok=True,
            machine_stop=True,
        )
        self._handle_result(result, model)
        logger.info(f"[{self._id}] machine_stop simulado — {reason}")

    def inject_result(self, is_ok: bool, count: int = 1) -> None:
        """Inyecta resultados sintéticos para probar la FSM. Solo actúa en RUNNING."""
        from pathlib import Path as _Path
        import numpy as _np
        from src.inspection import InspectionResult
        from src.pipeline.compare import CompareReport

        with self._lock:
            if self._state != ScannerState.RUNNING:
                return
            model = self._io.scanner_config(self._id).get("model", "")

        status = "OK" if is_ok else "NOK"
        _missing = 0 if is_ok else 30
        report = CompareReport(
            expected=100,
            detected=100 - _missing,
            missing=_missing,
            status=status,
            missing_points=[(0.0, 0.0)] * _missing,
            matched_detected_idx=list(range(100 - _missing)),
        )
        blank = _np.zeros((10, 10, 3), dtype=_np.uint8)
        mask  = _np.zeros((10, 10),    dtype=_np.uint8)
        for _ in range(count):
            with self._lock:
                if self._state != ScannerState.RUNNING:
                    break
            result = InspectionResult(
                model=model,
                image_path=_Path("_sim"),
                status=status,
                report=report,
                holes=[],
                mask=mask,
                overlay=blank,
                angle_deg=0.0,
                used_lines=0,
                shift_xy=None,
                detection_ratio=1.0,
                alignment_ok=True,
            )
            self._handle_result(result, model)

    def initialize_lights(self) -> None:
        """Enciende la luz correspondiente al estado actual.
        Llamar una vez tras conectar el PLC."""
        with self._lock:
            state = self._state
        # Backlight siempre ON al iniciar para que la cámara sea visible
        self._io.write(f"{self._id}.backlight", True)
        if state == ScannerState.IDLE:
            self._set_lights(blue=True)
        elif state == ScannerState.RUNNING:
            self._set_lights(green=True)
        elif state in (ScannerState.FAULT, ScannerState.STOPPED, ScannerState.ERROR):
            self._set_lights(red=True)   # rojo = intervención requerida (estándar industrial)

    def sync_solenoid(self, safe_mode_off: bool) -> None:
        """Sincroniza el solenoide con el estado actual del scanner.

        Llamar desde system.set_safe_mode() para evitar la race condition entre
        la lectura de estado y la escritura al PLC.
        safe_mode_off=True → activar solenoide solo si el scanner está en RUNNING.
        safe_mode_off=False → cortar solenoide sin condición.
        """
        with self._lock:
            is_running = self._state == ScannerState.RUNNING
        # La escritura es fuera del lock para no bloquear el inspector thread
        if safe_mode_off and is_running:
            self._io.write(f"{self._id}.solenoid", True)
        else:
            self._io.write(f"{self._id}.solenoid", False)

    def reload_cache(self) -> None:
        """Invalida recursos y pide recargar la sesión viva en el próximo frame."""
        model = self._io.scanner_config(self._id).get("model", "")
        self._inspector.invalidate(model=model or None, scanner_id=self._id)
        with self._lock:
            self._cache_revision += 1
            revision = self._cache_revision
            # La geometría de comparación cambió: no arrastrar una racha NOK
            # calculada con el ROI/patrón anterior hacia la sesión nueva.
            self._nok_streak = 0
            self._lq_streak = 0
            self._streak_start_mono = None
        logger.info(
            "[%s] cache invalidado (revision=%d) — ROI/patrón/tolerancias "
            "se aplicarán en el próximo frame",
            self._id, revision,
        )

    def set_model(self, model: str) -> None:
        cfg      = self._io.scanner_config(self._id)
        cfg["model"] = model
        insp_cfg = cfg.get("inspection", {})
        tols     = load_tolerances(model, scanner_id=self._id)
        self._consecutive_nok = max(
            int(tols.get("stop_min_frames", 0)),   # piso DURO por scanner (ej. 5 en scanner_1)
            int(insp_cfg.get("consecutive_nok_frames", tols["consecutive_nok_frames"])),
        )
        self._machine_stop_enabled = bool(tols.get("machine_stop_enabled", True))
        self._inspector.invalidate(model=model, scanner_id=self._id)
        with self._lock:
            self._cache_revision += 1
        logger.info(f"[{self._id}] modelo cambiado a '{model}' "
                    f"(consecutive_nok={self._consecutive_nok}, "
                    f"machine_stop_enabled={self._machine_stop_enabled})")

    # ------------------------------------------------------------------
    # Propiedades de estado (thread-safe)
    # ------------------------------------------------------------------

    @property
    def state(self) -> ScannerState:
        with self._lock:
            return self._state

    @property
    def mode(self) -> OperationMode:
        with self._lock:
            return self._mode

    @property
    def nok_streak(self) -> int:
        with self._lock:
            return self._nok_streak

    @property
    def last_result(self) -> Optional[InspectionResult]:
        with self._lock:
            return self._last_result

    def get_status(self) -> dict:
        with self._lock:
            avg_missing = (
                self._total_missing / self._nok_with_missing
                if self._nok_with_missing > 0 else 0.0
            )
            avg_detection_ratio = (
                self._total_detection_ratio / self._total_inspections
                if self._total_inspections > 0 else 0.0
            )
            low_quality_pct = (
                self._low_quality_count / self._total_inspections * 100.0
                if self._total_inspections > 0 else 0.0
            )
            inspection_uptime_pct = 0.0
            camera_missing_sec = 0.0
            if self._session_start is not None:
                session_s = max(0.0, (datetime.now() - self._session_start).total_seconds())
                camera_missing_sec = self._camera_missing_total_s
                if self._camera_missing_since is not None:
                    camera_missing_sec += max(0.0, time.monotonic() - self._camera_missing_since)
                inspection_uptime_pct = (
                    max(0.0, session_s - camera_missing_sec) / session_s * 100.0
                    if session_s > 0 else 0.0
                )
            return {
                "state":                self._state,
                "mode":                 self._mode,
                "mode_switch_raw":      self._mode_switch_raw,
                "nok_streak":           self._nok_streak,
                "last_result":          self._last_result,
                "total_inspections":    self._total_inspections,
                "ok_count":             self._ok_count,
                "nok_count":            self._nok_count,
                "session_start":        self._session_start,
                "max_nok_streak":       self._max_nok_streak,
                "fault_count":          self._fault_count,
                "machine_stop_count":   self._machine_stop_count,
                "stopped_by_fault":     self._stopped_by_fault,
                "avg_missing_holes":    avg_missing,
                "last_position_diff":   self._last_position_diff,
                "avg_detection_ratio":  avg_detection_ratio,
                "align_fail_count":     self._align_fail_count,
                "low_quality_count":    self._low_quality_count,
                "low_quality_pct":      low_quality_pct,
                "camera_missing":       self._camera_missing_since is not None,
                "camera_missing_sec":   camera_missing_sec,
                "camera_missing_timeout_s": self._camera_missing_timeout_s,
                "camera_missing_events": self._camera_missing_events,
                "inspection_uptime_pct": inspection_uptime_pct,
            }

    # ------------------------------------------------------------------
    # Thread: poller PLC
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        _tick = 0
        _prev_blink: Optional[bool] = None
        _last_license_check = 0.0
        while not self._stop_event.is_set():
            self._update_mode_from_plc()

            with self._lock:
                state  = self._state
                streak = self._nok_streak

            if state == ScannerState.RUNNING:
                now_m = time.monotonic()
                if now_m - _last_license_check >= 10.0:
                    _last_license_check = now_m
                    if not self._license_allows_operation():
                        self._handle_license_failure()
                        return

            _tick += 1
            if state == ScannerState.FAULT:
                # Rojo fijo + amarillo parpadea ~1 Hz
                blink_on = (_tick % 20) < 10   # 50 ms/tick → 10 ticks = 500 ms
                if blink_on != _prev_blink:
                    self._set_lights(red=True, yellow=blink_on)
                    _prev_blink = blink_on
            elif state == ScannerState.RUNNING and streak >= max(1, self._consecutive_nok // 3):
                # Verde fijo + amarillo parpadea ~1 Hz
                blink_on = (_tick % 20) < 10
                if blink_on != _prev_blink:
                    self._set_lights(green=True, yellow=blink_on)
                    _prev_blink = blink_on
            else:
                _prev_blink = None

            self._stop_event.wait(timeout=self._poll_interval)

    # ------------------------------------------------------------------
    # Thread: inspector (solo AUTO)
    # ------------------------------------------------------------------

    def _run_startup_selftest(self, model: str) -> bool:
        """Captura un frame de la cámara y verifica que la detección funciona.

        Retorna True si el test pasa (o si está deshabilitado), False si falla.
        IMPORTANTE: llamar solo después de escribir el backlight al PLC, y esperar
        al menos 2 ciclos de cámara para que el primer frame sea con luz.
        """
        tols = load_tolerances(model, scanner_id=self._id)
        if not tols.get("startup_selftest_enabled", False):
            return True
        timeout_s   = float(tols.get("selftest_timeout_s", 10.0))
        min_ratio   = float(tols.get("min_detection_ratio", 0.30))
        # Esperar que lleguen frames post-backlight (al menos 150ms = ~4 frames a 30fps)
        time.sleep(0.15)
        deadline    = time.monotonic() + timeout_s
        frame = None
        while time.monotonic() < deadline:
            frame = self._camera.get_frame()
            if frame is not None:
                break
            time.sleep(0.1)
        if frame is None:
            logger.error(f"[{self._id}] selftest: no se obtuvo frame en {timeout_s}s")
            return False
        result = self._inspector.inspect(model, frame, frame_id="selftest",
                                         scanner_id=self._id)
        if result is None:
            logger.error(f"[{self._id}] selftest: inspector no retornó resultado")
            return False
        ratio = getattr(result, "detection_ratio", 1.0)
        if ratio < min_ratio:
            logger.error(
                f"[{self._id}] selftest FALLO: detection_ratio={ratio:.0%} < {min_ratio:.0%}"
            )
            return False
        logger.info(f"[{self._id}] selftest OK: detection_ratio={ratio:.0%}")
        return True

    def _run_roi_precalibration(self, model: str, session: InspectionSession) -> None:
        """Mide el shift_x antes de iniciar el loop y corrige el ROI si está desplazado.

        Escribe roi.json y actualiza la sesión en memoria para que el análisis
        comience desde una posición ya calibrada.
        """
        from src.patterns.roi import load_roi, save_roi, ROI

        tols = load_tolerances(model, scanner_id=self._id)
        if not tols.get("roi_precal_enabled", True):
            return
        if not tols.get("roi_recenter_enabled", False):
            return

        n_frames   = int(tols.get("roi_precal_frames", 8))
        max_iters  = int(tols.get("roi_precal_max_iters", 4))
        threshold  = float(tols.get("roi_precal_threshold_px", tols.get("roi_recenter_trigger_delta_px", 6.0)))
        overshoot  = int(tols.get("roi_precal_overshoot_px", 3))
        resize_mode = str(tols.get("roi_recenter_mode", "resize")) == "resize"
        max_growth = float(tols.get("roi_recenter_max_width_growth_px", 60.0))

        logger.info("[%s] ROI pre-cal: iniciando (max %d iters, umbral %.1fpx)", self._id, max_iters, threshold)

        for iteration in range(max_iters):
            if self._stop_event.is_set():
                break

            shifts = []
            for _ in range(n_frames):
                if self._stop_event.is_set():
                    break
                frame = self._camera.get_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue
                result = session.inspect_frame(frame, force=True)
                if result is None:
                    continue
                ri = getattr(result, "roi_info", None)
                if ri is not None and ri.shift_x is not None:
                    shifts.append(float(ri.shift_x))

            if not shifts:
                logger.warning("[%s] ROI pre-cal: no se obtuvieron frames válidos", self._id)
                break

            avg_shift = sum(shifts) / len(shifts)
            logger.info(
                "[%s] ROI pre-cal iter %d/%d: shift_x medio=%.1fpx (%d frames)",
                self._id, iteration + 1, max_iters, avg_shift, len(shifts),
            )

            if abs(avg_shift) < threshold:
                logger.info("[%s] ROI pre-cal: ROI bien calibrada (shift=%.1fpx < %.1fpx)", self._id, avg_shift, threshold)
                break

            # Leer ROI actual del preloaded de la sesión
            current_roi: ROI | None = session._preloaded.get("roi")
            if current_roi is None:
                current_roi = load_roi(model, self._id)
            if current_roi is None:
                logger.warning("[%s] ROI pre-cal: no hay ROI definida, saltando", self._id)
                break

            # Magnitud de corrección con overshoot para dar margen
            direction = 1 if avg_shift > 0 else -1
            magnitude = int(round(abs(avg_shift))) + overshoot

            if resize_mode:
                # Expande el borde en la dirección del drift
                growth = min(magnitude, max(0.0, max_growth - (current_roi.w - (session._preloaded.get("saved_roi") or current_roi).w)))
                if growth < 1:
                    logger.info("[%s] ROI pre-cal: limite de crecimiento alcanzado, deteniendo", self._id)
                    break
                if direction > 0:
                    new_w = current_roi.w + int(growth)
                    new_x = current_roi.x
                else:
                    expand = min(int(growth), current_roi.x)
                    new_x = current_roi.x - expand
                    new_w = current_roi.w + expand
                new_roi = ROI(x=new_x, y=current_roi.y, w=new_w, h=current_roi.h)
                logger.info("[%s] ROI pre-cal: resize %+dpx borde %s -> x=%d w=%d",
                            self._id, int(growth) * direction,
                            "derecho" if direction > 0 else "izquierdo",
                            new_x, new_w)
            else:
                # Modo move: desplaza toda la ventana
                correction = direction * magnitude
                new_x = max(0, current_roi.x + correction)
                new_roi = ROI(x=new_x, y=current_roi.y, w=current_roi.w, h=current_roi.h)
                logger.info("[%s] ROI pre-cal: move %+dpx -> x=%d", self._id, correction, new_x)

            if new_roi == current_roi:
                logger.info("[%s] ROI pre-cal: sin cambio efectivo, deteniendo", self._id)
                break

            # Persistir en disco
            try:
                save_roi(new_roi, model, self._id)
            except Exception as exc:
                logger.error("[%s] ROI pre-cal: error escribiendo roi.json: %s", self._id, exc)
                break

            # Actualizar sesión en memoria Y el cache del Inspector — sin esto el
            # cache en memoria seguia devolviendo el ROI viejo en cada reinicio
            # del scanner (solo se corregia tras reiniciar todo el programa).
            session._preloaded["roi"] = new_roi
            session._preloaded["saved_roi"] = new_roi
            session._preloaded["roi_runtime_state"] = {}
            self._inspector.set_roi(model, self._id, new_roi)

            logger.info("[%s] ROI pre-cal: persistido x=%d w=%d (shift fue %.1fpx)", self._id, new_roi.x, new_roi.w, avg_shift)

        logger.info("[%s] ROI pre-cal: finalizada", self._id)

    def _continuous_loop(self) -> None:
        """Modo continuo AUTO con la misma sesion/criterios que run-folder."""
        try:
            self._continuous_loop_impl()
        except Exception as exc:
            logger.critical(
                "[%s] inspector thread crashed — transicionando a ERROR: %s",
                self._id, exc, exc_info=True,
            )
            try:
                self._cut_solenoid_critical("crash del inspector")
                self._set_lights(red=True)
            except Exception:
                pass
            with self._lock:
                self._state = ScannerState.ERROR
            try:
                self._fire_state_changed()
            except Exception:
                pass

    def _continuous_loop_impl(self) -> None:
        frame_counter = 0

        with self._lock:
            model_init = self._io.scanner_config(self._id)["model"]
        # Invalidar cache del Inspector al arrancar: roi_recenter escribe roi.json
        # a disco pero NO actualiza self._inspector._roi en memoria. Sin invalidar,
        # el INICIAR siguiente crea la sesion con el ROI viejo del cache. Invalidar
        # fuerza releer roi.json (y patron/tolerancias) al crear la nueva sesion.
        # Tambien elimina el detector de machine_stop del cache (se recrea fresco).
        self._inspector.invalidate(model=model_init, scanner_id=self._id)
        session = InspectionSession(
            model_init,
            scanner_id=self._id,
            movement_threshold=self._cont_pos_thr,
            min_interval_sec=self._min_insp_interval,
            require_initial_movement=True,
            resource_owner=self._inspector,
        )
        with self._lock:
            session_cache_revision = self._cache_revision
        if not self._run_startup_selftest(model_init):
            self._cut_solenoid_critical("selftest inicial fallido")
            self._set_lights(red=True)
            self._transition(ScannerState.ERROR)
            return

        self._run_roi_precalibration(model_init, session)

        _grace_tols = load_tolerances(model_init, scanner_id=self._id)
        self._startup_grace_remaining = int(_grace_tols.get("startup_grace_frames", 30))
        self._startup_grace_seconds   = float(_grace_tols.get("startup_grace_seconds", 0.0))
        # No iniciar el reloj al pulsar RUN: la cinta puede demorar un tiempo
        # variable en arrancar. Se ancla en _handle_result al primer frame que
        # supera el gate de movimiento y entra realmente al analisis.
        self._run_loop_start_mono     = 0.0
        if self._startup_grace_remaining > 0 or self._startup_grace_seconds > 0.0:
            logger.info(
                "[%s] startup grace: %d frames y/o %.1fs sin machine_stop ni fault",
                self._id, self._startup_grace_remaining, self._startup_grace_seconds,
            )

        while not self._stop_event.is_set():
            with self._lock:
                if self._state != ScannerState.RUNNING:
                    self._stop_event.wait(timeout=0.05)
                    continue
                model = self._io.scanner_config(self._id)["model"]
                cache_revision = self._cache_revision
            if model != session._model or cache_revision != session_cache_revision:
                session = InspectionSession(
                    model,
                    scanner_id=self._id,
                    movement_threshold=self._cont_pos_thr,
                    min_interval_sec=self._min_insp_interval,
                    require_initial_movement=True,
                    resource_owner=self._inspector,
                )
                session_cache_revision = cache_revision
                logger.info(
                    "[%s] sesión de análisis recargada en vivo "
                    "(modelo=%s revision=%d)",
                    self._id, model, session_cache_revision,
                )

            frame = self._camera.get_frame()
            if frame is None:
                now = time.monotonic()
                escalate = False
                with self._lock:
                    if self._camera_missing_since is None:
                        self._camera_missing_since = now
                        self._camera_missing_events += 1
                    missing_sec = now - self._camera_missing_since
                    if not self._camera_missing_warned:
                        self._camera_missing_warned = True
                        logger.warning(
                            f"[{self._id}] CAMARA DESCONECTADA - reconectando "
                            f"(timeout error {self._camera_missing_timeout_s:.1f}s)"
                        )
                    if missing_sec >= self._camera_missing_timeout_s:
                        escalate = (self._state == ScannerState.RUNNING)
                if escalate:
                    logger.error(
                        f"[{self._id}] ERROR por perdida de camara - "
                        f"sin frames durante {missing_sec:.1f}s"
                    )
                    self._cut_solenoid_critical("perdida de camara")
                    self._set_lights(red=True)
                    self._transition(ScannerState.ERROR)
                    return
                self._stop_event.wait(timeout=0.033)
                continue

            with self._lock:
                if self._camera_missing_since is not None:
                    self._camera_missing_total_s += max(
                        0.0, time.monotonic() - self._camera_missing_since
                    )
                    logger.info(f"[{self._id}] camara reconectada - vuelve inspeccion")
                self._camera_missing_since = None
                self._camera_missing_warned = False

            if self._recorder is not None:
                self._recorder.add_frame(frame)

            forced = self._force_inspect.is_set()
            if forced:
                self._force_inspect.clear()

            frame_counter += 1
            if False and frame_counter % 500 == 0:
                from src.utils.license import is_licensed
                if not is_licensed():
                    import logging as _lg
                    _lg.getLogger(__name__).critical(
                        "[%s] sistema no autorizado — deteniendo scanner", self._id
                    )
                    self._cut_solenoid_critical("licencia invalida")
                    self._set_lights()
                    with self._lock:
                        if self._state == ScannerState.RUNNING:
                            self._state = ScannerState.STOPPED
                    self._stop_event.set()
                    self._fire_state_changed()
                    return

            fid = (f"{self._id}_cont_{datetime.now().strftime('%H%M%S')}"
                   f"_{frame_counter:04d}")
            res = session.inspect_frame(frame, frame_id=fid, force=forced)
            with self._lock:
                self._last_position_diff = session.last_position_diff

            if res is None:
                self._stop_event.wait(timeout=0.005)
                continue

            self._handle_result(res, model, session=session)
            self._stop_event.wait(timeout=0.005)

    def _handle_result(
        self,
        result: InspectionResult,
        model: str = "",
        session: InspectionSession | None = None,
    ) -> None:
        """Actualiza la FSM y dispara callbacks tras un resultado de inspección."""
        if (result.status == "NOK" and self._save_nok) or \
           (result.status == "OK"  and self._save_ok):
            def _save_result(r=result) -> None:
                try:
                    save_result_images(r)
                except Exception as exc:
                    logger.error(f"[{self._id}] error guardando imagen: {exc}")
            self._enqueue_disk_task("save_result", _save_result, allow_drop=False)

        # Si el recorder está grabando el post-evento, guardar overlay del frame analizado
        if self._recorder is not None and result.overlay is not None:
            event_dir = self._recorder.get_post_event_dir()
            if event_dir is not None:
                _overlay = result.overlay
                _sid = self._id
                def _save_ev_overlay(ov=_overlay, d=event_dir) -> None:
                    try:
                        # Buscar el último frame post guardado y crear overlay con mismo índice
                        existing = sorted(d.glob("post_[0-9]*.jpg"))
                        existing = [f for f in existing if "_overlay" not in f.name]
                        if existing:
                            idx = int(existing[-1].stem.split("_")[1])
                            ov_path = d / f"post_{idx:04d}_overlay.jpg"
                            if not ov_path.exists():
                                import cv2 as _cv2
                                _cv2.imwrite(str(ov_path), ov,
                                             [_cv2.IMWRITE_JPEG_QUALITY, 85])
                    except Exception as exc:
                        logger.debug(f"[{_sid}] overlay post-evento: {exc}")
                self._enqueue_disk_task("event_overlay", _save_ev_overlay, allow_drop=True)

        consecutive_nok = self._consecutive_nok
        warn_at = max(1, consecutive_nok // 3)

        fault_triggered = False
        machine_stop_triggered = False
        streak_start_mono: Optional[float] = None
        with self._lock:
            self._last_result = result
            self._total_inspections += 1
            self._total_detection_ratio += getattr(result, "detection_ratio", 1.0)
            if not getattr(result, "alignment_ok", True):
                self._align_fail_count += 1

            frame_quality = getattr(result, "frame_quality", "GOOD")
            if frame_quality == "LOW_QUALITY":
                self._low_quality_count += 1
                # Hold: frame borroso/degradado no incrementa ni resetea la racha NOK.
                self._lq_streak += 1
                if self._low_quality_max_streak > 0 and self._lq_streak >= self._low_quality_max_streak:
                    # Demasiados frames de baja calidad seguidos → resetear racha
                    self._nok_streak = 0
                    self._lq_streak  = 0
                # Los contadores de ok/nok no se actualizan para frames de baja calidad
            else:
                self._lq_streak = 0
                if result.status == "NOK":
                    if self._nok_streak == 0:
                        self._streak_start_mono = time.monotonic()
                    self._nok_streak += 1
                    self._nok_count  += 1
                    self._total_missing    += result.report.missing
                    self._nok_with_missing += 1
                    self._frames_since_last_nok = 0
                else:
                    self._nok_streak = 0
                    self._streak_start_mono = None
                    self._ok_count  += 1
                    self._frames_since_last_nok += 1
                    if (
                        self._nok_count > 0
                        and self._frames_since_last_nok >= self._nok_count_reset_frames
                    ):
                        logger.info(
                            f"[{self._id}] contador NOK reseteado a 0 tras "
                            f"{self._frames_since_last_nok} frames OK seguidos "
                            f"(quedaba en {self._nok_count})"
                        )
                        self._nok_count = 0
                        self._frames_since_last_nok = 0

            streak = self._nok_streak
            if streak > self._max_nok_streak:
                self._max_nok_streak = streak

            in_grace_frames = self._startup_grace_remaining > 0
            if in_grace_frames:
                self._startup_grace_remaining -= 1
            if self._run_loop_start_mono <= 0.0:
                self._run_loop_start_mono = time.monotonic()
            in_grace_time = (
                self._startup_grace_seconds > 0.0
                and (time.monotonic() - self._run_loop_start_mono) < self._startup_grace_seconds
            )
            in_grace = in_grace_frames or in_grace_time

            _ms_suppressed = False
            if getattr(result, "machine_stop", False):
                if in_grace:
                    logger.debug(
                        "[%s] machine_stop suprimido (grace frames=%d tiempo=%s)",
                        self._id, self._startup_grace_remaining + 1, in_grace_time,
                    )
                    _ms_suppressed = True
                    if session is not None:
                        session.reset_stop_state()
                elif not self._machine_stop_enabled:
                    logger.debug("[%s] machine_stop suprimido (machine_stop_enabled=false)", self._id)
                    _ms_suppressed = True
                else:
                    machine_stop_triggered = True
                    self._machine_stop_count += 1
            if streak >= consecutive_nok and self._state == ScannerState.RUNNING:
                if in_grace:
                    logger.debug("[%s] fault suprimido por grace period (streak=%d)", self._id, streak)
                    self._nok_streak = 0  # reset streak para no acumular durante grace
                    self._streak_start_mono = None
                    # Resetear tambien el detector de machine_stop: sin esto, el
                    # historial acumulado durante grace persiste y puede disparar
                    # machine_stop al primer frame post-grace que coincida con la
                    # misma zona aunque la pieza sea correcta.
                    if session is not None:
                        session.reset_stop_state()
                    else:
                        self._inspector.reset_machine_stop(model, self._id)
                else:
                    self._state     = ScannerState.FAULT
                    fault_triggered = True
                    self._fault_count += 1
                    streak_start_mono = self._streak_start_mono

        if machine_stop_triggered:
            _ms_reason = self._derive_stop_reason(result)
            logger.warning(f"[{self._id}] DETENCION DE MAQUINA — {_ms_reason}")

            # Detener el scanner sin join (se llama desde el inspector thread;
            # join causaría deadlock). Los threads ven _stop_event y salen solos.
            _was_running = False
            with self._lock:
                if self._state == ScannerState.RUNNING:
                    self._state   = ScannerState.STOPPED
                    self._stopped_by_fault = True
                    _was_running  = True
            if _was_running:
                self._cut_solenoid_critical("machine fault")
                # backlight permanece encendido siempre
                self._set_lights(red=True)   # rojo fijo = intervención requerida
                self._stop_event.set()
                self._fire_state_changed()

            if self._recorder is not None:
                try:
                    self._recorder.flush_event("machine_stop", _ms_reason)
                except Exception as _exc:
                    logger.error(f"[{self._id}] EventRecorder flush error: {_exc}")

            # Notificar UI con el frame que causó la parada (overlay + diálogo pantalla completa)
            if self.on_result:
                try:
                    self.on_result(result, streak)
                except Exception as _exc:
                    logger.error(f"[{self._id}] on_result error (machine_stop): {_exc}")

            # No actualizar luces al final del método: el scanner ya está STOPPED
            return

        if fault_triggered:
            elapsed_txt = ""
            if streak_start_mono is not None:
                elapsed_txt = f" ({time.monotonic() - streak_start_mono:.2f}s reales)"
            logger.warning(f"[{self._id}] FAULT — {streak} NOK consecutivos{elapsed_txt}")
            if self._recorder is not None:
                try:
                    self._recorder.flush_event("fault", f"racha NOK {streak}")
                except Exception as _exc:
                    logger.error(f"[{self._id}] EventRecorder flush error: {_exc}")
            self._cut_solenoid_critical("fault por racha NOK")
            # backlight permanece encendido siempre
            self._set_lights(red=True)   # poll_loop toma el blink a partir de aquí
            self._fire_state_changed()
            # Detiene el hilo inspector para que no siga generando resultados en pantalla.
            # El operario debe presionar DETENER → RESET → INICIAR para reanudar.
            self._stop_event.set()
        elif streak >= warn_at:
            self._set_lights(green=True, yellow=True)   # poll_loop toma el blink
        else:
            self._set_lights(green=True)

        # Si machine_stop fue suprimido (grace o disabled), limpiar la bandera del
        # resultado antes de enviarlo a la UI para no mostrar "DETENCION DE MAQUINA"
        # en el overlay cuando la parada no se aplica realmente.
        if _ms_suppressed:
            result = dataclasses.replace(result, machine_stop=False)

        if self.on_result:
            try:
                self.on_result(result, streak)
            except Exception as exc:
                logger.error(f"[{self._id}] on_result callback error: {exc}")

        # Buffer circular de frames OK — guardado asíncrono, prioridad baja
        if self._ok_buf_enabled and result.status == "OK" and result.overlay is not None:
            self._ok_seen += 1
            if self._ok_seen % self._ok_buf_every == 0:
                slot  = self._ok_write % self._ok_buf_max
                self._ok_write += 1
                overlay   = result.overlay
                out_path  = self._ok_buf_dir / f"ok_{slot:04d}.jpg"
                quality   = self._ok_buf_quality
                buf_dir   = self._ok_buf_dir

                raw_frame = result.image
                raw_path  = self._ok_buf_dir / f"ok_{slot:04d}_raw.jpg"

                def _write(img=overlay, p=out_path, q=quality, d=buf_dir,
                           raw=raw_frame, rp=raw_path) -> None:
                    try:
                        d.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, q])
                        if raw is not None:
                            cv2.imwrite(str(rp), raw, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    except Exception:
                        pass  # buffer circular, pérdida de un frame es aceptable

                self._enqueue_disk_task("ok_buffer", _write, allow_drop=True)

        # Buffer cronológico — guarda todos los frames inspeccionados en orden
        if self._tl_enabled and result.overlay is not None:
            _tl_status = getattr(result, "frame_quality", "GOOD")
            if _tl_status == "LOW_QUALITY":
                _tl_tag = "LQ"
            elif getattr(result, "machine_stop", False):
                _tl_tag = "STOP"
            elif result.status == "NOK":
                _tl_tag = "NOK"
            else:
                _tl_tag = "OK"
            _tl_slot = self._tl_write % self._tl_max
            self._tl_write += 1
            _tl_path = self._tl_dir / f"{_tl_slot:05d}_{_tl_tag}.jpg"
            _tl_img  = result.overlay
            _tl_q    = self._tl_quality
            _tl_dir  = self._tl_dir

            def _write_tl(img=_tl_img, p=_tl_path, q=_tl_q, d=_tl_dir) -> None:
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    # Borrar el slot anterior del mismo número si existe (puede tener distinto tag)
                    for old in d.glob(f"{p.stem.split('_')[0]}_*.jpg"):
                        if old != p:
                            try:
                                old.unlink()
                            except Exception:
                                pass
                    cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, q])
                except Exception:
                    pass

            self._enqueue_disk_task("timeline", _write_tl, allow_drop=True)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _set_lights(self, *, blue=False, green=False, yellow=False, red=False) -> None:
        self._io.write_batch([
            (f"{self._id}.light_blue",   blue),
            (f"{self._id}.light_green",  green),
            (f"{self._id}.light_yellow", yellow),
            (f"{self._id}.light_red",    red),
        ])

    def _start_poller_thread(self) -> None:
        if self._poller_thread is not None and self._poller_thread.is_alive():
            raise RuntimeError(f"[{self._id}] poller ya activo")
        self._poller_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name=f"{self._id}-poller"
        )
        self._poller_thread.start()

    def _start_all_threads(self) -> None:
        self._start_poller_thread()
        if self._inspector_thread is not None and self._inspector_thread.is_alive():
            self._stop_event.set()
            raise RuntimeError(f"[{self._id}] inspector ya activo")
        self._inspector_thread = threading.Thread(
            target=self._continuous_loop, daemon=True, name=f"{self._id}-inspector"
        )
        self._inspector_thread.start()

    def _join_threads(self) -> None:
        current = threading.current_thread()
        for attr, timeout in (("_poller_thread", 1.0), ("_inspector_thread", 5.0)):
            thread = getattr(self, attr)
            if thread is None:
                continue
            if thread is current:
                logger.error("[%s] no se puede esperar al hilo actual %s", self._id, thread.name)
                continue
            thread.join(timeout=timeout)
            if thread.is_alive():
                # Conservar la referencia: start() debe bloquear una nueva
                # sesion para evitar dos loops compartiendo el mismo stop_event.
                logger.error(
                    "[%s] hilo %s no termino tras %.1fs; se bloquea nuevo inicio",
                    self._id,
                    thread.name,
                    timeout,
                )
            else:
                setattr(self, attr, None)

    def _workers_ready_for_start(self) -> bool:
        """Limpia referencias terminadas y rechaza un RUN con hilos viejos vivos."""
        ready = True
        for attr in ("_poller_thread", "_inspector_thread"):
            thread = getattr(self, attr)
            if thread is None:
                continue
            if thread.is_alive():
                ready = False
            else:
                setattr(self, attr, None)
        return ready

    def _update_mode_from_plc(self) -> None:
        # Siempre leer la maneta física (para mostrar su estado real en la UI),
        # aunque force_auto_mode este activo y el modo operativo real quede
        # forzado en AUTO independientemente de la lectura.
        mode_raw = self._io.read(f"{self._id}.mode_switch")
        with self._lock:
            self._mode_switch_raw = mode_raw
        if self._force_auto:
            return
        if mode_raw is not None:
            new_mode = OperationMode.AUTO if mode_raw else OperationMode.MANUAL
            with self._lock:
                self._mode = new_mode

    def _enqueue_disk_task(
        self,
        task_type: str,
        func: Callable[[], None],
        *,
        allow_drop: bool,
    ) -> None:
        item = (task_type, func)
        try:
            if allow_drop:
                self._disk_queue.put_nowait(item)
            else:
                self._disk_queue.put(item, timeout=0.1)
            return
        except queue.Full:
            count = self._disk_drop_counts.get(task_type, 0) + 1
            self._disk_drop_counts[task_type] = count
            if count in (1, 10, 100) or count % 1000 == 0:
                logger.warning(
                    "[%s] disk-writer saturado: descartando tarea %s (total=%d)",
                    self._id, task_type, count,
                )

    def _disk_worker_loop(self) -> None:
        while True:
            try:
                item = self._disk_queue.get(timeout=0.2)
            except queue.Empty:
                if self._disk_stop_event.is_set():
                    break
                continue

            if item is None:
                self._disk_queue.task_done()
                break

            task_type, func = item
            try:
                func()
            except Exception as exc:
                logger.error("[%s] disk task %s error: %s", self._id, task_type, exc)
            finally:
                self._disk_queue.task_done()

    def _stop_disk_worker(self) -> None:
        if self._disk_thread is None:
            return
        self._disk_stop_event.set()
        deadline = time.monotonic() + 5.0
        while not self._disk_queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            self._disk_queue.put_nowait(None)
        except queue.Full:
            pass
        self._disk_thread.join(timeout=5.0)
        if self._disk_thread.is_alive():
            logger.error("[%s] disk writer no termino limpiamente", self._id)
        self._disk_thread = None

    @staticmethod
    def _license_allows_operation() -> bool:
        from src.utils.license import is_licensed

        return is_licensed()

    def _handle_license_failure(self) -> None:
        logger.critical("[%s] licencia invalida o vencida - deteniendo scanner", self._id)
        self._cut_solenoid_critical("licencia invalida")
        self._set_lights()
        with self._lock:
            if self._state == ScannerState.RUNNING:
                self._state = ScannerState.STOPPED
        self._stop_event.set()
        self._fire_state_changed()

    def _cut_solenoid_critical(self, context: str) -> bool:
        """Corta la electroválvula con reintentos y verificación de coil."""
        ok = self._io.write_critical(
            f"{self._id}.solenoid",
            False,
            retries=5,
            retry_delay_s=0.15,
            verify=True,
        )
        if not ok:
            logger.error("[%s] NO se pudo confirmar solenoid=OFF tras %s", self._id, context)
        return ok

    @staticmethod
    def _derive_stop_reason(result: InspectionResult) -> str:
        """Extrae una razón legible del InspectionResult para el manifest de evento."""
        if getattr(result, "tilt_warn", False):
            return "chapa inclinada"
        if getattr(result, "pattern_alignment_warn", False):
            return "patron desalineado"
        missing = getattr(result.report, "missing", 0)
        return f"{missing} faltantes persistentes"

    def _transition(self, new_state: ScannerState) -> None:
        with self._lock:
            self._state = new_state
        self._fire_state_changed()

    def _fire_state_changed(self) -> None:
        if self.on_state_changed:
            with self._lock:
                state, mode = self._state, self._mode
            try:
                self.on_state_changed(state, mode)
            except Exception as exc:
                logger.error(f"[{self._id}] on_state_changed callback error: {exc}")
