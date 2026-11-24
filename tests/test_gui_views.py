"""Numerical plot models and offscreen waveform interaction."""

import numpy as np
import pytest

from openaudiokit import Audio


@pytest.fixture
def qt(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


def test_plot_models_and_rendering(qt):
    from openaudiokit.gui_views import Trace, mean_spectrum, stereo_points

    rate = 8192
    tone = np.sin(2 * np.pi * 440 * np.arange(rate) / rate)
    mono = Audio(tone, rate)
    stereo = Audio(np.column_stack((tone, -tone)), rate)
    hz, density = mean_spectrum(stereo)
    assert hz[np.argmax(density)] == pytest.approx(440)
    assert density == pytest.approx(mean_spectrum(mono)[1])
    assert stereo_points(mono) is None
    side, mid = stereo_points(stereo)
    assert side.size == 4096
    assert np.max(np.abs(side)) > 0.99
    assert not mid.any()
    for name, curves in (
        ("spectrum", [(hz, density), (hz, density - 6)]),
        ("loudness", [(np.arange(4), np.array([-np.inf, -26, -20, -24]))]),
        ("stereo", [(side, mid)]),
    ):
        trace = Trace(name)
        trace.resize(500, 220)
        trace.set_curves(*curves)
        assert not trace.grab().isNull()
        trace.close()


def test_waveform_seek_selection_zoom_and_pan(qt):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtTest import QTest

    from openaudiokit.gui_views import Timeline

    view = Timeline()
    view.resize(500, 180)
    view.show()
    qt.processEvents()
    lows, highs = np.full(100, -0.2), np.full(100, 0.3)
    view.set_envelope(lows, highs, 10)
    events = []
    view.seek.connect(lambda seconds: events.append(seconds))
    view.region.connect(lambda start, end: events.append((start, end)))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(266, 80))
    assert events[-1] == pytest.approx(5, abs=0.03)
    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(view, Qt.MouseButton.LeftButton, shift, QPoint(150, 80))
    QTest.mouseMove(view, QPoint(300, 80))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, shift, QPoint(350, 80))
    assert events[-1] == pytest.approx((2.30, 6.98), abs=0.03)

    def wheel(delta, modifiers):
        pos = QPointF(260, 80)
        view.wheelEvent(
            QWheelEvent(
                pos,
                pos,
                QPoint(),
                QPoint(0, delta),
                Qt.MouseButton.NoButton,
                modifiers,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        )

    wheel(120, Qt.KeyboardModifier.NoModifier)
    assert view.view[1] - view.view[0] == pytest.approx(0.8)
    before = view.view
    wheel(-120, shift)
    assert view.view[0] > before[0]
    view.set_position(5)
    assert not view.grab().isNull()
    view.set_envelope(lows.copy(), highs, 10)
    assert view.view == (0, 1)
    assert view.selection is None
    view.close()
