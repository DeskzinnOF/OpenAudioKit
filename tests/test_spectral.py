import numpy as np
import pytest

from openaudiokit.audio import Audio
from openaudiokit.errors import ValidationError
from openaudiokit.spectral import fft, spectral_summary, spectrogram, stft, transients


def test_fft_exact_bin_and_phase():
    rate = 8192
    time = np.arange(1024) / rate
    samples = 0.5 * np.sin(2 * np.pi * 512 * time)
    spectrum = fft(Audio(samples, rate))
    index = np.argmax(np.abs(spectrum.values[:, 0]))
    assert spectrum.frequencies_hz[index] == 512
    assert spectrum.values[index, 0] == pytest.approx(-256j)


def test_spectral_summary_preserves_antiphase_power():
    rate = 8192
    samples = np.sin(2 * np.pi * 1024 * np.arange(rate) / rate)
    summary = spectral_summary(Audio(np.column_stack((samples, -samples)), rate))
    assert summary.centroid_hz == pytest.approx(1024, abs=1)
    assert summary.bandwidth_hz == pytest.approx(2.309, abs=1)
    assert summary.rolloff_hz == pytest.approx(1024, abs=5)
    assert summary.band_energy["mid"] == pytest.approx(1, abs=0.01)
    assert sum(summary.band_energy.values()) == pytest.approx(1)


def test_stft_centers_and_parseval_power():
    samples = np.ones(256)
    audio = Audio(samples, 8000)
    transform = stft(audio, n_fft=64, hop=32)
    gram = spectrogram(audio, n_fft=64, hop=32)
    assert transform.values.shape == (33, 8, 1)
    assert gram.power.shape == (33, 8)
    assert gram.times_seconds.tolist() == pytest.approx(np.arange(8) * 32 / 8000)
    assert gram.frequencies_hz[-1] == 4000
    assert np.sum(gram.power[:, 1:-1], axis=0) == pytest.approx(np.ones(6))


def test_silence_and_short_spectrogram():
    silent = spectral_summary(Audio(np.zeros(1), 1))
    assert silent.centroid_hz is None
    assert silent.bandwidth_hz is None
    assert silent.rolloff_hz is None
    assert sum(silent.band_energy.values()) == 0
    gram = spectrogram(Audio(np.ones(1), 1), n_fft=5, hop=90)
    assert gram.power.shape == (3, 1)
    assert gram.power.sum() > 0


@pytest.mark.parametrize("amplitude", [1.0, 1e-250])
def test_summary_preserves_a_first_sample_impulse(amplitude):
    samples = np.zeros(4096)
    samples[0] = amplitude
    summary = spectral_summary(Audio(samples, 48000))
    assert summary.centroid_hz == pytest.approx(12000)
    assert sum(summary.band_energy.values()) == pytest.approx(1)


@pytest.mark.parametrize("n_fft,hop", [(0, 1), (32, 0), (64.0, 7), (64, 0.7)])
def test_spectrogram_rejects_invalid_configuration(n_fft, hop):
    with pytest.raises(ValidationError):
        spectrogram(Audio(np.zeros(100), 8000), n_fft=n_fft, hop=hop)


def test_transient_detection_finds_an_interior_click():
    audio = np.zeros(8000)
    audio[3200] = 1
    result = transients(Audio(audio, 8000), n_fft=256, hop=64)
    assert result.onset_times_seconds == pytest.approx([0.4], abs=0.02)
    silence = transients(Audio(np.zeros(8000), 8000), n_fft=256, hop=64)
    assert silence.onset_times_seconds.size == 0
