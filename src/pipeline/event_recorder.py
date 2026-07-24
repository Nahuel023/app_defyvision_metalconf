"""
Buffer circular de evidencia pre-evento por scanner.

Graba `pre_seconds` antes de la parada y `post_seconds` después.
Frames originales JPEG en RAM (sin overlay). Nunca supera `max_disk_gb`.
"""

import json
import logging
import queue
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from src.utils.atomic_write import atomic_write_json

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
        self._retrigger_idx: int = 0
        self._worker_stop = threading.Event()
        self._task_q: "queue.Queue[tuple[str, tuple] | None]" = queue.Queue(maxsize=8)
        self._worker = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"{self._id}-event-writer",
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def add_frame(self, frame: np.ndarray,
                  overlay: "Optional[np.ndarray]" = None) -> None:
        """
        Comprime el frame como JPEG y lo añade al buffer o al post-evento.
        Si se pasa `overlay`, también guarda el overlay en post_{idx}_overlay.jpg.
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

        # ── Determinar modo actual (bajo lock mínimo) ─────────────────
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
                    self._retrigger_idx = 0

        # ── Post-evento activo: escribir directo a disco ──────────────
        if post_write is not None:
            post_dir, idx = post_write
            try:
                (post_dir / f"post_{idx:04d}.jpg").write_bytes(data)
                if overlay is not None:
                    ok2, buf2 = cv2.imencode(
                        ".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_q]
                    )
                    if ok2:
                        (post_dir / f"post_{idx:04d}_overlay.jpg").write_bytes(bytes(buf2))
            except Exception as exc:
                logger.error(f"[{self._id}] post-evento write: {exc}")
            return  # no buffering mientras se graba post-evento

        # ── Post-evento expiró: actualizar manifest en background ─────
        if finalize_dir is not None:
            self._enqueue_task("finalize", finalize_dir)

        # ── Buffer circular normal ────────────────────────────────────
        with self._lock:
            self._buf.append((ts, data))
            self._buf_bytes += len(data)
            cutoff = ts - self._pre_seconds
            while self._buf and self._buf[0][0] < cutoff:
                self._buf_bytes -= len(self._buf.popleft()[1])
            while self._buf and self._buf_bytes > self._max_buf_bytes:
                self._buf_bytes -= len(self._buf.popleft()[1])

    def is_post_event_active(self) -> bool:
        """True si estamos dentro de la ventana de grabación post-evento."""
        with self._lock:
            return self._post_dir is not None and time.monotonic() < self._post_until

    def get_post_event_dir(self) -> "Optional[Path]":
        """Devuelve la carpeta del post-evento activo, o None si no hay."""
        with self._lock:
            if self._post_dir is not None and time.monotonic() < self._post_until:
                return self._post_dir
            return None

    def flush_event(
        self,
        event_type: str,
        reason: str = "",
        *,
        trigger_frame: Optional[np.ndarray] = None,
        trigger_overlay: Optional[np.ndarray] = None,
    ) -> Optional[Path]:
        """
        Vuelca el buffer pre-evento a disco en hilo background y activa
        la grabación post-evento. Ignora llamadas dentro del cooldown.
        """
        trigger_data: Optional[bytes] = None
        trigger_overlay_data: Optional[bytes] = None
        if trigger_frame is not None:
            ok, encoded = cv2.imencode(
                ".jpg", trigger_frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_q]
            )
            if ok:
                trigger_data = bytes(encoded)
        if trigger_overlay is not None:
            ok, encoded = cv2.imencode(
                ".jpg", trigger_overlay, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_q]
            )
            if ok:
                trigger_overlay_data = bytes(encoded)

        now_m = time.monotonic()
        with self._lock:
            # Si el evento vuelve a dispararse durante la ventana post-evento,
            # no hay buffer pre disponible. En ese caso extendemos la cola de
            # post-evento para no perder evidencia del segundo disparo.
            if self._post_dir is not None and now_m < self._post_until:
                if self._post_seconds > 0:
                    self._post_until = max(self._post_until, now_m + self._post_seconds)
                self._retrigger_idx += 1
                retrigger_idx = self._retrigger_idx
                event_dir = self._post_dir
                self._enqueue_task(
                    "retrigger",
                    event_dir,
                    retrigger_idx,
                    event_type,
                    reason,
                    trigger_data,
                    trigger_overlay_data,
                    time.time(),
                )
                logger.warning(
                    f"[{self._id}] evento {event_type} durante post-evento activo: "
                    "se conserva el nuevo disparador y se extiende la ventana"
                )
                return event_dir

            if now_m - self._last_flush < self._flush_cooldown:
                return None
            self._last_flush = now_m

            if not self._buf and trigger_data is None:
                return None
            frames = list(self._buf)
            self._buf.clear()
            self._buf_bytes = 0

        # El loop agrega el frame antes de inspeccionarlo. Si ese mismo JPEG ya
        # quedó como último pre-frame, quitarlo del bloque previo: se conserva
        # una sola vez con el nombre inequívoco trigger.jpg.
        if trigger_data is not None and frames and frames[-1][1] == trigger_data:
            frames.pop()

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            event_dir = self._next_event_dir()
        except Exception as exc:
            logger.error("[%s] no se pudo crear carpeta de evento: %s", self._id, exc)
            return None

        if self._post_seconds > 0:
            with self._lock:
                self._post_dir = event_dir
                self._post_until = now_m + self._post_seconds
                self._post_idx = 0
                self._retrigger_idx = 0

        if not self._enqueue_task(
            "flush",
            frames,
            event_type,
            reason,
            event_dir,
            trigger_data,
            trigger_overlay_data,
            time.time(),
            False,
        ):
            logger.error(
                "[%s] no se pudo encolar flush_event; se descarta evidencia de %s",
                self._id,
                event_type,
            )
            with self._lock:
                if self._post_dir == event_dir:
                    self._post_dir = None
                    self._post_idx = 0
                    self._retrigger_idx = 0
            return None
        return event_dir

    def finish_post_event(self) -> None:
        """Finaliza la ventana post y actualiza el manifest aun sin otro frame."""
        with self._lock:
            event_dir = self._post_dir
            self._post_dir = None
            self._post_idx = 0
            self._retrigger_idx = 0
        if event_dir is not None:
            self._enqueue_task("finalize", event_dir)

    def close(self) -> None:
        with self._lock:
            finalize_dir = self._post_dir
            self._post_dir = None
            self._post_idx = 0
            self._retrigger_idx = 0
        if finalize_dir is not None:
            self._enqueue_task("finalize", finalize_dir)
        self._worker_stop.set()
        try:
            self._task_q.put_nowait(None)
        except queue.Full:
            logger.warning("[%s] event writer saturado durante cierre", self._id)
        self._worker.join(timeout=5.0)
        if self._worker.is_alive():
            logger.error("[%s] event writer no termino limpiamente", self._id)

    # ------------------------------------------------------------------
    # Disco — hilo background
    # ------------------------------------------------------------------

    def _flush_sync(
        self,
        frames: list[tuple[float, bytes]],
        event_type: str,
        reason: str,
        event_dir: Optional[Path] = None,
        trigger_data: Optional[bytes] = None,
        trigger_overlay_data: Optional[bytes] = None,
        trigger_timestamp: Optional[float] = None,
        activate_post: bool = True,
    ) -> None:
        """Escribe frames pre-evento + manifest. Luego activa post-evento."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)

            trigger_size = len(trigger_data or b"") + len(trigger_overlay_data or b"")
            new_size = sum(len(d) for _, d in frames) + trigger_size

            # Truncar si supera el presupuesto total (conservar frames recientes)
            if new_size > self._max_disk_bytes > 0:
                kept: list[tuple[float, bytes]] = []
                kept_size = trigger_size
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

            self._prune_to_budget(needed=new_size, exclude=event_dir)
            if event_dir is None:
                event_dir = self._next_event_dir()

            total_bytes = 0
            for i, (_, data) in enumerate(frames):
                (event_dir / f"frame_{i:04d}.jpg").write_bytes(data)
                total_bytes += len(data)

            if trigger_data is not None:
                (event_dir / "trigger.jpg").write_bytes(trigger_data)
                total_bytes += len(trigger_data)
            if trigger_overlay_data is not None:
                (event_dir / "trigger_overlay.jpg").write_bytes(trigger_overlay_data)
                total_bytes += len(trigger_overlay_data)

            manifest = {
                "timestamp": datetime.fromtimestamp(
                    trigger_timestamp or time.time()
                ).isoformat(),
                "scanner_id": self._id,
                "event_type": event_type,
                "reason": reason,
                "pre_frames_count": len(frames),
                "trigger_frames_count": int(trigger_data is not None),
                "trigger_frame": "trigger.jpg" if trigger_data is not None else "",
                "trigger_overlay": (
                    "trigger_overlay.jpg" if trigger_overlay_data is not None else ""
                ),
                "post_frames_count": 0,
                "frames_count": len(frames) + int(trigger_data is not None),
                "total_bytes": total_bytes,
            }
            atomic_write_json(event_dir / "manifest.json", manifest, ensure_ascii=False)
            logger.info(
                f"[{self._id}] pre-evento guardado: {event_dir.name} "
                f"({len(frames)} frames, {total_bytes // 1024} KB)"
            )

            # Activar grabación post-evento
            if activate_post and self._post_seconds > 0:
                with self._lock:
                    self._post_dir = event_dir
                    self._post_until = time.monotonic() + self._post_seconds
                    self._post_idx = 0
                    self._retrigger_idx = 0

        except Exception as exc:
            logger.error(f"[{self._id}] error al guardar evento: {exc}")

    def _save_retrigger(
        self,
        event_dir: Path,
        idx: int,
        event_type: str,
        reason: str,
        trigger_data: Optional[bytes],
        trigger_overlay_data: Optional[bytes],
        timestamp: float,
    ) -> None:
        """Conserva un segundo disparador ocurrido durante la ventana post."""
        try:
            raw_name = f"retrigger_{idx:04d}.jpg"
            overlay_name = f"retrigger_{idx:04d}_overlay.jpg"
            if trigger_data is not None:
                (event_dir / raw_name).write_bytes(trigger_data)
            if trigger_overlay_data is not None:
                (event_dir / overlay_name).write_bytes(trigger_overlay_data)

            manifest_path = event_dir / "manifest.json"
            if not manifest_path.exists():
                return
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = manifest.setdefault("additional_triggers", [])
            entries.append({
                "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
                "event_type": event_type,
                "reason": reason,
                "frame": raw_name if trigger_data is not None else "",
                "overlay": overlay_name if trigger_overlay_data is not None else "",
            })
            if trigger_data is not None:
                manifest["trigger_frames_count"] = int(
                    manifest.get("trigger_frames_count", 0)
                ) + 1
                manifest["frames_count"] = int(manifest.get("frames_count", 0)) + 1
            atomic_write_json(manifest_path, manifest, ensure_ascii=False)
        except Exception as exc:
            logger.error("[%s] error guardando disparador adicional: %s", self._id, exc)

    def _finalize_manifest(self, event_dir: Path) -> None:
        """Actualiza el manifest con el conteo final de frames post-evento."""
        try:
            manifest_path = event_dir / "manifest.json"
            if not manifest_path.exists():
                return
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            all_post_files = sorted(event_dir.glob("post_*.jpg"))
            raw_post_files = [f for f in all_post_files if not f.stem.endswith("_overlay")]
            post_bytes = sum(f.stat().st_size for f in all_post_files)
            pre_bytes = sum(f.stat().st_size for f in event_dir.glob("frame_*.jpg"))
            trigger_files = [
                path for path in event_dir.glob("trigger*.jpg")
                if path.exists()
            ]
            trigger_files.extend(event_dir.glob("retrigger_*.jpg"))
            trigger_bytes = sum(path.stat().st_size for path in trigger_files)
            m["post_frames_count"] = len(raw_post_files)
            m["frames_count"] = (
                int(m.get("pre_frames_count", 0))
                + int(m.get("trigger_frames_count", 0))
                + len(raw_post_files)
            )
            # Recalcular, no acumular: finalize puede repetirse durante un cierre.
            m["total_bytes"] = pre_bytes + trigger_bytes + post_bytes
            atomic_write_json(manifest_path, m, ensure_ascii=False)
            logger.info(
                f"[{self._id}] post-evento finalizado: {event_dir.name} "
                f"(+{len(raw_post_files)} frames post, {post_bytes // 1024} KB)"
            )
        except Exception as exc:
            logger.error(f"[{self._id}] error actualizando manifest post: {exc}")

    def _enqueue_task(self, task_type: str, *args) -> bool:
        try:
            self._task_q.put((task_type, args), timeout=0.5)
            return True
        except queue.Full:
            logger.error("[%s] event writer saturado: %s", self._id, task_type)
            return False

    def _worker_loop(self) -> None:
        while True:
            try:
                item = self._task_q.get(timeout=0.2)
            except queue.Empty:
                if self._worker_stop.is_set():
                    break
                continue

            if item is None:
                self._task_q.task_done()
                break

            task_type, args = item
            try:
                if task_type == "flush":
                    self._flush_sync(*args)
                elif task_type == "retrigger":
                    self._save_retrigger(*args)
                elif task_type == "finalize":
                    self._finalize_manifest(*args)
                else:
                    logger.error("[%s] tarea de evento desconocida: %s", self._id, task_type)
            except Exception as exc:
                logger.error("[%s] error en tarea %s: %s", self._id, task_type, exc)
            finally:
                self._task_q.task_done()

    def _next_event_dir(self) -> Path:
        today = datetime.now().strftime("%d-%m-%Y")
        n = 1
        while True:
            d = self._dir / f"{today}_STOP_{n}"
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                return d
            n += 1

    def _prune_to_budget(
        self, needed: int, exclude: Optional[Path] = None
    ) -> None:
        if not self._dir.exists():
            return
        dirs = sorted(
            (
                d for d in self._dir.iterdir()
                if d.is_dir() and (exclude is None or d != exclude)
            ),
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
