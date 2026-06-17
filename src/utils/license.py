"""
Módulo de licencia Metalconf. Validación HMAC + detección de rollback de reloj.
"""
import hashlib
import hmac
import struct
import time
from datetime import datetime
from pathlib import Path

from src.utils.paths import app_root

# Secret XOR-obfuscado para que no aparezca como ASCII en el binario
_K_ENC = bytes([
    0xe8, 0xc6, 0x9c, 0x86, 0xd5, 0xe9, 0x97, 0xe5,
    0xdd, 0xf7, 0x92, 0x84, 0xce, 0xe7, 0x90, 0x81,
])
_K = bytes(b ^ 0xA5 for b in _K_ENC)

_LIC_FILE   = "config/calibration.key"
_STATE_FILE = "config/calibration_data.bin"
_MAGIC      = b'\xca\xfe\xba\xbe'


def _sig(payload: str) -> str:
    return hmac.new(_K, payload.encode("ascii"), hashlib.sha256).hexdigest()[:8].upper()


def generate_key(year: int, month: int) -> str:
    """Genera una clave válida para el mes indicado."""
    payload = f"{year:04d}{month:02d}"
    return f"MFC-{payload}-{_sig(payload)}"


def validate_key(key: str) -> bool:
    """True si la clave es válida y cubre el mes actual o posterior."""
    parts = key.strip().upper().split("-")
    if len(parts) != 3 or parts[0] != "MFC":
        return False
    try:
        payload = parts[1]
        if len(payload) != 6:
            return False
        year  = int(payload[:4])
        month = int(payload[4:6])
        if not (2024 <= year <= 2099 and 1 <= month <= 12):
            return False
        if not hmac.compare_digest(_sig(payload), parts[2]):
            return False
        now = datetime.now()
        return (year, month) >= (now.year, now.month)
    except Exception:
        return False


def load_license_file() -> str:
    p = app_root() / _LIC_FILE
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_license_file(key: str) -> None:
    p = app_root() / _LIC_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key.strip().upper(), encoding="utf-8")


def is_licensed() -> bool:
    """True si la licencia es válida y el reloj no fue retrocedido."""
    if _detect_clock_rollback():
        return False
    return validate_key(load_license_file())


# ── Detección de rollback de reloj ────────────────────────────────────

def _state_path() -> Path:
    return app_root() / _STATE_FILE


def _read_saved_ts() -> "float | None":
    p = _state_path()
    if not p.exists():
        return None
    try:
        data = p.read_bytes()
        if len(data) < 16 or data[:4] != _MAGIC:
            return None
        ts       = struct.unpack_from("<d", data, 4)[0]
        checksum = struct.unpack_from("<I", data, 12)[0]
        if (sum(data[:12]) & 0xFFFFFFFF) != checksum:
            return None
        return ts
    except Exception:
        return None


def _write_saved_ts(ts: float) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        header   = _MAGIC + struct.pack("<d", ts)
        checksum = sum(header) & 0xFFFFFFFF
        p.write_bytes(header + struct.pack("<I", checksum))
    except Exception:
        pass


def _detect_clock_rollback() -> bool:
    """True si el reloj del sistema fue retrocedido."""
    last = _read_saved_ts()
    now  = time.time()
    if last is not None and now < last - 120:   # 2 min de tolerancia
        return True
    if last is None or now > last:
        _write_saved_ts(now)
    return False


def update_heartbeat() -> None:
    """Llamar periódicamente para mantener el estado actualizado contra rollback."""
    _write_saved_ts(time.time())
