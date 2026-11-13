from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import main


class FakeParadataLogger:
    """Small test double for main.main orchestration tests."""

    instances: list["FakeParadataLogger"] = []

    def __init__(
        self,
        program: str,
        config: dict,
        paradata_dir: str,
        output_types=None,
        **_kwargs,
    ) -> None:
        self.program = program
        self.config = config
        self.paradata_dir = paradata_dir
        self.output_types = output_types or []
        self._run_id = "test-run"
        self.run_id = "test-run"  # mirrors ParadataLogger's public accessors
        self.run_uuid = "urn:uuid:00000000-0000-4000-8000-000000000000"
        self.paradata_ref = str(Path(paradata_dir) / f"{self.run_id}_{program}.json")
        self.successes: list[str] = []
        self.components: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        self.finalized_with: int | None = None
        self.limits_applied: list[dict] = []
        self.inputs: list[str] = []
        self.outputs: list[str] = []
        self._parameters: dict = {}
        self.parameters = self._parameters
        self.error: str | None = None
        Path(paradata_dir).mkdir(parents=True, exist_ok=True)
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def add_input(self, name: str, data=None, **kwargs) -> None:
        """ParadataLogger.add_input (paradata 3.0): an input by distribution name."""
        self.inputs.append(name)

    def add_output(self, name: str, data=None, **kwargs) -> None:
        self.outputs.append(name)

    def set_parameter(self, key: str, value) -> None:
        self.parameters[key] = value

    def fail(self, error: str) -> None:
        self.error = error

    def log_success(
        self,
        output_type: str,
        count: int = 1,
    ) -> None:
        self.successes.extend([output_type] * count)

    def log_component(
        self,
        name: str,
        license=None,
    ) -> None:
        self.components.append(name)

    def log_skip(
        self,
        filepath: str,
        reason: str,
    ) -> None:
        self.skipped.append((filepath, reason))

    def note_limits(self, notes) -> None:
        """ParadataLogger.note_limits (atrium-project#53): the limits that shaped a file."""
        self.limits_applied.extend(notes.as_list())

    def finalize(
        self,
        input_total=None,
        processed_total=None,
    ) -> str:
        self.finalized_with = input_total
        return str(Path(self.paradata_dir) / "test-run_translator.json")


def _write_xml(
    tmp_path: Path,
    name: str = "input.xml",
) -> Path:
    path = tmp_path / name
    path.write_text(
        "<root><child>hello</child></root>",
        encoding="utf-8",
    )
    return path


def _write_xpaths(tmp_path: Path) -> Path:
    path = tmp_path / "xpaths.txt"
    path.write_text(
        "# fields to translate\n//root/child\n",
        encoding="utf-8",
    )
    return path


def _write_empty_config(tmp_path: Path) -> Path:
    """
    Create an isolated configuration without repository-level defaults.

    In particular, this prevents the repository's config.txt vocabulary
    setting from leaking into tests that expect vocab_path=None.
    """
    path = tmp_path / "config.ini"
    path.write_text(
        "[DEFAULT]\n",
        encoding="utf-8",
    )
    return path


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        argv,
    )
    main.main()


