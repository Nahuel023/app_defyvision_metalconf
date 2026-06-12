"""
Grabador de métricas a SQLite.

Toma un snapshot de todos los scanners cada `interval_s` segundos y lo
persiste en data/metrics/metrics.db. Los datos sobreviven reinicios de la app.
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  REAL    NOT NULL,
    scanner_id          TEXT    NOT NULL,
    total_inspections   INTEGER DEFAULT 0,
    ok_count            INTEGER DEFAULT 0,
    nok_count           INTEGER DEFAULT 0,
    ok_pct              REAL    DEFAULT 0,
    nok_streak          INTEGER DEFAULT 0,
    max_nok_streak      INTEGER DEFAULT 0,
    fault_count         INTEGER DEFAULT 0,
    avg_missing_holes   REAL    DEFAULT 0,
    avg_detection_ratio REAL    DEFAULT 0,
    align_fail_count    INTEGER DEFAULT 0,
    low_quality_count   INTEGER DEFAULT 0,
    low_quality_pct     REAL    DEFAULT 0,
    machine_stop_count  INTEGER DEFAULT 0,
    camera_missing_sec  REAL    DEFAULT 0,
    camera_missing_events INTEGER DEFAULT 0,
    inspection_uptime_pct REAL  DEFAULT 0,
    last_position_diff  REAL    DEFAULT 0,
    insp_per_min        REAL    DEFAULT 0,
    camera_fps          REAL    DEFAULT 0,
    session_duration_s  REAL    DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ts      ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_scanner ON metrics(scanner_id, ts);
"""

_INSERT = """
INSERT INTO metrics
    (ts, scanner_id, total_inspections, ok_count, nok_count,
     ok_pct, nok_streak, max_nok_streak, fault_count,
     avg_missing_holes, avg_detection_ratio, align_fail_count,
     low_quality_count, low_quality_pct, machine_stop_count,
     camera_missing_sec, camera_missing_events, inspection_uptime_pct,
     last_position_diff, insp_per_min, camera_fps, session_duration_s)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


class MetricsRecorder:
    """Persiste snapshots de métricas en SQLite a intervalos regulares."""

    def __init__(
        self,
        db_path: str = "data/metrics/metrics.db",
        interval_s: float = 60.0,
    ) -> None:
        self._db_path  = Path(db_path)
        self._interval = interval_s
        self._system   = None
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self, system) -> None:
        self._system = system
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="metrics-recorder"
        )
        self._thread.start()
        logger.info(f"MetricsRecorder iniciado — intervalo={self._interval}s, DB={self._db_path}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def query(self, scanner_id: str, hours: int = 24) -> list[dict]:
        """Devuelve filas de los últimos `hours` horas para un scanner."""
        since = time.time() - hours * 3600
        try:
            con = sqlite3.connect(self._db_path)
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT * FROM metrics WHERE scanner_id=? AND ts>=? ORDER BY ts",
                (scanner_id, since),
            )
            rows = [dict(r) for r in cur.fetchall()]
            con.close()
            return rows
        except Exception as exc:
            logger.error(f"MetricsRecorder.query error: {exc}")
            return []

    def query_recent(self, scanner_id: str, limit: int = 240) -> list[dict]:
        """Devuelve los últimos `limit` snapshots de un scanner."""
        try:
            con = sqlite3.connect(self._db_path)
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT * FROM metrics WHERE scanner_id=? ORDER BY ts DESC LIMIT ?",
                (scanner_id, int(limit)),
            )
            rows = [dict(r) for r in cur.fetchall()]
            con.close()
            rows.reverse()
            return rows
        except Exception as exc:
            logger.error(f"MetricsRecorder.query_recent error: {exc}")
            return []

    def latest_timestamp(self, scanner_id: str) -> float | None:
        """Devuelve el timestamp del último snapshot disponible para un scanner."""
        try:
            con = sqlite3.connect(self._db_path)
            cur = con.execute(
                "SELECT MAX(ts) FROM metrics WHERE scanner_id=?",
                (scanner_id,),
            )
            value = cur.fetchone()[0]
            con.close()
            return float(value) if value is not None else None
        except Exception as exc:
            logger.error(f"MetricsRecorder.latest_timestamp error: {exc}")
            return None

    def available_scanners(self) -> list[str]:
        try:
            con = sqlite3.connect(self._db_path)
            cur = con.execute(
                "SELECT DISTINCT scanner_id FROM metrics ORDER BY scanner_id"
            )
            ids = [r[0] for r in cur.fetchall()]
            con.close()
            return ids
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        try:
            con = sqlite3.connect(self._db_path)
            con.executescript(_SCHEMA)
            # Migración: agregar columnas nuevas si la tabla ya existía sin ellas
            existing = {row[1] for row in con.execute("PRAGMA table_info(metrics)")}
            migrations = [
                ("avg_detection_ratio", "REAL DEFAULT 0"),
                ("align_fail_count",    "INTEGER DEFAULT 0"),
                ("low_quality_count",   "INTEGER DEFAULT 0"),
                ("low_quality_pct",     "REAL DEFAULT 0"),
                ("machine_stop_count",  "INTEGER DEFAULT 0"),
                ("camera_missing_sec",  "REAL DEFAULT 0"),
                ("camera_missing_events", "INTEGER DEFAULT 0"),
                ("inspection_uptime_pct", "REAL DEFAULT 0"),
            ]
            for col, typedef in migrations:
                if col not in existing:
                    con.execute(f"ALTER TABLE metrics ADD COLUMN {col} {typedef}")
                    logger.info(f"MetricsRecorder: columna '{col}' agregada a metrics.db")
            con.commit()
            con.close()
        except Exception as exc:
            logger.error(f"MetricsRecorder DB init error: {exc}")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(timeout=self._interval)
            if self._stop.is_set():
                break
            try:
                self._snapshot()
            except Exception as exc:
                logger.error(f"MetricsRecorder snapshot error: {exc}")

    def _snapshot(self) -> None:
        if self._system is None:
            return
        now  = time.time()
        rows = []
        for sid in self._system.scanner_ids():
            try:
                s   = self._system.scanner(sid).get_status()
                cam = self._system.camera(sid)
            except KeyError:
                continue

            total = s.get("total_inspections", 0)
            ok    = s.get("ok_count", 0)
            nok   = s.get("nok_count", 0)
            ok_pct = (ok / total * 100) if total > 0 else 0.0

            start = s.get("session_start")
            if start:
                dur = (datetime.now() - start).total_seconds()
                ipm = (total / dur * 60) if dur > 0 else 0.0
            else:
                dur = 0.0
                ipm = 0.0

            rows.append((
                now, sid,
                total, ok, nok, ok_pct,
                s.get("nok_streak", 0),
                s.get("max_nok_streak", 0),
                s.get("fault_count", 0),
                s.get("avg_missing_holes", 0.0),
                s.get("avg_detection_ratio", 0.0),
                s.get("align_fail_count", 0),
                s.get("low_quality_count", 0),
                s.get("low_quality_pct", 0.0),
                s.get("machine_stop_count", 0),
                s.get("camera_missing_sec", 0.0),
                s.get("camera_missing_events", 0),
                s.get("inspection_uptime_pct", 0.0),
                s.get("last_position_diff", 0.0),
                ipm,
                cam.fps,
                dur,
            ))

        if not rows:
            return
        try:
            con = sqlite3.connect(self._db_path)
            con.executemany(_INSERT, rows)
            con.commit()
            con.close()
        except Exception as exc:
            logger.error(f"MetricsRecorder write error: {exc}")
