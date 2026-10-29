"""
service/api.py

FastAPI service for the ATRIUM LINDAT Translator.
Brings this repository into API parity with the rest of the ATRIUM pipeline.

The typed contract (atrium-project#32 round 2). Every route declares its response model
and its error statuses, so the committed ``service/openapi.json`` — attached to every
release, and what the AMČR pipeline generates its clients from — types every field.
``/translate`` answers the translated XML (or multipart/mixed with the record) as before;
``response_format=json`` asks for one JSON object instead (``TranslateResponse``), the
shape a generated client reads without a multipart parser. Both of those carry the run's
Process Run Crate ``CreateAction`` (atrium-project#71); a bare XML answer has no place for it. The models below DOCUMENT the
responses (``response_model=None``), and ``tests/test_api_contract.py`` validates real
responses against the published schema. Refusals carry registered reasons: an upload that
is not ``.xml`` is 415 ``unsupported_media_type``, a record that cannot be opened is 422
``invalid_record``. Regenerate the spec after an API change::

    python atrium_openapi.py export --app service.api:app --out service/openapi.json
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

import atrium_rocrate
from atrium_document import canonical_doc_id
from atrium_limits import LimitExceeded, LimitNotes
from atrium_paradata import ParadataLogger
from main import (
    build_run_config,
    degenerate_replies,
    log_backend_components,
    process_single_file,
    record_doc_id,
    run_outcome_config,
    translated_filename,
)
from processors.backend import get_backend
from processors.identifier import LanguageIdentifier
from processors.language import (
    SourceLanguagePolicy,
    allowed_source_languages,
    language_pair_problem,
    normalise_lang_code,
)
from processors.vocab import load_vocabulary_records
from tool_limits import LIMITS, MAX_UPLOAD, max_request_mb
from utils import (
    DEFAULT_OUTPUT_MODE,
    XML_KIND_ALTO,
    load_xsd,
    normalize_output_mode,
    validate_xml_with_xsd,
    xml_kind,
)

# Shared ATRIUM meta-contract helpers (§4). Byte-identical across every service,
# enforced by para-drift.reusable.yml.
try:
    from .atrium_service import (
        AtriumDocument,
        AtriumHTTPError,
        CreateAction,
        InfoBase,
        LimitNote,
        ServiceState,
        add_cors,
        attach_error_handlers,
        attach_health,
        attach_inflight_middleware,
        attach_openapi_contract,
        build_info,
        error_body,
        error_responses,
        operation_id,
        parse_record_part,
        read_tool_version,
        read_upload_bounded,
        serve_lifecycle,
    )
except ImportError:
    from atrium_service import (
        AtriumDocument,
        AtriumHTTPError,
        CreateAction,
        InfoBase,
        LimitNote,
        ServiceState,
        add_cors,
        attach_error_handlers,
        attach_health,
        attach_inflight_middleware,
        attach_openapi_contract,
        build_info,
        error_body,
        error_responses,
        operation_id,
        parse_record_part,
        read_tool_version,
        read_upload_bounded,
        serve_lifecycle,
    )

logger = logging.getLogger(__name__)

#: The tool id (/info `service`, the spec's `x-atrium-service`): the repository name.
SERVICE = "atrium-translator"

#: Where para_config.txt (the tool version and licences the paradata records) lives: the repo root.
_PARA_CONFIG_DIR = str(Path(__file__).resolve().parents[1])

# Every limit this service has is declared in tool_limits.py (atrium-project#53, factor III)
# and read per request. These are the import-time values, kept because tests and clients
# import them.
MAX_UPLOAD_MB = MAX_UPLOAD.get()
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)

# Read uploads a megabyte at a time so the limit is enforced DURING the read.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

# Ceiling on the whole multipart envelope, checked against Content-Length before
# the body is touched at all. /translate accepts two file parts (the XML and an
# optional baseline document JSON), so a legitimate envelope can be about twice
# the per-file limit; the extra megabyte covers multipart boundaries and headers.
# This is a coarse early reject, not the real limit -- _read_bounded() below is.
# Reported in /info as the derived limit `max_request_mb` (tool_limits.max_request_mb).
MAX_REQUEST_BYTES = 2 * MAX_UPLOAD_BYTES + _UPLOAD_CHUNK_BYTES

_MIB = 1024 * 1024

#: Response header carrying the limits-applied summary (atrium-project#53);
#: atrium_service.EXPOSED_HEADERS lets a browser read it.
LIMITS_HEADER = "X-Atrium-Limits-Applied"
#: Response header with the XSD verdict of a metadata translation, when XSD_PATH is set.
XSD_HEADER = "X-Atrium-Xsd-Valid"

#: What /translate reads (§4.4 `accepted` of its 415): an XML file, by its name.
ACCEPTED_SUFFIXES = [".xml"]


# ── the typed contract (atrium-project#32 round 2) ──────────────────────────────────────────
# These models document the responses the handlers build; they do not filter them. A field
# the handler always sends has no default (required); one it sends only sometimes defaults to
# None. Descriptions are published in service/openapi.json, so they are written for the client.

#: `response_format` of /translate. `xml` is what the endpoint has always answered.
ResponseFormat = Literal["xml", "json"]


class TranslateResponse(BaseModel):
    """`/translate` with `response_format=json`: the translated XML and the record in one object."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="What the upload was translated as: `alto` (ALTO XML) or `metadata` (AMČR XML).")
    filename: str = Field(description="The translated file's name, e.g. `CTX000000003-1_en.alto.xml`.")
    media_type: str = Field(description="The media type of `content`: `application/xml`.")
    content: str = Field(description="The translated XML document, as UTF-8 text.")
    limits_applied: List[LimitNote] = Field(
        description="Every limit that shaped the translation without refusing it (also the response header)."
    )
    document_json: Optional[AtriumDocument] = Field(
        None,
        description=(
            "When a record was sent as `document_json`, or asked for with `include_record=true`: the record with "
            "the translator's `translations` block and `derived_from.translated_xml` added."
        ),
    )
    document_json_schema_error: Optional[str] = Field(
        None,
        description="Only when the returned record does not validate: the schema error (the sent record's).",
    )
    log_filename: Optional[str] = Field(
        None, description="With `include_log=true`: the log's file name, as the command line writes it."
    )
    log_csv: Optional[str] = Field(
        None,
        description=(
            "With `include_log=true`: the translation log (`file, page_num, line_num, text_<src>, text_<tgt>, "
            "status`), the `<doc>_log.csv` of the command line, as CSV text."
        ),
    )
    xsd_valid: Optional[bool] = Field(
        None,
        description="Only for a metadata record when the deployment sets `XSD_PATH`: whether the translation validates.",
    )
    xsd_errors: Optional[str] = Field(None, description="When `xsd_valid` is false: the validator's messages.")
    paradata: Optional[CreateAction] = Field(
        description=(
            "The call's provenance: its Process Run Crate `CreateAction` (atrium-project#71), whose `@id` is the "
            "`run_uuid` stamped into `document_json`."
        ),
    )


