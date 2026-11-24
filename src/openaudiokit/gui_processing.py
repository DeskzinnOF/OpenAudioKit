"""Editors for validated core presets and desktop batch jobs."""

import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .batch import Operation, load_preset

DEFAULTS: dict[str, dict[str, float | int | str]] = {
    "gain": {"db": 0},
    "parametric_eq": {"frequency_hz": 1000, "db": 0, "q": 1},
    "filter": {"kind": "highpass", "frequency": 40, "order": 4, "q": 30},
    "compress": {
        "threshold_dbfs": -18,
        "ratio": 4,
        "attack_ms": 10,
        "release_ms": 100,
        "makeup_db": 0,
    },
    "limit": {"ceiling_dbfs": -1, "release_ms": 50},
    "noise_gate": {"threshold_dbfs": -45, "range_db": 60, "attack_ms": 5, "release_ms": 75},
    "normalize_peak": {"target_dbfs": -1},
    "normalize_loudness": {"target_lufs": -23, "ceiling_dbtp": -1},
    "fade": {"fade_in_seconds": 0.02, "fade_out_seconds": 0.08},
    "invert_polarity": {},
    "mid_side": {"mid_db": 0, "side_db": 0},
    "to_mono": {},
    "to_stereo": {},
    "resample": {"sample_rate": 48000},
}

_NAMES = {
    "gain": ("Gain", "Ganho"),
    "parametric_eq": ("Parametric EQ", "EQ paramétrico"),
    "filter": ("Filter", "Filtro"),
    "compress": ("Compressor", "Compressor"),
    "limit": ("Limiter", "Limitador"),
    "noise_gate": ("Noise gate", "Gate de ruído"),
    "normalize_peak": ("Peak normalization", "Normalização por pico"),
    "normalize_loudness": ("Loudness normalization", "Normalização por loudness"),
    "fade": ("Fades", "Fades"),
    "invert_polarity": ("Invert polarity", "Inverter polaridade"),
    "mid_side": ("Mid/Side gain", "Ganho Mid/Side"),
    "to_mono": ("Convert to mono", "Converter para mono"),
    "to_stereo": ("Convert to stereo", "Converter para estéreo"),
    "resample": ("Resample", "Reamostrar"),
}


class ChainEditor(QWidget):
    preview_requested = pyqtSignal(object, arguments=[])
    export = pyqtSignal(arguments=[])
    error = pyqtSignal(str, arguments=[])

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[Operation] = []
        self.fields: dict[str, QLineEdit] = {}
        self.localized: list[tuple[QPushButton | QLabel, str, str]] = []
        self.language = "en"
        layout = QHBoxLayout(self)
        options = QVBoxLayout()
        chain = QVBoxLayout()
        layout.addLayout(options, 1)
        layout.addLayout(chain, 2)
        self.effects = QComboBox()
        self.effects.currentIndexChanged.connect(self._reset)
        options.addWidget(self.effects)
        self.form = QFormLayout()
        options.addLayout(self.form)
        self._button(options, "Reset parameters", "Redefinir parâmetros", self._reset)
        self._button(options, "Add effect", "Adicionar efeito", self._add)
        self._button(
            options, "Update selected", "Atualizar seleção", lambda: self._add(update=True)
        )
        options.addStretch()
        self.sequence = QListWidget()
        self.sequence.itemClicked.connect(self._select)
        chain.addWidget(self.sequence, 1)
        order = QHBoxLayout()
        chain.addLayout(order)
        self._button(order, "Move up", "Mover acima", lambda: self._move(-1))
        self._button(order, "Move down", "Mover abaixo", lambda: self._move(1))
        self._button(order, "Remove", "Remover", self._remove)
        presets = QHBoxLayout()
        chain.addLayout(presets)
        self._button(presets, "Load preset", "Abrir preset", self._load)
        self._button(presets, "Save preset", "Salvar preset", self._save)
        self.bypass = QCheckBox()
        chain.addWidget(self.bypass)
        render = QHBoxLayout()
        chain.addLayout(render)
        self._button(render, "Render preview", "Renderizar prévia", self._render)
        self._button(render, "Export preview", "Exportar prévia", self.export.emit)
        self.set_language("en")

    def _button(self, layout, en, pt, callback) -> None:
        button = QPushButton(en)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.localized.append((button, en, pt))

    def set_language(self, language: str) -> None:
        self.language = language
        index = int(language == "pt")
        selected = self.effects.currentData()
        self.effects.blockSignals(True)
        self.effects.clear()
        for name in DEFAULTS:
            self.effects.addItem(_NAMES[name][index], name)
        if selected:
            self.effects.setCurrentIndex(self.effects.findData(selected))
        self.effects.blockSignals(False)
        if not self.fields:
            self._reset()
        for widget, en, pt in self.localized:
            widget.setText(pt if index else en)
        self.bypass.setText("Ignorar cadeia" if index else "Bypass chain")
        self._refresh()

    def _reset(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields.clear()
        for name, value in DEFAULTS.get(self.effects.currentData(), {}).items():
            field = QLineEdit(json.dumps(value))
            field.setAccessibleName(name)
            self.fields[name] = field
            self.form.addRow(name, field)

    def _add(self, *, update: bool = False) -> None:
        try:
            params = {name: json.loads(field.text()) for name, field in self.fields.items()}
            operation = Operation(self.effects.currentData(), params)
            row = self.sequence.currentRow()
            if update and row >= 0:
                self.entries[row] = operation
            else:
                self.entries.append(operation)
                row = len(self.entries) - 1
            self._refresh(row)
        except ValueError as exc:
            self.error.emit(str(exc))

    def _select(self) -> None:
        row = self.sequence.currentRow()
        if row < 0:
            return
        entry = self.entries[row]
        index = self.effects.findData(entry.name)
        if index < 0:
            return
        self.effects.setCurrentIndex(index)
        self._reset()
        for key, value in entry.params.items():
            if key in self.fields:
                self.fields[key].setText(json.dumps(value))

    def _move(self, offset: int) -> None:
        row = self.sequence.currentRow()
        target = row + offset
        if row >= 0 and 0 <= target < len(self.entries):
            self.entries.insert(target, self.entries.pop(row))
            self._refresh(target)

    def _remove(self) -> None:
        row = self.sequence.currentRow()
        if row >= 0:
            self.entries.pop(row)
            self._refresh(min(row, len(self.entries) - 1))
            self._select()

    def _refresh(self, row: int = -1) -> None:
        self.sequence.clear()
        index = int(self.language == "pt")
        for entry in self.entries:
            name = _NAMES.get(entry.name, (entry.name, entry.name))[index]
            values = ", ".join(f"{key}={value}" for key, value in entry.params.items())
            self.sequence.addItem(f"{name}  {values}".strip())
        self.sequence.setCurrentRow(row)

    def _render(self) -> None:
        self.preview_requested.emit(tuple() if self.bypass.isChecked() else tuple(self.entries))

    def _load(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Preset", "", "JSON (*.json)")
        if filename:
            try:
                self.entries = list(load_preset(filename))
                self._refresh()
            except (OSError, ValueError) as exc:
                self.error.emit(str(exc))

    def _save(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Preset", "", "JSON (*.json)")
        if filename:
            try:
                path = Path(filename)
                if not path.suffix:
                    path = path.with_suffix(".json")
                payload = [{"name": op.name, "params": dict(op.params)} for op in self.entries]
                path.write_text(json.dumps({"operations": payload}, indent=2), encoding="utf-8")
            except OSError as exc:
                self.error.emit(str(exc))
