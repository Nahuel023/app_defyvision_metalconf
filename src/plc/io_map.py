"""
Capa semántica sobre PLCClient.

Carga config/io_map.yaml y expone operaciones por nombre de señal:
  io.read("scanner_1.punch_sensor")   → bool | None
  io.write("scanner_1.stop_line", True) → bool

El código de control nunca usa offsets o bases hexadecimales directamente.
"""

import logging
from pathlib import Path
import time
from typing import Any, Optional

import yaml

from src.plc.client import PLCClient

logger = logging.getLogger(__name__)


class IOMap:
    def __init__(self, client: PLCClient, config_path: Path,
                 disable_outputs: bool = False) -> None:
        self._client = client
        self._disable_outputs = disable_outputs
        self._config: dict[str, Any] = self._load(config_path)
        self._index: dict[str, tuple[str, int]] = self._build_index()
        # Modo seguro POR SCANNER (cada uno con su propia maneta) — bloqueados
        # por defecto hasta que el operador/la maneta los libere.
        self._safe_mode: dict[str, bool] = {sid: True for sid in self.scanner_ids()}

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

    def write_batch(self, signals: list[tuple[str, bool]]) -> bool:
        """Escribe varios outputs en una sola transacción si son contiguos.

        Hace fallback a escrituras individuales si los offsets no son contiguos
        o si alguna señal es una entrada.
        """
        if not signals:
            return True
        if self._disable_outputs:
            for sig, val in signals:
                logger.debug(f"[no-plc-outputs] {sig}={val} (suprimido)")
            return True
        resolved = []
        any_blocked = False
        for sig, val in signals:
            sig_type, addr = self._resolve(sig)
            if sig_type != "output":
                raise ValueError(f"'{sig}' es una entrada — no se puede escribir")
            if sig.endswith(".solenoid") and val and self._safe_mode.get(sig.split(".")[0], True):
                logger.warning(f"[SAFETY] Escritura bloqueada (modo seguro ON): {sig}=True")
                any_blocked = True
                continue
            resolved.append((addr, val))
        if not resolved:
            # Si llegamos aca con signals no vacio, fue porque TODAS las señales
            # quedaron bloqueadas por modo seguro — debe reportarse como False,
            # igual que write() en el caso equivalente (no como exito silencioso).
            return not any_blocked
        resolved.sort(key=lambda x: x[0])
        # Verificar si son contiguos
        addrs = [a for a, _ in resolved]
        if addrs == list(range(addrs[0], addrs[0] + len(addrs))):
            values = [v for _, v in resolved]
            return self._client.write_coils_batch(addrs[0], values)
        # Fallback: individuales
        ok = True
        for addr, val in resolved:
            ok &= self._client.write_coil(addr, val)
        return ok

    def write(self, signal: str, value: bool) -> bool:
        """
        Escribe una señal de salida por nombre completo.
        Lanza ValueError si el nombre corresponde a una entrada.
        Retorna True sin escribir si disable_outputs=True.
        """
        sig_type, address = self._resolve(signal)
        if sig_type != "output":
            raise ValueError(f"'{signal}' es una entrada — no se puede escribir")
        if self._disable_outputs:
            logger.debug(f"[no-plc-outputs] {signal}={value} (suprimido)")
            return True
        if signal.endswith(".solenoid") and value and self._safe_mode.get(signal.split(".")[0], True):
            logger.warning(
                f"[SAFETY] Escritura bloqueada (modo seguro ON): {signal}=True"
            )
            return False
        return self._client.write_coil(address, value)

    def write_critical(
        self,
        signal: str,
        value: bool,
        *,
        retries: int = 5,
        retry_delay_s: float = 0.15,
        verify: bool = True,
    ) -> bool:
        """Escritura reforzada para salidas críticas.

        Reintenta la escritura varias veces y, opcionalmente, verifica por lectura
        que el coil haya quedado en el valor pedido. Pensado especialmente para
        cortes de solenoide ante FAULT/MACHINE FAULT.
        """
        sig_type, address = self._resolve(signal)
        if sig_type != "output":
            raise ValueError(f"'{signal}' es una entrada — no se puede escribir")
        if self._disable_outputs:
            logger.debug(f"[no-plc-outputs] {signal}={value} (suprimido)")
            return True
        if signal.endswith(".solenoid") and value and self._safe_mode.get(signal.split(".")[0], True):
            logger.warning(
                f"[SAFETY] Escritura critica bloqueada (modo seguro ON): {signal}=True"
            )
            return False

        attempts = max(1, int(retries))
        last_read: Optional[bool] = None
        for attempt in range(1, attempts + 1):
            ok = self._client.write_coil(address, value)
            if ok:
                if not verify:
                    return True
                last_read = self._client.read_coil(address)
                if last_read == value:
                    return True
                logger.warning(
                    "[CRITICAL-OUTPUT] verificacion inconsistente %s=%s "
                    "(lectura=%s, intento=%d/%d)",
                    signal, value, last_read, attempt, attempts,
                )
            else:
                logger.warning(
                    "[CRITICAL-OUTPUT] escritura fallida %s=%s "
                    "(intento=%d/%d)",
                    signal, value, attempt, attempts,
                )

            if attempt < attempts:
                # Fuerza un reconnect inmediato sin esperar al siguiente ciclo del
                # poller; en una parada real preferimos insistir agresivamente.
                self._client.connect()
                time.sleep(max(0.0, retry_delay_s))

        logger.error(
            "[CRITICAL-OUTPUT] no se pudo confirmar %s=%s tras %d intentos "
            "(ultima lectura=%s)",
            signal, value, attempts, last_read,
        )
        return False

    def set_safe_mode(self, scanner_id: str, enabled: bool) -> None:
        """Activa o desactiva el bloqueo de solenoide de UN scanner puntual."""
        self._safe_mode[scanner_id] = bool(enabled)

    def get_safe_mode(self, scanner_id: str) -> bool:
        return self._safe_mode.get(scanner_id, True)

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
