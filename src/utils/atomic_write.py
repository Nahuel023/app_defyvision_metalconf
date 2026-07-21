"""Escrituras atomicas para configuracion y calibraciones criticas.

El archivo temporal se crea en el mismo directorio que el destino para que
``os.replace`` sea atomico tambien en Windows y no cruce volumenes.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: str | Path,
    payload: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii),
    )
