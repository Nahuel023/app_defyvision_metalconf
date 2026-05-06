"""
Punto de entrada del sistema de producción.

Crea y supervisa un ScannerController y Camera por cada scanner
definido en config/io_map.yaml. La conexión Modbus es compartida.
"""

import logging
from pathlib import Path

from src.plc.client import PLCClient
from src.plc.io_map import IOMap
from src.controller.scanner_controller import ScannerController
from src.vision.camera import Camera

logger = logging.getLogger(__name__)

_APP_CONFIG_PATH = Path("config/app.yaml")


class InspectionSystem:
    def __init__(self, io_map_path: Path = Path("config/io_map.yaml")) -> None:
        plc_cfg = self._load_plc_config(io_map_path)
        cam_cfg = self._load_camera_config()

        self._client = PLCClient(
            ip=plc_cfg["ip"],
            port=plc_cfg.get("port", 502),
            unit_id=plc_cfg.get("unit_id", 1),
        )
        self._io = IOMap(self._client, io_map_path)
        self._cameras: dict[str, Camera] = {}
        self._scanners: dict[str, ScannerController] = {}

        for scanner_id in self._io.scanner_ids():
            cfg = self._io.scanner_config(scanner_id)
            camera = Camera(
                cfg["camera_index"],
                max_retries=cam_cfg.get("max_retries", 10),
                retry_interval_s=cam_cfg.get("retry_interval_s", 3.0),
            )
            scanner = ScannerController(scanner_id, self._io, camera)
            self._cameras[scanner_id] = camera
            self._scanners[scanner_id] = scanner

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def connect_plc(self) -> bool:
        ok = self._client.connect()
        if not ok:
            logger.error("No se pudo conectar al PLC")
        return ok

    def start_cameras(self) -> dict[str, bool]:
        """Arranca todas las cámaras. Devuelve {scanner_id: ok}."""
        results = {}
        for sid, cam in self._cameras.items():
            ok = cam.start()
            results[sid] = ok
            if not ok:
                logger.error(f"[{sid}] cámara {cam.index} no disponible")
        return results

    def shutdown(self) -> None:
        """Detiene todos los scanners, cámaras y cierra el PLC."""
        for scanner in self._scanners.values():
            scanner.stop()
        for camera in self._cameras.values():
            camera.stop()
        self._client.disconnect()
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

    # ------------------------------------------------------------------

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
