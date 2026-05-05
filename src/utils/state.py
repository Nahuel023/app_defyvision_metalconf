from enum import Enum


class ScannerState(Enum):
    IDLE    = "idle"     # detenido, esperando START del operador
    RUNNING = "running"  # en marcha
    FAULT   = "fault"    # NOK detectado, línea parada, esperando reset del operador
    ERROR   = "error"    # fallo de hardware (cámara o PLC desconectado)


class OperationMode(Enum):
    MANUAL = "manual"  # sin inspección, solo control de pistón desde UI
    AUTO   = "auto"    # inspección activa en cada ciclo del punzón
