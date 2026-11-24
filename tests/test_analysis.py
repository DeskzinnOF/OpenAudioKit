import numpy as np
import pytest

from openaudiokit.analysis import AnalysisConfig, analyze
from openaudiokit.audio import Audio
from openaudiokit.errors import ValidationError
from openaudiokit.loudness import integrated_loudness, loudness_history, measure_loudness, true_peak


def sine(rate, frequency, seconds, amplitude=1.0):
    return amplitude * np.sin(2 * np.pi * frequency * np.arange(round(rate * seconds)) / rate)


def test_levels_and_stereo_correlation():
    samples = sine(48000, 1000, 1, 0.5)
    audio = Audio(np.column_stack((samples, -samples)), 48000)
    result = analyze(audio)
    assert result.rms_dbfs == pytest.approx(-9.03089987)
    assert result.peak_dbfs == pytest.approx(-6.02059991)
    assert result.crest_factor_db == pytest.approx(3.01029996)
    assert result.dynamic_range_db == pytest.approx(0, abs=1e-9)
    assert result.stereo_correlation == pytest.approx(-1)
    assert result.dc_offset == pytest.approx((0, 0), abs=1e-12)
    assert result.clipping_samples == 0
    assert result.silence_seconds == 0


def test_silence_and_short_audio_are_explicit():
    result = analyze(Audio(np.zeros((8000, 2)), 8000))
    assert result.rms_dbfs is None
    assert result.peak_dbfs is None
    assert result.true_peak_dbtp is None
    assert result.crest_factor_db is None
    assert result.dynamic_range_db is None
    assert result.stereo_correlation is None
    assert result.loudness.integrated_lufs is None
    assert result.loudness.momentary_max_lufs is None
    assert result.loudness.short_term_max_lufs is None
    assert result.loudness.lra_lu is None
    assert result.silence_seconds == 1
    short = Audio(np.array([0.2, -0.2]), 48000)
    assert integrated_loudness(short) is None
    assert loudness_history(short).momentary_lufs.size == 0


def test_clipping_dc_and_partial_silence_window():
    samples = np.concatenate((np.full(800, 0.1), np.zeros(400)))
    samples[0], samples[1] = 1.0, -1.1
    result = analyze(Audio(samples, 8000), AnalysisConfig(window_seconds=0.1))
    assert result.clipping_samples == 2
    assert result.dc_offset == pytest.approx((79.7 / 1200,))
    assert result.silence_seconds == pytest.approx(0.05)
    assert result.stereo_correlation is None


@pytest.mark.parametrize(
    "options",
    [
        {"silence_threshold_dbfs": 1},
        {"window_seconds": float("nan")},
        {"window_seconds": 0},
        {"clipping_threshold": 0},
    ],
)
def test_invalid_analysis_config(options):
    with pytest.raises(ValidationError):
        AnalysisConfig(**options)


@pytest.mark.parametrize("rate", [8000, 44100, 48000, 48001, 96000])
def test_calibration_and_histories(rate):
    tone = sine(rate, 1000, 4, 0.1)
    stereo = Audio(np.tile(tone[:, None], (1, 2)), rate)
    mono = Audio(tone, rate)
    result = measure_loudness(stereo)
    assert result.integrated_lufs == pytest.approx(-20, abs=0.25)
    assert result.momentary_max_lufs == pytest.approx(-20, abs=0.25)
    assert result.short_term_max_lufs == pytest.approx(-20, abs=0.25)
    assert measure_loudness(mono).integrated_lufs == pytest.approx(
        result.integrated_lufs - 10 * np.log10(2), abs=1e-8
    )
    history = loudness_history(stereo)
    assert history.momentary_times_seconds[0] == pytest.approx(0.4, abs=1 / rate)
    assert history.short_term_times_seconds[0] == pytest.approx(3)
    assert history.short_term_times_seconds[-1] == pytest.approx(4, abs=1 / rate)


def test_integrated_ignores_sustained_quiet_sections():
    rate = 48000
    tone = sine(rate, 1000, 4, 0.1)
    tail = sine(rate, 1000, 8, 0.0001)
    assert integrated_loudness(Audio(np.concatenate((tone, tail)), rate)) == pytest.approx(
        integrated_loudness(Audio(tone, rate)),
        abs=0.2,
    )


def test_momentary_max_resolves_an_off_grid_short_tone():
    rate = 48000
    samples = np.concatenate((np.zeros(2400), sine(rate, 1000, 0.4, 0.1), np.zeros(rate)))
    result = measure_loudness(Audio(np.tile(samples[:, None], (1, 2)), rate))
    assert result.momentary_max_lufs == pytest.approx(-20, abs=0.1)


@pytest.mark.parametrize("quiet,loud,expected", [(0.03162278, 0.1, 10), (0.1, 0.17782794, 5)])
def test_ebu_lra_synthetic_tone_steps(quiet, loud, expected):
    rate = 8000
    samples = np.concatenate((sine(rate, 1000, 20, loud), sine(rate, 1000, 20, quiet)))
    assert measure_loudness(Audio(np.tile(samples[:, None], (1, 2)), rate)).lra_lu == (
        pytest.approx(expected, abs=0.15)
    )


def test_true_peak_recovers_intersample_peak():
    rate = 48000
    samples = 0.75 * np.sin(0.5 * np.pi * np.arange(rate) + 0.25 * np.pi)
    samples *= np.minimum(1, np.minimum(np.arange(rate), np.arange(rate)[::-1]) / 256)
    audio = Audio(samples, rate)
    assert np.max(np.abs(samples)) == pytest.approx(0.75 / np.sqrt(2))
    assert true_peak(audio) == pytest.approx(0.75, abs=0.005)


def test_multichannel_and_low_rate_loudness_are_unavailable():
    audio = Audio(np.full((500, 4), 0.1), 1000)
    result = analyze(audio)
    assert result.rms_dbfs == pytest.approx(-20)
    assert result.stereo_correlation is None
    assert result.loudness.integrated_lufs is None
    assert integrated_loudness(Audio(np.full(4000, 0.1), 4000)) is None
    assert measure_loudness(Audio(np.full((8000, 4), 0.1), 8000)).integrated_lufs is None
    assert loudness_history(audio).short_term_lufs.size == 0


def test_one_hertz_loudness_has_no_frames_without_warnings():
    audio = Audio(np.ones(1), 1)
    assert loudness_history(audio).momentary_lufs.size == 0
    assert measure_loudness(audio).integrated_lufs is None


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
def test_reference_pyloudnorm(rate):
    pyloudnorm = pytest.importorskip("pyloudnorm")
    tone = sine(rate, 277, 4, 0.05) + sine(rate, 2345, 4, 0.01)
    tone = np.tile(tone[:, None], (1, 2))
    tone[rate : 2 * rate] *= 0.25
    expected = pyloudnorm.Meter(rate, filter_class="DeMan").integrated_loudness(tone)
    assert integrated_loudness(Audio(tone, rate)) == pytest.approx(expected, abs=0.01)
