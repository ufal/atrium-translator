"""
service/api.py

FastAPI service for the ATRIUM LINDAT Translator.
Brings this repository into API parity with the rest of the ATRIUM pipeline.
"""

import argparse
import asyncio
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from atrium_paradata import ParadataLogger
from main import log_backend_components, process_single_file, record_doc_id
from processors.backend import get_backend
from processors.chunking import DEFAULT_CHUNK_SIZE
from processors.identifier import LanguageIdentifier
from processors.language import SourceLanguagePolicy, allowed_source_languages
from processors.translator import resolve_translation_url
from utils import DEFAULT_OUTPUT_MODE, normalize_output_mode

# Shared ATRIUM meta-contract helpers (§4). Byte-identical across every service,
# enforced by para-drift.reusable.yml.
try:
    from .atrium_service import (
        ServiceState,
        add_cors,
        attach_health,
        attach_inflight_middleware,
        build_info,
        read_tool_version,
        resolve_max_upload_mb,
        serve_lifecycle,
    )
except ImportError:
    from atrium_service import (
        ServiceState,
        add_cors,
        attach_health,
        attach_inflight_middleware,
        build_info,
        read_tool_version,
        resolve_max_upload_mb,
        serve_lifecycle,
    )

logger = logging.getLogger(__name__)

# Canonical upload limit (§4.5): MAX_UPLOAD_MB, with a deprecated MAX_UPLOAD_BYTES fallback.
MAX_UPLOAD_MB = resolve_max_upload_mb(50)
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)  # retained: imported by tests/clients

# Read uploads a megabyte at a time so the limit is enforced DURING the read.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

# Ceiling on the whole multipart envelope, checked against Content-Length before
# the body is touched at all. /translate accepts two file parts (the XML and an
# optional baseline document JSON), so a legitimate envelope can be about twice
# the per-file limit; the extra megabyte covers multipart boundaries and headers.
# This is a coarse early reject, not the real limit -- _read_bounded() below is.
MAX_REQUEST_BYTES = 2 * MAX_UPLOAD_BYTES + _UPLOAD_CHUNK_BYTES


def _reject_oversized_envelope(request: Request) -> None:
    """413 on a declared Content-Length past MAX_REQUEST_BYTES, before reading.

    Starlette spools a multipart part to a temporary FILE once it grows past its
    own in-memory threshold, so an unbounded upload fills the container's disk
    during parsing -- before any handler code runs. A declared length is a hint
    (it can be absent, and it can lie), which is why this only supplements the
    per-part accounting in _read_bounded().
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Request too large. Max upload size is {MAX_UPLOAD_BYTES} bytes per file.",
        )


async def _read_bounded(upload: UploadFile, limit_bytes: int, label: str) -> bytes:
    """Read *upload* fully, raising 413 as soon as it exceeds *limit_bytes*.

    The obvious form -- `content = await upload.read()` and then check
    `len(content)` -- decides whether the upload was too large only after the
    whole of it is resident in memory, so the 413 it raises is unreachable for
    exactly the inputs that need it: an unauthenticated caller could OOM-kill
    the container before the check ran. Reading in bounded chunks and stopping
    at the limit costs one extra join and makes the limit real.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label} too large. Max size is {limit_bytes} bytes.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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


def _as_bool(raw, *, default: bool) -> bool:
    """Parse a query-string boolean the way FastAPI parses a form one.

    Kept deliberately narrow: anything unrecognised falls back to *default* rather
    than raising, so a typo in a query string cannot 500 a request that would
    otherwise have run in the default mode.
    """
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


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
    models["translator"] = get_backend(backend, vocab_path=None)
    models["identifier"] = LanguageIdentifier()
    # Metadata-mode XPath targets, read once here rather than per request — the same
    # warm-cache treatment the backend and identifier get. Before this existed the
    # endpoint passed a hard-coded empty list to process_single_file, so every
    # `is_alto=false` upload came back HTTP 200 with the document untranslated
    # (issue #46).
    models["xpaths_list"] = _load_xpaths(AMCR_FIELDS_PATH)
    logger.info("Loaded %d metadata XPath target(s) from %r", len(models["xpaths_list"]), AMCR_FIELDS_PATH)
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
)
attach_inflight_middleware(app, _state)

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


