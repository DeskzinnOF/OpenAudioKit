# Project tree

Source, documentation and 0.2.0 archives. Virtual environments, caches and generated MkDocs output are omitted.

```text
OpenAudioKit/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   ├── workflows/
│   │   ├── ci.yml
│   │   ├── pages.yml
│   │   └── release.yml
│   └── PULL_REQUEST_TEMPLATE.md
├── benchmarks/
│   └── measure.py
├── dist/
│   └── 0.2.0/
│       ├── openaudiokit-0.2.0-py3-none-any.whl
│       ├── openaudiokit-0.2.0.tar.gz
│       └── SHA256SUMS.txt
├── docs/
│   ├── assets/
│   │   ├── banner.png
│   │   ├── banner.svg
│   │   ├── desktop-comparison-pt.png
│   │   ├── desktop-spectrum.png
│   │   ├── desktop.png
│   │   ├── logo-light.svg
│   │   ├── logo.png
│   │   └── logo.svg
│   ├── architecture.md
│   ├── cli.md
│   ├── desktop-pt.md
│   ├── desktop.md
│   ├── development.md
│   ├── index.md
│   ├── installation.md
│   ├── measurement.md
│   ├── project-tree.md
│   ├── pt-br.md
│   ├── python-api.md
│   ├── release-checklist.md
│   ├── releasing.md
│   ├── rest.md
│   ├── scientific-audit.md
│   └── tutorial.md
├── examples/
│   ├── cleanup.json
│   ├── delivery_check.py
│   ├── generate_tone.py
│   └── render_desktop_screenshots.py
├── src/
│   └── openaudiokit/
│       ├── __init__.py
│       ├── analysis.py
│       ├── api.py
│       ├── audio.py
│       ├── batch.py
│       ├── cli.py
│       ├── config.py
│       ├── dsp.py
│       ├── errors.py
│       ├── gui.py
│       ├── gui_processing.py
│       ├── gui_views.py
│       ├── logo.svg
│       ├── loudness.py
│       ├── playback.py
│       ├── py.typed
│       ├── qc.py
│       ├── report.py
│       └── spectral.py
├── tests/
│   ├── test_analysis.py
│   ├── test_api.py
│   ├── test_audio.py
│   ├── test_cli.py
│   ├── test_dsp.py
│   ├── test_gui.py
│   ├── test_gui02.py
│   ├── test_gui_views.py
│   ├── test_playback.py
│   ├── test_scientific_audit.py
│   ├── test_spectral.py
│   └── test_workflows.py
├── tools/
│   └── checksums.py
├── .gitignore
├── CHANGELOG.md
├── CITATION.cff
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── LICENSE
├── MANIFEST.in
├── mkdocs.yml
├── pyproject.toml
├── README.md
├── README.pt-BR.md
├── ROADMAP.md
└── SECURITY.md
```
