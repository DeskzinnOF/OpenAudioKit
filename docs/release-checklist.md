# Release checklist

- [ ] Review the changelog and confirm version 0.2.0 in the package and citation metadata.
- [ ] Run lint, type checking, tests with coverage, and the strict documentation build.
- [ ] Confirm the GitHub Actions matrix passes for Windows, Linux and macOS.
- [ ] Review desktop screenshots and exercise an actual audio output device.
- [ ] Review dependency advisories and redistribution terms. Ship the wheel and sdist without bundled Qt.
- [ ] Build both distributions, check with Twine and install the wheel in a fresh environment.
- [ ] Verify the public PyPI name and configure Trusted Publishing and GitHub Pages.
- [ ] Rebuild on the release tag and attach the two artifacts and SHA-256 manifest to the GitHub release.
- [ ] Confirm the published PyPI package installs and the Pages URLs resolve.

## Repository metadata

Description: Open-source audio analysis, processing and quality control toolkit.

Suggested topics: `audio`, `dsp`, `audio-analysis`, `loudness`, `quality-control`, `python`, `pyqt6`, `fastapi`.

## Contributor tasks

| Suggested label | Task |
| --- | --- |
| good first issue | Add generated test coverage for PCM-24 rail detection at multiple sample rates. |
| good first issue | Add a worked example of decoding an unavailable or unrecognized format and displaying the error. |
| help wanted | Validate playback device negotiation on Core Audio and PipeWire. |
| help wanted | Run the licensed official EBU test corpus and report per-vector errors and tolerances. |
