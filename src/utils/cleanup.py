"""
Poda de artefactos de análisis en data/output.

`data/output` acumula overlays OK/NOK, exports y reportes de diagnóstico sin
ningún límite (a diferencia de data/events, que EventRecorder poda solo).
Este módulo aplica dos reglas sobre las entradas de primer nivel:

  1. Antigüedad: se eliminan entradas cuyo contenido más reciente supera
     `keep_days` días.
  2. Presupuesto: si el total sigue por encima de `max_gb`, se eliminan las
     entradas más viejas primero hasta entrar en presupuesto.

Por defecto opera en modo dry-run: informa qué borraría sin tocar nada.
"""

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    scanned: int = 0
    total_bytes: int = 0
    deleted: list[tuple[Path, int]] = field(default_factory=list)
    freed_bytes: int = 0
    applied: bool = False


def _entry_stats(entry: Path) -> tuple[int, float]:
    """Devuelve (bytes totales, mtime más reciente) de un archivo o carpeta."""
    if entry.is_file():
        st = entry.stat()
        return st.st_size, st.st_mtime
    size = 0
    newest = entry.stat().st_mtime
    for f in entry.rglob("*"):
        try:
            if f.is_file():
                st = f.stat()
                size += st.st_size
                newest = max(newest, st.st_mtime)
        except OSError:
            continue
    return size, newest


def prune_output(
    root: Path,
    keep_days: float = 30.0,
    max_gb: float = 2.0,
    apply: bool = False,
) -> CleanupReport:
    """Poda las entradas de primer nivel de `root` por edad y presupuesto.

    keep_days <= 0 desactiva la regla de antigüedad; max_gb <= 0 desactiva
    la de presupuesto. Con `apply=False` solo informa (dry-run).
    """
    report = CleanupReport(applied=apply)
    if not root.exists():
        return report

    now = time.time()
    entries: list[tuple[Path, int, float]] = []  # (path, bytes, newest_mtime)
    for entry in sorted(root.iterdir()):
        size, newest = _entry_stats(entry)
        entries.append((entry, size, newest))
        report.scanned += 1
        report.total_bytes += size

    def _delete(entry: Path, size: int) -> bool:
        if apply:
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError as exc:
                logger.error("cleanup: no se pudo borrar %s: %s", entry, exc)
                return False
        report.deleted.append((entry, size))
        report.freed_bytes += size
        return True

    # Regla 1: antigüedad
    survivors: list[tuple[Path, int, float]] = []
    for entry, size, newest in entries:
        if keep_days > 0 and (now - newest) > keep_days * 86400:
            if not _delete(entry, size):
                survivors.append((entry, size, newest))
        else:
            survivors.append((entry, size, newest))

    # Regla 2: presupuesto total (más viejo primero)
    if max_gb > 0:
        budget = int(max_gb * 1_000_000_000)
        remaining = sum(s for _, s, _ in survivors)
        for entry, size, _ in sorted(survivors, key=lambda t: t[2]):
            if remaining <= budget:
                break
            if _delete(entry, size):
                remaining -= size

    return report
