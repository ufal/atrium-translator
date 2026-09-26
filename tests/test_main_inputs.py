"""tests/test_main_inputs.py — which files a batch run picks up, and that its record is written.

Two defects found while re-running the shipped data samples (issue #46):

* **ALTO files in a metadata run.** `formats = xml` also matches `*.alto.xml`. In
  metadata mode the ALTO file matched none of the XPath targets, was written back
  unchanged with an empty log, and counted as a success — which is how both
  `data_samples/*/xml/` folders came to hold an `MTX…_en.alto.xml`.
* **The run record of a long run.** `ParadataLogger` creates `paradata/` when the run
  STARTS; a ten-minute ALTO run outlived the folder (git removed it once its last
  tracked record was deleted) and the finished run ended in a `FileNotFoundError`
  traceback instead of writing its record.
"""

import json
import shutil

import pytest

import main as main_module
from main import EXIT_OK

_ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout><Page ID="P1"><PrintSpace><TextBlock ID="B1"><TextLine ID="L1">
    <String ID="S1" CONTENT="Ahoj"/>
  </TextLine></TextBlock></PrintSpace></Page></Layout>
</alto>
"""

_AMCR = """<?xml version="1.0" encoding="UTF-8"?>
<amcr:amcr xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/"><amcr:nazev>Davle</amcr:nazev></amcr:amcr>
"""


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Isolated cwd (no config.txt) with one AMCR record and one ALTO file side by side."""
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "record.xml").write_text(_AMCR, encoding="utf-8")
    (docs / "scan.alto.xml").write_text(_ALTO, encoding="utf-8")
    (tmp_path / "fields.txt").write_text("//amcr:amcr/amcr:nazev\n", encoding="utf-8")
    return tmp_path


def _run(monkeypatch, *argv):
    processed = []

    def _fake_process_single_file(**kwargs):
        processed.append(kwargs["file_path"].name)
        return True, 0

    monkeypatch.setattr(main_module, "process_single_file", _fake_process_single_file)
    monkeypatch.setattr("sys.argv", ["main.py", *argv])
    return main_module.main(), processed


def test_metadata_run_leaves_alto_files_out(workdir, monkeypatch, capsys):
    code, processed = _run(
        monkeypatch, "docs", "--xpaths", "fields.txt", "--formats", "xml", "--source_lang", "cs", "-o", "out"
    )
    assert code == EXIT_OK
    assert processed == ["record.xml"]
    assert "Skipping 1 ALTO file(s)" in capsys.readouterr().out


def test_alto_run_still_picks_up_alto_files(workdir, monkeypatch):
    code, processed = _run(monkeypatch, "docs", "--alto", "--formats", "alto.xml", "--source_lang", "cs", "-o", "out")
    assert code == EXIT_OK
    assert processed == ["scan.alto.xml"]


def test_an_explicitly_named_alto_file_is_still_processed_in_metadata_mode(workdir, monkeypatch):
    """Only directory scans are filtered; a file the caller names is taken as given."""
    code, processed = _run(
        monkeypatch, "docs/scan.alto.xml", "--xpaths", "fields.txt", "--formats", "xml", "--source_lang", "cs"
    )
    assert code == EXIT_OK
    assert processed == ["scan.alto.xml"]


def test_run_record_is_written_even_if_its_folder_vanished_mid_run(workdir, monkeypatch):
    def _removes_the_paradata_folder(**kwargs):
        shutil.rmtree(workdir / "out" / "paradata")
        return True, 0

    monkeypatch.setattr(main_module, "process_single_file", _removes_the_paradata_folder)
    monkeypatch.setattr(
        "sys.argv", ["main.py", "docs", "--alto", "--formats", "alto.xml", "--source_lang", "cs", "-o", "out"]
    )
    assert main_module.main() == EXIT_OK

    records = list((workdir / "out" / "paradata").glob("*_translator.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["statistics"]["input_files_total"] == 1
