"""
FSM de un scanner individual.

Estados:
  IDLE    → azul encendida, esperando START del operador
  RUNNING → verde/amarillo según racha NOK, electroválvula activa, backlight ON
  FAULT   → roja encendida, electroválvula detenida, esperando reset
  ERROR   → fallo de hardware (cámara o PLC)

Threads:
  _poller_thread   — lee PLC cada poll_interval_ms, detecta flanco HIGH→LOW
  _inspector_thread — espera trigger, captura frame, inspecciona
"""

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from src.inspection import InspectionResult
from src.plc.io_map import IOMap
from src.utils.config import load_tolerances
from src.utils.state import OperationMode, ScannerState
from src.vision.camera import Camera
from src.vision.inspector import Inspector

logger = logging.getLogger(__name__)


class ScannerController:
    def __init__(self, scanner_id: str, io: IOMap, camera: Camera) -> None:
        self._id       = scanner_id
        self._io       = io
        self._camera   = camera
        self._inspector = Inspector()

        cfg        = io.scanner_config(scanner_id)
        tolerances = load_tolerances()
        insp_cfg   = cfg.get("inspection", {})
        self._consecutive_nok = int(
            insp_cfg.get("consecutive_nok_frames",
                         tolerances["consecutive_nok_frames"])
        )
        self._poll_interval = io.plc_config.get("poll_interval_ms", 50) / 1000.0

        self._state       = ScannerState.IDLE
        self._mode        = OperationMode.MANUAL
        self._nok_streak  = 0
        self._last_result: Optional[InspectionResult] = None

        # Métricas de sesión (resetean en start())
        self._total_inspections: int       = 0
        self._ok_count:          int       = 0
        self._nok_count:         int       = 0
        self._session_start: Optional[datetime] = None
        self._max_nok_streak:    int       = 0

        self._lock          = threading.Lock()
        self._trigger_event = threading.Event()
        self._stop_event    = threading.Event()

        self._poller_thread:   Optional[threading.Thread] = None
        self._inspector_thread: Optional[threading.Thread] = None

        # Callbacks opcionales para la UI (llamados fuera de locks)
        self.on_state_changed: Optional[Callable[[ScannerState, OperationMode], None]] = None
        self.on_result: Optional[Callable[[InspectionResult, int], None]] = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """IDLE → RUNNING. Arranca cámara, activa electroválvula y backlight."""
        with self._lock:
            if self._state != ScannerState.IDLE:
                logger.warning(f"[{self._id}] start() ignorado, estado={self._state.value}")
                return False

        if not self._camera.is_running:
            if not self._camera.start():
                self._transition(ScannerState.ERROR)
                return False

        with self._lock:
            self._nok_streak         = 0
            self._total_inspections  = 0
            self._ok_count           = 0
            self._nok_count          = 0
            self._session_start      = datetime.now()
            self._max_nok_streak     = 0
            self._stop_event.clear()
            self._trigger_event.clear()

        self._start_threads()
        self._transition(ScannerState.RUNNING)

        # Luces: verde ON, backlight ON, azul/amarillo/rojo OFF
        self._set_lights(green=True)
        self._io.write(f"{self._id}.solenoid",  True)
        self._io.write(f"{self._id}.backlight", True)

        logger.info(f"[{self._id}] iniciado")
        return True

    def stop(self) -> None:
        """Detiene el scanner → IDLE. Apaga electroválvula y backlight."""
        with self._lock:
            if self._state == ScannerState.IDLE:
                return

        self._transition(ScannerState.IDLE)

        self._io.write(f"{self._id}.solenoid",  False)
        self._io.write(f"{self._id}.backlight", False)
        self._set_lights(blue=True)   # IDLE = azul

        self._stop_event.set()
        self._trigger_event.set()
        self._join_threads()
        logger.info(f"[{self._id}] detenido")

    def reset(self) -> bool:
        """FAULT → RUNNING. Reactiva electroválvula y reanuda inspección."""
        with self._lock:
            if self._state != ScannerState.FAULT:
                return False
            self._nok_streak = 0

        self._io.write(f"{self._id}.solenoid",  True)
        self._io.write(f"{self._id}.backlight", True)
        self._set_lights(green=True)
        self._transition(ScannerState.RUNNING)
        logger.info(f"[{self._id}] reset, reanudando")
        return True

    def set_model(self, model: str) -> None:
        cfg = self._io.scanner_config(self._id)
        cfg["model"] = model
        logger.info(f"[{self._id}] modelo cambiado a '{model}'")

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
            return {
                "state":             self._state,
                "mode":              self._mode,
                "nok_streak":        self._nok_streak,
                "last_result":       self._last_result,
                "total_inspections": self._total_inspections,
                "ok_count":          self._ok_count,
                "nok_count":         self._nok_count,
                "session_start":     self._session_start,
                "max_nok_streak":    self._max_nok_streak,
            }

    # ------------------------------------------------------------------
    # Thread: poller PLC
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        prev_punch: Optional[bool] = None

        while not self._stop_event.is_set():
            mode_raw = self._io.read(f"{self._id}.mode_switch")
            if mode_raw is not None:
                new_mode = OperationMode.AUTO if mode_raw else OperationMode.MANUAL
                with self._lock:
                    self._mode = new_mode

            punch = self._io.read(f"{self._id}.punch_sensor")
            if punch is None:
                with self._lock:
                    current = self._state
                if current == ScannerState.RUNNING:
                    self._transition(ScannerState.ERROR)
            else:
                if prev_punch is True and punch is False:   # flanco HIGH→LOW
                    with self._lock:
                        should_trigger = (
                            self._state == ScannerState.RUNNING
                            and self._mode == OperationMode.AUTO
                        )
                    if should_trigger:
                        self._trigger_event.set()
                prev_punch = punch

            self._stop_event.wait(timeout=self._poll_interval)

    # ------------------------------------------------------------------
    # Thread: inspector
    # ------------------------------------------------------------------

    def _inspect_loop(self) -> None:
        frame_counter = 0

        while not self._stop_event.is_set():
            triggered = self._trigger_event.wait(timeout=1.0)
            self._trigger_event.clear()

            if not triggered or self._stop_event.is_set():
                continue

            with self._lock:
                if self._state != ScannerState.RUNNING:
                    continue
                model = self._io.scanner_config(self._id)["model"]

            frame = self._camera.get_frame()
            if frame is None:
                logger.warning(f"[{self._id}] sin frame disponible")
                self._transition(ScannerState.ERROR)
                continue

            frame_counter += 1
            frame_id = f"{self._id}_{datetime.now().strftime('%H%M%S')}_{frame_counter:04d}"

            result = self._inspector.inspect(model, frame, frame_id=frame_id)
            if result is None:
                continue

            fault_triggered = False
            with self._lock:
                self._last_result = result
                self._total_inspections += 1
                if result.status == "NOK":
                    self._nok_streak += 1
                    self._nok_count  += 1
                else:
                    self._nok_streak  = 0
                    self._ok_count   += 1
                streak = self._nok_streak
                if streak > self._max_nok_streak:
                    self._max_nok_streak = streak
                if streak >= self._consecutive_nok and self._state == ScannerState.RUNNING:
                    self._state   = ScannerState.FAULT
                    fault_triggered = True

            if fault_triggered:
                logger.warning(f"[{self._id}] FAULT — {streak} NOK consecutivos")
                self._io.write(f"{self._id}.solenoid",  False)
                self._io.write(f"{self._id}.backlight", False)
                self._set_lights(red=True)
                self._fire_state_changed()
            elif streak > 0:
                # Racha NOK activa pero sin alcanzar umbral → amarillo
                self._set_lights(yellow=True)
            else:
                # Streak reseteado → verde
                self._set_lights(green=True)

            if self.on_result:
                try:
                    self.on_result(result, streak)
                except Exception as exc:
                    logger.error(f"[{self._id}] on_result callback error: {exc}")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _set_lights(self, *, blue=False, green=False, yellow=False, red=False) -> None:
        """Escribe los 4 colores de una sola vez. Errores PLC ignorados."""
        for signal, value in (
            (f"{self._id}.light_blue",   blue),
            (f"{self._id}.light_green",  green),
            (f"{self._id}.light_yellow", yellow),
            (f"{self._id}.light_red",    red),
        ):
            self._io.write(signal, value)

    def _start_threads(self) -> None:
        self._poller_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name=f"{self._id}-poller"
        )
        self._inspector_thread = threading.Thread(
            target=self._inspect_loop, daemon=True, name=f"{self._id}-inspector"
        )
        self._poller_thread.start()
        self._inspector_thread.start()

    def _join_threads(self) -> None:
        if self._poller_thread:
            self._poller_thread.join(timeout=1.0)
            self._poller_thread = None
        if self._inspector_thread:
            self._inspector_thread.join(timeout=5.0)
            self._inspector_thread = None

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
