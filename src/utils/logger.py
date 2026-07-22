import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configura el logging de la aplicacion.

    Robustez 24/7:
      - SIEMPRE se agrega un RotatingFileHandler a disco, aunque config/app.yaml
        no defina log_file. En un build empaquetado sin consola (PyInstaller
        --windowed) `sys.stdout` es None y sin archivo no quedaria NINGUN
        registro de una planta que corre dia y noche. El log en disco es la
        unica forma de diagnosticar un incidente despues de que ocurre.
      - El StreamHandler solo se agrega si hay un stdout real (evita que
        logging intente escribir sobre None en el build sin consola).
      - setup_logging() nunca lanza: si algo del parseo de config falla, cae a
        una configuracion minima segura en vez de tumbar el arranque.
    """
    try:
        app_cfg = _load_app_logging_config()
        if level == "INFO":
            level = str(app_cfg.get("log_level", level))
        if log_file is None:
            log_file = app_cfg.get("log_file")
        # Fallback obligatorio: si nadie configuro un archivo, usar uno por
        # defecto para no quedarnos sin rastro en produccion.
        if not log_file:
            log_file = str(_default_log_file())

        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        handlers: list[logging.Handler] = []

        # Consola solo si existe un stdout real (no en build --windowed).
        if getattr(sys, "stdout", None) is not None:
            handlers.append(logging.StreamHandler(sys.stdout))

        file_handler = _build_file_handler(log_file, app_cfg, fmt)
        if file_handler is not None:
            handlers.append(file_handler)

        # Si por lo que sea no quedo ningun handler, garantizar al menos uno
        # (NullHandler) para que logging no instale su handler de ultimo recurso.
        if not handlers:
            handlers.append(logging.NullHandler())

        logging.basicConfig(
            level=getattr(logging, str(level).upper(), logging.INFO),
            format=fmt,
            handlers=handlers,
            force=True,
        )
    except Exception:
        # El logging jamas debe impedir que la aplicacion arranque. Caer a una
        # configuracion minima y seguir.
        try:
            logging.basicConfig(level=logging.INFO, force=True)
        except Exception:
            pass


def _build_file_handler(
    log_file: str, app_cfg: dict, fmt: str
) -> logging.Handler | None:
    """Crea el RotatingFileHandler; devuelve None si el disco no lo permite."""
    try:
        path = Path(log_file)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(path),
            maxBytes=int(app_cfg.get("log_max_bytes", 20_000_000)),
            backupCount=int(app_cfg.get("log_backup_count", 10)),
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(fmt))
        return handler
    except Exception:
        # Disco lleno, permisos, ruta invalida: preferible perder el archivo
        # antes que impedir el arranque de produccion.
        return None


def _default_log_file() -> Path:
    """Ruta de log por defecto, junto al ejecutable cuando esta empaquetado."""
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path.cwd()
    except Exception:
        base = Path.cwd()
    return base / "logs" / "app.log"


def _load_app_logging_config() -> dict:
    path = Path("config/app.yaml")
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
