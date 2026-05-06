"""
Captura de cámara USB en hilo de fondo con reconexión automática.

Uso:
    cam = Camera(index=0, max_retries=10, retry_interval_s=3.0)
    cam.start()
    frame = cam.get_frame()   # BGR ndarray o None
    cam.stop()
"""

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES    = 10
_DEFAULT_RETRY_INTERVAL = 3.0   # segundos entre intentos de reconexión


class Camera:
    def __init__(
        self,
        index: int,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_interval_s: float = _DEFAULT_RETRY_INTERVAL,
    ) -> None:
        self._index          = index
        self._max_retries    = max_retries
        self._retry_interval = retry_interval_s

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
        self._cap = cap
        return True

    def _release_capture(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    def _capture_loop(self) -> None:
        fail_count = 0

        while self._running:
            if self._cap is None:
                # Intento de reconexión
                if fail_count >= self._max_retries:
                    logger.error(
                        f"Camera {self._index}: sin imagen tras {fail_count} intentos — abandonando"
                    )
                    self._running = False
                    break

                logger.warning(
                    f"Camera {self._index}: reconectando (intento {fail_count + 1}/{self._max_retries})…"
                )
                # Espera entre intentos; respeta stop() comprobando _running
                deadline = time.monotonic() + self._retry_interval
                while self._running and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not self._running:
                    break

                if self._open_capture():
                    logger.info(f"Camera {self._index}: reconexión exitosa")
                    fail_count = 0
                else:
                    fail_count += 1
                continue

            ok, frame = self._cap.read()
            if not ok:
                logger.warning(f"Camera {self._index}: fallo de lectura — intentando reconectar")
                self._release_capture()
                fail_count += 1
                continue

            fail_count = 0
            with self._lock:
                self._frame = frame
