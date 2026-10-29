"""
tests/test_cli_service_parity.py — the command line and `/translate` translate alike (atrium-project#72).

The same ALTO page and the same AMČR record go through `main.main()` and through `/translate`, with one
stub backend on both sides: the translated XML, the log rows and the record's `translations` block must be
equal. The other checks pin what v1.6.0-beta aligned:

* the kind of a file is decided by its root element (`utils.xml_kind`) on both sides when the caller does
  not say: no `--alto`/`--xpaths`/`--formats`, no `is_alto`; a mode the caller names is kept;
* the service's opt-in fields `fast_align`, `include_log`, `include_record`, `xsd_strict` (with `XSD_PATH`)
  and its `document_json_schema_error`;
* the command line's `-o <file>.xml`, `.txt` URL lists inside a directory, and one record per document in
  a batch.

v1.7.0-beta: the service's paradata records the run's outcome keys as the command line's does
(`main.run_outcome_config`), and a query boolean that cannot be read is refused (422) as the same form
value is, instead of meaning its default.

What stays apart by design (directories and URL lists on the command line only; backend, vocabulary and
XPaths set per deployment in the service): docs/usage.md "The command line and the service".
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree

import main as main_module
from main import EXIT_FAILED, EXIT_OK, EXIT_USAGE, translated_filename
from service import api as api_module
from utils import load_xsd

client = TestClient(api_module.app)

_ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout><Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="1000" HEIGHT="1000"><PrintSpace>
    <TextBlock ID="B1">
      <TextLine ID="L1"><String ID="S1" CONTENT="Výzkum"/><String ID="S2" CONTENT="hradiště"/></TextLine>
      <TextLine ID="L2"><String ID="S3" CONTENT="u"/><String ID="S4" CONTENT="Davle"/></TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>
"""

_AMCR = """<?xml version="1.0" encoding="UTF-8"?>
<amcr:amcr xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/"><amcr:nazev>Výzkum hradiště u Davle</amcr:nazev></amcr:amcr>
"""

_XPATHS = "//amcr:amcr/amcr:nazev\n"

#: A schema the AMČR fixture and its translation satisfy, and one they do not.
_XSD_OK = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="https://api.aiscr.cz/schema/amcr/2.2/"
           xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/" elementFormDefault="qualified">
  <xs:element name="amcr"><xs:complexType><xs:sequence>
    <xs:element name="nazev" type="xs:string"/>
  </xs:sequence></xs:complexType></xs:element>
</xs:schema>
"""
_XSD_STRICTER = _XSD_OK.replace(
    '<xs:element name="nazev" type="xs:string"/>', '<xs:element name="ident_cely" type="xs:string"/>'
)


class StubBackend:
    """A deterministic backend: each line comes back prefixed, so the alignment keeps the line count."""

    name = "stub"
    supports_glossary = False
    vocabulary: dict = {}
    protected_count = 0

    def translate(self, text, src_lang, tgt_lang="en"):
        return "\n".join(f"EN {line}" if line.strip() else line for line in str(text).split("\n"))

    def supported_languages(self):
        return ["cs", "en"]

    def reset_protected_count(self):
        self.protected_count = 0

    def license_components(self, vocab_loaded):
        return []

    def describe(self):
        return {"stub_model": "echo"}


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """The same stub backend for main() and /translate, and an isolated working directory."""
    backend = StubBackend()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "get_backend", lambda *a, **k: backend)
    monkeypatch.setattr(
        api_module,
        "models",
        {"translator": backend, "identifier": None, "xpaths_list": [_XPATHS.strip()], "xsd_schema": None},
    )
    (tmp_path / "fields.txt").write_text(_XPATHS, encoding="utf-8")
    return backend


def _cli(monkeypatch, *argv) -> int:
    monkeypatch.setattr("sys.argv", ["main.py", *argv])
    return main_module.main()


def _canonical(xml_bytes: bytes) -> bytes:
    return etree.tostring(etree.fromstring(xml_bytes), method="c14n")


def _rows(csv_text: str) -> list:
    return list(csv.reader(io.StringIO(csv_text)))


def _translations(record: dict) -> dict:
    """The record's `translations` block without what names the run (ids, times, the paradata)."""
    block = json.loads(json.dumps(record.get("translations") or {}))

    def strip(node):
        if isinstance(node, dict):
            return {
                key: strip(value)
                for key, value in node.items()
                if not any(word in key for word in ("run", "paradata", "time", "generated", "created"))
            }
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    return strip(block)


