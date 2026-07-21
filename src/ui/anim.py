"""
Micro-animaciones sutiles y reutilizables para la UI (PyQt6).

Diseñadas para dar un toque estetico industrial: sutiles, con proposito, sin
rebotes ni transiciones "raras", y sin costo cuando no se usan.

  - fade_in(win): la ventana/dialogo aparece con un fundido suave de opacidad.
  - polish_buttons(root): cada QPushButton se ilumina en hover y hace un dip al
    presionar (un unico QGraphicsOpacityEffect por boton).
  - fade_button(btn, visible): muestra/oculta un boton con fundido (usado por el
    RESET para que no aparezca/desaparezca de golpe).
  - animate_bg_color(widget, hex, style_fn): transiciona el color de fondo de un
    label (badge de estado) de un color al otro en vez de saltar seco.
  - add_hover_shadow(widget): realce sutil (sombra/glow) al pasar el mouse por
    tarjetas o paneles. El efecto se crea al entrar y se remueve al salir (costo
    cero en reposo).

Todo esta envuelto en try/except: si algo falla, la UI sigue funcionando sin
animacion (nunca debe romper el control de maquina).
"""

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPropertyAnimation,
                          QTimer, QVariantAnimation)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
                             QPushButton, QWidget)

_DEL_WHEN_STOPPED = QPropertyAnimation.DeletionPolicy.DeleteWhenStopped

# Niveles de opacidad de los tres estados del boton.
_REST  = 0.88   # reposo: apenas mate
_HOVER = 1.0    # mouse encima: iluminado
_PRESS = 0.55   # presionado: dip momentaneo


# ======================================================================
# Fundido de ventanas / dialogos
# ======================================================================

def fade_in(win: QWidget, ms: int = 170) -> None:
    """Anima la opacidad de una ventana/dialogo top-level de 0 -> 1.
    Llamar JUSTO DESPUES de show()/showMaximized() (o desde showEvent)."""
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


# ======================================================================
# Botones: hover / press / fundido de aparicion
# ======================================================================

class _ButtonAnimator(QObject):
    """Filtro de eventos que anima la opacidad de un boton en hover/press y
    ofrece show_fade()/hide_fade() para mostrar/ocultar con fundido.

    Usa un solo QGraphicsOpacityEffect persistente por boton (un widget solo
    admite UN efecto grafico), de modo que hover, click y fundido conviven."""

    def __init__(self, btn: QPushButton) -> None:
        super().__init__(btn)
        self._btn = btn
        self._hovered = False
        self._pending_hide = False
        self._eff = QGraphicsOpacityEffect(btn)
        self._eff.setOpacity(_REST)
        btn.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_finished)
        btn.installEventFilter(self)

    def _to(self, value: float, ms: int = 130) -> None:
        self._pending_hide = False
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._eff.opacity())
        self._anim.setEndValue(value)
        self._anim.start()

    def _on_finished(self) -> None:
        if self._pending_hide and self._eff.opacity() <= 0.03:
            self._pending_hide = False
            try:
                self._btn.setVisible(False)
            except Exception:
                pass

    def show_fade(self, ms: int = 190) -> None:
        if self._btn.isVisible() and self._eff.opacity() > 0.5:
            return
        self._eff.setOpacity(0.0)
        self._btn.setVisible(True)
        self._to(_HOVER if self._hovered else _REST, ms)

    def hide_fade(self, ms: int = 190) -> None:
        if not self._btn.isVisible():
            return
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._eff.opacity())
        self._anim.setEndValue(0.0)
        self._pending_hide = True
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
                if getattr(btn, "_animator", None) is not None \
                        or btn.graphicsEffect() is not None:
                    continue  # ya animado o usa otro efecto: no pisar
                btn._animator = _ButtonAnimator(btn)
            except Exception:
                pass
    except Exception:
        pass


def fade_button(btn: QPushButton, visible: bool) -> None:
    """Muestra u oculta un boton con fundido, si tiene animador; si no, cae a
    setVisible directo. Idempotente."""
    try:
        anim = getattr(btn, "_animator", None)
        if anim is None:
            btn.setVisible(visible)
            return
        if visible:
            anim.show_fade()
        else:
            anim.hide_fade()
    except Exception:
        try:
            btn.setVisible(visible)
        except Exception:
            pass


