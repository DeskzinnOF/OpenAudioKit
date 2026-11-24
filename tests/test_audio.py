from io import BytesIO

import numpy as np
import pytest

from openaudiokit.audio import Audio, load_audio, write_audio
from openaudiokit.errors import AudioIOError, AudioLimitError, ValidationError


def test_audio_contract():
    original = np.array([0.1, 0.2])
    audio = Audio(original, 8000)
    original[0] = 1
    assert (audio.frames, audio.channels, audio.duration) == (2, 1, 0.00025)
    assert audio.samples[0, 0] == 0.1
    assert not audio.samples.flags.writeable


@pytest.mark.parametrize(
    ("samples", "rate"),
    [
        ([], 8000),
        ([np.nan], 8000),
        ([np.inf], 8000),
        ([1e7], 8000),
        ([1], 0),
        ([1], 384001),
        ([1], True),
        ([1], 8000.5),
        (np.zeros((1, 33)), 8000),
        (np.ones((1, 1, 1)), 8000),
        (["bad"], 8000),
        ([1j], 8000),
    ],
)
def test_invalid_audio(samples, rate):
    with pytest.raises(ValidationError):
        Audio(samples, rate)


def test_io_roundtrip_and_limits(tmp_path):
    path = tmp_path / "sound.wav"
    audio = Audio([[1.2, -0.1], [0.2, 0.3]], 48000)
    write_audio(path, audio)
    loaded = load_audio(BytesIO(path.read_bytes()))
    np.testing.assert_allclose(loaded.samples, audio.samples)
    assert loaded.sample_rate == 48000
    with pytest.raises(AudioLimitError):
        load_audio(path, max_samples=3)
    with pytest.raises(ValidationError):
        load_audio(path, max_samples=0)
    with pytest.raises(ValidationError):
        write_audio(path, audio, subtype="PCM_16")
    restored = load_audio(path)
    np.testing.assert_array_equal(restored.samples, loaded.samples)
    with pytest.raises(AudioIOError):
        load_audio(BytesIO(b"garbage"))
    with pytest.raises(AudioIOError):
        write_audio(tmp_path / "bad.nope", audio)
    with pytest.raises(AudioIOError):
        write_audio(tmp_path / "missing" / "sound.wav", audio)
    assert not list(tmp_path.glob(".*.tmp"))
