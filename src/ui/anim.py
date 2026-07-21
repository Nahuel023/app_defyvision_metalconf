"""
Micro-animaciones sutiles y reutilizables para la UI (PyQt6).

Diseñadas para dar un toque estetico sin ser intrusivas ni pesadas en una
UI de planta 24/7:

  - fade_in(win): la ventana aparece con un fundido suave de opacidad en vez
    de "aparecer de golpe". Se aplica a las ventanas grandes al abrir.
  - add_press_pulse(btn): feedback tactil al presionar un boton (un breve
    pulso de opacidad). El efecto grafico es TRANSITORIO: se crea al presionar
    y se remueve al terminar, asi los botones en reposo no cargan ningun
    efecto (costo cero cuando no se usan).
  - polish_buttons(root): aplica add_press_pulse a todos los QPushButton de
    una ventana de una sola llamada.

Todo esta envuelto en try/except: si algo falla, la UI sigue funcionando sin
animacion (nunca debe romper el control de maquina).
"""

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QWidget

_DEL_WHEN_STOPPED = QPropertyAnimation.DeletionPolicy.DeleteWhenStopped


def fade_in(win: QWidget, ms: int = 170) -> None:
    """Anima la opacidad de una ventana top-level de 0 -> 1 (fundido de entrada).
    Llamar JUSTO DESPUES de show()/showMaximized()."""
    try:
        win.setWindowOpacity(0.0)
        anim = QPropertyAnimation(win, b"windowOpacity", win)
        anim.setDuration(max(1, int(ms)))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(_DEL_WHEN_STOPPED)
        win._fade_in_anim = anim  # evita que el GC la corte antes de terminar
    except Exception:
        try:
            win.setWindowOpacity(1.0)
        except Exception:
            pass


def _pulse(btn: QPushButton) -> None:
    """Pulso de opacidad transitorio para feedback de presion."""
    try:
        if btn.graphicsEffect() is not None:
            return  # ya hay un pulso corriendo (o el boton usa otro efecto)
        eff = QGraphicsOpacityEffect(btn)
        eff.setOpacity(1.0)
        btn.setGraphicsEffect(eff)

        anim = QPropertyAnimation(eff, b"opacity", btn)
        anim.setDuration(150)
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.4, 0.55)
        anim.setKeyValueAt(1.0, 1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            try:
                btn.setGraphicsEffect(None)
            except Exception:
                pass  # el boton pudo destruirse/ocultarse durante el pulso

        anim.finished.connect(_cleanup)
        anim.start(_DEL_WHEN_STOPPED)
        btn._press_pulse_anim = anim
    except Exception:
        pass


def add_press_pulse(btn: QPushButton) -> None:
    """Conecta el pulso de presion a un boton (se dispara en cada press)."""
    try:
        btn.pressed.connect(lambda b=btn: _pulse(b))
    except Exception:
        pass


def polish_buttons(root: QWidget) -> None:
    """Aplica el feedback de presion a todos los QPushButton descendientes."""
    try:
        for btn in root.findChildren(QPushButton):
            add_press_pulse(btn)
    except Exception:
        pass
