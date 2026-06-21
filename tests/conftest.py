"""
tests/conftest.py
=================
Shared pytest fixtures for atrium-translator unit tests.

No ML models, no network, no GPU required.
All file I/O uses pytest's ``tmp_path`` fixture so tests are hermetic.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── XML file fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def alto_xml_file(tmp_path: Path) -> Path:
    """Copy the sample ALTO XML fixture into a writable temp directory."""
    dst = tmp_path / "sample.alto.xml"
    dst.write_bytes((FIXTURES_DIR / "sample.alto.xml").read_bytes())
    return dst


@pytest.fixture
def amcr_xml_file(tmp_path: Path) -> Path:
    """Copy the sample AMCR OAI-PMH XML fixture into a writable temp directory."""
    dst = tmp_path / "sample_amcr.xml"
    dst.write_bytes((FIXTURES_DIR / "sample_amcr.xml").read_bytes())
    return dst


# ── CSV capture ───────────────────────────────────────────────────────────────


class _CapturingWriter:
    """Minimal csv.writer stand-in that records every writerow call."""

    def __init__(self):
        self.rows: list = []

    def writerow(self, row):
        self.rows.append(list(row))


@pytest.fixture
def csv_sink():
    """Return a ``(writer, rows)`` pair.

    *writer* implements ``writerow``; every call appends to *rows*.
    Usage::

        writer, rows = csv_sink
        process_amcr_xml(..., csv_writer=writer)
        assert len(rows) == 1
    """
    writer = _CapturingWriter()
    return writer, writer.rows


# ── mock collaborators ────────────────────────────────────────────────────────


@pytest.fixture
def mock_translator():
    """Translator whose ``translate()`` echoes ``[TR: <original_text>]``.

    Keeps the original text visible so tests can verify what was passed in.
    """
    t = MagicMock()
    t.translate.side_effect = lambda text, src, tgt: f"[TR: {text}]"
    return t


@pytest.fixture
def mock_identifier():
    """Language identifier that always returns ``("cs", 0.99)``."""
    ident = MagicMock()
    ident.detect.return_value = ("cs", 0.99)
    return ident