class VocabularyInfo(BaseModel):
    """The vocabulary `/translate` applies (`VOCABULARY_PATH`, translator#4)."""

    file: str = Field(description="The CSV's file name (not its path on the host).")
    terms: int = Field(description="Term pairs loaded.")
    with_description: int = Field(description="Terms that carry a concept description.")
    sha256: str = Field(description="sha256 of the CSV, to tell two vocabularies apart.")


class XsdInfo(BaseModel):
    """The XML Schema metadata translations are validated against (`XSD_PATH`)."""

    file: str = Field(description="The schema's file name (not its path on the host).")
    sha256: str = Field(description="sha256 of the schema file.")
    strict_default: bool = Field(description="`XSD_STRICT`: whether an invalid translation is refused by default.")


class TranslatorInfo(InfoBase):
    """`/info` of atrium-translator."""

    supported_formats: List[str] = Field(description="The kinds of XML `/translate` reads.")
    vocabulary: Optional[VocabularyInfo] = Field(
        None, description="The vocabulary `/translate` applies, or null when `VOCABULARY_PATH` is unset."
    )
    xsd: Optional[XsdInfo] = Field(
        None, description="The schema metadata translations are validated against, or null when `XSD_PATH` is unset."
    )


#: The 200 of /translate in its three shapes (the JSON one is TranslateResponse, added by FastAPI).
_TRANSLATE_200: Dict[str, Any] = {
    "description": (
        "The translated XML (`application/xml`, as an attachment). With a record sent, `multipart/mixed`: the "
        "XML, the record (`application/json`), `limits_applied.json` and `paradata.json` (the call's Process Run "
        "Crate `CreateAction`, `application/ld+json`). With `response_format=json`, one `TranslateResponse`."
    ),
    "headers": {
        LIMITS_HEADER: {
            "description": "`key=effect:count; …` for each limit that applied; absent when none did.",
            "schema": {"type": "string"},
        }
    },
    "content": {
        "application/xml": {"schema": {"type": "string"}},
        "multipart/mixed": {"schema": {"type": "string"}},
    },
}


def _reject_oversized_envelope(request: Request) -> None:
    """413 ``limit_exceeded`` on a declared Content-Length past the request cap, before reading.

    Starlette spools a multipart part to a temporary FILE once it grows past its
    own in-memory threshold, so an unbounded upload fills the container's disk
    during parsing -- before any handler code runs. A declared length is a hint
    (it can be absent, and it can lie), which is why this only supplements the
    per-part accounting in _read_bounded().
    """
    declared = request.headers.get("content-length", "")
    limit_mb = max_request_mb()
    if declared.isdigit() and int(declared) > limit_mb * _MIB:
        raise LimitExceeded(
            "max_request_mb",
            limit_mb,
            round(int(declared) / _MIB, 2),
            unit="MB",
            env="MAX_UPLOAD_MB",
            detail=(
                f"Request too large: over {limit_mb:g} MB (two parts of MAX_UPLOAD_MB={MAX_UPLOAD.get():g} "
                "plus 1 MB of multipart overhead)."
            ),
        )


async def _read_bounded(upload: UploadFile, limit_bytes: int, label: str) -> bytes:
    """Read *upload* fully, refusing it (413 ``limit_exceeded``) once it exceeds *limit_bytes*.

    The obvious form -- `content = await upload.read()` and then check
    `len(content)` -- decides whether the upload was too large only after the
    whole of it is resident in memory, so the 413 it raises is unreachable for
    exactly the inputs that need it: an unauthenticated caller could OOM-kill
    the container before the check ran. Reading in bounded chunks and stopping
    at the limit costs one extra join and makes the limit real. The reader itself
    moved to the shared atrium_service.read_upload_bounded (atrium-project#53), so
    every service now reads uploads this way.
    """
    return await read_upload_bounded(upload, limit_bytes / _MIB, label)


#: A client-supplied name used as a file name inside the request's temporary directory:
#: at most this many UTF-8 bytes, printable, and no path separators.
_MAX_NAME_BYTES = 200


def _safe_file_name(name: str | None) -> str | None:
    """*name* reduced to a plain file name, or ``None`` when nothing usable is left.

    The upload's and the baseline's names, and the baseline record's ``doc_id``, all
    become paths under the request's temporary directory. They are the client's, so a
    name like ``../../x.xml`` must not climb out of it (atrium-project#68 §D).
    """
    if not name:
        return None
    base = Path(str(name).replace("\\", "/")).name
    if base in ("", ".", "..") or not base.isprintable() or len(base.encode("utf-8")) > _MAX_NAME_BYTES:
        return None
    return base


models = {}

#: Readiness/draining/in-flight state for the §4.6 disposability contract (issue #55).
_state = ServiceState()


#: Where the metadata-mode XPath targets come from. The batch CLI reads the same
#: list from config.txt's `fields =` key (main.py:345-346); the service reads an env
#: var instead, because the service's whole config surface is environment-based
#: (12-factor III) and config.txt is a CLI-local artifact. `COPY . .` in the
#: Dockerfile already ships amcr-fields.txt to /app, so the default resolves inside
#: the published image with nothing to mount.
_REPO_ROOT = Path(__file__).resolve().parent.parent
AMCR_FIELDS_PATH = os.getenv("AMCR_FIELDS_PATH", "amcr-fields.txt")
#: The vocabulary CSV /translate applies (translator#4); empty: none, as before.
VOCABULARY_PATH = os.getenv("VOCABULARY_PATH", "")
#: A local XML Schema that metadata translations are validated against, loaded once at startup
#: (atrium-project#72, the command line's --xsd); empty: no validation, as before. A deployment
#: file, not a request field: a URL from a request would let a caller make the service fetch.
XSD_PATH = os.getenv("XSD_PATH", "")


