"""
GPIO test via Smooth64.dll (vendor driver).

Prerequisites:
  1. Run "Smooth Install(驱动安装).exe" AS ADMINISTRATOR (installs smooth.sys kernel driver)
  2. Run THIS SCRIPT as Administrator

The smooth.sys driver initializes LDN7 at boot time (before BIOS config lock),
which is why physical GPIO pins respond to writes via this DLL.

GPIO map (from CONFIG.INI):
  Inputs:  GPI1=0xA00.0  GPI2=0xA00.2  GPI3=0xA00.7  GPI4=0xA02.0
           GPI5=0xA00.4  GPI6=0xA03.4  GPI7=0xA04.3
  Outputs: GPO1=0xA03.1  GPO2=0xA05.5  GPO3=0xA00.1  GPO4=0xA03.6
           GPO5=0xA04.5  GPO6=0xA04.4  GPO7=0xA03.0

Usage:
  python test_smooth_gpio.py               # read all GPIs, toggle all GPOs once
  python test_smooth_gpio.py --blink GPO1  # blink GPO1 until Ctrl+C
"""

import ctypes
import os
import sys
import time
import argparse
import shutil

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# DLL paths (try GPIO_Driver first, fall back to IBC_SMOOTH)
_DLL_CANDIDATES = [
    os.path.join(_SCRIPT_DIR, "setup_prov_pc", "windows text",
                 "windows系统专用库", "GPIO_Driver", "Smooth64.dll"),
    os.path.join(_SCRIPT_DIR, "setup_prov_pc", "IBC_SMOOTH", "Smooth64.dll"),
]

# Config.ini must be in the SAME DIRECTORY as the script (SmoothAutoInitConfigFromFile
# looks for "Config.ini" relative to the calling process working directory)
_CONFIG_SRC = os.path.join(_SCRIPT_DIR, "setup_prov_pc", "IBC_SMOOTH", "CONFIG.INI")
_CONFIG_DST = os.path.join(_SCRIPT_DIR, "Config.ini")

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


class SmoothDriver:
    def __init__(self):
        self.dll = None
        self._load_dll()
        self._setup_prototypes()

    def _load_dll(self):
        for path in _DLL_CANDIDATES:
            if os.path.exists(path):
                try:
                    self.dll = ctypes.WinDLL(path)
                    print(f"[OK] Loaded DLL: {path}")
                    return
                except OSError as e:
                    print(f"[WARN] Could not load {path}: {e}")
        raise RuntimeError("Smooth64.dll not found. Check setup_prov_pc directory.")

    def _setup_prototypes(self):
        d = self.dll
        d.SmoothConnectDriver.restype    = ctypes.c_bool
        d.SmoothConnectDriver.argtypes   = []
        d.SmoothDisconnectDriver.restype = None
        d.SmoothDisconnectDriver.argtypes = []
        d.SmoothAutoInitConfigFromFile.restype  = ctypes.c_bool
        d.SmoothAutoInitConfigFromFile.argtypes = []
        d.SmoothEasyGetPort.restype  = ctypes.c_int
        d.SmoothEasyGetPort.argtypes = [ctypes.c_ushort, ctypes.c_ubyte]
        d.SmoothEasySetPort.restype  = ctypes.c_bool
        d.SmoothEasySetPort.argtypes = [ctypes.c_ushort, ctypes.c_ubyte, ctypes.c_ubyte]
        d.SmoothSetPortOrPhyValue.restype  = ctypes.c_bool
        d.SmoothSetPortOrPhyValue.argtypes = [ctypes.c_wchar_p, ctypes.c_bool]
        d.SmoothGetPortOrPhyValue.restype  = ctypes.c_int
        d.SmoothGetPortOrPhyValue.argtypes = [ctypes.c_wchar_p]

    def connect(self):
        ok = self.dll.SmoothConnectDriver()
        if not ok:
            raise RuntimeError(
                "SmoothConnectDriver() failed.\n"
                "  → Make sure 'Smooth Install(驱动安装).exe' was run as Admin\n"
                "  → And that THIS script runs as Administrator"
            )
        print("[OK] SmoothConnectDriver() success")
        return ok

    def init_config(self):
        """Copy Config.ini next to script and call SmoothAutoInitConfigFromFile."""
        if not os.path.exists(_CONFIG_DST):
            if os.path.exists(_CONFIG_SRC):
                shutil.copy2(_CONFIG_SRC, _CONFIG_DST)
                print(f"[OK] Copied Config.ini to {_CONFIG_DST}")
            else:
                print("[WARN] CONFIG.INI not found — named pin functions won't work")
                return False
        ok = self.dll.SmoothAutoInitConfigFromFile()
        if ok:
            print("[OK] SmoothAutoInitConfigFromFile() success")
        else:
            print("[WARN] SmoothAutoInitConfigFromFile() failed")
        return ok

    def disconnect(self):
        self.dll.SmoothDisconnectDriver()

    def read_bit(self, addr, bit):
        return self.dll.SmoothEasyGetPort(addr, bit)

    def write_bit(self, addr, bit, value):
        return self.dll.SmoothEasySetPort(addr, bit, int(bool(value)))

    def set_named(self, name, value):
        return self.dll.SmoothSetPortOrPhyValue(name, bool(value))

    def get_named(self, name):
        return self.dll.SmoothGetPortOrPhyValue(name)


