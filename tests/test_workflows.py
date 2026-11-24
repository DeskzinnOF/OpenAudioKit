import csv
import io
import json

import numpy as np
import pytest

from openaudiokit import Audio, AudioIOError, ValidationError, load_audio, write_audio
from openaudiokit.batch import load_preset, process_audio, run_batch
from openaudiokit.config import QCConfig
from openaudiokit.qc import compare, inspect
from openaudiokit.report import render_report, write_report


def test_qc():
    stereo = np.zeros((48000, 2))
    stereo[:4800] = [0.9, -0.9]
    stereo[:100] = [1.02, -1.02]
    report = inspect(Audio(stereo, 16000), QCConfig(expected_sample_rate=48000))
    codes = {issue.code for issue in report.issues}
    assert {
        "clipping",
        "true_peak",
        "dc_offset",
        "negative_correlation",
        "silence",
        "sample_rate_mismatch",
    } <= codes
    assert inspect(Audio(np.zeros(200), 8000)).issues[0].code == "digital_silence"
    for kwargs in (
        {"minimum_correlation": 3},
        {"clipping_level": 0},
        {"dc_threshold": -1},
        {"minimum_silence_seconds": -1},
        {"silence_dbfs": 1},
        {"true_peak_ceiling": 1},
        {"expected_sample_rate": 0},
        {"dc_threshold": float("nan")},
    ):
        with pytest.raises(ValidationError):
            QCConfig(**kwargs)


def test_compare():
    t = np.arange(3 * 8000) / 8000
    samples = 0.1 * np.sin(2 * np.pi * 1000 * t)
    reference = Audio(np.column_stack([samples, samples]), 8000)
    candidate = Audio(np.column_stack([0.5 * samples, -0.5 * samples]), 8000)
    result = compare(reference, candidate)
    assert result.deltas["integrated_lufs"] == pytest.approx(-6.0206, abs=0.001)
    assert result.deltas["stereo_correlation"] == pytest.approx(-2)
    assert result.deltas["side_energy_fraction"] == pytest.approx(1)
    assert result.band_energy_deltas
    uneven = compare(reference, Audio([0], 48000))
    assert uneven.deltas["integrated_lufs"] is None
    assert uneven.band_energy_deltas is None
    near_zero = Audio([[1e-250, -1e-250]], 8000)
    assert inspect(near_zero).side_energy_fraction == pytest.approx(1)
    assert inspect(Audio([[0, 0]], 8000)).side_energy_fraction is None


def test_export(tmp_path):
    data = {
        "unsafe": "<script>alert(1)</script>",
        "formula": "=1+1",
        "nested": {"count": 2, "nil": None},
        "empty": [],
    }
    json_file = tmp_path / "report.json"
    write_report(json_file, data)
    assert json.loads(json_file.read_text("utf-8")) == data
    html = render_report(data, "html")
    assert "<script>" not in html and "&lt;script&gt;" in html
    rows = dict(list(csv.reader(io.StringIO(render_report(data, "csv"))))[1:])
    assert rows["formula"] == "'=1+1"
    assert rows["nested.count"] == "2"
    with pytest.raises(ValidationError):
        write_report(tmp_path / "invalid", data)
    with pytest.raises(ValidationError):
        render_report({"value": float("nan")}, "json")
    with pytest.raises(AudioIOError):
        write_report(tmp_path / "missing" / "report.json", data)


