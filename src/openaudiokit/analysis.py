"""Time-domain audio measurements and programme loudness."""

from dataclasses import dataclass
from math import isfinite, log10, sqrt

import numpy as np

from .audio import Audio, FloatArray
from .errors import ValidationError
from .loudness import LoudnessResult, measure_loudness, true_peak


@dataclass(frozen=True)
class AnalysisConfig:
    """Thresholds and window duration shared by silence and dynamics measurements."""

    silence_threshold_dbfs: float = -60.0
    window_seconds: float = 0.1
    clipping_threshold: float = 1.0

    def __post_init__(self) -> None:
        if (
            not isfinite(self.silence_threshold_dbfs)
            or not -1000 <= self.silence_threshold_dbfs <= 0
        ):
            raise ValidationError("silence_threshold_dbfs must be between -1000 and 0")
        if not isfinite(self.window_seconds) or not 0 < self.window_seconds <= 60:
            raise ValidationError("window_seconds must be greater than 0 and at most 60")
        if not isfinite(self.clipping_threshold) or self.clipping_threshold <= 0:
            raise ValidationError("clipping_threshold must be positive and finite")


@dataclass(frozen=True)
class AnalysisResult:
    """Audio measurements using dBFS, dBTP, seconds and normalized amplitude."""

    rms_dbfs: float | None
    peak_dbfs: float | None
    true_peak_dbtp: float | None
    crest_factor_db: float | None
    dynamic_range_db: float | None
    clipping_samples: int
    dc_offset: tuple[float, ...]
    silence_seconds: float
    stereo_correlation: float | None
    loudness: LoudnessResult
    above_nominal_samples: int = 0


def _dbfs(amplitude: float) -> float | None:
    return 20 * log10(amplitude) if amplitude > 0 else None


def _correlation(samples: FloatArray) -> float | None:
    if samples.shape[1] != 2:
        return None
    centered = samples - samples[0]
    centered -= centered.mean(axis=0)
    peak = np.max(np.abs(centered), axis=0)
    if np.any(peak == 0):
        return None
    left, right = (centered / peak).T
    numerator = float(np.dot(left, right))
    denominator = sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
    return float(np.clip(numerator / denominator, -1, 1))


def _level_statistics(
    audio: Audio,
    config: AnalysisConfig,
    peak: float,
) -> tuple[float | None, float | None, float]:
    if not peak:
        return None, None, audio.duration
    block_length = max(1, round(config.window_seconds * audio.sample_rate))
    starts = np.arange(0, audio.frames, block_length)
    lengths = np.diff(starts, append=audio.frames)
    absolute_peak_db = 20 * log10(peak)
    total_power = 0.0
    strongest_window = np.zeros(starts.size)
    for samples in audio.samples.T:
        power = np.square(samples / peak)
        total_power += float(np.sum(power))
        window_power = np.add.reduceat(power, starts) / lengths
        np.maximum(strongest_window, window_power, out=strongest_window)
    rms_db = absolute_peak_db + 10 * log10(total_power / audio.samples.size)
    window_db = np.full(strongest_window.shape, -np.inf)
    np.log10(strongest_window, out=window_db, where=strongest_window > 0)
    window_db = absolute_peak_db + 10 * window_db
    silent = window_db < config.silence_threshold_dbfs
    active = window_db[~silent]
    dynamic_range = None
    if active.size:
        low, high = np.percentile(active, [10, 95])
        dynamic_range = float(high - low)
    return rms_db, dynamic_range, float(np.sum(lengths[silent])) / audio.sample_rate


def analyze(audio: Audio, config: AnalysisConfig = AnalysisConfig()) -> AnalysisResult:
    """Measure waveform levels, clipping, dynamics, stereo phase and loudness."""
    peak = 0.0
    threshold_samples = 0
    above_nominal_samples = 0
    for start in range(0, audio.frames, 65_536):
        absolute = np.abs(audio.samples[start : start + 65_536])
        peak = max(peak, float(np.max(absolute)))
        threshold_samples += int(np.count_nonzero(absolute >= config.clipping_threshold))
        above_nominal_samples += int(np.count_nonzero(absolute > 1))
    peak_db = _dbfs(peak)
    rms_db, dynamic_range, silence_seconds = _level_statistics(audio, config, peak)
    return AnalysisResult(
        rms_dbfs=rms_db,
        peak_dbfs=peak_db,
        true_peak_dbtp=_dbfs(true_peak(audio)) if peak else None,
        crest_factor_db=peak_db - rms_db if peak_db is not None and rms_db is not None else None,
        dynamic_range_db=dynamic_range,
        clipping_samples=threshold_samples,
        dc_offset=tuple(float(value) for value in np.mean(audio.samples, axis=0)),
        silence_seconds=silence_seconds,
        stereo_correlation=_correlation(audio.samples),
        loudness=measure_loudness(audio),
        above_nominal_samples=above_nominal_samples,
    )