def _configure_noop_pipeline(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Replace heavyweight pipeline components while preserving the real
    main.py orchestration and DocumentRecord integration.
    """
    process_alto_xml = MagicMock(
        name="process_alto_xml",
    )
    process_metadata_xml = MagicMock(
        name="process_metadata_xml",
    )
    load_xsd = MagicMock(
        name="load_xsd",
        return_value=object(),
    )

    translator = MagicMock(
        name="translator",
    )
    translator.vocabulary = None
    translator.protected_count = 0
    translator.supports_glossary = False
    translator.reset_protected_count = MagicMock(
        name="reset_protected_count",
    )
    translator.license_components = MagicMock(
        name="license_components",
        return_value=["lindat_cubbitt"],
    )

    identifier = MagicMock(
        name="identifier",
    )
    identifier.detect.return_value = (
        "cs",
        0.99,
    )

    get_backend = MagicMock(
        name="get_backend",
        return_value=translator,
    )
    identifier_factory = MagicMock(
        name="LanguageIdentifier",
        return_value=identifier,
    )

    FakeParadataLogger.instances.clear()

    monkeypatch.setattr(
        main,
        "process_alto_xml",
        process_alto_xml,
    )
    monkeypatch.setattr(
        main,
        "process_metadata_xml",
        process_metadata_xml,
    )
    monkeypatch.setattr(
        main,
        "load_xsd",
        load_xsd,
    )
    monkeypatch.setattr(
        main,
        "get_backend",
        get_backend,
    )
    monkeypatch.setattr(
        main,
        "LanguageIdentifier",
        identifier_factory,
    )
    monkeypatch.setattr(
        main,
        "ParadataLogger",
        FakeParadataLogger,
    )

    return SimpleNamespace(
        process_alto_xml=process_alto_xml,
        process_metadata_xml=process_metadata_xml,
        load_xsd=load_xsd,
        translator=translator,
        identifier=identifier,
        get_backend=get_backend,
        identifier_factory=identifier_factory,
    )


def test_parse_arguments_honours_config_and_cli_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = tmp_path / "config.ini"
    config.write_text(
        """[DEFAULT]
source_lang = cs
target_lang = en
formats = xml
output = ./from-config
translation_backend = lindat
""",
        encoding="utf-8",
    )

    input_file = _write_xml(
        tmp_path,
    )
    xpaths_file = _write_xpaths(
        tmp_path,
    )

    output = tmp_path / "out"
    xsd = tmp_path / "schema.xsd"
    vocabulary = tmp_path / "vocab.csv"
    document_json = tmp_path / "doc.json"
    document_json_out = tmp_path / "doc-out.json"
    download_dir = tmp_path / "downloads"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--config",
            str(config),
            "--output",
            str(output),
            "--source_lang",
            "auto",
            "--target_lang",
            "de",
            "--formats",
            "alto.xml",
            "--xpaths",
            str(xpaths_file),
            "--xsd",
            str(xsd),
            "--vocabulary",
            str(vocabulary),
            "--document-json",
            str(document_json),
            "--document-json-out",
            str(document_json_out),
            "--download-dir",
            str(download_dir),
            "--fast-align",
            "--backend",
            "openai_compatible",
            "--alto",
            str(input_file),
        ],
    )

    args, parsed_config = main.parse_arguments()

    assert args.input_path == input_file
    assert args.output == output
    assert args.source_lang == "auto"
    assert args.target_lang == "de"
    assert args.formats == "alto.xml"
    assert args.xpaths == xpaths_file
    assert args.xsd == str(xsd)
    assert args.vocabulary == vocabulary
    assert args.document_json == document_json
    assert args.document_json_out == document_json_out
    assert args.download_dir == download_dir
    assert args.fast_align is True
    assert args.backend == "openai_compatible"
    assert args.alto is True

    assert parsed_config["DEFAULT"]["source_lang"] == "cs"


def test_main_routes_to_alto_processor_and_writes_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    spies = _configure_noop_pipeline(
        monkeypatch,
    )
    config = _write_empty_config(
        tmp_path,
    )

    input_file = tmp_path / "sample.alto.xml"
    input_file.write_text(
        """<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout>
    <Page ID="P1" WIDTH="100" HEIGHT="100">
      <PrintSpace>
        <TextBlock ID="TB1">
          <TextLine ID="L1">
            <String ID="S1" CONTENT="Dobrý"/>
            <String ID="S2" CONTENT="den"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>""",
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"

    _run_main(
        monkeypatch,
        [
            "main.py",
            "--config",
            str(config),
            "--alto",
            "--formats",
            "alto.xml",
            "--source_lang",
            "cs",
            "--target_lang",
            "en",
            "--backend",
            "lindat",
            "--output",
            str(output_dir),
            str(input_file),
        ],
    )

    expected_output = output_dir / "sample_en.alto.xml"

    spies.process_alto_xml.assert_called_once()
    call = spies.process_alto_xml.call_args

    assert call.args[0] == input_file
    assert call.args[1] == expected_output
    assert call.args[2] is spies.translator
    assert call.args[3:5] == (
        "cs",
        "en",
    )
    assert call.kwargs["line_anchors"] is True
    assert isinstance(call.kwargs["facts"], dict)

    spies.get_backend.assert_called_once_with(
        "lindat",
        vocab_path=None,
    )
    spies.translator.reset_protected_count.assert_called_once()
    spies.identifier_factory.assert_not_called()

    assert (output_dir / "sample_log.csv").exists()
    # Record schema 2.0: no record without one sent; the paradata name the original read.
    assert not (output_dir / "sample.document.json").exists()

    logger = FakeParadataLogger.instances[-1]
    assert logger.finalized_with == 1
    assert logger.inputs == ["original"] and logger.outputs == []


def test_main_routes_to_metadata_processor_with_vocab_xpaths_and_xsd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    spies = _configure_noop_pipeline(
        monkeypatch,
    )

    spies.translator.vocabulary = {
        "stone": "kámen",
    }
    spies.translator.protected_count = 2
    spies.translator.license_components.return_value = [
        "lindat_cubbitt",
        "udpipe2_engine",
        "udpipe2_models",
        "amcr_vocab",
        "teater_data",
    ]

    input_file = _write_xml(
        tmp_path,
        "metadata.xml",
    )
    xpaths_file = _write_xpaths(
        tmp_path,
    )

    vocabulary = tmp_path / "vocab.csv"
    vocabulary.write_text(
        "source_lemma,target_translation\nstone,stone\n",
        encoding="utf-8",
    )

    xsd = tmp_path / "schema.xsd"
    xsd.write_text(
        ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>'),
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"
    document_json_out = output_dir / "metadata.document.json"

    _run_main(
        monkeypatch,
        [
            "main.py",
            "--formats",
            "xml",
            "--source_lang",
            "auto",
            "--target_lang",
            "en",
            "--xpaths",
            str(xpaths_file),
            "--xsd",
            str(xsd),
            "--vocabulary",
            str(vocabulary),
            "--document-json-out",
            str(document_json_out),
            "--backend",
            "lindat",
            "--output",
            str(output_dir),
            str(input_file),
        ],
    )

    expected_output = output_dir / "metadata_en.xml"

    spies.process_metadata_xml.assert_called_once()
    call = spies.process_metadata_xml.call_args

    assert call.args[0] == input_file
    assert call.args[1] == expected_output
    assert call.args[2] == [
        "//root/child",
    ]
    assert call.args[3] is spies.translator
    assert call.args[4:6] == (
        "auto",
        "en",
    )
    assert call.kwargs["xsd_schema"] is spies.load_xsd.return_value
    assert call.kwargs["identifier"] is spies.identifier
    assert isinstance(call.kwargs["facts"], dict)

    spies.load_xsd.assert_called_once_with(
        str(xsd),
    )
    spies.get_backend.assert_called_once_with(
        "lindat",
        vocab_path=vocabulary,
    )
    spies.identifier_factory.assert_called_once_with()

    # No record was sent, so none comes back (record schema 2.0).
    assert not document_json_out.exists()
    assert (output_dir / "metadata_log.csv").exists()

    logger = FakeParadataLogger.instances[-1]
    assert logger.inputs == ["metadata"] and logger.outputs == ["metadata-en"]
    assert logger.parameters["xpaths"] == ["//root/child"]

    assert "fasttext" in logger.components
    assert logger.config["vocabulary_protected_terms"] == {
        "metadata": 2,
    }
    assert logger.config["vocabulary_protected_terms_total"] == 2
    assert "json" not in logger.successes


def test_main_records_processor_failure_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    spies = _configure_noop_pipeline(
        monkeypatch,
    )
    spies.process_alto_xml.side_effect = RuntimeError(
        "translation failed",
    )

    config = _write_empty_config(
        tmp_path,
    )
    input_file = _write_xml(
        tmp_path,
        "broken.alto.xml",
    )
    output_dir = tmp_path / "out"

    _run_main(
        monkeypatch,
        [
            "main.py",
            "--config",
            str(config),
            "--alto",
            "--formats",
            "alto.xml",
            "--backend",
            "lindat",
            "--output",
            str(output_dir),
            str(input_file),
        ],
    )

    logger = FakeParadataLogger.instances[-1]

    assert logger.skipped == [
        (
            str(input_file),
            "translation failed",
        )
    ]
    assert (output_dir / "broken_log.csv").exists()


def test_fetch_xml_from_url_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    response = MagicMock()
    response.content = b"<root/>"
    response.raise_for_status.return_value = None

    get = MagicMock(
        return_value=response,
    )
    monkeypatch.setattr(
        main.requests,
        "get",
        get,
    )

    result = main.fetch_xml_from_url(
        "https://api.aiscr.cz/id/ABC-123",
        tmp_path,
    )

    assert result == (tmp_path / "ABC-123.xml")
    assert result.read_bytes() == b"<root/>"

    get.assert_called_once_with(
        "https://api.aiscr.cz/id/ABC-123",
        headers={
            "User-Agent": "Mozilla/5.0",
        },
        timeout=60,
    )


def test_fetch_xml_from_url_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        main.requests,
        "get",
        MagicMock(
            side_effect=RuntimeError(
                "offline",
            ),
        ),
    )

    result = main.fetch_xml_from_url(
        "https://example.invalid/file.xml",
        tmp_path,
    )

    assert result is None
