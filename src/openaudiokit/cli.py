"""Typer commands. Install the cli extra to use the command-line interface."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

try:
    import typer
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install OpenAudioKit with the [cli] extra to use this command.") from exc

from .audio import load_audio, write_audio
from .batch import Progress, load_preset, process_audio, run_batch
from .config import QCConfig
from .dsp import normalize_loudness, normalize_peak
from .errors import AudioError, ValidationError
from .qc import compare as compare_audio
from .qc import inspect
from .report import render_report, write_report

app = typer.Typer(help="Analyze, compare, process and check digital audio.", no_args_is_help=True)


@contextmanager
def _handle_audio_error() -> Iterator[None]:
    try:
        yield
    except AudioError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _emit(data: object, output: Path | None) -> None:
    if output is None:
        typer.echo(render_report(data), nl=False)
    else:
        write_report(output, data)


@app.command()
def analyze(
    source: Path, output: Annotated[Path | None, typer.Option("--output", "-o")] = None
) -> None:
    """Analyze one recording and emit JSON, CSV or HTML."""
    with _handle_audio_error():
        _emit(inspect(load_audio(source)), output)


@app.command()
def compare(
    reference: Path,
    candidate: Path,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Compare measurements for two recordings."""
    with _handle_audio_error():
        _emit(compare_audio(load_audio(reference), load_audio(candidate)), output)


@app.command()
def normalize(
    source: Path,
    destination: Path,
    peak: Annotated[float | None, typer.Option(help="Sample peak target in dBFS.")] = None,
    lufs: Annotated[float | None, typer.Option(help="Integrated loudness target in LUFS.")] = None,
    ceiling: Annotated[float, typer.Option(help="True-peak ceiling in LUFS mode, in dBTP.")] = -1.0,
) -> None:
    """Normalize to a sample peak or a loudness target."""
    with _handle_audio_error():
        if peak is not None and lufs is not None:
            raise ValidationError("Specify only one of --peak and --lufs.")
        audio = load_audio(source)
        result = (
            normalize_loudness(audio, lufs, ceiling)
            if lufs is not None
            else normalize_peak(audio, peak if peak is not None else -1.0)
        )
        write_audio(destination, result)


@app.command()
def process(source: Path, destination: Path, preset: Annotated[Path, typer.Option()]) -> None:
    """Process one file with an ordered JSON preset."""
    with _handle_audio_error():
        write_audio(destination, process_audio(load_audio(source), load_preset(preset)))


@app.command()
def batch(
    sources: Annotated[list[Path], typer.Argument(help="Input recordings.")],
    preset: Annotated[Path | None, typer.Option()] = None,
    output_dir: Annotated[Path | None, typer.Option()] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Analyze several recordings and optionally apply a preset."""
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    else:
        # Keep the machine-readable report on stdout. Progress goes to stderr.
        logging.basicConfig(level=logging.ERROR)

    def report_progress(event: Progress) -> None:
        status = "failed" if event.result.error is not None else "done"
        typer.echo(f"[{event.completed}/{event.total}] {event.result.source}: {status}", err=True)

    with _handle_audio_error():
        operations = load_preset(preset) if preset is not None else ()
        result = run_batch(
            sources, operations=operations, output_dir=output_dir, progress=report_progress
        )
        _emit(result, output)
    if any(item.error is not None for item in result):
        raise typer.Exit(1)


@app.command()
def report(
    source: Path,
    destination: Path,
    expected_sample_rate: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Write a QC report as JSON, CSV or HTML."""
    with _handle_audio_error():
        config = QCConfig(expected_sample_rate=expected_sample_rate)
        write_report(destination, inspect(load_audio(source), config))


def main() -> None:
    """Run the CLI entry point."""
    app()


if __name__ == "__main__":
    main()
