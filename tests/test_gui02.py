"""Desktop workflows and plot interactions with generated sound."""

import json

import numpy as np
import pytest


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PyQt6.QtWidgets")
    core = pytest.importorskip("PyQt6.QtCore")

    from openaudiokit.audio import Audio, write_audio
    from openaudiokit.gui import MainWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    settings = core.QSettings(str(tmp_path / "settings.ini"), core.QSettings.Format.IniFormat)

    def configure_preferences(*args):
        return settings

    monkeypatch.setattr("openaudiokit.gui.QSettings", configure_preferences)
    window = MainWindow()
    rate = 8000
    time = np.arange(4 * rate) / rate
    left = 0.1 * np.sin(2 * np.pi * 440 * time)
    right = 0.15 * np.sin(2 * np.pi * 880 * time)
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    write_audio(a, Audio(np.column_stack((left, right)), rate))
    write_audio(b, Audio(np.column_stack((right, left)) / 2, rate))
    window.show()

    def wait():
        timer = core.QElapsedTimer()
        timer.start()
        while window.busy and timer.elapsed() < 15000:
            app.processEvents()
            core.QThread.msleep(5)
        app.processEvents()
        assert not window.busy

    window.open_path("A", a)
    wait()
    try:
        yield window, wait, a, b, app
    finally:
        wait()
        window.close()


def test_desktop_tabs_locale_and_interactive_plots(desktop):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest

    window, wait, a, b, app = desktop
    assert window.recent.currentText() == a.name
    assert "WAV" in window.overview_file.text()
    window.open_path("B", b)
    wait()
    assert window.tabs.count() == 8
    window._set_language("pt")
    assert window.tabs.tabText(0) == "Visão geral"
    assert window.chain.effects.itemText(0) == "Ganho"
    window._set_language("en")
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        assert not window.grab().isNull()
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    left, right = QPoint(100, 40), QPoint(350, 40)
    qt_flag = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(window.waveform, Qt.MouseButton.LeftButton, qt_flag, left)
    QTest.mouseMove(window.waveform, right)
    QTest.mouseRelease(window.waveform, Qt.MouseButton.LeftButton, qt_flag, right)
    assert window.waveform.selection is not None
    QTest.mouseClick(window.waveform, Qt.MouseButton.LeftButton, pos=right)
    window._on_position(3.5)
    assert "50 ms" in window.position_levels.text()
    window.scale.setCurrentIndex(1)
    assert window.spectrum.log_frequency
    assert window.overlay.curves[1][0][-1] == 4000
    assert window.comparison.item(0, 3).text().startswith("-")


def test_desktop_chains_preview_export_and_batch(desktop, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from openaudiokit.audio import load_audio

    window, wait, a, b, _ = desktop
    editor = window.chain
    editor.fields["db"].setText("6")
    editor._add()
    editor._reset()
    assert editor.fields["db"].text() == "0"
    editor.effects.setCurrentIndex(editor.effects.findData("invert_polarity"))
    editor._add()
    editor._move(-1)
    assert editor.entries[0].name == "invert_polarity"
    editor._select()
    assert editor.effects.currentData() == "invert_polarity"
    editor._remove()
    assert len(editor.entries) == 1
    editor.fields["db"].setText("[")
    editor._add()
    assert "Error" in window.status.text()
    editor._reset()

    preset = tmp_path / "gain.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(preset), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(preset), ""))
    editor._save()
    editor.entries.clear()
    editor._load()
    assert editor.entries[0].params["db"] == 6

    editor._render()
    wait()
    assert window._preview is not None
    change = window._preview.report.metrics.peak_dbfs - window.sources["A"].report.metrics.peak_dbfs
    assert change == pytest.approx(6)
    window.export_audio(a)
    assert "Error" in window.status.text()
    exported = tmp_path / "processed.flac"
    window.export_audio(exported)
    wait()
    assert exported.is_file()
    assert np.max(load_audio(a).samples) < np.max(load_audio(exported).samples)

    editor.bypass.setChecked(True)
    editor._render()
    wait()
    assert window._preview.report.metrics.peak_dbfs == window.sources["A"].report.metrics.peak_dbfs
    window.open_path("A", b)
    wait()
    assert window._preview is None

    output = tmp_path / "batch"
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(a), str(b)], ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(output))
    window._pick_batch()
    wait()
    results = json.loads((output / "batch-report.json").read_text())
    assert all(result["error"] is None for result in results)
    assert "2/2" in window.batch_output.text()


def test_plot_models_bound_short_files():
    pytest.importorskip("PyQt6")
    from openaudiokit.audio import Audio
    from openaudiokit.gui_views import mean_spectrum, stereo_points

    first, second = mean_spectrum(Audio([0], 8000))
    np.testing.assert_array_equal(first, [0])
    np.testing.assert_array_equal(second, [-150])
    assert stereo_points(Audio([0], 8000)) is None
    side, mid = stereo_points(Audio([[1, -1], [0.5, 0.5]], 8000))
    np.testing.assert_array_equal(side, [1, 0])
    np.testing.assert_array_equal(mid, [0, 0.5])
