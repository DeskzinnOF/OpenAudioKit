# Developer guide

From a Python virtual environment, install `python -m pip install -e ".[cli,gui,api,docs,dev]"`. The core accepts `Audio` on every DSP path. Put codec I/O in the audio layer and adapt reports rather than importing an interface from core code.

```sh
python -m ruff check .
python -m ruff format --check .
python -m mypy src/openaudiokit
python -m pytest --cov --cov-report=term-missing -W error
python -m mkdocs build --strict
```

Set `QT_QPA_PLATFORM=offscreen` for GUI tests without a display. Keep a separate device smoke test for playback because offscreen tests can validate state and seeking without confirming what a listener hears. Use generated files in tests. Describe the expected units, mathematical tolerance and codec assumptions when adding a fixture.

Use `python benchmarks/measure.py --seconds 10 --repeat 5` to time QC and high-pass filtering with the committed synthetic workload. Report the platform, versions, fixture length and run count with results.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| GUI cannot start on Linux | Verify the Qt platform plugin and its EGL/X11 runtime dependencies. Use offscreen only for automation. |
| Supported file opens but playback fails | The Qt media backend and libsndfile can support different codecs. Try a decoded WAV and check the selected system output device. |
| LUFS is empty | Check file duration, gating threshold, sample rate and channel layout. |
| FLAC export rejects the signal | Reduce the peak first or select floating-point WAV to preserve values above nominal full scale. |
| API rejects the upload | Check the actual body size, decoded scalar sample count and the same-origin policy. |
| Doc links fail in CI | Run MkDocs strict mode at the repository root. Use relative links for project Pages hosting. |

See the [release checklist](release-checklist.md) for review and publication steps.
