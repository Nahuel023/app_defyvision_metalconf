"""
Captura de cámara USB en hilo de fondo con reconexión automática indefinida.

Uso:
    cam = Camera(index=0, retry_interval_s=3.0)
    cam.start()
    frame = cam.get_frame()   # BGR ndarray o None si está reconectando
    cam.stop()
"""

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_RETRY_INTERVAL = 3.0   # segundos entre intentos de reconexión

# Mapping from settings-dict key → OpenCV CAP_PROP constant.
# Boolean auto properties (autofocus, auto_exposure, auto_white_balance) are
# handled explicitly in _apply_settings / apply_setting because their numeric
# encoding differs per backend (DirectShow uses 1/3 for manual/auto exposure).
_PROP_MAP: dict[str, int] = {
    "focus":                  cv2.CAP_PROP_FOCUS,
    "exposure":               cv2.CAP_PROP_EXPOSURE,
    "white_balance":          cv2.CAP_PROP_WHITE_BALANCE_BLUE_U,
    "gain":                   cv2.CAP_PROP_GAIN,
    "brightness":             cv2.CAP_PROP_BRIGHTNESS,
    "contrast":               cv2.CAP_PROP_CONTRAST,
    "saturation":             cv2.CAP_PROP_SATURATION,
    "sharpness":              cv2.CAP_PROP_SHARPNESS,
    "gamma":                  cv2.CAP_PROP_GAMMA,
    "backlight_compensation": getattr(cv2, "CAP_PROP_BACKLIGHT", 32),
}


class Camera:
    def __init__(
        self,
        index: int,
        retry_interval_s: float = _DEFAULT_RETRY_INTERVAL,
        settings: dict | None = None,
    ) -> None:
        self._index          = index
        self._retry_interval = retry_interval_s
        self._settings: dict = settings or {}

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True
        if not self._open_capture():
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"camera-{self._index}",
        )
        self._thread.start()
        logger.info(f"Camera {self._index}: iniciada")
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._release_capture()
        with self._lock:
            self._frame = None
        logger.info(f"Camera {self._index}: detenida")

    # ------------------------------------------------------------------
    # Acceso al frame
    # ------------------------------------------------------------------

    def get_frame(self) -> Optional[np.ndarray]:
        """Devuelve una copia del último frame capturado, o None si no hay."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def index(self) -> int:
        return self._index

    # ------------------------------------------------------------------
    # Ajuste en vivo de parámetros
    # ------------------------------------------------------------------

    def apply_setting(self, name: str, value: float) -> bool:
        """Apply a single camera property live. Returns True if accepted."""
        with self._lock:
            cap = self._cap
        if cap is None:
            return False
        try:
            return self._set_prop(cap, name, value)
        except Exception as exc:
            logger.debug(f"Camera {self._index}: apply_setting {name}={value}: {exc}")
            return False

    def read_setting(self, name: str) -> float:
        """Read current value from the driver. Returns -1 on failure."""
        with self._lock:
            cap = self._cap
        if cap is None:
            return -1.0
        try:
            if name == "auto_exposure":
                raw = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
                return 1.0 if raw >= 2.0 else 0.0
            if name == "autofocus":
                return float(cap.get(cv2.CAP_PROP_AUTOFOCUS))
            if name == "auto_white_balance":
                prop = getattr(cv2, "CAP_PROP_AUTO_WB", 44)
                return float(cap.get(prop))
            prop = _PROP_MAP.get(name)
            if prop is None:
                return -1.0
            return float(cap.get(prop))
        except Exception:
            return -1.0

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _open_capture(self) -> bool:
        cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.error(f"Camera {self._index}: no se pudo abrir")
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)
        self._apply_settings(cap)
        self._cap = cap
        return True

    def _apply_settings(self, cap: cv2.VideoCapture) -> None:
        s = self._settings
        if not s:
            return

        # --- Autofocus ---
        if "autofocus" in s:
            self._set_prop(cap, "autofocus", 1.0 if s["autofocus"] else 0.0)
        if not s.get("autofocus", True) and "focus" in s:
            self._set_prop(cap, "focus", float(s["focus"]))

        # --- Exposure ---
        if "auto_exposure" in s:
            self._set_prop(cap, "auto_exposure", 1.0 if s["auto_exposure"] else 0.0)
        if not s.get("auto_exposure", True) and "exposure" in s:
            self._set_prop(cap, "exposure", float(s["exposure"]))

        # --- White balance ---
        if "auto_white_balance" in s:
            self._set_prop(cap, "auto_white_balance",
                           1.0 if s["auto_white_balance"] else 0.0)
        if not s.get("auto_white_balance", True) and "white_balance" in s:
            self._set_prop(cap, "white_balance", float(s["white_balance"]))

        # --- Numeric properties ---
        for name in ("gain", "brightness", "contrast", "saturation",
                     "sharpness", "gamma", "backlight_compensation"):
            if name in s:
                self._set_prop(cap, name, float(s[name]))

        logger.info(f"Camera {self._index}: settings aplicados")

    @staticmethod
    def _set_prop(cap: cv2.VideoCapture, name: str, value: float) -> bool:
        """Map a settings key to a CAP_PROP and call cap.set()."""
        if name == "autofocus":
            return bool(cap.set(cv2.CAP_PROP_AUTOFOCUS, value))
        if name == "auto_exposure":
            # DirectShow: 3.0 = auto, 1.0 = manual
            dshow_val = 3.0 if value >= 0.5 else 1.0
            return bool(cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, dshow_val))
        if name == "auto_white_balance":
            prop = getattr(cv2, "CAP_PROP_AUTO_WB", 44)
            return bool(cap.set(prop, value))
        prop = _PROP_MAP.get(name)
        if prop is None:
            return False
        return bool(cap.set(prop, value))

    def _release_capture(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    def _capture_loop(self) -> None:
        while self._running:

            # ── sin captura activa: esperar y reconectar ───────────
            if self._cap is None:
                deadline = time.monotonic() + self._retry_interval
                while self._running and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not self._running:
                    break
                if self._open_capture():
                    logger.info(f"Camera {self._index}: reconectada")
                else:
                    logger.debug(f"Camera {self._index}: dispositivo no disponible, reintentando…")
                continue

            # ── captura normal ─────────────────────────────────────
            ok, frame = self._cap.read()
            if not ok:
                logger.warning(
                    f"Camera {self._index}: pérdida de señal — "
                    f"reconectando en {self._retry_interval:.0f}s…"
                )
                self._release_capture()
                with self._lock:
                    self._frame = None   # evitar frame congelado en la UI
                continue

            with self._lock:
                self._frame = frame
