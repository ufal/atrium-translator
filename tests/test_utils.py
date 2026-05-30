"""
tests/test_utils.py
===================
Unit tests for utils.py:
  - validate_xml_with_xsd
  - process_amcr_xml
  - process_alto_xml

Design notes
------------
* No ML models, no network, no GPU required.
* The translator and identifier are always replaced with MagicMock objects
  (provided by conftest.py fixtures) so the tests are purely structural.
* All file I/O uses pytest's ``tmp_path`` fixture so tests are hermetic.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from lxml import etree

from utils import validate_xml_with_xsd, process_amcr_xml, process_alto_xml

# ── constants shared across test classes ─────────────────────────────────────

AMCR_NS = "https://api.aiscr.cz/schema/amcr/2.2/"
ALTO_NS  = "http://www.loc.gov/standards/alto/ns-v2#"

# XPaths that match the sample_amcr.xml fixture
XPATH_POPIS  = "//amcr:amcr/amcr:dokument/amcr:popis"
XPATH_POZNAM = "//amcr:amcr/amcr:dokument/amcr:poznamka"

# Minimal XSD that accepts <root> with optional <child> elements
_SAMPLE_XSD = """\
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="root">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="child" type="xs:string"
                    minOccurs="0" maxOccurs="unbounded"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""


# ════════════════════════════════════════════════════════════════════════════
# validate_xml_with_xsd
# ════════════════════════════════════════════════════════════════════════════

class TestValidateXmlWithXsd:
    """validate_xml_with_xsd(xml_tree, xsd_url_or_path) → (bool, log)."""

    def _xsd_file(self, tmp_path: Path, content: str = _SAMPLE_XSD) -> Path:
        p = tmp_path / "schema.xsd"
        p.write_text(content, encoding="utf-8")
        return p

    def test_valid_xml_returns_true_and_empty_log(self, tmp_path):
        xsd = self._xsd_file(tmp_path)
        tree = etree.ElementTree(etree.fromstring("<root><child>ok</child></root>"))
        valid, log = validate_xml_with_xsd(tree, str(xsd))
        assert valid is True
        assert log == ""

    def test_xml_with_wrong_root_returns_false(self, tmp_path):
        """Element not declared in schema must fail validation."""
        xsd = self._xsd_file(tmp_path)
        tree = etree.ElementTree(etree.fromstring("<wrong_root/>"))
        valid, log = validate_xml_with_xsd(tree, str(xsd))
        assert valid is False
        assert log  # non-empty error information

    def test_missing_schema_file_returns_false_with_message(self, tmp_path):
        tree = etree.ElementTree(etree.fromstring("<root/>"))
        valid, log = validate_xml_with_xsd(tree, str(tmp_path / "no_such.xsd"))
        assert valid is False
        assert log  # contains the exception message

    def test_root_element_with_no_children_satisfies_min_occurs_zero(self, tmp_path):
        """<root/> alone is valid when minOccurs='0'."""
        xsd = self._xsd_file(tmp_path)
        tree = etree.ElementTree(etree.fromstring("<root/>"))
        valid, _ = validate_xml_with_xsd(tree, str(xsd))
        assert valid is True


# ════════════════════════════════════════════════════════════════════════════
# process_amcr_xml
# ════════════════════════════════════════════════════════════════════════════

