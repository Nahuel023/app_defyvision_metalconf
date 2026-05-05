"""
Prueba de la capa PLCClient + IOMap.
Uso: python test_io_map.py
"""

from pathlib import Path
from src.plc.client import PLCClient
from src.plc.io_map import IOMap


def main() -> None:
    client = PLCClient("192.168.10.175", port=502)
    if not client.connect():
        print("[ERROR] No se pudo conectar al PLC")
        return

    io = IOMap(client, Path("config/io_map.yaml"))

    print("\n=== Señales disponibles ===")
    for name in sorted(io._index):
        sig_type, addr = io._index[name]
        print(f"  {name:<35} {sig_type}  offset={addr}")

    print("\n=== Lectura de entradas ===")
    for scanner_id in io.scanner_ids():
        cfg = io.scanner_config(scanner_id)
        for sig_name in cfg.get("inputs", {}):
            signal = f"{scanner_id}.{sig_name}"
            val = io.read(signal)
            print(f"  {signal:<35} = {val}")

    print("\n=== Lectura de salidas (estado actual) ===")
    for scanner_id in io.scanner_ids():
        cfg = io.scanner_config(scanner_id)
        for sig_name in cfg.get("outputs", {}):
            signal = f"{scanner_id}.{sig_name}"
            val = io.read(signal)
            print(f"  {signal:<35} = {val}")

    client.disconnect()
    print("\n[OK] Test completo.")


if __name__ == "__main__":
    main()
