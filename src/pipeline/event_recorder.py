"""
Buffer circular de evidencia pre-evento por scanner.

Graba `pre_seconds` antes de la parada y `post_seconds` después.
Frames originales JPEG en RAM (sin overlay). Nunca supera `max_disk_gb`.
"""

import json
import logging
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class EventRecorder:
    """
    Buffer circular JPEG por scanner + volcado a disco en eventos de parada.

    Flujo pre/post evento:
      1. `add_frame` acumula JPEG en un deque circular limitado por tiempo y RAM.
      2. `flush_event` snapshot-ea el buffer, lo guarda en hilo background,
         y activa el modo POST-EVENTO.
      3. Durante post-evento, `add_frame` escribe directamente a disco
         durante `post_seconds` segundos.
      4. Al expirar el post-evento, el manifest se actualiza con los totales
         finales y se reanuda el buffer circular normal.

    Garantías de disco:
      - Poda automática oldest-first antes de crear cada evento.
      - Si un único evento supera el presupuesto, se trunca conservando
        los frames más recientes (los más cercanos a la parada).
    """

    def __init__(
        self,
        scanner_id: str,
        events_dir: Path,
        max_disk_gb: float = 10.0,
        pre_seconds: float = 60.0,
        post_seconds: float = 30.0,
        fps: float = 5.0,
        jpeg_quality: int = 80,
        max_ram_mb: float = 256.0,
    ) -> None:
        self._id = scanner_id
        self._dir = Path(events_dir)
        self._max_disk_bytes = int(max_disk_gb * 1_000_000_000)
        self._max_buf_bytes = int(max_ram_mb * 1_048_576)
        self._pre_seconds = max(1.0, float(pre_seconds))
        self._post_seconds = max(0.0, float(post_seconds))
        self._min_interval = 1.0 / max(float(fps), 0.5)
        self._jpeg_q = int(max(10, min(95, jpeg_quality)))

        # Buffer circular pre-evento: deque de (timestamp_unix, jpeg_bytes)
        self._buf: deque[tuple[float, bytes]] = deque()
        self._buf_bytes: int = 0
        self._lock = threading.Lock()

        self._last_add: float = 0.0
        self._last_flush: float = 0.0
        self._flush_cooldown: float = 5.0

        # Estado post-evento (protegido por _lock)
        self._post_dir: Optional[Path] = None
        self._post_until: float = 0.0
        self._post_idx: int = 0

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def add_frame(self, frame: np.ndarray) -> None:
        """
        Comprime el frame como JPEG y lo añade al buffer o al post-evento.
        Rate-limited. Thread-safe. No bloquea (escrituras ~1 ms por frame).
        """
        now_m = time.monotonic()
        if now_m - self._last_add < self._min_interval:
            return
        self._last_add = now_m

        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_q]
        )
        if not ok:
            return
        data: bytes = bytes(buf)
        ts = time.time()

        # ── Determinar modo actual (bajo lock mínimo) ──────────────────
        post_write: Optional[tuple[Path, int]] = None
        finalize_dir: Optional[Path] = None

        with self._lock:
            if self._post_dir is not None:
                if now_m < self._post_until:
                    # Dentro de la ventana post-evento: guardar en disco
                    post_write = (self._post_dir, self._post_idx)
                    self._post_idx += 1
                else:
                    # Ventana expirada: finalizar y reanudar buffer
                    finalize_dir = self._post_dir
                    self._post_dir = None
                    self._post_idx = 0

        # ── Post-evento activo: escribir directo a disco ───────────────
        if post_write is not None:
            post_dir, idx = post_write
            try:
                (post_dir / f"post_{idx:04d}.jpg").write_bytes(data)
            except Exception as exc:
                logger.error(f"[{self._id}] post-evento write: {exc}")
            return  # no buffering mientras se graba post-evento

        # ── Post-evento expiró: actualizar manifest en background ──────
        if finalize_dir is not None:
            threading.Thread(
                target=self._finalize_manifest,
                args=(finalize_dir,),
                daemon=True,
                name=f"{self._id}-event-finalize",
            ).start()

        # ── Buffer circular normal ─────────────────────────────────────
        with self._lock:
            self._buf.append((ts, data))
            self._buf_bytes += len(data)
            cutoff = ts - self._pre_seconds
            while self._buf and self._buf[0][0] < cutoff:
                self._buf_bytes -= len(self._buf.popleft()[1])
            while self._buf and self._buf_bytes > self._max_buf_bytes:
                self._buf_bytes -= len(self._buf.popleft()[1])

    def flush_event(self, event_type: str, reason: str = "") -> None:
        """
        Vuelca el buffer pre-evento a disco en hilo background y activa
        la grabación post-evento. Ignora llamadas dentro del cooldown.
        """
        now_m = time.monotonic()
        if now_m - self._last_flush < self._flush_cooldown:
            return
        self._last_flush = now_m

        with self._lock:
            if not self._buf:
                return
            frames = list(self._buf)
            self._buf.clear()
            self._buf_bytes = 0

        threading.Thread(
            target=self._flush_sync,
            args=(frames, event_type, reason),
            daemon=True,
            name=f"{self._id}-event-flush",
        ).start()

    # ------------------------------------------------------------------
    # Disco — hilo background
    # ------------------------------------------------------------------

    def _flush_sync(
        self,
        frames: list[tuple[float, bytes]],
        event_type: str,
        reason: str,
    ) -> None:
        """Escribe frames pre-evento + manifest. Luego activa post-evento."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)

            new_size = sum(len(d) for _, d in frames)

            # Truncar si supera el presupuesto total (conservar frames recientes)
            if new_size > self._max_disk_bytes > 0:
                kept: list[tuple[float, bytes]] = []
                kept_size = 0
                for ts, data in reversed(frames):
                    if kept_size + len(data) > self._max_disk_bytes:
                        break
                    kept.append((ts, data))
                    kept_size += len(data)
                frames = list(reversed(kept))
                new_size = kept_size
                logger.warning(
                    f"[{self._id}] pre-evento truncado: {len(frames)} frames "
                    f"({new_size // 1024} KB)"
                )

            self._prune_to_budget(needed=new_size)
            event_dir = self._next_event_dir()

            total_bytes = 0
            for i, (_, data) in enumerate(frames):
                (event_dir / f"frame_{i:04d}.jpg").write_bytes(data)
                total_bytes += len(data)

            manifest = {
                "timestamp": datetime.now().isoformat(),
                "scanner_id": self._id,
                "event_type": event_type,
                "reason": reason,
                "pre_frames_count": len(frames),
                "post_frames_count": 0,
                "total_bytes": total_bytes,
            }
            (event_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                f"[{self._id}] pre-evento guardado: {event_dir.name} "
                f"({len(frames)} frames, {total_bytes // 1024} KB)"
            )

            # Activar grabación post-evento
            if self._post_seconds > 0:
                with self._lock:
                    self._post_dir = event_dir
                    self._post_until = time.monotonic() + self._post_seconds
                    self._post_idx = 0

        except Exception as exc:
            logger.error(f"[{self._id}] error al guardar evento: {exc}")

    def _finalize_manifest(self, event_dir: Path) -> None:
        """Actualiza el manifest con el conteo final de frames post-evento."""
        try:
            manifest_path = event_dir / "manifest.json"
            if not manifest_path.exists():
                return
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            post_files = sorted(event_dir.glob("post_*.jpg"))
            post_bytes = sum(f.stat().st_size for f in post_files)
            m["post_frames_count"] = len(post_files)
            m["total_bytes"] = m.get("total_bytes", 0) + post_bytes
            manifest_path.write_text(
                json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(
                f"[{self._id}] post-evento finalizado: {event_dir.name} "
                f"(+{len(post_files)} frames post, {post_bytes // 1024} KB)"
            )
        except Exception as exc:
            logger.error(f"[{self._id}] error actualizando manifest post: {exc}")

    def _next_event_dir(self) -> Path:
        today = datetime.now().strftime("%d-%m-%Y")
        n = 1
        while True:
            d = self._dir / f"{today}_STOP_{n}"
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                return d
            n += 1

    def _prune_to_budget(self, needed: int) -> None:
        if not self._dir.exists():
            return
        dirs = sorted(
            (d for d in self._dir.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
        )
        total = sum(_dir_size(d) for d in dirs)
        target = self._max_disk_bytes - needed

        for d in dirs:
            if total <= target:
                break
            sz = _dir_size(d)
            try:
                shutil.rmtree(d)
                total -= sz
                logger.info(
                    f"[EventRecorder] poda: eliminado {d.name} ({sz // 1024} KB)"
                )
            except Exception as exc:
                logger.error(
                    f"[EventRecorder] error al borrar {d.name}: {exc}"
                )
