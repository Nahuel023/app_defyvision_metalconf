"""Bloqueo de instancia unica para los modos que controlan hardware.

En Windows usa un mutex nombrado del kernel: la adquisicion es atomica entre
procesos y el propio sistema operativo elimina el bloqueo si el proceso termina
o se cae. El fallback POSIX existe para desarrollo y pruebas fuera de Windows.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import BinaryIO


ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = r"Global\DEFYVISION_METALCONF_HARDWARE_SESSION_V1"


class SingleInstanceGuard:
    """Mantiene un bloqueo interproceso hasta llamar :meth:`close`."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._lock_file: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None or self._lock_file is not None

    def acquire(self) -> bool:
        """Devuelve ``True`` solo para la primera instancia activa."""
        if self.acquired:
            return True
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_posix()

    def _acquire_windows(self) -> bool:
        # Import local: en plataformas no Windows ctypes.WinDLL puede no existir.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int

        ctypes.set_last_error(0)
        handle = create_mutex(None, 0, self._name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, f"No se pudo crear el mutex {self._name!r}")
        if error == ERROR_ALREADY_EXISTS:
            close_handle(handle)
            return False

        self._handle = int(handle)
        return True

    def _acquire_posix(self) -> bool:
        import fcntl

        safe_name = "".join(ch if ch.isalnum() else "_" for ch in self._name)
        path = Path(tempfile.gettempdir()) / f"{safe_name}.lock"
        lock_file = path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        except Exception:
            lock_file.close()
            raise
        self._lock_file = lock_file
        return True

    def close(self) -> None:
        """Libera el bloqueo. Es seguro llamarlo mas de una vez."""
        if self._handle is not None:
            import ctypes

            handle = self._handle
            self._handle = None
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = (ctypes.c_void_p,)
                close_handle.restype = ctypes.c_int
                close_handle(handle)
            except Exception:
                # Al finalizar el proceso Windows cierra igualmente el handle.
                pass

        if self._lock_file is not None:
            lock_file = self._lock_file
            self._lock_file = None
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    def __enter__(self) -> SingleInstanceGuard:
        if not self.acquire():
            raise RuntimeError("Ya existe otra instancia activa")
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def show_already_running_notice() -> None:
    """Avisa al operario sin cargar Qt ni ningun modulo de hardware."""
    title = "DefyVision Metalconf - Aplicacion ya abierta"
    message = (
        "DefyVision Metalconf ya se encuentra en ejecucion.\n\n"
        "Solo se permite una sesion para proteger las camaras, el PLC y la "
        "estabilidad del sistema.\n\n"
        "Use la ventana que ya esta abierta. Si no la encuentra, revise la barra "
        "de tareas."
    )
    if _show_native_message(title, message, warning=True):
        return
    try:
        print(message)
    except Exception:
        pass


def show_guard_failure_notice(exc: BaseException) -> None:
    """Falla cerrada: si no se puede verificar el bloqueo, no inicia hardware."""
    title = "DefyVision Metalconf - Inicio bloqueado por seguridad"
    message = (
        "No se pudo comprobar si DefyVision Metalconf ya esta abierto.\n\n"
        "Por seguridad, esta ejecucion no iniciara las camaras ni el PLC. "
        "Reinicie la computadora y vuelva a intentarlo.\n\n"
        f"Detalle tecnico: {type(exc).__name__}: {exc}"
    )
    if _show_native_message(title, message, warning=False):
        return
    try:
        print(message)
    except Exception:
        pass


def _show_native_message(title: str, message: str, *, warning: bool) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        message_box = user32.MessageBoxW
        message_box.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        )
        message_box.restype = ctypes.c_int
        # MB_OK | MB_ICONWARNING/ERROR | MB_SETFOREGROUND | MB_TOPMOST
        icon = 0x00000030 if warning else 0x00000010
        message_box(None, message, title, icon | 0x00010000 | 0x00040000)
        return True
    except Exception:
        return False
