"""
Heartbeat de estado de la maquina hacia una planilla de Google Sheets.

Envia por HTTP POST (JSON) el estado agregado de la maquina a un Web App de
Google Apps Script (ver scripts/apps_script/). El script pinta una casilla:

  EN_USO   -> verde     algun scanner en RUNNING
  REPOSO   -> amarillo  app abierta, ningun scanner en RUNNING
  APAGADA  -> rojo      cierre ordenado de la app, o el script deja de recibir
                         heartbeats (PC apagada, corte de luz, sin red)

El estado "apagada por corte" no lo puede mandar el programa: lo deduce el
Apps Script por falta de heartbeat. Por eso se reenvia el estado cada
`interval_s` aunque no cambie, y de inmediato cuando cambia.

Solo usa la libreria estandar (urllib) para no sumar dependencias al build.
Cualquier fallo de red se registra en el log y se ignora: el reporte NUNCA
puede trabar el arranque, la inspeccion ni el apagado de la maquina.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATUS_IN_USE = "EN_USO"
STATUS_IDLE   = "REPOSO"
STATUS_OFF    = "APAGADA"

_APP_CONFIG_PATH = Path("config/app.yaml")


class StatusReporter:
    """Envia el estado de la maquina a un Web App de Apps Script en segundo plano."""

    def __init__(
        self,
        url: str,
        machine: str = "Metalconf",
        interval_s: float = 30.0,
        timeout_s: float = 5.0,
        token: str = "",
        poll_s: float = 1.0,
    ) -> None:
        self._url        = str(url).strip()
        self._machine    = str(machine)
        self._interval   = max(5.0, float(interval_s))
        self._timeout    = max(1.0, float(timeout_s))
        self._token      = str(token or "")
        self._poll       = max(0.1, float(poll_s))
        self._system     = None
        self._stop       = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_sent_status: Optional[str] = None
        self._last_sent_mono = 0.0
        self._last_error_logged: Optional[str] = None

    # ------------------------------------------------------------------
    # Construccion desde config/app.yaml
    # ------------------------------------------------------------------

    @classmethod
    def from_app_config(cls, path: Path = _APP_CONFIG_PATH) -> Optional["StatusReporter"]:
        """Devuelve un reporter configurado, o None si esta deshabilitado / sin URL.

        Nunca lanza: una seccion `status_report` invalida solo desactiva el reporte.
        """
        try:
            import yaml
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cfg = data.get("status_report") or {}
            if not cfg.get("enabled", False):
                return None
            url = str(cfg.get("url") or "").strip()
            if not url:
                logger.warning("status_report habilitado pero sin url; reporte desactivado")
                return None
            return cls(
                url=url,
                machine=cfg.get("machine", "Metalconf"),
                interval_s=cfg.get("interval_s", 30.0),
                timeout_s=cfg.get("timeout_s", 5.0),
                token=cfg.get("token", ""),
            )
        except Exception as exc:
            logger.error("Config status_report invalida; reporte desactivado: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self, system) -> None:
        self._system = system
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="status-reporter"
        )
        self._thread.start()
        logger.info(
            "StatusReporter iniciado — maquina=%s, intervalo=%ss", self._machine, self._interval
        )

    def stop(self, send_off: bool = True) -> None:
        """Detiene el hilo y, si se pide, avisa APAGADA (best effort, timeout corto)."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._timeout + 1.0)
            self._thread = None
        if send_off:
            self._send(STATUS_OFF, {}, timeout_s=min(self._timeout, 3.0))

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @staticmethod
    def machine_status(scanner_states: dict[str, str]) -> str:
        """EN_USO si algun scanner esta en RUNNING; si no, REPOSO."""
        if any(state == "running" for state in scanner_states.values()):
            return STATUS_IN_USE
        return STATUS_IDLE

    def _scanner_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        system = self._system
        if system is None:
            return states
        for sid in system.scanner_ids():
            try:
                states[sid] = system.scanner(sid).state.value
            except Exception:
                states[sid] = "unknown"
        return states

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                states = self._scanner_states()
                status = self.machine_status(states)
                due = (time.monotonic() - self._last_sent_mono) >= self._interval
                if status != self._last_sent_status or due:
                    self._send(status, states)
            except Exception as exc:
                logger.error("StatusReporter: error inesperado: %s", exc)
            self._stop.wait(self._poll)

    def _send(self, status: str, states: dict[str, str],
              timeout_s: Optional[float] = None) -> bool:
        payload = {
            "token":    self._token,
            "machine":  self._machine,
            "status":   status,
            "scanners": states,
            "ts":       datetime.now().isoformat(timespec="seconds"),
        }
        # Se marca como enviado aun si falla: el reintento queda para el proximo
        # intervalo, sin martillar la red cada segundo cuando no hay internet.
        self._last_sent_status = status
        self._last_sent_mono = time.monotonic()
        try:
            req = urllib.request.Request(
                self._url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s or self._timeout) as resp:
                resp.read()
            if self._last_error_logged is not None:
                logger.info("StatusReporter: conexion restablecida")
                self._last_error_logged = None
            return True
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            # Loguear solo cuando cambia el error, para no inundar el log sin red.
            if msg != self._last_error_logged:
                logger.warning("StatusReporter: no se pudo enviar estado (%s)", msg)
                self._last_error_logged = msg
            return False
