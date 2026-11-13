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
from pathlib import Path

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


def _shipped_formats(workdir):
    """A config.txt with the shipped `formats = alto.xml` (the ALTO sample's setting)."""
    (workdir / "config.txt").write_text("[DEFAULT]\nformats = alto.xml\n", encoding="utf-8")


def test_xpaths_on_the_command_line_is_a_metadata_run_despite_the_shipped_formats(workdir, monkeypatch, capsys):
    """(issue #46) AMČR's recipe names --xpaths and no --formats; config.txt's alto.xml must not turn it
    into an ALTO run (exit 2 on a folder of records, only the ALTO file of a mixed folder)."""
    _shipped_formats(workdir)
    code, processed = _run(monkeypatch, "docs", "--xpaths", "fields.txt", "--source_lang", "cs", "-o", "out")
    assert code == EXIT_OK
    assert processed == ["record.xml"]
    assert "metadata mode with formats 'xml,txt'" in capsys.readouterr().out


def test_the_shipped_formats_still_mean_alto_without_xpaths(workdir, monkeypatch):
    _shipped_formats(workdir)
    code, processed = _run(monkeypatch, "docs", "--source_lang", "cs", "-o", "out")
    assert code == EXIT_OK
    assert processed == ["scan.alto.xml"]


def test_an_explicit_formats_still_wins_over_xpaths(workdir, monkeypatch):
    _shipped_formats(workdir)
    code, processed = _run(
        monkeypatch, "docs", "--xpaths", "fields.txt", "--formats", "alto.xml", "--source_lang", "cs", "-o", "out"
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


def test_degenerate_replies_are_reported_once_per_document(workdir, monkeypatch, capsys):
    """One line and one paradata entry per document, instead of a warning per reply."""

    class _Backend:
        name = "lindat"
        vocabulary: dict = {}
        protected_count = 0

        def __init__(self):
            self.degenerate_replies = 0

        def reset_protected_count(self):
            pass

        def reset_degenerate_count(self):
            self.degenerate_replies = 0

    backend = _Backend()

    def _process(**kwargs):
        kwargs["translator"].reset_degenerate_count()
        kwargs["translator"].degenerate_replies = 7  # what the guard counted in this document
        return True, 0

    monkeypatch.setattr(main_module, "get_backend", lambda *a, **k: backend)
    monkeypatch.setattr(main_module, "process_single_file", _process)
    monkeypatch.setattr("sys.argv", ["main.py", "docs/scan.alto.xml", "--alto", "--source_lang", "cs", "-o", "out"])
    assert main_module.main() == EXIT_OK

    assert "LINDAT: 7 degenerate reply(ies) re-requested in scan.alto.xml" in capsys.readouterr().out
    record = json.loads(next((workdir / "out" / "paradata").glob("*_translator.json")).read_text(encoding="utf-8"))
    assert record["config"]["lindat_degenerate_replies"] == {"scan": 7}
    assert record["config"]["lindat_degenerate_replies_total"] == 7


# ──────────────────────────────────────────────────────────────────────────────
# The CSV log is swapped in when the document is done, never truncated up front
# ──────────────────────────────────────────────────────────────────────────────
#
# The log used to be opened with "w" when a document STARTED, so for the whole run
# (10-20 minutes on the ALTO sample — the rows are written only when the document is
# finished) it was an empty file next to the previous run's XML. Two such 0-byte logs
# were committed as samples before anyone noticed.


class _QuietBackend:
    name = "lindat"
    vocabulary: dict = {}
    protected_count = 0

    def reset_protected_count(self):
        pass


def _run_alto_with(monkeypatch, fake_process_alto_xml):
    monkeypatch.setattr(main_module, "get_backend", lambda *a, **k: _QuietBackend())
    monkeypatch.setattr(main_module, "process_alto_xml", fake_process_alto_xml)
    monkeypatch.setattr("sys.argv", ["main.py", "docs/scan.alto.xml", "--alto", "--source_lang", "cs", "-o", "out"])
    return main_module.main()


def _previous_log(workdir):
    log = workdir / "out" / "scan_log.csv"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("file,page_num,line_num,text_cs,text_en,status\nscan,1,L1,old,old,ok\n", encoding="utf-8")
    return log


def test_previous_log_stays_readable_until_the_new_one_is_complete(workdir, monkeypatch):
    log = _previous_log(workdir)
    seen_during_run = {}

    def _translate(input_path, output_path, translator, src, tgt, csv_writer, *args, **kwargs):
        seen_during_run["log"] = log.read_text(encoding="utf-8")
        seen_during_run["partial"] = (workdir / "out" / "scan_log.csv.partial").exists()
        csv_writer.writerow(["scan", 1, "L1", "Ahoj", "Hello", "ok"])

    assert _run_alto_with(monkeypatch, _translate) == EXIT_OK

    assert "old,old" in seen_during_run["log"], "the previous log must not be truncated while the run works"
    assert seen_during_run["partial"], "the new log is written beside it"
    rows = log.read_text(encoding="utf-8").splitlines()
    assert rows == ["file,page_num,line_num,text_cs,text_en,status", "scan,1,L1,Ahoj,Hello,ok"]
    assert not (workdir / "out" / "scan_log.csv.partial").exists()


def test_a_failed_translation_keeps_the_previous_log(workdir, monkeypatch):
    log = _previous_log(workdir)
    before = log.read_text(encoding="utf-8")

    def _fails(*args, **kwargs):
        raise RuntimeError("backend down")

    _run_alto_with(monkeypatch, _fails)

    assert log.read_text(encoding="utf-8") == before, "the log still describes the XML that is still there"
    assert not (workdir / "out" / "scan_log.csv.partial").exists()


# ──────────────────────────────────────────────────────────────────────────────
# The run's paradata list the backend's components
# ──────────────────────────────────────────────────────────────────────────────
#
# The backend's components used to be logged only after the FIRST file had finished, so the
# first record of every run carried no backend component. Since paradata 3.0
# (atrium-project#84) nothing is written into the record and no licence is computed: the run's
# paradata list each component with its own terms.


_PARA_CONFIG = Path(main_module.__file__).resolve().parent / "para_config.txt"


def _with_licence_table(workdir):
    """The component licences come from para_config.txt, read from the working directory."""
    shutil.copy(_PARA_CONFIG, workdir / "para_config.txt")


def _run_components(workdir):
    (path,) = list((workdir / "out" / "paradata").glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert not {"license", "license_url", "license_detail"} & set(record)
    return {component["name"]: component["license"] for component in record["components"]}


def _writes_the_output(input_path, output_path, translator, src, tgt, csv_writer, *args, **kwargs):
    output_path.write_text(_ALTO, encoding="utf-8")


def test_a_one_file_run_lists_the_backend_components(workdir, monkeypatch):
    """The pipeline case: one page per run, the source language given."""
    _with_licence_table(workdir)
    assert _run_alto_with(monkeypatch, _writes_the_output) == EXIT_OK

    components = _run_components(workdir)
    assert components["lindat_cubbitt"] == "CC BY-NC-SA 4.0"
    assert not (workdir / "out" / "scan.document.json").exists(), "no record without one sent"


def test_a_batch_lists_the_backend_components_once(workdir, monkeypatch):
    _with_licence_table(workdir)
    (workdir / "docs" / "second.alto.xml").write_text(_ALTO, encoding="utf-8")
    monkeypatch.setattr(main_module, "get_backend", lambda *a, **k: _QuietBackend())
    monkeypatch.setattr(main_module, "process_alto_xml", _writes_the_output)
    monkeypatch.setattr(
        "sys.argv", ["main.py", "docs", "--alto", "--formats", "alto.xml", "--source_lang", "cs", "-o", "out"]
    )
    assert main_module.main() == EXIT_OK

    assert _run_components(workdir)["lindat_cubbitt"] == "CC BY-NC-SA 4.0"
