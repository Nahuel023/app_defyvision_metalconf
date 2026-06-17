import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    app_cfg = _load_app_logging_config()
    if level == "INFO":
        level = str(app_cfg.get("log_level", level))
    if log_file is None:
        log_file = app_cfg.get("log_file")

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=int(app_cfg.get("log_max_bytes", 20_000_000)),
                backupCount=int(app_cfg.get("log_backup_count", 10)),
                encoding="utf-8",
            )
        )
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers, force=True)


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
