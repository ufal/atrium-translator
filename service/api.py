"""
service/api.py

FastAPI service for the ATRIUM LINDAT Translator.
Brings this repository into API parity with the rest of the ATRIUM pipeline.
"""
import os
import tempfile
import argparse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from processors.identifier import LanguageIdentifier
from processors.translator import LindatTranslator
from processors.chunking import DEFAULT_CHUNK_SIZE
from atrium_paradata import ParadataLogger
from main import process_single_file

# Security limit: Default 50MB
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))

models = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[INFO] Warming up LINDAT Translator models...")
    models["translator"] = LindatTranslator(vocab_path=None)
    models["identifier"] = LanguageIdentifier()
    yield
    print("[INFO] Shutting down service...")
    models.clear()


app = FastAPI(
    title="ATRIUM Translator API",
    version="0.6.1",
    lifespan=lifespan
)

# Single CORS registration with env allow-list
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/translate")
async def translate_document(
        request: Request,
        file: UploadFile = File(...),
        source_lang: str = "auto",
        target_lang: str = "en",
        is_alto: bool = True
):
    if not file.filename.endswith(".xml"):
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
            source_lang=source_lang,
            target_lang=target_lang,
            alto=is_alto,
            fast_align=False,
            xsd=None
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
            "translation_api": "https://lindat.mff.cuni.cz/services/translation/api/v2/"
        }

        with ParadataLogger(
                program="translator-api",
                config=para_config,
                paradata_dir=str(output_dir / "paradata"),
                output_types=["xml", "csv"]
        ) as logger:
            success, _ = process_single_file(
                file_path=input_path,
                output_file=output_path,
                args=args,
                translator=models["translator"],
                identifier=models["identifier"] if source_lang == "auto" else None,
                xpaths_list=[],
                _logger=logger
            )

        if not success:
            raise HTTPException(status_code=500, detail="Translation processing failed.")

        return FileResponse(
            path=output_path,
            media_type="application/xml",
            filename=out_filename
        )


@app.get("/info")
async def get_info():
    return {
        "name": "ATRIUM Translator Service",
        "version": "0.6.1",
        "supported_formats": ["ALTO XML", "AMCR Metadata XML"]
    }