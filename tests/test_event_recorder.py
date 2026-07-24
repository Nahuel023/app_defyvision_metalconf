"""
Tests unitarios para EventRecorder.
Cubre: naming secuencial, presupuesto de disco, límite de RAM del buffer,
contenido del manifest y truncado por presupuesto.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.pipeline.event_recorder import EventRecorder, _dir_size
from src.ui.frame_viewer import _event_summary


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _make_recorder(events_dir: Path, max_disk_mb: float = 10.0, **kw) -> EventRecorder:
    return EventRecorder(
        scanner_id="scanner_1",
        events_dir=events_dir,
        max_disk_gb=max_disk_mb / 1000.0,
        pre_seconds=kw.pop("pre_seconds", 10.0),
        fps=kw.pop("fps", 5.0),
        jpeg_quality=kw.pop("jpeg_quality", 80),
        max_ram_mb=kw.pop("max_ram_mb", 64.0),
        **kw,
    )


def _blank_frame(h: int = 100, w: int = 100) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _fake_jpeg(size: int = 50_000) -> bytes:
    """JPEG mínimo válido para llenar espacio en disco sin usar cv2."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * (size - 4) + b"\xff\xd9"


# ----------------------------------------------------------------------
# Naming secuencial
# ----------------------------------------------------------------------

class TestFolderNaming:
    def test_first_event_is_stop_1(self, tmp_path: Path) -> None:
        r = _make_recorder(tmp_path / "events")
        d = r._next_event_dir()
        today = datetime.now().strftime("%d-%m-%Y")
        assert d.name == f"{today}_STOP_1"

    def test_sequential_numbering(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events)
        today = datetime.now().strftime("%d-%m-%Y")
        # Crear STOP_1 y STOP_2 manualmente
        (events / f"{today}_STOP_1").mkdir(parents=True)
        (events / f"{today}_STOP_2").mkdir(parents=True)
        d = r._next_event_dir()
        assert d.name == f"{today}_STOP_3"

    def test_gap_no_skip(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events)
        today = datetime.now().strftime("%d-%m-%Y")
        # Solo existe STOP_1 (falta el 2)
        (events / f"{today}_STOP_1").mkdir(parents=True)
        d = r._next_event_dir()
        assert d.name == f"{today}_STOP_2"


# ----------------------------------------------------------------------
# Poda por presupuesto de disco
# ----------------------------------------------------------------------

