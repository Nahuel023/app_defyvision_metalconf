"""
Captura de camara USB/IP en hilo de fondo con reconexion automatica indefinida.

Uso:
    cam = Camera(index=0, retry_interval_s=3.0)
    cam.start()
    frame = cam.get_frame()   # BGR ndarray o None si esta reconectando
    cam.stop()
"""

import base64
import logging
import threading
import time
from collections import deque
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_RETRY_INTERVAL = 3.0

# Mapping from settings-dict key -> OpenCV CAP_PROP constant.
# Boolean auto properties are handled explicitly because their numeric encoding
# differs per backend (DirectShow uses 1/3 for manual/auto exposure).
_PROP_MAP: dict[str, int] = {
    "focus": cv2.CAP_PROP_FOCUS,
    "exposure": cv2.CAP_PROP_EXPOSURE,
    "white_balance": cv2.CAP_PROP_WHITE_BALANCE_BLUE_U,
    "gain": cv2.CAP_PROP_GAIN,
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION,
    "sharpness": cv2.CAP_PROP_SHARPNESS,
    "gamma": cv2.CAP_PROP_GAMMA,
    "backlight_compensation": getattr(cv2, "CAP_PROP_BACKLIGHT", 32),
    "fps": cv2.CAP_PROP_FPS,
}

_FOURCC_MJPEG = cv2.VideoWriter.fourcc("M", "J", "P", "G")


