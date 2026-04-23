"""
GUI para PLC Coolmay CX3G - Modbus TCP
Uso: python test_plc_gui.py
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    import sys
    print("Instala pymodbus: pip install pymodbus")
    sys.exit(1)

PLC_IP   = "192.168.10.175"
PLC_PORT = 502
UNIT_ID  = 1

X_COUNT = 16
Y_COUNT = 16
X_BASE  = 0x3400
Y_BASE  = 0x3300

POLL_INTERVAL_MS = 500  # actualización automática cada 500ms


class PLCGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PLC Coolmay CX3G — Modbus TCP")
        self.root.resizable(False, False)

        self.client: ModbusTcpClient | None = None
        self._poll_job = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        # ── Conexión ──────────────────────────────────────────────────
        top = ttk.LabelFrame(self.root, text="Conexión", padding=8)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(top, text="IP:").grid(row=0, column=0, sticky="w")
        self.ip_var = tk.StringVar(value=PLC_IP)
        ttk.Entry(top, textvariable=self.ip_var, width=18).grid(row=0, column=1, padx=(4, 12))

        ttk.Label(top, text="Puerto:").grid(row=0, column=2, sticky="w")
        self.port_var = tk.StringVar(value=str(PLC_PORT))
        ttk.Entry(top, textvariable=self.port_var, width=6).grid(row=0, column=3, padx=(4, 12))

        self.conn_btn = ttk.Button(top, text="Conectar", command=self._toggle_connect, width=12)
        self.conn_btn.grid(row=0, column=4, padx=(0, 8))

        self.status_lbl = tk.Label(
            top, text="● Desconectado", fg="#b91c1c",
            font=("Segoe UI", 10, "bold"), width=18, anchor="w"
        )
        self.status_lbl.grid(row=0, column=5, sticky="w")

        # ── Entradas X (solo lectura) ─────────────────────────────────
        x_frame = ttk.LabelFrame(self.root, text="Entradas X  (lectura)", padding=8)
        x_frame.grid(row=1, column=0, sticky="nsew", **pad)

        self.x_leds: list[tk.Label] = []
        for i in range(X_COUNT):
            col, row = i % 8, i // 8
            lbl = tk.Label(
                x_frame, text=f"X{i}", width=5,
                relief="groove", bg="#e5e7eb",
                font=("Segoe UI", 9)
            )
            lbl.grid(row=row * 2, column=col, padx=3, pady=(4, 0))
            led = tk.Label(x_frame, bg="#d1d5db", width=5, height=1, relief="sunken")
            led.grid(row=row * 2 + 1, column=col, padx=3, pady=(0, 4))
            self.x_leds.append(led)

        # ── Salidas Y (lectura + control) ─────────────────────────────
        y_frame = ttk.LabelFrame(self.root, text="Salidas Y  (control)", padding=8)
        y_frame.grid(row=1, column=1, sticky="nsew", **pad)

        self.y_leds: list[tk.Label] = []
        self.y_btns: list[ttk.Button] = []
        self.y_state: list[bool] = [False] * Y_COUNT

        for i in range(Y_COUNT):
            col, row = i % 8, i // 8
            lbl = tk.Label(
                y_frame, text=f"Y{i}", width=5,
                relief="groove", bg="#e5e7eb",
                font=("Segoe UI", 9)
            )
            lbl.grid(row=row * 3, column=col, padx=3, pady=(4, 0))

            led = tk.Label(y_frame, bg="#d1d5db", width=5, height=1, relief="sunken")
            led.grid(row=row * 3 + 1, column=col, padx=3)

            btn = ttk.Button(
                y_frame, text="OFF", width=5,
                command=lambda idx=i: self._toggle_output(idx),
                state="disabled"
            )
            btn.grid(row=row * 3 + 2, column=col, padx=3, pady=(0, 4))

            self.y_leds.append(led)
            self.y_btns.append(btn)

        # ── Log ───────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=6)
        log_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        self.log = tk.Text(
            log_frame, height=6, state="disabled",
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            relief="flat", wrap="word"
        )
        self.log.pack(fill="x")
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log["yscrollcommand"] = sb.set

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def _toggle_connect(self) -> None:
        if self.client is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        ip = self.ip_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Puerto", "Puerto inválido.")
            return

        self._log(f"Conectando a {ip}:{port}...")
        client = ModbusTcpClient(ip, port=port, timeout=5)
        if not client.connect():
            self._log(f"[ERROR] No se pudo conectar a {ip}:{port}")
            messagebox.showerror("Conexión", f"No se pudo conectar a {ip}:{port}")
            return

        self.client = client
        self._log(f"[OK] Conectado a {ip}:{port}")
        self.status_lbl.config(text="● Conectado", fg="#166534")
        self.conn_btn.config(text="Desconectar")
        for btn in self.y_btns:
            btn.config(state="normal")
        self._start_poll()

    def _disconnect(self) -> None:
        self._stop_poll()
        if self.client:
            self.client.close()
            self.client = None
        self._log("[OK] Desconectado.")
        self.status_lbl.config(text="● Desconectado", fg="#b91c1c")
        self.conn_btn.config(text="Conectar")
        for btn in self.y_btns:
            btn.config(state="disabled")
        self._reset_leds()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _start_poll(self) -> None:
        self._poll()

    def _stop_poll(self) -> None:
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None

    def _poll(self) -> None:
        if self.client is None:
            return
        threading.Thread(target=self._read_all, daemon=True).start()
        self._poll_job = self.root.after(POLL_INTERVAL_MS, self._poll)

    def _read_all(self) -> None:
        if self.client is None:
            return
        try:
            # Entradas X
            rx = self.client.read_discrete_inputs(X_BASE, count=X_COUNT, device_id=UNIT_ID)
            if rx.isError():
                rx = self.client.read_coils(X_BASE, count=X_COUNT, device_id=UNIT_ID)
            if not rx.isError():
                bits = rx.bits[:X_COUNT]
                self.root.after(0, lambda b=bits: self._update_x_leds(b))

            # Salidas Y
            ry = self.client.read_coils(Y_BASE, count=Y_COUNT, device_id=UNIT_ID)
            if not ry.isError():
                bits = ry.bits[:Y_COUNT]
                self.root.after(0, lambda b=bits: self._update_y_leds(b))
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._log(f"[ERROR] poll: {e}"))

    # ------------------------------------------------------------------
    # Actualizar UI
    # ------------------------------------------------------------------

    def _update_x_leds(self, bits: list) -> None:
        for i, val in enumerate(bits):
            color = "#22c55e" if val else "#d1d5db"
            self.x_leds[i].config(bg=color)

    def _update_y_leds(self, bits: list) -> None:
        for i, val in enumerate(bits):
            self.y_state[i] = bool(val)
            color = "#f97316" if val else "#d1d5db"
            self.y_leds[i].config(bg=color)
            self.y_btns[i].config(text="ON" if val else "OFF")

    def _reset_leds(self) -> None:
        for led in self.x_leds:
            led.config(bg="#d1d5db")
        for i, led in enumerate(self.y_leds):
            led.config(bg="#d1d5db")
            self.y_btns[i].config(text="OFF")

    # ------------------------------------------------------------------
    # Control Y
    # ------------------------------------------------------------------

    def _toggle_output(self, idx: int) -> None:
        if self.client is None:
            return
        new_val = not self.y_state[idx]
        threading.Thread(target=self._write_coil, args=(idx, new_val), daemon=True).start()

    def _write_coil(self, idx: int, value: bool) -> None:
        try:
            r = self.client.write_coil(Y_BASE + idx, value, device_id=UNIT_ID)
            if r.isError():
                self.root.after(0, lambda: self._log(f"[ERROR] write Y{idx}: {r}"))
            else:
                label = "ON" if value else "OFF"
                self.root.after(0, lambda: self._log(f"[OK] Y{idx} → {label}"))
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._log(f"[ERROR] Y{idx}: {e}"))

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


def main() -> None:
    root = tk.Tk()
    PLCGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
