from enum import Enum


class ScannerState(Enum):
    IDLE    = "idle"     # detenido, esperando START del operador
    RUNNING = "running"  # en marcha
    FAULT   = "fault"    # NOK streak alcanzado; espera DETENER
    STOPPED = "stopped"  # post-DETENER AUTO o post-FAULT; espera RESET → IDLE
    ERROR   = "error"    # fallo de hardware (cámara o PLC desconectado)


class OperationMode(Enum):
    MANUAL = "manual"  # solo electroválvula, sin inspección ni backlight
    AUTO   = "auto"    # inspección continua + electroválvula + backlight
