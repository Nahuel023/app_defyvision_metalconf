"""
GPIO test script - ITE IT8625 Super I/O.

Root cause identified: LDN7 (GPIO) is inactive (active=0x00, SIMBA=0x0000).
Fix: activate LDN7 and set SIMBA=0xA000, then GPIO outputs work via 0xA00-0xA07.

GPIO map (CONFIG.INI - vendor confirmed):
  Inputs:  GPI1=0xA00.0  GPI2=0xA00.2  GPI3=0xA00.7  GPI4=0xA02.0  GPI5=0xA00.4
           GPI6=0xA03.4  GPI7=0xA04.3
  Outputs: GPO1=0xA03.1  GPO2=0xA05.5  GPO3=0xA00.1  GPO4=0xA03.6
           GPO5=0xA04.5  GPO6=0xA04.4  GPO7=0xA03.0

Prerequisites:
  1. install_inpout_driver.bat AS ADMINISTRATOR (once)
  2. Run AS ADMINISTRATOR
"""

import ctypes, sys, os, time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPOUT_DLL  = os.path.join(_SCRIPT_DIR, "InpOutBinaries_1501", "x64", "inpoutx64.dll")
SIO_INDEX   = 0x2E
SIO_DATA    = 0x2F

GPIO_MAP = {
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

# Direction registers in LDN7 (bit=1 means OUTPUT)
DIR_REGS = {
    0xA00: 0xC0,  # GP1x
    0xA01: 0xC1,  # GP2x
    0xA02: 0xC2,  # GP3x
    0xA03: 0xC3,  # GP4x
    0xA04: 0xC4,  # GP5x
    0xA05: 0xC5,  # GP6x
}


class InpOutDriver:
    def __init__(self, dll_path=INPOUT_DLL):
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"DLL no encontrada: {dll_path}")
        self._dll = ctypes.WinDLL(dll_path)

    def inp(self, port): return self._dll.Inp32(port) & 0xFF
    def out(self, port, value): self._dll.Out32(port, value & 0xFF)
    def read_bit(self, port, bit): return (self.inp(port) >> bit) & 1
    def write_bit(self, port, bit, value):
        cur = self.inp(port)
        self.out(port, (cur | (1 << bit)) if value else (cur & ~(1 << bit)))

    def ite_enter(self):
        for b in [0x87, 0x01, 0x55, 0x55]: self.out(SIO_INDEX, b)
    def ite_exit(self):
        self.out(SIO_INDEX, 0x02); self.out(SIO_DATA, 0x02)
    def ite_select_ldn(self, ldn):
        self.out(SIO_INDEX, 0x07); self.out(SIO_DATA, ldn)
    def ite_read(self, reg):
        self.out(SIO_INDEX, reg); return self.inp(SIO_DATA)
    def ite_write(self, reg, value):
        self.out(SIO_INDEX, reg); self.out(SIO_DATA, value)


def activate_ldn7(drv):
    """
    Activate LDN7 (GPIO) with SIMBA=0xA000.
    This is the missing step — without it, GPIO outputs have no effect.
    """
    drv.ite_enter()
    drv.ite_select_ldn(0x07)

    # Set SIMBA = 0xA000
    drv.ite_write(0x60, 0xA0)  # high byte
    drv.ite_write(0x61, 0x00)  # low byte

    # Activate LDN7
    drv.ite_write(0x30, 0x01)

    # Read back to verify
    active = drv.ite_read(0x30)
    hi     = drv.ite_read(0x60)
    lo     = drv.ite_read(0x61)
    drv.ite_exit()

    simba = (hi << 8) | lo
    print(f"  LDN7 activado: active=0x{active:02X}  SIMBA=0x{simba:04X}")
    return active == 0x01 and simba == 0xA000


def set_gpio_output(drv, port, bit):
    """Set a GPIO pin as output in LDN7 direction register."""
    dir_reg = DIR_REGS.get(port)
    if dir_reg is None:
        print(f"  Sin registro de direccion para puerto 0x{port:03X}")
        return
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    cur = drv.ite_read(dir_reg)
    drv.ite_write(dir_reg, cur | (1 << bit))
    new = drv.ite_read(dir_reg)
    drv.ite_exit()
    print(f"  Dir reg 0x{dir_reg:02X}: 0x{cur:02X} -> 0x{new:02X} (bit {bit} = OUTPUT)")


def read_all_gpio(drv):
    print("\n--- Estado GPIO ---")
    for name, (port, bit, is_out) in GPIO_MAP.items():
        val = drv.read_bit(port, bit)
        kind = "OUT" if is_out else "IN "
        print(f"  {name} [{kind}] 0x{port:03X}.{bit}: {val}")


def blink(drv, name, cycles=5, delay=0.5):
    port, bit, is_out = GPIO_MAP[name]
    if not is_out:
        print(f"{name} es entrada.")
        return

    print(f"\n--- Blink {name} (0x{port:03X} bit {bit}) ---")
    for i in range(cycles):
        drv.write_bit(port, bit, 0)
        print(f"  LOW   port=0x{drv.inp(port):02X}")
        time.sleep(delay)
        drv.write_bit(port, bit, 1)
        print(f"  HIGH  port=0x{drv.inp(port):02X}")
        time.sleep(delay)


def blink_loop(drv, name):
    port, bit, is_out = GPIO_MAP[name]
    print(f"Blink continuo {name} (0x{port:03X} bit {bit}) - Ctrl+C para parar")
    try:
        while True:
            drv.write_bit(port, bit, 0)
            time.sleep(1)
            drv.write_bit(port, bit, 1)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDetenido.")


def main():
    print("=== Test GPIO - ITE IT8625 ===")
    print(f"Admin: {'SI' if ctypes.windll.shell32.IsUserAnAdmin() else 'NO - ejecutar como Administrador'}")

    try:
        drv = InpOutDriver()
        print("InpOutx64 OK")
    except Exception as e:
        print(f"ERROR: {e}"); sys.exit(1)

    args = sys.argv[1:]

    if "--blink" in args:
        idx  = args.index("--blink")
        name = args[idx+1] if len(args) > idx+1 else "GPO4"
        loop = "--loop" in args

        print("\n1. Activando LDN7 (GPIO)...")
        ok = activate_ldn7(drv)
        if not ok:
            print("  ADVERTENCIA: verificacion fallo, continuando igual...")

        print("2. Configurando pin como output...")
        port, bit, _ = GPIO_MAP[name]
        set_gpio_output(drv, port, bit)

        print("3. Estado actual:")
        read_all_gpio(drv)

        if loop:
            blink_loop(drv, name)
        else:
            blink(drv, name)

    else:
        # Default: activate LDN7 and show state
        print("\n1. Activando LDN7 (GPIO)...")
        activate_ldn7(drv)
        read_all_gpio(drv)

    print("\nTest finalizado.")


if __name__ == "__main__":
    main()
