"""
Punto de entrada del sistema de producción.

Crea y supervisa un ScannerController y Camera por cada scanner
definido en config/io_map.yaml. La conexión Modbus es compartida.
"""

import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from src.plc.client import PLCClient
from src.plc.io_map import IOMap
from src.controller.scanner_controller import ScannerController
from src.metrics.recorder import MetricsRecorder
from src.utils.camera_config import load_camera_settings
from src.vision.camera import Camera

logger = logging.getLogger(__name__)

_APP_CONFIG_PATH = Path("config/app.yaml")


class InspectionSystem:
    def __init__(self, io_map_path: Path = Path("config/io_map.yaml"),
                 disable_plc_outputs: bool = False) -> None:
        plc_cfg = self._load_plc_config(io_map_path)
        cam_cfg = self._load_camera_config()

        self._client = PLCClient(
            ip=plc_cfg["ip"],
            port=plc_cfg.get("port", 502),
            unit_id=plc_cfg.get("unit_id", 1),
        )
        self._io = IOMap(self._client, io_map_path,
                         disable_outputs=disable_plc_outputs)
        self._cameras: dict[str, Camera] = {}
        self._scanners: dict[str, ScannerController] = {}
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False

        for scanner_id in self._io.scanner_ids():
            cfg = self._io.scanner_config(scanner_id)
            camera_source = self._camera_source_for_scanner(cfg)
            cam_settings = load_camera_settings(scanner_id)
            camera = Camera(
                camera_source,
                retry_interval_s=cam_cfg.get("retry_interval_s", 1.0),
                settings=cam_settings,
            )
            scanner = ScannerController(scanner_id, self._io, camera)
            self._cameras[scanner_id] = camera
            self._scanners[scanner_id] = scanner

        # Anti-bleed: each camera rejects frames identical to another camera's feed.
        # This prevents DSHOW index drift from making two scanners show the same image.
        # SOLO aplica a camaras USB locales (indice DSHOW). Ver _is_local_device().
        _local_ids = [
            sid for sid, cam in self._cameras.items()
            if self._is_local_device(cam.index)
        ]
        if len(_local_ids) > 1:
            for scanner_id in _local_ids:
                self._cameras[scanner_id].set_frame_validator(
                    self._make_bleed_validator(scanner_id, _local_ids)
                )

        self._recorder = MetricsRecorder()
        self._recorder.start(self)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def connect_plc(self) -> bool:
        ok = self._client.connect()
        if not ok:
            logger.error("No se pudo conectar al PLC")
            return ok
        for scanner in self._scanners.values():
            scanner.initialize_lights()
        return ok

    def start_cameras(self) -> dict[str, bool]:
        """Arranca todas las cámaras en background. Devuelve {scanner_id: True}."""
        return {sid: cam.start() for sid, cam in self._cameras.items()}

    def shutdown(self) -> None:
        """Detiene todos los scanners, cámaras, recorder y cierra el PLC."""
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True

        try:
            self._recorder.stop()
        except Exception as exc:
            logger.error("Error deteniendo MetricsRecorder: %s", exc)
        for scanner in self._scanners.values():
            try:
                scanner.shutdown()
            except Exception as exc:
                logger.error("Error deteniendo scanner: %s", exc)
        # Apagar todas las salidas PLC incondicionalmente (por si el scanner
        # quedó en IDLE/MANUAL y no apagó las luces en stop())
        for sid in self._scanners:
            try:
                # El solenoide es critico: insistir y verificar antes de desconectar.
                self._io.write_critical(
                    f"{sid}.solenoid", False, retries=5, retry_delay_s=0.15, verify=True
                )
                self._io.write_batch([
                    (f"{sid}.backlight", False),
                    (f"{sid}.light_blue", False),
                    (f"{sid}.light_green", False),
                    (f"{sid}.light_yellow", False),
                    (f"{sid}.light_red", False),
                ])
            except Exception as exc:
                logger.error("[%s] error apagando salidas durante shutdown: %s", sid, exc)
        for camera in self._cameras.values():
            try:
                camera.stop()
            except Exception as exc:
                logger.error("Error deteniendo camara: %s", exc)
        try:
            self._client.disconnect()
        except Exception as exc:
            logger.error("Error desconectando PLC: %s", exc)
        logger.info("Sistema detenido")

    # ------------------------------------------------------------------
    # Acceso a componentes
    # ------------------------------------------------------------------

    def scanner(self, scanner_id: str) -> ScannerController:
        return self._scanners[scanner_id]

    def camera(self, scanner_id: str) -> Camera:
        return self._cameras[scanner_id]

    def scanner_ids(self) -> list[str]:
        return list(self._scanners.keys())

    @property
    def io(self) -> IOMap:
        return self._io

    @property
    def plc(self) -> PLCClient:
        return self._client

    @property
    def metrics(self) -> MetricsRecorder:
        return self._recorder

    def safe_mode(self, scanner_id: str) -> bool:
        """Modo seguro de UN scanner puntual — cada uno tiene su propia maneta."""
        return self._io.get_safe_mode(scanner_id)

    def set_safe_mode(self, scanner_id: str, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._io.get_safe_mode(scanner_id) == enabled:
            return
        self._io.set_safe_mode(scanner_id, enabled)
        logger.warning(
            "[%s] Modo seguro %s", scanner_id, "ACTIVADO" if enabled else "DESACTIVADO"
        )
        self._scanners[scanner_id].sync_solenoid(safe_mode_off=not enabled)

    # ------------------------------------------------------------------

    @staticmethod
    def _is_local_device(source) -> bool:
        """True si la fuente es una camara USB local (indice DSHOW), no una IP cam.

        El anti-bleed existe unicamente por el re-enumerado de indices de DSHOW.
        Una camara de red se identifica por URL: dos URLs distintas NUNCA pueden
        ser el mismo dispositivo, asi que compararlas por imagen no aporta nada
        y solo genera falsos positivos (ver docstring de _make_bleed_validator).
        """
        if isinstance(source, int):
            return True
        src = str(source).strip().lower()
        if "://" in src:
            return False
        return src.isdigit()

    def _make_bleed_validator(self, scanner_id: str, peer_ids: list[str]) -> Callable:
        """Return a frame validator that rejects frames identical to any other camera.

        DSHOW can re-enumerate device indices when a camera disconnects, causing
        a reconnecting camera to open the still-connected sibling device instead
        of its own. The validator detects this by comparing a sample of pixels.

        SOLO se instala entre camaras USB locales. Con camaras IP (una URL por
        scanner) este chequeo era un bug critico: los dos scanners miran la MISMA
        chapa microperforada, el parche central sale casi identico (medido 1.7-7.4px
        en produccion, umbral 8.0) y la camara quedaba rechazando TODOS sus frames.
        El resultado era un scanner "en marcha" para siempre con la ultima imagen
        congelada: 0 OK, 0 NOK y ningun error.
        """
        cameras = self._cameras  # capture reference, not copy

        def validator(frame: np.ndarray) -> bool:
            for sid in peer_ids:
                if sid == scanner_id:
                    continue
                cam = cameras[sid]
                # Comparar ambos feeds CRUDOS. get_frame() aplica zoom/pan y
                # podia ocultar que dos indices DSHOW apuntaban a la misma camara.
                other = cam.get_raw_frame()
                if other is None or other.shape != frame.shape:
                    continue
                # Sample the center 200×200 patch for speed (avoid full-frame diff)
                cy, cx = frame.shape[0] // 2, frame.shape[1] // 2
                patch_a = frame [cy-100:cy+100, cx-100:cx+100].astype(np.float32)
                patch_b = other[cy-100:cy+100, cx-100:cx+100].astype(np.float32)
                diff = float(np.mean(np.abs(patch_a - patch_b)))
                if diff < 8.0:
                    logger.warning(
                        f"[{scanner_id}] Camera validation FAILED: frame "
                        f"near-identical to {sid} (diff={diff:.1f}px) — "
                        f"DSHOW bleed, liberando"
                    )
                    return False
            return True

        return validator

    @staticmethod
    def _load_plc_config(path: Path) -> dict:
        import yaml
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)["plc"]

    @staticmethod
    def _load_camera_config() -> dict:
        import yaml
        if not _APP_CONFIG_PATH.exists():
            return {}
        with _APP_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("camera", {})

    @staticmethod
    def _camera_source_for_scanner(cfg: dict) -> int | str:
        """Resolve camera source from io_map (camera_source URL or camera_index int)."""
        if "camera_source" in cfg:
            source = cfg["camera_source"]
            if isinstance(source, str) and source.isdigit():
                return int(source)
            return source
        return int(cfg.get("camera_index", 0))
