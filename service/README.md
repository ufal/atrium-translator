# ATRIUM Translator API service

The runbook of the translator's HTTP service, for whoever deploys and operates it or calls it from another stage.
`POST /translate` takes one XML record or ALTO page and returns it translated, with its markup intact: it chunks the
document and translates each chunk through the configured backend (LINDAT CUBBITT by default). In the AMČR pipeline
it is the record-level job that turns the `metadata` distribution into `metadata-en`, with the field list sent per
request (atrium-project#84, D34); full-text and ALTO translation are research use, not a pipeline step. The service version is
read from `para_config.txt` `[tool]` (single source of truth, never hard-coded). The command line is in
[docs/usage.md](../docs/usage.md); how a translation is made, in [docs/how-it-works.md](../docs/how-it-works.md).

On this page: [Start](#start) · [Models and caches](#models-and-caches) · [Endpoints](#endpoints) ·
[Errors](#errors) · [Limits](#limits) · [What a 200 carries](#what-a-200-carries) · [Known limits](#known-limits) ·
[Configuration](#configuration-environment) · [How it works](#how-it-works) ·
[Shutdown behaviour](#shutdown-behaviour) · [OpenAPI](#openapi-the-typed-contract) · [Tests](#tests)

## Start

**At a glance:**

| Image                                          | CPU / GPU                                                 | Memory                                                                                                   | Volumes                                                                                                   | Calls out to                                                                                                                                                                   | Ready after                                                    |
|------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| `ghcr.io/ufal/atrium-translator-api:<version>` | CPU only. A GPU matters only for a self-built `ct2` image | light, because the translation is remote; the language-ID file is 1.18 GB; steady-state use not measured | none needed: the language-ID model is in the image. `/cache/huggingface` keeps the download of a checkout | LINDAT translation, once per chunk; LINDAT UDPipe, only with a vocabulary; `LLM_BASE_URL`, only with `openai_compatible`. A checkout also downloads the language-ID model once | not measured; the image's healthcheck has a 180 s start period |

The published image, with one of the AMČR metadata samples of this repository (LINDAT is called, so the container
needs egress to `lindat.mff.cuni.cz`):

```bash
docker run --rm -p 8000:8000 ghcr.io/ufal/atrium-translator-api:<version>     # the release without its v
curl -s http://localhost:8000/ready                                          # 200 once the backend is warm
curl --fail-with-body -sS -F "file=@data_samples/my_documents/C-9100725A.xml" \
     "localhost:8000/translate?is_alto=false&source_lang=cs&target_lang=en&response_format=json" -o out.json
```

Expected: HTTP 200 and `out.json` with `"type": "metadata"`, the translated XML in `content`,
`limits_applied` (usually `[]`) and `paradata`. From a checkout instead:

```bash
pip install -r requirements.txt -r service/requirements.txt
python -m service.api                          # honours PORT/HOST; default 0.0.0.0:8000
# or, for development with auto-reload:
uvicorn service.api:app --host 0.0.0.0 --port 8000
# or:
docker compose --profile api up -d
```

**Deploying it.** The reference Kubernetes manifest and its acceptance runbook are in the hub, shared by all six ATRIUM
services:

- [docs/k8s_deployment.md](https://github.com/ufal/atrium-project/blob/main/docs/k8s_deployment.md): the manifest,
  the three probes, the port and bind configuration, and a *Known limits* section worth reading before promising
  anything from it;
- [docs/templates/k8s/atrium-service.deployment.yaml](https://github.com/ufal/atrium-project/blob/main/docs/templates/k8s/atrium-service.deployment.yaml):
  the manifest itself. Substitute the image and size `resources.limits.memory`; everything else is identical across
  the six services by design.

What this image gives an orchestrator: `GET /ready` for the `readinessProbe` and `startupProbe`, `GET /health` for the
`livenessProbe`, and a Dockerfile `HEALTHCHECK`. It also gives `STOPSIGNAL SIGTERM` with the drain described under
[Shutdown behaviour](#shutdown-behaviour), and one uvicorn process per container with no shared state, so it scales out
by replica count. A single `/translate` call issues one retried backend request per chunk and can run for minutes.
Raise `GRACEFUL_SHUTDOWN_S` **and** the deployment's `terminationGracePeriodSeconds` together for large documents,
since a request that outlives both is still cut short by `SIGKILL`.

## Models and caches

What the service fetches or loads before it can answer, so that a deployment without internet access knows what to
provide.

| What                                                                                                                                                  | From where                                                                              | Revision                                | Into                                                                         | Without egress                                                                                                                                                                                                                    |
|-------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|-----------------------------------------|------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| FastText language identification (`facebook/fasttext-language-identification`, `model.bin`, 1.18 GB, CC BY-NC 4.0), used only with `source_lang=auto` | the image; from a checkout, the Hugging Face Hub, downloaded once                       | `3af127d…`, checked by SHA-256 at build | `/opt/models/fasttext-lid.bin` (`LANG_ID_MODEL_PATH`); a checkout: `HF_HOME` | the image needs nothing. A checkout without the file starts degraded: `GET /health?deep=true` says so, and `auto` falls back to the element's label, the document language, `DEFAULT_SOURCE_LANG`. Pass `source_lang` to avoid it |
| LINDAT translation (the default backend, `TRANSLATION_URL`)                                                                                           | a remote service                                                                        | —                                       | —                                                                            | every request fails (500 after `LINDAT_MAX_RETRIES`); use `TRANSLATION_BACKEND=openai_compatible` with a self-hosted server                                                                                                       |
| LINDAT UDPipe (`UDPIPE_URL`), only with `VOCABULARY_PATH` and the `lindat` backend                                                                    | a remote service                                                                        | —                                       | —                                                                            | the terms of a chunk go unprotected                                                                                                                                                                                               |
| The vocabulary CSV                                                                                                                                    | bundled: `data_samples/vocabulary.csv`, built from atrium-keyword-extract `v1.3.0-beta` | `v1.3.0-beta`                           | the image's `data_samples/`                                                  | —                                                                                                                                                                                                                                 |
| `openai_compatible` and `ct2` models                                                                                                                  | your LLM server (`LLM_BASE_URL`); a converted model directory (`CT2_MODEL_DIR`)         | —                                       | —                                                                            | —                                                                                                                                                                                                                                 |

With `TRANSLATION_BACKEND=ct2` the model, and EuroLLM's tokenizer, is loaded during startup, before `GET /ready` turns
200. A configuration error stops the service instead of failing the first request: `CT2_MODEL_DIR` unset, a missing
tokenizer, or an unsupported `CT2_COMPUTE_TYPE`. Concurrent first requests share one load. One short generation
follows the load, so a model that loads but cannot run stops the service there too, with the fix. On
`CT2_DEVICE=cuda`, CTranslate2 4.x needs CUDA 12's cuBLAS (`libcublas.so.12`; see `requirements-ct2.txt`); or use
`CT2_DEVICE=cpu`. The published images do not include `requirements-ct2.txt`; a `ct2` service needs an image that does.

## Endpoints

| Method | Path         | Purpose                                                                                                                                                                                                                      |
|--------|--------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| GET    | `/info`      | service identity + capabilities: `service`, `version`, `endpoints`, `limits` (every [limit](#limits), current value), `limits_meta` (the variable that sets each), `supported_formats` (`["ALTO XML", "AMCR Metadata XML"]`) |
| GET    | `/health`    | liveness probe — 200 `{"status": "ok"}` always, even mid-shutdown. `?deep=true` additionally checks the translation backend warmed up (503 on failure or while draining)                                                     |
| GET    | `/ready`     | readiness probe — 503 `starting` until the backend has warmed up, 200 `ready` while serving, 503 `draining` the instant `SIGTERM` arrives. The Kubernetes `readinessProbe`/`startupProbe` target                             |
| POST   | `/translate` | translate one XML document (multipart upload; optional baseline ATRIUM Document JSON)                                                                                                                                        |

Machine-readable schemas: the committed [`openapi.json`](openapi.json), which every release also
attaches (see [OpenAPI](#openapi-the-typed-contract)); or `GET /openapi.json` and the Swagger UI at
`/docs` from a running server. The command line is in [docs/usage.md](../docs/usage.md), the translation logic in
[docs/how-it-works.md](../docs/how-it-works.md).

### `POST /translate` (multipart form)

| Field             | In            | Type    | Default         | Meaning                                                                                                                                                                                                                                                          |
|-------------------|---------------|---------|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `file`            | form          | file    | —               | **Required.** The XML document. Filename must end in `.xml`.                                                                                                                                                                                                     |
| `document_json`   | form          | file    | —               | Optional ATRIUM document record, or an AMČR seed: its `doc_id` names the outputs, and it comes back with nothing added (record schema 2.0: the translator writes no block). One that cannot be opened is a 422 `invalid_record`; an empty part counts as none.   |
| `xpaths`          | form          | string  | —               | **Required for a metadata record.** The fields to translate, one XPath per line (`#` comments allowed): AMČR's list, passed per job (aiscr-webamcr#3570). There is no default list; [`amcr-fields.txt`](../amcr-fields.txt) is an example. Ignored for ALTO.     |
| `source_lang`     | form or query | string  | `auto`          | ISO 639-1 code, or `auto`: FastText per block/field, trusted only when confident and translatable, else the element's label, the document's language, `DEFAULT_SOURCE_LANG`. A confident guess the backend cannot translate is reported, not refused: see below. |
| `target_lang`     | form or query | string  | `en`            | ISO 639-1 code.                                                                                                                                                                                                                                                  |
| `is_alto`         | form or query | boolean | unset: detected | `true` → ALTO dual-pass reconstruction; `false` → XPath metadata mode. Unset: the upload's root element decides, `alto` or any other, as the command line decides without a mode flag.                                                                           |
| `output_mode`     | form or query | string  | `OUTPUT_MODE`   | `replace` or `append`. Unset → the `OUTPUT_MODE` env var, then `replace`; an unknown value degrades to the default with a warning.                                                                                                                               |
| `response_format` | form or query | string  | `xml`           | `xml`: the translated XML, or `multipart/mixed` with a record or a log (below). `json`: one JSON object instead. Anything else is a 422.                                                                                                                         |
| `fast_align`      | form or query | boolean | `false`         | ALTO only: spread a block's translation over its lines by word counts instead of translating each line as an anchor (the CLI's `--fast-align`): far fewer calls, coarser line splits.                                                                            |
| `include_record`  | form or query | boolean | `false`         | Kept for compatibility. Since record schema 2.0 the translator writes no record, so a record comes back only when `document_json` was sent.                                                                                                                      |
| `include_log`     | form or query | boolean | `false`         | Return the translation log, the CLI's `<doc>_log.csv`: `log_csv` in JSON, else a `text/csv` part of a `multipart/mixed` answer.                                                                                                                                  |
| `xsd_strict`      | form or query | boolean | `XSD_STRICT`    | With `XSD_PATH` set, for a metadata record: `true` refuses a translation that does not validate (422), `false` returns it with `xsd_valid: false`.                                                                                                               |

Every field is read from the multipart body first and the query string second, so either calling style works. A
boolean that cannot be read is refused (422) from either place, so `?is_alto=ture` is an error and not ALTO.

```bash
curl -sf -F "file=@page.alto.xml" \
     "localhost:8000/translate?source_lang=cs&target_lang=en&is_alto=true" \
     -o page_en.alto.xml
```

### Response

**200** with `Content-Type: application/xml` and
`Content-Disposition: attachment; filename="<doc>_<lang>.alto.xml"`. The body is
the translated document, structurally identical to the input.

When `document_json` is supplied, or `include_record` or `include_log` is true, the response is instead
`multipart/mixed` (a uuid4 boundary). It holds the translated XML first, then the record (when one was sent), then
`<doc>_log.csv` (`text/csv`, with `include_log`), then `limits_applied.json`
(see [Limits](#limits)), then `paradata.json` (`application/ld+json`, the call's
paradata, below), each with its own `Content-Disposition` filename.

**XSD verdict.** With `XSD_PATH` set, a metadata translation carries `X-Atrium-Xsd-Valid: true|false`, and the JSON
answer `xsd_valid` and `xsd_errors`. `xsd_strict=true` (or `XSD_STRICT`) refuses an invalid one instead (422, below).

**Limits applied.** When a limit shaped the translation without refusing it, the response carries an
`X-Atrium-Limits-Applied` header: a segment split into chunks, a language decided on a sample, or a glossary that
gave the prompt a selection of the matched terms. The header holds `<key>=<effect>:<count>` pairs joined by `; `
(ASCII only; e.g. `lang_id_document_chars=sampled:1; translation_chunk_chars=split:2`). No header means no limit
applied. The full notes, with a sentence each, are the multipart response's `limits_applied.json` part and the run's
paradata `limits`. A segment left in the source language, or a vocabulary lookup that timed out, is not a note: the
call fails (422 `limit_exceeded`, D13).

By default the response carries no JSON envelope. The document is the payload, so
the endpoint composes with `curl -o` and with the pipeline's other stages.

**`response_format=json`** answers one `application/json` object
instead, typed in the spec as `TranslateResponse`. It is the shape a client generated from
`openapi.json` reads without a multipart parser:

```json
{"type": "alto", "filename": "CTX000000003-1_en.alto.xml", "media_type": "application/xml",
 "content": "<?xml version='1.0' encoding='UTF-8'?>\n<alto …>…</alto>",
 "limits_applied": [],
 "document_json": {"doc_id": "CTX000000003", "…": "…"},
 "paradata": {"@id": "urn:uuid:…", "@type": "CreateAction", "…": "…"}}
```

`type` is `alto` or `metadata`; `content` is the translated XML as UTF-8 text. `document_json` is present when a
record was sent, and holds it with nothing added. `document_json_schema_error` appears when that record does not
validate: an invalid baseline is passed through and said so, but only this JSON answer says it, not the multipart one.
`log_filename` and `log_csv` come with `include_log`; `xsd_valid` and `xsd_errors` with `XSD_PATH`.
`limits_applied` is the full list (the header is still set).

**An untranslatable source language.** With `source_lang=auto`, a segment
identified with confidence as a language the backend cannot translate (Latin or Hungarian on
LINDAT) is still translated from the fallback language. The run's paradata count such segments
per language and document (`parameters.translations.<doc_id>.unsupported_source_langs`, e.g.
`{"la": 1, "hu": 1}`). It is not a limit note: nothing was cut, and none of the shared effects
fits. In the CLI's `_log.csv` those rows have the status `unsupported_lang`.

**`paradata`** is the call's paradata: one Process Run Crate `CreateAction` (paradata 3.0,
atrium-project#84), built by `atrium_rocrate.action_from_paradata()`, in the JSON response and as the
multipart form's `paradata.json`. Its `@id` is the call's `run_uuid`. For a metadata record, `object`
is `metadata` and `result` is `metadata-en`, each by SHA-512; the field list, the language pair, the
backend and the output mode are parameters, and no field text is recorded. An ALTO translation
names the original it read and has no result. Each component is listed with its own terms; no
licence is computed. `agent` is `ATRIUM_RUN_AGENT` when set. A bare XML answer has no
place for it, and an error response carries none. The service writes no paradata file. Hub
[`docs/rocrate_export.md`](https://github.com/ufal/atrium-project/blob/main/docs/rocrate_export.md) §5
describes it.

## Errors

Harmonised across all six ATRIUM services (`agent_skill_strategy.md` §4.4), so a client can treat them uniformly. Read
a row from the left: the status and `reason` a caller sees, the cause, then what to do.

| Status | `reason`                 | Cause                                                                                                                                                                                                                                | What to do                                                                                                                                                    |
|--------|--------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `413`  | `limit_exceeded`         | A part exceeds `MAX_UPLOAD_MB`, or the declared request exceeds `max_request_mb` (2 × `MAX_UPLOAD_MB` + 1 MB). The declared size is checked before the body is read; a request without one is bounded part by part while it is read. | split the document, or raise `MAX_UPLOAD_MB`                                                                                                                  |
| `415`  | `unsupported_media_type` | A filename not ending in `.xml` (`accepted: [".xml"]`), or a `Content-Type` that is neither `multipart/form-data` nor `application/json` (`accepted` lists both).                                                                    | send an `.xml` file as `multipart/form-data`                                                                                                                  |
| `422`  | `invalid_record`         | The `document_json` part is not UTF-8 JSON, not a JSON object, or has a newer `schema_version` major than this tool reads.                                                                                                           | send the record the previous stage returned, or none; update this service if the record is newer                                                              |
| `422`  | `null`                   | A missing or unusable filename, a metadata record without `xpaths` (`cause: "missing_xpaths"`), an unknown `response_format`, or request validation (`errors` names the field).                                                      | fix the field `errors` or `detail` names; for a metadata record, send the field list as `xpaths`                                                              |
| `422`  | `limit_exceeded`         | A limit would have left content untranslated: a segment still degenerate after the re-run rounds, or a vocabulary lookup that timed out (`limit.effect: "skipped"`). The call fails and returns nothing (D13).                       | retry later; raise `TRANSLATION_RERUN_ROUNDS` or `UDPIPE_TIMEOUT_S`                                                                                           |
| `422`  | `null`                   | `cause: "malformed_xml"`: `is_alto` was not sent and the upload is not well-formed XML, so its kind cannot be told (with `is_alto` sent, such a file is still the 500 below).                                                        | fix the XML                                                                                                                                                   |
| `422`  | `null`                   | `cause: "xsd_validation_failed"`: `xsd_strict` (or `XSD_STRICT`) and the translation does not validate against `XSD_PATH`; `detail` has the validator's messages.                                                                    | fix the record or the XPath targets; send `xsd_strict=false` to get the translation with `xsd_valid: false`                                                   |
| `422`  | `null`                   | `cause: "unsupported_language_pair"`: the backend cannot translate `source_lang` → `target_lang` (with `auto`, the fallback language is checked). Nothing is translated, and nothing is rerouted.                                    | set `source_lang` to one of `errors[].supported_source_langs`, or use another `TRANSLATION_BACKEND`                                                           |
| `500`  | `null`                   | The pipeline raised: malformed XML, or the backend failed after its retries.                                                                                                                                                         | read the service log: for a backend failure check that LINDAT (or your server) answers and retry later once; for malformed XML fix the input; report a repeat |
| `503`  | `null`                   | Warming up, or shutting down after `SIGTERM`.                                                                                                                                                                                        | retry with backoff, against another replica                                                                                                                   |

Every error has one JSON body (hub `docs/agent_skill_strategy.md` §4.4):
`{"status": <int>, "reason": <code or null>, "detail": "<text>"}`. `detail` is
always a string. A `limit_exceeded` body adds `limit` (`key`, `env`, `value`, `observed`,
`unit`), and a request-validation 422 adds `errors`, FastAPI's list of problems:

```json
{"status": 413, "reason": "limit_exceeded", "detail": "File too large: over 50 MB (MAX_UPLOAD_MB).",
 "limit": {"key": "max_upload_mb", "env": "MAX_UPLOAD_MB", "value": 50.0, "observed": null, "unit": "MB"}}
```

## Limits

Every limit this service has is a setting. Each is declared once
in [`tool_limits.py`](../tool_limits.py) (the CLI reads the same declaration), reported with its
current value in `GET /info` `limits` and with the variable that sets it in `limits_meta`. A
malformed value stops the service at startup, naming the variable. Over a limit the service
**refuses** (`reason: "limit_exceeded"`) or **translates the input in full** and says how the
limit shaped the result (`X-Atrium-Limits-Applied`, `limits_applied`). The LLM and CT2 rows
apply only when `TRANSLATION_BACKEND` selects that backend. `tests/test_limits_contract.py`
checks this table against `tool_limits.py` and `.env.example`.

| Key (`/info`)                    | Variable                                     | Default | Unit    | Over the limit                                                                                     |
|----------------------------------|----------------------------------------------|---------|---------|----------------------------------------------------------------------------------------------------|
| `max_upload_mb`                  | `MAX_UPLOAD_MB`                              | 50      | MB      | 413 `limit_exceeded` — per part: the XML and the baseline document JSON                            |
| `max_request_mb`                 | — (derived from `MAX_UPLOAD_MB`: 2 × it + 1) | —       | MB      | 413 `limit_exceeded`, from the declared `Content-Length`, before the body is read                  |
| `translation_chunk_chars`        | `TRANSLATION_CHUNK_CHARS`                    | 4000    | chars   | translated in full, in pieces re-joined with a line break — `split` note                           |
| `lang_id_segment_chars`          | `LANG_ID_SEGMENT_CHARS`                      | 2000    | chars   | `source_lang=auto`: the segment's language is decided on its first N characters — `sampled` note   |
| `lang_id_document_chars`         | `LANG_ID_DOCUMENT_CHARS`                     | 20000   | chars   | `source_lang=auto`: the document's language is decided on its first N characters — `sampled` note  |
| `lindat_timeout_s`               | `LINDAT_TIMEOUT_S`                           | 60      | s       | the call is retried (`LINDAT_MAX_RETRIES`)                                                         |
| `lindat_max_retries`             | `LINDAT_MAX_RETRIES`                         | 4       | retries | the file fails → 500                                                                               |
| `lindat_guard_retries`           | `LINDAT_GUARD_RETRIES`                       | 2       | retries | the segment is flagged for the end-of-document re-run                                              |
| `translation_rerun_rounds`       | `TRANSLATION_RERUN_ROUNDS`                   | 1       | rounds  | a segment still degenerate would keep its source text: the call fails (422 `limit_exceeded`, D13)  |
| `udpipe_timeout_s`               | `UDPIPE_TIMEOUT_S`                           | 30      | s       | with a vocabulary (LINDAT): the chunk's terms would go unprotected: the call fails (D13)           |
| `llm_timeout_s`                  | `LLM_TIMEOUT_S`                              | 120     | s       | the call is retried (`LLM_MAX_RETRIES`)                                                            |
| `llm_max_tokens`                 | `LLM_MAX_TOKENS`                             | 2048    | tokens  | a reply cut at it (`finish_reason=length`) is degenerate: re-run, else kept as source — never used |
| `llm_max_retries`                | `LLM_MAX_RETRIES`                            | 4       | retries | the file fails → 500                                                                               |
| `llm_max_glossary_terms`         | `LLM_MAX_GLOSSARY_TERMS`                     | 40      | terms   | with a vocabulary: the longest terms go into the prompt — `sampled` note                           |
| `llm_glossary_description_chars` | `LLM_GLOSSARY_DESCRIPTION_CHARS`             | 0       | chars   | 0: no descriptions in the prompt; a longer one gives the prompt its first part — `sampled` note    |
| `ct2_max_input_tokens`           | `CT2_MAX_INPUT_TOKENS`                       | 1024    | tokens  | the chunk is re-split and translated in full — `split` note                                        |
| `ct2_max_decoding_tokens`        | `CT2_MAX_DECODING_TOKENS`                    | 2048    | tokens  | a reply that reaches it is degenerate: re-run, else kept as source — never used                    |
| `ct2_max_glossary_terms`         | `CT2_MAX_GLOSSARY_TERMS`                     | 40      | terms   | with a vocabulary: the longest terms go into the prompt — `sampled` note                           |

Platform limits (not settings): libxml2's default limits (parsed without `huge_tree`: nesting
depth, a 10 MB text node) — a document over them fails as malformed XML (500); Starlette's
multipart defaults.

## What a 200 carries

A 200 can still be a result a limit or a backend shaped. Two places say so:

- **`limits_applied`** (JSON and multipart answers) and the `X-Atrium-Limits-Applied` header:
  `split` (a long segment translated in pieces) and `sampled` (the language decided on the first
  characters; a glossary's selection of terms in a prompt). The table above says which limit
  produces which note.
- **`parameters.translations.<doc_id>.unsupported_source_langs`** in the paradata: segments identified as
  a language the backend cannot translate, translated from the fallback language instead, counted per
  language.

A partial translation is never a 200: a segment left in the source language fails the call with 422
`limit_exceeded` (D13, atrium-project#84).

## Known limits

- **The default backend's models are CC BY-NC-SA 4.0.** They are the CUBBITT models, and UDPipe's when a vocabulary is
  applied, offered as LINDAT services under the [LINDAT Terms of Use](https://lindat.mff.cuni.cz/en/terms-of-use).
  AMČR's use of the default backend waits on LINDAT's written permission. The self-hosted alternative is
  `openai_compatible` with Qwen3.8-27B (Apache-2.0; [the backend decision](../docs/decisions/backend-choice.md)). None
  of these licences is the licence of the translation; the rights in an output depend on the source document
  ([licences](../docs/reference/licences.md)).
- **LINDAT is a public research service without a service-level agreement.** A broken replica has answered every other
  request with a loop. The guard recovers such replies at the cost of time
  ([LINDAT's replies](../docs/evaluation/lindat-replies.md)). Without LINDAT every request fails (500).
- **XSD validation only with `XSD_PATH`.** The service validates metadata translations against a local schema its
  deployment sets, and by default says so in the answer (`xsd_valid`) rather than refusing. `append` output of AMČR
  records is not valid AMČR 2.2. Production AMČR metadata is translated in `replace` mode.
- **No authentication or rate limiting.** `/translate` does unbounded outbound work against a translation backend on
  behalf of any caller: deploy it behind your own gateway, or on a trusted network, never directly on the public
  internet.
- **A language pair the backend has no model for is refused** (422 `unsupported_language_pair`), never routed
  through Czech: LINDAT has no `cs-it` or `hu-en`, for example.
- **`ct2` with EuroLLM drops short segments.** In the bake-off of 2026-10-19 EuroLLM-9B ran on past 96 of 1,230
  segments, almost all a number or a single word; the guard keeps their source text (`untranslated`). On a GPU
  it needs CUDA 12's cuBLAS (12.8+ on a Blackwell card), and `int8_float16` needs compute capability 7.0+.
- **The bake-off is reference-less and small:** 37 metadata fields and 1,193 ALTO blocks, measured by
  failures, kept terms and numbers, length and similarity to LINDAT. No human has scored the translations yet.

## Configuration (environment)

| Variable                    | Default           | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                    |
|-----------------------------|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `PORT`                      | `8000`            | port the service **binds**, and the one `service/healthcheck.py` probes                                                                                                                                                                                                                                                                                                                                                    |
| `HOST`                      | `0.0.0.0`         | bind address; see the note below                                                                                                                                                                                                                                                                                                                                                                                           |
| `GRACEFUL_SHUTDOWN_S`       | `20`              | seconds uvicorn waits for in-flight requests on shutdown                                                                                                                                                                                                                                                                                                                                                                   |
| `RELOAD`                    | `false`           | filesystem auto-reload — development only, never in a deployment                                                                                                                                                                                                                                                                                                                                                           |
| `LOG_LEVEL`                 | `INFO`            | root logger level for the `python -m service.api` start path                                                                                                                                                                                                                                                                                                                                                               |
| `MAX_UPLOAD_MB`             | `50`              | canonical upload limit, sized for ALTO XML in and ALTO XML out                                                                                                                                                                                                                                                                                                                                                             |
| `ALLOWED_ORIGINS`           | `*`               | CSV of CORS origins. The code default is the `*` wildcard (credentials are then disabled, per the CORS spec); `.env.example` ships it commented (`# ALLOWED_ORIGINS=*`) so the default stays `*` until an operator narrows it; an empty value means no origin.                                                                                                                                                             |
| `TRANSLATION_BACKEND`       | `lindat`          | `lindat`, `openai_compatible` or `ct2`; the backend seam shared with the CLI                                                                                                                                                                                                                                                                                                                                               |
| `OUTPUT_MODE`               | `replace`         | `replace` overwrites the source-language field; `append` keeps it and adds an `xml:lang`-marked sibling (ALTO: keeps every `String`'s `CONTENT` and adds `<ALTERNATIVE PURPOSE="translation:<lang>">`). Shared with the CLI's `--output-mode`                                                                                                                                                                              |
| `DEFAULT_SOURCE_LANG`       | `cs`              | with `source_lang=auto` (the `/translate` default): the language used when detection cannot be trusted and neither the element's label nor the document's language settles it                                                                                                                                                                                                                                              |
| `LANG_ID_MIN_CONFIDENCE`    | `0.5`             | FastText score a detected language needs before it is used (`auto` only)                                                                                                                                                                                                                                                                                                                                                   |
| `LANG_ID_MIN_LETTERS`       | `20`              | texts with fewer letters are not sent to FastText; they inherit their label / the document language (`auto` only)                                                                                                                                                                                                                                                                                                          |
| `LANG_ID_MODEL_PATH`        |                   | the FastText language-ID model file (`auto` only). The published images set it to `/opt/models/fasttext-lid.bin`; unset (a checkout), the pinned Hub revision is downloaded into `HF_HOME`. A path that is not a file: the service starts, `/health?deep=true` reports the identifier unavailable, and `auto` falls back (see Models and caches)                                                                           |
| `TRANSLATION_RERUN_DELAY_S` | `10.0`            | cool-down before each re-run round — **added to the request's duration** whenever a document has a flagged segment                                                                                                                                                                                                                                                                                                         |
| `XSD_PATH`                  |                   | a local XML Schema metadata translations are validated against, compiled at startup (the CLI's `--xsd`). A file that cannot be read or compiled: the service **refuses to start**. `/info` reports it under `xsd`. Only a local file: a request never makes the service fetch a schema                                                                                                                                     |
| `XSD_STRICT`                |                   | with `XSD_PATH`: the default of `xsd_strict`; `1`/`true` refuses an invalid translation with 422, unset or anything else returns it with `xsd_valid: false`                                                                                                                                                                                                                                                                |
| `VOCABULARY_PATH`           |                   | vocabulary CSV `/translate` applies: the LLM and `ct2` backends put the matching terms into the prompt, `lindat` protects them (Tag-and-Protect, which calls `UDPIPE_URL`). Relative paths resolve against the repo root. Empty: no vocabulary. Set to a missing file or one with no term pair: the service **refuses to start**, naming the file. `/info` reports the file, its term count and SHA-256 under `vocabulary` |
| `LLM_EXTRA_BODY`            |                   | `openai_compatible`: a JSON object merged into every request body (e.g. `{"chat_template_kwargs": {"enable_thinking": false}}` for a vLLM serving Qwen3.8); not a JSON object ⇒ the service refuses to start                                                                                                                                                                                                               |
| `LLM_LICENSE_COMPONENT`     |                   | `openai_compatible`: the `para_config.txt` component recorded for the model instead of `llm_api` (provider terms) — for a model the deployment serves itself (`qwen3_8`: Qwen3.8-27B, Apache-2.0)                                                                                                                                                                                                                          |
| `TRANSLATION_URL`           | LINDAT            | translation API base URL for the `lindat` backend; `LINDAT_BASE_URL` is an alias                                                                                                                                                                                                                                                                                                                                           |
| `UDPIPE_URL`                | LINDAT            | UDPipe 2 endpoint for vocabulary lemma matching (Tag-and-Protect); same name as atrium-nlp-enrich. The service calls it only with `VOCABULARY_PATH` set and the `lindat` backend                                                                                                                                                                                                                                           |

The first five rows are the ecosystem-wide contract, identical in all six services. The complete ledger, with every
variable the image reads, is [`.env.example`](../.env.example).

`TRANSLATION_URL` and `UDPIPE_URL` make the two LINDAT-hosted backing services
attachable (12-factor IV): set either to reach a self-hosted or stubbed instance
without a code change. Unset, both reach LINDAT's public hosts. The endpoint
the `lindat` backend actually resolved is what `/translate` writes into the
`translation_api` paradata field. The record names the host the request went
to, never a literal, since a provenance claim that is confidently wrong is worse
than one that is absent. The `LINDAT_MIN_INTERVAL_S` / `LINDAT_MAX_RETRIES` /
`LINDAT_BACKOFF_BASE_S` transport dials are separate settings.

**Degenerate replies cost time, not correctness.** A LINDAT reply that comes back as a repetition loop is
re-requested (`LINDAT_GUARD_RETRIES`; the first time immediately, then with back-off). A segment still degenerate
after that is re-run once the document is done (`TRANSLATION_RERUN_ROUNDS` × `TRANSLATION_RERUN_DELAY_S`). The `ct2`
backend decodes a re-run with a wider beam, since the same request would get the same reply. A segment that never
recovers keeps its source text. All of that happens inside the synchronous `/translate` request, so on a bad day for
the backend a request takes longer. Size `GRACEFUL_SHUTDOWN_S` (and the orchestrator's grace period) with that in
mind, or lower `TRANSLATION_RERUN_DELAY_S` for the service.

`PORT` and `HOST` are read by `service/api.py`'s `__main__` block, which is what the `api`
image's `ENTRYPOINT` (`python -m service.api`) runs. `service/healthcheck.py` reads `PORT` too, so the listener and the
health probe move together.

> **Note:** `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`, so a
> loopback bind passes every probe while being unreachable from outside the container.

## How it works

1. **Guard** — `verify_content_type` rejects a wrong `Content-Type` with 415;
   `_refuse_if_draining()` answers 503 once a shutdown signal has arrived, which
   bounds the set of requests the drain has to wait for.
2. **Read** — the upload is read in 1 MiB chunks and abandoned the moment it
   crosses `MAX_UPLOAD_MB`, then written into a per-request
   `TemporaryDirectory()`. Nothing is retained between requests: that is what
   makes the service horizontally scalable.
3. **Translate** — the handler calls the *same* `main.process_single_file()` the
   batch CLI uses, in a worker thread via `asyncio.to_thread`. The thread is not
   a tidy-up: uvicorn's `SIGTERM` handler is an event-loop callback, so a
   synchronous translation holding the loop would make the shutdown contract
   unenforceable.
4. **Record** — the call's provenance is built (the `CreateAction` returned as `paradata`; no
   paradata file is written), including the translation endpoint *actually resolved*
   rather than a literal, and the components the run used, each with its licence or terms
   ([licences](../docs/reference/licences.md)); none of them is the licence of the translation.
5. **Return** — the rewritten XML streams back as an attachment; the
   `TemporaryDirectory` is removed as the request ends.

## Shutdown behaviour

The published `api` image (`ghcr.io/ufal/atrium-translator-api:<version>`) declares `HEALTHCHECK` (shallow
`GET /health`, via the vendored `service/healthcheck.py`) and `STOPSIGNAL SIGTERM`. It sets
`ENV GRACEFUL_SHUTDOWN_S=20`, which `service/api.py`'s `__main__` block passes to uvicorn
as `timeout_graceful_shutdown`.

On `SIGTERM` the service flips `GET /ready` to **503** at once so an orchestrator stops
routing to it, answers new `/translate` calls with 503, and lets in-flight translation
finish before `models.clear()` tears the backend down. `GET /health` deliberately stays
200 throughout: a liveness probe failing mid-shutdown would get the container killed
before the drain completed. Translation runs in a worker thread
([How it works](#how-it-works), step 3), so that the event loop can process the signal at all.

This is the fleet's slowest request shape: one **retried** LINDAT call per chunk, so a
large document can legitimately run for minutes and outlive the 20 s drain budget. Raise
`GRACEFUL_SHUTDOWN_S` and the deployment's grace period together for that workload — see
`docs/k8s_deployment.md` ("Known limits") in the hub.

A clean shutdown exits **143** (128 + SIGTERM), not 0: uvicorn re-raises the captured
signal on purpose so a supervisor sees the real cause. That is a normal stop, not a crash.

## OpenAPI (the typed contract)

The service's OpenAPI document is committed as [`service/openapi.json`](openapi.json) and
attached to every release as `openapi.json` with its `openapi.json.sha256`. It is what a client is generated from:
every request and response field is typed,
every error response is the `ErrorBody` above, the registered `reason` codes are listed in
`x-atrium-reason-codes`, and a returned record is typed by the vendored record schema
(`AtriumDocument`). `GET /info` reports `openapi_sha256`, the digest of the spec the running
image serves — equal to the release's `openapi.json.sha256` for an image built from that tag.
The XML and multipart answers of `/translate` are declared as their media types; the JSON one
(`response_format=json`) is fully typed.

- **After an API change**, regenerate and commit it:
  `python atrium_openapi.py export --app service.api:app --out service/openapi.json`.
  `tests/test_openapi_contract.py` fails while it is stale.
- **Compatibility.** Each release compares its spec with the previous release's
  (`release.yml`, `atrium_openapi.py compare` with oasdiff): a breaking change fails the
  release unless the major version went up (for 0.x, that means 1.0), and a removed reason
  code always fails. New fields, endpoints and reason codes are additive.
- **fastapi and pydantic are pinned** exactly (`service/requirements.txt`,
  `requirements-test.txt`): the spec is generated by them. Bump both by hand and regenerate.
- **The release bundle** ships `atrium_openapi.py` (the service imports it to finish and digest
  the spec) and `service/openapi.json`.

## Tests

```bash
pytest -q tests/test_api_contract.py tests/test_service_api_contract.py tests/test_api.py tests/test_openapi_contract.py
```

`tests/test_api_contract.py` asserts the §4 meta-contract (including `/ready` and the
liveness-stays-200-while-draining rule) against the in-process app, and drives `/translate`
through the real `process_single_file` with a fake backend to hold every JSON response —
the `response_format=json` 200 and every refusal — to the published schema;
`tests/test_openapi_contract.py` (vendored from the hub) checks the committed spec itself. The
container-level equivalent runs in CI via `docker-tool.reusable.yml`'s `probe-targets: '["api"]'`.
