"""
Resolución del directorio raíz de la aplicación.

Funciona tanto en desarrollo (python -m src.main) como en ejecutable
congelado por PyInstaller (metalconf.exe).

En modo frozen, run_production.py ya ejecutó os.chdir(project_root) antes
de importar cualquier módulo src, por lo que Path.cwd() es el root correcto.
En modo desarrollo, se sube dos niveles desde este archivo (src/utils/paths.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

_cached: Path | None = None


def app_root() -> Path:
    """Devuelve la raíz del proyecto de forma confiable en Python y en .exe."""
    global _cached
    if _cached is not None:
        return _cached
    if getattr(sys, "frozen", False):
        # Frozen: run_production.py ya hizo os.chdir(project_root)
        _cached = Path.cwd().resolve()
    else:
        # Desarrollo: este archivo está en src/utils/paths.py
        _cached = Path(__file__).resolve().parent.parent.parent
    return _cached
