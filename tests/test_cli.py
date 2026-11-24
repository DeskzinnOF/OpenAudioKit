import json

import pytest
from typer.testing import CliRunner

from openaudiokit import Audio, load_audio, write_audio
from openaudiokit.cli import app


@pytest.fixture
def recording(tmp_path):
    source = tmp_path / "sample.wav"
    write_audio(source, Audio([0.25, -0.25] * 2000, 8000))
    return source


def test_cli_workflow(recording, tmp_path):
    runner = CliRunner()
    analyzed = runner.invoke(app, ["analyze", str(recording)])
    assert analyzed.exit_code == 0, analyzed.output
    assert json.loads(analyzed.stdout)["sample_rate"] == 8000
    normalized = tmp_path / "norm.wav"
    result = runner.invoke(app, ["normalize", str(recording), str(normalized), "--peak", "-6"])
    assert result.exit_code == 0, result.output
    assert load_audio(normalized).samples.max() == pytest.approx(10 ** (-6 / 20))
    comparison = runner.invoke(app, ["compare", str(recording), str(normalized)])
    assert comparison.exit_code == 0, comparison.output
    assert json.loads(comparison.stdout)["deltas"]["peak_dbfs"] > 0
    preset = tmp_path / "preset.json"
    preset.write_text('{"operations":[{"name":"invert_polarity"}]}', "utf-8")
    processed = tmp_path / "processed.wav"
    result = runner.invoke(
        app, ["process", str(recording), str(processed), "--preset", str(preset)]
    )
    assert result.exit_code == 0, result.output
    assert load_audio(processed).samples[0, 0] == -0.25
    report = tmp_path / "report.html"
    result = runner.invoke(
        app, ["report", str(recording), str(report), "--expected-sample-rate", "16000"]
    )
    assert result.exit_code == 0, result.output
    assert "sample_rate_mismatch" in report.read_text("utf-8")
    result = runner.invoke(app, ["batch", str(recording), str(tmp_path / "missing.wav")])
    assert result.exit_code == 1
    assert json.loads(result.stdout)[1]["error"]
    assert "[2/2]" in result.stderr


def test_cli_invalid_input(recording, tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["normalize", str(recording), str(tmp_path / "out.wav"), "--peak", "-4", "--lufs", "-20"],
    )
    assert result.exit_code == 1
    assert "Specify only one" in result.stderr