def _service(name: str, body: str, **data) -> dict:
    form = {"source_lang": "cs", "response_format": "json", "include_log": "true", "include_record": "true"}
    form.update({key: str(value).lower() for key, value in data.items()})
    response = client.post("/translate", files={"file": (name, body.encode("utf-8"), "application/xml")}, data=form)
    assert response.status_code == 200, response.text[:600]
    return response.json()


@pytest.mark.parametrize(
    ("name", "body", "cli_flags"),
    [
        ("CTX000000003-1.alto.xml", _ALTO, []),  # no mode flag: the root element decides
        ("C-TX-201750164.xml", _AMCR, ["--xpaths", "fields.txt"]),
    ],
)
def test_the_same_input_gives_the_same_xml_log_and_record(stub, monkeypatch, tmp_path, name, body, cli_flags):
    source = tmp_path / "in" / name
    source.parent.mkdir()
    source.write_text(body, encoding="utf-8")
    code = _cli(
        monkeypatch, str(source), "--source_lang", "cs", "--backend", "stub", "-o", str(tmp_path / "out"), *cli_flags
    )
    assert code == EXIT_OK

    out = tmp_path / "out"
    cli_xml = (out / translated_filename(name, "en")).read_bytes()
    cli_log = next(out.glob("*_log.csv")).read_text(encoding="utf-8")
    cli_record = json.loads(next(out.glob("*.document.json")).read_text(encoding="utf-8"))

    answer = _service(name, body)
    assert answer["filename"] == translated_filename(name, "en")
    assert _canonical(answer["content"].encode("utf-8")) == _canonical(cli_xml)
    assert answer["log_filename"] == next(out.glob("*_log.csv")).name
    assert _rows(answer["log_csv"]) == _rows(cli_log)
    assert answer["document_json"]["doc_id"] == cli_record["doc_id"]
    assert _translations(answer["document_json"]) == _translations(cli_record)
    assert "EN" in answer["content"]


def test_both_sides_detect_the_kind_from_the_root_element(stub, monkeypatch, tmp_path):
    """No mode given: ALTO by its root, a record by any other root, on both sides."""
    assert _service("record.xml", _AMCR)["type"] == "metadata"  # was ALTO by default, and untranslated
    assert _service("scan.xml", _ALTO)["type"] == "alto"  # the name does not decide

    seen = {}

    def fake(**kwargs):
        seen[kwargs["file_path"].name] = kwargs["args"].alto
        return True, 0

    monkeypatch.setattr(main_module, "process_single_file", fake)
    # No mode flag: config.txt's `fields` supplies the XPath targets a record needs, as the shipped one does.
    (tmp_path / "config.txt").write_text("[DEFAULT]\nformats = xml\nfields = fields.txt\n", encoding="utf-8")
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "record.xml").write_text(_AMCR, encoding="utf-8")
    (folder / "scan.xml").write_text(_ALTO, encoding="utf-8")
    assert _cli(monkeypatch, str(folder), "--source_lang", "cs", "-o", str(tmp_path / "out")) == EXIT_OK
    assert seen == {"record.xml": False, "scan.xml": True}


def test_a_mode_the_caller_names_is_kept(stub, monkeypatch, tmp_path, capsys):
    assert _service("scan.alto.xml", _ALTO, is_alto=False)["type"] == "metadata"

    seen = {}
    monkeypatch.setattr(
        main_module, "process_single_file", lambda **kw: (seen.setdefault("alto", kw["args"].alto), (True, 0))[1]
    )
    record = tmp_path / "record.xml"
    record.write_text(_AMCR, encoding="utf-8")
    assert _cli(monkeypatch, str(record), "--alto", "--formats", "xml", "-o", str(tmp_path / "out")) == EXIT_OK
    assert seen["alto"] is True
    assert "its root element makes it metadata" in capsys.readouterr().out


