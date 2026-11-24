"""Inspect a delivery against a sample-rate requirement and save the results."""

from argparse import ArgumentParser
from pathlib import Path

from openaudiokit import load_audio
from openaudiokit.config import QCConfig
from openaudiokit.qc import inspect
from openaudiokit.report import write_report


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--sample-rate", type=int, default=48000)
    arguments = parser.parse_args()
    report = inspect(
        load_audio(arguments.audio), QCConfig(expected_sample_rate=arguments.sample_rate)
    )
    write_report(arguments.report, report)
    return int(any(issue.severity == "error" for issue in report.issues))


if __name__ == "__main__":
    raise SystemExit(main())
