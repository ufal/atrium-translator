"""
tests/test_api.py
Automated TestClient coverage for the FastAPI service and DoS guards.
"""
import pytest
from fastapi.testclient import TestClient
from service.api import app, MAX_UPLOAD_BYTES

client = TestClient(app)

def test_info_endpoint():
    response = client.get("/info")
    assert response.status_code == 200
    assert response.json()["version"] == "0.6.1"
    assert "ALTO XML" in response.json()["supported_formats"]

def test_translate_rejects_non_xml():
    response = client.post(
        "/translate",
        files={"file": ("test.txt", b"dummy content", "text/plain")},
        data={"is_alto": "true"}
    )
    assert response.status_code == 400
    assert "Only XML files" in response.json()["detail"]

def test_translate_upload_size_limit():
    oversized_content = b"x" * (MAX_UPLOAD_BYTES + 1)
    response = client.post(
        "/translate",
        files={"file": ("large.alto.xml", oversized_content, "application/xml")},
        data={"is_alto": "true"}
    )
    assert response.status_code == 413
    assert "File too large" in response.json()["detail"]