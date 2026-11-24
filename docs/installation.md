# Installation

OpenAudioKit requires Python 3.10 or later. Run these commands from the source checkout.

```sh
python -m venv .venv
```

Activate the environment with `.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` in a POSIX shell.

```sh
python -m pip install -e .
python -m pip install -e ".[cli,gui,api]"
```

NumPy, SciPy and SoundFile are the only direct core dependencies. Most SoundFile wheels bundle libsndfile. When using a source distribution, install libsndfile through your system package manager. Readable and writable formats depend on that installation.

Start `openaudiokit --help`, `openaudiokit-gui`, or `openaudiokit-api --help` after installing the corresponding extra. Linux desktop installs need a working Qt platform plugin and its system libraries.

Original source code is licensed under MIT. Dependencies retain their own licenses. In particular, review the [PyQt licensing terms](https://www.riverbankcomputing.com/commercial/license-faq) before redistributing packages that bundle it.
