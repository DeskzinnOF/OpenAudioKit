"""Bounded desktop plot models and custom Qt plots."""

from math import log10

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QWheelEvent
from PyQt6.QtWidgets import QWidget
from scipy.signal import welch

from .audio import Audio, FloatArray


def mean_spectrum(audio: Audio) -> tuple[FloatArray, FloatArray]:
    """Average up to 128 short overlapping periodograms across the recording."""
    size = min(audio.frames, 8192)
    starts = np.linspace(
        0, audio.frames - size, num=min(128, max(1, audio.frames // (size // 2 or 1))), dtype=int
    )
    power = np.zeros(size // 2 + 1)
    frequencies = np.fft.rfftfreq(size, 1 / audio.sample_rate)
    for start in starts:
        _, density = welch(
            audio.samples[start : start + size],
            fs=audio.sample_rate,
            nperseg=size,
            axis=0,
            detrend=False,
        )
        power += density.mean(axis=1)
    return frequencies, 10 * np.log10(np.maximum(power / len(starts), 1e-15))


def stereo_points(audio: Audio) -> tuple[FloatArray, FloatArray] | None:
    if audio.channels != 2:
        return None
    samples = audio.samples[np.linspace(0, audio.frames - 1, min(audio.frames, 4096), dtype=int)]
    left, right = samples.T
    return (left - right) / 2, (left + right) / 2


class Trace(QWidget):
    """Draw a labeled spectrum, loudness timeline or L/R goniometer."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.curves: list[tuple[FloatArray, FloatArray]] = []
        self.setMinimumHeight(180)

    def set_curves(self, *curves: tuple[FloatArray, FloatArray]) -> None:
        self.curves = list(curves)
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(61, 15, max(1, self.width() - 84), max(1, self.height() - 52))
        painter.fillRect(box, QColor("#121718"))
        try:
            self._draw(painter, box)
        finally:
            painter.end()

    def _draw(self, painter: QPainter, box: QRectF) -> None:
        spectrum = self.kind == "spectrum"
        stereo = self.kind == "stereo"
        max_x = max((float(np.max(x)) for x, _ in self.curves if x.size), default=1)
        if spectrum:
            max_x = min(20000, max_x)
            if max_x <= 20:
                return
        x_low, x_high = (
            (log10(20), log10(max_x))
            if spectrum
            else ((-1, 1) if stereo else (0, max(max_x, 0.001)))
        )
        y_low, y_high = (-1, 1) if stereo else ((-140, 30) if spectrum else (-80, 20))
        painter.setPen(QColor("#364142"))
        for index in range(5):
            ratio = index / 4
            y = box.bottom() - ratio * box.height()
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
            x = box.left() + ratio * box.width()
            painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            painter.setPen(QColor("#a9b8b8"))
            level = y_low + ratio * (y_high - y_low)
            painter.drawText(QPointF(4, y + 4), f"{level:g}")
            position = x_low + ratio * (x_high - x_low)
            hz = 10**position if spectrum else 0
            if spectrum:
                label = f"{hz / 1000:.2g} kHz" if hz >= 999.5 else f"{hz:.3g} Hz"
            else:
                label = f"{position:g}" if stereo else f"{position:.3g} s"
            text_width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(
                QPointF(min(x - text_width / 2, self.width() - text_width - 2), box.bottom() + 20),
                label,
            )
            painter.setPen(QColor("#364142"))
        painter.setClipRect(box)
        for index, (horizontal, vertical) in enumerate(self.curves):
            painter.setPen(QPen(QColor("#56e5b5" if index == 0 else "#f2c87c"), 1.25))
            path = QPainterPath()
            connected = False
            for x, y in zip(horizontal, vertical, strict=True):
                if not np.isfinite(y) or (spectrum and not 20 <= x <= max_x):
                    connected = False
                    continue
                coordinate = log10(x) if spectrum else x
                point = QPointF(
                    box.left() + (coordinate - x_low) / (x_high - x_low) * box.width(),
                    box.bottom() - (y - y_low) / (y_high - y_low) * box.height(),
                )
                if stereo:
                    painter.drawPoint(point)
                elif connected:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                connected = True
            if not stereo:
                painter.drawPath(path)


class Timeline(QWidget):
    """Interactive signed envelope. Shift+drag selects a range."""

    seek = pyqtSignal(float, arguments=[])
    region = pyqtSignal(float, float, arguments=[])

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(145)
        self.duration = 0.0
        self.lows = np.empty(0)
        self.highs = np.empty(0)
        self.view = (0.0, 1.0)
        self.position = 0.0
        self.selection: tuple[float, float] | None = None
        self.anchor: float | None = None

    def set_envelope(self, lows: FloatArray, highs: FloatArray, duration: float) -> None:
        if lows is not self.lows:
            self.view = (0, 1)
            self.selection = None
        self.lows, self.highs, self.duration = lows, highs, duration
        self.update()

    def _box(self) -> QRectF:
        return QRectF(52, 10, max(1, self.width() - 73), max(1, self.height() - 37))

    def _fraction(self, x: float) -> float:
        box = self._box()
        ratio = min(1, max(0, (x - box.left()) / box.width()))
        return self.view[0] + ratio * (self.view[1] - self.view[0])

    def _x(self, fraction: float) -> float:
        box = self._box()
        start, end = self.view
        return box.left() + (fraction - start) * box.width() / (end - start)

    def set_position(self, seconds: float) -> None:
        self.position = seconds
        self.update()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or not self.duration:
            return
        fraction = self._fraction(event.position().x())
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.anchor = fraction
        else:
            self.seek.emit(fraction * self.duration)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if self.anchor is not None and event is not None:
            self.selection = (self.anchor, self._fraction(event.position().x()))
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if self.anchor is None or event is None:
            return
        low, high = sorted((self.anchor, self._fraction(event.position().x())))
        self.anchor = None
        if high > low:
            self.selection = (low, high)
            self.region.emit(low * self.duration, high * self.duration)
        self.update()

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        if event is None or not self.duration:
            return
        center = self._fraction(event.position().x())
        start, end = self.view
        delta = event.angleDelta().y()
        span = end - start
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            offset = -span * delta / 600
            left = min(1 - span, max(0, start + offset))
        else:
            ratio = (center - start) / span
            span = min(1.0, max(0.005, span * (0.8 if delta > 0 else 1.25)))
            left = min(1 - span, max(0, center - span * ratio))
        self.view = (left, left + span)
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        try:
            box = self._box()
            painter.fillRect(box, QColor("#121718"))
            scale = max(
                1.0,
                float(np.max(np.abs(self.lows), initial=0)),
                float(np.max(np.abs(self.highs), initial=0)),
            )
            painter.setPen(QColor("#98a9ab"))
            for ratio, label in ((0, f"{scale:.2g}"), (0.5, "0"), (1, f"{-scale:.2g}")):
                painter.drawText(QPointF(5, box.top() + ratio * box.height() + 5), label)
            for ratio in np.linspace(0, 1, 5):
                start, end = self.view
                value = self.duration * (start + ratio * (end - start))
                painter.drawText(
                    QPointF(box.left() + ratio * box.width() - 13, box.bottom() + 17),
                    f"{value:.3g} s",
                )
            if self.duration:
                self._render_signal(painter, box, scale)
        finally:
            painter.end()

    def _render_signal(self, painter: QPainter, box: QRectF, scale: float) -> None:
        painter.setClipRect(box)
        if self.selection is not None:
            low, high = sorted(self.selection)
            painter.fillRect(
                QRectF(self._x(low), box.top(), self._x(high) - self._x(low), box.height()),
                QColor("#283f37"),
            )
        painter.setPen(QColor("#364142"))
        painter.drawLine(
            QPointF(box.left(), box.center().y()), QPointF(box.right(), box.center().y())
        )
        path = QPainterPath()
        for index, (low, high) in enumerate(zip(self.lows, self.highs, strict=True)):
            x = self._x((index + 0.5) / len(self.lows))
            if box.left() <= x <= box.right():
                top = box.center().y() - float(high) / scale * box.height() / 2
                bottom = box.center().y() - float(low) / scale * box.height() / 2
                path.moveTo(x, top)
                path.lineTo(x, max(top + 1, bottom))
        painter.setPen(QColor("#56e5b5"))
        painter.drawPath(path)
        x = self._x(self.position / self.duration)
        painter.setPen(QColor("#e8f6f3"))
        painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
