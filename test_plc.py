"""
PLC Coolmay L02M32T - Test MC Protocol (pymcprotocol)
IP: 192.168.10.175  Puerto: 5556

Uso:
  python test_plc.py            → scan + menu interactivo
  python test_plc.py --scan     → solo muestra estado X e Y
"""

import argparse
import sys

try:
    import pymcprotocol
except ImportError:
    print("Instala pymcprotocol: pip install pymcprotocol")
    sys.exit(1)

PLC_IP   = "192.168.10.175"
PLC_PORT = 5556

X_COUNT = 16
Y_COUNT = 16


def connect():
    plc = pymcprotocol.Type3E()
    plc.connect(PLC_IP, PLC_PORT)
    print(f"[OK] Conectado a {PLC_IP}:{PLC_PORT}")
    return plc


def print_inputs(plc):
    try:
        data = plc.batchread_bitunits(headdevice="X0", readsize=X_COUNT)
        print("\n--- Entradas X ---")
        for i, v in enumerate(data):
            print(f"  X{i}: {'1 (ON)' if v else '0 (off)'}")
    except Exception as e:
        print(f"[ERROR] read X: {e}")


def print_outputs(plc):
    try:
        data = plc.batchread_bitunits(headdevice="Y0", readsize=Y_COUNT)
        print("\n--- Salidas Y ---")
        for i, v in enumerate(data):
            print(f"  Y{i}: {'1 (ON)' if v else '0 (off)'}")
    except Exception as e:
        print(f"[ERROR] read Y: {e}")


def set_output(plc, y_num, value: bool):
    device = f"Y{y_num}"
    try:
        plc.batchwrite_bitunits(headdevice=device, values=[int(value)])
        print(f"[OK] {device} → {'ON' if value else 'OFF'}")
    except Exception as e:
        print(f"[ERROR] write {device}: {e}")


def menu(plc):
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
            print_inputs(plc)
        elif op == "2":
            print_outputs(plc)
        elif op in ("3", "4"):
            try:
                n = int(input("Numero de salida (ej: 0 para Y0): "))
                set_output(plc, n, op == "3")
            except ValueError:
                print("Numero invalido.")
        elif op == "5":
            break
        else:
            print("Opcion invalida.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true",
                        help="Solo muestra estado de X e Y")
    args = parser.parse_args()

    plc = connect()
    try:
        print_inputs(plc)
        print_outputs(plc)
        if not args.scan:
            menu(plc)
    finally:
        plc.close()
        print("[OK] Desconectado.")


if __name__ == "__main__":
    main()
