"""Offline digital signal processing with linked channel dynamics."""

from collections.abc import Sequence
from math import ceil, exp, gcd
from numbers import Integral, Real
from typing import cast

import numpy as np
from scipy import signal

from .audio import Audio, FloatArray, validate_rate
from .errors import ValidationError
from .loudness import integrated_loudness, true_peak


def _number(value: float, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not low <= value <= high:
        raise ValidationError(f"{label} must be in [{low:g}, {high:g}].")
    return float(value)


def _amplitude(db: float) -> float:
    return 10.0 ** (db / 20)


def gain(audio: Audio, db: float) -> Audio:
    """Apply a fixed gain in decibels without sample clipping."""
    return Audio(audio.samples * _amplitude(_number(db, "Gain", -300, 300)), audio.sample_rate)


def invert_polarity(audio: Audio) -> Audio:
    """Invert all channels."""
    return Audio(-audio.samples, audio.sample_rate)


def normalize_peak(audio: Audio, target_dbfs: float = -1.0) -> Audio:
    """Scale to a sample peak. Digital silence is returned unchanged."""
    target = _amplitude(_number(target_dbfs, "Peak target", -300, 0))
    peak = float(np.max(np.abs(audio.samples)))
    if peak == 0:
        return audio
    return Audio((audio.samples / peak) * target, audio.sample_rate)


def normalize_loudness(
    audio: Audio, target_lufs: float = -23.0, ceiling_dbtp: float = -1.0
) -> Audio:
    """Apply measured LUFS gain, capped by an estimated true-peak ceiling."""
    target = _number(target_lufs, "Loudness target", -69, 0)
    ceiling = _amplitude(_number(ceiling_dbtp, "True peak ceiling", -300, 0))
    level = integrated_loudness(audio)
    if level is None:
        raise ValidationError("Integrated loudness is unavailable for this recording.")
    factor = min(_amplitude(target - level), ceiling / true_peak(audio))
    return Audio(audio.samples * factor, audio.sample_rate)


def _frequency(audio: Audio, value: float) -> float:
    nyquist = audio.sample_rate / 2
    cutoff = _number(value, "Frequency", 0, nyquist)
    spacing = np.finfo(np.float64).eps * nyquist
    if not spacing < cutoff < nyquist - spacing:
        raise ValidationError("Frequency must be distinguishable from DC and Nyquist.")
    return cutoff


def filter_audio(
    audio: Audio,
    kind: str,
    frequency: float | Sequence[float],
    *,
    order: int = 4,
    q: float = 30.0,
) -> Audio:
    """Apply causal Butterworth low/high/band-pass or a second-order notch."""
    if kind not in ("lowpass", "highpass", "bandpass", "notch"):
        raise ValidationError(f"Unknown filter: {kind!r}.")
    if kind == "bandpass":
        if (
            not isinstance(frequency, Sequence)
            or isinstance(frequency, (str, bytes))
            or len(frequency) != 2
        ):
            raise ValidationError("Band-pass requires two cutoff frequencies.")
        low, high = (_frequency(audio, value) for value in frequency)
        if high <= low:
            raise ValidationError("Band-pass cutoffs must be ascending.")
        cutoff: float | tuple[float, float] = (low, high)
    elif isinstance(frequency, Real):
        cutoff = _frequency(audio, cast(float, frequency))
    else:
        raise ValidationError("Filter requires a single cutoff frequency.")
    if kind == "notch":
        assert isinstance(cutoff, float)
        b, a = signal.iirnotch(cutoff, _number(q, "Notch Q", 0.01, 1000), fs=audio.sample_rate)
        return Audio(signal.lfilter(b, a, audio.samples, axis=0), audio.sample_rate)
    if isinstance(order, bool) or not isinstance(order, Integral) or not 1 <= order <= 12:
        raise ValidationError("Butterworth order must be an integer in [1, 12].")
    try:
        with np.errstate(divide="raise", invalid="raise", over="raise"):
            sos = signal.butter(int(order), cutoff, btype=kind, fs=audio.sample_rate, output="sos")
    except (FloatingPointError, ValueError) as exc:
        raise ValidationError("The sample rate and cutoffs do not yield a usable filter.") from exc
    return Audio(signal.sosfilt(sos, audio.samples, axis=0), audio.sample_rate)


def parametric_eq(audio: Audio, frequency_hz: float, db: float, *, q: float = 1.0) -> Audio:
    """Apply the RBJ peaking EQ biquad at the given center frequency."""
    omega = 2 * np.pi * _frequency(audio, frequency_hz) / audio.sample_rate
    amplitude = _amplitude(_number(db, "EQ gain", -48, 48) / 2)
    alpha = np.sin(omega) / (2 * _number(q, "EQ Q", 0.01, 1000))
    middle = -2 * np.cos(omega)
    b = [1 + alpha * amplitude, middle, 1 - alpha * amplitude]
    a = [1 + alpha / amplitude, middle, 1 - alpha / amplitude]
    return Audio(signal.lfilter(b, a, audio.samples, axis=0), audio.sample_rate)


def _coefficient(audio: Audio, ms: float) -> float:
    milliseconds = _number(ms, "Time constant (ms)", 0, 60_000)
    return exp(-1_000 / (milliseconds * audio.sample_rate)) if milliseconds else 0.0


def _envelope(
    targets: FloatArray, downward: float, upward: float, *, initial: float = 1.0
) -> FloatArray:
    smoothed = np.empty_like(targets)
    previous = initial
    for index, target in enumerate(targets):
        coefficient = downward if target < previous else upward
        previous = coefficient * previous + (1 - coefficient) * target
        smoothed[index] = previous
    return smoothed


def _linked_peak(audio: Audio) -> FloatArray:
    return np.max(np.abs(audio.samples), axis=1)


def compress(
    audio: Audio,
    threshold_dbfs: float = -18.0,
    ratio: float = 4.0,
    *,
    attack_ms: float = 10.0,
    release_ms: float = 100.0,
    makeup_db: float = 0.0,
) -> Audio:
    """Apply a hard-knee, peak-sensing compressor with shared channel gain."""
    threshold = _number(threshold_dbfs, "Compressor threshold", -300, 0)
    ratio = _number(ratio, "Compression ratio", 1, 100)
    makeup = _number(makeup_db, "Makeup gain", -48, 48)
    levels = 20 * np.log10(np.maximum(_linked_peak(audio), 1e-30))
    targets = 10 ** (np.maximum(levels - threshold, 0) * (1 / ratio - 1) / 20)
    envelope = _envelope(targets, _coefficient(audio, attack_ms), _coefficient(audio, release_ms))
    return Audio(audio.samples * envelope[:, None] * _amplitude(makeup), audio.sample_rate)


def limit(audio: Audio, ceiling_dbfs: float = -1.0, *, release_ms: float = 50.0) -> Audio:
    """Guarantee a sample-peak ceiling with instantaneous attack and smoothed release."""
    ceiling = _amplitude(_number(ceiling_dbfs, "Limiter ceiling", -300, 0))
    targets = ceiling / np.maximum(ceiling, _linked_peak(audio))
    envelope = _envelope(targets, 0.0, _coefficient(audio, release_ms))
    return Audio(audio.samples * envelope[:, None], audio.sample_rate)


def noise_gate(
    audio: Audio,
    threshold_dbfs: float = -45.0,
    *,
    range_db: float = 60.0,
    attack_ms: float = 5.0,
    release_ms: float = 75.0,
) -> Audio:
    """Attenuate frames below a linked peak threshold with smoothed gain."""
    threshold = _amplitude(_number(threshold_dbfs, "Gate threshold", -300, 0))
    closed = _amplitude(-_number(range_db, "Gate attenuation", 0, 300))
    targets = np.where(_linked_peak(audio) >= threshold, 1.0, closed)
    envelope = _envelope(
        targets, _coefficient(audio, release_ms), _coefficient(audio, attack_ms), initial=closed
    )
    return Audio(audio.samples * envelope[:, None], audio.sample_rate)


def fade(audio: Audio, fade_in_seconds: float = 0, fade_out_seconds: float = 0) -> Audio:
    """Apply linear-amplitude fades, multiplying the ramps where they overlap."""
    result = audio.samples.copy()
    for seconds, view in ((fade_in_seconds, result), (fade_out_seconds, result[::-1])):
        duration = _number(seconds, "Fade duration (seconds)", 0, 86_400)
        length = ceil(duration * audio.sample_rate)
        used = min(length, audio.frames)
        if used:
            ramp = np.arange(used) / max(length - 1, 1)
            view[:used] *= ramp[:, None]
    return Audio(result, audio.sample_rate)


def to_mono(audio: Audio) -> Audio:
    """Mix channels with an arithmetic mean without layout-specific weights."""
    if audio.channels == 1:
        return audio
    return Audio(audio.samples.mean(axis=1), audio.sample_rate)


def to_stereo(audio: Audio) -> Audio:
    """Duplicate mono or return stereo unchanged."""
    if audio.channels == 2:
        return audio
    if audio.channels != 1:
        raise ValidationError("Stereo conversion accepts mono or stereo only.")
    return Audio(np.repeat(audio.samples, 2, axis=1), audio.sample_rate)


def _stereo_channels(audio: Audio) -> tuple[FloatArray, FloatArray]:
    if audio.channels != 2:
        raise ValidationError("Mid/Side processing requires exactly two channels.")
    return audio.samples[:, 0], audio.samples[:, 1]


def encode_mid_side(audio: Audio) -> Audio:
    """Convert stereo to [(L + R) / 2, (L - R) / 2]."""
    left, right = _stereo_channels(audio)
    return Audio(np.column_stack((left + right, left - right)) / 2, audio.sample_rate)


def decode_mid_side(audio: Audio) -> Audio:
    """Convert Mid/Side to left/right stereo."""
    mid, side = _stereo_channels(audio)
    return Audio(np.column_stack((mid + side, mid - side)), audio.sample_rate)


def process_mid_side(audio: Audio, mid_db: float = 0, side_db: float = 0) -> Audio:
    """Change mid and side gains independently, then decode to left/right."""
    ms = encode_mid_side(audio)
    mid = _amplitude(_number(mid_db, "Mid gain", -300, 300))
    side = _amplitude(_number(side_db, "Side gain", -300, 300))
    return decode_mid_side(Audio(ms.samples * [mid, side], audio.sample_rate))


def resample(audio: Audio, sample_rate: int) -> Audio:
    """Convert sample rate with a polyphase low-pass FIR."""
    target = validate_rate(sample_rate)
    if target == audio.sample_rate:
        return audio
    divisor = gcd(target, audio.sample_rate)
    samples = signal.resample_poly(
        audio.samples, target // divisor, audio.sample_rate // divisor, axis=0
    )
    return Audio(samples, target)
