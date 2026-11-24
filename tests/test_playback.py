"""Verify transport setup and Qt decoding without relying on a sound device."""

from math import inf
from pathlib import Path

import numpy as np
import pytest


def test_matching_gain():
    pytest.importorskip("PyQt6.QtMultimedia")
    from openaudiokit.playback import matching_gain

    assert matching_gain(-18, -24) == pytest.approx(10 ** (-6 / 20))
    assert matching_gain(-24, -18) == 1
    assert matching_gain(None, -18) == 1
    assert matching_gain(-18, -inf) == 1


def test_player_load_seek_switch_and_release(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    core = pytest.importorskip("PyQt6.QtCore")
    multimedia = pytest.importorskip("PyQt6.QtMultimedia")

    from openaudiokit import Audio, write_audio
    from openaudiokit.playback import PlaybackBar

    application = widgets.QApplication.instance() or widgets.QApplication([])
    rate = 8000
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    wave = np.sin(2 * np.pi * 440 * np.arange(rate * 2) / rate)
    write_audio(a, Audio(wave * 0.2, rate), subtype="PCM_16")
    write_audio(b, Audio(wave * 0.4, rate), subtype="PCM_16")

    def wait(condition) -> None:
        timer = core.QElapsedTimer()
        timer.start()
        while not condition() and timer.elapsed() < 4000:
            application.processEvents()
            core.QThread.msleep(5)
        application.processEvents()
        assert condition()

    bar = PlaybackBar()
    slots = []
    bar.playback_source_changed.connect(slots.append)
    try:
        assert not bar.play_toggle.isEnabled()
        bar.set_language("pt-BR")
        assert bar.stop_button.text() == "Parar"
        bar.set_source("A", a, -24)
        bar.set_source("B", b, -18)
        assert bar.match.isEnabled()
        wait(
            lambda: (
                bar.player.mediaStatus()
                in (
                    multimedia.QMediaPlayer.MediaStatus.LoadedMedia,
                    multimedia.QMediaPlayer.MediaStatus.InvalidMedia,
                )
            )
        )
        if bar.player.mediaStatus() == multimedia.QMediaPlayer.MediaStatus.InvalidMedia:
            pytest.skip(f"Qt WAV playback backend unavailable: {bar.player.errorString()}")
        wait(lambda: bar.player.isSeekable() and bar.player.duration() == 2000)
        bar.seek_seconds(0.8)
        wait(lambda: bar.player.position() == 800)
        bar.set_region(0, 1.8)
        assert bar.selection.text() == "0.00–1.80 s"
        bar.match.setChecked(True)
        bar.select_source("B")
        wait(lambda: bar.player.position() == 800 and bar.player.isSeekable())
        wait(lambda: abs(bar.output.volume() - 0.7 * 10 ** (-6 / 20)) < 1e-6)
        assert slots == ["A", "B"]
        assert bar.player.source().toLocalFile() == b.as_posix()
        bar.stop()
        assert bar.player.position() == 0
        bar.clear()
        assert bar.player.source().isEmpty()
        assert not bar.play_toggle.isEnabled()
    finally:
        bar.clear()
        bar.close()


def test_running_transport_loops_scrubs_and_switches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    core = pytest.importorskip("PyQt6.QtCore")
    media = pytest.importorskip("PyQt6.QtMultimedia")
    from openaudiokit.playback import PlaybackBar

    application = widgets.QApplication.instance() or widgets.QApplication([])
    bar = PlaybackBar()
    player = bar.player
    playing = media.QMediaPlayer.PlaybackState.PlayingState
    stopped = media.QMediaPlayer.PlaybackState.StoppedState
    paused = media.QMediaPlayer.PlaybackState.PausedState
    loaded = media.QMediaPlayer.MediaStatus.LoadedMedia
    ended = media.QMediaPlayer.MediaStatus.EndOfMedia
    backend = {"position": 0, "url": core.QUrl(), "state": stopped, "duration": 2000}

    def state(value):
        backend["state"] = value
        player.playbackStateChanged.emit(value)

    def seek(position):
        backend["position"] = position
        player.positionChanged.emit(position)

    def source(url):
        backend["url"] = url
        state(stopped)
        seek(0)
        player.durationChanged.emit(backend["duration"])

    monkeypatch.setattr(player, "setSource", source)
    monkeypatch.setattr(player, "source", lambda: backend["url"])
    monkeypatch.setattr(player, "position", lambda: backend["position"])
    monkeypatch.setattr(player, "setPosition", seek)
    monkeypatch.setattr(player, "duration", lambda: backend["duration"])
    monkeypatch.setattr(player, "isSeekable", lambda: True)
    monkeypatch.setattr(player, "playbackState", lambda: backend["state"])
    monkeypatch.setattr(player, "play", lambda: state(playing))
    monkeypatch.setattr(player, "pause", lambda: state(paused))
    monkeypatch.setattr(player, "stop", lambda: state(stopped))

    def finish_ramp() -> None:
        for _ in range(10):
            bar._step_gain()

    try:
        bar.set_source("A", tmp_path / "a.wav", -24)
        bar.set_source("B", tmp_path / "b.wav", -18)
        bar.toggle()  # queue play while source metadata loads
        bar.seek_seconds(0.3)
        player.mediaStatusChanged.emit(loaded)
        assert backend["state"] == playing
        assert bar.play_toggle.text() == "Pause"

        bar.progress.setSliderPosition(675)
        bar.progress.sliderReleased.emit()
        assert backend["position"] == 675
        bar.progress.triggerAction(widgets.QAbstractSlider.SliderAction.SliderSingleStepAdd)
        assert backend["position"] == 676

        bar.set_region(0.5, 1.0)
        bar.loop.setChecked(True)
        seek(1001)
        assert backend["position"] == 500
        state(stopped)
        player.mediaStatusChanged.emit(ended)
        assert backend["state"] == playing
        assert backend["position"] == 500

        bar.loop.setChecked(False)
        seek(850)
        finish_ramp()
        bar.match.setChecked(True)
        bar.select_source("B")
        assert backend["url"].fileName() == "a.wav"
        finish_ramp()
        assert backend["url"].fileName() == "b.wav"
        assert bar.output.volume() == 0
        player.mediaStatusChanged.emit(loaded)
        finish_ramp()
        assert backend["position"] == 850
        assert backend["state"] == playing
        assert bar.output.volume() == pytest.approx(0.7 * 10 ** (-6 / 20))

        bar.toggle()
        assert backend["state"] == paused
        bar.set_region(1.0, 1.0)
        assert not bar.selection.text()
        bar.stop()
        assert backend["state"] == stopped
        assert backend["position"] == 0

        player.errorOccurred.emit(media.QMediaPlayer.Error.ResourceError, "missing media")
        assert "missing media" in bar.error.text()
        bar.set_source("B", None, None)
        player.mediaStatusChanged.emit(loaded)
        assert backend["url"].fileName() == "a.wav"
        assert not bar.match.isEnabled()
    finally:
        bar.clear()
        bar.deleteLater()
        application.processEvents()
