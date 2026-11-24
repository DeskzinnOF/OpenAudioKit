"""Shared quality-control configuration."""

from dataclasses import dataclass
from numbers import Real

from .audio import validate_rate
from .errors import ValidationError


@dataclass(frozen=True)
class QCConfig:
    """QC limits in dBFS, dBTP, seconds and full-scale sample units."""

    expected_sample_rate: int | None = None
    clipping_level: float = 1.0
    dc_threshold: float = 0.01
    minimum_correlation: float = -0.2
    silence_dbfs: float = -60.0
    minimum_silence_seconds: float = 1.0
    true_peak_ceiling: float = -1.0

    def __post_init__(self) -> None:
        if self.expected_sample_rate is not None:
            validate_rate(self.expected_sample_rate)
        bounds = (
            ("clipping_level", self.clipping_level, 1e-9, 1.0),
            ("dc_threshold", self.dc_threshold, 0.0, 1.0),
            ("minimum_correlation", self.minimum_correlation, -1.0, 1.0),
            ("silence_dbfs", self.silence_dbfs, -300.0, 0.0),
            ("minimum_silence_seconds", self.minimum_silence_seconds, 0.0, 86_400.0),
            ("true_peak_ceiling", self.true_peak_ceiling, -300.0, 0.0),
        )
        for name, value, low, high in bounds:
            if isinstance(value, bool) or not isinstance(value, Real) or not low <= value <= high:
                raise ValidationError(f"{name} must be in [{low:g}, {high:g}].")
