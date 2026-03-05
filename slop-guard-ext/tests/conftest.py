"""Pytest fixtures for Slop Guard browser extension tests.

Provides fixtures that:
- Build browser extension packages
- Serve extension files via a local HTTP server for cross-browser testing

Both Chromium and Firefox tests use the Firefox build (which bundles
Pyodide locally) so tests work offline without CDN access.
"""

import http.server
import subprocess
import sys
import threading
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).parent.parent
DIST_DIR = EXT_DIR / "dist"


def _ensure_build() -> None:
    """Run build.py so tests always use a fresh browser bundle."""
    subprocess.check_call(
        [sys.executable, str(EXT_DIR / "build.py"), "--no-zip"],
    )


@pytest.fixture(scope="session", autouse=True)
def build_extensions() -> None:
    """Build browser extensions before running tests."""
    _ensure_build()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that suppresses request logs."""

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="session")
def extension_server():
    """Serve the Firefox build on a local HTTP server.

    Uses the Firefox build because it bundles Pyodide locally,
    allowing tests to run without CDN access. The popup UI and
    analysis logic are identical across builds.

    Returns the base URL (e.g. http://127.0.0.1:PORT).
    """
    directory = str(DIST_DIR / "firefox")
    handler = type(
        "Handler",
        (_QuietHandler,),
        {
            "__init__": lambda self, *a, **kw: _QuietHandler.__init__(
                self, *a, directory=directory, **kw
            )
        },
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# Aliases for test files that reference browser-specific servers
chrome_server = extension_server
firefox_server = extension_server
