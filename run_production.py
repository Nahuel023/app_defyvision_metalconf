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
        candidate = Path(sys.executable).parent.parent.parent
        _log(f"exe: {sys.executable}")
        _log(f"candidate root: {candidate}")
        _log(f"io_map exists: {(candidate / 'config' / 'io_map.yaml').exists()}")
        if (candidate / "config" / "io_map.yaml").exists():
            os.chdir(candidate)
        _log(f"cwd: {os.getcwd()}")

    sys.argv = ["metalconf", "run"]

    from src.main import main
    sys.exit(main())

except Exception:
    _log(traceback.format_exc())
    raise