# ======================================================================
# Transicion de color de fondo (badge de estado)
# ======================================================================

def animate_bg_color(widget: QWidget, target_hex: str, style_fn, ms: int = 260) -> None:
    """Transiciona el color de fondo de un widget de su color previo al nuevo.

    `style_fn(color_hex_str) -> str` construye el stylesheet COMPLETO dado el
    color de fondo actual de la animacion. En el primer llamado (sin color
    previo) aplica directo, sin animar."""
    try:
        target = QColor(target_hex)
        prev = getattr(widget, "_bg_anim_color", None)
        if prev is None or ms <= 0:
            widget.setStyleSheet(style_fn(target.name()))
            widget._bg_anim_color = target
            return
        if QColor(prev) == target:
            return
        # Guardar el objetivo YA (no al terminar): asi los refresh_status con el
        # mismo estado que llegan durante la animacion cortan temprano y no la
        # reinician en cada tick.
        widget._bg_anim_color = target
        anim = QVariantAnimation(widget)
        anim.setDuration(ms)
        anim.setStartValue(QColor(prev))
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(
            lambda c: widget.setStyleSheet(style_fn(c.name()))
        )
        anim.start(_DEL_WHEN_STOPPED)
        widget._bg_color_anim = anim
    except Exception:
        try:
            widget.setStyleSheet(style_fn(QColor(target_hex).name()))
        except Exception:
            pass


# ======================================================================
# Realce (sombra/glow) al pasar el mouse por tarjetas / paneles
# ======================================================================

class _HoverShadow(QObject):
    """Realce sutil en hover via QGraphicsDropShadowEffect. El efecto se crea al
    entrar y se REMUEVE al salir -> costo cero en reposo (importante para el
    panel de camara, que se repinta muchas veces por segundo)."""

    def __init__(self, w: QWidget, blur: float = 22.0,
                 color: QColor | None = None) -> None:
        super().__init__(w)
        self._w = w
        self._blur = float(blur)
        self._color = color or QColor(56, 189, 248, 150)  # celeste industrial suave
        self._eff = None
        self._anim = None
        w.installEventFilter(self)

    def _animate(self, value: float, remove: bool = False) -> None:
        if self._eff is None:
            return
        if self._anim is not None:
            self._anim.stop()
        a = QPropertyAnimation(self._eff, b"blurRadius", self)
        a.setDuration(160)
        a.setStartValue(self._eff.blurRadius())
        a.setEndValue(value)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        if remove:
            def _fin() -> None:
                try:
                    self._w.setGraphicsEffect(None)
                except Exception:
                    pass
                self._eff = None
            a.finished.connect(_fin)
        a.start()
        self._anim = a

    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            t = ev.type()
            if t == QEvent.Type.Enter:
                if self._eff is None:
                    if self._w.graphicsEffect() is not None:
                        return False  # el widget usa otro efecto: no pisar
                    self._eff = QGraphicsDropShadowEffect(self._w)
                    self._eff.setColor(self._color)
                    self._eff.setOffset(0, 0)
                    self._eff.setBlurRadius(0.0)
                    self._w.setGraphicsEffect(self._eff)
                self._animate(self._blur)
            elif t == QEvent.Type.Leave:
                if self._eff is not None:
                    self._animate(0.0, remove=True)
        except Exception:
            pass
        return False


def add_hover_shadow(widget: QWidget, blur: float = 22.0,
                     color: QColor | None = None) -> None:
    """Aplica realce en hover a un widget (tarjeta, panel, feed de camara)."""
    try:
        if getattr(widget, "_hover_shadow", None) is not None:
            return
        widget._hover_shadow = _HoverShadow(widget, blur=blur, color=color)
    except Exception:
        pass


# ======================================================================
# Glow de la camara segun estado del scanner
# ======================================================================

