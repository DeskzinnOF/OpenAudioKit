# Contributing

Install a development environment from the checkout.

```sh
python -m pip install -e ".[cli,gui,api,docs,dev]"
python -m ruff check .
python -m ruff format --check .
python -m mypy src/openaudiokit
python -m pytest --cov --cov-report=term-missing
python -m mkdocs build --strict
```

Set `QT_QPA_PLATFORM=offscreen` for headless Qt runs. Keep recording fixtures synthetic and small. In a DSP change, include the expected numerical result, the units, the tolerance and its rationale. Describe any differences from a published algorithm or reference implementation. Keep PyQt6, Typer and FastAPI imports in their adapters.

For a bug, include the smallest reproducible command or generated signal. For changes to file or API behavior, update the matching tutorial or reference page. Submit original material or verify that its license permits redistribution. Pull requests are contributed under the repository's MIT license.

## 0.2.0 development

Use synthetic recordings or shareable, redacted regression fixtures. A desktop change should include an offscreen check and identify any tests that require real audio hardware. See `docs/release-checklist.md` for suggested contributor tasks.
