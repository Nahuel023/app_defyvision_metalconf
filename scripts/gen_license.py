#!/usr/bin/env python3
"""
Generador de claves de licencia Metalconf — USO INTERNO DEFYMOTION.

Uso:
  python scripts/gen_license.py              # clave para el mes actual
  python scripts/gen_license.py next         # clave para el mes siguiente
  python scripts/gen_license.py 2025 8       # clave para agosto 2025
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.license import generate_key
from datetime import date


def main() -> None:
    if len(sys.argv) == 3:
        y, m = int(sys.argv[1]), int(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "next":
        today = date.today()
        if today.month == 12:
            y, m = today.year + 1, 1
        else:
            y, m = today.year, today.month + 1
    else:
        today = date.today()
        y, m = today.year, today.month

    key = generate_key(y, m)
    print(f"\nClave de activación {y:04d}/{m:02d}:")
    print(f"\n  {key}\n")


if __name__ == "__main__":
    main()
