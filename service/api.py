"""
service/api.py

FastAPI service for the ATRIUM LINDAT Translator.
Brings this repository into API parity with the rest of the ATRIUM pipeline.
"""

import argparse
import asyncio
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from atrium_paradata import ParadataLogger
from main import process_single_file, record_doc_id
from processors.backend import get_backend
from processors.chunking import DEFAULT_CHUNK_SIZE
from processors.identifier import LanguageIdentifier

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

# Canonical upload limit (§4.5): MAX_UPLOAD_MB, with a deprecated MAX_UPLOAD_BYTES fallback.
MAX_UPLOAD_MB = resolve_max_upload_mb(50)
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)  # retained: imported by tests/clients


models = {}

#: Readiness/draining/in-flight state for the §4.6 disposability contract (issue #55).
_state = ServiceState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Backend selected via the TRANSLATION_BACKEND env var (default: lindat).
    # Matches the CLI seam in main.py so the service can be pointed at the
    # OpenAI-compatible LLM backend without code changes (issue #4).
    backend = os.getenv("TRANSLATION_BACKEND")
    print(f"[INFO] Warming up translation backend ({backend or 'lindat'})...")
    models["translator"] = get_backend(backend, vocab_path=None)
    models["identifier"] = LanguageIdentifier()
    _state.warm = True
    # issue #55: composes with the warmup above rather than replacing it. Flips /ready to
    # 503 on SIGTERM and — the reason ordering matters here — waits for in-flight requests
    # BEFORE the models.clear() below pulls the backend out from under a request that is
    # still translating.
    async with serve_lifecycle(_state):
        yield
    print("[INFO] Shutting down service...")
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
    """Deep readiness (§4.1): the translation backend has warmed up."""
    if not models.get("translator"):
        return "translation backend not warmed up"
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
    source_lang: str = "auto",
    target_lang: str = "en",
    is_alto: bool = True,
):
    _refuse_if_draining()

    if not file.filename or not file.filename.endswith(".xml"):
        # §4.4: unusable/invalid input is 422 (harmonized from 400).
        raise HTTPException(status_code=422, detail="Only XML files are supported.")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Max size is {MAX_UPLOAD_BYTES} bytes.")

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        input_path = work_dir / file.filename
        input_path.write_bytes(content)

        doc_json_path = None
        if document_json:
            doc_json_path = work_dir / (document_json.filename or "baseline.json")
            doc_json_path.write_bytes(await document_json.read())

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
            "chunk_limit": DEFAULT_CHUNK_SIZE,
            "translation_backend": backend_name,
        }
        # Only record the hardcoded LINDAT URL when the active backend is
        # actually lindat — avoids misrepresenting LLM / CT2 runs (M1).
        if backend_name == "lindat":
            para_config["translation_api"] = "https://lindat.mff.cuni.cz/services/translation/api/v2/"

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
                xpaths_list=[],
                _logger=logger,
            )

            # API-path paradata component logging (mirrors main.py logic, M1).
            if success:
                vocab_loaded = bool(getattr(models["translator"], "vocabulary", None))
                components_fn = getattr(models["translator"], "license_components", None)
                if callable(components_fn):
                    for comp in components_fn(vocab_loaded):
                        logger.log_component(comp)
                else:
                    logger.log_component("lindat_cubbitt")
                    if vocab_loaded:
                        for comp in ("udpipe2_engine", "udpipe2_models", "amcr_vocab", "teater_data"):
                            logger.log_component(comp)

                if source_lang == "auto":
                    logger.log_component("fasttext")

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
