"""
Captura de camara USB/IP en hilo de fondo con reconexion automatica indefinida.

Uso:
    cam = Camera(index=0, retry_interval_s=3.0)
    cam.start()
    frame = cam.get_frame()   # BGR ndarray o None si esta reconectando
    cam.stop()
"""

import base64
import http.client
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
#
# IMPORTANTE: usar siempre getattr(cv2, NOMBRE, valor_numerico_estandar) aqui.
# Algunos builds de OpenCV empaquetados (PyInstaller, headless, etc.) no
# exponen todas las constantes CAP_PROP_* como atributo del modulo. Como este
# diccionario se evalua al importar el archivo, un solo atributo faltante
# lanzaba AttributeError en el import y tumbaba toda la aplicacion antes de
# llegar a abrir la UI (ver incidente 2026-07-22: no arrancaba en produccion
# por CAP_PROP_FOCUS ausente). Los valores numericos de respaldo son los
# codigos estables de la enum cv2.VideoCaptureProperties.
_PROP_MAP: dict[str, int] = {
    "focus": getattr(cv2, "CAP_PROP_FOCUS", 28),
    "exposure": getattr(cv2, "CAP_PROP_EXPOSURE", 15),
    "white_balance": getattr(cv2, "CAP_PROP_WHITE_BALANCE_BLUE_U", 17),
    "gain": getattr(cv2, "CAP_PROP_GAIN", 14),
    "brightness": getattr(cv2, "CAP_PROP_BRIGHTNESS", 10),
    "contrast": getattr(cv2, "CAP_PROP_CONTRAST", 11),
    "saturation": getattr(cv2, "CAP_PROP_SATURATION", 12),
    "sharpness": getattr(cv2, "CAP_PROP_SHARPNESS", 20),
    "gamma": getattr(cv2, "CAP_PROP_GAMMA", 22),
    "backlight_compensation": getattr(cv2, "CAP_PROP_BACKLIGHT", 32),
    "fps": getattr(cv2, "CAP_PROP_FPS", 5),
}

_FOURCC_MJPEG = cv2.VideoWriter.fourcc("M", "J", "P", "G")

# Constantes CAP_PROP_* usadas fuera de _PROP_MAP: mismo criterio de robustez
# (getattr con valor numerico estandar de respaldo), para que nunca dependan
# de que el build de cv2 empaquetado las exponga por nombre.
_CAP_PROP_AUTOFOCUS = getattr(cv2, "CAP_PROP_AUTOFOCUS", 39)
_CAP_PROP_AUTO_EXPOSURE = getattr(cv2, "CAP_PROP_AUTO_EXPOSURE", 21)
_CAP_PROP_AUTO_WB = getattr(cv2, "CAP_PROP_AUTO_WB", 44)
_CAP_PROP_FRAME_WIDTH = getattr(cv2, "CAP_PROP_FRAME_WIDTH", 3)
_CAP_PROP_FRAME_HEIGHT = getattr(cv2, "CAP_PROP_FRAME_HEIGHT", 4)
_CAP_PROP_FOURCC = getattr(cv2, "CAP_PROP_FOURCC", 6)
_CAP_PROP_FPS = getattr(cv2, "CAP_PROP_FPS", 5)
_CAP_PROP_BUFFERSIZE = getattr(cv2, "CAP_PROP_BUFFERSIZE", 38)


