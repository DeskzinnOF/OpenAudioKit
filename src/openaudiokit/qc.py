"""Objective signal inspection and side-by-side recording comparisons."""

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .analysis import AnalysisConfig, AnalysisResult, analyze
from .audio import Audio
from .config import QCConfig
from .spectral import SpectralSummary, spectral_summary


@dataclass(frozen=True)
class Issue:
    """A machine-readable observation and its human-readable message."""

    code: str
    severity: Literal["info", "warning", "error"]
    message: str


@dataclass(frozen=True)
class QCReport:
    """Processing-independent measurements with the applied QC thresholds."""

    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    metrics: AnalysisResult
    spectrum: SpectralSummary
    side_energy_fraction: float | None
    issues: tuple[Issue, ...]
    config: QCConfig


@dataclass(frozen=True)
class ComparisonResult:
    """Independent measurements and candidate-minus-reference differences."""

    reference: QCReport
    candidate: QCReport
    deltas: dict[str, float | None]
    band_energy_deltas: dict[str, float] | None


def _side_fraction(audio: Audio) -> float | None:
    if audio.channels != 2:
        return None
    peak = float(np.max(np.abs(audio.samples)))
    if peak == 0:
        return None
    left, right = (audio.samples / peak).T
    total = float(np.dot(left, left) + np.dot(right, right))
    side = left - right
    return float(np.dot(side, side)) / (2 * total)


def inspect(audio: Audio, config: QCConfig = QCConfig()) -> QCReport:
    """Measure audio and emit threshold-based QC observations."""
    metrics = analyze(
        audio,
        AnalysisConfig(
            silence_threshold_dbfs=config.silence_dbfs, clipping_threshold=config.clipping_level
        ),
    )
    issues: list[Issue] = []
    if metrics.peak_dbfs is None:
        issues.append(Issue("digital_silence", "warning", "Every sample is zero."))
    if metrics.clipping_samples and audio.source_format == "MP3":
        issues.append(
            Issue(
                "lossy_peak_threshold",
                "warning",
                f"{metrics.clipping_samples} decoded MP3 samples meet the peak threshold. "
                f"{metrics.above_nominal_samples} exceed nominal full scale. "
                "Lossy coding can produce overshoot; clipping history is undetermined.",
            )
        )
    elif metrics.clipping_samples:
        issues.append(
            Issue(
                "clipping",
                "error",
                f"{metrics.clipping_samples} sample values meet the clipping threshold. "
                "Clipping history is undetermined.",
            )
        )
    if metrics.true_peak_dbtp is not None and metrics.true_peak_dbtp > config.true_peak_ceiling:
        issues.append(Issue("true_peak", "warning", "Estimated true peak exceeds the ceiling."))
    if max(abs(offset) for offset in metrics.dc_offset) > config.dc_threshold:
        issues.append(Issue("dc_offset", "warning", "A channel mean exceeds the DC offset limit."))
    if metrics.stereo_correlation is not None and (
        metrics.stereo_correlation < config.minimum_correlation
    ):
        issues.append(
            Issue(
                "negative_correlation",
                "warning",
                "Stereo correlation is below the configured limit.",
            )
        )
    if metrics.silence_seconds > 0 and metrics.silence_seconds >= config.minimum_silence_seconds:
        issues.append(
            Issue(
                "silence",
                "info",
                f"{metrics.silence_seconds:.3f} seconds of low-level windows in total.",
            )
        )
    if config.expected_sample_rate not in (None, audio.sample_rate):
        issues.append(
            Issue(
                "sample_rate_mismatch",
                "error",
                f"Expected {config.expected_sample_rate} Hz, found {audio.sample_rate} Hz.",
            )
        )
    return QCReport(
        audio.sample_rate,
        audio.channels,
        audio.frames,
        audio.duration,
        metrics,
        spectral_summary(audio),
        _side_fraction(audio),
        tuple(issues),
        config,
    )


def _values(report: QCReport) -> dict[str, float | None]:
    metrics, spectrum = report.metrics, report.spectrum
    return {
        "integrated_lufs": metrics.loudness.integrated_lufs,
        "momentary_max_lufs": metrics.loudness.momentary_max_lufs,
        "short_term_max_lufs": metrics.loudness.short_term_max_lufs,
        "lra_lu": metrics.loudness.lra_lu,
        "rms_dbfs": metrics.rms_dbfs,
        "peak_dbfs": metrics.peak_dbfs,
        "true_peak_dbtp": metrics.true_peak_dbtp,
        "crest_factor_db": metrics.crest_factor_db,
        "dynamic_range_db": metrics.dynamic_range_db,
        "centroid_hz": spectrum.centroid_hz,
        "rolloff_hz": spectrum.rolloff_hz,
        "bandwidth_hz": spectrum.bandwidth_hz,
        "stereo_correlation": metrics.stereo_correlation,
        "side_energy_fraction": report.side_energy_fraction,
    }


def compare(reference: Audio, candidate: Audio, config: QCConfig = QCConfig()) -> ComparisonResult:
    """Compare unaligned recordings. Spectral deltas require equal sample rates."""
    first, second = inspect(reference, config), inspect(candidate, config)
    reference_values, candidate_values = _values(first), _values(second)
    deltas: dict[str, float | None] = {}
    equal_rate = first.sample_rate == second.sample_rate
    for name, before in reference_values.items():
        after = candidate_values[name]
        compatible = equal_rate or not name.endswith("_hz")
        if (name.endswith("_lufs") or name == "lra_lu") and first.channels != second.channels:
            compatible = False
        deltas[name] = (
            after - before if compatible and before is not None and after is not None else None
        )
    bands = None
    if (
        equal_rate
        and first.spectrum.centroid_hz is not None
        and second.spectrum.centroid_hz is not None
    ):
        bands = {
            name: second.spectrum.band_energy[name] - value
            for name, value in first.spectrum.band_energy.items()
        }
    return ComparisonResult(first, second, deltas, bands)
