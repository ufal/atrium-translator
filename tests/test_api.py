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

    ``models`` is normally populated by the lifespan handler, which does not
    run in a bare TestClient call.  We inject a fake translator via
    ``patch.dict`` so the endpoint can reach the component-logging block
    without a real backend or model directory.
    """
    from unittest.mock import MagicMock

    from fastapi.responses import Response as _Response

    mock_process_single_file.return_value = (True, 0)

    fake_translator = MagicMock()
    fake_translator.name = "lindat"
    fake_translator.vocabulary = {}
    fake_translator.license_components.return_value = ["lindat_cubbitt"]

    fake_models = {"translator": fake_translator, "identifier": MagicMock()}
    # process_single_file is mocked so no output file is ever written;
    # patch FileResponse so the endpoint doesn't try to serve a missing path.
    dummy_response = _Response(content=b"<alto/>", media_type="application/xml")

    with patch("service.api.models", fake_models), patch("service.api.FileResponse", return_value=dummy_response):
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