def _xsd_strict_default() -> bool:
    """`XSD_STRICT`, the command line's own default for --xsd-strict: refuse an invalid translation."""
    return os.environ.get("XSD_STRICT", "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_xsd(path_value: str):
    """The schema ``XSD_PATH`` names, compiled: ``(schema, /info entry)``, or ``(None, None)`` when unset.

    As strict as ``VOCABULARY_PATH``: a schema someone set and that silently does not apply would
    let invalid records through, so one that cannot be read or compiled stops the service at
    startup, with the fix in the message.
    """
    if not path_value or not path_value.strip():
        return None, None
    candidate = Path(path_value.strip())
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    if not candidate.is_file():
        raise RuntimeError(
            f"XSD_PATH={path_value!r} does not resolve to a file (looked at {candidate}). Point it at a local "
            "XML Schema, or unset it to translate without validation."
        )
    try:
        schema = load_xsd(str(candidate))
    except Exception as exc:
        raise RuntimeError(f"XSD_PATH={path_value!r} ({candidate}) cannot be compiled: {exc}") from exc
    info = {
        "file": candidate.name,
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "strict_default": _xsd_strict_default(),
    }
    return schema, info


def _resolve_vocabulary(path_value: str) -> tuple[Optional[Path], Optional[dict]]:
    """The vocabulary ``VOCABULARY_PATH`` names, checked: ``(path, /info entry)``, or ``(None, None)`` when unset.

    Stricter than ``AMCR_FIELDS_PATH`` on purpose. A missing XPath file only refuses metadata
    requests, each with a 422 that says why; a vocabulary someone set and that is silently
    not applied would change every translation without a trace. So a value that does not
    name a usable CSV stops the service at startup, with the fix in the message.
    """
    if not path_value or not path_value.strip():
        return None, None
    candidate = Path(path_value.strip())
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    if not candidate.is_file():
        raise RuntimeError(
            f"VOCABULARY_PATH={path_value!r} does not resolve to a file (looked at {candidate}). Point it at a "
            "vocabulary CSV such as data_samples/vocabulary.csv, or unset it to translate without a vocabulary."
        )
    records = load_vocabulary_records(candidate)
    if not records:
        raise RuntimeError(
            f"VOCABULARY_PATH={path_value!r} ({candidate}) holds no term pair "
            "(source_lemma,target_translation). Fix the file, or unset the variable."
        )
    info = {
        "file": candidate.name,
        "terms": len(records),
        "with_description": sum(1 for record in records.values() if record.description),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
    }
    return candidate, info


def _as_bool(raw, *, default: bool, field: str = "") -> bool:
    """Parse a query-string boolean the way FastAPI parses a form one.

    Unset is *default*. A value that is not a boolean is refused with 422, as the same
    value in the form is: it used to fall back to *default*, so `?is_alto=ture` translated
    a metadata record as ALTO (atrium-project#72).
    """
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise HTTPException(status_code=422, detail=f"{field or 'The query value'} must be true or false, not {raw!r}.")


def _load_xpaths(path_value: str) -> list[str]:
    """Parse the XPath targets file, using the same rule as the CLI.

    Returns [] and logs rather than raising when the file is missing: the API image
    also serves ALTO mode, which needs no XPaths at all, so an absent file must not
    crash-loop a pod that is about to do perfectly valid work. Metadata requests are
    refused individually instead — see `translate_document`.
    """
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    if not candidate.is_file():
        logger.warning(
            "AMCR_FIELDS_PATH=%r does not resolve to a file (looked at %s); "
            "metadata-mode /translate requests will be refused.",
            path_value,
            candidate,
        )
        return []
    with open(candidate, "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Backend selected via the TRANSLATION_BACKEND env var (default: lindat).
    # Matches the CLI seam in main.py so the service can be pointed at the
    # OpenAI-compatible LLM backend without code changes (issue #4).
    backend = os.getenv("TRANSLATION_BACKEND")
    logger.info("Warming up translation backend (%s)", backend or "lindat")
    # The vocabulary first: a VOCABULARY_PATH that names no usable CSV stops the service here.
    vocab_path, models["vocabulary_info"] = _resolve_vocabulary(VOCABULARY_PATH)
    if vocab_path is not None:
        logger.info("Applying the vocabulary %s (%d terms)", vocab_path, models["vocabulary_info"]["terms"])
    models["translator"] = get_backend(backend, vocab_path=str(vocab_path) if vocab_path else None)
    # A backend with a model to load (ct2) loads it now: a configuration error stops the
    # service here instead of failing the first request, and /ready below means "loaded".
    warm = getattr(models["translator"], "warm", None)
    if callable(warm):
        logger.info("Loading the %s model", getattr(models["translator"], "name", backend))
        warm()
    models["identifier"] = LanguageIdentifier()
    # Metadata-mode XPath targets, read once here rather than per request — the same
    # warm-cache treatment the backend and identifier get. Before this existed the
    # endpoint passed a hard-coded empty list to process_single_file, so every
    # `is_alto=false` upload came back HTTP 200 with the document untranslated
    # (issue #46).
    models["xpaths_list"] = _load_xpaths(AMCR_FIELDS_PATH)
    logger.info("Loaded %d metadata XPath target(s) from %r", len(models["xpaths_list"]), AMCR_FIELDS_PATH)
    # The schema metadata translations are validated against: compiled once, as the CLI's --xsd is.
    models["xsd_schema"], models["xsd_info"] = _resolve_xsd(XSD_PATH)
    if models["xsd_schema"] is not None:
        logger.info("Validating metadata translations against %s", models["xsd_info"]["file"])
    _state.warm = True
    # issue #55: composes with the warmup above rather than replacing it. Flips /ready to
    # 503 on SIGTERM and — the reason ordering matters here — waits for in-flight requests
    # BEFORE the models.clear() below pulls the backend out from under a request that is
    # still translating.
    async with serve_lifecycle(_state):
        yield
    logger.info("Shutting down service")
    models.clear()


app = FastAPI(
    title="ATRIUM Translator API",
    description="Automated pipeline for the translation and enrichment of archaeological archival collections.",
    version=read_tool_version(Path(__file__).resolve().parent),
    lifespan=lifespan,
    # The typed contract (atrium-project#32 round 2): every route documents the §4.4 error
    # body for 422 and 500 (and FastAPI's own 422 body, which is not what is sent, goes);
    # operationIds are the handler names; the spec never depends on a root_path.
    responses=error_responses(422, 500),
    generate_unique_id_function=operation_id,
    root_path_in_servers=False,
)
attach_inflight_middleware(app, _state)


@app.middleware("http")
async def _refuse_oversized_translate_request(request: Request, call_next):
    """413 on a declared Content-Length past ``max_request_mb``, BEFORE the body is read.

    FastAPI parses (and Starlette spools to disk) the whole multipart form before the
    handler runs, so the check inside ``translate_document`` alone bounds what is
    processed, not what is received. Here it runs first. A request without a declared
    length is still bounded per part while its parts are read (``read_upload_bounded``).
    """
    if request.method == "POST" and request.url.path == "/translate":
        try:
            _reject_oversized_envelope(request)
        except LimitExceeded as exc:
            body = error_body(exc.http_status, exc.detail, "limit_exceeded", limit=jsonable_encoder(exc.to_dict()))
            return JSONResponse(body, status_code=exc.http_status)
    return await call_next(request)


# §4.4 error body {status, reason, detail} for every error (atrium-project#32 item 2, #53).
attach_error_handlers(app)
# The published spec: reason registry, record schema, service id (atrium-project#32 item 3).
attach_openapi_contract(app, SERVICE)

# CORS — standard §4.5 configuration (ALLOWED_ORIGINS CSV, default "*").
add_cors(app)


def _deep_health() -> str | None:
    """Deep readiness (§4.1): both backing models are usable, not merely present.

    The translator check alone was not enough. `LanguageIdentifier.__init__`
    downloads the FastText model from HuggingFace at container start, and used to
    swallow any failure: `self.model` became None, `detect()` then answered
    ("en", 0.0) for every document, and the service reported itself perfectly
    healthy while silently mislabelling the source language of everything it was
    given. In an egress-restricted cluster that download is precisely what fails,
    so the failure mode is the deployment we are asking ARUP/ARUB to run.

    Reported rather than fatal, deliberately: a deployment that always passes
    `--source_lang` never consults the identifier, and crash-looping it would be
    wrong. `GET /health?deep=true` is where a partner sees the degradation --
    `/health` (liveness) and `/ready` (routing) stay as they were.
    """
    if not models.get("translator"):
        return "translation backend not warmed up"

    identifier = models.get("identifier")
    load_error = getattr(identifier, "load_error", None)
    if load_error:
        return f"language identification model unavailable ({load_error}); source-language detection is degraded"

    return None


attach_health(app, deep_check=_deep_health, state=_state)


def _refuse_if_draining() -> None:
    """Reject NEW work once a shutdown signal has arrived (issue #55).

    /ready has already flipped to 503 by this point, but a request accepted before the
    orchestrator noticed can still reach a handler. Answering 503 here bounds the set of
    requests the drain must wait for — which matters most in this service, where a single
    /translate issues one retried LINDAT call per chunk and can run for minutes.
    """
    if _state.draining:
        raise HTTPException(status_code=503, detail="Service is shutting down; retry against a live replica.")


def _refuse_untranslatable_pair(source_lang: str, target_lang: str) -> None:
    """422 for a language pair the warmed backend cannot translate (translator#4).

    LINDAT used to translate such a request from Czech without a word. ``auto`` checks the
    language a segment falls back to (``DEFAULT_SOURCE_LANG``). No registered reason exists
    for it, so ``reason`` is null; ``cause`` names it and ``errors`` lists what is offered.
    """
    translator = models.get("translator")
    if translator is None:
        return
    field = "source_lang"
    pair_source = source_lang
    if source_lang == "auto":
        field, pair_source = "DEFAULT_SOURCE_LANG", SourceLanguagePolicy.from_env().default
    problem = language_pair_problem(translator, pair_source, target_lang)
    if problem:
        offered = allowed_source_languages(translator, target_lang)
        raise AtriumHTTPError(
            422,
            f"Cannot translate {pair_source} → {target_lang}: {problem}",
            reason=None,
            cause="unsupported_language_pair",
            errors=[
                {
                    "field": field,
                    "value": pair_source,
                    "target_lang": target_lang,
                    "supported_source_langs": sorted(offered) if offered else [],
                }
            ],
        )


# Opus 4.8 Hardening: Strict Content-Type Guards
async def verify_content_type(request: Request):
    """Ensure incoming POST requests provide acceptable payload formats."""
    if request.method in ("POST", "PUT"):
        content_type = request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json") and not content_type.startswith("multipart/form-data"):
            # §4.4: the registered reason and the accepted types (atrium-project#32 round 2).
            raise AtriumHTTPError(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Unsupported media type: {content_type}. Expected application/json or multipart/form-data.",
                reason="unsupported_media_type",
                accepted=["application/json", "multipart/form-data"],
            )


@app.post(
    "/translate",
    dependencies=[Depends(verify_content_type)],
    response_model=None,
    responses={200: {"model": TranslateResponse, **_TRANSLATE_200}, **error_responses(413, 415, 503)},
)
async def translate_document(
    request: Request,
    file: UploadFile = File(
        ...,
        description=(
            "The XML to translate, its name ending in `.xml`: an ALTO page, or a metadata record such as AMČR's. Its root "
            "element decides which, unless `is_alto` is sent."
        ),
    ),
    document_json: UploadFile = File(
        None,
        description=(
            "Optional baseline ATRIUM Document JSON (accretion model), or an AMČR seed (`doc_id`, `source`). When "
            "given, the record comes back with the translator's `translations` block added: as the second part of "
            "the multipart/mixed response, or as `document_json` with `response_format=json`. A record that does "
            "not validate against atrium_document.schema.json is still accepted (rule 6); one that cannot be "
            "opened is refused (422 `invalid_record`). An empty part counts as none."
        ),
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    # Declared as Form(None) and resolved against the query string below, because
    # callers are genuinely split and both shapes must keep working.
    #
    # A bare `is_alto: bool = True` on a POST binds from the QUERY STRING only, so
    # the `data={"is_alto": ...}` this repo's own tests send was silently discarded
    # and the default won — invisible because every test passed "true", which is
    # also the default. But other callers (test_translate_real_pipeline_keeps_the_
    # multi_dot_doc_id) pass `?source_lang=cs` in the query and rely on it being
    # read. Binding strictly to either source breaks the other half. (issue #46)
    source_lang: Optional[str] = Form(
        None, description="The source language, or `auto` (the default) to detect it. Also read from the query."
    ),
    target_lang: Optional[str] = Form(
        None, description="The target language; `en` by default. Also read from the query."
    ),
    is_alto: Optional[bool] = Form(
        None,
        description=(
            "`true`: ALTO XML; `false`: AMČR metadata XML. Unset (since v1.6.0-beta): the upload's root element "
            "decides, `alto` or any other. Until then unset meant `true`, so a metadata record sent without it came "
            "back untranslated. Also read from the query."
        ),
    ),
    fast_align: Optional[bool] = Form(
        None,
        description=(
            "ALTO only: distribute a block's translation over its lines by word counts instead of translating each "
            "line as an anchor (the command line's --fast-align): far fewer calls, coarser line splits. Default "
            "false. Also read from the query."
        ),
    ),
    xsd_strict: Optional[bool] = Form(
        None,
        description=(
            "With `XSD_PATH` set, for a metadata record: `true` refuses a translation that does not validate (422), "
            "`false` returns it with `xsd_valid: false`. Default: the `XSD_STRICT` setting. Also read from the query."
        ),
    ),
    include_log: Optional[bool] = Form(
        None,
        description=(
            "`true`: also return the translation log, the command line's `<doc>_log.csv` (`log_csv` with "
            "`response_format=json`, else a part of the multipart/mixed answer). Default false. Also read from "
            "the query."
        ),
    ),
    include_record: Optional[bool] = Form(
        None,
        description=(
            "`true`: return the document record even when none was sent: a new one, as the command line writes. "
            "Default false: a record only when `document_json` was sent. Also read from the query."
        ),
    ),
    # An open string, not an enum: any case is accepted, and an unknown value falls back to
    # the default with a warning (utils.normalize_output_mode) — an enum would refuse input
    # that works today (atrium-project#32 round 2, rule 2).
    output_mode: Optional[str] = Form(
        None,
        description=(
            "`replace` (the default, or the OUTPUT_MODE setting) or `append` — see issue #46. Also read from the query."
        ),
    ),
    response_format: Optional[ResponseFormat] = Form(
        None,
        description=(
            "`xml` (the default): the translated XML, or multipart/mixed when a record was sent or `include_record` "
            "or `include_log` is true. `json`: one `TranslateResponse` object with the XML as text, the record as "
            "`document_json` and the log as `log_csv`. Also read from the query."
        ),
    ),
):
    _refuse_if_draining()

    qp = request.query_params
    source_lang = source_lang or qp.get("source_lang") or "auto"
    target_lang = target_lang or qp.get("target_lang") or "en"
    if source_lang.strip().lower() != "auto":
        source_lang = normalise_lang_code(source_lang) or source_lang
    target_lang = normalise_lang_code(target_lang) or target_lang
    _refuse_untranslatable_pair(source_lang, target_lang)
    if is_alto is None and qp.get("is_alto") is not None:
        is_alto = _as_bool(qp.get("is_alto"), default=True, field="is_alto")
    if fast_align is None:
        fast_align = _as_bool(qp.get("fast_align"), default=False, field="fast_align")
    if xsd_strict is None:
        xsd_strict = _as_bool(qp.get("xsd_strict"), default=_xsd_strict_default(), field="xsd_strict")
    if include_log is None:
        include_log = _as_bool(qp.get("include_log"), default=False, field="include_log")
    if include_record is None:
        include_record = _as_bool(qp.get("include_record"), default=False, field="include_record")
    # The form field is validated by its enum; the query value is checked here, so both
    # sources refuse an unknown format alike (422) rather than falling back silently.
    response_format = response_format or qp.get("response_format") or "xml"
    if response_format not in ("xml", "json"):
        raise HTTPException(status_code=422, detail="response_format must be 'xml' or 'json'.")

    # CLI precedence, mirrored: explicit request field wins, then OUTPUT_MODE, then
    # the shipped default. An unrecognised value degrades to the default with a
    # warning rather than 4xx — the effective mode is recorded in paradata and in the
    # document record either way, so the run is never ambiguous about what it made.
    effective_output_mode = normalize_output_mode(
        output_mode or qp.get("output_mode") or os.getenv("OUTPUT_MODE") or DEFAULT_OUTPUT_MODE,
        source="output_mode",
    )

    upload_name = _safe_file_name(file.filename)
    if not upload_name:
        # §4.4: an unusable name (missing, a bare path, unprintable, over the length cap) is
        # unusable input, 422 — it cannot become a file in the request's directory.
        raise HTTPException(status_code=422, detail="The upload has no usable file name.")
    if not upload_name.endswith(".xml"):
        # §4.4: a file of a type this endpoint does not read is 415 `unsupported_media_type`
        # (a bare 422 before atrium-project#32 round 2), with the accepted suffix in the body.
        raise AtriumHTTPError(
            415,
            "Only XML files are supported.",
            reason="unsupported_media_type",
            accepted=ACCEPTED_SUFFIXES,
        )

    _reject_oversized_envelope(request)  # also in the middleware above; kept for direct calls
    upload_mb = MAX_UPLOAD.get()
    content = await read_upload_bounded(file, upload_mb, "File")

    # Unset is_alto: the root element decides, with the function the command line uses when no
    # mode flag is given (utils.xml_kind, atrium-project#72). Unset used to mean ALTO, so an AMČR
    # record sent without the field came back 200 and untranslated.
    detected_kind = None
    if is_alto is None:
        try:
            detected_kind = xml_kind(content)
        except ValueError as exc:
            raise AtriumHTTPError(
                422,
                f"The upload is not well-formed XML, so its kind cannot be told: {exc}",
                reason=None,
                cause="malformed_xml",
            ) from exc
        is_alto = detected_kind == XML_KIND_ALTO

    # Metadata mode with no XPath targets cannot translate anything. It used to
    # return 200 and an unchanged document, which is the worst possible answer: the
    # caller has no way to tell a successful no-op from a successful translation.
    # Refuse explicitly instead (issue #46).
    xpaths_list = models.get("xpaths_list") or []
    if not is_alto and not xpaths_list:
        raise HTTPException(
            status_code=422,
            detail=(
                "Metadata mode requires XPath targets, and none are configured. "
                f"Set AMCR_FIELDS_PATH (currently {AMCR_FIELDS_PATH!r}) to a readable "
                "file listing one XPath per line, or send is_alto=true for ALTO input."
            ),
        )

    # The record, read and opened before any translation, so one that cannot be opened is
    # refused up front (422 `invalid_record`, atrium-project#32 round 2): it used to reach
    # process_single_file, which logged a skip, and the endpoint answered a 500 "Translation
    # processing failed." An empty part counts as none. The bytes go on as sent.
    baseline_bytes = None
    if document_json is not None:
        raw = await read_upload_bounded(document_json, upload_mb, "Baseline document JSON")
        if parse_record_part(raw, "document_json") is not None:
            baseline_bytes = raw

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        input_path = work_dir / upload_name
        input_path.write_bytes(content)

        doc_json_path = None
        if baseline_bytes is not None:
            baseline_name = _safe_file_name(document_json.filename) or "baseline.json"
            if baseline_name == upload_name:
                baseline_name = "baseline.json"
            doc_json_path = work_dir / baseline_name
            doc_json_path.write_bytes(baseline_bytes)

        # D3/D11 (atrium-project#10): the same derivation process_single_file() uses, so the
        # filename this endpoint promises the client and the doc_id the record is keyed on
        # cannot diverge. `filename.split('.')[0]` truncated at the FIRST dot, so an upload
        # named `CTX01.v2.alto.xml` was answered with `CTX01.document.json` while the record
        # inside it said `CTX01.v2` — and the accreted record was then looked up under the
        # wrong id by the next stage. Original case is preserved deliberately: nothing else in
        # the pipeline lower-cases a doc_id.
        #
        # It reads the BASELINE, which is why it must run after the part above is on disk: an
        # upload is as likely as a CLI run to be one page of a document (`<doc>-1.alto.xml`),
        # and for those the uploaded filename is not what the record is keyed on. Deriving
        # from `file.filename` alone would reintroduce exactly the divergence this comment
        # was written about, one level further out.
        #
        # The id can come from the client's baseline, so it is checked before it becomes a
        # path; an id that is not a plain file name falls back to the upload's own id. The
        # record inside keeps the baseline's doc_id either way.
        record_name = _safe_file_name(f"{record_doc_id(input_path, doc_json_path)}.document.json")
        doc_json_out_path = work_dir / (record_name or f"{canonical_doc_id(input_path)}.document.json")

        output_dir = work_dir / "output"
        output_dir.mkdir()

        # The backend that actually warmed up in lifespan(), read once and threaded into BOTH
        # the args Namespace and the paradata config below.
        #
        # `backend=` is not optional: process_single_file() passes `args.backend` down to
        # process_alto_xml/process_metadata_xml, which stamp it into `translations.backend`.
        # Without the field, the very first REAL request raised AttributeError inside
        # process_single_file's catch-all, which logged a skip and returned success=False —
        # so the endpoint answered 500 for every upload. Nothing in CI could see it: all
        # /translate tests mock process_single_file, exactly the blindness that let alto's J1
        # ship (atrium-project#10 review pass; found while landing D3/D4).
        backend_name = models["translator"].name

        xsd_schema = models.get("xsd_schema") if not is_alto else None
        xsd_info = models.get("xsd_info") or {}
        args = argparse.Namespace(
            source_lang=source_lang,
            target_lang=target_lang,
            alto=is_alto,
            fast_align=fast_align,
            xsd=xsd_info.get("file") if xsd_schema is not None else None,
            document_json=doc_json_path,
            document_json_out=doc_json_out_path,
            backend=backend_name,
            output_mode=effective_output_mode,
        )

        # The same name the command line gives the translated file (main.translated_filename).
        out_filename = translated_filename(input_path.name, target_lang)
        output_path = output_dir / out_filename

        # The run's parameters through the command line's builder (atrium-project#72): the same
        # keys in both records. The endpoint or model is read off the backend that warmed up —
        # the object that issues the requests — and the accepted source languages are that
        # backend's, so a /translate run and a batch run are comparable.
        vocabulary_info = models.get("vocabulary_info") or {}
        para_config = build_run_config(
            backend_name=backend_name,
            translator=models["translator"],
            source_lang=source_lang,
            target_lang=target_lang,
            mode="detect" if detected_kind else ("alto" if is_alto else "metadata"),
            output_mode=effective_output_mode,
            allowed_languages=allowed_source_languages(models["translator"], target_lang),
            input_path=upload_name,
            formats="xml",
            xpaths_file="" if is_alto else AMCR_FIELDS_PATH,
            xsd=args.xsd or "",
            xsd_strict=bool(xsd_strict) if xsd_schema is not None else False,
            fast_align=fast_align,
            vocabulary=vocabulary_info.get("file", ""),
            vocabulary_sha256=vocabulary_info.get("sha256", ""),
        )
        if detected_kind:
            para_config["detected_kind"] = {canonical_doc_id(input_path): detected_kind}

        # The call's paradata (atrium-project#71). paradata_dir=None: a service writes no
        # paradata file; the run goes back as the response's `paradata`, and its run_id /
        # run_uuid stamp the record. config_dir: para_config.txt sits at the repo root, found
        # from here rather than from whatever the working directory happens to be.
        with ParadataLogger(
            program="translator-api",
            config=para_config,
            paradata_dir=None,
            output_types=["xml", "csv", "json"],
            config_dir=_PARA_CONFIG_DIR,
        ) as logger:
            # Off the event loop (issue #55): process_single_file() chunks the document
            # and issues one RETRIED, blocking HTTP call to LINDAT per chunk — a single
            # request can legitimately run for minutes. Called inline in an `async def`
            # it held the ONLY event loop for that whole time, so uvicorn's SIGTERM
            # handler (an event-loop callback) could not run at all and
            # --timeout-graceful-shutdown had nothing to measure.
            xsd_verdicts: Dict[str, Any] = {}
            success, protected = await asyncio.to_thread(
                process_single_file,
                file_path=input_path,
                output_file=output_path,
                args=args,
                translator=models["translator"],
                identifier=models["identifier"] if source_lang == "auto" else None,
                xpaths_list=xpaths_list,
                _logger=logger,
                xsd_schema=xsd_schema,
                xsd_verdicts=xsd_verdicts,
            )

            # API-path paradata component logging (the same helper as main.py, M1).
            # process_single_file() already logged them before the returned record
            # took its licence block; this keeps the run's own paradata complete.
            if success:
                log_backend_components(models["translator"], logger, detected=source_lang == "auto")

            # The keys the command line adds once a document is done (main.run_outcome_config).
            run_config = getattr(logger, "config", None)
            if isinstance(run_config, dict):
                doc_key = record_doc_id(input_path, doc_json_path)
                degenerate = degenerate_replies(models["translator"])
                run_config.update(
                    run_outcome_config(
                        protected_by_doc={doc_key: protected}
                        if getattr(models["translator"], "vocabulary", None)
                        else None,
                        degenerate_by_doc={doc_key: degenerate} if degenerate is not None else None,
                        xsd_verdicts=xsd_verdicts if xsd_schema is not None else None,
                        xsd_strict=bool(xsd_strict),
                    )
                )

        # The limits that shaped this translation without refusing it (atrium-project#53):
        # recorded in the run's paradata by process_single_file, and echoed to the caller below
        # on their own, since a bare XML answer carries no paradata.
        limit_notes = LimitNotes(logger.limits_applied)

        if not success:
            raise HTTPException(status_code=500, detail="Translation processing failed.")

        # The XSD verdict, as the command line reports it (--xsd, --xsd-strict).
        xsd_valid = None
        xsd_errors = None
        if xsd_schema is not None and xsd_verdicts:
            xsd_valid = bool(next(iter(xsd_verdicts.values()))[1])
            if not xsd_valid:
                xsd_errors = _xsd_errors(output_path, xsd_schema)
                if xsd_strict:
                    raise AtriumHTTPError(
                        422,
                        f"The translation does not validate against {xsd_info.get('file')}: {xsd_errors}",
                        reason=None,
                        cause="xsd_validation_failed",
                    )

        # C1: read into memory while the TemporaryDirectory is still open.
        # FileResponse streams lazily *after* the context exits, so the tmpdir
        # is already deleted before the first byte is sent — returning an
        # in-memory Response eliminates that race entirely.
        with open(output_path, "rb") as fh:
            xml_bytes = fh.read()

        json_bytes = None
        # The record goes back when one was sent, or when asked for (include_record): the command
        # line writes one either way, and so did this endpoint, only to discard it.
        if (baseline_bytes is not None or include_record) and doc_json_out_path.exists():
            with open(doc_json_out_path, "rb") as fh:
                json_bytes = fh.read()
        record_schema_error = _record_schema_error(json_bytes) if json_bytes else None

        # The translation log, as the command line writes it beside the XML.
        log_name = None
        log_bytes = None
        if include_log:
            log_path = output_path.with_name(f"{canonical_doc_id(input_path)}_log.csv")
            if log_path.is_file():
                log_name, log_bytes = log_path.name, log_path.read_bytes()

    # The run as its CreateAction (atrium-project#71), in the two shapes that have a place for it.
    action = None
    if response_format == "json" or json_bytes or log_bytes:
        action = _run_action(
            logger, upload_name, content, baseline_bytes is not None, json_bytes, out_filename, xml_bytes
        )

    # The limits echo (atrium-project#53). The response is XML, so the notes travel as an
    # ASCII header — `key=effect:count; …`, present only when a limit applied (header values
    # are latin-1, and a note's detail may be Czech) — and, in the multipart form, in full
    # as a third part, `limits_applied.json`, always present (`[]` when nothing applied).
    limits_header = limit_notes.header_summary()
    extra_headers = {LIMITS_HEADER: limits_header} if limits_header else {}
    if xsd_valid is not None:
        extra_headers[XSD_HEADER] = "true" if xsd_valid else "false"

    # response_format=json (atrium-project#32 round 2): the same result as one typed JSON
    # object — the shape a client generated from the spec reads without a multipart parser.
    # The XML is UTF-8 (utils writes it so); the record is the same bytes the multipart
    # form carries.
    if response_format == "json":
        payload: Dict[str, Any] = {
            "type": "alto" if is_alto else "metadata",
            "filename": out_filename,
            "media_type": "application/xml",
            "content": xml_bytes.decode("utf-8"),
            "limits_applied": limit_notes.as_list(),
            "paradata": action,
        }
        if json_bytes:
            payload["document_json"] = json.loads(json_bytes)
        if record_schema_error:
            payload["document_json_schema_error"] = record_schema_error
        if log_bytes is not None:
            payload["log_filename"] = log_name
            payload["log_csv"] = log_bytes.decode("utf-8")
        if xsd_valid is not None:
            payload["xsd_valid"] = xsd_valid
            payload["xsd_errors"] = xsd_errors
        return JSONResponse(payload, headers=extra_headers)

    # Deliver multipart/mixed response if document_json is active and generated, allowing
    # clients to retrieve both the updated ATRIUM Document JSON and the resulting ALTO XML.
    if json_bytes or log_bytes is not None:
        boundary = uuid.uuid4().hex
        headers = {"Content-Type": f"multipart/mixed; boundary={boundary}", **extra_headers}
        notes_bytes = json.dumps(limit_notes.as_list(), ensure_ascii=False).encode("utf-8")
        action_bytes = json.dumps(action, ensure_ascii=False).encode("utf-8")

        def generate_multipart():
            yield f"--{boundary}\r\n".encode()
            yield b"Content-Type: application/xml\r\n"
            yield f'Content-Disposition: attachment; filename="{out_filename}"\r\n\r\n'.encode()
            yield xml_bytes + b"\r\n"
            if json_bytes:
                yield f"--{boundary}\r\n".encode()
                yield b"Content-Type: application/json\r\n"
                yield f'Content-Disposition: attachment; filename="{doc_json_out_path.name}"\r\n\r\n'.encode()
                yield json_bytes + b"\r\n"
            if log_bytes is not None:
                yield f"--{boundary}\r\n".encode()
                yield b"Content-Type: text/csv; charset=utf-8\r\n"
                yield f'Content-Disposition: attachment; filename="{log_name}"\r\n\r\n'.encode()
                yield log_bytes + b"\r\n"
            yield f"--{boundary}\r\n".encode()
            yield b"Content-Type: application/json\r\n"
            yield b'Content-Disposition: attachment; filename="limits_applied.json"\r\n\r\n'
            yield notes_bytes + b"\r\n"
            yield f"--{boundary}\r\n".encode()
            yield b"Content-Type: application/ld+json\r\n"
            yield b'Content-Disposition: attachment; filename="paradata.json"\r\n\r\n'
            yield action_bytes + b"\r\n"
            yield f"--{boundary}--\r\n".encode()

        return StreamingResponse(generate_multipart(), headers=headers)

    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{out_filename}"', **extra_headers},
    )


def _xsd_errors(output_path: Path, schema) -> str:
    """The validator's messages for a translated file, as ``utils.validate_xml_with_xsd`` gives them."""
    from lxml import etree

    try:
        tree = etree.parse(str(output_path), etree.XMLParser(resolve_entities=False, no_network=True))
    except Exception as exc:  # the translation was written by this service; unreadable is a defect
        return f"the translated file cannot be read back: {exc}"
    _, errors = validate_xml_with_xsd(tree, schema)
    return errors


def _record_schema_error(record_bytes: bytes) -> Optional[str]:
    """The schema error of the record this call returns, or None (atrium-project#72: reported as
    `document_json_schema_error`, as the other services report it). A record whose own blocks are
    invalid never gets here: process_single_file refuses to write it; what remains is a baseline
    that came in invalid and is passed through (rule 6)."""
    from atrium_document import validate_document

    try:
        validate_document(json.loads(record_bytes))
    except (RuntimeError, FileNotFoundError):
        return None  # validation unavailable (jsonschema or the schema missing): logged elsewhere
    except Exception as exc:
        return str(exc)
    return None


def _run_action(
    run: ParadataLogger,
    upload_name: str,
    content: bytes,
    baseline_sent: bool,
    record_bytes: Optional[bytes],
    out_filename: str,
    xml_bytes: bytes,
) -> Dict[str, Any]:
    """The call's CreateAction (atrium-project#71): what it read and what it wrote.

    `object` is the upload and, when one was sent, the record; `result` is the record's blocks
    this call stamped and the translated XML.
    """
    inputs = [atrium_rocrate.file_entity(upload_name, content, media_type="application/xml")]
    outputs: List[Dict[str, Any]] = []
    if baseline_sent:
        record = json.loads(record_bytes) if record_bytes else {}
        inputs.append(atrium_rocrate.record_entity(str(record.get("doc_id") or canonical_doc_id(upload_name))))
        outputs = atrium_rocrate.block_entities(atrium_rocrate.blocks_written(record, run.run_uuid))
    outputs.append(atrium_rocrate.file_entity(out_filename, xml_bytes, media_type="application/xml"))
    return atrium_rocrate.create_action(run.record, inputs=inputs, outputs=outputs)


@app.get(
    "/info",
    response_model=None,
    responses={200: {"model": TranslatorInfo, "description": "Identity, limits, capabilities."}},
)
async def get_info():
    return build_info(
        app,
        service=SERVICE,
        limits=LIMITS,
        supported_formats=["ALTO XML", "AMCR Metadata XML"],
        vocabulary=models.get("vocabulary_info"),
        xsd=models.get("xsd_info"),
    )


if __name__ == "__main__":
    import logging
    import os
    import sys

    import uvicorn

    # (12-factor XI) Logs are an event stream: emit to stdout and let the supervisor
    # route them. The library modules only getLogger(); this is the one place allowed
    # to configure handlers. The format string is alto-postprocess's, verbatim, in all
    # five services — a partner tailing five logs wants one shape, and format drift is
    # never fixed later. (issue #61)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # (12-factor VII) The service exports itself by binding a port, and which port is
    # configuration. This was baked into an exec-form ENTRYPOINT array, where no shell
    # exists to expand a variable even if one is set — while the reference manifest we
    # hand ARÚP/ARÚB (atrium-project docs/templates/k8s/atrium-service.deployment.yaml)
    # declares `env: PORT` and service/healthcheck.py already reads it. Setting PORT
    # therefore moved the health PROBE and not the listener, so the container reported
    # unhealthy forever rather than simply ignoring the knob. (issue #58)
    reload = os.getenv("RELOAD", "false").strip().lower() in ("true", "1", "yes", "on")

    # uvicorn needs an IMPORT STRING to respawn workers on reload; everywhere else the
    # app OBJECT is correct and strictly better. Passing a string under the container
    # entrypoint (`python -m service.api`) re-imports this module under its real name
    # while it is already running as __main__: the whole body executes twice, and the
    # copy uvicorn serves is not the one __main__ built. __spec__ is None under a direct
    # `python api.py` from service/ (service/README.md's documented start), where no
    # import string resolves anyway — so reload degrades to a uvicorn warning there
    # instead of silently pretending to be on.
    _app_ref = f"{__spec__.name}:app" if reload and __spec__ is not None else app

    uvicorn.run(
        _app_ref,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=reload,
        # (12-factor IX) Disposability: this is the `--timeout-graceful-shutdown 20`
        # that moved off the ENTRYPOINT line when the port became configurable. It
        # bounds uvicorn's wait for in-flight requests; serve_lifecycle() adds its own
        # drain on top, and docs/k8s_deployment.md in the hub carries the full grace
        # budget the two have to fit inside. (issue #55)
        timeout_graceful_shutdown=int(os.getenv("GRACEFUL_SHUTDOWN_S", "20")),
    )
