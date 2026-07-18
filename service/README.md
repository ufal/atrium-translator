# ATRIUM Translator API service 🌍

FastAPI entry point for the ATRIUM translation pipeline: upload an **ALTO XML**
page or an **AMCR metadata XML** file, get back the same document with its text
content translated (XML attachment). Translation runs through the configured
backend — LINDAT/CUBBITT NMT by default — with FastText source-language
detection, optional vocabulary Tag-and-Protect, and paradata provenance
logging. The service version is read from `para_config.txt` `[tool]` (single
source of truth, never hard-coded).

## Quick start

```bash
pip install -r requirements.txt -r service/requirements.txt
uvicorn service.api:app --host 0.0.0.0 --port 8000
# or:
docker compose up -d api
```

A minimal demo frontend is served at `http://localhost:8000/frontend/`
(file picker + language selectors + result download).

## Endpoints

| Method | Path         | Purpose                                                                                        |
|--------|--------------|------------------------------------------------------------------------------------------------|
| GET    | `/`          | redirects to the demo frontend                                                                 |
| GET    | `/info`      | service identity + capabilities: `service`, `version`, `endpoints`, `limits`, formats, backend |
| GET    | `/health`    | liveness probe; `?deep=true` additionally HEAD-checks the LINDAT translation API (503 on fail) |
| POST   | `/translate` | **single entry point** — upload one XML file, receive the translated XML attachment            |

### `POST /translate` (multipart file + query params)

| Parameter     | In    | Default    | Notes                                                          |
|---------------|-------|------------|----------------------------------------------------------------|
| `file`        | form  | *required* | one `.xml` file (ALTO page or AMCR metadata)                   |
| `source_lang` | query | `auto`     | ISO code, or `auto` for FastText detection (per ALTO TextBlock)|
| `target_lang` | query | `en`       | ISO code of the translation target                             |
| `is_alto`     | query | `true`     | `true` = ALTO line translation; `false` = metadata-field mode  |

```bash
# ALTO page, autodetect source, translate to English, save the attachment:
curl -X POST "http://localhost:8000/translate?source_lang=auto&target_lang=en&is_alto=true" \
     -F "file=@page.alto.xml" -OJ

# AMCR metadata XML, Czech → English:
curl -X POST "http://localhost:8000/translate?source_lang=cs&target_lang=en&is_alto=false" \
     -F "file=@record.xml" -OJ

# Capabilities:
curl -s http://localhost:8000/info
```

### Response semantics

`/translate` returns the **translated XML document itself** (media type
`application/xml`), not a JSON envelope. The filename arrives in the
`Content-Disposition: attachment` header:

- `page.alto.xml` → `page_en.alto.xml` (ALTO naming preserved)
- `record.xml` → `record_en.xml`

Use `curl -OJ` (or read the header) to save under the server-proposed name.
`/info` returns JSON:

```json
{
  "service": "atrium-translator",
  "name": "ATRIUM Translator Service",
  "version": "0.8.1",
  "endpoints": ["/info", "/health", "/translate"],
  "limits": {"max_upload_mb": 50},
  "supported_formats": ["ALTO XML", "AMCR Metadata XML"],
  "translation_backend": "lindat"
}
```

## Errors

| Code        | Meaning                                                            |
|-------------|--------------------------------------------------------------------|
| 413         | file exceeds `MAX_UPLOAD_MB`                                       |
| 415         | request Content-Type is neither multipart nor JSON                 |
| 422         | unusable input (non-`.xml` upload, malformed parameters)           |
| 500         | translation processing failed (see server log; backend errors)    |
| 502/503/504 | not ready / upstream (LINDAT) unavailable — **clients retry 3×**   |

## Configuration (environment)

| Variable              | Default  | Meaning                                                                                       |
|-----------------------|----------|-----------------------------------------------------------------------------------------------|
| `TRANSLATION_BACKEND` | `lindat` | `lindat` (CUBBITT NMT) or `openai_compatible` (LLM adapter, see `translation_backends.md`)    |
| `MAX_UPLOAD_MB`       | `50`     | canonical upload limit (family standard)                                                      |
| `MAX_UPLOAD_BYTES`    | —        | **deprecated** fallback for `MAX_UPLOAD_MB`; honored when the MB variable is unset            |
| `ALLOWED_ORIGINS`     | `*`      | CSV of CORS origins                                                                           |
| `LLM_BASE_URL` etc.   | —        | `LLM_MODEL`, `LLM_API_KEY`, `LLM_PROVIDER`, `LLM_LANGUAGES` — configure the LLM backend       |

## How it works

The endpoint drives the same `process_single_file()` used by the batch CLI
(`main.py`): source-language identification (once per ALTO `TextBlock` when
`source_lang=auto`), language-grouped chunked requests to the translation
backend, and XML reconstruction that preserves the original structure. Every
API run is recorded through `atrium_paradata.ParadataLogger` (program
`translator-api`), including the active backend and component licenses.

## Frontend

`service/frontend/` is a minimal standalone HTML/JS client mounted at
`/frontend`: it demonstrates the multipart upload, the query parameters, and
saving the XML attachment, and links the live API docs (`/docs`,
`/openapi.json`).

## Tests

The API test suite lives on the development
([`test`](https://github.com/ufal/atrium-translator/tree/test)) branch
(`tests/test_api.py`, hermetic — mock translator injected):

```bash
pytest tests/test_api.py
```
