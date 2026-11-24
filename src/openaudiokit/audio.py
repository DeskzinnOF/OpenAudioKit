"""Audio buffers and SoundFile-backed decoding and encoding."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from numbers import Integral
from os import PathLike
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

import numpy as np
import soundfile as sf
from numpy.typing import ArrayLike, NDArray

from .errors import AudioIOError, AudioLimitError, ValidationError

FloatArray = NDArray[np.float64]
AudioPath = str | PathLike[str]
MAX_CHANNELS = 32
MAX_SAMPLE_RATE = 384_000
MAX_AMPLITUDE = 1e6


def validate_rate(sample_rate: int) -> int:
    """Validate and canonicalize an integer sample rate."""
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, Integral)
        or not 1 <= sample_rate <= MAX_SAMPLE_RATE
    ):
        raise ValidationError(f"Sample rate must be an integer in [1, {MAX_SAMPLE_RATE}].")
    return int(sample_rate)


@dataclass(frozen=True, eq=False, init=False)
class Audio:
    """An owned, read-only float64 buffer in (frames, channels) order."""

    samples: FloatArray
    sample_rate: int
    source_format: str | None
    source_subtype: str | None

    def __init__(self, samples: ArrayLike, sample_rate: int) -> None:
        rate = validate_rate(sample_rate)
        try:
            if np.iscomplexobj(samples):
                raise ValueError("complex samples")
            values = np.array(samples, dtype=np.float64, copy=True, order="C")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValidationError("Samples must contain real numbers.") from exc
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or not values.shape[0] or not 1 <= values.shape[1] <= MAX_CHANNELS:
            raise ValidationError(f"Expected nonempty audio with 1–{MAX_CHANNELS} channels.")
        self._set_samples(values, rate)

    def _set_samples(
        self,
        values: FloatArray,
        rate: int,
        source_format: str | None = None,
        source_subtype: str | None = None,
    ) -> None:
        for start in range(0, len(values), 65_536):
            block = values[start : start + 65_536]
            if not np.isfinite(block).all() or np.max(np.abs(block)) > MAX_AMPLITUDE:
                raise ValidationError(
                    f"Samples must be finite with magnitude at most {MAX_AMPLITUDE:g}."
                )
        values.flags.writeable = False
        object.__setattr__(self, "samples", values)
        object.__setattr__(self, "sample_rate", rate)
        object.__setattr__(self, "source_format", source_format)
        object.__setattr__(self, "source_subtype", source_subtype)

    @property
    def frames(self) -> int:
        return self.samples.shape[0]

    @property
    def channels(self) -> int:
        return self.samples.shape[1]

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def load_audio(source: AudioPath | BinaryIO, *, max_samples: int | None = None) -> Audio:
    """Decode a path or seekable stream, optionally limiting scalar samples before reading."""
    if max_samples is not None and (
        isinstance(max_samples, bool) or not isinstance(max_samples, Integral) or max_samples < 1
    ):
        raise ValidationError("max_samples must be a positive integer.")
    try:
        with sf.SoundFile(source) as stream:
            rate = validate_rate(stream.samplerate)
            if not 1 <= stream.channels <= MAX_CHANNELS or stream.frames < 1:
                raise ValidationError("File must contain audio with a supported channel count.")
            if max_samples is not None and stream.frames * stream.channels > max_samples:
                raise AudioLimitError("Decoded audio exceeds the sample limit.")
            samples = stream.read(frames=stream.frames, dtype="float64", always_2d=True)
            if len(samples) != stream.frames:
                raise AudioIOError("Audio decode returned fewer frames than expected.")
            audio = object.__new__(Audio)
            audio._set_samples(samples, rate, stream.format, stream.subtype)
            return audio
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise AudioIOError("Could not decode audio.") from exc


@contextmanager
def atomic_output(destination: AudioPath) -> Iterator[Path]:
    """Stage output next to its destination and replace it only on success."""
    path = Path(destination)
    with NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
    try:
        yield temporary
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_audio(
    destination: AudioPath | BinaryIO,
    audio: Audio,
    *,
    subtype: str | None = None,
    format: str | None = None,
) -> None:
    """Write audio atomically for paths. WAV defaults to lossless floating-point DOUBLE."""
    path = Path(destination) if isinstance(destination, (str, PathLike)) else None
    fmt = (format or (path.suffix.lstrip(".") if path is not None else "")).upper()
    fmt = {"WAVE": "WAV", "AIF": "AIFF", "OGA": "OGG"}.get(fmt, fmt)
    try:
        encoding = subtype or ("DOUBLE" if fmt == "WAV" else sf.default_subtype(fmt))
        if not encoding or not sf.check_format(fmt, encoding):
            raise AudioIOError(f"Unsupported output format or subtype: {fmt}/{encoding}.")
        if encoding not in {"FLOAT", "DOUBLE", "VORBIS", "OPUS"} and (
            np.max(audio.samples) >= 1 or np.min(audio.samples) < -1
        ):
            raise ValidationError(
                "Output encoding would saturate samples. Use float WAV or reduce gain."
            )
        if path is None:
            sf.write(destination, audio.samples, audio.sample_rate, subtype=encoding, format=fmt)
        else:
            with atomic_output(path) as temporary:
                sf.write(temporary, audio.samples, audio.sample_rate, subtype=encoding, format=fmt)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, (ValidationError, AudioIOError)):
            raise
        raise AudioIOError("Could not encode or write audio.") from exc
