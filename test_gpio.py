"""
GPIO test script for industrial mini PC (Smooth/Super I/O via InpOutx64).

Prerequisites:
  1. Run install_inpout_driver.bat AS ADMINISTRATOR (once per machine)
  2. Run this script AS ADMINISTRATOR

GPIO map from CONFIG.INI (setup_prov_pc/IBC_SMOOTH/CONFIG.INI)
"""

import ctypes
import sys
import os
import time

# Path to inpoutx64.dll relative to this script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPOUT_DLL = os.path.join(_SCRIPT_DIR, "InpOutBinaries_1501", "x64", "inpoutx64.dll")

# --- GPIO map from CONFIG.INI ---
GPIO_MAP = {
    # name: (port_addr, bit, is_output)
    "GPI1": (0xA00, 0, False),
    "GPI2": (0xA00, 2, False),
    "GPI3": (0xA00, 7, False),
    "GPI4": (0xA02, 0, False),
    "GPI5": (0xA00, 4, False),
    "GPI6": (0xA03, 4, False),
    "GPI7": (0xA04, 3, False),
    "GPO1": (0xA03, 1, True),
    "GPO2": (0xA05, 5, True),
    "GPO3": (0xA00, 1, True),
    "GPO4": (0xA03, 6, True),
    "GPO5": (0xA04, 5, True),
    "GPO6": (0xA04, 4, True),
    "GPO7": (0xA03, 0, True),
}


class InpOutDriver:
    def __init__(self, dll_path: str = INPOUT_DLL):
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"DLL no encontrada: {dll_path}")
        self._dll = ctypes.WinDLL(dll_path)

    def inp(self, port: int) -> int:
        return self._dll.Inp32(port) & 0xFF

    def out(self, port: int, value: int):
        self._dll.Out32(port, value & 0xFF)

    def read_bit(self, port: int, bit: int) -> int:
        return (self.inp(port) >> bit) & 1

    def write_bit(self, port: int, bit: int, value: int):
        current = self.inp(port)
        new_val = (current | (1 << bit)) if value else (current & ~(1 << bit))
        self.out(port, new_val)


class GPIOController:
    def __init__(self, driver: InpOutDriver):
        self._drv = driver

    def read(self, name: str) -> int:
        port, bit, _ = GPIO_MAP[name]
        return self._drv.read_bit(port, bit)

    def write(self, name: str, value: int):
        port, bit, is_output = GPIO_MAP[name]
        if not is_output:
            raise ValueError(f"{name} es entrada, no se puede escribir.")
        self._drv.write_bit(port, bit, value)

    def read_all_inputs(self) -> dict:
        return {
            name: self._drv.read_bit(port, bit)
            for name, (port, bit, is_output) in GPIO_MAP.items()
            if not is_output
        }

    def dump_raw_ports(self):
        ports = sorted({port for port, _, _ in GPIO_MAP.values()})
        print("\n--- Valores raw de puertos ---")
        for port in ports:
            val = self._drv.inp(port)
            print(f"  Puerto 0x{port:03X}: 0x{val:02X}  ({val:08b}b)")


def main():
    is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    print("=== Test GPIO - DEFYVISION Metalconf ===")
    print(f"Admin: {'SI' if is_admin else 'NO (puede fallar - ejecutar como Administrador)'}")
    print(f"DLL:   {INPOUT_DLL}")

    try:
        driver = InpOutDriver()
        print("InpOutx64 cargado OK\n")
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        print("Ejecutar primero: install_inpout_driver.bat (como Administrador)")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR al cargar DLL: {e}")
        sys.exit(1)

    gpio = GPIOController(driver)

    # 1. Valores raw de todos los puertos GPIO
    gpio.dump_raw_ports()

    # 2. Lectura de todas las entradas
    print("\n--- Estado de entradas (GPI) ---")
    for name, val in gpio.read_all_inputs().items():
        print(f"  {name}: {val}")

    # 3. Blink en GPO1 si se pasa --blink
    if "--blink" in sys.argv:
        target = sys.argv[sys.argv.index("--blink") + 1] if len(sys.argv) > sys.argv.index("--blink") + 1 else "GPO1"
        print(f"\n--- Blink en {target} (3 ciclos, 0.5s) ---")
        for i in range(3):
            gpio.write(target, 1)
            print(f"  {target} -> HIGH")
            time.sleep(0.5)
            gpio.write(target, 0)
            print(f"  {target} -> LOW")
            time.sleep(0.5)
        print("Blink completo.")

    print("\nTest finalizado.")


if __name__ == "__main__":
    main()
