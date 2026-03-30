"""
GPIO test script for industrial mini PC (Smooth/Super I/O via InpOutx64).

Prerequisites:
  1. Run install_inpout_driver.bat AS ADMINISTRATOR (once per machine)
  2. Run this script AS ADMINISTRATOR

GPIO map from vendor connector diagram:
  GPIO0-GPIO7 all on port 0xA02, bits 0-7
  Direction (IN/OUT) configurable per pin - all treated as bidirectional for testing.
"""

import ctypes
import sys
import os
import time

# Path to inpoutx64.dll relative to this script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPOUT_DLL = os.path.join(_SCRIPT_DIR, "InpOutBinaries_1501", "x64", "inpoutx64.dll")

# --- GPIO map from vendor connector diagram ---
# Physical connector: GPIO0-GPIO7 all on port 0xA02
# is_output=True means the BIOS/config sets it as output; False = input.
# All are treated as bidirectional for testing purposes.
GPIO_MAP = {
    # name: (port_addr, bit, is_output)
    "GPIO0": (0xA02, 0, True),
    "GPIO1": (0xA02, 1, True),
    "GPIO2": (0xA02, 2, True),
    "GPIO3": (0xA02, 3, True),
    "GPIO4": (0xA02, 4, True),
    "GPIO5": (0xA02, 5, True),
    "GPIO6": (0xA02, 6, True),
    "GPIO7": (0xA02, 7, True),
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

    def read_all(self) -> dict:
        return {
            name: self._drv.read_bit(port, bit)
            for name, (port, bit, _) in GPIO_MAP.items()
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

    # 2. Lectura de todos los GPIO
    print("\n--- Estado de GPIO0-GPIO7 (puerto 0xA02) ---")
    for name, val in gpio.read_all().items():
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
