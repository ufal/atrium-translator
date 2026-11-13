"""tests/test_api_contract.py — ATRIUM API meta-contract conformance (strategy §4, issue #32).

Hermetic contract test: asserts the ``/info`` envelope, ``/health``, ``/ready`` (issue #55), the advertised endpoint
set, and OpenAPI validity against the in-process app. ``importorskip``-guarded and tolerant of
missing service dependencies, so it is a clean no-op in the fast lane and a real check in CI.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

# --- per-service contract parameters -----------------------------------------------------------
SERVICE = "atrium-translator"
APP_IMPORT = "service.api"
PRIMARY_ENDPOINTS = ["/translate"]
# -----------------------------------------------------------------------------------------------

try:
    app = __import__(APP_IMPORT, fromlist=["app"]).app
# Only a missing dependency skips (atrium-project#53). This used to be `except Exception`,
# which turned ANY import-time failure into a green skip — including a malformed limit
# (atrium_limits.LimitConfigError), which must fail loudly.
except ImportError as exc:
    pytest.skip(f"cannot import {APP_IMPORT}.app: {exc}", allow_module_level=True)

client = TestClient(app)


def test_info_envelope_required_fields():
    """§4.1: /info always carries service, version, endpoints, limits.max_upload_mb."""
    response = client.get("/info")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == SERVICE
    assert data["version"] and data["version"] == app.version
    assert isinstance(data["endpoints"], list) and data["endpoints"]
    assert isinstance(data["limits"], dict)
    assert "max_upload_mb" in data["limits"]


def test_info_reports_every_declared_limit():
    """atrium-project#53: /info `limits` is tool_limits.LIMITS, value for value, and
    `limits_meta` names the variable that sets each one. tests/test_limits_contract.py checks
    the declaration against .env.example and the README."""
    from tool_limits import LIMITS

    data = client.get("/info").json()
    assert data["limits"] == LIMITS.values()
    assert data["limits_meta"] == LIMITS.meta()


def test_errors_have_the_harmonised_body():
    """§4.4 (atrium-project#32 item 2): every error is {status, reason, detail}."""
    body = client.get("/no-such-route").json()
    assert body == {"status": 404, "reason": None, "detail": "Not Found"}


def test_info_endpoints_match_real_routes():
    """Advertised endpoints are real routes, and every primary endpoint is advertised."""
    advertised = set(client.get("/info").json()["endpoints"])
    real = {r.path for r in app.routes if getattr(r, "methods", None)}
    assert advertised <= real
    for path in PRIMARY_ENDPOINTS:
        assert path in advertised, f"{path} missing from /info endpoints"


def test_health_shallow_ok():
    """§4.1: shallow /health is a cheap 200 liveness probe."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


def test_primary_endpoints_documented_in_openapi():
    paths = app.openapi()["paths"]
    for path in PRIMARY_ENDPOINTS:
        assert path in paths, f"{path} missing from OpenAPI paths"


def test_openapi_document_is_spec_valid():
    """The runtime /openapi.json validates against the OpenAPI 3.x spec (§2.2)."""
    spec_validator = pytest.importorskip("openapi_spec_validator")
    spec_validator.validate(app.openapi())


# --- §4.6 readiness + shutdown contract (issue #55) --------------------------------------------
# The state-machine itself is unit-tested once, in the hub
# (atrium-project/docs/templates/shared/test_atrium_service.py). What these assert is that THIS
# repo actually wired it up: the route exists, it is advertised, and — the one that matters —
# liveness does not start failing just because the service is draining.

try:
    _state = getattr(__import__(APP_IMPORT, fromlist=["app"]), "_state", None)
except Exception:  # noqa: BLE001 - same missing-heavy-deps case this file already guards
    # Repos guard the app import two different ways (module-level pytest.skip vs a
    # `deps_present` flag + pytestmark.skipif). Under the second style this module keeps
    # loading after a failed import, so this must not raise at import time; the skip
    # marker already stops the tests below from running.
    _state = None


def test_ready_route_is_registered_and_advertised():
    """§4.6: /ready exists, and /info advertises it like any other route."""
    assert _state is not None, (
        f"{APP_IMPORT} has no module-level `_state` — the service has not adopted "
        "ServiceState/attach_health(state=...) (issue #55)"
    )
    response = client.get("/ready")
    assert response.status_code in (200, 503)
    assert response.json()["status"] in {"ready", "starting", "draining"}
    assert "/ready" in client.get("/info").json()["endpoints"]


def test_ready_reports_starting_before_warmup_and_ready_after():
    """503 until the service's own lifespan marks it warm, 200 once it has.

    `client` above is a bare TestClient, so the ASGI lifespan has NOT run and the service is
    genuinely un-warm here — which is exactly the pre-warmup state a Kubernetes startupProbe
    sees on a cold pod.
    """
    assert _state is not None
    was_warm, was_draining = _state.warm, _state.draining
    try:
        _state.draining = False
        _state.warm = False
        assert client.get("/ready").status_code == 503
        assert client.get("/ready").json()["status"] == "starting"

        _state.warm = True
        assert client.get("/ready").status_code == 200
        assert client.get("/ready").json()["status"] == "ready"
    finally:
        _state.warm, _state.draining = was_warm, was_draining


def test_liveness_stays_200_while_draining_but_readiness_does_not():
    """The load-bearing distinction of issue #55.

    If shallow /health went 503 on SIGTERM, an orchestrator's livenessProbe would SIGKILL the
    container before its drain finished — the very failure the drain exists to prevent. Routing
    traffic away from a draining pod is /ready's job.
    """
    assert _state is not None
    was_warm, was_draining = _state.warm, _state.draining
    try:
        _state.warm = True
        _state.draining = True

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "draining"
    finally:
        _state.warm, _state.draining = was_warm, was_draining


def test_deep_health_reports_draining_with_operator_fields():
    """`?deep=true` had no coverage in any repo before issue #55."""
    assert _state is not None
    was_warm, was_draining = _state.warm, _state.draining
    try:
        _state.warm = True
        _state.draining = True
        response = client.get("/health?deep=true")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["detail"] == "shutting down"
        assert body["draining"] is True
        assert "in_flight" in body
    finally:
        _state.warm, _state.draining = was_warm, was_draining


# --- the typed contract (atrium-project#32 round 2) --------------------------------------------
# tests/test_openapi_contract.py (canonical, vendored) checks the committed spec itself. What
# these add is the part only this repo can do: drive /translate through the real
# process_single_file (with a fake backend, as tests/test_api.py does) and hold every JSON
# response — the opt-in response_format=json 200 and every refusal — to the schema the
# PUBLISHED spec declares for it.

import json  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import atrium_openapi  # noqa: E402

_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")

_ALTO_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?><alto xmlns="http://www.loc.gov/standards/alto/ns-v2#"><Layout>'
    b'<Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="1000" HEIGHT="1000"><PrintSpace><TextBlock ID="TB1">'
    b'<TextLine ID="L1"><String ID="S1" CONTENT="Dobr\xc3\xbd"/><String ID="S2" CONTENT="den"/></TextLine>'
    b"</TextBlock></PrintSpace></Page></Layout></alto>"
)
_SEED_ID = "C-202000543A-DT-27"
#: A #67 R1 seed: the AMČR file id and the source, nothing else.
_SEED = {"doc_id": _SEED_ID, "source": {"sha512": "a" * 128, "filename": "scan.pdf", "media_type": "application/pdf"}}


