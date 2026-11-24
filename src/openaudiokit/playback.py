"""Desktop player with time-aligned clip switching and attenuation-only level matching."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class _Clip:
    path: Path
    lufs: float | None


def matching_gain(selected_lufs: float | None, other_lufs: float | None) -> float:
    """Match the louder source to the quieter one without boosting either source."""
    if selected_lufs is None or other_lufs is None:
        return 1.0
    if not isfinite(selected_lufs) or not isfinite(other_lufs):
        return 1.0
    return 10.0 ** (min(0.0, other_lufs - selected_lufs) / 20)


def _clock(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class PlaybackBar(QWidget):
    """Select A, B, or a rendered preview and audition them through Qt Multimedia."""

    position_seconds = pyqtSignal(float, arguments=["seconds"])
    playback_source_changed = pyqtSignal(str, arguments=["slot"])

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clips: dict[str, _Clip] = {}
        self._slot: str | None = None
        self._pending_slot: str | None = None
        self._pending_seek: int | None = None
        self._resume = False
        self._regions: dict[str, tuple[int, int]] = {}
        self._language = "en"
        self._gain = 0.0
        self._gain_target = 0.0
        self._ramp = QTimer(self)
        self._ramp.setInterval(8)
        self._ramp.timeout.connect(self._step_gain)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0)
        self._connect_controls()
        self.set_language("en")
        self._update_enabled()

    def _connect_controls(self) -> None:
        self.source = QComboBox()
        for slot in ("A", "B", "Processed"):
            self.source.addItem(slot, slot)
        self.source.currentIndexChanged.connect(self._chosen)
        self.play_toggle = QPushButton()
        self.play_toggle.clicked.connect(self.toggle)
        self.stop_button = QPushButton()
        self.stop_button.clicked.connect(self.stop)
        self.loop = QCheckBox()
        self.match = QCheckBox()
        self.match.toggled.connect(self._update_gain)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setMaximumWidth(104)
        self.volume.valueChanged.connect(self._update_gain)
        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setTracking(False)
        self.progress.setRange(0, 0)
        self.progress.sliderMoved.connect(self._preview_position)
        self.progress.sliderReleased.connect(self._seek_slider)
        self.progress.actionTriggered.connect(self._slider_action)
        self.clock = QLabel("00:00 / 00:00")
        self.selection = QLabel()
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._update_play_text)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.seekableChanged.connect(self._update_enabled)
        self.player.errorOccurred.connect(self._player_error)

        transport = QHBoxLayout()
        for control in (self.source, self.play_toggle, self.stop_button, self.loop, self.match):
            transport.addWidget(control)
        transport.addStretch()
        transport.addWidget(self.volume)

        timeline = QHBoxLayout()
        timeline.addWidget(self.progress, 1)
        timeline.addWidget(self.clock)
        timeline.addWidget(self.selection)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(transport)
        layout.addLayout(timeline)
        layout.addWidget(self.error)

    def _tr(self, english: str, portuguese: str) -> str:
        return portuguese if self._language.startswith("pt") else english

    def set_language(self, language: str) -> None:
        """Set control labels to English or Portuguese."""
        self._language = language.lower()
        self.stop_button.setText(self._tr("Stop", "Parar"))
        self.loop.setText(self._tr("Loop", "Repetir"))
        self.match.setText(self._tr("Match A/B level", "Compensar nível A/B"))
        self.match.setToolTip(
            self._tr(
                "Attenuates the louder file using integrated LUFS.",
                "Atenua o arquivo mais alto com base no LUFS integrado.",
            )
        )
        self.source.setAccessibleName(self._tr("Playback source", "Fonte de reprodução"))
        self.volume.setAccessibleName(self._tr("Playback volume", "Volume de reprodução"))
        self.volume.setToolTip(self.volume.accessibleName())
        self.progress.setAccessibleName(self._tr("Playback position", "Posição da reprodução"))
        self.source.setItemText(2, self._tr("Processed", "Processado"))
        self._update_play_text()
        self._update_region_label()

    def set_source(self, slot: str, path: Path | None, integrated_lufs: float | None) -> None:
        """Register a playable file and its precomputed whole-file loudness."""
        if self.source.findData(slot) < 0:
            raise ValueError(f"Unknown playback slot {slot!r}.")
        previous = self._clips.get(slot)
        if path is None:
            self._clips.pop(slot, None)
            self._regions.pop(slot, None)
            if self._pending_slot == slot:
                self._pending_slot = None
            if self._slot == slot:
                next_slot = next(iter(self._clips), None)
                if next_slot is None:
                    self.stop()
                    self._slot = None
                    self.player.setSource(QUrl())
                else:
                    self.select_source(next_slot)
        else:
            self._clips[slot] = _Clip(path, integrated_lufs)
            if previous is None or previous.path != path:
                self._regions.pop(slot, None)
                if self._slot is None or self._slot == slot:
                    self.select_source(slot)
        self._update_gain()
        self._update_region_label()
        self._update_enabled()

    def _chosen(self, index: int) -> None:
        slot = self.source.itemData(index)
        if isinstance(slot, str):
            self.select_source(slot)

    def select_source(self, slot: str) -> None:
        """Switch source while preserving elapsed time and play/pause intent."""
        clip = self._clips.get(slot)
        if clip is None:
            return
        if self._slot == slot and self.player.source() == QUrl.fromLocalFile(
            str(clip.path.resolve())
        ):
            self._pending_slot = None
            self._show_slot(slot)
            self._update_gain()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and self._gain:
            self._pending_slot = slot
            self._target_gain(0)
        else:
            self._apply_source(slot)

    def _apply_source(self, slot: str) -> None:
        self._pending_slot = None
        self._resume = self._resume or (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if self._pending_seek is None:
            self._pending_seek = self.player.position()
        self._slot = slot
        self._gain = 0.0
        self.output.setVolume(0)
        self._gain_target = 0.0
        self.error.hide()
        self._show_slot(slot)
        self.player.setSource(QUrl.fromLocalFile(str(self._clips[slot].path.resolve())))
        self.playback_source_changed.emit(slot)
        self._update_region_label()
        self._update_enabled()

    def _show_slot(self, slot: str) -> None:
        was_blocked = self.source.blockSignals(True)
        self.source.setCurrentIndex(self.source.findData(slot))
        self.source.blockSignals(was_blocked)

    def _media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if (
            status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)
            and self._pending_seek is not None
        ):
            position = self._pending_seek
            self._pending_seek = None
            self._resume, resume = False, self._resume
            self.player.setPosition(min(position, self.player.duration()))
            self._update_gain()
            if resume and (not self.player.duration() or position < self.player.duration()):
                self._start()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self.loop.isChecked():
            interval = self._regions.get(self._slot or "")
            self.player.setPosition(interval[0] if interval else 0)
            self.player.play()
        self._update_enabled()

    def _start(self) -> None:
        region = self._regions.get(self._slot or "") if self.loop.isChecked() else None
        if region and not region[0] <= self.player.position() < region[1]:
            self.player.setPosition(region[0])
        elif self.player.position() >= self.player.duration() > 0:
            self.player.setPosition(0)
        self.player.play()

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._resume = False
            self.player.pause()
        elif self._slot is not None:
            if self._pending_seek is not None:
                self._resume = True
            else:
                self._start()

    def stop(self) -> None:
        self._resume = False
        self._pending_slot = None
        if self._slot is not None:
            self._show_slot(self._slot)
        if self._pending_seek is not None:
            self._pending_seek = 0
        self.player.stop()
        self.player.setPosition(0)
        self._update_gain()

    def seek_seconds(self, seconds: float) -> None:
        """Seek in the active source timeline."""
        if self._slot is None or not isfinite(seconds):
            return
        position = round(max(0, seconds) * 1000)
        if self._pending_seek is not None:
            self._pending_seek = position
        elif self.player.isSeekable():
            self.player.setPosition(min(position, self.player.duration()))

    def set_region(self, start: float, end: float) -> None:
        """Select a per-source interval in seconds. Equal boundaries clear it."""
        if self._slot is None or not isfinite(start) or not isfinite(end):
            return
        lo, hi = sorted((round(max(0, start) * 1000), round(max(0, end) * 1000)))
        duration = self.player.duration()
        if duration:
            lo, hi = min(lo, duration), min(hi, duration)
        if hi - lo < 100:
            self._regions.pop(self._slot, None)
        else:
            self._regions[self._slot] = (lo, hi)
        self._update_region_label()

    def _position_changed(self, position: int) -> None:
        if not self.progress.isSliderDown():
            self.progress.setValue(position)
            self._preview_position(position)
        self.position_seconds.emit(position / 1000)
        if self.loop.isChecked() and (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            region = self._regions.get(self._slot or "")
            if region and position >= region[1]:
                self.player.setPosition(region[0])

    def _duration_changed(self, duration: int) -> None:
        self.progress.setRange(0, min(duration, 2_147_483_647))
        self._preview_position(self.player.position())
        self._update_enabled()

    def _slider_action(self, _action: int) -> None:
        if not self.progress.isSliderDown():
            self.seek_seconds(self.progress.sliderPosition() / 1000)

    def _seek_slider(self) -> None:
        self.seek_seconds(self.progress.sliderPosition() / 1000)

    def _preview_position(self, position: int) -> None:
        self.clock.setText(f"{_clock(position)} / {_clock(self.player.duration())}")

    def _update_region_label(self) -> None:
        region = self._regions.get(self._slot or "")
        self.selection.setText(f"{region[0] / 1000:.2f}–{region[1] / 1000:.2f} s" if region else "")

    def _update_play_text(self, *_args: object) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_toggle.setText(
            self._tr("Pause", "Pausar") if playing else self._tr("Play", "Reproduzir")
        )

    def _update_enabled(self, *_args: object) -> None:
        model = self.source.model()
        if isinstance(model, QStandardItemModel):
            for index in range(self.source.count()):
                entry = model.item(index)
                if entry is not None:
                    entry.setEnabled(self.source.itemData(index) in self._clips)
        active = self._slot in self._clips
        self.play_toggle.setEnabled(active)
        self.stop_button.setEnabled(active)
        self.progress.setEnabled(active and self.player.isSeekable())
        levels = [self._clips[slot].lufs for slot in ("A", "B") if slot in self._clips]
        self.match.setEnabled(
            len(levels) == 2 and all(level is not None and isfinite(level) for level in levels)
        )

    def _player_error(self, _error: QMediaPlayer.Error, detail: str) -> None:
        self._pending_seek = None
        self._resume = False
        self.error.setText(self._tr("Playback unavailable. ", "Reprodução indisponível. ") + detail)
        self.error.show()

    def _update_gain(self, *_args: object) -> None:
        if self._pending_slot is not None or self._pending_seek is not None:
            return
        gain = self.volume.value() / 100
        if self.match.isChecked() and self._slot in {"A", "B"}:
            other = self._clips.get("B" if self._slot == "A" else "A")
            current = self._clips.get(self._slot or "")
            if other and current:
                gain *= matching_gain(current.lufs, other.lufs)
        self._target_gain(gain)

    def _target_gain(self, gain: float) -> None:
        self._gain_target = gain
        self._ramp.start()

    def _step_gain(self) -> None:
        difference = self._gain_target - self._gain
        self._gain += max(-0.12, min(0.12, difference))
        self.output.setVolume(self._gain)
        if abs(difference) <= 0.12:
            self._ramp.stop()
            if self._pending_slot is not None:
                self._apply_source(self._pending_slot)

    def clear(self) -> None:
        """Release the active media source, including open temporary preview handles."""
        self.stop()
        self._ramp.stop()
        self._clips.clear()
        self._regions.clear()
        self._slot = None
        self._pending_seek = None
        self.player.setSource(QUrl())
        self._update_region_label()
        self._update_enabled()
