"""Offline mono/stereo K-weighted loudness and oversampled peak estimates."""

from dataclasses import dataclass
from math import atan, log10, pi, sqrt, tan

import numpy as np
from scipy.signal import firwin, resample_poly, sosfilt

from .audio import Audio, FloatArray

_OFFSET = -0.691
_ABSOLUTE_GATE_POWER = 10 ** ((-70 - _OFFSET) / 10)
_SHELF: tuple[float, ...] = (
    1.53512485958697,
    -2.69169618940638,
    1.19839281085285,
    1.0,
    -1.69065929318241,
    0.73248077421585,
)
_RLB: tuple[float, ...] = (1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621)
_OVERSAMPLE = 4
_INTERPOLATION_RADIUS = 32
_INTERPOLATION_FIR = firwin(
    2 * _INTERPOLATION_RADIUS * _OVERSAMPLE + 1,
    1 / _OVERSAMPLE,
    window=("kaiser", 8.6),
)


@dataclass(frozen=True)
class LoudnessResult:
    """Scalar programme summary, with None for an unavailable level."""

    integrated_lufs: float | None = None
    momentary_max_lufs: float | None = None
    short_term_max_lufs: float | None = None
    lra_lu: float | None = None


@dataclass(frozen=True)
class LoudnessHistory:
    """Window-end timestamps and ungated levels. Silence is negative infinity."""

    momentary_times_seconds: FloatArray
    momentary_lufs: FloatArray
    short_term_times_seconds: FloatArray
    short_term_lufs: FloatArray


def _bilinear_polynomial(coefficients: tuple[float, ...], scale: float) -> tuple[float, ...]:
    a0, a1, a2 = coefficients
    return (a0 + a1 + a2, 2 * (a0 - a2) * scale, (a0 - a1 + a2) * scale**2)


def _retune(sos: tuple[float, ...], sample_rate: int) -> tuple[float, ...]:
    """Transform the canonical digital biquad via its prewarped analog prototype."""
    numerator, denominator = sos[:3], sos[3:]
    analog_denominator = _bilinear_polynomial(denominator, 1)
    reference_warp = sqrt(analog_denominator[0] / analog_denominator[2])
    frequency = 48000 * atan(reference_warp) / pi
    new_warp = tan(pi * frequency / sample_rate)
    scale = reference_warp / new_warp
    transformed = (
        *_bilinear_polynomial(_bilinear_polynomial(numerator, scale), 1),
        *_bilinear_polynomial(_bilinear_polynomial(denominator, scale), 1),
    )
    normalizer = transformed[3]
    return tuple(value / normalizer for value in transformed)


def _weighted_power(audio: Audio) -> FloatArray:
    # The layout of more than two channels is not encoded by Audio.
    if audio.channels > 2 or audio.sample_rate < 8000:
        return np.empty(0, dtype=np.float64)
    shelf, rlb = _SHELF, _RLB
    if audio.sample_rate != 48000:
        shelf = _retune(shelf, audio.sample_rate)
        rlb = (1.0, -2.0, 1.0, *_retune(rlb, audio.sample_rate)[3:])
    coefficients = np.array([shelf, rlb])
    state = np.zeros((2, 2, audio.channels), dtype=np.float64)
    power = np.empty(audio.frames, dtype=np.float64)
    for start in range(0, audio.frames, 65_536):
        stop = min(start + 65_536, audio.frames)
        filtered, state = sosfilt(coefficients, audio.samples[start:stop], axis=0, zi=state)
        power[start:stop] = np.einsum("ij,ij->i", filtered, filtered)
    return power


