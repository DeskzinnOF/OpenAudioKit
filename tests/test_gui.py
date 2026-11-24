"""Smoke tests for the optional desktop interface."""

import json
from pathlib import Path

import numpy as np
import pytest


def test_desktop_loads_compares_renders_and_exports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PyQt6.QtWidgets")
    core = pytest.importorskip("PyQt6.QtCore")

    from openaudiokit.audio import Audio, write_audio
    from openaudiokit.gui import MainWindow

    app = qt.QApplication.instance() or qt.QApplication([])
    rate = 8000
    wave = np.sin(2 * np.pi * 440 * np.arange(rate) / rate)
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    write_audio(a, Audio(wave * 0.25, rate))
    write_audio(b, Audio(wave * 0.5, rate))

    window = MainWindow()
    window.show()

    def wait() -> None:
        timer = core.QElapsedTimer()
        timer.start()
        while window.busy and timer.elapsed() < 15000:
            app.processEvents()
            core.QThread.msleep(5)
        app.processEvents()
        assert not window.busy

    try:
        window.open_path("A", a)
        assert window.busy
        wait()
        window.open_path("B", b)
        wait()
        assert window.sources["A"].report.metrics.peak_dbfs == pytest.approx(-12.04, abs=0.02)
        assert window.sources["B"].report.metrics.peak_dbfs == pytest.approx(-6.02, abs=0.02)
        assert window.comparison.item(0, 2).text() != "—"

        window.switch_source("B")
        assert window.active == "B"
        assert not window.grab().isNull()

        def require_48khz() -> None:
            dialog = app.activeModalWidget()
            dialog.rate.setValue(48000)
            dialog.accept()

        core.QTimer.singleShot(0, require_48khz)
        window.settings.click()
        wait()
        assert "sample_rate_mismatch" in {issue.code for issue in window.sources["B"].report.issues}

        window.export_report(tmp_path / "report.html", comparison=True)
        wait()
        assert (tmp_path / "report.html").is_file()
        window.export_report(tmp_path / "qc.json")
        wait()
        assert (
            json.loads((tmp_path / "qc.json").read_text())["config"]["expected_sample_rate"]
            == 48000
        )

        window.open_path("B", tmp_path / "missing.wav")
        wait()
        assert window.sources["B"].path == b
        assert "Error" in window.status.text()
    finally:
        wait()
        window.close()
