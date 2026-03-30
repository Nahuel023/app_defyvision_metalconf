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


# ── Diagnostics ──────────────────────────────────────────────────────────────

def diag_global_regs(drv):
    """Read global config registers (no LDN selected) - pin mux lives here."""
    drv.ite_enter()
    print("\n--- Registros globales (sin LDN) reg 0x20-0x30 ---")
    for reg in range(0x20, 0x31):
        val = drv.ite_read(reg)
        print(f"  0x{reg:02X}: 0x{val:02X}  {val:08b}b")
    drv.ite_exit()


def diag_all_ldn_bases(drv):
    """Read base addresses of all LDNs (0x00-0x0F)."""
    print("\n--- Base addresses de todos los LDNs ---")
    for ldn in range(0x10):
        drv.ite_enter()
        drv.ite_select_ldn(ldn)
        active = drv.ite_read(0x30)   # activate register
        hi     = drv.ite_read(0x60)
        lo     = drv.ite_read(0x61)
        hi2    = drv.ite_read(0x62)
        lo2    = drv.ite_read(0x63)
        drv.ite_exit()
        base1 = (hi  << 8) | lo
        base2 = (hi2 << 8) | lo2
        if base1 or base2 or active:
            print(f"  LDN 0x{ldn:02X}: active=0x{active:02X}  base1=0x{base1:04X}  base2=0x{base2:04X}")


def diag_ldn7_regs(drv):
    """Dump all non-zero LDN7 registers 0x20-0xFF."""
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    print("\n--- LDN7 registros completos (no-cero) ---")
    for reg in range(0x20, 0x100):
        val = drv.ite_read(reg)
        if val not in (0x00, 0xFF):
            print(f"  0x{reg:02X}: 0x{val:02X}  {val:08b}b")
    drv.ite_exit()


def diag_writable_ports(drv, start=0xA00, count=16):
    """Find which ports accept writes."""
    print(f"\n--- Scan de puertos escribibles 0x{start:03X}-0x{start+count-1:03X} ---")
    for port in range(start, start + count):
        orig = drv.inp(port)
        drv.out(port, orig ^ 0x01)
        rb   = drv.inp(port)
        drv.out(port, orig)
        flag = "ESCRIBIBLE" if rb == (orig ^ 0x01) else "solo-lectura"
        if flag == "ESCRIBIBLE":
            print(f"  0x{port:03X}: 0x{orig:02X} -> {flag}")


def blink_loop(drv, port, bit):
    """Blink indefinitely until Ctrl+C."""
    print(f"\nBlink continuo puerto=0x{port:03X} bit={bit} - Ctrl+C para parar")
    try:
        while True:
            drv.write_bit(port, bit, 0)
            time.sleep(1)
            drv.write_bit(port, bit, 1)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDetenido.")


def try_mux_and_blink(drv, port=0xA03, bit=6, dir_reg=0xC3):
    """
    Try setting pin mux registers to enable GPIO output, then blink.
    For ITE IT8625, GPIO pin function select is in global config regs 0x25-0x2C.
    """
    print(f"\n--- Intento de habilitar mux GPIO y blink ---")
    print(f"  Puerto=0x{port:03X}  bit={bit}  dir_reg=LDN7/0x{dir_reg:02X}")

    # Step 1: Read global config regs 0x25-0x2C (pin function select)
    drv.ite_enter()
    print("  Registros globales de mux (0x25-0x2C) ANTES:")
    mux_regs = {}
    for reg in range(0x25, 0x2D):
        mux_regs[reg] = drv.ite_read(reg)
        print(f"    0x{reg:02X}: 0x{mux_regs[reg]:02X}  {mux_regs[reg]:08b}b")
    drv.ite_exit()

    # Step 2: Set direction as output in LDN7
    drv.ite_enter()
    drv.ite_select_ldn(0x07)
    orig_dir = drv.ite_read(dir_reg)
    drv.ite_write(dir_reg, orig_dir | (1 << bit))
    dir_set  = drv.ite_read(dir_reg)
    drv.ite_exit()
    print(f"  Direccion LDN7/0x{dir_reg:02X}: 0x{orig_dir:02X} -> 0x{dir_set:02X}")

    # Step 3: Blink 5 cycles, show data register each time
    print(f"  Blink 5 ciclos (medi todos los pines del conector con el tester):")
    for i in range(5):
        drv.write_bit(port, bit, 0)
        print(f"    Ciclo {i+1} LOW:  0x{port:03X}=0x{drv.inp(port):02X}")
        time.sleep(1)
        drv.write_bit(port, bit, 1)
        print(f"    Ciclo {i+1} HIGH: 0x{port:03X}=0x{drv.inp(port):02X}")
        time.sleep(1)

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

    args = sys.argv[1:]

    if "--blink" in args:
        # --blink <port_hex> <bit>
        idx  = args.index("--blink")
        port = int(args[idx+1], 16) if len(args) > idx+1 else 0xA03
        bit  = int(args[idx+2])     if len(args) > idx+2 else 6
        blink_loop(drv, port, bit)

    elif "--mux" in args:
        try_mux_and_blink(drv)

    else:
        diag_global_regs(drv)
        diag_all_ldn_bases(drv)
        diag_ldn7_regs(drv)
        diag_writable_ports(drv)

    print("\nTest finalizado.")


if __name__ == "__main__":
    main()
