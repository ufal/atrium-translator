"""
service/api.py

FastAPI service for the ATRIUM LINDAT Translator.
Brings this repository into API parity with the rest of the ATRIUM pipeline.
"""

import argparse
import configparser
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from atrium_paradata import ParadataLogger
from main import process_single_file
from processors.backend import get_backend
from processors.chunking import DEFAULT_CHUNK_SIZE
from processors.identifier import LanguageIdentifier

# Canonical upload limit (family standard): MAX_UPLOAD_MB, with the historical
# MAX_UPLOAD_BYTES kept as a deprecated fallback for one release. Default 50 MB.
if "MAX_UPLOAD_MB" in os.environ:
    MAX_UPLOAD_MB = int(os.environ["MAX_UPLOAD_MB"])
else:
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024)) // (1024 * 1024)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

SERVICE_NAME = "atrium-translator"
API_ENDPOINTS = ["/info", "/health", "/translate"]


def _read_tool_version() -> str:
    """Read the tool version from para_config.txt [tool] section.

    Single source of truth — security.reusable.yml already validates this value
    against CITATION.cff and the release tag, so the API version can never drift
    from the released version again.
    """
    config = configparser.ConfigParser()
    config.read(Path(__file__).resolve().parent.parent / "para_config.txt", encoding="utf-8")
    version = config.get("tool", "version", fallback="unknown")
    return version[1:] if version.lower().startswith("v") else version


models = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Backend selected via the TRANSLATION_BACKEND env var (default: lindat).
    # Matches the CLI seam in main.py so the service can be pointed at the
    # OpenAI-compatible LLM backend without code changes (issue #4).
    backend = os.getenv("TRANSLATION_BACKEND")
    print(f"[INFO] Warming up translation backend ({backend or 'lindat'})...")
    models["translator"] = get_backend(backend, vocab_path=None)
    models["identifier"] = LanguageIdentifier()
    yield
    print("[INFO] Shutting down service...")
    models.clear()


app = FastAPI(
    title="ATRIUM Translator API",
    description="Automated pipeline for the translation and enrichment of archaeological archival collections.",
    version=_read_tool_version(),
    lifespan=lifespan,
)

# Opus 4.8 Hardening: Restrictive CORS Configuration
# Mirrors the atrium-page-classification exemplar: a wildcard origin must not be
# combined with credentials (browsers reject that pairing); methods narrowed to
# the family standard (GET/POST) rather than a wildcard.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
if "*" in ALLOWED_ORIGINS and os.getenv("ALLOW_CREDENTIALS", "true").lower() == "true":
    ALLOWED_ORIGINS.remove("*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOWED_ORIGINS != ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


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
    source_lang: str = "auto",
    target_lang: str = "en",
    is_alto: bool = True,
):
    if not file.filename or not file.filename.endswith(".xml"):
        raise HTTPException(status_code=422, detail="Only XML files are supported.")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Max size is {MAX_UPLOAD_MB} MB.")

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        input_path = work_dir / file.filename
        input_path.write_bytes(content)

        output_dir = work_dir / "output"
        output_dir.mkdir()

        args = argparse.Namespace(
            source_lang=source_lang, target_lang=target_lang, alto=is_alto, fast_align=False, xsd=None
        )

        # ALTO vs standard XML naming preservation
        if input_path.name.endswith(".alto.xml"):
            out_filename = f"{input_path.name[: -len('.alto.xml')]}_{target_lang}.alto.xml"
        else:
            out_filename = f"{input_path.stem}_{target_lang}{input_path.suffix}"

        output_path = output_dir / out_filename

        backend_name = models["translator"].name
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
            output_types=["xml", "csv"],
        ) as logger:
            success, _ = process_single_file(
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
            content_bytes = fh.read()

    return Response(
        content=content_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
    )


@app.get("/info")
async def get_info():
    translator = models.get("translator")
    return {
        "service": SERVICE_NAME,
        "name": "ATRIUM Translator Service",
        "version": app.version,
        "endpoints": API_ENDPOINTS,
        "limits": {"max_upload_mb": MAX_UPLOAD_MB},
        "supported_formats": ["ALTO XML", "AMCR Metadata XML"],
        "translation_backend": getattr(translator, "name", None),
    }


@app.get("/health")
async def health(deep: bool = False):
    """Liveness (shallow) / readiness (deep=true, backend reachability) probe."""
    if not models.get("translator") or not models.get("identifier"):
        return JSONResponse(
            {"status": "degraded", "detail": "translation backend not initialized"},
            status_code=503,
        )
    payload = {"status": "ok", "translation_backend": models["translator"].name}
    if deep:
        base_url = getattr(models["translator"], "BASE_URL", None)
        if base_url:
            import urllib.request

            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"{base_url}/models", method="HEAD"), timeout=5
                )
                payload["backend_reachable"] = True
            except Exception as exc:
                return JSONResponse(
                    {
                        "status": "degraded",
                        "detail": f"translation backend unreachable: {exc}",
                        "translation_backend": models["translator"].name,
                    },
                    status_code=503,
                )
    return JSONResponse(payload)


# Minimal demo frontend (file picker + language selectors + result download).
_FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/frontend")