def _models():
    translator = MagicMock()
    translator.name = "lindat"
    translator.vocabulary = {}
    translator.protected_count = 0
    translator.translate.side_effect = lambda text, *a, **k: f"EN:{text}"
    translator.license_components.return_value = ["lindat_cubbitt"]
    return {"translator": translator, "identifier": MagicMock()}


def _conforms(status, response, path="/translate", method="post"):
    pytest.importorskip("jsonschema")
    assert response.status_code == status, response.text[:400]
    atrium_openapi.validate_response(_SPEC, path, method, status, response.json())
    return response.json()


def test_response_format_json_conforms_including_the_record():
    """The returned record is held to the vendored record schema, through the spec's
    AtriumDocument component — the type AMČR's generated client deserialises it into."""
    with patch("service.api.models", _models()):
        response = client.post(
            "/translate?source_lang=cs",
            files={
                "file": ("scan.alto.xml", _ALTO_XML, "application/xml"),
                "document_json": ("seed.document.json", json.dumps(_SEED).encode(), "application/json"),
            },
            data={"is_alto": "true", "response_format": "json"},
        )
    body = _conforms(200, response)
    assert response.headers["content-type"].startswith("application/json")
    assert (body["type"], body["filename"], body["media_type"]) == ("alto", "scan_en.alto.xml", "application/xml")
    assert "EN:" in body["content"] and body["content"].startswith("<?xml")
    # Record schema 2.0: the translator writes nothing into the record; it comes back as a 2.0
    # record with the seed's content and nothing added.
    assert body["document_json"] == {**_SEED, "schema_version": "2.0", "record_type": "atrium-document"}


