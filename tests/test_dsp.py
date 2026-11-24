import numpy as np
import pytest

from openaudiokit import Audio, ValidationError, dsp
from openaudiokit.loudness import integrated_loudness, true_peak


@pytest.fixture
def tone():
    time = np.arange(48000) / 48000
    return Audio(0.2 * np.sin(2 * np.pi * 1000 * time), 48000)


def test_level_processing(tone):
    louder = dsp.gain(tone, 6.020599913)
    np.testing.assert_allclose(louder.samples, tone.samples * 2)
    np.testing.assert_allclose(dsp.invert_polarity(tone).samples, -tone.samples)
    assert np.max(np.abs(dsp.normalize_peak(tone, -3).samples)) == pytest.approx(10 ** (-3 / 20))
    quiet = Audio([0.0], 48000)
    assert dsp.normalize_peak(quiet) is quiet
    result = dsp.normalize_loudness(tone)
    assert integrated_loudness(result) == pytest.approx(-23, abs=0.15)
    result = dsp.normalize_loudness(tone, -1, -6)
    assert true_peak(result) == pytest.approx(10 ** (-6 / 20), abs=1e-8)
    with pytest.raises(ValidationError):
        dsp.normalize_loudness(quiet)
    with pytest.raises(ValidationError):
        dsp.normalize_peak(tone, 1)


@pytest.mark.parametrize(
    ("kind", "frequency", "ratio"),
    [
        ("lowpass", 400, 0.04),
        ("highpass", 2500, 0.04),
        ("bandpass", (1500, 2500), 0.2),
        ("notch", 1000, 0.04),
    ],
)
def test_filter_rejection(tone, kind, frequency, ratio):
    filtered = dsp.filter_audio(tone, kind, frequency)
    assert np.std(filtered.samples[24000:]) < ratio * np.std(tone.samples)


def test_eq(tone):
    equalized = dsp.parametric_eq(tone, 1000, 6, q=1)
    assert np.std(equalized.samples[24000:]) / np.std(tone.samples) == pytest.approx(10**0.3)


def test_dynamics():
    audio = Audio(np.tile([0.8, 0.4], (8000, 1)), 8000)
    result = dsp.compress(audio, threshold_dbfs=-12, ratio=4)
    target = 10 ** ((-12 + (20 * np.log10(0.8) + 12) / 4) / 20)
    np.testing.assert_allclose(result.samples[-1], [target, target / 2], atol=1e-8)
    assert np.max(np.abs(dsp.limit(audio, -6).samples)) == pytest.approx(10 ** (-6 / 20))
    assert dsp.noise_gate(audio, threshold_dbfs=-6).samples[-1, 0] == pytest.approx(0.8)
    assert dsp.noise_gate(dsp.gain(audio, -50)).samples[-1, 0] < 0.001
    assert dsp.limit(Audio([0, 0.8, 0.1], 8000), -6).samples[-1, 0] < 0.1


def test_routing_and_fades(tone):
    stereo = dsp.to_stereo(tone)
    np.testing.assert_array_equal(stereo.samples[:, 0], stereo.samples[:, 1])
    assert dsp.to_stereo(stereo) is stereo
    assert dsp.to_mono(tone) is tone
    np.testing.assert_allclose(dsp.to_mono(stereo).samples, tone.samples)
    audio = Audio([[0.5, 0.1], [-0.4, 0.3]], 48000)
    ms = dsp.encode_mid_side(audio)
    np.testing.assert_allclose(ms.samples[0], [0.3, 0.2])
    np.testing.assert_allclose(dsp.decode_mid_side(ms).samples, audio.samples)
    assert dsp.process_mid_side(audio, side_db=-6).samples[0, 0] < 0.5
    flat = Audio(np.ones(10), 10)
    np.testing.assert_allclose(
        dsp.fade(flat, 0.3, 0.3).samples[:, 0], [0, 0.5, 1, 1, 1, 1, 1, 1, 0.5, 0]
    )
    assert dsp.fade(flat, 1.5).samples[-1, 0] < 1
    assert dsp.fade(flat, fade_out_seconds=0.1).samples[-1, 0] == 0


def test_resampling(tone):
    assert dsp.resample(tone, 48000) is tone
    reduced = dsp.resample(tone, 16000)
    assert (reduced.frames, reduced.sample_rate) == (16000, 16000)
    expected = np.sin(2 * np.pi * 1000 * np.arange(16000) / 16000) * 0.2
    np.testing.assert_allclose(reduced.samples[100:-100, 0], expected[100:-100], atol=3e-4)


@pytest.mark.parametrize(
    "call",
    [
        lambda a: dsp.gain(a, np.nan),
        lambda a: dsp.gain(a, 9000),
        lambda a: dsp.filter_audio(a, "lowpass", 25000),
        lambda a: dsp.filter_audio(a, "lowpass", (100, 200)),
        lambda a: dsp.filter_audio(a, "bandpass", (200, 100)),
        lambda a: dsp.filter_audio(a, "lowpass", 100, order=0),
        lambda a: dsp.filter_audio(a, "other", 100),
        lambda a: dsp.parametric_eq(a, 100, 2, q=0),
        lambda a: dsp.compress(a, ratio=0.5),
        lambda a: dsp.compress(a, attack_ms=-1),
        lambda a: dsp.limit(a, 3),
        lambda a: dsp.noise_gate(a, range_db=-1),
        lambda a: dsp.fade(a, -1),
        lambda a: dsp.encode_mid_side(a),
        lambda a: dsp.to_stereo(Audio([[0.1, 0.2, 0.3]], 48000)),
        lambda a: dsp.resample(a, 0),
    ],
)
def test_invalid_parameters(tone, call):
    with pytest.raises(ValidationError):
        call(tone)
