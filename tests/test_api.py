"""
tests/test_api.py
Automated TestClient coverage for the FastAPI service and DoS guards.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from service.api import MAX_UPLOAD_BYTES, app

client = TestClient(app)


def test_info_endpoint():
    response = client.get("/info")
    assert response.status_code == 200
    assert "ALTO XML" in response.json()["supported_formats"]


def test_translate_rejects_non_xml():
    response = client.post(
        "/translate", files={"file": ("test.txt", b"dummy content", "text/plain")}, data={"is_alto": "true"}
    )
    # §4.4: unusable/invalid input is 422 (harmonized from 400).
    assert response.status_code == 422
    assert "Only XML files" in response.json()["detail"]


def test_translate_upload_size_limit():
    oversized_content = b"x" * (MAX_UPLOAD_BYTES + 1)
    response = client.post(
        "/translate", files={"file": ("large.alto.xml", oversized_content, "application/xml")}, data={"is_alto": "true"}
    )
    assert response.status_code == 413
    assert "File too large" in response.json()["detail"]


@patch("service.api.process_single_file")
@patch("service.api.ParadataLogger.log_component")
def test_translate_logs_components(mock_log_component, mock_process_single_file):
    """Component logging must fire on a successful API translation (M1)."""

    def _write_and_succeed(file_path=None, output_file=None, **kwargs):
        if output_file is not None:
            output_file.write_bytes(b"<alto/>")
        return True, 0

    mock_process_single_file.side_effect = _write_and_succeed

    fake_translator = MagicMock()
    fake_translator.name = "lindat"
    fake_translator.vocabulary = {}
    fake_translator.license_components.return_value = ["lindat_cubbitt"]

    fake_models = {"translator": fake_translator, "identifier": MagicMock()}

    with patch("service.api.models", fake_models):
        valid_xml_content = b"<alto></alto>"
        response = client.post(
            "/translate?source_lang=auto",
            files={"file": ("test.alto.xml", valid_xml_content, "application/xml")},
            data={"is_alto": "true"},
        )

    assert response.status_code == 200
    # fasttext is always logged when source_lang == "auto"
    mock_log_component.assert_any_call("fasttext")
    # At least one backend component must also have been logged
    assert mock_log_component.call_count >= 2, "backend license components should also be logged"


@patch("service.api.process_single_file")
def test_translate_happy_path(mock_process_single_file):
    """Full upload-to-response round-trip verifying HTTP headers and payload."""

    def fake_process(file_path=None, output_file=None, **kwargs):
        if output_file is not None:
            output_file.write_bytes(b"<alto><String CONTENT='translated'/></alto>")
        return True, 0

    mock_process_single_file.side_effect = fake_process

    fake_translator = MagicMock()
    fake_translator.name = "lindat"
    fake_translator.vocabulary = {}
    fake_translator.license_components.return_value = ["lindat_cubbitt"]

    fake_models = {"translator": fake_translator, "identifier": MagicMock()}

    with patch("service.api.models", fake_models):
        valid_xml_content = b"<alto><String CONTENT='original'/></alto>"
        response = client.post(
            "/translate?source_lang=cs&target_lang=en",
            files={"file": ("test.alto.xml", valid_xml_content, "application/xml")},
            data={"is_alto": "true"},
        )

    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert 'attachment; filename="test_en.alto.xml"' in response.headers["content-disposition"]
    assert b"translated" in response.content
