---
name: atrium-translator
description: Translates archaeological archival XML documents - ALTO OCR pages or AMCR metadata records - between languages (Czech-centric, default target English) via the LINDAT/CUBBITT NMT service, preserving the XML structure and returning the translated document as a file. Use this skill to make digitized historical documents readable in another language after OCR and quality filtering, keeping ALTO layout or AMCR metadata fields intact.
---

# ATRIUM Translator Skill 🌍

This skill provides agent access to the **ATRIUM Translator** service -
structure-preserving machine translation of ALTO OCR pages and AMCR metadata
XML through the LINDAT/CUBBITT NMT API (or a configured LLM backend). It
follows a **server-client** design: a FastAPI server (in `service/`) performs
the translation pipeline, and a zero-dependency client script
(`scripts/atrium_translate.py`) is the only thing the agent calls directly.

## Operational Requirements ⚙️

- **Server**: a running instance is required. Default `http://localhost:8000`;
  override with `--base-url` or the `ATRIUM_TR_URL` environment variable.
- **Client dependencies**: none - `scripts/atrium_translate.py` uses only the
  Python 3 standard library.
- **Server dependencies**: Docker (recommended, compose `api` service) or a
  Python venv with `requirements.txt` + `service/requirements.txt`. The
  translation itself calls the remote LINDAT API - outbound network access is
  required at request time.
- **First launch**: the FastText language-identification model is downloaded;
  warmup is fast compared to the model-heavy siblings, but the first
  translation of a long document still takes minutes. Do **not** treat a slow
  call as failure.
- **Limits**: 50 MB per file; one XML document per request.

## Modes & languages 🌍

| Mode                     | Input                    | What gets translated                                                                |
|--------------------------|--------------------------|-------------------------------------------------------------------------------------|
| ALTO (`--alto`, default) | ALTO OCR page XML        | every text line; source language detected per `TextBlock` when `--source-lang auto` |
| Metadata (`--no-alto`)   | AMCR metadata record XML | configured free-text fields; structure and codes untouched                          |

Languages: Czech-centric CUBBITT model pairs (`cs↔en` best quality; additional
pairs such as `de`, `fr`, `pl`, `ru`, `uk` depending on the deployed LINDAT
models - query `GET /info` / the LINDAT models endpoint). `--source-lang auto`
uses FastText detection with an intelligent fallback; the default target is
English. The backend is selected server-side via `TRANSLATION_BACKEND`
(`lindat` default, `openai_compatible` for an LLM adapter).

## Workflows 🪄

### 1. Ensure the server is running

```bash
bash scripts/server.sh          # Docker Compose api service (or local fallback)
bash scripts/server.sh --local  # force local uvicorn (no Docker)
```

Idempotent: exits immediately if GET /info already answers; waits for
first-run warmup.

### 2. Translate

```bash
# ALTO page, autodetect source, translate to English (saves page_en.alto.xml)
python3 scripts/atrium_translate.py small_data_samples/MTX201501307_anon.alto.xml

# AMCR metadata record, Czech → English
python3 scripts/atrium_translate.py small_data_samples/C-202000543A-DT-27.xml --no-alto --source-lang cs

# Different target language, explicit output path
python3 scripts/atrium_translate.py page.alto.xml --target-lang de -o page_de.alto.xml

# Translated XML to stdout (for piping)
python3 scripts/atrium_translate.py page.alto.xml -o -

# Discover capabilities and limits
python3 scripts/atrium_translate.py --info
```

### 3. Interpret output

The result is the translated XML document itself, not a report: ALTO
naming is preserved (page.alto.xml → page_en.alto.xml), metadata files get
_<target> suffixed (record.xml → record_en.xml). By default the client
saves under the server-proposed name in the current directory and prints the
path; use -o FILE to choose the path or -o - to stream to stdout.

## Agent Guidelines 🤖

1. Mode discipline: pass --no-alto for AMCR metadata records - running a
metadata file in ALTO mode (or vice versa) produces empty or mangled
output, not an error.
2. Keep --source-lang auto unless the user names the source language;
detection is per-TextBlock and handles mixed-language pages.
3. For full request/response schemas, fetch GET /openapi.json from the
running server (Swagger UI at /docs).
4. Exit code 2 (unreachable): start the server (bash scripts/server.sh)
and retry once. Exit code 3 (server error): the client already retried
502/503/504 three times - check GET /health?deep=true (verifies LINDAT
reachability) and server logs; do not loop.
5. Size limits: files over 50 MB are rejected - split multi-page exports
first, and tell the user you did so.
6. Validate the returned XML parses before handing it downstream; report the
output path rather than inlining large XML into the conversation.
7. Do not bypass the API by importing the pipeline code directly; server-side
runs are paradata-logged (program translator-api), so translations stay
traceable even when invoked by an agent.

## Acknowledgements & Citations 🙏

The models and dataset are developed within the [ATRIUM](https://atrium-research.eu/)
project at ÚFAL, Charles University, with data hosted on
[LINDAT/CLARIAH-CZ](https://lindat.cz). If you use this service for research, cite the
repository's `CITATION.cff` and the LINDAT dataset record
(http://hdl.handle.net/20.500.12800/1-6184).
