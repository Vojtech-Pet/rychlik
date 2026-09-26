import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from http_fixture_server import HttpFixtureServer


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def http_fixture_server():
    server = HttpFixtureServer().start()
    yield server
    server.stop()