def apply_digital_zoom(frame: np.ndarray, zoom_pct, pan_x=0, pan_y=0) -> np.ndarray:
    """Zoom + pan digital: recorta el frame (centro desplazado por pan_x/pan_y)
    y reescala al tamano original. zoom<=100 (%) devuelve el frame sin tocar;
    zoom=200 duplica el acercamiento. pan_x/pan_y en -100..100 mueven el recorte
    dentro del margen disponible (0 = centrado, +100 = extremo derecho/abajo).

    Funcion pura y reutilizable: la usan tanto Camera.get_frame() (inspeccion)
    como las vistas en vivo de modo servicio, para que lo que se ve/graba coincida
    exactamente con lo que analiza produccion."""
    if frame is None:
        return frame
    zoom_pct = float(zoom_pct or 100)
    if zoom_pct <= 100.0:
        return frame
    ratio = zoom_pct / 100.0
    h, w = frame.shape[:2]
    crop_w = max(1, int(round(w / ratio)))
    crop_h = max(1, int(round(h / ratio)))
    max_x = w - crop_w
    max_y = h - crop_h
    pan_x = max(-100.0, min(100.0, float(pan_x or 0)))
    pan_y = max(-100.0, min(100.0, float(pan_y or 0)))
    x0 = int(round((max_x / 2) * (1 + pan_x / 100.0)))
    y0 = int(round((max_y / 2) * (1 + pan_y / 100.0)))
    x0 = max(0, min(max_x, x0))
    y0 = max(0, min(max_y, y0))
    cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]
    return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)


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
        self._lifecycle_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_times: deque = deque(maxlen=60)
        self._frame_validator = None
        self._snapshot_ok: bool = False
        self._snapshot_conn: http.client.HTTPConnection | None = None

        # Watchdog de IMAGEN CONGELADA. Sin esto, cualquier situacion que deje de
        # refrescar self._frame pero NO lo ponga en None (validador rechazando
        # todos los frames, camara IP devolviendo siempre el mismo JPEG cacheado)
        # hace que get_frame() siga entregando la MISMA imagen para siempre: el
        # scanner queda "en marcha" sin inspeccionar nada, sin OK, sin NOK y sin
        # error. Al vencer el timeout se suelta el frame retenido y el scanner lo
        # trata como perdida de camara (ya escala a ERROR y corta el solenoide).
        self._freeze_timeout_s: float = float(
            self._settings.get("frozen_frame_timeout_s", 10.0)
        )
        self._last_fresh_mono: float = 0.0
        self._last_sig: Optional[np.ndarray] = None
        self._frozen_logged: bool = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._running:
                    return True
                # stop() ya pidio terminar pero el backend sigue bloqueado. No
                # volver a poner _running=True: existe una carrera donde el hilo
                # viejo sale justo despues y start() retorna exito sin captura.
                logger.error(
                    "Camera %s: reinicio bloqueado; el hilo anterior aun no termino",
                    self._source_label(),
                )
                return False
            self._thread = None
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
        with self._lifecycle_lock:
            self._running = False
            thread = self._thread
        self._close_snapshot_connection()
        self._release_capture()
        if thread:
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.error(
                    "Camera %s: el hilo no termino tras stop(); se bloquea un restart duplicado",
                    self._source_label(),
                )
            else:
                with self._lifecycle_lock:
                    if self._thread is thread:
                        self._thread = None
        else:
            with self._lifecycle_lock:
                self._thread = None
        with self._lock:
            self._frame           = None
            self._last_sig        = None
            self._last_fresh_mono = 0.0
            self._frozen_logged   = False
        logger.info("Camera %s: detenida", self._source_label())

    # ------------------------------------------------------------------
    # Acceso al frame
    # ------------------------------------------------------------------

    def get_frame(self) -> Optional[np.ndarray]:
        """Devuelve una copia del ultimo frame capturado (con zoom digital
        aplicado si esta configurado), o None si no hay."""
        with self._lock:
            if self._frame is None:
                return None
            frame = self._frame.copy()
        return self._apply_zoom(frame)

    def get_raw_frame(self) -> Optional[np.ndarray]:
        """Devuelve el ultimo frame sin zoom, para diagnostico/anti-bleed."""
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    # ------------------------------------------------------------------
    # Watchdog de imagen congelada
    # ------------------------------------------------------------------

    def _publish_frame(self, frame: np.ndarray) -> None:
        """Publica un frame aceptado y refresca el watchdog de imagen congelada."""
        # Firma barata (submuestreo 1/16) para saber si la imagen cambio de verdad.
        sig = frame[::16, ::16].copy()
        now = time.monotonic()
        with self._lock:
            changed = self._last_sig is None or not np.array_equal(sig, self._last_sig)
            self._last_sig    = sig
            self._frame       = frame
            self._snapshot_ok = True
            if changed:
                self._last_fresh_mono = now
                self._frozen_logged   = False
        self._frame_times.append(now)
        if not changed:
            self._check_frozen(now)

    def _check_frozen(self, now: float | None = None) -> None:
        """Suelta el frame retenido si hace demasiado que no llega uno NUEVO.

        Se llama tanto al publicar un frame identico al anterior como al
        descartar uno (validador). Al poner _frame=None, get_frame() devuelve
        None y el ScannerController lo escala por su via de camara perdida.
        """
        if self._freeze_timeout_s <= 0:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._frame is None or self._last_fresh_mono <= 0.0:
                return
            elapsed = now - self._last_fresh_mono
            if elapsed < self._freeze_timeout_s:
                return
            self._frame       = None
            self._snapshot_ok = False
            self._last_sig    = None
            already_logged    = self._frozen_logged
            self._frozen_logged = True
        if not already_logged:
            logger.error(
                "Camera %s: IMAGEN CONGELADA — sin imagen nueva hace %.1fs; "
                "se descarta el frame retenido",
                self._source_label(), elapsed,
            )

    def _apply_zoom(self, frame: np.ndarray) -> np.ndarray:
        """Aplica el zoom/pan digital configurado para esta camara."""
        with self._lock:
            zoom = self._settings.get("zoom", 100)
            pan_x = self._settings.get("pan_x", 0)
            pan_y = self._settings.get("pan_y", 0)
        return apply_digital_zoom(
            frame,
            zoom,
            pan_x,
            pan_y,
        )

    def set_zoom(self, value_pct: float) -> None:
        """Ajusta el zoom digital (%) en caliente (se aplica en el proximo get_frame)."""
        with self._lock:
            self._settings["zoom"] = max(100.0, float(value_pct))

    def set_pan(self, pan_x: float, pan_y: float) -> None:
        """Ajusta el desplazamiento del recorte (-100..100 cada eje) en caliente."""
        with self._lock:
            self._settings["pan_x"] = max(-100.0, min(100.0, float(pan_x)))
            self._settings["pan_y"] = max(-100.0, min(100.0, float(pan_y)))

    @property
    def zoom(self) -> float:
        """Zoom digital actual, en porcentaje (100 = sin zoom)."""
        with self._lock:
            return float(self._settings.get("zoom", 100) or 100)

    @property
    def pan(self) -> tuple[float, float]:
        """Desplazamiento actual del recorte (pan_x, pan_y), -100..100."""
        with self._lock:
            return (
                float(self._settings.get("pan_x", 0) or 0),
                float(self._settings.get("pan_y", 0) or 0),
            )

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
                raw = cap.get(_CAP_PROP_AUTO_EXPOSURE)
                return 1.0 if raw >= 2.0 else 0.0
            if name == "autofocus":
                return float(cap.get(_CAP_PROP_AUTOFOCUS))
            if name == "auto_white_balance":
                return float(cap.get(_CAP_PROP_AUTO_WB))
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
            cap.set(_CAP_PROP_FRAME_WIDTH, width)
            cap.set(_CAP_PROP_FRAME_HEIGHT, height)
            cap.set(_CAP_PROP_FOURCC, _FOURCC_MJPEG)
            cap.set(_CAP_PROP_FPS, fps)
        cap.set(_CAP_PROP_BUFFERSIZE, 1)

        actual_fps = cap.get(_CAP_PROP_FPS)
        actual_w = int(cap.get(_CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(_CAP_PROP_FRAME_HEIGHT))
        raw_fourcc = int(cap.get(_CAP_PROP_FOURCC))
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

        self._publish_frame(test_frame)
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
            return bool(cap.set(_CAP_PROP_AUTOFOCUS, value))
        if name == "auto_exposure":
            dshow_val = 3.0 if value >= 0.5 else 1.0
            return bool(cap.set(_CAP_PROP_AUTO_EXPOSURE, dshow_val))
        if name == "auto_white_balance":
            return bool(cap.set(_CAP_PROP_AUTO_WB, value))
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
        self._close_snapshot_connection()

    def _close_snapshot_connection(self) -> None:
        conn = self._snapshot_conn
        self._snapshot_conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

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
                    # Un rechazo tampoco es imagen nueva: si el validador rechaza
                    # todo, el watchdog suelta el frame retenido en vez de dejar
                    # al scanner corriendo sobre una imagen congelada.
                    self._check_frozen()
                    continue
                if not _first_frame_logged:
                    logger.info(
                        "Camera %s: primer snapshot recibido %dx%d",
                        self._source_label(), frame.shape[1], frame.shape[0],
                    )
                    _first_frame_logged = True
                self._publish_frame(frame)

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
                        self._snapshot_conn = conn

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
                        self._snapshot_conn = None
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
                        self._snapshot_conn = None

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
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            self._snapshot_conn = None
            # El queue puede estar lleno si stop() llega con decode atrasado.
            # Liberar un slot evita bloquear para siempre intentando insertar
            # el sentinel durante el cierre.
            try:
                _raw_q.put_nowait(None)
            except queue.Full:
                try:
                    _raw_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    _raw_q.put_nowait(None)
                except queue.Full:
                    pass
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

            self._publish_frame(frame)