class TestProcessAmcrXml:
    """
    Tests for process_amcr_xml(input_path, output_path, xpaths, translator,
                                src_lang, tgt_lang, xsd_url, csv_writer, identifier).
    """

    # ── translation ──────────────────────────────────────────────────────────

    def test_popis_element_text_is_replaced(self, amcr_xml_file, tmp_path, mock_translator):
        """<amcr:popis> text must be overwritten with the translator's return value."""
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator, "cs", "en")

        root = etree.parse(str(out)).getroot()
        popis_elems = root.findall(f".//{{{AMCR_NS}}}popis")
        assert popis_elems, "<amcr:popis> element missing from output"
        assert popis_elems[0].text.startswith("[TR:")

    def test_translator_called_once_per_matched_element(self, amcr_xml_file, tmp_path, mock_translator):
        """One translate() call is expected for each XPath that resolves to text."""
        out = tmp_path / "out.xml"
        process_amcr_xml(
            amcr_xml_file, out,
            [XPATH_POPIS, XPATH_POZNAM],
            mock_translator, "cs", "en",
        )
        assert mock_translator.translate.call_count == 2

    def test_original_text_is_passed_to_translator(self, amcr_xml_file, tmp_path, mock_translator):
        """translator.translate must receive the unmodified source text."""
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator, "cs", "en")

        call_args = mock_translator.translate.call_args
        source_text_arg = call_args[0][0]          # first positional argument
        assert "Stará Boleslav" in source_text_arg

    # ── CSV logging ───────────────────────────────────────────────────────────

    def test_csv_row_written_for_each_translated_element(
        self, amcr_xml_file, tmp_path, mock_translator, csv_sink
    ):
        writer, rows = csv_sink
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator, "cs", "en",
                         csv_writer=writer)
        assert len(rows) == 1

    def test_csv_row_columns_are_correct(self, amcr_xml_file, tmp_path, mock_translator, csv_sink):
        """CSV columns: [doc_name, '', xpath, original_text, translated_text]."""
        writer, rows = csv_sink
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator, "cs", "en",
                         csv_writer=writer)
        row = rows[0]
        assert row[1] == ""                                # page_num is empty for AMCR
        assert row[2] == XPATH_POPIS                      # xpath column
        assert "Stará Boleslav" in row[3]                 # original text
        assert row[4].startswith("[TR:")                  # translated text

    # ── language detection ────────────────────────────────────────────────────

    def test_auto_src_lang_invokes_identifier(
        self, amcr_xml_file, tmp_path, mock_translator, mock_identifier
    ):
        """With src_lang='auto', identifier.detect() must be called for non-empty text."""
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator,
                         "auto", "en", identifier=mock_identifier)
        mock_identifier.detect.assert_called()

    def test_auto_without_identifier_defaults_to_cs(self, amcr_xml_file, tmp_path, mock_translator):
        """src_lang='auto' with no identifier must fall back to 'cs'."""
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator,
                         "auto", "en", identifier=None)
        # translator must have been called with 'cs' as the detected language
        call_args = mock_translator.translate.call_args[0]
        assert call_args[1] == "cs"

    # ── edge cases ────────────────────────────────────────────────────────────

    def test_element_with_empty_text_is_not_translated(self, tmp_path, mock_translator):
        """Elements whose text is None or whitespace-only must be silently skipped."""
        xml_content = (
            f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            f'<GetRecord><record><metadata>'
            f'<amcr:amcr xmlns:amcr="{AMCR_NS}">'
            f'<amcr:dokument><amcr:popis></amcr:popis></amcr:dokument>'
            f'</amcr:amcr></metadata></record></GetRecord></OAI-PMH>'
        )
        src = tmp_path / "empty.xml"
        src.write_text(xml_content, encoding="utf-8")
        process_amcr_xml(src, tmp_path / "out.xml", [XPATH_POPIS], mock_translator, "cs", "en")
        mock_translator.translate.assert_not_called()

    def test_xpath_with_no_match_does_not_raise(self, amcr_xml_file, tmp_path, mock_translator):
        """An XPath that resolves to zero elements must not raise; output must still be created."""
        out = tmp_path / "out.xml"
        process_amcr_xml(
            amcr_xml_file, out,
            ["//amcr:amcr/amcr:nonexistent_field"],
            mock_translator, "cs", "en",
        )
        assert out.exists()
        mock_translator.translate.assert_not_called()

    def test_output_file_is_parseable_xml(self, amcr_xml_file, tmp_path, mock_translator):
        """The written output file must be well-formed XML."""
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS], mock_translator, "cs", "en")
        assert out.exists()
        tree = etree.parse(str(out))   # raises ParseError if malformed
        assert tree.getroot() is not None

    def test_multiple_xpaths_two_csv_rows(self, amcr_xml_file, tmp_path, mock_translator, csv_sink):
        """Processing two XPaths on the fixture should produce two CSV rows."""
        writer, rows = csv_sink
        out = tmp_path / "out.xml"
        process_amcr_xml(amcr_xml_file, out, [XPATH_POPIS, XPATH_POZNAM],
                         mock_translator, "cs", "en", csv_writer=writer)
        assert len(rows) == 2


# ════════════════════════════════════════════════════════════════════════════
# process_alto_xml
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# process_alto_xml
# ════════════════════════════════════════════════════════════════════════════

