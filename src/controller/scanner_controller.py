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

import logging
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
from src.utils.state import OperationMode, ScannerState
from src.vision.camera import Camera
from src.vision.inspector import Inspector

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
        tols     = load_tolerances(model)

        self._consecutive_nok = int(
            insp_cfg.get("consecutive_nok_frames",
                         tols["consecutive_nok_frames"])
        )
        self._low_quality_max_streak = int(tols.get("low_quality_max_streak", 10))
        self._save_nok      = bool(insp_cfg.get("save_nok_frames", True))
        self._save_ok       = bool(insp_cfg.get("save_ok_frames",  False))
        self._poll_interval = io.plc_config.get("poll_interval_ms", 50) / 1000.0
        self._cont_pos_thr  = float(
            tols.get("continuous_position_threshold", 8.0)
        )
        _max_hz = float(tols.get("max_inspection_hz", 0))
        self._min_insp_interval = (1.0 / _max_hz) if _max_hz > 0 else 0.0

        self._state      = ScannerState.IDLE
        self._mode       = OperationMode.MANUAL
        self._nok_streak = 0
        self._lq_streak  = 0
        self._last_result: Optional[InspectionResult] = None

        # Métricas de sesión (resetean en start())
        self._total_inspections: int       = 0
        self._ok_count:          int       = 0
        self._nok_count:         int       = 0
        self._session_start: Optional[datetime] = None
        self._max_nok_streak:    int       = 0
        self._fault_count:       int       = 0
        self._machine_stop_count: int      = 0
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
        self._ok_buf_dir      = Path("data/output/ok_buffer") / scanner_id
        self._ok_seen: int    = 0   # frames OK vistos (para throttle)
        self._ok_write: int   = 0   # posición de escritura en el pool

        self._lock          = threading.Lock()
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
                    events_dir=Path("data/events"),
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
        with self._lock:
            if self._state != ScannerState.IDLE:
                logger.warning(f"[{self._id}] start() ignorado, estado={self._state.value}")
                return False
            mode = self._mode

        if mode == OperationMode.AUTO:
            if not self._camera.is_running:
                if not self._camera.start():
                    self._transition(ScannerState.ERROR)
                    return False

        with self._lock:
            self._nok_streak              = 0
            self._lq_streak               = 0
            self._total_inspections       = 0
            self._ok_count                = 0
            self._nok_count               = 0
            self._session_start           = datetime.now()
            self._max_nok_streak          = 0
            self._fault_count             = 0
            self._machine_stop_count      = 0
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
            # Encender backlight ANTES de iniciar threads para que el selftest
            # vea frames iluminados desde el primer momento.
            self._io.write(f"{self._id}.backlight", True)
            self._start_all_threads()
        else:
            self._start_poller_thread()

        self._transition(ScannerState.RUNNING)
        self._set_lights(green=True)
        logger.info(f"[{self._id}] iniciado ({mode.value})")
        return True

    def stop(self) -> None:
        """Detiene el scanner.

        MANUAL RUNNING → IDLE  (azul; listo para INICIAR de nuevo).
        AUTO RUNNING   → STOPPED (luces apagadas; espera RESET → IDLE).
        FAULT          → STOPPED (ídem).
        """
        with self._lock:
            if self._state in (ScannerState.IDLE, ScannerState.STOPPED):
                return
            state = self._state
            mode  = self._mode

        # FAULT siempre → STOPPED; AUTO RUNNING → STOPPED; MANUAL RUNNING → IDLE
        if state == ScannerState.FAULT or mode == OperationMode.AUTO:
            new_state = ScannerState.STOPPED
        else:
            new_state = ScannerState.IDLE

        self._transition(new_state)
        self._io.write(f"{self._id}.solenoid", False)
        # backlight permanece encendido siempre

        if new_state == ScannerState.IDLE:
            self._set_lights(blue=True)
        else:
            self._set_lights()   # todas apagadas en STOPPED

        self._stop_event.set()
        self._join_threads()
        logger.info(f"[{self._id}] detenido → {new_state.value}")

    def reset(self) -> bool:
        """STOPPED → IDLE + azul. Requiere INICIAR para reanudar."""
        with self._lock:
            if self._state != ScannerState.STOPPED:
                return False

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
        with self._lock:
            if self._state != ScannerState.IDLE:
                return False
            self._mode                    = OperationMode.AUTO
            self._nok_streak              = 0
            self._lq_streak               = 0
            self._total_inspections       = 0
            self._ok_count                = 0
            self._nok_count               = 0
            self._session_start           = datetime.now()
            self._max_nok_streak          = 0
            self._fault_count             = 0
            self._machine_stop_count      = 0
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

        self._start_poller_thread()
        # solenoid deshabilitado por seguridad hasta implementar control automático
        self._io.write(f"{self._id}.backlight", True)
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
        self._io.write(f"{self._id}.solenoid", False)
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

    def set_model(self, model: str) -> None:
        cfg      = self._io.scanner_config(self._id)
        cfg["model"] = model
        insp_cfg = cfg.get("inspection", {})
        tols     = load_tolerances(model)
        self._consecutive_nok = int(
            insp_cfg.get("consecutive_nok_frames", tols["consecutive_nok_frames"])
        )
        self._inspector.invalidate(model=model, scanner_id=self._id)
        logger.info(f"[{self._id}] modelo cambiado a '{model}' "
                    f"(consecutive_nok={self._consecutive_nok})")

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
        # Lee el switch directamente del PLC para reflejar cambios en cualquier estado
        mode_raw = self._io.read(f"{self._id}.mode_switch")
        if mode_raw is not None:
            with self._lock:
                self._mode = OperationMode.AUTO if mode_raw else OperationMode.MANUAL

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
                "nok_streak":           self._nok_streak,
                "last_result":          self._last_result,
                "total_inspections":    self._total_inspections,
                "ok_count":             self._ok_count,
                "nok_count":            self._nok_count,
                "session_start":        self._session_start,
                "max_nok_streak":       self._max_nok_streak,
                "fault_count":          self._fault_count,
                "machine_stop_count":   self._machine_stop_count,
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
        while not self._stop_event.is_set():
            # Leer switch de modo
            mode_raw = self._io.read(f"{self._id}.mode_switch")
            if mode_raw is not None:
                new_mode = OperationMode.AUTO if mode_raw else OperationMode.MANUAL
                with self._lock:
                    self._mode = new_mode

            with self._lock:
                state  = self._state
                streak = self._nok_streak

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
        tols = load_tolerances(model)
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

    def _continuous_loop(self) -> None:
        """Modo continuo AUTO: inspecciona cuando el material avanzó lo suficiente.

        Compara cada frame con el ÚLTIMO FRAME INSPECCIONADO (no con el anterior).
        Si la diferencia media supera continuous_position_threshold → nueva sección
        → inspeccionar → actualizar referencia.
        """
        last_gray: Optional[np.ndarray] = None
        frame_counter = 0
        last_insp_time: float = 0.0

        with self._lock:
            model_init = self._io.scanner_config(self._id)["model"]
        if not self._run_startup_selftest(model_init):
            self._io.write(f"{self._id}.solenoid", False)
            # backlight permanece encendido siempre
            self._set_lights(red=True)
            self._transition(ScannerState.ERROR)
            return

        while not self._stop_event.is_set():
            with self._lock:
                if self._state != ScannerState.RUNNING:
                    self._stop_event.wait(timeout=0.05)
                    continue
                model = self._io.scanner_config(self._id)["model"]

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
                    self._io.write(f"{self._id}.solenoid", False)
                    # backlight permanece encendido siempre
                    self._set_lights(red=True)
                    self._transition(ScannerState.ERROR)
                    return
                self._stop_event.wait(timeout=0.033)
                continue
            else:
                with self._lock:
                    if self._camera_missing_since is not None:
                        self._camera_missing_total_s += max(
                            0.0, time.monotonic() - self._camera_missing_since
                        )
                        logger.info(f"[{self._id}] camara reconectada - vuelve inspeccion")
                    self._camera_missing_since = None
                    self._camera_missing_warned = False

            # Alimentar buffer de evidencia con frame ORIGINAL (sin overlay)
            if self._recorder is not None:
                self._recorder.add_frame(frame)

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            pos_thr = self._cont_pos_thr
            min_interval = self._min_insp_interval

            forced = self._force_inspect.is_set()
            if forced:
                self._force_inspect.clear()

            now = time.monotonic()
            # Límite de tasa: si no ha pasado el intervalo mínimo, saltar
            if not forced and min_interval > 0 and (now - last_insp_time) < min_interval:
                self._stop_event.wait(timeout=0.005)
                continue

            if last_gray is not None and not forced:
                diff = float(np.mean(cv2.absdiff(gray, last_gray)))
                with self._lock:
                    self._last_position_diff = diff
                if pos_thr > 0 and diff < pos_thr:
                    self._stop_event.wait(timeout=0.033)
                    continue
                logger.debug(f"[{self._id}] nueva sección detectada (diff={diff:.1f})")

            frame_counter += 1
            fid = (f"{self._id}_cont_{datetime.now().strftime('%H%M%S')}"
                   f"_{frame_counter:04d}")
            res = self._inspector.inspect(model, frame, frame_id=fid,
                                          scanner_id=self._id)
            if res is not None:
                last_gray = gray
                last_insp_time = time.monotonic()
                self._handle_result(res, model)

            self._stop_event.wait(timeout=0.005)

    def _handle_result(self, result: InspectionResult, model: str = "") -> None:
        """Actualiza la FSM y dispara callbacks tras un resultado de inspección."""
        if (result.status == "NOK" and self._save_nok) or \
           (result.status == "OK"  and self._save_ok):
            try:
                save_result_images(result)
            except Exception as exc:
                logger.error(f"[{self._id}] error guardando imagen: {exc}")

        consecutive_nok = self._consecutive_nok
        warn_at = max(1, consecutive_nok // 3)

        fault_triggered = False
        machine_stop_triggered = False
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
                    self._nok_streak += 1
                    self._nok_count  += 1
                    self._total_missing    += result.report.missing
                    self._nok_with_missing += 1
                else:
                    self._nok_streak = 0
                    self._ok_count  += 1

            streak = self._nok_streak
            if streak > self._max_nok_streak:
                self._max_nok_streak = streak
            if getattr(result, "machine_stop", False):
                # Virtual stop only — no FSM transition, no hardware writes.
                # Safety rule: solenoids stay blocked; only UI/overlay/log are affected.
                machine_stop_triggered = True
                self._machine_stop_count += 1
            if streak >= consecutive_nok and self._state == ScannerState.RUNNING:
                self._state     = ScannerState.FAULT
                fault_triggered = True
                self._fault_count += 1

        if machine_stop_triggered:
            _ms_reason = self._derive_stop_reason(result)
            logger.warning(f"[{self._id}] DETENCION DE MAQUINA — {_ms_reason}")

            # Detener el scanner sin join (se llama desde el inspector thread;
            # join causaría deadlock). Los threads ven _stop_event y salen solos.
            _was_running = False
            with self._lock:
                if self._state == ScannerState.RUNNING:
                    self._state   = ScannerState.STOPPED
                    _was_running  = True
            if _was_running:
                self._io.write(f"{self._id}.solenoid", False)
                # backlight permanece encendido siempre
                self._set_lights(red=True)   # rojo fijo = intervención requerida
                self._stop_event.set()
                self._fire_state_changed()

            if self._recorder is not None:
                try:
                    self._recorder.flush_event("machine_stop", _ms_reason)
                except Exception as _exc:
                    logger.error(f"[{self._id}] EventRecorder flush error: {_exc}")

            # No actualizar luces al final del método: el scanner ya está STOPPED
            return

        if fault_triggered:
            logger.warning(f"[{self._id}] FAULT — {streak} NOK consecutivos")
            if self._recorder is not None:
                try:
                    self._recorder.flush_event("fault", f"racha NOK {streak}")
                except Exception as _exc:
                    logger.error(f"[{self._id}] EventRecorder flush error: {_exc}")
            self._io.write(f"{self._id}.solenoid", False)
            # backlight permanece encendido siempre
            self._set_lights(red=True)   # poll_loop toma el blink a partir de aquí
            self._fire_state_changed()
        elif streak >= warn_at:
            self._set_lights(green=True, yellow=True)   # poll_loop toma el blink
        else:
            self._set_lights(green=True)

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

                def _write(img=overlay, p=out_path, q=quality, d=buf_dir) -> None:
                    try:
                        d.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, q])
                    except Exception:
                        pass  # buffer circular, pérdida de un frame es aceptable

                threading.Thread(target=_write, daemon=True,
                                 name=f"{self._id}-ok-buf").start()

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
        self._poller_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name=f"{self._id}-poller"
        )
        self._poller_thread.start()

    def _start_all_threads(self) -> None:
        self._start_poller_thread()
        self._inspector_thread = threading.Thread(
            target=self._continuous_loop, daemon=True, name=f"{self._id}-inspector"
        )
        self._inspector_thread.start()

    def _join_threads(self) -> None:
        if self._poller_thread:
            self._poller_thread.join(timeout=1.0)
            self._poller_thread = None
        if self._inspector_thread:
            self._inspector_thread.join(timeout=5.0)
            self._inspector_thread = None

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
