"""Render actual OpenAudioKit desktop pages with synthetic stereo sources."""

import os
from argparse import ArgumentParser
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="Destination for three PNG screenshots.")
    parser.add_argument(
        "--font", type=Path, help="Optional installed TTF for a fontless Qt backend."
    )
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtCore import QElapsedTimer, QSettings, QThread
    from PyQt6.QtGui import QFont, QFontDatabase
    from PyQt6.QtWidgets import QApplication

    from openaudiokit.audio import Audio, write_audio
    from openaudiokit.gui import MainWindow

    args.output_dir.mkdir(exist_ok=True, parents=True)
    with TemporaryDirectory() as working_dir:
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, working_dir)
        app = QApplication([])
        app.setStyle("Fusion")
        font_path = args.font
        if font_path is None and not QFontDatabase.families() and os.name == "nt":
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if font_path is not None:
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if not families:
                raise OSError(f"Could not load screenshot font {font_path}")
            app.setFont(QFont(families[0]))
        if not QFontDatabase.families():
            raise OSError("No display fonts. Supply an installed font with --font.")
        rate = 48000
        time = np.arange(rate * 10) / rate
        pulse = np.sin(np.pi * time) ** 6
        low = 0.18 * np.sin(2 * np.pi * 110 * time)
        sweep = 0.16 * pulse * np.sin(2 * np.pi * (330 * time + 126 * time**2))
        high = 0.013 * np.cos(2 * np.pi * 6400 * time)
        left = low + sweep + high
        right = low + 0.16 * pulse * np.cos(2 * np.pi * (330 * time + 126 * time**2)) - high
        a = Path(working_dir) / "Coastline · reference.wav"
        b = Path(working_dir) / "Coastline · candidate.wav"
        write_audio(a, Audio(np.column_stack((left, right)), rate))
        write_audio(b, Audio(np.column_stack((left * 0.85, right * 0.65)), rate))
        window = MainWindow()
        window.resize(1280, 800)
        window.show()
        try:
            for slot, path in (("A", a), ("B", b)):
                window.open_path(slot, path)
                elapsed = QElapsedTimer()
                elapsed.start()
                while window.busy and elapsed.elapsed() < 20_000:
                    app.processEvents()
                    QThread.msleep(5)
                if window.busy:
                    raise TimeoutError("Desktop did not finish analyzing screenshot source.")
            window.scale.setCurrentIndex(1)
            for page, image, language in (
                (0, "desktop.png", "en"),
                (2, "desktop-spectrum.png", "en"),
                (4, "desktop-comparison-pt.png", "pt"),
            ):
                window._set_language(language)
                window.tabs.setCurrentIndex(page)
                app.processEvents()
                if not window.grab().save(str(args.output_dir / image)):
                    raise OSError(f"Could not write {image}")
        finally:
            window.close()


if __name__ == "__main__":
    main()
