import os
import sys

# Must run before any PySide6 import: under WSL2 Qt's "wayland" platform
# plugin can fail to load, so default to "xcb" there (same as oilIQ). Only a
# default - an explicit QT_QPA_PLATFORM always wins - and skipped entirely on
# Windows/macOS where it doesn't apply.
if sys.platform.startswith("linux") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from skyfish.ui.main_window import MainWindow
from skyfish.version import APP_NAME, APP_VERSION


def main() -> None:
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    window = MainWindow()
    window.showMaximized()
    # Once the event loop has run, so the window appears straight away and
    # the file loads into it rather than delaying the first paint.
    QTimer.singleShot(0, window.reopen_last_file)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