class Camera:
    def __init__(
        self,
        index: int | str,
        retry_interval_s: float = _DEFAULT_RETRY_INTERVAL,
        settings: dict | None = None,
    ) -> None:
        self._index = index
        self._retry_max = max(0.5, retry_interval_s)
        self._settings: dict = settings or {}

        self._cap: Optional[cv2.VideoCapture] = None
        self._mjpeg_response = None
        self._mjpeg_buffer = b""
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_times: deque = deque(maxlen=60)
        self._frame_validator = None
        self._snapshot_ok: bool = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"camera-{self._source_label()}",
        )
        self._thread.start()
        logger.info("Camera %s: iniciando en background...", self._source_label())
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._release_capture()
        with self._lock:
            self._frame = None
        logger.info("Camera %s: detenida", self._source_label())

    # ------------------------------------------------------------------
    # Acceso al frame
    # ------------------------------------------------------------------

    def get_frame(self) -> Optional[np.ndarray]:
        """Devuelve una copia del ultimo frame capturado, o None si no hay."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def index(self) -> int | str:
        return self._index

    @property
    def fps(self) -> float:
        """FPS real medido sobre los ultimos frames capturados."""
        times = list(self._frame_times)
        if len(times) < 2:
            return 0.0
        elapsed = times[-1] - times[0]
        return (len(times) - 1) / elapsed if elapsed > 0 else 0.0

    # ------------------------------------------------------------------
    # Ajuste en vivo de parametros
    # ------------------------------------------------------------------

    def set_frame_validator(self, validator) -> None:
        """Set a callable(frame) -> bool called after reconnect to reject bleed."""
        self._frame_validator = validator

    @property
    def is_connected(self) -> bool:
        """True when the capture source is open and delivering frames."""
        with self._lock:
            if self._is_snapshot_source():
                return self._snapshot_ok and self._frame is not None
            opened = self._cap is not None or self._mjpeg_response is not None
            return opened and self._frame is not None

    def apply_setting(self, name: str, value: float) -> bool:
        """Apply a single camera property live. Returns True if accepted."""
        with self._lock:
            cap = self._cap
        if cap is None:
            return False
        try:
            return self._set_prop(cap, name, value)
        except Exception as exc:
            logger.debug("Camera %s: apply_setting %s=%s: %s",
                         self._source_label(), name, value, exc)
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
        if self._is_mjpeg_http_source():
            return self._open_mjpeg_capture()
        return self._open_opencv_capture()

    def _open_opencv_capture(self) -> bool:
        t0 = time.monotonic()
        if isinstance(self._index, int):
            cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
            backend = "DSHOW"
        else:
            cap = cv2.VideoCapture(self._index)
            backend = "AUTO"

        if not cap.isOpened():
            logger.error(
                "Camera %s: no se pudo abrir (%s, %.1fs)",
                self._source_label(), backend, time.monotonic() - t0,
            )
            cap.release()
            return False

        if isinstance(self._index, int):
            width = int(self._settings.get("width", 1920))
            height = int(self._settings.get("height", 1080))
            fps = int(self._settings.get("fps", 5))

            # DSHOW format negotiation: dimensions first so the driver resolves
            # the available format list, then MJPEG, then FPS.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FOURCC, _FOURCC_MJPEG)
            cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        raw_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join(chr((raw_fourcc >> 8 * i) & 0xFF) for i in range(4))
        logger.info(
            "Camera %s: %sx%s @ %.0ffps codec=%s (backend=%s, open=%.1fs)",
            self._source_label(), actual_w, actual_h, actual_fps,
            fourcc_str, backend, time.monotonic() - t0,
        )

        self._apply_settings(cap)

        test_frame = None
        for _ in range(2):
            ok, f = cap.read()
            if ok:
                test_frame = f

        if self._frame_validator is not None and test_frame is not None:
            if not self._frame_validator(test_frame):
                logger.warning("Camera %s: frame duplicado detectado - liberando",
                               self._source_label())
                cap.release()
                return False

        self._cap = cap
        return True

    def _open_mjpeg_capture(self) -> bool:
        t0 = time.monotonic()
        url = str(self._index)
        headers = self._build_auth_headers()

        try:
            request = Request(url, headers=headers)
            response = urlopen(
                request,
                timeout=float(self._settings.get("open_timeout_s", 10.0)),
            )
        except HTTPError as exc:
            if exc.code == 401:
                logger.error(
                    "Camera %s: HTTP 401 Unauthorized. Configurar username/password "
                    "o habilitar stream anonimo en la camara.",
                    self._source_label(),
                )
            else:
                logger.error("Camera %s: error HTTP %s",
                             self._source_label(), exc.code)
            return False
        except Exception as exc:
            logger.error("Camera %s: no se pudo abrir MJPEG HTTP: %s",
                         self._source_label(), exc)
            return False

        self._mjpeg_response = response
        self._mjpeg_buffer = b""

        ok, test_frame = self._read_mjpeg_frame()
        if not ok or test_frame is None:
            logger.error("Camera %s: MJPEG abierto pero sin frames JPEG validos",
                         self._source_label())
            self._release_capture()
            return False

        if self._frame_validator is not None and not self._frame_validator(test_frame):
            logger.warning("Camera %s: frame duplicado detectado - liberando",
                           self._source_label())
            self._release_capture()
            return False

        with self._lock:
            self._frame = test_frame
        self._frame_times.append(time.monotonic())
        logger.info(
            "Camera %s: MJPEG HTTP %sx%s abierto en %.1fs",
            self._source_label(), test_frame.shape[1], test_frame.shape[0],
            time.monotonic() - t0,
        )
        return True

    def _read_mjpeg_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        response = self._mjpeg_response
        if response is None:
            return False, None

        while self._running:
            start = self._mjpeg_buffer.find(b"\xff\xd8")
            end = self._mjpeg_buffer.find(b"\xff\xd9", start + 2) if start != -1 else -1
            if start != -1 and end != -1:
                jpg = self._mjpeg_buffer[start:end + 2]
                self._mjpeg_buffer = self._mjpeg_buffer[end + 2:]
                arr = np.frombuffer(jpg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame

            chunk = response.read(4096)
            if not chunk:
                return False, None
            self._mjpeg_buffer += chunk
            if len(self._mjpeg_buffer) > 4_000_000:
                self._mjpeg_buffer = self._mjpeg_buffer[-1_000_000:]

        return False, None

    def _apply_settings(self, cap: cv2.VideoCapture) -> None:
        s = self._settings
        if not s:
            return

        if "autofocus" in s:
            self._set_prop(cap, "autofocus", 1.0 if s["autofocus"] else 0.0)
        if not s.get("autofocus", True) and "focus" in s:
            self._set_prop(cap, "focus", float(s["focus"]))

        if "auto_exposure" in s:
            self._set_prop(cap, "auto_exposure", 1.0 if s["auto_exposure"] else 0.0)
        if not s.get("auto_exposure", True) and "exposure" in s:
            self._set_prop(cap, "exposure", float(s["exposure"]))

        if "auto_white_balance" in s:
            self._set_prop(cap, "auto_white_balance",
                           1.0 if s["auto_white_balance"] else 0.0)
        if not s.get("auto_white_balance", True) and "white_balance" in s:
            self._set_prop(cap, "white_balance", float(s["white_balance"]))

        for name in ("gain", "brightness", "contrast", "saturation",
                     "sharpness", "gamma", "backlight_compensation"):
            if name in s:
                self._set_prop(cap, name, float(s[name]))

        logger.info("Camera %s: settings aplicados", self._source_label())

    @staticmethod
    def _set_prop(cap: cv2.VideoCapture, name: str, value: float) -> bool:
        """Map a settings key to a CAP_PROP and call cap.set()."""
        if name == "autofocus":
            return bool(cap.set(cv2.CAP_PROP_AUTOFOCUS, value))
        if name == "auto_exposure":
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
        if self._mjpeg_response is not None:
            try:
                self._mjpeg_response.close()
            except Exception:
                pass
            self._mjpeg_response = None
        self._mjpeg_buffer = b""
        self._snapshot_ok = False

    def _is_snapshot_source(self) -> bool:
        if not isinstance(self._index, str):
            return False
        path = self._index.lower().split("?", 1)[0]
        return path.endswith(".jpg") or path.endswith(".jpeg")

    def _is_mjpeg_http_source(self) -> bool:
        if not isinstance(self._index, str):
            return False
        src = self._index.lower()
        return (src.startswith("http://") or src.startswith("https://")) \
               and not self._is_snapshot_source()

    def _build_auth_headers(self) -> dict:
        headers: dict = {}
        username = self._settings.get("username")
        password = self._settings.get("password")
        if username and password:
            token = base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _snapshot_loop(self) -> None:
        import http.client
        import queue
        from urllib.parse import urlparse

        fps_target    = max(0.5, float(self._settings.get("fps", 25)))
        interval      = 1.0 / fps_target
        timeout       = float(self._settings.get("open_timeout_s", 10.0))
        stale_timeout = float(self._settings.get("stale_frame_timeout_s", 3.0))
        auth_headers  = self._build_auth_headers()

        parsed   = urlparse(str(self._index))
        host     = parsed.netloc or parsed.hostname or ""
        req_path = parsed.path or "/"
        if parsed.query:
            req_path += "?" + parsed.query

        # Decode worker: recibe bytes crudos y decodifica en paralelo al siguiente GET.
        # maxsize=2 descarta frames viejos si el worker no da abasto (prefiere frescura).
        _raw_q: queue.Queue = queue.Queue(maxsize=2)

        _first_frame_logged = False

        def _decode_worker() -> None:
            nonlocal _first_frame_logged
            while True:
                item = _raw_q.get()
                if item is None:
                    break
                data, t_req = item
                arr   = np.frombuffer(data, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                if self._frame_validator is not None and not self._frame_validator(frame):
                    logger.warning("Camera %s: frame duplicado detectado",
                                   self._source_label())
                    continue
                if not _first_frame_logged:
                    logger.info(
                        "Camera %s: primer snapshot recibido %dx%d",
                        self._source_label(), frame.shape[1], frame.shape[0],
                    )
                    _first_frame_logged = True
                with self._lock:
                    self._frame       = frame
                    self._snapshot_ok = True
                self._frame_times.append(time.monotonic())

        dec_thread = threading.Thread(
            target=_decode_worker,
            daemon=True,
            name=f"cam-dec-{self._source_label()}",
        )
        dec_thread.start()

        conn:      http.client.HTTPConnection | None = None
        fail_count = 0
        last_ok_t  = 0.0

        logger.info(
            "Camera %s: modo snapshot keep-alive @ %.1f fps (decode async)",
            self._source_label(), fps_target,
        )

        try:
            while self._running:
                t0 = time.monotonic()
                try:
                    if conn is None:
                        conn = http.client.HTTPConnection(host, timeout=timeout)

                    conn.request("GET", req_path, headers=auth_headers)
                    resp = conn.getresponse()

                    if resp.status == 401:
                        logger.error(
                            "Camera %s: HTTP 401 — verificar username/password en camera.yaml",
                            self._source_label(),
                        )
                        resp.read()
                        conn.close()
                        conn = None
                        self._sleep_interruptible(5.0)
                        continue

                    if resp.status != 200:
                        raise IOError(f"HTTP {resp.status}")

                    data = resp.read()

                    # Encolar para decode asíncrono; si la cola está llena descartar
                    # el frame más viejo para mantener baja la latencia.
                    try:
                        _raw_q.put_nowait((data, t0))
                    except queue.Full:
                        try:
                            _raw_q.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            _raw_q.put_nowait((data, t0))
                        except queue.Full:
                            pass

                    last_ok_t  = t0
                    fail_count = 0

                except Exception as exc:
                    fail_count += 1
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None

                    now   = time.monotonic()
                    stale = now - last_ok_t > stale_timeout
                    with self._lock:
                        self._snapshot_ok = False
                        if stale:
                            self._frame = None

                    logger.warning(
                        "Camera %s: error #%d%s: %s",
                        self._source_label(), fail_count,
                        " (frame retenido)" if not stale else "",
                        exc,
                    )
                    wait = min(self._retry_max, 0.1 * (2 ** min(fail_count - 1, 4)))
                    self._sleep_interruptible(wait)
                    continue

                elapsed   = time.monotonic() - t0
                sleep_for = max(0.0, interval - elapsed)
                if sleep_for > 0.002:
                    self._sleep_interruptible(sleep_for)
        finally:
            _raw_q.put(None)
            dec_thread.join(timeout=1.0)

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep en chunks de 5ms para responder rápido a stop() y mantener timing preciso."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.005)

    def _source_label(self) -> str:
        if isinstance(self._index, str):
            if "://" in self._index:
                scheme, rest = self._index.split("://", 1)
                host_path = rest.split("@", 1)[-1]
                return f"{scheme}://{host_path}"
            return self._index
        return str(self._index)

    def _capture_loop(self) -> None:
        if self._is_snapshot_source():
            self._snapshot_loop()
            return

        retry_wait = 0.0
        first_open = True

        while self._running:
            if self._cap is None and self._mjpeg_response is None:
                if retry_wait > 0:
                    deadline = time.monotonic() + retry_wait
                    while self._running and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if not self._running:
                        break

                if self._open_capture():
                    if first_open:
                        logger.info("Camera %s: iniciada", self._source_label())
                        first_open = False
                    else:
                        logger.info("Camera %s: reconectada", self._source_label())
                    retry_wait = 0.0
                else:
                    retry_wait = min(
                        self._retry_max,
                        0.5 if retry_wait == 0 else retry_wait * 2,
                    )
                    logger.debug(
                        "Camera %s: no disponible, reintentando en %.1fs...",
                        self._source_label(), retry_wait,
                    )
                continue

            if self._mjpeg_response is not None:
                ok, frame = self._read_mjpeg_frame()
            else:
                ok, frame = self._cap.read()

            if not ok:
                logger.warning("Camera %s: perdida de senal - reconectando...",
                               self._source_label())
                self._release_capture()
                with self._lock:
                    self._frame = None
                retry_wait = 0.0
                continue

            with self._lock:
                self._frame = frame
            self._frame_times.append(time.monotonic())
