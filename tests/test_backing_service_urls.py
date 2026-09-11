"""
tests/test_backing_service_urls.py – Endpoint attachability (atrium-project#63).

Factor IV asks that a backing service be swappable by configuration alone. This
repo reaches two LINDAT-hosted services — UDPipe 2 (lemmatisation, for
Tag-and-Protect) and the CUBBITT translation API — and both were pinned to
class constants while every neighbouring dial (``LLM_BASE_URL``,
``LINDAT_MIN_INTERVAL_S``, ``LINDAT_MAX_RETRIES``) was already configurable.

Precedence under test, highest first:

    explicit argument  >  TRANSLATION_URL  >  LINDAT_BASE_URL  >  class constant
    explicit argument  >  UDPIPE_URL                           >  class constant

The paradata assertions matter as much as the request ones. ``translation_api``
is a provenance claim, and once the host is configurable a repeated literal
eventually names somewhere the request never went. Before this issue nothing
asserted that field at all, in either the CLI or the service path.
"""

from __future__ import annotations

import argparse
import configparser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from processors.lemmatizer import (
    DEFAULT_UDPIPE_URL,
    LindatLemmatizer,
    resolve_udpipe_url,
)
from processors.translator import (
    DEFAULT_TRANSLATION_URL,
    LindatTranslator,
    resolve_translation_url,
)

STUB_UDPIPE = "http://127.0.0.1:9991/udpipe"
STUB_TRANSLATE = "http://127.0.0.1:9992/translate/api/v2"
ARG_URL = "http://127.0.0.1:9993/from-argument"


@pytest.fixture(autouse=True)
def _clean_endpoint_env(monkeypatch):
    """No test may inherit another's endpoint, or the defaults case is a lie."""
    for name in ("UDPIPE_URL", "TRANSLATION_URL", "LINDAT_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


# ════════════════════════════════════════════════════════════════════════════
# UDPipe — processors/lemmatizer.py
# ════════════════════════════════════════════════════════════════════════════


class TestUDPipeEndpoint:
    def test_default_is_the_lindat_endpoint(self):
        assert resolve_udpipe_url() == DEFAULT_UDPIPE_URL
        assert LindatLemmatizer().url == DEFAULT_UDPIPE_URL
        assert LindatLemmatizer.URL == DEFAULT_UDPIPE_URL

    def test_env_redirects_the_lemmatizer(self, monkeypatch):
        monkeypatch.setenv("UDPIPE_URL", STUB_UDPIPE)
        assert LindatLemmatizer().url == STUB_UDPIPE

    def test_explicit_argument_beats_env(self, monkeypatch):
        monkeypatch.setenv("UDPIPE_URL", STUB_UDPIPE)
        assert LindatLemmatizer(url=ARG_URL).url == ARG_URL

    def test_the_request_actually_goes_to_the_configured_host(self, monkeypatch):
        """The endpoint is only attachable if the POST follows it."""
        monkeypatch.setenv("UDPIPE_URL", STUB_UDPIPE)
        lem = LindatLemmatizer()

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"result": "# sent_id = 1\n1\tAhoj\tahoj\n"}

        with patch("processors.lemmatizer.requests.post", return_value=response) as post:
            list(lem._request_conllu_chunks("Ahoj světe.", "czech-pdt"))

        assert post.call_args[0][0] == STUB_UDPIPE

    def test_construction_stays_network_free(self, monkeypatch):
        """__init__ is called eagerly whenever a vocabulary loads."""
        monkeypatch.setenv("UDPIPE_URL", STUB_UDPIPE)
        with patch("processors.lemmatizer.requests.post") as post:
            LindatLemmatizer()
        post.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# Translation — processors/translator.py
# ════════════════════════════════════════════════════════════════════════════