def test_an_upload_that_is_not_xml_is_refused_when_its_kind_is_asked(stub):
    response = client.post("/translate", files={"file": ("broken.xml", b"<amcr><nazev>", "application/xml")})
    assert response.status_code == 422
    assert response.json().get("cause") == "malformed_xml"


def test_the_record_comes_back_only_when_sent_or_asked_for(stub):
    form = {"source_lang": "cs", "response_format": "json"}
    files = {"file": ("CTX000000003-1.alto.xml", _ALTO.encode("utf-8"), "application/xml")}
    assert "document_json" not in client.post("/translate", files=files, data=form).json()
    asked = client.post("/translate", files=files, data={**form, "include_record": "true"}).json()
    assert asked["document_json"]["doc_id"] == "CTX000000003-1"


def test_include_log_makes_a_multipart_answer_without_a_record(stub):
    response = client.post(
        "/translate",
        files={"file": ("CTX000000003-1.alto.xml", _ALTO.encode("utf-8"), "application/xml")},
        data={"source_lang": "cs", "include_log": "true"},
    )
    assert response.status_code == 200
    assert "multipart/mixed" in response.headers["content-type"]
    assert b'filename="CTX000000003-1_log.csv"' in response.content
    assert b".document.json" not in response.content


def test_fast_align_reaches_the_alto_path(stub, monkeypatch):
    seen = {}

    def fake(**kwargs):
        seen["fast_align"] = kwargs["args"].fast_align
        Path(kwargs["output_file"]).write_text(_ALTO, encoding="utf-8")
        return True, 0

    monkeypatch.setattr(api_module, "process_single_file", fake)
    _service("scan.alto.xml", _ALTO, fast_align=True, include_log=False, include_record=False)
    assert seen["fast_align"] is True


@pytest.mark.parametrize(
    ("schema", "strict", "status", "valid"),
    [
        (_XSD_OK, False, 200, True),
        (_XSD_STRICTER, False, 200, False),
        (_XSD_STRICTER, True, 422, None),
    ],
)
def test_xsd_validation_with_a_deployment_schema(stub, tmp_path, schema, strict, status, valid):
    xsd = tmp_path / "amcr.xsd"
    xsd.write_text(schema, encoding="utf-8")
    api_module.models["xsd_schema"] = load_xsd(str(xsd))
    api_module.models["xsd_info"] = {"file": "amcr.xsd", "sha256": "x", "strict_default": False}
    response = client.post(
        "/translate",
        files={"file": ("record.xml", _AMCR.encode("utf-8"), "application/xml")},
        data={"source_lang": "cs", "response_format": "json", "xsd_strict": str(strict).lower()},
    )
    assert response.status_code == status, response.text[:400]
    if status == 200:
        assert response.json()["xsd_valid"] is valid
        assert response.headers["X-Atrium-Xsd-Valid"] == str(valid).lower()
        recorded = response.json()["paradata"]["paradataRecord"]["config"]["xsd_validation"]
        assert recorded == {"valid": int(valid), "invalid": [] if valid else ["record"], "strict": False}
    else:
        assert response.json().get("cause") == "xsd_validation_failed"


@pytest.mark.parametrize("field", ["is_alto", "fast_align", "include_record"])
def test_a_query_boolean_that_cannot_be_read_is_refused(stub, field):
    response = client.post(
        f"/translate?{field}=ture",
        files={"file": ("record.xml", _AMCR.encode("utf-8"), "application/xml")},
        data={"source_lang": "cs"},
    )
    assert response.status_code == 422
    assert field in response.text


def test_an_inherited_invalid_record_is_reported(stub):
    baseline = {"schema_version": "1.0", "record_type": "atrium-document", "doc_id": "CTX000000003", "pages": "x"}
    response = client.post(
        "/translate",
        files={
            "file": ("CTX000000003-1.alto.xml", _ALTO.encode("utf-8"), "application/xml"),
            "document_json": ("CTX000000003.document.json", json.dumps(baseline).encode(), "application/json"),
        },
        data={"source_lang": "cs", "response_format": "json"},
    )
    assert response.status_code == 200, response.text[:400]
    assert response.json().get("document_json_schema_error")


