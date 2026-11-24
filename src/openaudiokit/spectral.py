"""Fourier transforms, power features and frame-based onset detection."""

from collections.abc import Iterator
from dataclasses import dataclass
from math import ceil, sqrt

import numpy as np
from numpy.typing import NDArray
from scipy.fft import rfft, rfftfreq
from scipy.signal import find_peaks, windows

from .audio import Audio, FloatArray
from .errors import ValidationError

ComplexArray = NDArray[np.complex128]

BANDS = (
    ("sub_bass", 0.0, 60.0),
    ("bass", 60.0, 250.0),
    ("low_mid", 250.0, 500.0),
    ("mid", 500.0, 2000.0),
    ("upper_mid", 2000.0, 4000.0),
    ("presence", 4000.0, 6000.0),
    ("brilliance", 6000.0, 20000.0),
    ("ultrasonic", 20000.0, float("inf")),
)


@dataclass(frozen=True)
class Spectrum:
    """Unscaled one-sided DFT coefficients in [frequency, channel] order."""

    frequencies_hz: FloatArray
    values: ComplexArray


@dataclass(frozen=True)
class STFT:
    """Hann-windowed DFT coefficients in [frequency, time, channel] order."""

    frequencies_hz: FloatArray
    times_seconds: FloatArray
    values: ComplexArray


@dataclass(frozen=True)
class Spectrogram:
    """Channel-averaged mean-square power per bin in [frequency, time] order."""

    frequencies_hz: FloatArray
    times_seconds: FloatArray
    power: FloatArray


@dataclass(frozen=True)
class SpectralSummary:
    """Power-weighted spectral descriptors. Bands are fractions of total power."""

    centroid_hz: float | None
    bandwidth_hz: float | None
    rolloff_hz: float | None
    band_energy: dict[str, float]


@dataclass(frozen=True)
class TransientResult:
    """Frame-aligned positive spectral flux and detected peak times."""

    times_seconds: FloatArray
    flux: FloatArray
    onset_times_seconds: FloatArray


def fft(audio: Audio) -> Spectrum:
    """Return the unwindowed real-input Fourier transform of the whole buffer."""
    return Spectrum(rfftfreq(audio.frames, 1 / audio.sample_rate), rfft(audio.samples, axis=0))


def _validate(n_fft: int, hop: int) -> None:
    if isinstance(n_fft, bool) or not isinstance(n_fft, (int, np.integer)) or n_fft < 2:
        raise ValidationError("n_fft must be an integer of at least 2")
    if isinstance(hop, bool) or not isinstance(hop, (int, np.integer)) or hop < 1:
        raise ValidationError("hop must be a positive integer")


