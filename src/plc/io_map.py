"""
Capa semántica sobre PLCClient.

Carga config/io_map.yaml y expone operaciones por nombre de señal:
  io.read("scanner_1.punch_sensor")   → bool | None
  io.write("scanner_1.stop_line", True) → bool

El código de control nunca usa offsets o bases hexadecimales directamente.
"""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from src.plc.client import PLCClient

logger = logging.getLogger(__name__)


class IOMap:
    def __init__(self, client: PLCClient, config_path: Path) -> None:
        self._client = client
        self._config: dict[str, Any] = self._load(config_path)
        self._index: dict[str, tuple[str, int]] = self._build_index()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def read(self, signal: str) -> Optional[bool]:
        """
        Lee una señal por nombre completo ("scanner_1.punch_sensor").
        Devuelve None si hay error de comunicación.
        """
        sig_type, address = self._resolve(signal)
        if sig_type == "input":
            return self._client.read_input(address)
        return self._client.read_coil(address)

    def write(self, signal: str, value: bool) -> bool:
        """
        Escribe una señal de salida por nombre completo.
        Lanza ValueError si el nombre corresponde a una entrada.
        """
        sig_type, address = self._resolve(signal)
        if sig_type != "output":
            raise ValueError(f"'{signal}' es una entrada — no se puede escribir")
        return self._client.write_coil(address, value)

    def scanner_ids(self) -> list[str]:
        """Devuelve los IDs de scanners definidos en la config."""
        return [k for k in self._config if k != "plc"]

    def scanner_config(self, scanner_id: str) -> dict[str, Any]:
        """Devuelve la sección completa de un scanner (camera_index, model, etc.)."""
        return self._config[scanner_id]

    @property
    def plc_config(self) -> dict[str, Any]:
        return self._config["plc"]

    def signals(self) -> dict[str, tuple[str, int]]:
        """Devuelve una copia del índice {signal_full_name: (type, address)}."""
        return dict(self._index)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"io_map.yaml inválido: {path}")
        return data

    def _build_index(self) -> dict[str, tuple[str, int]]:
        """Pre-construye {signal_full_name: (type, address)} para lookup O(1)."""
        index: dict[str, tuple[str, int]] = {}
        for scanner_id, cfg in self._config.items():
            if scanner_id == "plc" or not isinstance(cfg, dict):
                continue
            for name, offset in cfg.get("inputs", {}).items():
                index[f"{scanner_id}.{name}"] = ("input", int(offset))
            for name, offset in cfg.get("outputs", {}).items():
                index[f"{scanner_id}.{name}"] = ("output", int(offset))
        return index

    def _resolve(self, signal: str) -> tuple[str, int]:
        try:
            return self._index[signal]
        except KeyError:
            known = ", ".join(sorted(self._index))
            raise KeyError(f"Señal desconocida: '{signal}'. Disponibles: {known}")