class TestTranslationEndpoint:
    def test_default_is_the_lindat_endpoint(self):
        assert resolve_translation_url() == DEFAULT_TRANSLATION_URL
        assert LindatTranslator.BASE_URL == DEFAULT_TRANSLATION_URL

    def test_translation_url_is_honoured(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE)
        assert resolve_translation_url() == STUB_TRANSLATE

    def test_lindat_base_url_is_honoured_as_the_issue_spelling(self, monkeypatch):
        monkeypatch.setenv("LINDAT_BASE_URL", STUB_TRANSLATE)
        assert resolve_translation_url() == STUB_TRANSLATE

    def test_translation_url_wins_when_both_are_set(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE)
        monkeypatch.setenv("LINDAT_BASE_URL", "http://127.0.0.1:9994/loser")
        assert resolve_translation_url() == STUB_TRANSLATE

    def test_explicit_argument_beats_env(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE)
        assert resolve_translation_url(ARG_URL) == ARG_URL

    def test_endpoint_is_resolved_before_the_first_request(self, monkeypatch):
        """Ordering guard.

        ``_fetch_models()`` is the first thing __init__ used to do, and it reads
        the endpoint. Resolving the URL after it would send the very first
        request of the process to the default host no matter how the deployment
        is configured — a bug that leaves every later request correct and is
        therefore easy to miss.
        """
        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE)

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = ["cs-en"]

        with patch("processors.translator.requests.get", return_value=response) as get:
            translator = LindatTranslator()

        assert translator.base_url == STUB_TRANSLATE
        assert get.call_args[0][0] == f"{STUB_TRANSLATE}/models"

    def test_trailing_slash_is_normalised_away(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE + "/")
        with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
            assert LindatTranslator().base_url == STUB_TRANSLATE

    def test_translate_requests_target_the_configured_host(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE)
        with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
            translator = LindatTranslator()

        with patch.object(translator, "_post_with_retry", return_value="hello") as post:
            translator._basic_translate("ahoj", "cs", "en")

        assert post.call_args[0][0].startswith(f"{STUB_TRANSLATE}/models/cs-en")


# ════════════════════════════════════════════════════════════════════════════
# Paradata — the endpoint a record CLAIMS must be the one it USED
# ════════════════════════════════════════════════════════════════════════════


def _cli_args(backend: str) -> argparse.Namespace:
    return argparse.Namespace(
        input_path=Path("in.xml"),
        output=Path("out"),
        source_lang="cs",
        target_lang="en",
        formats="xml",
        alto=True,
        backend=backend,
        xpaths=None,
        xsd=None,
        vocabulary=None,
    )


class TestCLIParadata:
    def test_records_the_effective_endpoint(self, monkeypatch):
        import main

        monkeypatch.setenv("TRANSLATION_URL", STUB_TRANSLATE)
        cfg = main._build_paradata_config(_cli_args("lindat"), configparser.ConfigParser())
        assert cfg["translation_api"] == STUB_TRANSLATE + "/"

    def test_default_record_is_byte_identical_to_the_pre_issue_literal(self):
        import main

        cfg = main._build_paradata_config(_cli_args("lindat"), configparser.ConfigParser())
        assert cfg["translation_api"] == "https://lindat.mff.cuni.cz/services/translation/api/v2/"

    def test_non_lindat_backends_claim_no_lindat_endpoint(self):
        """The guard service/api.py has had since M1, applied to the CLI path.

        An LLM or CT2 run never touches this host, so naming it is a false
        provenance claim — and omitting the field beats stating a wrong one.
        """
        import main

        cfg = main._build_paradata_config(_cli_args("openai_compatible"), configparser.ConfigParser())
        assert "translation_api" not in cfg


class TestServiceParadata:
    """The regression the issue calls out, and which nothing covered before."""

    @staticmethod
    def _post_and_capture(monkeypatch, backend_name, base_url):
        from fastapi.testclient import TestClient

        import service.api as api

        captured: dict = {}

        def _fake_process(file_path=None, output_file=None, **kwargs):
            if output_file is not None:
                output_file.write_bytes(b"<alto/>")
            return True, 0

        class _CapturingLogger:
            def __init__(self, **kwargs):
                captured.update(kwargs.get("config") or {})

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def log_component(self, *a, **kw):
                pass

        translator = MagicMock()
        translator.name = backend_name
        translator.vocabulary = {}
        translator.license_components.return_value = ["lindat_cubbitt"]
        translator.base_url = base_url

        with (
            patch("service.api.process_single_file", side_effect=_fake_process),
            patch("service.api.ParadataLogger", _CapturingLogger),
            patch("service.api.models", {"translator": translator, "identifier": MagicMock()}),
        ):
            response = TestClient(api.app).post(
                "/translate?source_lang=cs&target_lang=en",
                files={"file": ("t.alto.xml", b"<alto/>", "application/xml")},
                data={"is_alto": "true"},
            )
        assert response.status_code == 200
        return captured

    def test_records_the_endpoint_the_warmed_backend_will_call(self, monkeypatch):
        captured = self._post_and_capture(monkeypatch, "lindat", STUB_TRANSLATE)
        assert captured["translation_api"] == STUB_TRANSLATE + "/"

    def test_default_record_is_byte_identical_to_the_pre_issue_literal(self, monkeypatch):
        captured = self._post_and_capture(monkeypatch, "lindat", DEFAULT_TRANSLATION_URL)
        assert captured["translation_api"] == "https://lindat.mff.cuni.cz/services/translation/api/v2/"

    def test_non_lindat_backends_claim_no_lindat_endpoint(self, monkeypatch):
        captured = self._post_and_capture(monkeypatch, "openai_compatible", None)
        assert "translation_api" not in captured
