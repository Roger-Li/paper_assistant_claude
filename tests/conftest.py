"""Shared test configuration."""

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with all subdirectories."""
    for subdir in ["papers", "audio", "pdfs"]:
        (tmp_path / subdir).mkdir()
    return tmp_path
