"""Generate a stereo test waveform with no external assets."""

from argparse import ArgumentParser
from pathlib import Path

import numpy as np

from openaudiokit import Audio, write_audio


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rate = 48000
    time = np.arange(rate * 4) / rate
    tone = 0.25 * np.sin(2 * np.pi * 1000 * time)
    write_audio(args.output, Audio(np.column_stack((tone, tone * 0.8)), rate))


if __name__ == "__main__":
    main()
