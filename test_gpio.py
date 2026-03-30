"""
GPIO test script for industrial mini PC - ITE IT8625 Super I/O.

Hardware confirmed: ITE IT8625, Simple I/O base = 0xA00
  0xA00 = GP1x data, 0xA01 = GP2x data, 0xA02 = GP3x data (connector GPIO0-GPIO7)
  0xA03 = GP4x data, 0xA04 = GP5x data, 0xA05 = GP6x data

Direction registers (via LDN7 config mode, index/data at 0x2E/0x2F):
  LDN7 reg 0xC0 = GP1x output enable (bit=1 -> output)
  LDN7 reg 0xC1 = GP2x output enable
  LDN7 reg 0xC2 = GP3x output enable  <-- connector GPIO0-GPIO7
  LDN7 reg 0xC3 = GP4x output enable
  LDN7 reg 0xC4 = GP5x output enable
  LDN7 reg 0xC5 = GP6x output enable

Prerequisites:
  1. install_inpout_driver.bat run AS ADMINISTRATOR (once)
  2. Run this script AS ADMINISTRATOR
"""

import ctypes
import sys
import os
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPOUT_DLL = os.path.join(_SCRIPT_DIR, "InpOutBinaries_1501", "x64", "inpoutx64.dll")

SIO_INDEX = 0x2E
SIO_DATA  = 0x2F

# Physical connector GPIO0-GPIO7 -> ITE GP30-GP37 -> data at 0xA02, dir at LDN7 reg 0xC2
GPIO_DATA_PORT = 0xA02
GPIO_DIR_REG   = 0xC2   # LDN7 register for GP3x output enable (1=output, 0=input)

GPIO_PINS = {f"GPIO{i}": i for i in range(8)}  # GPIO0=bit0 ... GPIO7=bit7


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

    # --- ITE IT8625 config mode via LPC index/data ---

    def ite_enter_config(self):
        """Enter ITE Super I/O config mode."""
        for b in [0x87, 0x01, 0x55, 0x55]:
            self.out(SIO_INDEX, b)

    def ite_exit_config(self):
        """Exit ITE Super I/O config mode."""
        self.out(SIO_INDEX, 0x02)
        self.out(SIO_DATA,  0x02)

    def ite_select_ldn(self, ldn: int):
        """Select a Logical Device Number."""
        self.out(SIO_INDEX, 0x07)
        self.out(SIO_DATA,  ldn)

    def ite_read_reg(self, reg: int) -> int:
        self.out(SIO_INDEX, reg)
        return self.inp(SIO_DATA)

    def ite_write_reg(self, reg: int, value: int):
        self.out(SIO_INDEX, reg)
        self.out(SIO_DATA,  value)

    def ite_set_gpio_direction(self, dir_reg: int, bit: int, output: bool):
        """Set a GPIO bit as output (True) or input (False) in LDN7."""
        self.ite_enter_config()
        self.ite_select_ldn(0x07)  # LDN7 = GPIO
        current = self.ite_read_reg(dir_reg)
        if output:
            new_val = current | (1 << bit)
        else:
            new_val = current & ~(1 << bit)
        self.ite_write_reg(dir_reg, new_val)
        self.ite_exit_config()
        return current, new_val


def dump_state(drv: InpOutDriver):
    print("\n--- Raw ports 0xA00-0xA05 ---")
    for port in range(0xA00, 0xA06):
        val = drv.inp(port)
        print(f"  0x{port:03X}: 0x{val:02X}  {val:08b}b")

    print("\n--- Registro de direccion GP3x (LDN7 reg 0xC2) ---")
    drv.ite_enter_config()
    drv.ite_select_ldn(0x07)
    dir_val = drv.ite_read_reg(GPIO_DIR_REG)
    drv.ite_exit_config()
    print(f"  0xC2 = 0x{dir_val:02X}  {dir_val:08b}b")
    print(f"  (bit=1 -> output, bit=0 -> input)")
    for i in range(8):
        direction = "OUTPUT" if (dir_val >> i) & 1 else "input "
        data_val  = drv.read_bit(GPIO_DATA_PORT, i)
        print(f"  GPIO{i}: {direction}  valor={data_val}")


def blink(drv: InpOutDriver, gpio_name: str, cycles: int = 3, delay: float = 0.5):
    bit = GPIO_PINS[gpio_name]
    print(f"\n--- Blink {gpio_name} (bit {bit}, puerto 0x{GPIO_DATA_PORT:03X}) ---")

    # 1. Configure as output via config mode
    orig, new = drv.ite_set_gpio_direction(GPIO_DIR_REG, bit, output=True)
    print(f"  Direccion: 0x{orig:02X} -> 0x{new:02X} (bit {bit} = OUTPUT)")

    # 2. Blink
    for i in range(cycles):
        drv.write_bit(GPIO_DATA_PORT, bit, 0)
        print(f"  {gpio_name} -> LOW   (0xA02 = 0x{drv.inp(GPIO_DATA_PORT):02X})")
        time.sleep(delay)
        drv.write_bit(GPIO_DATA_PORT, bit, 1)
        print(f"  {gpio_name} -> HIGH  (0xA02 = 0x{drv.inp(GPIO_DATA_PORT):02X})")
        time.sleep(delay)

    # 3. Restore original direction
    drv.ite_enter_config()
    drv.ite_select_ldn(0x07)
    drv.ite_write_reg(GPIO_DIR_REG, orig)
    drv.ite_exit_config()
    print(f"  Direccion restaurada a 0x{orig:02X}")


def main():
    is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    print("=== Test GPIO - DEFYVISION Metalconf (ITE IT8625) ===")
    print(f"Admin: {'SI' if is_admin else 'NO - ejecutar como Administrador'}")

    try:
        drv = InpOutDriver()
        print("InpOutx64 cargado OK")
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    dump_state(drv)

    if "--blink" in sys.argv:
        idx = sys.argv.index("--blink")
        target = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "GPIO1"
        if target not in GPIO_PINS:
            print(f"ERROR: {target} no valido. Usar GPIO0-GPIO7.")
            sys.exit(1)
        blink(drv, target)
        print("\nBlink completo.")

    print("\nTest finalizado.")


if __name__ == "__main__":
    main()
