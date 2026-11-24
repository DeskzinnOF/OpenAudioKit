"""Regression signals for mastering measurements and PCM/MP3 distinctions."""

from io import BytesIO

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from openaudiokit import Audio, load_audio, write_audio
from openaudiokit.dsp import compress, filter_audio, limit, noise_gate, resample
from openaudiokit.loudness import _RLB, _SHELF, _weighted_power
from openaudiokit.qc import inspect


def test_decoded_float_wav_keeps_headroom_and_format():
    stream = BytesIO()
    write_audio(stream, Audio([1, 1.1, -1.01, 0], 48000), format="WAV")
    stream.seek(0)
    audio = load_audio(stream)
    assert (audio.source_format, audio.source_subtype) == ("WAV", "DOUBLE")
    result = inspect(audio)
    assert result.metrics.clipping_samples == 3
    assert result.metrics.above_nominal_samples == 2
    assert "undetermined" in next(i.message for i in result.issues if i.code == "clipping")


def test_mp3_can_decode_above_one_from_a_signal_below_one():
    if not sf.check_format("MP3", "MPEG_LAYER_III"):
        pytest.skip("libsndfile MP3 encoder unavailable")
    rate = 48_000
    t = np.arange(rate) / rate
    source = np.sign(np.sin(2 * np.pi * 997 * t)) * 0.98
    stream = BytesIO()
    sf.write(stream, source, rate, format="MP3", subtype="MPEG_LAYER_III")
    stream.seek(0)
    audio = load_audio(stream)
    assert audio.source_format == "MP3"
    assert np.max(audio.samples) > 1
    assert np.count_nonzero(audio.samples > 1) > 0
    result = inspect(audio)
    assert result.metrics.above_nominal_samples == np.count_nonzero(np.abs(audio.samples) > 1)
    assert "lossy_peak_threshold" in {item.code for item in result.issues}
    assert "clipping" not in {item.code for item in result.issues}


def test_block_k_weighting_matches_continuous_filter_state():
    rng = np.random.default_rng(2026)
    audio = Audio(rng.uniform(-0.4, 0.4, (180000, 2)), 48000)
    weighted = signal.sosfilt(np.array([_SHELF, _RLB]), audio.samples, axis=0)
    np.testing.assert_array_equal(_weighted_power(audio), np.sum(weighted**2, axis=1))


@pytest.mark.parametrize("kind", ["lowpass", "highpass"])
def test_butterworth_is_minus_3db_at_cutoff(kind):
    fs, frequency = 48_000, 3000
    t = np.arange(fs) / fs
    audio = Audio(0.2 * np.sin(2 * np.pi * frequency * t), fs)
    result = filter_audio(audio, kind, frequency)
    ratio = np.linalg.norm(result.samples[fs // 2 :]) / np.linalg.norm(audio.samples[fs // 2 :])
    assert ratio == pytest.approx(1 / np.sqrt(2), abs=1e-9)


def test_compressor_gain_time_constants_and_gate():
    rate = 8_000
    attack_ms, release_ms = 10, 25
    audio = Audio(np.r_[np.full(2000, 0.8), np.full(500, 0.1)], rate)
    result = compress(audio, -20, 2, attack_ms=attack_ms, release_ms=release_ms)
    reduction = np.sqrt(0.1 / 0.8)
    assert result.samples[79, 0] / 0.8 == pytest.approx(
        reduction + (1 - reduction) * np.exp(-1), abs=1e-10
    )
    assert result.samples[2199, 0] / 0.1 == pytest.approx(
        1 - (1 - reduction) * np.exp(-1), abs=1e-10
    )
    gated = noise_gate(Audio([0.05, 0.5], rate), -20, range_db=20, attack_ms=0, release_ms=0)
    np.testing.assert_allclose(gated.samples[:, 0], [0.005, 0.5])
    limited = limit(audio, -6, release_ms=50)
    assert np.max(np.abs(limited.samples)) <= 10 ** (-6 / 20) + 1e-12


def test_downsampling_rejects_frequencies_above_new_nyquist():
    t = np.arange(48000) / 48000
    audio = Audio(np.sin(2 * np.pi * 15_000 * t), 48000)
    output = resample(audio, 16000)
    assert np.sqrt(np.mean(output.samples[100:-100] ** 2)) < 0.002
