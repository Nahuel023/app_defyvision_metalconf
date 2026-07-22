import os
import sys
import traceback
from pathlib import Path

_log_path = Path(sys.executable).parent / "startup.log" if getattr(sys, "frozen", False) else Path("startup.log")

def _log(msg: str) -> None:
    with open(_log_path, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

try:
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent
        _log(f"exe: {sys.executable}")
        _log(f"candidate root: {candidate}")
        _log(f"io_map exists: {(candidate / 'config' / 'io_map.yaml').exists()}")
        if (candidate / "config" / "io_map.yaml").exists():
            os.chdir(candidate)
        _log(f"cwd: {os.getcwd()}")

    # Autotest seguro del artefacto congelado. Solo se habilita desde el script
    # de build; importa el camino critico pero sale antes de crear el sistema,
    # abrir camaras o conectarse/escribir al PLC.
    if os.environ.get("DEFYVISION_BUILD_SMOKE_TEST") == "1":
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import yaml  # noqa: F401
        from PyQt6.QtWidgets import QApplication  # noqa: F401
        from scipy.optimize import linear_sum_assignment  # noqa: F401
        from src.controller.system import InspectionSystem  # noqa: F401
        from src.inspection import inspect_frame  # noqa: F401
        from src.utils import license as license_module

        license_path = Path(license_module.__file__)
        if license_path.suffix.lower() != ".pyd":
            raise RuntimeError(
                f"Smoke test invalido: licencia no esta compilada con Cython ({license_path})"
            )
        _log(f"BUILD_SMOKE_OK license={license_path.name}")
        sys.exit(0)

    sys.argv = ["metalconf", "run"]

    from src.main import main
    sys.exit(main())

except Exception:
    _log(traceback.format_exc())
    raise