def test_response_format_json_without_a_record_has_none():
    with patch("service.api.models", _models()):
        response = client.post(
            "/translate?source_lang=cs&response_format=json",
            files={"file": ("scan.alto.xml", _ALTO_XML, "application/xml")},
            data={"is_alto": "true"},
        )
    body = _conforms(200, response)
    assert "document_json" not in body and body["limits_applied"] == []


def test_the_default_response_is_still_the_xml():
    with patch("service.api.models", _models()):
        response = client.post(
            "/translate?source_lang=cs",
            files={"file": ("scan.alto.xml", _ALTO_XML, "application/xml")},
            data={"is_alto": "true"},
        )
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/xml")
    assert b"EN:" in response.content


def test_an_unknown_response_format_is_422_from_either_source():
    for kwargs in ({"data": {"response_format": "yaml"}}, {"params": {"response_format": "yaml"}}):
        with patch("service.api.models", _models()):
            response = client.post(
                "/translate", files={"file": ("scan.alto.xml", _ALTO_XML, "application/xml")}, **kwargs
            )
        assert _conforms(422, response)["reason"] is None


def test_a_non_xml_upload_is_415_unsupported_media_type():
    body = _conforms(415, client.post("/translate", files={"file": ("scan.pdf", b"%PDF-1.7", "application/pdf")}))
    assert (body["reason"], body["accepted"]) == ("unsupported_media_type", [".xml"])


def test_a_wrong_content_type_is_415_unsupported_media_type():
    response = client.post("/translate", content=b"<alto/>", headers={"Content-Type": "application/xml"})
    body = _conforms(415, response)
    assert body["reason"] == "unsupported_media_type"
    assert body["accepted"] == ["application/json", "multipart/form-data"]


@pytest.mark.parametrize("record", [b"[1, 2]", b"{not json", b'{"schema_version": "9.0", "doc_id": "x"}'])
@patch("service.api.process_single_file")
def test_a_record_that_cannot_be_opened_is_422_invalid_record_before_any_translation(mock_process, record):
    with patch("service.api.models", _models()):
        response = client.post(
            "/translate",
            files={
                "file": ("scan.alto.xml", _ALTO_XML, "application/xml"),
                "document_json": ("r.document.json", record, "application/json"),
            },
        )
    body = _conforms(422, response)
    assert body["reason"] == "invalid_record" and mock_process.call_count == 0


def test_an_empty_record_part_counts_as_none():
    """It used to be written as an empty baseline and the record came back from scratch."""
    with patch("service.api.models", _models()):
        response = client.post(
            "/translate?source_lang=cs",
            files={
                "file": ("scan.alto.xml", _ALTO_XML, "application/xml"),
                "document_json": ("r.document.json", b"", "application/json"),
            },
        )
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/xml")


def test_a_seed_with_a_byte_order_mark_is_accepted():
    """parse_record_part accepts a UTF-8 BOM, and so does atrium_document.load_document
    (utf-8-sig): the record the gate lets through must not fail later as a 500."""
    seed = b"\xef\xbb\xbf" + json.dumps(_SEED).encode()
    with patch("service.api.models", _models()):
        response = client.post(
            "/translate?source_lang=cs&response_format=json",
            files={
                "file": ("scan.alto.xml", _ALTO_XML, "application/xml"),
                "document_json": ("seed.document.json", seed, "application/json"),
            },
        )
    assert _conforms(200, response)["document_json"]["doc_id"] == _SEED_ID


def test_a_draining_replica_refuses_new_work_with_the_error_body():
    from service.api import _state

    was_draining = _state.draining
    try:
        _state.draining = True
        body = _conforms(
            503, client.post("/translate", files={"file": ("scan.alto.xml", _ALTO_XML, "application/xml")})
        )
        assert body["reason"] is None
    finally:
        _state.draining = was_draining


def test_info_conforms_to_the_published_schema():
    _conforms(200, client.get("/info"), path="/info", method="get")
