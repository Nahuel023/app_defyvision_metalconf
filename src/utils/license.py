"""Compatibilidad de licencia con operacion permanentemente habilitada.

Este modulo se conserva porque forma parte del contrato de imports y del build
protegido con Cython. La aplicacion ya no tiene vencimientos, bloqueos por fecha,
validacion mensual ni deteccion de retroceso del reloj.
"""


def generate_key(year: int, month: int) -> str:
    """API historica: devuelve un identificador legible, sin efecto operativo."""
    return f"MFC-{int(year):04d}{int(month):02d}-PERMANENTE"


def validate_key(key: str) -> bool:
    """La operacion es permanente; ninguna clave puede bloquearla."""
    return True


def load_license_file() -> str:
    """API historica conservada para instalaciones o integraciones anteriores."""
    return ""


def save_license_file(key: str) -> None:
    """No-op: ya no se persisten claves ni estados de licencia."""
    return None


def is_licensed() -> bool:
    """La aplicacion esta habilitada permanentemente, sin consultar fecha o disco."""
    return True


def update_heartbeat() -> None:
    """No-op historico: ya no existe deteccion de rollback del reloj."""
    return None