def read_all_inputs(drv):
    print("\n--- GPI states ---")
    for name, (addr, bit, is_out) in GPIO_MAP.items():
        if not is_out:
            val = drv.read_bit(addr, bit)
            level = "HIGH" if val == 1 else ("LOW" if val == 0 else f"ERR({val})")
            print(f"  {name:5s}  0x{addr:03X}.{bit}  →  {level}")


def toggle_all_outputs(drv):
    print("\n--- GPO toggle test ---")
    outputs = [(n, a, b) for n, (a, b, o) in GPIO_MAP.items() if o]
    for name, addr, bit in outputs:
        drv.write_bit(addr, bit, 1)
        time.sleep(0.2)
        v = drv.read_bit(addr, bit)
        drv.write_bit(addr, bit, 0)
        time.sleep(0.1)
        print(f"  {name:5s}  0x{addr:03X}.{bit}  set HIGH → readback={v}")


def blink(drv, name, delay=0.5):
    if name not in GPIO_MAP:
        print(f"Unknown pin: {name}. Options: {list(GPIO_MAP)}")
        return
    addr, bit, is_out = GPIO_MAP[name]
    if not is_out:
        print(f"{name} is an input, cannot blink.")
        return
    print(f"Blinking {name} (0x{addr:03X}.{bit}) — Ctrl+C to stop")
    state = 0
    try:
        while True:
            ok = drv.write_bit(addr, bit, state)
            v  = drv.read_bit(addr, bit)
            print(f"  {name} → {'HIGH' if state else 'LOW ':4s}  write_ok={ok}  readback={v}")
            state ^= 1
            time.sleep(delay)
    except KeyboardInterrupt:
        drv.write_bit(addr, bit, 0)
        print(f"\nStopped. {name} set LOW.")


def main():
    parser = argparse.ArgumentParser(description="GPIO test via Smooth64.dll")
    parser.add_argument("--blink", metavar="PIN",
                        help="Blink a specific output (e.g. GPO1) until Ctrl+C")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Blink delay in seconds (default 0.5)")
    args = parser.parse_args()

    drv = SmoothDriver()
    drv.connect()
    drv.init_config()

    try:
        if args.blink:
            blink(drv, args.blink.upper(), delay=args.delay)
        else:
            read_all_inputs(drv)
            toggle_all_outputs(drv)
    finally:
        drv.disconnect()
        print("\n[OK] Driver disconnected.")


if __name__ == "__main__":
    main()
