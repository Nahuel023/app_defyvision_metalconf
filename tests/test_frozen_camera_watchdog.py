"""Regresion del bug critico de scanner_2 (2026-07-29).

Sintoma: el operario pulsaba INICIAR en scanner_2 y el scanner quedaba "EN MARCHA"
indefinidamente sin contar ni una imagen OK, sin NOK y sin ningun error.

Causa: el validador anti-bleed (pensado para camaras USB con indices DSHOW) se
instalaba tambien sobre las camaras IP. Los dos scanners miran la MISMA chapa
microperforada, el parche central salia casi identico (1.7-7.4px medidos en
produccion, umbral 8.0) y la camara rechazaba TODOS sus frames. Como el rechazo
no ponia _frame en None, get_frame() devolvia siempre la misma imagen congelada:
el gate de movimiento nunca se superaba, no se inspeccionaba nada y ninguna via
de parada podia dispararse.

Aca se cubren las tres defensas: no instalar el validador entre camaras IP,
soltar el frame retenido cuando deja de llegar imagen nueva, y escalar a ERROR
si el scanner corre sin analizar nada.
"""

import time
from types import SimpleNamespace

import numpy as np

from src.controller.scanner_controller import ScannerController
from src.controller.system import InspectionSystem
from src.utils.state import ScannerState
from src.vision.camera import Camera

from tests.test_scanner_controller import _FakeCamera, _FakeIO


def test_ip_cameras_are_not_treated_as_local_devices() -> None:
    assert InspectionSystem._is_local_device(0) is True
    assert InspectionSystem._is_local_device("1") is True
    assert InspectionSystem._is_local_device(
        "http://192.168.1.2/oneshotimage.jpg") is False
    assert InspectionSystem._is_local_device(
        "rtsp://192.168.1.3/stream") is False


def test_bleed_validator_rejects_near_identical_local_feeds() -> None:
    """El anti-bleed sigue funcionando entre camaras USB locales."""
    system = InspectionSystem.__new__(InspectionSystem)
    frame = np.full((480, 640, 3), 100, dtype=np.uint8)
    twin = Camera(1)
    twin._frame = frame.copy()
    system._cameras = {"scanner_1": Camera(0), "scanner_2": twin}

    validator = system._make_bleed_validator("scanner_1", ["scanner_1", "scanner_2"])
    assert validator(frame) is False

    different = np.full((480, 640, 3), 200, dtype=np.uint8)
    assert validator(different) is True


def test_frozen_feed_is_released_so_camera_loss_can_escalate() -> None:
    """Frames identicos (o rechazados) no pueden quedar servidos para siempre."""
    cam = Camera("http://192.168.1.2/oneshotimage.jpg")
    cam._freeze_timeout_s = 0.05

    frame = np.random.default_rng(0).integers(
        0, 255, (480, 640, 3), dtype=np.uint8
    )
    cam._publish_frame(frame.copy())
    assert cam.get_frame() is not None

    time.sleep(0.06)
    cam._publish_frame(frame.copy())          # misma imagen → congelada
    assert cam.get_frame() is None

    # Al volver imagen nueva, la camara se recupera sola.
    fresh = np.random.default_rng(1).integers(
        0, 255, (480, 640, 3), dtype=np.uint8
    )
    cam._publish_frame(fresh)
    assert cam.get_frame() is not None


def test_camera_sequence_advances_only_for_visually_fresh_frames() -> None:
    cam = Camera("http://192.168.1.2/oneshotimage.jpg")
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    cam._publish_frame(frame.copy())
    first, first_seq = cam.get_fresh_frame()
    assert first is not None
    assert first_seq > 0

    cam._publish_frame(frame.copy())
    repeated, repeated_seq = cam.get_fresh_frame()
    assert repeated is not None
    assert repeated_seq == first_seq

    fresh = frame.copy()
    fresh[0, 0] = 255  # la firma submuestreada incluye este pixel
    cam._publish_frame(fresh)
    _, fresh_seq = cam.get_fresh_frame()
    assert fresh_seq == first_seq + 1


def test_rejected_frames_also_trip_the_freeze_watchdog() -> None:
    cam = Camera("http://192.168.1.2/oneshotimage.jpg")
    cam._freeze_timeout_s = 0.05
    cam._publish_frame(np.zeros((480, 640, 3), dtype=np.uint8))
    assert cam.get_frame() is not None

    time.sleep(0.06)
    cam._check_frozen()                        # lo que hace el validador al rechazar
    assert cam.get_frame() is None


def test_inspection_stall_escalates_to_error() -> None:
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    try:
        controller._transition(ScannerState.RUNNING)
        controller._stall_warn_s    = 0.0
        controller._stall_timeout_s = 0.05
        controller._last_inspection_mono = time.monotonic() - 1.0

        assert controller._check_inspection_stall(0.0) is True
        assert controller.state == ScannerState.ERROR
        assert ("scanner_1.solenoid", False) in controller._io.writes
    finally:
        controller.shutdown()


def test_inspection_stall_does_not_fire_while_inspecting() -> None:
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    try:
        controller._transition(ScannerState.RUNNING)
        controller._stall_warn_s    = 0.0
        controller._stall_timeout_s = 60.0
        controller._last_inspection_mono = time.monotonic()

        assert controller._check_inspection_stall(0.0) is False
        assert controller.state == ScannerState.RUNNING
    finally:
        controller.shutdown()


def test_sustained_low_quality_stops_instead_of_running_blind() -> None:
    controller = ScannerController("scanner_1", _FakeIO(), _FakeCamera())
    controller._recorder = None
    controller._low_quality_stop_frames = 3
    result = SimpleNamespace(
        status="OK",
        frame_quality="LOW_QUALITY",
        detection_ratio=1.0,
        alignment_ok=True,
        machine_stop=False,
        overlay=None,
        image=None,
        report=SimpleNamespace(missing=0),
    )
    try:
        controller._transition(ScannerState.RUNNING)
        controller._handle_result(result)
        controller._handle_result(result)
        assert controller.state == ScannerState.RUNNING
        assert controller.get_status()["ok_count"] == 0
        assert controller.get_status()["nok_count"] == 0

        controller._handle_result(result)
        assert controller.state == ScannerState.ERROR
        assert controller._stop_event.is_set()
        assert ("scanner_1.solenoid", False) in controller._io.writes
    finally:
        controller.shutdown()
