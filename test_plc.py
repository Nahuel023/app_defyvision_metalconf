"""
PLC Coolmay L02M32T - Modbus TCP
IP: 192.168.10.175  Puerto: 502

Uso:
  python test_plc.py            → scan + menu interactivo
  python test_plc.py --scan     → solo muestra estado X e Y
"""

import argparse
import sys

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    print("Instala pymodbus: pip install pymodbus")
    sys.exit(1)

PLC_IP   = "192.168.10.175"
PLC_PORT = 502
UNIT_ID  = 1

X_COUNT = 16
Y_COUNT = 16

# CX3G (Coolmay): Salidas Y → Coils 0x3300, Entradas X → Discrete Inputs 0x3400
X_BASE = 0x3400
Y_BASE = 0x3300


def connect():
    client = ModbusTcpClient(PLC_IP, port=PLC_PORT, timeout=5)
    if not client.connect():
        print(f"[ERROR] No se pudo conectar a {PLC_IP}:{PLC_PORT}")
        sys.exit(1)
    print(f"[OK] Conectado a {PLC_IP}:{PLC_PORT}")
    return client


def print_inputs(client):
    r = client.read_discrete_inputs(X_BASE, count=X_COUNT, device_id=UNIT_ID)
    if r.isError():
        r = client.read_coils(X_BASE, count=X_COUNT, device_id=UNIT_ID)
    if r.isError():
        print(f"[ERROR] read X: {r}")
        return
    print("\n--- Entradas X ---")
    for i, v in enumerate(r.bits[:X_COUNT]):
        print(f"  X{i}: {'1 (ON)' if v else '0 (off)'}")


def print_outputs(client):
    r = client.read_coils(Y_BASE, count=Y_COUNT, device_id=UNIT_ID)
    if r.isError():
        print(f"[ERROR] read Y: {r}")
        return
    print("\n--- Salidas Y ---")
    for i, v in enumerate(r.bits[:Y_COUNT]):
        print(f"  Y{i}: {'1 (ON)' if v else '0 (off)'}")


def set_output(client, y_num, value: bool):
    r = client.write_coil(Y_BASE + y_num, value, device_id=UNIT_ID)
    if r.isError():
        print(f"[ERROR] write Y{y_num}: {r}")
    else:
        print(f"[OK] Y{y_num} → {'ON' if value else 'OFF'}")


def scan(client):
    """Escanea distintos rangos para encontrar el mapeo X/Y del PLC."""
    print("\n--- Scan coils FC01 (salidas Y) ---")
    for base in [0, 0x400, 0x500, 0x800, 0x1000, 0x3300, 0x3400]:
        r = client.read_coils(base, count=16, device_id=UNIT_ID)
        if not r.isError():
            vals = [int(b) for b in r.bits[:16]]
            marker = " ← Y_BASE" if base == Y_BASE else ""
            print(f"  0x{base:04X}: {vals}{marker}")
        else:
            print(f"  0x{base:04X}: ERROR")

    print("\n--- Scan discrete inputs FC02 (entradas X) ---")
    for base in [0, 0x400, 0x500, 0x800, 0x1000, 0x3300, 0x3400]:
        r = client.read_discrete_inputs(base, count=16, device_id=UNIT_ID)
        if not r.isError():
            vals = [int(b) for b in r.bits[:16]]
            marker = " ← X_BASE" if base == X_BASE else ""
            print(f"  0x{base:04X}: {vals}{marker}")
        else:
            print(f"  0x{base:04X}: ERROR")


def menu(client):
    while True:
        print("\n========= MENU PLC =========")
        print("  1. Leer entradas X")
        print("  2. Leer salidas Y")
        print("  3. Activar salida Yn")
        print("  4. Desactivar salida Yn")
        print("  5. Salir")
        print("============================")
        op = input("Opcion: ").strip()

        if op == "1":
            print_inputs(client)
        elif op == "2":
            print_outputs(client)
        elif op in ("3", "4"):
            try:
                n = int(input("Numero de salida (ej: 0 para Y0): "))
                set_output(client, n, op == "3")
            except ValueError:
                print("Numero invalido.")
        elif op == "5":
            break
        else:
            print("Opcion invalida.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true",
                        help="Escanea rangos Modbus para encontrar el mapeo")
    args = parser.parse_args()

    client = connect()
    try:
        if args.scan:
            scan(client)
        else:
            print_inputs(client)
            print_outputs(client)
            menu(client)
    finally:
        client.close()
        print("[OK] Desconectado.")


if __name__ == "__main__":
    main()
