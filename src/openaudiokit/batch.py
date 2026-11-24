"""Validated JSON DSP chains and failure-isolated batch jobs."""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unicodedata import normalize

from . import dsp
from .audio import Audio, AudioPath, load_audio, validate_rate, write_audio
from .config import QCConfig
from .errors import AudioError, AudioIOError, AudioLimitError, ValidationError
from .qc import QCReport, inspect

_LOG = logging.getLogger(__name__)
_OPERATIONS: dict[str, Callable[..., Audio]] = {
    "gain": dsp.gain,
    "invert_polarity": dsp.invert_polarity,
    "normalize_peak": dsp.normalize_peak,
    "normalize_loudness": dsp.normalize_loudness,
    "filter": dsp.filter_audio,
    "parametric_eq": dsp.parametric_eq,
    "compress": dsp.compress,
    "limit": dsp.limit,
    "noise_gate": dsp.noise_gate,
    "fade": dsp.fade,
    "to_mono": dsp.to_mono,
    "to_stereo": dsp.to_stereo,
    "encode_mid_side": dsp.encode_mid_side,
    "decode_mid_side": dsp.decode_mid_side,
    "mid_side": dsp.process_mid_side,
    "resample": dsp.resample,
}


@dataclass(frozen=True)
class Operation:
    """A registered DSP operation and its keyword parameters."""

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in _OPERATIONS:
            raise ValidationError(f"Unknown operation: {self.name!r}.")
        try:
            params = dict(self.params)
            signature(_OPERATIONS[self.name]).bind(None, **params)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Invalid parameters for {self.name!r}: {exc}.") from exc
        object.__setattr__(self, "params", MappingProxyType(params))


@dataclass(frozen=True)
class BatchResult:
    """Result of one file. Failed items keep an error message and no output."""

    source: Path
    output: Path | None = None
    report: QCReport | None = None
    error: str | None = None


@dataclass(frozen=True)
class Progress:
    """Progress delivered after each item, including failed ones."""

    completed: int
    total: int
    result: BatchResult


def load_preset(path: AudioPath) -> tuple[Operation, ...]:
    """Load an ordered DSP chain from a UTF-8 JSON preset."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, RecursionError) as exc:
        raise ValidationError("Preset must contain valid UTF-8 JSON.") from exc
    except OSError as exc:
        raise AudioIOError(f"Could not read preset: {path}") from exc
    if not isinstance(document, dict) or set(document) != {"operations"}:
        raise ValidationError("Preset requires an operations array.")
    entries = document["operations"]
    if not isinstance(entries, list):
        raise ValidationError("Preset operations must be an array.")
    steps = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or "name" not in entry
            or entry.keys() - {"name", "params"}
            or not isinstance(entry.get("params", {}), dict)
        ):
            raise ValidationError("Each operation requires a name and optional params object.")
        steps.append(Operation(entry["name"], entry.get("params", {})))
    return tuple(steps)


def process_audio(
    audio: Audio, operations: Sequence[Operation], *, max_samples: int | None = None
) -> Audio:
    """Apply a DSP chain and optionally bound input and intermediate sample counts."""
    if max_samples is not None:
        if isinstance(max_samples, bool) or not isinstance(max_samples, int) or max_samples < 1:
            raise ValidationError("max_samples must be a positive integer.")
        if audio.frames * audio.channels > max_samples:
            raise AudioLimitError("Input exceeds the processing sample limit.")
    for operation in operations:
        if max_samples is not None:
            frames, channels = audio.frames, audio.channels
            if operation.name == "resample":
                rate = validate_rate(operation.params["sample_rate"])
                frames = (frames * rate + audio.sample_rate - 1) // audio.sample_rate
            elif operation.name == "to_stereo" and channels == 1:
                channels = 2
            if frames * channels > max_samples:
                raise AudioLimitError(f"{operation.name} would exceed the processing sample limit.")
        audio = _OPERATIONS[operation.name](audio, **operation.params)
    return audio


def _identity(path: Path) -> str:
    return normalize("NFC", str(path.resolve())).casefold()


def run_batch(
    sources: Sequence[AudioPath],
    *,
    operations: Sequence[Operation] = (),
    output_dir: AudioPath | None = None,
    config: QCConfig = QCConfig(),
    progress: Callable[[Progress], None] | None = None,
    max_samples: int | None = None,
) -> list[BatchResult]:
    """Analyze sequentially and optionally write processed float WAVs with unique names."""
    paths = [Path(source) for source in sources]
    if not paths:
        raise ValidationError("Batch requires at least one source.")
    if operations and output_dir is None:
        raise ValidationError("Processing requires an output directory.")
    directory = Path(output_dir) if output_dir is not None else None
    planned = [directory / f"{path.name}.wav" for path in paths] if directory is not None else []
    destination_keys = {_identity(path) for path in planned}
    if len(destination_keys) != len(planned) or destination_keys.intersection(
        map(_identity, paths)
    ):
        raise ValidationError("Batch outputs collide with each other or an input.")
    if directory is not None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AudioIOError(f"Could not create output directory: {directory}") from exc
    results = []
    for index, path in enumerate(paths, 1):
        output = planned[index - 1] if planned else None
        try:
            audio = process_audio(
                load_audio(path, max_samples=max_samples), operations, max_samples=max_samples
            )
            report = inspect(audio, config)
            if output is not None:
                write_audio(output, audio)
            result = BatchResult(path, output, report)
            _LOG.info("Completed %s", path)
        except AudioError as exc:
            result = BatchResult(path, error=str(exc))
            _LOG.warning("Failed %s: %s", path, exc)
        results.append(result)
        if progress is not None:
            progress(Progress(index, len(paths), result))
    return results
