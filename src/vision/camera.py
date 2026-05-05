"""
Captura de cámara USB en hilo de fondo.

Uso:
    cam = Camera(index=0)
    cam.start()
    frame = cam.get_frame()   # BGR ndarray o None
    cam.stop()
"""

import logging
import threading
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class Camera:
    def __init__(self, index: int) -> None:
        self._index = index
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True
        self._cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            logger.error(f"Camera {self._index}: no se pudo abrir")
            return False

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # buffer mínimo → frames frescos

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
        if self._cap:
            self._cap.release()
            self._cap = None
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
    # Loop interno
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                logger.warning(f"Camera {self._index}: fallo de lectura de frame")
                self._running = False
                break
            with self._lock:
                self._frame = frame