def _transform_chunks(
    audio: Audio,
    starts: NDArray[np.int64],
    window: FloatArray,
    *,
    scale: float = 1.0,
) -> Iterator[tuple[int, ComplexArray]]:
    width = window.size
    chunk_size = max(1, min(256, 2**18 // (width * audio.channels)))
    offsets = np.arange(width)
    for index in range(0, starts.size, chunk_size):
        frame_indices = starts[index : index + chunk_size, None] + offsets
        valid = (frame_indices >= 0) & (frame_indices < audio.frames)
        frames = audio.samples[np.clip(frame_indices, 0, audio.frames - 1)]
        if scale != 1:
            frames /= scale
        frames *= valid[:, :, None] * window[None, :, None]
        yield index, rfft(frames, axis=1)


def _bin_power(coefficients: ComplexArray, window: FloatArray) -> FloatArray:
    power = np.mean(np.square(np.abs(coefficients)), axis=2)
    power /= window.size * np.sum(window**2)
    # Negative-frequency partners carry the same power, except DC and Nyquist.
    power[:, 1 : -1 if window.size % 2 == 0 else None] *= 2
    return power


def stft(audio: Audio, n_fft: int = 1024, hop: int = 512) -> STFT:
    """Return complex frames centered at samples 0, hop, 2*hop, and so on."""
    _validate(n_fft, hop)
    centers = np.arange(0, audio.frames, hop, dtype=np.int64)
    window = windows.hann(n_fft, sym=False)
    values = np.empty((n_fft // 2 + 1, centers.size, audio.channels), dtype=np.complex128)
    for index, coefficients in _transform_chunks(audio, centers - n_fft // 2, window):
        values[:, index : index + len(coefficients)] = np.swapaxes(coefficients, 0, 1)
    return STFT(rfftfreq(n_fft, 1 / audio.sample_rate), centers / audio.sample_rate, values)


def spectrogram(audio: Audio, n_fft: int = 1024, hop: int = 512) -> Spectrogram:
    """Return a centered Hann spectrogram. A hop larger than n_fft skips samples."""
    _validate(n_fft, hop)
    centers = np.arange(0, audio.frames, hop, dtype=np.int64)
    window = windows.hann(n_fft, sym=False)
    power = np.empty((n_fft // 2 + 1, centers.size), dtype=np.float64)
    for index, coefficients in _transform_chunks(audio, centers - n_fft // 2, window):
        power[:, index : index + len(coefficients)] = _bin_power(coefficients, window).T
    return Spectrogram(rfftfreq(n_fft, 1 / audio.sample_rate), centers / audio.sample_rate, power)


def spectral_summary(audio: Audio, n_fft: int = 2048, rolloff: float = 0.85) -> SpectralSummary:
    """Summarize the averaged modified periodogram of overlapping waveform frames."""
    _validate(n_fft, 1)
    if not np.isfinite(rolloff) or not 0 < rolloff <= 1:
        raise ValidationError("rolloff must be in (0, 1]")
    peak = max(float(np.max(audio.samples)), -float(np.min(audio.samples)))
    if peak == 0:
        return SpectralSummary(None, None, None, dict.fromkeys((name for name, _, _ in BANDS), 0.0))
    last_start = audio.frames - n_fft
    if last_start < 0:
        starts = np.array([last_start // 2], dtype=np.int64)
    else:
        starts = np.arange(0, last_start + 1, n_fft // 2, dtype=np.int64)
        if starts[-1] != last_start:
            starts = np.append(starts, last_start)
    # Half-sample phase keeps the boundary sample observable in the first frame.
    window = np.square(np.sin(np.pi * (np.arange(n_fft) + 0.5) / n_fft))
    average = np.zeros(n_fft // 2 + 1)
    for _, coefficients in _transform_chunks(audio, starts, window, scale=peak):
        average += np.sum(_bin_power(coefficients, window), axis=0)
    cumulative = np.cumsum(average)
    total = float(cumulative[-1])
    frequency = rfftfreq(n_fft, 1 / audio.sample_rate)
    weights = average / total
    centroid = float(np.dot(frequency, weights))
    bandwidth = sqrt(float(np.dot(np.square(frequency - centroid), weights)))
    rolloff_hz = float(frequency[np.searchsorted(cumulative, rolloff * total)])
    band_energy = {
        name: float(np.sum(weights[(frequency >= start) & (frequency < stop)]))
        for name, start, stop in BANDS
    }
    return SpectralSummary(centroid, bandwidth, rolloff_hz, band_energy)


def transients(audio: Audio, n_fft: int = 1024, hop: int = 256) -> TransientResult:
    """Find transient candidates from peaks in positive spectral flux."""
    gram = spectrogram(audio, n_fft=n_fft, hop=hop)
    amplitudes = np.sqrt(gram.power)
    increases = np.maximum(0, np.diff(amplitudes, axis=1))
    flux = np.concatenate(([0.0], np.linalg.norm(increases, axis=0)))
    half_window = n_fft / (2 * audio.sample_rate)
    interior = (gram.times_seconds >= half_window) & (
        gram.times_seconds <= audio.duration - half_window
    )
    flux[~interior] = 0
    median = float(np.median(flux))
    mad = float(np.median(np.abs(flux - median)))
    minimum_prominence = 0.005 * sqrt(float(np.max(gram.power)))
    distance = max(1, ceil(0.05 * audio.sample_rate / hop))
    peaks, _ = find_peaks(
        flux,
        height=median + 3 * mad,
        prominence=minimum_prominence,
        distance=distance,
    )
    return TransientResult(gram.times_seconds, flux, gram.times_seconds[peaks])