class CameraGlow(QObject):
    """Realce (glow difuso) alrededor del panel de camara, atado al estado del
    scanner y ademas sensible al hover:

        run   -> glow VERDE persistente (mientras corre).
        stop  -> glow ROJO por 30s y luego se apaga (para no simular error
                 permanente si el scanner queda detenido).
        idle  -> sin glow de estado; al pasar el mouse, glow neutro celeste.

    El hover intensifica el glow actual (mismo color del estado). Usa un unico
    QGraphicsDropShadowEffect persistente sobre el widget. `set_mode()` recibe un
    string semantico ('run'/'stop'/'idle') para no acoplar anim.py al enum de
    estados del scanner."""

    _GREEN   = QColor(34, 197, 94, 190)
    _RED     = QColor(239, 68, 68, 190)
    _NEUTRAL = QColor(56, 189, 248, 150)
    _BASE_BLUR   = 14.0   # glow de estado: apenas pequeno y difuso
    _HOVER_BONUS = 14.0   # extra al pasar el mouse
    _RED_HOLD_MS = 30_000

    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self._w = widget
        self._mode = "idle"
        self._hovered = False
        self._base_blur = 0.0
        self._eff = QGraphicsDropShadowEffect(widget)
        self._eff.setColor(self._NEUTRAL)
        self._eff.setOffset(0, 0)
        self._eff.setBlurRadius(0.0)
        widget.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"blurRadius", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._red_timer = QTimer(self)
        self._red_timer.setSingleShot(True)
        self._red_timer.setInterval(self._RED_HOLD_MS)
        self._red_timer.timeout.connect(self._on_red_expired)
        widget.installEventFilter(self)

    def _target_blur(self) -> float:
        bonus = self._HOVER_BONUS if self._hovered else 0.0
        if self._base_blur <= 0.0:
            return bonus  # sin estado: solo el bonus de hover (0 si no hoverea)
        return self._base_blur + bonus

    def _refresh(self, ms: int = 260) -> None:
        if self._base_blur <= 0.0 and self._hovered:
            self._eff.setColor(self._NEUTRAL)  # hover sin estado -> celeste
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._eff.blurRadius())
        self._anim.setEndValue(self._target_blur())
        self._anim.start()

    def set_mode(self, mode: str) -> None:
        try:
            if mode == self._mode:
                return  # solo actuar en transiciones (no reinicia el timer rojo)
            self._mode = mode
            if mode == "run":
                self._red_timer.stop()
                self._eff.setColor(self._GREEN)
                self._base_blur = self._BASE_BLUR
                self._refresh()
            elif mode == "stop":
                self._eff.setColor(self._RED)
                self._base_blur = self._BASE_BLUR
                self._red_timer.start()   # a los 30s se apaga solo
                self._refresh()
            else:  # idle
                self._red_timer.stop()
                self._base_blur = 0.0
                self._refresh()
        except Exception:
            pass

    def _on_red_expired(self) -> None:
        # Sigue detenido pero ya paso el instante rojo -> apagar suave el glow.
        if self._mode == "stop":
            self._base_blur = 0.0
            self._refresh(ms=700)

    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            t = ev.type()
            if t == QEvent.Type.Enter:
                self._hovered = True
                self._refresh(ms=160)
            elif t == QEvent.Type.Leave:
                self._hovered = False
                self._refresh(ms=220)
        except Exception:
            pass
        return False


# ======================================================================
# Flash de atencion (parpadeo breve para avisar un cambio importante)
# ======================================================================

def attention_flash(widget: QWidget, blinks: int = 2, ms: int = 520) -> None:
    """Parpadeo breve de opacidad para llamar la atencion sobre un cambio
    (p. ej. la maneta pasando de MANUAL a AUTOMATICO). El efecto es transitorio:
    se crea al empezar y se remueve al terminar (costo cero en reposo)."""
    try:
        if widget.graphicsEffect() is not None:
            return  # ya hay un efecto/flash en curso: no pisar
        eff = QGraphicsOpacityEffect(widget)
        eff.setOpacity(1.0)
        widget.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", widget)
        anim.setDuration(max(1, int(ms)))
        n = max(1, int(blinks))
        anim.setKeyValueAt(0.0, 1.0)
        for i in range(n):
            anim.setKeyValueAt(min(1.0, (i + 0.5) / n), 0.25)
            anim.setKeyValueAt(min(1.0, (i + 1.0) / n), 1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        def _cleanup() -> None:
            try:
                widget.setGraphicsEffect(None)
            except Exception:
                pass

        anim.finished.connect(_cleanup)
        anim.start(_DEL_WHEN_STOPPED)
        widget._flash_anim = anim
    except Exception:
        pass
