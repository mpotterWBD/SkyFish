import os

# Must be set before any PySide6 import happens anywhere in the test
# session, so widget-constructing tests run headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    # Qt only tolerates one QApplication per process, so every test shares this one.
    app = QApplication.instance() or QApplication([])
    yield app