class TestProcessAltoXml:
    """
    Tests for process_alto_xml(input_path, output_path, translator,
                                src_lang, tgt_lang, csv_writer, identifier).
    """

    # ── translation ──────────────────────────────────────────────────────────

    def test_translator_called_once_per_text_block(self, alto_xml_file, tmp_path, mock_translator):
        """
        sample.alto.xml has 1 TextBlock containing 2 TextLines.
        Due to the Dual-Pass architecture, it will call translate():
        1 time for the block + 2 times for the lines = 3 calls total.
        """
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "cs", "en")

        # 1 block call + 2 line calls = 3 total translations
        assert mock_translator.translate.call_count == 3

    def test_concatenated_block_text_passed_to_translator(self, alto_xml_file, tmp_path, mock_translator):
        """CONTENT attrs of all Strings in the TextBlock must be joined into a single string."""
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "cs", "en")

        # First call argument: concatenated CONTENT of the whole block (both lines)
        first_call_text = mock_translator.translate.call_args_list[0][0][0]
        assert "Dobrý" in first_call_text
        assert "den" in first_call_text

    def test_translated_words_written_back_to_string_content(self, tmp_path):
        """Words from translated text must be distributed across <String CONTENT=...> sequentially."""
        alto_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<alto xmlns="{ALTO_NS}"><Layout><Page ID="P1" WIDTH="100" HEIGHT="100">'
            f'<PrintSpace><TextBlock ID="TB1"><TextLine ID="L1">'
            f'<String ID="S1" CONTENT="a" />'
            f'<String ID="S2" CONTENT="b" />'
            f'<String ID="S3" CONTENT="c" />'
            f'</TextLine></TextBlock></PrintSpace></Page></Layout></alto>'
        )
        src = tmp_path / "three.xml"
        dst = tmp_path / "three_out.xml"
        src.write_text(alto_xml, encoding="utf-8")

        t = MagicMock()
        t.translate.return_value = "alpha beta gamma"
        process_alto_xml(src, dst, t, "cs", "en")

        root = etree.parse(str(dst)).getroot()
        contents = [s.get("CONTENT") for s in root.findall(f".//{{{ALTO_NS}}}String")]
        assert contents == ["alpha", "beta", "gamma"]

    def test_greedy_token_redistribution_crams_remainder_in_last_element(self, tmp_path):
        """
        If 3 translated words are distributed across 2 Strings:
        first String gets 1 word, second (last) gets the remainder (2 words).
        """
        alto_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<alto xmlns="{ALTO_NS}"><Layout><Page ID="P1" WIDTH="100" HEIGHT="100">'
            f'<PrintSpace><TextBlock ID="TB1"><TextLine ID="L1">'
            f'<String ID="S1" CONTENT="x" />'
            f'<String ID="S2" CONTENT="y" />'
            f'</TextLine></TextBlock></PrintSpace></Page></Layout></alto>'
        )
        src = tmp_path / "two.xml"
        dst = tmp_path / "two_out.xml"
        src.write_text(alto_xml, encoding="utf-8")

        t = MagicMock()
        t.translate.return_value = "one two three"
        process_alto_xml(src, dst, t, "cs", "en")

        root = etree.parse(str(dst)).getroot()
        contents = [s.get("CONTENT") for s in root.findall(f".//{{{ALTO_NS}}}String")]
        # S1 gets "one", S2 gets "two three"
        assert contents == ["one", "two three"]

    def test_empty_string_elements_when_translation_is_shorter(self, tmp_path):
        """If there are more Strings than translated words, remaining Strings become empty."""
        alto_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<alto xmlns="{ALTO_NS}"><Layout><Page ID="P1" WIDTH="100" HEIGHT="100">'
            f'<PrintSpace><TextBlock ID="TB1"><TextLine ID="L1">'
            f'<String ID="S1" CONTENT="x" />'
            f'<String ID="S2" CONTENT="y" />'
            f'<String ID="S3" CONTENT="z" />'
            f'</TextLine></TextBlock></PrintSpace></Page></Layout></alto>'
        )
        src = tmp_path / "three.xml"
        dst = tmp_path / "three_out.xml"
        src.write_text(alto_xml, encoding="utf-8")

        t = MagicMock()
        t.translate.return_value = "one"
        process_alto_xml(src, dst, t, "cs", "en")

        root = etree.parse(str(dst)).getroot()
        contents = [s.get("CONTENT") for s in root.findall(f".//{{{ALTO_NS}}}String")]
        # S1 gets "one", S2 gets "", S3 gets ""
        assert contents == ["one", "", ""]

    # ── CSV logging ───────────────────────────────────────────────────────────

    def test_csv_row_written_per_text_line(self, alto_xml_file, tmp_path, mock_translator, csv_sink):
        """sample.alto.xml has 2 TextLines → 2 CSV rows."""
        writer, rows = csv_sink
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "cs", "en", csv_writer=writer)
        assert len(rows) == 2

    def test_csv_row_contains_line_id_in_correct_column(
        self, alto_xml_file, tmp_path, mock_translator, csv_sink
    ):
        """CSV column index 2 must contain the TextLine ID attribute value."""
        writer, rows = csv_sink
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "cs", "en", csv_writer=writer)
        line_ids = [row[2] for row in rows]
        assert "L1" in line_ids
        assert "L2" in line_ids

    def test_csv_row_contains_page_number(self, alto_xml_file, tmp_path, mock_translator, csv_sink):
        """CSV column index 1 must be the page index (1 for the first and only page)."""
        writer, rows = csv_sink
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "cs", "en", csv_writer=writer)
        assert all(row[1] == 1 for row in rows)

    # ── language detection ────────────────────────────────────────────────────

    def test_auto_src_lang_invokes_identifier_per_block(
        self, alto_xml_file, tmp_path, mock_translator, mock_identifier
    ):
        """With src_lang='auto', identifier.detect() must be called for each TextBlock."""
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "auto", "en",
                         identifier=mock_identifier)
        assert mock_identifier.detect.call_count == 1  # one call per TextBlock

    # ── edge case ─────────────────────────────────────────────────────────────

    def test_output_file_is_parseable_xml(self, alto_xml_file, tmp_path, mock_translator):
        """The written output file must be well-formed XML."""
        out = tmp_path / "out.xml"
        process_alto_xml(alto_xml_file, out, mock_translator, "cs", "en")
        assert out.exists()
        tree = etree.parse(str(out))
        assert tree.getroot() is not None