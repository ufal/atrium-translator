"""
tests/test_api.py
Automated TestClient coverage for the FastAPI service and DoS guards.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from service.api import MAX_UPLOAD_BYTES, app

client = TestClient(app)


def test_info_endpoint():
    response = client.get("/info")
    assert response.status_code == 200
    # assert response.json()["version"] == "0.6.1"
    assert "ALTO XML" in response.json()["supported_formats"]


def test_translate_rejects_non_xml():
    response = client.post(
        "/translate", files={"file": ("test.txt", b"dummy content", "text/plain")}, data={"is_alto": "true"}
    )
    assert response.status_code == 400
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
    """Component logging must fire on a successful API translation (M1).

    ``models`` is not populated in a bare TestClient call (lifespan doesn't
    run), so we inject a fake translator via ``patch``.  After C1 the endpoint
    reads the output file while inside the TemporaryDirectory context, so the
    side-effect must write it; a plain return_value is no longer enough.
    """
    from unittest.mock import MagicMock

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
    """Full upload-to-response round-trip: verifies status, content-type,
    Content-Disposition header, and that translated bytes reach the client.

    Uses a side-effect that writes the output file (required after C1, which
    reads the file into memory while the TemporaryDirectory is still alive).
    """
    from unittest.mock import MagicMock

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
