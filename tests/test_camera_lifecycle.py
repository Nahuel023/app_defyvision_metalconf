from src.vision.camera import Camera


class _AliveThread:
    def is_alive(self) -> bool:
        return True


def test_camera_does_not_revive_thread_that_is_still_stopping() -> None:
    camera = Camera(0)
    camera._thread = _AliveThread()
    camera._running = False

    assert camera.start() is False
    assert camera._running is False


def test_raw_frame_accessor_does_not_apply_zoom() -> None:
    import numpy as np

    camera = Camera(0, settings={"zoom": 200})
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frame[:, 4:] = 255
    camera._frame = frame

    assert np.array_equal(camera.get_raw_frame(), frame)
    assert not np.array_equal(camera.get_frame(), frame)
