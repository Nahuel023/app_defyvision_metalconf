import sqlite3
import time
from pathlib import Path

from src.metrics.recorder import MetricsRecorder


def _insert_rows(db_path: Path, rows: list[tuple[float, str]]) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            """
            INSERT INTO metrics (
                ts, scanner_id, total_inspections, ok_count, nok_count,
                ok_pct, nok_streak, max_nok_streak, fault_count,
                avg_missing_holes, avg_detection_ratio, align_fail_count,
                low_quality_count, low_quality_pct, machine_stop_count,
                camera_missing_sec, camera_missing_events, inspection_uptime_pct,
                last_position_diff, insp_per_min, camera_fps, session_duration_s
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    ts, scanner_id, 0, 0, 0,
                    0.0, 0, 0, 0,
                    0.0, 0.0, 0,
                    0, 0.0, 0,
                    0.0, 0, 0.0,
                    0.0, 0.0, 0.0, 0.0,
                )
                for ts, scanner_id in rows
            ],
        )
        con.commit()
    finally:
        con.close()


def test_prune_old_rows_removes_expired_samples(tmp_path: Path) -> None:
    db_path = tmp_path / "metrics.db"
    recorder = MetricsRecorder(
        db_path=str(db_path),
        retention_days=1.0,
        max_rows=100,
        maintenance_interval_s=60.0,
    )

    now = time.time()
    _insert_rows(
        db_path,
        [
            (now - 3 * 86400, "scanner_1"),
            (now - 2 * 3600, "scanner_1"),
        ],
    )

    deleted = recorder._prune_old_rows()

    assert deleted == 1
    rows = recorder.query_recent("scanner_1", limit=10)
    assert len(rows) == 1


def test_prune_old_rows_caps_rows_per_scanner(tmp_path: Path) -> None:
    db_path = tmp_path / "metrics.db"
    recorder = MetricsRecorder(
        db_path=str(db_path),
        retention_days=365.0,
        max_rows=3,
        maintenance_interval_s=60.0,
    )

    now = time.time()
    _insert_rows(
        db_path,
        [(now + idx, "scanner_1") for idx in range(5)],
    )

    deleted = recorder._prune_old_rows()

    assert deleted == 2
    rows = recorder.query_recent("scanner_1", limit=10)
    assert len(rows) == 3
    assert rows[0]["ts"] == now + 2
