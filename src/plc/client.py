"""
Wrapper thread-safe sobre pymodbus para el PLC Coolmay CX3G.

Convenciones de dirección:
  - Entradas X  → Discrete Inputs,  base 0x3400 + offset
  - Salidas  Y  → Coils,            base 0x3300 + offset

Todas las operaciones devuelven None / False ante cualquier error
y registran el fallo en el log — nunca lanzan excepciones al caller.
"""

import logging
import threading
import time
from typing import Optional

from pymodbus.client import ModbusTcpClient

logger = logging.getLogger(__name__)

_X_BASE = 0x3400
_Y_BASE = 0x3300
_RECONNECT_COOLDOWN_S = 5.0   # segundos mínimos entre intentos de reconexión


class PLCClient:
    def __init__(
        self,
        ip: str,
        port: int = 502,
        unit_id: int = 1,
        timeout: float = 3.0,
    ) -> None:
        self._ip = ip
        self._port = port
        self._unit_id = unit_id
        self._timeout = timeout

        self._client: Optional[ModbusTcpClient] = None
        self._lock = threading.Lock()
        self._connected = False
        self._last_reconnect_attempt: float = 0.0

    # ------------------------------------------------------------------
    # Conexión pública
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        with self._lock:
            return self._connect_locked()

    def disconnect(self) -> None:
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._connected = False
            logger.info("PLC disconnected")

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Lectura de entradas X (Discrete Inputs)
    # ------------------------------------------------------------------

    def read_input(self, offset: int) -> Optional[bool]:
        """Lee una entrada X. Devuelve None si hay error de comunicación."""
        with self._lock:
            if not self._ensure_connected():
                return None
            try:
                r = self._client.read_discrete_inputs(
                    _X_BASE + offset, count=1, device_id=self._unit_id
                )
                if r.isError():
                    self._on_error(f"read_input X{offset}: {r}")
                    return None
                return bool(r.bits[0])
            except Exception as exc:
                self._on_error(f"read_input X{offset}: {exc}")
                return None

    def read_inputs_batch(self, offset: int, count: int) -> Optional[list[bool]]:
        """Lee varios bits de entrada en una sola transacción Modbus."""
        with self._lock:
            if not self._ensure_connected():
                return None
            try:
                r = self._client.read_discrete_inputs(
                    _X_BASE + offset, count=count, device_id=self._unit_id
                )
                if r.isError():
                    self._on_error(f"read_inputs_batch X{offset}+{count}: {r}")
                    return None
                return [bool(b) for b in r.bits[:count]]
            except Exception as exc:
                self._on_error(f"read_inputs_batch: {exc}")
                return None

    # ------------------------------------------------------------------
    # Lectura de salidas Y (Coils)
    # ------------------------------------------------------------------

    def read_coil(self, offset: int) -> Optional[bool]:
        """Lee el estado actual de una salida Y."""
        with self._lock:
            if not self._ensure_connected():
                return None
            try:
                r = self._client.read_coils(
                    _Y_BASE + offset, count=1, device_id=self._unit_id
                )
                if r.isError():
                    self._on_error(f"read_coil Y{offset}: {r}")
                    return None
                return bool(r.bits[0])
            except Exception as exc:
                self._on_error(f"read_coil Y{offset}: {exc}")
                return None

    # ------------------------------------------------------------------
    # Escritura de salidas Y (Coils)
    # ------------------------------------------------------------------

    def write_coil(self, offset: int, value: bool) -> bool:
        """Escribe una salida Y. Devuelve False si hay error."""
        with self._lock:
            if not self._ensure_connected():
                return False
            try:
                r = self._client.write_coil(
                    _Y_BASE + offset, value, device_id=self._unit_id
                )
                if r.isError():
                    self._on_error(f"write_coil Y{offset}={value}: {r}")
                    return False
                return True
            except Exception as exc:
                self._on_error(f"write_coil Y{offset}={value}: {exc}")
                return False

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> bool:
        """Llama a _connect_locked si no está conectado, con cooldown."""
        if self._connected:
            return True
        now = time.monotonic()
        if now - self._last_reconnect_attempt < _RECONNECT_COOLDOWN_S:
            return False
        logger.info("PLC reconnecting...")
        return self._connect_locked()

    def _connect_locked(self) -> bool:
        """Conecta al PLC. Debe llamarse con self._lock adquirido."""
        self._last_reconnect_attempt = time.monotonic()
        try:
            self._client = ModbusTcpClient(
                self._ip, port=self._port, timeout=self._timeout
            )
            ok = self._client.connect()
            self._connected = ok
            if ok:
                logger.info(f"PLC connected: {self._ip}:{self._port}")
            else:
                logger.warning(f"PLC connection failed: {self._ip}:{self._port}")
            return ok
        except Exception as exc:
            self._connected = False
            logger.error(f"PLC connect error: {exc}")
            return False

    def _on_error(self, msg: str) -> None:
        logger.error(f"PLC: {msg}")
        self._connected = False