# Opus 4.8 Hardening: Strict Content-Type Guards
async def verify_content_type(request: Request):
    """Ensure incoming POST requests provide acceptable payload formats."""
    if request.method in ("POST", "PUT"):
        content_type = request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json") and not content_type.startswith("multipart/form-data"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported media type: {content_type}. Expected application/json or multipart/form-data.",
            )


@app.post("/translate", dependencies=[Depends(verify_content_type)])
async def translate_document(
    request: Request,
    file: UploadFile = File(...),
    document_json: UploadFile = File(None, description="Optional baseline ATRIUM Document JSON (accretion model)"),
    # Declared as Form(None) and resolved against the query string below, because
    # callers are genuinely split and both shapes must keep working.
    #
    # A bare `is_alto: bool = True` on a POST binds from the QUERY STRING only, so
    # the `data={"is_alto": ...}` this repo's own tests send was silently discarded
    # and the default won — invisible because every test passed "true", which is
    # also the default. But other callers (test_translate_real_pipeline_keeps_the_
    # multi_dot_doc_id) pass `?source_lang=cs` in the query and rely on it being
    # read. Binding strictly to either source breaks the other half. (issue #46)
    source_lang: str = Form(None),
    target_lang: str = Form(None),
    is_alto: bool = Form(None),
    output_mode: str = Form(None, description="replace | append — see issue #46"),
):
    _refuse_if_draining()

    qp = request.query_params
    source_lang = source_lang or qp.get("source_lang") or "auto"
    target_lang = target_lang or qp.get("target_lang") or "en"
    if is_alto is None:
        is_alto = _as_bool(qp.get("is_alto"), default=True)

    # CLI precedence, mirrored: explicit request field wins, then OUTPUT_MODE, then
    # the shipped default. An unrecognised value degrades to the default with a
    # warning rather than 4xx — the effective mode is recorded in paradata and in the
    # document record either way, so the run is never ambiguous about what it made.
    effective_output_mode = normalize_output_mode(
        output_mode or qp.get("output_mode") or os.getenv("OUTPUT_MODE") or DEFAULT_OUTPUT_MODE,
        source="output_mode",
    )

    if not file.filename or not file.filename.endswith(".xml"):
        # §4.4: unusable/invalid input is 422 (harmonized from 400).
        raise HTTPException(status_code=422, detail="Only XML files are supported.")

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

    _reject_oversized_envelope(request)
    content = await _read_bounded(file, MAX_UPLOAD_BYTES, "File")

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        input_path = work_dir / file.filename
        input_path.write_bytes(content)

        doc_json_path = None
        if document_json:
            doc_json_path = work_dir / (document_json.filename or "baseline.json")
            doc_json_path.write_bytes(await _read_bounded(document_json, MAX_UPLOAD_BYTES, "Baseline document JSON"))

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
        doc_json_out_path = work_dir / f"{record_doc_id(input_path, doc_json_path)}.document.json"

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

        args = argparse.Namespace(
            source_lang=source_lang,
            target_lang=target_lang,
            alto=is_alto,
            fast_align=False,
            xsd=None,
            document_json=doc_json_path,
            document_json_out=doc_json_out_path,
            backend=backend_name,
            output_mode=effective_output_mode,
        )

        # ALTO vs standard XML naming preservation
        if input_path.name.endswith(".alto.xml"):
            out_filename = f"{input_path.name[: -len('.alto.xml')]}_{target_lang}.alto.xml"
        else:
            out_filename = f"{input_path.stem}_{target_lang}{input_path.suffix}"

        output_path = output_dir / out_filename

        para_config = {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "mode": "alto" if is_alto else "metadata",
            "output_mode": effective_output_mode,
            "chunk_limit": DEFAULT_CHUNK_SIZE,
            "translation_backend": backend_name,
        }
        # The source-language policy in force (processors/language.py) — the same keys
        # the CLI records, so a /translate run and a batch run are comparable. The
        # accepted-language set is derived from the backend that actually warmed up.
        para_config.update(
            SourceLanguagePolicy.from_env(
                allowed=allowed_source_languages(models["translator"], target_lang)
            ).describe()
        )
        # Only record the translation endpoint when the active backend is
        # actually lindat — avoids misrepresenting LLM / CT2 runs (M1) — and
        # record the endpoint the warmed backend will ACTUALLY call rather than
        # a literal (atrium-project#63). Now that the host is configurable, a
        # repeated literal would eventually name somewhere the request never
        # went, and paradata is this project's provenance claim: a record that
        # is confidently wrong is a data-integrity defect, not a cosmetic one.
        #
        # Read off the live instance first — it is the same object that issues
        # the requests, so the two cannot diverge. resolve_translation_url()
        # covers a backend that exposes no base_url (a test double), and returns
        # what the real backend would have resolved. The trailing slash keeps
        # the shape this field has carried since it was introduced.
        if backend_name == "lindat":
            effective_url = getattr(models["translator"], "base_url", None)
            if not isinstance(effective_url, str) or not effective_url.strip():
                effective_url = resolve_translation_url()
            para_config["translation_api"] = effective_url.rstrip("/") + "/"

        with ParadataLogger(
            program="translator-api",
            config=para_config,
            paradata_dir=str(output_dir / "paradata"),
            output_types=["xml", "csv", "json"],
        ) as logger:
            # Off the event loop (issue #55): process_single_file() chunks the document
            # and issues one RETRIED, blocking HTTP call to LINDAT per chunk — a single
            # request can legitimately run for minutes. Called inline in an `async def`
            # it held the ONLY event loop for that whole time, so uvicorn's SIGTERM
            # handler (an event-loop callback) could not run at all and
            # --timeout-graceful-shutdown had nothing to measure.
            success, _ = await asyncio.to_thread(
                process_single_file,
                file_path=input_path,
                output_file=output_path,
                args=args,
                translator=models["translator"],
                identifier=models["identifier"] if source_lang == "auto" else None,
                xpaths_list=xpaths_list,
                _logger=logger,
            )

            # API-path paradata component logging (the same helper as main.py, M1).
            # process_single_file() already logged them before the returned record
            # took its licence block; this keeps the run's own paradata complete.
            if success:
                log_backend_components(models["translator"], logger, detected=source_lang == "auto")

        if not success:
            raise HTTPException(status_code=500, detail="Translation processing failed.")

        # C1: read into memory while the TemporaryDirectory is still open.
        # FileResponse streams lazily *after* the context exits, so the tmpdir
        # is already deleted before the first byte is sent — returning an
        # in-memory Response eliminates that race entirely.
        with open(output_path, "rb") as fh:
            xml_bytes = fh.read()

        json_bytes = None
        # Only attach the multipart JSON response if the client opted into the flow
        if document_json and doc_json_out_path.exists():
            with open(doc_json_out_path, "rb") as fh:
                json_bytes = fh.read()

    # Deliver multipart/mixed response if document_json is active and generated, allowing
    # clients to retrieve both the updated ATRIUM Document JSON and the resulting ALTO XML.
    if json_bytes:
        boundary = uuid.uuid4().hex
        headers = {"Content-Type": f"multipart/mixed; boundary={boundary}"}

        def generate_multipart():
            yield f"--{boundary}\r\n".encode()
            yield b"Content-Type: application/xml\r\n"
            yield f'Content-Disposition: attachment; filename="{out_filename}"\r\n\r\n'.encode()
            yield xml_bytes + b"\r\n"
            yield f"--{boundary}\r\n".encode()
            yield b"Content-Type: application/json\r\n"
            yield f'Content-Disposition: attachment; filename="{doc_json_out_path.name}"\r\n\r\n'.encode()
            yield json_bytes + b"\r\n"
            yield f"--{boundary}--\r\n".encode()

        return StreamingResponse(generate_multipart(), headers=headers)

    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
    )


@app.get("/info")
async def get_info():
    return build_info(
        app,
        service="atrium-translator",
        limits={"max_upload_mb": MAX_UPLOAD_MB},
        supported_formats=["ALTO XML", "AMCR Metadata XML"],
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
