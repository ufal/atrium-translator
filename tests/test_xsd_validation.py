"""tests/test_xsd_validation.py — `--xsd` against a real-world record schema (issue #46).

Two defects kept `--xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd` from ever
answering the #46 `maxOccurs` question:

* **The schema could not be loaded.** AMCR 2.2 imports the XML namespace's schema from
  `http://www.w3.org/2001/03/xml.xsd` (for `xml:lang`). lxml 6 bundles libxml2 2.14,
  which has no HTTP client, so the import failed and `main()` aborted with "XSD schema
  load failed" in every environment. Imports are now resolved in Python.
* **The envelope was validated, not the record.** Harvested AMCR records arrive inside
  an OAI-PMH envelope; the schema declares `amcr` as its root, so every file — even an
  untouched source — failed at the root. The record inside is validated now.

With both fixed, the published schema accepts the shipped records as source and as
`replace` output and rejects them as `append` output (`xml:lang` is not declared on the
free-text fields, and the repeated element is not allowed there) — which is why append
mode on AMCR records now warns. Everything here is hermetic: small local schemas, no
network.
"""

import csv
import io
import logging

import pytest
from lxml import etree

import utils
from utils import OUTPUT_MODE_APPEND, OUTPUT_MODE_REPLACE, load_xsd, process_metadata_xml, validate_xml_with_xsd

_RECORD_SCHEMA = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:r="urn:rec" targetNamespace="urn:rec"
           elementFormDefault="qualified">
  <xs:import namespace="http://www.w3.org/XML/1998/namespace" schemaLocation="http://www.w3.org/2001/03/xml.xsd"/>
  <xs:element name="rec">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="title" type="xs:string"/>
        <xs:element name="term" minOccurs="0">
          <xs:complexType>
            <xs:simpleContent>
              <xs:extension base="xs:string">
                <xs:attribute ref="xml:lang" use="required"/>
              </xs:extension>
            </xs:simpleContent>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

_OAI = "http://www.openarchives.org/OAI/2.0/"


def _in_envelope(record: str) -> etree._ElementTree:
    return etree.ElementTree(
        etree.fromstring(
            f'<OAI-PMH xmlns="{_OAI}"><GetRecord><record><header/><metadata>{record}</metadata></record>'
            "</GetRecord></OAI-PMH>"
        )
    )


_VALID = '<rec xmlns="urn:rec"><title>Davle</title><term xml:lang="cs">kostel</term></rec>'
_APPENDED = (
    '<rec xmlns="urn:rec"><title xml:lang="cs">Davle</title><title xml:lang="en">Davle</title>'
    '<term xml:lang="cs">kostel</term></rec>'
)


@pytest.fixture
def no_network(monkeypatch):
    def _refuse(url):
        raise AssertionError(f"unexpected network fetch: {url}")

    monkeypatch.setattr(utils, "_fetch", _refuse)


@pytest.fixture
def schema(tmp_path, no_network):
    path = tmp_path / "rec.xsd"
    path.write_text(_RECORD_SCHEMA, encoding="utf-8")
    return load_xsd(str(path))


# ── loading ──────────────────────────────────────────────────────────────────


def test_a_schema_importing_the_xml_namespace_over_http_compiles_offline(schema):
    """The import that made AMCR 2.2 unloadable is served locally, without a fetch."""
    assert isinstance(schema, etree.XMLSchema)


def test_other_http_imports_are_fetched_through_urllib(tmp_path, monkeypatch):
    fetched = []
    other = b"""<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:other">
                  <xs:element name="note" type="xs:string"/></xs:schema>"""

    def _fetch(url):
        fetched.append(url)
        return other

    monkeypatch.setattr(utils, "_fetch", _fetch)
    path = tmp_path / "main.xsd"
    path.write_text(
        """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main">
             <xs:import namespace="urn:other" schemaLocation="https://example.org/other.xsd"/>
             <xs:element name="main" type="xs:string"/></xs:schema>""",
        encoding="utf-8",
    )
    load_xsd(str(path))
    assert fetched == ["https://example.org/other.xsd"]


# ── what gets validated ──────────────────────────────────────────────────────


def test_a_record_inside_an_oai_pmh_envelope_is_validated_not_the_envelope(schema):
    valid, log = validate_xml_with_xsd(_in_envelope(_VALID), schema)
    assert valid, log


def test_an_appended_record_fails_on_both_halves_of_the_pair(schema):
    valid, log = validate_xml_with_xsd(_in_envelope(_APPENDED), schema)
    assert not valid
    assert "lang" in log and "is not allowed" in log, "xml:lang on an element that does not declare it"
    assert "This element is not expected" in log, "the repeated element"


def test_a_bare_document_is_validated_as_before(schema):
    assert validate_xml_with_xsd(etree.ElementTree(etree.fromstring(_VALID)), schema)[0]
    assert not validate_xml_with_xsd(etree.ElementTree(etree.fromstring(_APPENDED)), schema)[0]


# ── append mode on AMCR records warns ────────────────────────────────────────

_AMCR = """<?xml version="1.0" encoding="utf-8"?>
<amcr:amcr xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/"><amcr:nazev>Davle</amcr:nazev></amcr:amcr>
"""
_OTHER = """<?xml version="1.0" encoding="utf-8"?>
<doc><nazev>Davle</nazev></doc>
"""


class _Translator:
    name = "lindat"
    vocabulary: dict = {}
    protected_count = 0

    def reset_protected_count(self):
        pass

    def translate(self, text, src_lang, tgt_lang="en"):
        return "\n".join(f"EN {line}" for line in text.split("\n"))


def _run(tmp_path, document, xpath, mode):
    src = tmp_path / "rec.xml"
    src.write_text(document, encoding="utf-8")
    process_metadata_xml(
        src,
        tmp_path / "rec_en.xml",
        [xpath],
        _Translator(),
        "cs",
        "en",
        csv_writer=csv.writer(io.StringIO()),
        output_mode=mode,
    )


@pytest.fixture
def fresh_latch(monkeypatch):
    monkeypatch.setattr(utils, "_AMCR_APPEND_WARNED", False)


def test_append_on_an_amcr_record_warns_once(tmp_path, fresh_latch, caplog):
    with caplog.at_level(logging.WARNING, logger="utils"):
        _run(tmp_path, _AMCR, "//amcr:amcr/amcr:nazev", OUTPUT_MODE_APPEND)
        _run(tmp_path, _AMCR, "//amcr:amcr/amcr:nazev", OUTPUT_MODE_APPEND)
    warnings = [r for r in caplog.records if "does NOT validate against the AMCR 2.2 schema" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    ("document", "xpath", "mode"),
    [(_AMCR, "//amcr:amcr/amcr:nazev", OUTPUT_MODE_REPLACE), (_OTHER, "//nazev", OUTPUT_MODE_APPEND)],
    ids=["amcr-replace", "non-amcr-append"],
)
def test_no_warning_for_replace_or_for_other_documents(tmp_path, fresh_latch, caplog, document, xpath, mode):
    with caplog.at_level(logging.WARNING, logger="utils"):
        _run(tmp_path, document, xpath, mode)
    assert "AMCR 2.2 schema" not in caplog.text