def test_batch(tmp_path):
    valid = tmp_path / "sample.wav"
    invalid = tmp_path / "invalid.wav"
    invalid.write_bytes(b"invalid")
    write_audio(valid, Audio([0.1, -0.1] * 100, 8000))
    preset = tmp_path / "preset.json"
    preset.write_text(
        json.dumps(
            {
                "operations": [
                    {"name": "gain", "params": {"db": 6}},
                    {"name": "resample", "params": {"sample_rate": 16000}},
                ]
            }
        ),
        "utf-8",
    )
    steps = load_preset(preset)
    progress = []
    result = run_batch(
        [valid, invalid], operations=steps, output_dir=tmp_path / "out", progress=progress.append
    )
    assert [item.error is None for item in result] == [True, False]
    assert [(event.completed, event.total) for event in progress] == [(1, 2), (2, 2)]
    assert load_audio(result[0].output).sample_rate == 16000
    assert result[0].report.sample_rate == 16000
    assert run_batch([valid])[0].output is None
    with pytest.raises(ValidationError):
        run_batch([valid], operations=steps)
    with pytest.raises(ValidationError):
        run_batch([])
    other = tmp_path / "elsewhere" / valid.name
    with pytest.raises(ValidationError):
        run_batch([valid, other], operations=steps, output_dir=tmp_path / "out")
    with pytest.raises(ValidationError):
        run_batch([valid, other.with_name("SAMPLE.WAV")], output_dir=tmp_path / "out")
    with pytest.raises(ValidationError):
        run_batch(
            [tmp_path / "out" / "sample.wav.wav", valid],
            operations=steps,
            output_dir=tmp_path / "out",
        )


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        "[]",
        "{}",
        '{"operations":{}}',
        '{"operations":[3]}',
        '{"operations":[{"name":"nope"}]}',
        '{"operations":[{"name":"gain","params":[]}]}',
        '{"operations":[{"name":"gain","params":{"wrong":1}}]}',
        '{"operations":[{"name":"gain","params":{"db":2,"audio":1}}]}',
    ],
)
def test_bad_presets(tmp_path, payload):
    preset = tmp_path / "bad.json"
    preset.write_text(payload, "utf-8")
    with pytest.raises(ValidationError):
        load_preset(preset)


def test_runtime_preset_errors(tmp_path):
    with pytest.raises(AudioIOError):
        load_preset(tmp_path / "missing")
    preset = tmp_path / "bad.json"
    preset.write_text('{"operations":[{"name":"gain","params":{"db":"bad"}}]}', "utf-8")
    with pytest.raises(ValidationError):
        process_audio(Audio([0.1], 8000), load_preset(preset))


@pytest.mark.parametrize(
    "operation",
    [
        {"name": "fade", "params": {"fade_in_seconds": 10**1000}},
        {"name": "filter", "params": {"kind": "lowpass", "frequency": 5e-324}},
    ],
)
def test_batch_extreme_preset_isolates_errors(tmp_path, operation):
    valid = tmp_path / "sample.wav"
    write_audio(valid, Audio([0.1], 16000))
    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps({"operations": [operation]}), "utf-8")
    result = run_batch(
        [valid, tmp_path / "missing.wav"],
        operations=load_preset(preset),
        output_dir=tmp_path / "out",
    )
    assert len(result) == 2
    assert all(item.error and item.output is None for item in result)


def test_processing_sample_budget(tmp_path):
    from openaudiokit import AudioLimitError
    from openaudiokit.batch import Operation

    audio = Audio([0.1] * 10, 8000)
    assert (
        process_audio(audio, [Operation("resample", {"sample_rate": 16000})], max_samples=20).frames
        == 20
    )
    with pytest.raises(AudioLimitError):
        process_audio(audio, [Operation("resample", {"sample_rate": 16000})], max_samples=19)
    with pytest.raises(AudioLimitError):
        process_audio(audio, [Operation("to_stereo")], max_samples=10)
    with pytest.raises(AudioLimitError):
        process_audio(audio, [], max_samples=9)
    with pytest.raises(ValidationError):
        process_audio(audio, [], max_samples=0)

    source = tmp_path / "sample.wav"
    write_audio(source, audio)
    out = tmp_path / "out"
    result = run_batch(
        [source], operations=[Operation("to_stereo")], output_dir=out, max_samples=10
    )
    assert result[0].output is None
    assert "processing sample limit" in result[0].error
    assert not list(out.iterdir())
