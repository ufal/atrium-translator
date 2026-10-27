"""
tests/test_cli_exit_codes.py — the batch CLI must tell its caller what happened.

`main()` had no `sys.exit` anywhere and every failure path was a bare `return`,
so `python main.py` exited **0** for a missing input path, a missing mode flag,
an unloadable XSD, an empty input directory, and for a batch in which every
single document failed. A Kubernetes `Job` or a cron wrapper around the batch
image therefore reported success for a run that translated nothing at all — the
one signal an operator has, inverted.

Per-file failures are still caught inside `process_single_file()` so that one bad
document does not abandon the batch; that behaviour is correct and is kept. What
changed is that the batch now reports them.

These tests run `main()` in an isolated cwd: `config.txt` in the repo root
supplies `fields`/`formats`, so a test that runs from the repo root silently gets
a valid configuration and cannot see the usage paths at all.
"""

import pytest

import main as main_module
from main import EXIT_FAILED, EXIT_NO_INPUT, EXIT_OK, EXIT_USAGE

_ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout><Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="100" HEIGHT="100">
    <PrintSpace><TextBlock ID="B1"><TextLine ID="L1">
      <String ID="S1" CONTENT="Ahoj" HPOS="0" VPOS="0" WIDTH="10" HEIGHT="5"/>
    </TextLine></TextBlock></PrintSpace>
  </Page></Layout>
</alto>
"""


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Run from a directory with no config.txt, so argv alone decides."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(monkeypatch, *argv) -> int:
    monkeypatch.setattr("sys.argv", ["main.py", *argv])
    return main_module.main()


def test_missing_input_path_is_a_usage_error(isolated, monkeypatch):
    assert _run(monkeypatch, str(isolated / "nope"), "--alto", "-o", str(isolated / "out")) == EXIT_USAGE


_AMCR = """<?xml version="1.0" encoding="UTF-8"?>
<amcr:amcr xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/"><amcr:nazev>Davle</amcr:nazev></amcr:amcr>
"""


def test_an_explicit_metadata_run_without_targets_is_a_usage_error(isolated, monkeypatch):
    doc = isolated / "record.xml"
    doc.write_text(_AMCR)
    # --formats names the mode (metadata), and there is no --xpaths and no config.txt `fields`.
    assert _run(monkeypatch, str(doc), "--formats", "xml", "-o", str(isolated / "out")) == EXIT_USAGE


def test_without_a_mode_flag_a_record_without_targets_is_a_failed_document(isolated, monkeypatch):
    """No --alto, --xpaths or --formats: since v1.6.0-beta the file's root element decides, as
    /translate decides an upload sent without is_alto (atrium-project#72). This used to be a usage
    error. A metadata record still needs XPath targets: without them it fails, before any call."""
    doc = isolated / "record.xml"
    doc.write_text(_AMCR)
    assert _run(monkeypatch, str(doc), "-o", str(isolated / "out")) == EXIT_FAILED


def test_empty_input_directory_reports_no_input(isolated, monkeypatch):
    empty = isolated / "empty"
    empty.mkdir()
    assert (
        _run(monkeypatch, str(empty), "--alto", "--formats", "alto.xml", "-o", str(isolated / "out")) == EXIT_NO_INPUT
    )


def test_a_failed_document_is_reported(isolated, monkeypatch):
    """A batch where every document fails must not exit 0."""
    doc = isolated / "sample.alto.xml"
    doc.write_text(_ALTO)
    monkeypatch.setattr(main_module, "process_single_file", lambda **kwargs: (False, 0))

    code = _run(monkeypatch, str(doc), "--alto", "--formats", "alto.xml", "-o", str(isolated / "out"))
    assert code == EXIT_FAILED


def test_a_successful_document_exits_ok(isolated, monkeypatch):
    doc = isolated / "sample.alto.xml"
    doc.write_text(_ALTO)
    monkeypatch.setattr(main_module, "process_single_file", lambda **kwargs: (True, 0))

    code = _run(monkeypatch, str(doc), "--alto", "--formats", "alto.xml", "-o", str(isolated / "out"))
    assert code == EXIT_OK


def test_exit_codes_are_distinct():
    """Each outcome a caller would branch on needs its own code."""
    assert len({EXIT_OK, EXIT_USAGE, EXIT_NO_INPUT, EXIT_FAILED}) == 4
    assert EXIT_OK == 0
