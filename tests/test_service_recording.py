from pathlib import Path

import cv2
import numpy as np

from src.ui import service


def test_recordings_root_is_anchored_to_application_root(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_ROOT", tmp_path)

    assert service.RecordingTab._recordings_root() == tmp_path / "data" / "recordings"


def test_recording_frame_writer_creates_valid_png(tmp_path):
    path = tmp_path / "frame_0000.png"
    frame = np.full((24, 32, 3), 127, dtype=np.uint8)

    assert service.RecordingTab._write_recording_frame(path, frame) is True
    assert path.is_file()
    decoded = cv2.imread(str(path))
    assert decoded is not None
    assert decoded.shape == frame.shape
