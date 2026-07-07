"""
Punto de entrada del sistema de producción.

Crea y supervisa un ScannerController y Camera por cada scanner
definido en config/io_map.yaml. La conexión Modbus es compartida.
"""

import logging
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
        for scanner_id, camera in self._cameras.items():
            camera.set_frame_validator(self._make_bleed_validator(scanner_id))

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
        self._recorder.stop()
        for scanner in self._scanners.values():
            scanner.shutdown()
        # Apagar todas las salidas PLC incondicionalmente (por si el scanner
        # quedó en IDLE/MANUAL y no apagó las luces en stop())
        for sid in self._scanners:
            for sig in ("solenoid", "backlight",
                        "light_blue", "light_green", "light_yellow", "light_red"):
                self._io.write(f"{sid}.{sig}", False)
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

    def _make_bleed_validator(self, scanner_id: str) -> Callable:
        """Return a frame validator that rejects frames identical to any other camera.

        DSHOW can re-enumerate device indices when a camera disconnects, causing
        a reconnecting camera to open the still-connected sibling device instead
        of its own. The validator detects this by comparing a sample of pixels;
        two distinct real-world views will never be near-identical.
        """
        cameras = self._cameras  # capture reference, not copy

        def validator(frame: np.ndarray) -> bool:
            for sid, cam in cameras.items():
                if sid == scanner_id:
                    continue
                # get_raw_frame() (no get_frame()): frame aca es el candidato crudo
                # recien capturado (sin zoom); comparar contra el frame YA ZOOMEADO
                # del otro scanner es una comparacion inconsistente que puede dar
                # falsos positivos/negativos de "bleed" segun el zoom de cada uno.
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