def test_dash_o_with_one_file_names_the_translated_file(stub, monkeypatch, tmp_path):
    source = tmp_path / "CTX000000003-1.alto.xml"
    source.write_text(_ALTO, encoding="utf-8")
    target = tmp_path / "result" / "page.xml"
    target.parent.mkdir()
    assert _cli(monkeypatch, str(source), "--source_lang", "cs", "-o", str(target)) == EXIT_OK
    assert target.is_file()  # it used to become a directory named page.xml
    assert b'CONTENT="EN"' in target.read_bytes()


def test_a_txt_in_a_folder_is_a_url_list_not_xml(stub, monkeypatch, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "urls.txt").write_text("https://api.aiscr.cz/id/C-TX-201750164\n", encoding="utf-8")
    downloaded = tmp_path / "C-TX-201750164.xml"
    downloaded.write_text(_AMCR, encoding="utf-8")
    monkeypatch.setattr(main_module, "fetch_xml_from_url", lambda url, download_dir: downloaded)
    seen = []
    monkeypatch.setattr(
        main_module, "process_single_file", lambda **kw: (seen.append(kw["file_path"].name), (True, 0))[1]
    )
    code = _cli(monkeypatch, str(folder), "--xpaths", "fields.txt", "--formats", "xml,txt", "-o", str(tmp_path / "out"))
    assert code == EXIT_OK
    assert seen == ["C-TX-201750164.xml"]


def test_a_batch_writes_one_record_per_document(stub, monkeypatch, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "A-1.alto.xml").write_text(_ALTO, encoding="utf-8")
    (folder / "B-1.alto.xml").write_text(_ALTO, encoding="utf-8")
    records = tmp_path / "records"
    code = _cli(
        monkeypatch,
        str(folder),
        "--alto",
        "--formats",
        "alto.xml",
        "--source_lang",
        "cs",
        "-o",
        str(tmp_path / "out"),
        "--document-json-out",
        str(records),
    )
    assert code == EXIT_OK
    assert sorted(p.name for p in records.glob("*.document.json")) == ["A-1.document.json", "B-1.document.json"]


@pytest.mark.parametrize("flag", ["--document-json", "--document-json-out"])
def test_one_record_file_for_a_batch_is_refused(stub, monkeypatch, tmp_path, flag):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "A-1.alto.xml").write_text(_ALTO, encoding="utf-8")
    record = tmp_path / "one.document.json"
    record.write_text("{}", encoding="utf-8")
    code = _cli(
        monkeypatch, str(folder), "--alto", "--formats", "alto.xml", "-o", str(tmp_path / "out"), flag, str(record)
    )
    assert code == EXIT_USAGE


def test_a_record_without_targets_fails_only_that_file(stub, monkeypatch, tmp_path):
    record = tmp_path / "record.xml"
    record.write_text(_AMCR, encoding="utf-8")
    assert _cli(monkeypatch, str(record), "--source_lang", "cs", "-o", str(tmp_path / "out")) == EXIT_FAILED


def test_both_entry_points_record_the_same_run_keys(stub, monkeypatch):
    args = main_module.argparse.Namespace(
        input_path=Path("a.xml"),
        output=Path("out"),
        source_lang="cs",
        target_lang="en",
        formats="xml",
        alto=False,
        mode_explicit=False,
        backend="lindat",
        xpaths=None,
        xsd=None,
        vocabulary=None,
        output_mode="replace",
    )
    import configparser

    cli_keys = set(main_module._build_paradata_config(args, configparser.ConfigParser()))
    service_keys = set(
        main_module.build_run_config(
            backend_name="lindat",
            translator=stub,
            source_lang="cs",
            target_lang="en",
            mode="detect",
            output_mode="replace",
            allowed_languages=frozenset({"cs", "en"}),
        )
    )
    assert cli_keys == service_keys