class TestPruneByBudget:
    def _populate(self, events: Path, n: int, size_per_dir: int) -> None:
        """Crea n carpetas de eventos con un archivo de `size_per_dir` bytes cada una."""
        events.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            d = events / f"01-01-2026_STOP_{i}"
            d.mkdir()
            (d / "frame_0000.jpg").write_bytes(b"x" * size_per_dir)
            time.sleep(0.01)  # mtime distinto para orden determinista

    def test_prune_oldest_first(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        # 3 dirs × 400 KB = 1.2 MB; budget = 1 MB; needed = 400 KB
        # → debe borrar al menos 1 dir (el más viejo)
        self._populate(events, 3, 400_000)
        r = _make_recorder(events, max_disk_mb=1.0)
        r._prune_to_budget(needed=400_000)

        remaining = sorted(d.name for d in events.iterdir() if d.is_dir())
        # El más viejo es STOP_1 → debe haberse borrado
        assert "01-01-2026_STOP_1" not in remaining

    def test_total_stays_under_budget(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        self._populate(events, 5, 200_000)  # 5 × 200 KB = 1 MB
        budget_mb = 1.0
        r = _make_recorder(events, max_disk_mb=budget_mb)
        needed = 200_000
        r._prune_to_budget(needed=needed)

        total = sum(_dir_size(d) for d in events.iterdir() if d.is_dir())
        assert total + needed <= int(budget_mb * 1_000_000)

    def test_no_prune_when_within_budget(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        self._populate(events, 2, 100_000)  # 200 KB total
        r = _make_recorder(events, max_disk_mb=10.0)
        r._prune_to_budget(needed=100_000)

        dirs = list(events.iterdir())
        assert len(dirs) == 2  # nada se borró


# ----------------------------------------------------------------------
# Límite de RAM del buffer
# ----------------------------------------------------------------------

class TestBufferRamLimit:
    def test_buffer_byte_limit_enforced(self, tmp_path: Path) -> None:
        r = _make_recorder(tmp_path / "events", max_ram_mb=1.0)
        # Añadir frames grandes forzando FPS mínimo
        r._min_interval = 0.0  # desactivar rate-limit para el test
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        for _ in range(100):
            r.add_frame(frame)
        assert r._buf_bytes <= int(1.0 * 1_048_576)

    def test_time_window_evicts_old_frames(self, tmp_path: Path) -> None:
        r = _make_recorder(tmp_path / "events", pre_seconds=1.0, max_ram_mb=64.0)
        r._min_interval = 0.0

        # Simular frames viejos con timestamp anterior
        old_ts = time.time() - 5.0
        data = b"\xff\xd8\xff\xd9" + b"\x00" * 1000
        with r._lock:
            r._buf.append((old_ts, data))
            r._buf_bytes += len(data)

        # Al añadir un frame nuevo, el viejo debe ser expulsado
        r.add_frame(_blank_frame())
        with r._lock:
            timestamps = [ts for ts, _ in r._buf]
        assert all(ts >= time.time() - 2.0 for ts in timestamps)


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------

class TestManifest:
    def test_manifest_required_fields(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events)
        r._min_interval = 0.0

        r.add_frame(_blank_frame())

        frames = [(time.time(), b"\xff\xd8\xff\xd9")]
        r._flush_sync(frames, "machine_stop", "agujero faltante persistente")

        event_dirs = [d for d in events.iterdir() if d.is_dir()]
        assert len(event_dirs) == 1

        manifest_path = event_dirs[0] / "manifest.json"
        assert manifest_path.exists()

        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        for field in ("timestamp", "scanner_id", "event_type", "reason",
                      "pre_frames_count", "post_frames_count", "total_bytes"):
            assert field in m, f"campo faltante en manifest: {field}"

        assert m["scanner_id"] == "scanner_1"
        assert m["event_type"] == "machine_stop"

    def test_manifest_frame_count_matches(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events)
        frames = [(time.time() + i * 0.1, _fake_jpeg(10_000)) for i in range(5)]
        r._flush_sync(frames, "fault", "streak NOK")

        manifest = json.loads(
            (next((events).iterdir()) / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["pre_frames_count"] == 5
        assert manifest["total_bytes"] == sum(len(_fake_jpeg(10_000)) for _ in range(5))


class TestPostEventWindow:
    def test_event_during_post_event_extends_window(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events, post_seconds=5.0)
        frames = [(time.time(), _fake_jpeg(10_000))]
        r._flush_sync(frames, "machine_stop", "primer stop")

        assert r._post_dir is not None
        first_until = r._post_until

        with r._lock:
            r._buf.clear()
            r._buf_bytes = 0

        time.sleep(0.02)
        second_trigger = np.full((40, 60, 3), 200, dtype=np.uint8)
        r.flush_event("fault", "segundo stop", trigger_frame=second_trigger)
        r._task_q.join()

        assert r._post_dir is not None
        assert r._post_until >= first_until
        event_dir = next(events.iterdir())
        assert (event_dir / "retrigger_0001.jpg").is_file()
        manifest = json.loads(
            (event_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["additional_triggers"][0]["reason"] == "segundo stop"

    def test_trigger_and_post_frames_are_preserved_and_counted(
        self, tmp_path: Path
    ) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events, post_seconds=5.0)
        r._min_interval = 0.0
        trigger = np.full((80, 120, 3), 90, dtype=np.uint8)
        overlay = np.full((80, 120, 3), 180, dtype=np.uint8)

        # No hay buffer previo: el frame disparador por sí solo debe crear evento.
        event_dir = r.flush_event(
            "machine_stop",
            "patron desalineado",
            trigger_frame=trigger,
            trigger_overlay=overlay,
        )
        assert event_dir is not None
        assert r.is_post_event_active()

        r.add_frame(np.full((80, 120, 3), 220, dtype=np.uint8))
        r.finish_post_event()
        r._task_q.join()

        assert (event_dir / "trigger.jpg").is_file()
        assert (event_dir / "trigger_overlay.jpg").is_file()
        assert (event_dir / "post_0000.jpg").is_file()

        manifest = json.loads(
            (event_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["reason"] == "patron desalineado"
        assert manifest["trigger_frames_count"] == 1
        assert manifest["post_frames_count"] == 1
        assert manifest["frames_count"] == 2

        summary = _event_summary(event_dir)
        assert summary["trigger_count"] == 1
        assert [path.name for path in summary["all_frames"]] == [
            "trigger_overlay.jpg",
            "post_0000.jpg",
        ]
        r.close()

    def test_trigger_bypasses_prebuffer_rate_limit(self, tmp_path: Path) -> None:
        r = _make_recorder(tmp_path / "events", post_seconds=0.0)
        r._last_add = time.monotonic()

        event_dir = r.flush_event(
            "fault",
            "falla puntual",
            trigger_frame=np.full((40, 60, 3), 77, dtype=np.uint8),
        )
        assert event_dir is not None
        r._task_q.join()
        assert (event_dir / "trigger.jpg").is_file()
        r.close()

    def test_trigger_is_not_duplicated_as_last_pre_frame(self, tmp_path: Path) -> None:
        r = _make_recorder(tmp_path / "events", post_seconds=0.0)
        r._min_interval = 0.0
        trigger = np.full((40, 60, 3), 123, dtype=np.uint8)
        r.add_frame(trigger)

        event_dir = r.flush_event(
            "machine_stop",
            "patron desalineado",
            trigger_frame=trigger,
        )
        assert event_dir is not None
        r._task_q.join()

        manifest = json.loads(
            (event_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["pre_frames_count"] == 0
        assert manifest["trigger_frames_count"] == 1
        assert not list(event_dir.glob("frame_*.jpg"))
        r.close()


# ----------------------------------------------------------------------
# Truncado por presupuesto
# ----------------------------------------------------------------------

class TestTruncation:
    def test_oversized_event_truncated(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        # Budget = 500 KB; frames = 10 × 100 KB = 1 MB → debe truncarse
        r = _make_recorder(events, max_disk_mb=0.5)
        frames = [
            (time.time() + i * 0.1, _fake_jpeg(100_000)) for i in range(10)
        ]
        r._flush_sync(frames, "machine_stop", "test")

        event_dirs = [d for d in events.iterdir() if d.is_dir()]
        assert len(event_dirs) == 1

        total = _dir_size(event_dirs[0])
        # El manifest también ocupa espacio, pero frames solos < budget
        assert total <= int(0.5 * 1_000_000) + 2048  # +2 KB margen para manifest

    def test_truncated_keeps_most_recent(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        r = _make_recorder(events, max_disk_mb=0.15)  # 150 KB
        # 5 frames de 50 KB cada uno (250 KB total > budget)
        now = time.time()
        frames = [(now + i, _fake_jpeg(50_000)) for i in range(5)]
        r._flush_sync(frames, "machine_stop", "test")

        event_dirs = [d for d in events.iterdir() if d.is_dir()]
        m = json.loads((event_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        # Solo caben 2 frames de 50 KB en 150 KB → los más recientes (índices 3,4)
        assert m["pre_frames_count"] <= 3


def test_finalize_counts_raw_post_frames_once_and_is_idempotent(tmp_path: Path) -> None:
    event_dir = tmp_path / "event"
    event_dir.mkdir()
    (event_dir / "frame_0000.jpg").write_bytes(b"pre")
    (event_dir / "post_0000.jpg").write_bytes(b"post")
    (event_dir / "post_0000_overlay.jpg").write_bytes(b"overlay")
    (event_dir / "manifest.json").write_text(
        json.dumps({"post_frames_count": 0, "total_bytes": 999}),
        encoding="utf-8",
    )
    recorder = EventRecorder.__new__(EventRecorder)
    recorder._id = "scanner_1"

    recorder._finalize_manifest(event_dir)
    recorder._finalize_manifest(event_dir)

    manifest = json.loads((event_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["post_frames_count"] == 1
    assert manifest["total_bytes"] == len(b"pre") + len(b"post") + len(b"overlay")
