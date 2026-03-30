"""
GPIO test script - ITE IT8625 Super I/O diagnostic.

Prerequisites:
  1. install_inpout_driver.bat AS ADMINISTRATOR (once)
  2. Run AS ADMINISTRATOR
"""

import ctypes, sys, os, time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPOUT_DLL  = os.path.join(_SCRIPT_DIR, "InpOutBinaries_1501", "x64", "inpoutx64.dll")
SIO_INDEX   = 0x2E
SIO_DATA    = 0x2F


class InpOutDriver:
    def __init__(self, dll_path=INPOUT_DLL):
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"DLL no encontrada: {dll_path}")
        self._dll = ctypes.WinDLL(dll_path)

    def inp(self, port):
        return self._dll.Inp32(port) & 0xFF

    def out(self, port, value):
        self._dll.Out32(port, value & 0xFF)

    def read_bit(self, port, bit):
        return (self.inp(port) >> bit) & 1

    def write_bit(self, port, bit, value):
        cur = self.inp(port)
        self.out(port, (cur | (1 << bit)) if value else (cur & ~(1 << bit)))

    # ITE config mode
    def ite_enter(self):
        for b in [0x87, 0x01, 0x55, 0x55]:
            self.out(SIO_INDEX, b)

    def ite_exit(self):
        self.out(SIO_INDEX, 0x02)
        self.out(SIO_DATA,  0x02)

    def ite_select_ldn(self, ldn):
        self.out(SIO_INDEX, 0x07)
        self.out(SIO_DATA,  ldn)

    def ite_read(self, reg):
        self.out(SIO_INDEX, reg)
        return self.inp(SIO_DATA)

    def ite_write(self, reg, value):
        self.out(SIO_INDEX, reg)
        self.out(SIO_DATA,  value)


# ── Diagnostics ──────────────────────────────────────────────────────────────

def diag_simba(drv):
    """Read the actual Simple I/O Base Address from LDN7."""
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    hi = drv.ite_read(0x60)
    lo = drv.ite_read(0x61)
    drv.ite_exit()
    simba = (hi << 8) | lo
    print(f"\n--- SIMBA (LDN7 reg 0x60/0x61) ---")
    print(f"  Base address: 0x{simba:04X}")
    print(f"  Esperado:     0xA000  {'OK' if simba == 0xA000 else 'DIFERENTE - ajustar mapeo'}")
    return simba


def diag_direction_regs(drv):
    """Read and show all GPIO direction registers (LDN7 0xC0-0xC7)."""
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    print("\n--- Registros de direccion LDN7 (0xC0-0xC7) ---")
    regs = {}
    for reg in range(0xC0, 0xC8):
        val = drv.ite_read(reg)
        regs[reg] = val
        group = reg - 0xC0 + 1
        print(f"  0x{reg:02X} (GP{group}x): 0x{val:02X}  {val:08b}b")
    drv.ite_exit()
    return regs


def diag_direction_write_verify(drv, reg=0xC2, bit=1):
    """Write a bit to a direction reg and read it back to confirm it sticks."""
    print(f"\n--- Verificacion escritura reg 0x{reg:02X} bit {bit} ---")

    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    original = drv.ite_read(reg)
    drv.ite_exit()
    print(f"  Antes:  0x{original:02X}")

    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    new_val = original | (1 << bit)
    drv.ite_write(reg, new_val)
    drv.ite_exit()

    # Read back after exiting config mode
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    readback = drv.ite_read(reg)
    drv.ite_exit()
    print(f"  Escrito: 0x{new_val:02X}  Leido de vuelta: 0x{readback:02X}  {'OK persiste' if readback == new_val else 'NO persiste - registro de solo lectura o incorrecto'}")

    # Restore
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    drv.ite_write(reg, original)
    drv.ite_exit()
    return readback == new_val


def diag_writable_ports(drv, base=0xA00, count=8):
    """Find which ports actually accept writes by toggling bit 0 and reading back."""
    print(f"\n--- Scan de puertos escribibles (0x{base:03X} - 0x{base+count-1:03X}) ---")
    print(f"  (escribe bit 0, lee de vuelta, restaura)")
    for port in range(base, base + count):
        original = drv.inp(port)
        toggled  = original ^ 0x01          # flip bit 0
        drv.out(port, toggled)
        readback = drv.inp(port)
        drv.out(port, original)             # restore
        writable = readback == toggled
        flag = "ESCRIBIBLE" if writable else "solo-lectura"
        print(f"  0x{port:03X}: orig=0x{original:02X}  escrito=0x{toggled:02X}  leido=0x{readback:02X}  -> {flag}")


def diag_ldn7_full(drv):
    """Dump full LDN7 config registers 0x20-0xFF."""
    print("\n--- LDN7 registros completos (solo no-cero) ---")
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    for reg in range(0x20, 0x100):
        val = drv.ite_read(reg)
        if val not in (0x00, 0xFF):
            print(f"  Reg 0x{reg:02X}: 0x{val:02X}  {val:08b}b")
    drv.ite_exit()


def blink(drv, port, bit, cycles=3, delay=0.5, dir_reg=0xC2):
    """Blink a GPIO: set direction OUTPUT in config space, then toggle data port."""
    print(f"\n--- Blink puerto=0x{port:03X} bit={bit} dir_reg=0x{dir_reg:02X} ---")

    # Set direction as output
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    orig_dir = drv.ite_read(dir_reg)
    drv.ite_write(dir_reg, orig_dir | (1 << bit))
    # Verify
    set_dir = drv.ite_read(dir_reg)
    drv.ite_exit()
    print(f"  Direccion: 0x{orig_dir:02X} -> 0x{set_dir:02X}")

    for i in range(cycles):
        drv.write_bit(port, bit, 0)
        low_read = drv.inp(port)
        time.sleep(delay)
        drv.write_bit(port, bit, 1)
        high_read = drv.inp(port)
        time.sleep(delay)
        print(f"  Ciclo {i+1}: LOW=0x{low_read:02X}  HIGH=0x{high_read:02X}  {'dato cambia OK' if low_read != high_read else 'dato NO cambia'}")

    # Restore direction
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    drv.ite_write(dir_reg, orig_dir)
    drv.ite_exit()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=== Test GPIO - ITE IT8625 Diagnostic ===")
    print(f"Admin: {'SI' if ctypes.windll.shell32.IsUserAnAdmin() else 'NO - ejecutar como Administrador'}")

    try:
        drv = InpOutDriver()
        print("InpOutx64 cargado OK")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if "--blink" in sys.argv:
        # Usage: --blink <port_hex> <bit> [dir_reg_hex]
        idx = sys.argv.index("--blink")
        port    = int(sys.argv[idx+1], 16) if len(sys.argv) > idx+1 else 0xA02
        bit     = int(sys.argv[idx+2])     if len(sys.argv) > idx+2 else 1
        dir_reg = int(sys.argv[idx+3], 16) if len(sys.argv) > idx+3 else 0xC2
        blink(drv, port, bit, dir_reg=dir_reg)
    else:
        simba = diag_simba(drv)
        diag_direction_regs(drv)
        diag_direction_write_verify(drv)
        diag_writable_ports(drv)
        diag_ldn7_full(drv)

    print("\nTest finalizado.")


if __name__ == "__main__":
    main()