def _window_energy(
    power: FloatArray,
    rate: int,
    window_cells: int,
    *,
    cells_per_second: int = 10,
    trailing_zeros: int = 0,
) -> tuple[FloatArray, FloatArray]:
    length = round(rate * window_cells / cells_per_second)
    available = power.size + trailing_zeros
    last_start = available - length
    if power.size == 0 or last_start < 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    if rate % cells_per_second == 0:
        hop = rate // cells_per_second
        cell_count = available // hop
        complete = power.size // hop
        cells = np.zeros(cell_count)
        cells[:complete] = power[: complete * hop].reshape(complete, hop).mean(axis=1)
        if complete < cell_count:
            cells[complete] = np.sum(power[complete * hop :]) / hop
        cell_windows = np.lib.stride_tricks.sliding_window_view(cells, window_cells)
        energies = cell_windows.mean(axis=1)
        starts = np.arange(energies.size) * hop
    else:
        # Absolute indexing keeps the time grid from drifting at odd sample rates.
        max_index = (cells_per_second * last_start + cells_per_second - 1) // rate
        indices = np.arange(max_index + 1, dtype=np.int64)
        starts = (indices * rate + cells_per_second // 2) // cells_per_second
        starts = starts[starts <= last_start]
        energies = np.fromiter(
            (np.sum(power[start : start + length]) / length for start in starts),
            dtype=np.float64,
            count=starts.size,
        )
    return (starts + length) / rate, energies


def _gated(
    power: FloatArray,
    relative_factor: float,
    *,
    include_threshold: bool = False,
) -> FloatArray:
    above = np.greater_equal if include_threshold else np.greater
    absolute = power[above(power, _ABSOLUTE_GATE_POWER)]
    if absolute.size == 0:
        return absolute
    return absolute[above(absolute, np.mean(absolute) * relative_factor)]


def _integrated(momentary: FloatArray) -> float | None:
    audible = _gated(momentary, 0.1)
    return _OFFSET + 10 * log10(float(np.mean(audible))) if audible.size else None


def _maximum(power: FloatArray) -> float | None:
    peak = float(np.max(power, initial=0))
    return _OFFSET + 10 * log10(peak) if peak > 0 else None


def _levels(power: FloatArray) -> FloatArray:
    levels = np.full(power.shape, -np.inf, dtype=np.float64)
    np.log10(power, where=power > 0, out=levels)
    return _OFFSET + 10 * levels


def integrated_loudness(audio: Audio) -> float | None:
    """Return gated integrated LUFS for mono/stereo at sample rates of 8 kHz or more."""
    if audio.frames < round(0.4 * audio.sample_rate):
        return None
    _, momentary = _window_energy(_weighted_power(audio), audio.sample_rate, 4)
    return _integrated(momentary)


def loudness_history(audio: Audio) -> LoudnessHistory:
    """Return ungated 400 ms windows every 10 ms and 3 s windows every 100 ms."""
    power = _weighted_power(audio)
    momentary_times, momentary = _window_energy(power, audio.sample_rate, 40, cells_per_second=100)
    short_times, short = _window_energy(power, audio.sample_rate, 30)
    return LoudnessHistory(momentary_times, _levels(momentary), short_times, _levels(short))


def measure_loudness(audio: Audio) -> LoudnessResult:
    """Calculate integrated LUFS, ungated maxima and Loudness Range."""
    power = _weighted_power(audio)
    rate = audio.sample_rate
    _, momentary = _window_energy(power, rate, 40, cells_per_second=100)
    # EBU Tech 3342 recommends 1.5 seconds of trailing silence for file LRA.
    padding = round(1.5 * rate) if audio.frames >= 3 * rate else 0
    short_times, lra_windows = _window_energy(power, rate, 30, trailing_zeros=padding)
    short = lra_windows[short_times <= audio.duration]
    lra = None
    if short.size:
        gated = _gated(lra_windows, 0.01, include_threshold=True)
        if gated.size >= 2:
            low, high = np.percentile(_levels(gated), [10, 95])
            lra = float(high - low)
    return LoudnessResult(_integrated(momentary[::10]), _maximum(momentary), _maximum(short), lra)


def true_peak(audio: Audio) -> float:
    """Estimate the sample and intersample peak as a linear amplitude across channels."""
    # Context spans the input support of the FIR at each chunk boundary.
    # Zero extension applies only where the signal starts or ends.
    chunk_size = max(2048, 131072 // audio.channels)
    peak = 0.0
    for start in range(0, audio.frames, chunk_size):
        stop = min(start + chunk_size, audio.frames)
        lo = max(0, start - _INTERPOLATION_RADIUS)
        hi = min(audio.frames, stop + _INTERPOLATION_RADIUS)
        upsampled = resample_poly(
            audio.samples[lo:hi],
            _OVERSAMPLE,
            1,
            axis=0,
            window=_INTERPOLATION_FIR,
        )
        segment = upsampled[(start - lo) * _OVERSAMPLE : (stop - lo) * _OVERSAMPLE]
        peak = max(
            peak, float(np.max(np.abs(segment))), float(np.max(np.abs(audio.samples[start:stop])))
        )
    return peak
