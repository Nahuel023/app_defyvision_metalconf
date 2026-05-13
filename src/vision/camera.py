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
from collections import deque
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
    "fps":                    cv2.CAP_PROP_FPS,
}

_FOURCC_MJPEG = cv2.VideoWriter.fourcc('M', 'J', 'P', 'G')


class Camera:
    def __init__(
        self,
        index: int,
        retry_interval_s: float = _DEFAULT_RETRY_INTERVAL,
        settings: dict | None = None,
    ) -> None:
        self._index          = index
        self._retry_max      = max(0.5, retry_interval_s)  # caps backoff ceiling
        self._settings: dict = settings or {}

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # FPS real: timestamps de los últimos 60 frames capturados exitosamente
        self._frame_times: deque = deque(maxlen=60)
        # Optional callable(frame) -> bool: called after open to reject bleed
        self._frame_validator = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True
        self._running = True
        opened = self._open_capture()   # attempt immediate open
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"camera-{self._index}",
        )
        self._thread.start()
        if opened:
            logger.info(f"Camera {self._index}: iniciada")
        else:
            logger.warning(f"Camera {self._index}: no disponible al inicio, reintentando en background")
        return opened

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

    @property
    def fps(self) -> float:
        """FPS real medido sobre los últimos frames capturados."""
        times = list(self._frame_times)
        if len(times) < 2:
            return 0.0
        elapsed = times[-1] - times[0]
        return (len(times) - 1) / elapsed if elapsed > 0 else 0.0

    # ------------------------------------------------------------------
    # Ajuste en vivo de parámetros
    # ------------------------------------------------------------------

    def set_frame_validator(self, validator) -> None:
        """Set a callable(frame) -> bool called after reconnect to reject bleed."""
        self._frame_validator = validator

    @property
    def is_connected(self) -> bool:
        """True when the capture device is open and delivering frames."""
        with self._lock:
            return self._cap is not None and self._frame is not None

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

        width  = int(self._settings.get("width",  1920))
        height = int(self._settings.get("height", 1080))
        fps    = int(self._settings.get("fps",    5))

        # MJPEG must be requested before width/height/fps so DSHOW negotiates
        # the right format. Without it the C920 falls back to YUY2 which caps
        # at 5fps at 1920×1080 due to USB 2.0 bandwidth.
        cap.set(cv2.CAP_PROP_FOURCC, _FOURCC_MJPEG)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS,          fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)

        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        actual_w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(
            f"Camera {self._index}: {actual_w}x{actual_h} @ {actual_fps:.0f}fps "
            f"(requested {width}x{height} @ {fps}fps)"
        )

        self._apply_settings(cap)

        # Warm-up: drain 3 frames so the driver stabilises before validation.
        test_frame = None
        for _ in range(3):
            ok, f = cap.read()
            if ok:
                test_frame = f

        # Bleed guard: reject if the frame is identical to another camera's feed.
        if self._frame_validator is not None and test_frame is not None:
            if not self._frame_validator(test_frame):
                logger.warning(
                    f"Camera {self._index}: frame duplicado detectado "
                    f"(bleed DSHOW) — liberando"
                )
                cap.release()
                return False

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
        # retry_wait=0 → intento inmediato; sube exponencialmente hasta _retry_max
        retry_wait = 0.0

        while self._running:

            # ── sin captura activa: esperar y reconectar ───────────
            if self._cap is None:
                if retry_wait > 0:
                    deadline = time.monotonic() + retry_wait
                    while self._running and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if not self._running:
                        break

                if self._open_capture():
                    logger.info(f"Camera {self._index}: reconectada")
                    retry_wait = 0.0   # reset backoff on success
                else:
                    # exponential backoff: 0.5 → 1 → 2 → 4 → max
                    retry_wait = min(self._retry_max, 0.5 if retry_wait == 0 else retry_wait * 2)
                    logger.debug(
                        f"Camera {self._index}: no disponible, "
                        f"reintentando en {retry_wait:.1f}s…"
                    )
                continue

            # ── captura normal ─────────────────────────────────────
            ok, frame = self._cap.read()
            if not ok:
                logger.warning(f"Camera {self._index}: pérdida de señal — reconectando…")
                self._release_capture()
                with self._lock:
                    self._frame = None   # evitar frame congelado en la UI
                retry_wait = 0.0         # primer reintento inmediato
                continue

            with self._lock:
                self._frame = frame
            self._frame_times.append(time.monotonic())
