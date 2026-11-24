"""Optional PyQt6 desktop interface for local audio quality control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Literal
from unicodedata import normalize

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .audio import Audio, FloatArray, load_audio, write_audio
from .batch import Operation, Progress, process_audio, run_batch
from .config import QCConfig
from .gui_processing import ChainEditor
from .gui_views import Timeline, Trace, mean_spectrum, stereo_points
from .loudness import LoudnessHistory, loudness_history
from .playback import PlaybackBar
from .qc import QCReport, compare, inspect
from .report import write_report
from .spectral import spectrogram

Slot = Literal["A", "B"]
_MAX_SAMPLES = 32_000_000
_OPEN_FILTER = "Audio (*.wav *.flac *.aif *.aiff *.ogg *.au *.caf *.mp3);;All files (*)"
_SAVE_FILTER = "JSON (*.json);;CSV (*.csv);;HTML (*.html)"
_SLOTS: tuple[Slot, Slot] = ("A", "B")
_COLORS = np.array([[17, 23, 37], [37, 56, 111], [65, 170, 201], [241, 239, 174]])
_PALETTE = np.column_stack(
    [np.interp(np.arange(256), [0, 75, 170, 255], _COLORS[:, ch]) for ch in range(3)]
).astype(np.uint8)

_STYLE = """
QWidget { background: #141819; color: #e8eef9; font-size: 13px; }
QFrame[card="true"] { background: #1d2425; border: 1px solid #303a3b; border-radius: 10px; }
QFrame[card="true"] QLabel, QFrame[card="true"] QWidget[plot="true"] {
    background: transparent;
}
QLabel[role="title"] { font-size: 25px; font-weight: 700; }
QLabel[role="heading"] { font-size: 16px; font-weight: 650; }
QLabel[role="subtle"] { color: #a6b6b8; }
QLabel[role="status"] { color: #a6b6b8; }
QLabel[role="error"] { color: #ff9daa; }
QPushButton {
    background: #273132; color: #e8eef9; border: 1px solid #465454;
    padding: 7px 12px; border-radius: 6px; font-weight: 600;
}
QPushButton:hover { background: #2f4162; }
QPushButton:checked, QPushButton[primary="true"] {
    background: #204a50; color: #caffed; border-color: #3c827e;
}
QPushButton:checked:hover, QPushButton[primary="true"]:hover { background: #2b6267; }
QPushButton:disabled { background: #1b273c; color: #8193b2; border-color: #303a3b; }
QPushButton:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: #7bdfc7; }
QProgressBar { background: #121718; border: 0; border-radius: 4px; min-height: 7px; }
QProgressBar::chunk { background: #6be0bd; border-radius: 4px; }
QTableWidget { background: #1d2425; alternate-background-color: #222b2c; border: 0; }
QHeaderView::section { background: #2a3535; color: #e8eef9; border: 0; padding: 6px; }

QTabWidget::pane { border: 0; }
QTabBar::tab { background: #1c2223; padding: 9px 10px; color: #a6b6b8; }
QTabBar::tab:selected { background: #273632; color: #78e6bc; border-bottom: 2px solid #50dba6; }
QComboBox, QLineEdit, QListWidget { background: #1d2626; border: 1px solid #364142; padding: 4px; }
QSlider::groove:horizontal { background: #273131; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #54dcb0; border-radius: 2px; }
QSlider::handle:horizontal { background: #a5f4db; border: 1px solid #30614f; width: 9px; margin: -3px 0; border-radius: 4px; }
QSlider::handle:horizontal:disabled { background: #475656; border-color: #344040; }
QSpinBox, QDoubleSpinBox {
    background: #273132; border: 1px solid #465454; border-radius: 5px; padding: 4px;
}
"""


@dataclass(frozen=True)
class _Source:
    path: Path
    audio: Audio
    report: QCReport
    lows: FloatArray
    highs: FloatArray
    image: QImage
    nyquist_hz: float
    log_image: QImage
    frequency: tuple[FloatArray, FloatArray]
    loudness: LoudnessHistory
    stereo: tuple[FloatArray, FloatArray] | None


@dataclass(frozen=True)
class _Preview:
    source: _Source


@dataclass(frozen=True)
class _WorkspaceState:
    sources: dict[Slot, _Source]
    config: QCConfig


def _envelope(audio: Audio, width: int = 1200) -> tuple[FloatArray, FloatArray]:
    """Retain signed extrema from every channel in each display bin."""
    count = min(audio.frames, width)
    starts = np.arange(count, dtype=np.int64) * audio.frames // count
    return (
        np.min(np.minimum.reduceat(audio.samples, starts, axis=0), axis=1),
        np.max(np.maximum.reduceat(audio.samples, starts, axis=0), axis=1),
    )


def _read_source(path: Path, audio: Audio, config: QCConfig) -> _Source:
    report = inspect(audio, config=config)
    lows, highs = _envelope(audio)
    # Bound the number of display tiles and their working set for long files.
    columns = min(1000, 12_000 // audio.channels)
    hop = max(256, (audio.frames + columns - 1) // columns)
    result = spectrogram(audio, n_fft=1024, hop=hop)
    db = 10 * np.log10(np.maximum(result.power, 1e-10))
    indices = np.clip((db + 100) * 2.55, 0, 255).astype(np.uint8)
    rgb = np.ascontiguousarray(_PALETTE[indices][::-1])
    height, width, _ = rgb.shape
    image = QImage(rgb.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888).copy()

    nyquist = float(result.frequencies_hz[-1])
    log_image = image
    if nyquist > 20:
        log_rows = np.rint(np.geomspace(20, nyquist, height) / nyquist * (height - 1)).astype(int)
        log_rgb = np.ascontiguousarray(_PALETTE[indices[log_rows[::-1]]])
        log_image = QImage(
            log_rgb.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888
        ).copy()
    return _Source(
        path,
        audio,
        report,
        lows,
        highs,
        image,
        nyquist,
        log_image,
        mean_spectrum(audio),
        loudness_history(audio),
        stereo_points(audio),
    )


class _Worker(QThread):
    succeeded = pyqtSignal(object, arguments=[])
    failed = pyqtSignal(str, arguments=[])

    def __init__(
        self, parent: QWidget, run_job: Callable[[], _WorkspaceState | _Preview | Path]
    ) -> None:
        super().__init__(parent)
        self._run_job = run_job

    def run(self) -> None:
        """Perform I/O and DSP outside the GUI thread."""
        try:
            outcome = self._run_job()
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(outcome)


def _title(text: str, role: str = "heading") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    return label


def _card() -> tuple[QFrame, QVBoxLayout]:
    widget = QFrame()
    widget.setProperty("card", True)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(13, 11, 13, 11)
    layout.setSpacing(6)
    return widget, layout


def _number(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


class _Plot(QWidget):
    def __init__(self, *, spectrum: bool) -> None:
        super().__init__()
        self.setProperty("plot", True)
        self.setMinimumHeight(116)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Spectrogram" if spectrum else "Waveform")
        self._source: _Source | None = None
        self._spectrum = spectrum
        self.log_frequency = False

    def set_source(self, source: _Source | None) -> None:
        self._source = source
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        try:
            self._paint_plot(painter)
        finally:
            painter.end()

    def _paint_plot(self, painter: QPainter) -> None:
        box = QRectF(58, 8, max(self.width() - 81, 1), max(self.height() - 31, 1))
        painter.fillRect(box, QColor("#121718"))
        source = self._source
        if source is None:
            painter.setPen(QColor("#a6b6b8"))
            painter.drawText(
                box, Qt.AlignmentFlag.AlignCenter, "Open an audio file to view this plot"
            )
            return
        if self._spectrum:
            scale = source.nyquist_hz

            axis = (f"{scale / 1000:.2g} kHz", f"{scale / 2000:.2g} kHz", "0 Hz")
            if self.log_frequency and scale > 20:
                axis = (f"{scale / 1000:.2g} kHz", f"{(scale * 20) ** 0.5:.3g} Hz", "20 Hz")
        else:
            scale = max(1.0, float(np.max(source.highs)), -float(np.min(source.lows)))
            axis = (f"{scale:.2g}", "0", f"{-scale:.2g}")
        self._draw_axes(painter, box, axis, source.audio.duration)

        if self._spectrum:
            painter.drawImage(box, source.log_image if self.log_frequency else source.image)
        else:
            painter.setPen(QPen(QColor("#6be0bd"), 1))
            path = QPainterPath()
            step = box.width() / len(source.highs)
            for index, (low, high) in enumerate(zip(source.lows, source.highs, strict=True)):
                x = box.left() + (index + 0.5) * step
                y_low = box.center().y() - float(low) / scale * box.height() / 2
                y_high = box.center().y() - float(high) / scale * box.height() / 2
                path.moveTo(x, y_high)
                path.lineTo(x, max(y_high + 0.8, y_low))
            painter.save()
            painter.setClipRect(box)
            painter.drawPath(path)
            painter.restore()
        painter.setPen(QColor("#465454"))
        painter.drawRect(box)

    @staticmethod
    def _draw_axes(
        painter: QPainter, box: QRectF, axis: tuple[str, str, str], duration: float
    ) -> None:
        for index, label in enumerate(axis):
            y = box.top() + index * box.height() / 2
            painter.setPen(QColor("#2c3b55"))
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
            painter.setPen(QColor("#a6b6b8"))
            painter.drawText(
                QRectF(0, y - 8, box.left() - 7, 17),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        for index in range(5):
            x = box.left() + index * box.width() / 4
            painter.setPen(QColor("#2c3b55"))
            painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            painter.setPen(QColor("#a6b6b8"))
            painter.drawText(
                QRectF(x - 37, box.bottom() + 3, 74, 18),
                Qt.AlignmentFlag.AlignHCenter,
                f"{duration * index / 4:.4g} s",
            )


class _Settings(QDialog):
    def __init__(self, parent: QWidget, config: QCConfig, language: str = "en") -> None:
        super().__init__(parent)

        def tr(en: str, pt: str) -> str:
            return pt if language == "pt" else en

        self.setWindowTitle(tr("Quality control settings", "Ajustes de controle de qualidade"))
        self.setMinimumWidth(370)
        self._base = config
        layout = QFormLayout(self)
        self.rate = QSpinBox()
        self.rate.setRange(0, 384000)
        self.rate.setSpecialValueText(tr("Any sample rate", "Qualquer taxa"))
        self.rate.setValue(config.expected_sample_rate or 0)
        layout.addRow(tr("Expected sample rate (Hz)", "Taxa esperada (Hz)"), self.rate)
        self.clipping = self._field(
            layout,
            tr("Clipping threshold (linear)", "Limite de amplitude (linear)"),
            1e-9,
            1,
            config.clipping_level,
            9,
        )
        self.ceiling = self._field(
            layout,
            tr("True peak ceiling (dBTP)", "Teto de true peak (dBTP)"),
            -300,
            0,
            config.true_peak_ceiling,
        )
        self.dc = self._field(
            layout, tr("DC threshold (linear)", "Limite DC (linear)"), 0, 1, config.dc_threshold, 5
        )
        self.correlation = self._field(
            layout,
            tr("Minimum stereo correlation", "Correlação estéreo mínima"),
            -1,
            1,
            config.minimum_correlation,
            3,
        )
        self.silence = self._field(
            layout,
            tr("Silence threshold (dBFS)", "Limiar de silêncio (dBFS)"),
            -300,
            0,
            config.silence_dbfs,
        )
        self.silence_time = self._field(
            layout,
            tr("Minimum silence (s)", "Silêncio mínimo (s)"),
            0,
            86_400,
            config.minimum_silence_seconds,
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _field(
        layout: QFormLayout,
        label: str,
        low: float,
        high: float,
        value: float,
        decimals: int = 2,
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setDecimals(decimals)
        field.setRange(low, high)
        field.setValue(value)
        layout.addRow(label, field)
        return field

    def configuration(self) -> QCConfig:
        return replace(
            self._base,
            expected_sample_rate=self.rate.value() or None,
            clipping_level=self.clipping.value(),
            true_peak_ceiling=self.ceiling.value(),
            dc_threshold=self.dc.value(),
            minimum_correlation=self.correlation.value(),
            silence_dbfs=self.silence.value(),
            minimum_silence_seconds=self.silence_time.value(),
        )


class MainWindow(QMainWindow):
    """Analyze, listen to and process local audio without modifying source recordings."""

    batch_progress = pyqtSignal(str, arguments=[])

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OpenAudioKit")
        self.setStyleSheet(_STYLE)
        self.resize(1220, 850)

        self.setMinimumSize(800, 565)
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("logo.svg"))))
        self.setAcceptDrops(True)
        self.preferences = QSettings("OpenAudioKit", "OpenAudioKit")
        self.language = "en"
        self._translations: list[tuple[QLabel | QPushButton, str, str]] = []
        self._cancel = Event()
        self._preview_files = TemporaryDirectory(prefix="openaudiokit-", ignore_cleanup_errors=True)
        self._preview: _Source | None = None
        self._preview_sequence = 0
        self._player_slot = "A"

        self.sources: dict[Slot, _Source] = {}
        self.active: Slot = "A"
        self.config = QCConfig()
        self._worker: _Worker | None = None
        self._source_labels: dict[Slot, QLabel] = {}
        self._source_details: dict[Slot, QLabel] = {}
        self._open_buttons: dict[Slot, QPushButton] = {}
        self._switch_buttons: dict[Slot, QPushButton] = {}

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 17, 20, 14)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())

        source_row = QHBoxLayout()
        for slot, title in zip(_SLOTS, ("A · Reference", "B · Candidate"), strict=True):
            source_row.addWidget(self._source_card(slot, title))
        layout.addLayout(source_row)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs, 1)
        self._build_pages()
        self.player = PlaybackBar()
        layout.addWidget(self.player)
        self.player.position_seconds.connect(self._on_position)
        self.player.playback_source_changed.connect(self._playback_changed)
        self.waveform.seek.connect(self.player.seek_seconds)
        self.waveform.region.connect(self.player.set_region)
        status_row = QHBoxLayout()
        self.status = _title("Open a file to begin.", "status")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Job status")
        status_row.addWidget(self.status, 1)
        self.cancel = self._button("Cancel job", "Cancelar tarefa", self._cancel_job)
        status_row.addWidget(self.cancel)
        layout.addLayout(status_row)
        for key, action in (
            ("Ctrl+O", lambda: self._pick_source("A")),
            ("Ctrl+Shift+O", lambda: self._pick_source("B")),
            ("Ctrl+E", self._pick_qc_report),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
        self._set_language(str(self.preferences.value("language", "en")))
        self._refresh()

    @property
    def busy(self) -> bool:
        """Whether a job is running or its result is pending on the main thread."""
        return self._worker is not None

    def _toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.addWidget(_title("OpenAudioKit", "title"))
        toolbar.addStretch()
        self.settings = self._button("QC settings", "Ajustes de QC", self._show_settings)
        toolbar.addWidget(self.settings)
        self.export_qc = self._button("Export QC", "Exportar QC", self._pick_qc_report)
        toolbar.addWidget(self.export_qc)
        self.export_ab = self._button("Export A/B", "Exportar A/B", self._pick_ab_report)
        toolbar.addWidget(self.export_ab)
        return toolbar

    def _source_card(self, slot: Slot, title: str) -> QFrame:
        card, layout = _card()
        row = QHBoxLayout()
        row.addWidget(self._label(title, "A · Referência" if slot == "A" else "B · Candidato"))
        row.addStretch()
        pick = self._button(f"Open {slot}…", f"Abrir {slot}…", lambda: self._pick_source(slot))
        pick.setProperty("primary", slot == "A")
        self._open_buttons[slot] = pick
        row.addWidget(pick)
        layout.addLayout(row)
        name = _title("No file selected", "subtle")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._source_labels[slot] = name
        layout.addWidget(name)
        details = _title("—", "subtle")
        self._source_details[slot] = details
        layout.addWidget(details)
        return card

    def _local(self, en: str, pt: str) -> str:
        return pt if self.language == "pt" else en

    def _label(self, en: str, pt: str, role: str = "heading") -> QLabel:
        label = _title(en, role)
        self._translations.append((label, en, pt))
        return label

    def _button(self, en: str, pt: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(en)
        button.clicked.connect(callback)
        self._translations.append((button, en, pt))
        return button

    def _set_language(self, language: str) -> None:
        self.language = "pt" if language == "pt" else "en"
        for index, (en, pt) in enumerate(self._pages):
            self.tabs.setTabText(index, self._local(en, pt))
        for widget, en, pt in self._translations:
            widget.setText(self._local(en, pt))
        self.scale.setItemText(1, self._local("Logarithmic", "Logarítmica"))
        for slot in _SLOTS:
            if slot not in self.sources:
                self._source_labels[slot].setText(
                    self._local("No file selected", "Nenhum arquivo selecionado")
                )
        self.chain.set_language(self.language)
        self.player.set_language(self.language)
        self.preferences.setValue("language", self.language)
        self._refresh()

    def _build_pages(self) -> None:
        self._pages = [
            ("Overview", "Visão geral"),
            ("Audio Analysis", "Análise de áudio"),
            ("Spectrum", "Espectro"),
            ("Stereo Imaging", "Imagem estéreo"),
            ("A/B Comparison", "Comparação A/B"),
            ("DSP Processing", "Processamento DSP"),
            ("Batch Processing", "Processamento em lote"),
            ("Settings", "Configurações"),
        ]
        layouts = []
        for en, _ in self._pages:
            page = QWidget()
            box = QVBoxLayout(page)
            box.setContentsMargins(10, 9, 10, 4)
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, en)
            layouts.append(box)
        overview, analysis, spectrum, stereo, ab, dsp, batch, settings = layouts
        card, card_layout = _card()
        card_layout.addWidget(self._label("File overview", "Resumo do arquivo"))
        self.overview_file = _title("—", "subtle")
        card_layout.addWidget(self.overview_file)
        overview.addWidget(card)
        self._overview_values: dict[str, QLabel] = {}
        grid = QGridLayout()
        for idx, (key, en, pt) in enumerate(
            (
                ("lufs", "Integrated LUFS", "LUFS integrado"),
                ("rms", "RMS · dBFS", "RMS · dBFS"),
                ("peak", "Sample Peak · dBFS", "Pico de amostra · dBFS"),
                ("true", "Estimated True Peak · dBTP", "True Peak estimado · dBTP"),
                ("lra", "Loudness Range · LU", "Faixa de loudness · LU"),
                ("crest", "Crest Factor · dB", "Fator de crista · dB"),
                ("correlation", "Stereo correlation", "Correlação estéreo"),
            )
        ):
            tile, title_area = _card()
            title_area.addWidget(self._label(en, pt, "subtle"))
            value = _title("—", "title")
            value.setMinimumHeight(32)
            tile.setMinimumHeight(71)
            title_area.addWidget(value)
            self._overview_values[key] = value
            grid.addWidget(tile, idx // 4, idx % 4)
        overview.addLayout(grid)
        qc, qc_layout = _card()
        qc_layout.addWidget(self._label("Quality control", "Controle de qualidade"))
        self._issues = _title("—", "subtle")
        self._issues.setWordWrap(True)
        self._issues.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        qc_layout.addWidget(self._issues)
        overview.addWidget(qc)
        self.recent = QComboBox()
        self.recent.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        row = QHBoxLayout()
        row.addWidget(self._label("Recent files", "Arquivos recentes", "subtle"))
        row.addWidget(self.recent, 1)
        row.addWidget(self._button("Open in A", "Abrir em A", self._open_recent))
        overview.addLayout(row)
        overview.addWidget(self._label("Waveform · whole file", "Forma de onda · arquivo completo"))
        self.overview_waveform = _Plot(spectrum=False)
        overview.addWidget(self.overview_waveform, 1)
        self._refresh_recent()

        row = QHBoxLayout()
        row.addWidget(
            self._label(
                "Waveform · click to seek, Shift+drag to select",
                "Forma de onda · clique para buscar, Shift+arraste para selecionar",
            )
        )
        row.addStretch()
        self._switch_group = QButtonGroup(self)
        for slot in _SLOTS:
            button = QPushButton(slot)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, source=slot: self.switch_source(source))
            self._switch_group.addButton(button)
            self._switch_buttons[slot] = button
            row.addWidget(button)
        analysis.addLayout(row)
        self.waveform = Timeline()
        analysis.addWidget(self.waveform, 1)
        analysis.addWidget(
            self._label(
                "File loudness history · LUFS · momentary / short-term",
                "Loudness do arquivo · LUFS · momentâneo / curto prazo",
            )
        )
        self.loudness_plot = Trace("loudness")
        analysis.addWidget(self.loudness_plot, 1)
        self.levels = _title("—", "subtle")
        self.levels.setWordWrap(True)
        analysis.addWidget(self.levels)
        self.position_levels = _title("—", "subtle")
        self.position_levels.setWordWrap(True)
        analysis.addWidget(self.position_levels)

        spectrum.addWidget(
            self._label(
                "Average power spectral density · dBFS/Hz",
                "Densidade espectral média de potência · dBFS/Hz",
            )
        )
        self.frequency_plot = Trace("spectrum")
        spectrum.addWidget(self.frequency_plot, 1)
        spectral_row = QHBoxLayout()
        spectral_row.addWidget(
            self._label("Spectrogram · −100 to 0 dBFS/bin", "Espectrograma · −100 a 0 dBFS/bin")
        )
        self.scale = QComboBox()
        self.scale.addItems(["Linear", "Log"])
        self.scale.currentIndexChanged.connect(self._change_scale)
        spectral_row.addWidget(self.scale)
        spectrum.addLayout(spectral_row)
        self.spectrum = _Plot(spectrum=True)
        spectrum.addWidget(self.spectrum, 1)
        self.bands = _title("—", "subtle")
        self.bands.setWordWrap(True)
        spectrum.addWidget(self.bands)

        stereo.addWidget(
            self._label(
                "Goniometer · Side (horizontal) / Mid (vertical)",
                "Goniômetro · Side (horizontal) / Mid (vertical)",
            )
        )
        self.stereo_plot = Trace("stereo")
        stereo.addWidget(self.stereo_plot, 1)
        self.stereo_label = _title("—", "subtle")
        stereo.addWidget(self.stereo_label)
        self.correlation_meter = QProgressBar()
        self.correlation_meter.setRange(0, 200)
        self.correlation_meter.setTextVisible(False)
        stereo.addWidget(self.correlation_meter)

        ab.addWidget(
            self._label(
                "Overlaid spectral density · A emerald / B amber · dBFS/Hz",
                "Densidade espectral sobreposta · A esmeralda / B âmbar · dBFS/Hz",
            )
        )
        self.overlay = Trace("spectrum")
        ab.addWidget(self.overlay, 1)
        self.comparison = QTableWidget(0, 4)
        self.comparison.setHorizontalHeaderLabels(["Metric", "A", "B", "Δ B−A"])
        row_header = self.comparison.verticalHeader()
        column_header = self.comparison.horizontalHeader()
        if row_header is not None:
            row_header.hide()
        if column_header is not None:
            column_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.comparison.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.comparison.setAlternatingRowColors(True)
        self.comparison.setShowGrid(False)
        ab.addWidget(self.comparison, 1)

        dsp.addWidget(
            self._label(
                "Offline chain · renders from source A",
                "Cadeia offline · renderiza a partir da fonte A",
            )
        )
        self.chain = ChainEditor()
        self.chain.preview_requested.connect(self.render_preview)
        self.chain.export.connect(self._pick_audio_export)
        self.chain.error.connect(self._job_failed)
        dsp.addWidget(self.chain, 1)

        batch.addWidget(
            self._label(
                "Run the current DSP chain over multiple files",
                "Aplicar a cadeia DSP atual a vários arquivos",
            )
        )
        self.batch_output = _title("—", "subtle")
        self.batch_output.setWordWrap(True)
        batch.addWidget(self.batch_output, 1)
        self.batch_progress.connect(self.batch_output.setText)
        self.batch_button = self._button(
            "Choose files and run", "Selecionar arquivos e executar", self._pick_batch
        )
        batch.addWidget(self.batch_button)
        settings.addWidget(self._label("Interface language", "Idioma da interface"))
        language = QComboBox()
        language.addItem("English", "en")
        language.addItem("Português", "pt")
        language.setCurrentIndex(int(self.preferences.value("language", "en") == "pt"))
        language.currentIndexChanged.connect(
            lambda: self._set_language(str(language.currentData()))
        )
        settings.addWidget(language)
        settings.addWidget(
            self._button(
                "Quality control thresholds",
                "Limites de controle de qualidade",
                self._show_settings,
            )
        )
        settings.addStretch()

    def _change_scale(self, index: int) -> None:
        self.spectrum.log_frequency = index == 1
        self.spectrum.update()

    def _recent_paths(self) -> list[str]:
        value = self.preferences.value("recent", [])
        return [str(path) for path in value] if isinstance(value, list) else []

    def _refresh_recent(self) -> None:
        self.recent.clear()
        for path in self._recent_paths():
            self.recent.addItem(Path(path).name, path)
        if self.recent.count():
            self.recent.setCurrentIndex(0)

    def _open_recent(self) -> None:
        path = self.recent.currentData()
        if path and not self.busy:
            self.open_path("A", Path(path))

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is not None and not self.busy:
            mime = event.mimeData()
            if mime is not None and any(url.isLocalFile() for url in mime.urls()):
                event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None or self.busy:
            return
        mime = event.mimeData()
        paths = (
            [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()] if mime else []
        )
        if paths:
            self.open_path(self.active, paths[0])
            event.acceptProposedAction()

    def _on_position(self, seconds: float) -> None:
        self.waveform.set_position(seconds)
        active = (
            self._preview if self._player_slot == "Processed" else self.sources.get(self.active)
        )
        if active is None:
            return
        rate = active.audio.sample_rate
        frame = min(active.audio.frames, max(0, round(seconds * rate)))
        local = active.audio.samples[max(0, frame - round(rate * 0.05)) : frame]

        def level(amplitude: float) -> str:
            return f"{20 * np.log10(amplitude):.1f}" if amplitude > 0 else "—"

        def current(times: FloatArray, values: FloatArray) -> str:
            index = int(np.searchsorted(times, seconds, side="right")) - 1
            return (
                _number(float(values[index])) if index >= 0 and np.isfinite(values[index]) else "—"
            )

        peak = level(float(np.max(np.abs(local), initial=0)))
        rms = level(float(np.sqrt(np.mean(np.square(local))))) if local.size else "—"
        history = active.loudness
        m = current(history.momentary_times_seconds, history.momentary_lufs)
        short = current(history.short_term_times_seconds, history.short_term_lufs)
        self.position_levels.setText(
            self._local(
                "Source at playhead · up to 50 ms", "Fonte na posição do player · até 50 ms"
            )
            + f"  ·  Peak {peak} dBFS  ·  RMS {rms} dBFS  ·  M {m} LUFS  ·  S {short} LUFS"
        )

    def _playback_changed(self, slot: str) -> None:
        self._player_slot = slot
        if slot in ("A", "B"):
            self.active = "A" if slot == "A" else "B"
        self._refresh()

    def _cancel_job(self) -> None:
        self._cancel.set()
        self._set_status(self._local("Cancellation requested…", "Cancelamento solicitado…"))

    def render_preview(self, operations: tuple[Operation, ...]) -> None:
        original = self.sources.get("A")
        if original is None:
            self._job_failed(self._local("Open source A first.", "Abra a fonte A primeiro."))
            return
        self._preview_sequence += 1
        temporary = Path(self._preview_files.name) / f"preview-{self._preview_sequence}.wav"
        config = self.config

        def render() -> _Preview:
            audio = original.audio
            for operation in operations:
                if self._cancel.is_set():
                    raise InterruptedError("Preview cancelled.")
                audio = process_audio(audio, (operation,), max_samples=_MAX_SAMPLES)
            measured = _read_source(temporary, audio, config)
            self._check_cancel()
            write_audio(temporary, audio, subtype="FLOAT")
            return _Preview(measured)

        self._start_job(self._local("Rendering preview…", "Renderizando prévia…"), render)

    def _pick_audio_export(self) -> None:
        preview = self._preview
        if preview is None:
            self._job_failed(
                self._local("Render a preview first.", "Renderize uma prévia primeiro.")
            )
            return
        filename, selected = QFileDialog.getSaveFileName(
            self,
            self._local("Export processed audio", "Exportar áudio processado"),
            "processed.wav",
            "WAV (*.wav);;FLAC (*.flac);;AIFF (*.aiff);;OGG (*.ogg)",
        )
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(
                {"FLAC (*.flac)": ".flac", "AIFF (*.aiff)": ".aiff", "OGG (*.ogg)": ".ogg"}.get(
                    selected, ".wav"
                )
            )
        self.export_audio(path)

    def export_audio(self, path: Path) -> None:
        preview = self._preview
        if preview is None:
            return
        if not self._safe_destination(path):
            return

        def save() -> Path:
            self._check_cancel()
            write_audio(path, preview.audio)
            return path

        self._start_job(self._local("Exporting audio…", "Exportando áudio…"), save)

    def _safe_destination(self, path: Path, additional: tuple[Path, ...] = ()) -> bool:
        def identity(file: Path) -> str:
            return normalize("NFC", str(file.resolve())).casefold()

        originals = (*additional, *(source.path for source in self.sources.values()))
        if self._preview is not None:
            originals = (*originals, self._preview.path)
        if identity(path) in {identity(file) for file in originals}:
            self._job_failed(
                self._local(
                    "Choose a destination other than the source.",
                    "Escolha um destino diferente do arquivo original.",
                )
            )
            return False
        return True

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise InterruptedError(self._local("Job cancelled.", "Tarefa cancelada."))

    def _pick_batch(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Audio", "", _OPEN_FILTER)
        if not filenames:
            return
        folder = QFileDialog.getExistingDirectory(
            self, self._local("Output folder", "Pasta de destino")
        )
        if not folder:
            return
        operations = () if self.chain.bypass.isChecked() else tuple(self.chain.entries)
        config = self.config
        output = Path(folder)
        destination = output / "batch-report.json"
        if not self._safe_destination(destination, tuple(Path(name) for name in filenames)):
            return

        def job() -> Path:
            def check_progress(event: Progress) -> None:
                outcome = event.result.error or "OK"
                self.batch_progress.emit(
                    f"{event.completed}/{event.total} · {event.result.source.name} · {outcome}"
                )
                if self._cancel.is_set():
                    raise InterruptedError(
                        self._local(
                            "Batch cancelled; completed outputs remain.",
                            "Lote cancelado; as saídas concluídas foram mantidas.",
                        )
                    )

            result = run_batch(
                filenames,
                operations=operations,
                output_dir=output,
                config=config,
                progress=check_progress,
                max_samples=_MAX_SAMPLES,
            )
            self._check_cancel()
            write_report(destination, result)
            return destination

        self.batch_output.setText(f"{len(filenames)} → {output}")
        self._start_job(self._local("Running batch…", "Processando lote…"), job)

    def _pick_source(self, slot: Slot) -> None:
        if self.busy:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, self._local(f"Open source {slot}", f"Abrir fonte {slot}"), "", _OPEN_FILTER
        )
        if filename:
            self.open_path(slot, Path(filename))

    def _show_settings(self) -> None:
        dialog = _Settings(self, self.config, self.language)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            config = dialog.configuration()
        except ValueError as exc:
            self._set_status(f"Error: {exc}", error=True)
            return
        sources = dict(self.sources)
        if not sources:
            self.config = config
            self._set_status("Quality control settings updated.")
            return

        def recheck() -> _WorkspaceState:
            measured = {
                slot: replace(source, report=inspect(source.audio, config=config))
                for slot, source in sources.items()
            }
            return _WorkspaceState(measured, config)

        self._start_job("Applying quality control settings…", recheck)

    def open_path(self, slot: Slot, path: Path) -> None:
        """Load a source in the background, preserving it if replacement fails."""
        config = self.config
        sources = dict(self.sources)

        def load() -> _WorkspaceState:
            audio = load_audio(path, max_samples=_MAX_SAMPLES)
            sources[slot] = _read_source(path, audio, config)
            return _WorkspaceState(sources, config)

        self._start_job(f"Analyzing {path.name}…", load)

    def switch_source(self, slot: Slot) -> None:
        """Set the source shown by the plots and meters."""
        if slot in self.sources:
            self.active = slot
            self.player.select_source(slot)
            self._refresh()

    def _pick_qc_report(self) -> None:
        self._pick_report(comparison=False)

    def _pick_ab_report(self) -> None:
        self._pick_report(comparison=True)

    def _pick_report(self, *, comparison: bool) -> None:
        if self.busy:
            return
        name = "comparison" if comparison else f"qc-{self.active}"
        filename, selected = QFileDialog.getSaveFileName(
            self, self._local("Export report", "Exportar relatório"), name, _SAVE_FILTER
        )
        if filename:
            path = Path(filename)
            if not path.suffix:
                extension = {"CSV (*.csv)": ".csv", "HTML (*.html)": ".html"}.get(selected, ".json")
                path = path.with_suffix(extension)
            self.export_report(path, comparison=comparison)

    def export_report(self, path: Path, *, comparison: bool = False) -> None:
        """Export the active QC report or the comparison between both sources."""
        a = self.sources.get("A")
        b = self.sources.get("B")
        active = (
            self._preview if self._player_slot == "Processed" else self.sources.get(self.active)
        )
        config = self.config

        if not self._safe_destination(path):
            return

        def export() -> Path:
            if comparison and a is not None and b is not None:
                data = compare(a.audio, b.audio, config=config)
                self._check_cancel()
                write_report(path, data)
            elif not comparison and active is not None:
                self._check_cancel()
                write_report(path, active.report)
            else:
                raise ValueError(
                    "Open both sources to export A/B."
                    if comparison
                    else "Open an audio file to export QC."
                )
            return path

        self._start_job(f"Writing {path.name}…", export)

    def _start_job(
        self, message: str, job: Callable[[], _WorkspaceState | _Preview | Path]
    ) -> None:
        if self.busy:
            return
        self._cancel.clear()
        worker = _Worker(self, job)
        self._worker = worker
        worker.succeeded.connect(self._job_completed)
        worker.failed.connect(self._job_failed)
        worker.finished.connect(self._job_finished)
        worker.finished.connect(worker.deleteLater)
        self._set_status(message)
        self._update_actions()
        worker.start()

    def _job_completed(self, result: _WorkspaceState | _Preview | Path) -> None:

        if self._cancel.is_set() and not isinstance(result, Path):
            if isinstance(result, _Preview):
                self._remove_temporary(result.source.path)
            self._set_status(self._local("Job cancelled.", "Tarefa cancelada."))
            return
        if isinstance(result, Path):
            self._set_status(self._local("Saved ", "Salvo ") + result.name)
        elif isinstance(result, _Preview):
            previous = self._preview
            self._preview = result.source
            self.player.set_source(
                "Processed",
                result.source.path,
                result.source.report.metrics.loudness.integrated_lufs,
            )
            self.player.select_source("Processed")
            if previous is not None:
                self._remove_temporary(previous.path)
            self._set_status(self._local("Preview ready.", "Prévia pronta."))
        else:
            previous_sources = self.sources
            self.sources = result.sources
            original = previous_sources.get("A")
            replacement = self.sources.get("A")
            if (
                original is not None
                and replacement is not None
                and original.audio is not replacement.audio
            ):
                stale, self._preview = self._preview, None
                self.player.set_source("Processed", None, None)
                if self._player_slot == "Processed":
                    self._player_slot = "A"
                if stale is not None:
                    self._remove_temporary(stale.path)
            for slot, source in self.sources.items():
                previous = previous_sources.get(slot)
                if (
                    previous is None
                    or previous.path != source.path
                    or previous.audio is not source.audio
                ):
                    self.player.set_source(
                        slot, source.path, source.report.metrics.loudness.integrated_lufs
                    )
                    location = str(source.path.resolve())
                    recent = [location, *(p for p in self._recent_paths() if p != location)]
                    self.preferences.setValue("recent", recent[:10])
                    self._refresh_recent()
            self.config = result.config
            if self.active not in self.sources and self.sources:
                self.active = next(iter(self.sources))
            self._refresh()
            self._set_status(self._local("Analysis is up to date.", "Análise atualizada."))

    def _remove_temporary(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Qt may still be releasing the previous medium. The window owns the
            # directory and releases the player before its final cleanup.
            return

    def _job_failed(self, message: str) -> None:
        self._set_status(self._local("Error: ", "Erro: ") + message, error=True)

    def _job_finished(self) -> None:
        self._worker = None
        self._update_actions()

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status.setText(message)
        self.status.setProperty("role", "error" if error else "status")
        style = self.status.style()
        if style is not None:
            style.unpolish(self.status)
            style.polish(self.status)

    def _update_actions(self) -> None:
        self.cancel.setEnabled(self.busy)
        self.batch_button.setEnabled(not self.busy)
        self.chain.setEnabled(not self.busy)
        for button in self._open_buttons.values():
            button.setEnabled(not self.busy)
        self.settings.setEnabled(not self.busy)
        self.export_qc.setEnabled(not self.busy and self.active in self.sources)
        self.export_ab.setEnabled(not self.busy and len(self.sources) == 2)
        for slot, button in self._switch_buttons.items():
            button.setEnabled(slot in self.sources)
            button.setChecked(self.active == slot and slot in self.sources)

    def _refresh(self) -> None:
        for slot, source in self.sources.items():
            self._source_labels[slot].setText(source.path.name)
            self._source_labels[slot].setToolTip(str(source.path))
            audio = source.audio
            channels = {1: "mono", 2: "stereo"}.get(audio.channels, f"{audio.channels} channels")
            self._source_details[slot].setText(
                f"{audio.sample_rate:,} Hz  /  {channels}  /  {audio.duration:.2f} s"
            )

        active = (
            self._preview if self._player_slot == "Processed" else self.sources.get(self.active)
        )
        if active is not None:
            self.waveform.set_envelope(active.lows, active.highs, active.audio.duration)
        self.spectrum.set_source(active)
        self.overview_waveform.set_source(active)
        if active is not None:
            metrics = active.report.metrics
            loudness = metrics.loudness
            metadata = (
                getattr(active.audio, "source_format", None)
                or active.path.suffix.lstrip(".").upper()
            )
            self.overview_file.setText(
                f"{active.path.name}   ·   {metadata}   ·   {active.audio.duration:.2f} s   ·   "
                f"{active.audio.sample_rate:,} Hz   ·   {active.audio.channels} ch"
            )
            for key, value in zip(
                self._overview_values,
                (
                    loudness.integrated_lufs,
                    metrics.rms_dbfs,
                    metrics.peak_dbfs,
                    metrics.true_peak_dbtp,
                    loudness.lra_lu,
                    metrics.crest_factor_db,
                    metrics.stereo_correlation,
                ),
                strict=True,
            ):
                self._overview_values[key].setText(_number(value, 3 if key == "correlation" else 1))
            history = active.loudness
            self.loudness_plot.set_curves(
                (history.momentary_times_seconds, history.momentary_lufs),
                (history.short_term_times_seconds, history.short_term_lufs),
            )
            self.levels.setText(
                self._local("File maxima", "Máximos do arquivo")
                + f"  ·  M {_number(loudness.momentary_max_lufs)} LUFS  ·  S {_number(loudness.short_term_max_lufs)} LUFS"
                f"  ·  Peak {_number(metrics.peak_dbfs)} dBFS  ·  RMS {_number(metrics.rms_dbfs)} dBFS"
            )
            self.frequency_plot.set_curves(active.frequency)
            self.bands.setText(
                "   ·   ".join(
                    f"{name.replace('_', ' ')} {value:.1%}"
                    for name, value in active.report.spectrum.band_energy.items()
                )
            )
            correlation = metrics.stereo_correlation
            self.stereo_plot.set_curves(*(() if active.stereo is None else (active.stereo,)))
            self.stereo_label.setText(
                self._local("File stereo correlation", "Correlação estéreo do arquivo")
                + f"  ·  {_number(correlation, 3)}"
            )
            self.correlation_meter.setValue(
                round((correlation + 1) * 100) if correlation is not None else 0
            )
            messages = [f"{issue.code}: {issue.message}" for issue in active.report.issues]
            self._issues.setText(
                "\n".join(messages[:3])
                if messages
                else self._local(
                    "No issues at the selected thresholds.",
                    "Sem ocorrências nos limites selecionados.",
                )
            )
            self._issues.setToolTip("\n".join(messages))
        empty = np.empty(0)
        self.overlay.set_curves(
            *(
                self.sources[slot].frequency if slot in self.sources else (empty, empty)
                for slot in _SLOTS
            )
        )
        self._update_comparison()
        self._update_actions()

    def _update_comparison(self) -> None:
        summaries = {
            slot: self._measurements(source.report) for slot, source in self.sources.items()
        }
        names = list(self._measurements(None))
        for summary in summaries.values():
            names.extend(name for name in summary if name not in names)
        self.comparison.setHorizontalHeaderLabels(
            [self._local("Metric", "Métrica"), "A", "B", "Δ B−A"]
        )
        self.comparison.setRowCount(len(names))
        a_report = self.sources["A"].report if "A" in self.sources else None
        b_report = self.sources["B"].report if "B" in self.sources else None
        for row, name in enumerate(names):
            self.comparison.setItem(row, 0, QTableWidgetItem(self._metric_label(name)))
            for column, slot in enumerate(_SLOTS, start=1):
                value = summaries.get(slot, {}).get(name, "—")
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.comparison.setItem(row, column, item)
            values = [summaries.get(slot, {}).get(name, "—") for slot in _SLOTS]
            compatible = a_report is not None and b_report is not None
            if a_report is not None and b_report is not None:
                if name.endswith((" (Hz)", " energy")):
                    compatible = a_report.sample_rate == b_report.sample_rate
                elif name in ("Integrated (LUFS)", "Loudness range (LU)"):
                    compatible = a_report.channels == b_report.channels
            if compatible and all(value != "—" for value in values):
                a, b = (float(value.rstrip("%")) for value in values)
                unit = " pp" if values[0].endswith("%") else ""
                self.comparison.setItem(row, 3, QTableWidgetItem(f"{b - a:+.2f}{unit}"))
            else:
                self.comparison.setItem(row, 3, QTableWidgetItem("—"))

    def _metric_label(self, name: str) -> str:
        translated = {
            "Integrated (LUFS)": "Integrado (LUFS)",
            "True peak (dBTP)": "True peak estimado (dBTP)",
            "Peak (dBFS)": "Pico de amostra (dBFS)",
            "RMS (dBFS)": "RMS (dBFS)",
            "Crest factor (dB)": "Fator de crista (dB)",
            "Dynamic range (dB)": "Faixa dinâmica (dB)",
            "Loudness range (LU)": "Faixa de loudness (LU)",
            "Stereo correlation": "Correlação estéreo",
            "Side energy (%)": "Energia Side (%)",
            "Centroid (Hz)": "Centroide (Hz)",
            "Bandwidth (Hz)": "Largura de banda (Hz)",
            "Rolloff (Hz)": "Rolloff (Hz)",
        }
        if name.endswith(" energy"):
            band = name.removesuffix(" energy")
            return self._local(
                f"{band.replace('_', ' ')} energy", f"Energia · {band.replace('_', ' ')}"
            )
        return self._local(name, translated.get(name, name))

    @staticmethod
    def _measurements(report: QCReport | None) -> dict[str, str]:
        metrics = report.metrics if report else None
        loudness = metrics.loudness if metrics else None
        values = {
            "Integrated (LUFS)": _number(loudness.integrated_lufs if loudness else None),
            "True peak (dBTP)": _number(metrics.true_peak_dbtp if metrics else None),
            "Peak (dBFS)": _number(metrics.peak_dbfs if metrics else None),
            "RMS (dBFS)": _number(metrics.rms_dbfs if metrics else None),
            "Crest factor (dB)": _number(metrics.crest_factor_db if metrics else None),
            "Dynamic range (dB)": _number(metrics.dynamic_range_db if metrics else None),
            "Loudness range (LU)": _number(loudness.lra_lu if loudness else None),
            "Stereo correlation": _number(metrics.stereo_correlation if metrics else None, 3),
            "Side energy (%)": _number(
                100 * report.side_energy_fraction
                if report is not None and report.side_energy_fraction is not None
                else None
            ),
            "Centroid (Hz)": _number(report.spectrum.centroid_hz if report else None),
            "Bandwidth (Hz)": _number(report.spectrum.bandwidth_hz if report else None),
            "Rolloff (Hz)": _number(report.spectrum.rolloff_hz if report else None),
        }
        if report is not None:
            values.update(
                {
                    f"{band} energy": f"{fraction:.1%}"
                    for band, fraction in report.spectrum.band_energy.items()
                }
            )
        return values

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if event is None:
            return
        if self.busy:
            self._cancel_job()
            event.ignore()
        else:
            self.player.clear()
            self._preview_files.cleanup()
            event.accept()


def main() -> int:
    """Open the desktop application."""
    app = QApplication([])
    app.setStyle("Fusion")
    app.setApplicationName("OpenAudioKit")
    window = MainWindow()
    window.show()
    return app.exec()
