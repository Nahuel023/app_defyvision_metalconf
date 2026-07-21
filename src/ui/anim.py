"""
Micro-animaciones sutiles y reutilizables para la UI (PyQt6).

Diseñadas para dar un toque estetico sin ser intrusivas ni pesadas en una
UI de planta 24/7:

  - fade_in(win): la ventana aparece con un fundido suave de opacidad en vez
    de "aparecer de golpe". Se aplica a las ventanas grandes al abrir.
  - polish_buttons(root): instala en cada QPushButton una animacion unificada
    de opacidad (un UNICO QGraphicsOpacityEffect por boton) que maneja los
    tres estados con transiciones suaves:
        reposo  -> levemente mate (_REST)
        hover   -> se ilumina      (_HOVER)
        presion -> pulso hacia abajo y vuelve (_PRESS)
    Sin recortes ni cambios de layout (la opacidad no dibuja fuera del rect).

Todo esta envuelto en try/except: si algo falla, la UI sigue funcionando sin
animacion (nunca debe romper el control de maquina).
"""

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPropertyAnimation)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QWidget

_DEL_WHEN_STOPPED = QPropertyAnimation.DeletionPolicy.DeleteWhenStopped

# Niveles de opacidad de los tres estados del boton.
_REST  = 0.88   # reposo: apenas mate
_HOVER = 1.0    # mouse encima: iluminado
_PRESS = 0.55   # presionado: dip momentaneo


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


class _ButtonAnimator(QObject):
    """Filtro de eventos que anima la opacidad de un boton en hover/press.

    Usa un solo QGraphicsOpacityEffect persistente por boton (un widget solo
    admite UN efecto grafico), de modo que hover y click conviven sin conflicto."""

    def __init__(self, btn: QPushButton) -> None:
        super().__init__(btn)
        self._btn = btn
        self._hovered = False
        self._eff = QGraphicsOpacityEffect(btn)
        self._eff.setOpacity(_REST)
        btn.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        btn.installEventFilter(self)

    def _to(self, value: float, ms: int = 130) -> None:
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._eff.opacity())
        self._anim.setEndValue(value)
        self._anim.start()

    def eventFilter(self, obj, ev):  # noqa: N802 (firma Qt)
        try:
            t = ev.type()
            if t == QEvent.Type.Enter:
                self._hovered = True
                self._to(_HOVER)
            elif t == QEvent.Type.Leave:
                self._hovered = False
                self._to(_REST)
            elif t == QEvent.Type.MouseButtonPress:
                self._to(_PRESS, 90)
            elif t == QEvent.Type.MouseButtonRelease:
                self._to(_HOVER if self._hovered else _REST, 150)
        except Exception:
            pass
        return False  # nunca consumir el evento: el boton se comporta normal


def polish_buttons(root: QWidget) -> None:
    """Instala la animacion de hover/press en todos los QPushButton descendientes."""
    try:
        for btn in root.findChildren(QPushButton):
            try:
                if getattr(btn, "_has_anim", False) or btn.graphicsEffect() is not None:
                    continue  # ya animado o usa otro efecto: no pisar
                _ButtonAnimator(btn)
                btn._has_anim = True
            except Exception:
                pass
    except Exception:
        pass
