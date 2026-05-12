"""
Mapeo entre IDs internos de modelos y nombres de visualización para el operador.

Los IDs internos (modelo_A, modelo_B) son los usados en archivos, patrones y
configuración. Los nombres de display son los visibles en toda la UI.
"""

# Orden de aparición en combos (el primero es el default)
_DISPLAY: dict[str, str] = {
    "modelo_A": "Esterilla",
    "modelo_B": "Microperforado",
}

_INTERNAL: dict[str, str] = {v: k for k, v in _DISPLAY.items()}

KNOWN_MODELS: list[str] = list(_DISPLAY.keys())
DISPLAY_NAMES: list[str] = list(_DISPLAY.values())


def to_display(internal_id: str) -> str:
    """'modelo_A' → 'Esterilla'. Devuelve el ID original si no tiene mapeo."""
    return _DISPLAY.get(internal_id, internal_id)


def to_internal(display_name: str) -> str:
    """'Esterilla' → 'modelo_A'. Devuelve el nombre original si no tiene mapeo."""
    return _INTERNAL.get(display_name, display_name)
