"""
GPIO test script for industrial mini PC (Smooth/Super I/O via InpOutx64).

Prerequisites:
  1. Run install_inpout_driver.bat AS ADMINISTRATOR (once per machine)
  2. Run this script AS ADMINISTRATOR

GPIO map from vendor connector diagram:
  GPIO0-GPIO7 all on port 0xA02, bits 0-7
  Direction register (OE) is typically at port 0xA03 for the same bank.
"""

import ctypes
import sys
import os
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPOUT_DLL = os.path.join(_SCRIPT_DIR, "InpOutBinaries_1501", "x64", "inpoutx64.dll")

# Physical connector: GPIO0-GPIO7 on port 0xA02
GPIO_MAP = {
    "GPIO0": (0xA02, 0),
    "GPIO1": (0xA02, 1),
    "GPIO2": (0xA02, 2),
    "GPIO3": (0xA02, 3),
    "GPIO4": (0xA02, 4),
    "GPIO5": (0xA02, 5),
    "GPIO6": (0xA02, 6),
    "GPIO7": (0xA02, 7),
}

# Super I/O index/data ports for chip identification and config
SIO_INDEX = 0x2E
SIO_DATA  = 0x2F


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

    def sio_read(self, reg: int) -> int:
        """Read a Super I/O register via index/data ports."""
        self.out(SIO_INDEX, reg)
        return self.inp(SIO_DATA)

    def sio_write(self, reg: int, value: int):
        """Write a Super I/O register via index/data ports."""
        self.out(SIO_INDEX, reg)
        self.out(SIO_DATA, value)


def identify_superio(drv: InpOutDriver):
    """Try to identify the Super I/O chip model."""
    print("\n--- Identificacion chip Super I/O ---")

    # Try ITE entry sequence: 0x87 0x01 0x55 0x55
    for b in [0x87, 0x01, 0x55, 0x55]:
        drv.out(SIO_INDEX, b)

    chip_id_hi = drv.sio_read(0x20)
    chip_id_lo = drv.sio_read(0x21)
    chip_id = (chip_id_hi << 8) | chip_id_lo
    print(f"  Secuencia ITE  -> Chip ID: 0x{chip_id:04X}")

    if 0x8600 <= chip_id <= 0x8FFF:
        print(f"  Detectado: ITE IT{chip_id:04X}")
    else:
        print(f"  No reconocido como ITE (o ya estaba en modo config)")

    # Exit ITE config mode
    drv.sio_write(0x02, 0x02)

    # Try Nuvoton entry sequence: 0x87 0x87
    drv.out(SIO_INDEX, 0x87)
    drv.out(SIO_INDEX, 0x87)
    chip_id_hi = drv.sio_read(0x20)
    chip_id_lo = drv.sio_read(0x21)
    chip_id2 = (chip_id_hi << 8) | chip_id_lo
    print(f"  Secuencia Nuvoton -> Chip ID: 0x{chip_id2:04X}")

    if 0xC000 <= chip_id2 <= 0xCFFF or 0xD000 <= chip_id2 <= 0xDFFF:
        print(f"  Detectado: Nuvoton NCT/W8{chip_id2:04X}")

    # Exit Nuvoton config mode
    drv.out(SIO_INDEX, 0xAA)

    return chip_id, chip_id2


def dump_raw_ports(drv: InpOutDriver):
    ports = sorted({port for port, _ in GPIO_MAP.values()})
    # Also dump direction-candidate ports around 0xA02
    candidate_ports = sorted(set(ports) | {0xA00, 0xA01, 0xA02, 0xA03, 0xA04, 0xA05})
    print("\n--- Valores raw de puertos (0xA00-0xA05) ---")
    for port in candidate_ports:
        val = drv.inp(port)
        print(f"  Puerto 0x{port:03X}: 0x{val:02X}  ({val:08b}b)")


def blink_with_direction(drv: InpOutDriver, gpio_name: str, dir_port: int):
    """
    Blink a GPIO by also setting its direction bit as OUTPUT in dir_port.
    For most Super I/O chips: direction bit=1 means OUTPUT.
    """
    port, bit = GPIO_MAP[gpio_name]
    print(f"\n--- Blink {gpio_name} con registro de direccion en 0x{dir_port:03X} ---")

    # Save original direction
    orig_dir = drv.inp(dir_port)
    print(f"  Direccion original (0x{dir_port:03X}): 0x{orig_dir:02X}")

    # Set bit as OUTPUT (bit=1 in direction register)
    drv.write_bit(dir_port, bit, 1)
    print(f"  Direccion configurada como OUTPUT bit {bit}")

    for i in range(3):
        drv.write_bit(port, bit, 0)
        print(f"  {gpio_name} -> LOW  (0x{drv.inp(port):02X})")
        time.sleep(0.5)
        drv.write_bit(port, bit, 1)
        print(f"  {gpio_name} -> HIGH (0x{drv.inp(port):02X})")
        time.sleep(0.5)

    # Restore direction
    drv.out(dir_port, orig_dir)
    print(f"  Direccion restaurada a 0x{orig_dir:02X}")


def main():
    is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    print("=== Test GPIO - DEFYVISION Metalconf ===")
    print(f"Admin: {'SI' if is_admin else 'NO (puede fallar - ejecutar como Administrador)'}")
    print(f"DLL:   {INPOUT_DLL}")

    try:
        drv = InpOutDriver()
        print("InpOutx64 cargado OK")
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR al cargar DLL: {e}")
        sys.exit(1)

    # 1. Identificar chip Super I/O
    identify_superio(drv)

    # 2. Dump raw ports
    dump_raw_ports(drv)

    # 3. Leer GPIO0-GPIO7
    print("\n--- Lectura GPIO0-GPIO7 (puerto 0xA02) ---")
    for name, (port, bit) in GPIO_MAP.items():
        val = drv.read_bit(port, bit)
        print(f"  {name} (bit {bit}): {val}")

    # 4. Blink con registro de direccion
    # --blink GPIO1            -> usa dir_port por defecto 0xA03
    # --blink GPIO1 0xA01      -> usa dir_port especificado
    if "--blink" in sys.argv:
        idx = sys.argv.index("--blink")
        target = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "GPIO1"
        dir_port_arg = sys.argv[idx + 2] if len(sys.argv) > idx + 2 else "0xA03"
        dir_port = int(dir_port_arg, 16)
        blink_with_direction(drv, target, dir_port)
        print("\nBlink completo.")

    print("\nTest finalizado.")


if __name__ == "__main__":
    main()
