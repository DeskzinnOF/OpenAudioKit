"""Time synthetic stereo QC and DSP separately, with no stored baseline."""

from argparse import ArgumentParser
from math import isfinite
from statistics import median
from time import perf_counter

import numpy as np

from openaudiokit import Audio
from openaudiokit.dsp import filter_audio
from openaudiokit.qc import inspect


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--repeat", type=int, default=3)
    options = parser.parse_args()
    if not isfinite(options.seconds) or not 0 < options.seconds <= 300 or options.repeat < 1:
        parser.error("Require 0 < seconds <= 300 and a positive repeat count.")
    rate = 48000
    samples = np.random.default_rng(1729).normal(0, 0.1, (max(1, round(rate * options.seconds)), 2))
    audio = Audio(samples, rate)

    def highpass(audio: Audio) -> Audio:
        return filter_audio(audio, "highpass", 70)

    for name, function in (("quality control", inspect), ("high-pass", highpass)):
        function(audio)
        elapsed = []
        for _ in range(options.repeat):
            start = perf_counter()
            function(audio)
            elapsed.append(perf_counter() - start)
        print(f"{name}: {median(elapsed):.4f} s median ({options.repeat} runs)")


if __name__ == "__main__":
    main()
