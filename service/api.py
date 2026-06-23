"""
service/api.py

FastAPI service for the ATRIUM LINDAT Translator.
Brings this repository into API parity with the rest of the ATRIUM pipeline.
"""

import argparse
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from atrium_paradata import ParadataLogger
from main import process_single_file
from processors.backend import get_backend
from processors.chunking import DEFAULT_CHUNK_SIZE
from processors.identifier import LanguageIdentifier

# Security limit: Default 50MB
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))

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
    version="0.6.2",
    lifespan=lifespan,
)

# Opus 4.8 Hardening: Restrictive CORS Configuration
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
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
        raise HTTPException(status_code=400, detail="Only XML files are supported.")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Max size is {MAX_UPLOAD_BYTES} bytes.")

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

        return FileResponse(path=output_path, media_type="application/xml", filename=out_filename)


@app.get("/info")
async def get_info():
    return {
        "name": "ATRIUM Translator Service",
        "version": app.version,
        "supported_formats": ["ALTO XML", "AMCR Metadata XML"],
    }
