from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atrium_test_support import REPO_ROOT, FakeTranslator


@pytest.fixture(autouse=True)
def disable_sleep():
    """Globally disable time.sleep() for all tests to prevent slow backoffs."""
    # Note: If your http_retry.py uses `from time import sleep`,
    # you may need to patch 'processors.http_retry.sleep' instead.
    with patch("time.sleep", return_value=None):
        yield


def _build_translator(prefix: str, suffix: str = "") -> MagicMock:
    mock = MagicMock(name=f"translator[{prefix}]")

    def render(text: str, *args, **kwargs) -> str:
        return f"{prefix}{text}{suffix}"

    mock.side_effect = render
    mock.translate.side_effect = render
    mock.translate_text.side_effect = render
    mock.translate_line.side_effect = render
    return mock


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture()
def fake_translator() -> FakeTranslator:
    return FakeTranslator(prefix="EN:")


@pytest.fixture()
def mock_translator() -> MagicMock:
    return _build_translator("[TR:", "]")


@pytest.fixture()
def mock_identifier() -> MagicMock:
    identifier = MagicMock(name="identifier")
    identifier.detect.return_value = ("cs", 0.99)
    # What the pipeline actually calls (processors/language.py): ranked candidates.
    identifier.candidates.return_value = [("cs", 0.99)]
    return identifier


@pytest.fixture()
def minimal_xml_file(tmp_path: Path) -> Path:
    path = tmp_path / "input.xml"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<root xmlns:tei="http://www.tei-c.org/ns/1.0">
  <title>Stará Boleslav</title>
  <body>
    <p type="abstract">Archaeological note.</p>
  </body>
</root>
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def minimal_alto_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.alto.xml"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout>
    <Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="100" HEIGHT="100">
      <PrintSpace>
        <TextBlock ID="TB1">
          <TextLine ID="L1">
            <String ID="S1" CONTENT="Ahoj"/>
            <String ID="S2" CONTENT="svete"/>
          </TextLine>
          <TextLine ID="L2">
            <String ID="S3" CONTENT="Druha"/>
            <String ID="S4" CONTENT="radka"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def amcr_xml_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample_amcr.xml"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/">
  <responseDate>2026-07-26T00:00:00Z</responseDate>
  <request>https://example.invalid/oai</request>
  <GetRecord>
    <record>
      <header>
        <identifier>oai:example:1</identifier>
        <datestamp>2026-07-26</datestamp>
      </header>
      <metadata>
        <amcr:amcr>
          <amcr:dokument>
            <amcr:popis>Stará Boleslav byla důležitým centrem.</amcr:popis>
            <amcr:poznamka>Poznámka k nálezu.</amcr:poznamka>
          </amcr:dokument>
        </amcr:amcr>
      </metadata>
    </record>
  </GetRecord>
</OAI-PMH>
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def alto_xml_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.alto.xml"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout>
    <Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="1000" HEIGHT="1000">
      <PrintSpace>
        <TextBlock ID="TB1">
          <TextLine ID="L1">
            <String ID="S1" CONTENT="Dobrý"/>
            <String ID="S2" CONTENT="den"/>
          </TextLine>
          <TextLine ID="L2">
            <String ID="S3" CONTENT="Archeologický"/>
            <String ID="S4" CONTENT="výzkum"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def sample_vocab_csv(tmp_path: Path) -> Path:
    path = tmp_path / "vocab.csv"
    path.write_text(
        "source_lemma,target_translation\n"
        "kostel,church\n"
        "pohřebiště,burial ground\n"
        "fotografie události,photograph of event\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def sample_paradata_files(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        """{
  "schema_version": "2.0",
  "program": "translator",
  "tool_version": "v0.5.0",
  "license": "CC BY 4.0",
  "license_detail": {
    "effective_license": "CC BY 4.0",
    "components": [
      {"name": "fasttext", "license": "CC BY 4.0"}
    ]
  },
  "statistics": {
    "input_files_total": 1,
    "successfully_processed": 1,
    "skipped_files": 0,
    "output_counts_by_type": {"xml": 1, "csv": 1},
    "performance_per_minute": {"xml": 10.0, "csv": 10.0}
  },
  "skipped_files_detail": []
}
""",
        encoding="utf-8",
    )
    second.write_text(
        """{
  "schema_version": "2.0",
  "program": "translator",
  "tool_version": "v0.5.0",
  "license": "CC BY-NC 4.0",
  "license_detail": {
    "effective_license": "CC BY-NC 4.0",
    "components": [
      {"name": "lindat_cubbitt", "license": "CC BY-NC 4.0"}
    ]
  },
  "statistics": {
    "input_files_total": 1,
    "successfully_processed": 1,
    "skipped_files": 0,
    "output_counts_by_type": {"xml": 1, "csv": 1},
    "performance_per_minute": {"xml": 10.0, "csv": 10.0}
  },
  "skipped_files_detail": []
}
""",
        encoding="utf-8",
    )
    return first, second


class _CsvSink:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []

    def writerow(self, row) -> None:
        self.rows.append(list(row))

    def writerows(self, rows) -> None:
        for row in rows:
            self.writerow(row)


@pytest.fixture()
def csv_sink() -> tuple[_CsvSink, list[list[str]]]:
    sink = _CsvSink()
    return sink, sink.rows
