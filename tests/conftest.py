"""Shared fixtures and matplotlib hardening for the suite."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

# Make `import example_figures` work from the tests/fixtures dir.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "fixtures"))


@pytest.fixture(autouse=True)
def _cleanup_figs():
    """Close any matplotlib figures the test created so we don't leak state."""
    yield
    plt.close("all")


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()
