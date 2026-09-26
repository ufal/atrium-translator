# ATRIUM Translator API service 🌐

Structure-preserving translation of ALTO/AMCR XML. Chunks the document, translates each
chunk through the configured backend (LINDAT CUBBITT by default), and returns the
rewritten XML with its markup intact. The service version is read from
`para_config.txt` `[tool]` (single source of truth, never hard-coded).

## Quick start

```bash
pip install -r requirements.txt -r service/requirements.txt
python -m service.api                          # honours PORT/HOST; default 0.0.0.0:8000
# or, for development with auto-reload:
uvicorn service.api:app --host 0.0.0.0 --port 8000
# or:
docker compose --profile api up -d
```

## Endpoints

| Method | Path         | Purpose                                                                                                                                                                        |
|--------|--------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| GET    | `/info`      | service identity + capabilities: `service`, `version`, `endpoints`, `limits`, `chunk_limit`, backends                                                                          |
| GET    | `/health`    | liveness probe — 200 always, even mid-shutdown. `?deep=true` additionally checks the translation backend warmed up (503 on failure or while draining)                          |
| GET    | `/ready`     | readiness probe (issue #55) — 503 until the backend has warmed up, 200 while serving, 503 the instant `SIGTERM` arrives. The Kubernetes `readinessProbe`/`startupProbe` target |
| POST   | `/translate` | translate one XML document (multipart upload; optional baseline ATRIUM Document JSON)                                                                                          |

Machine-readable schemas: `GET /openapi.json`, or the Swagger UI at `/docs`, from a
running server. The repo-root `README.md` covers the CLI and the translation logic itself.

### `POST /translate` (multipart form)

| Field           | In            | Type    | Default       | Meaning                                                                                                                                                                      |
|-----------------|---------------|---------|---------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `file`          | form          | file    | —             | **Required.** The XML document. Filename must end in `.xml`.                                                                                                                 |
| `document_json` | form          | file    | —             | Optional baseline ATRIUM Document JSON to accrete onto.                                                                                                                      |
| `source_lang`   | form or query | string  | `auto`        | ISO 639-1 code, or `auto`: FastText per block/field, trusted only when confident and translatable, else the element's label, the document's language, `DEFAULT_SOURCE_LANG`. |
| `target_lang`   | form or query | string  | `en`          | ISO 639-1 code.                                                                                                                                                              |
| `is_alto`       | form or query | boolean | `true`        | `true` → ALTO dual-pass reconstruction; `false` → XPath metadata mode.                                                                                                       |
| `output_mode`   | form or query | string  | `OUTPUT_MODE` | `replace` or `append` (issue #46). Unset → the `OUTPUT_MODE` env var, then `replace`; an unknown value degrades to the default with a warning.                               |

Every field is read from the multipart body first and the query string second (issue #46), so either calling
style works.

```bash
curl -sf -F "file=@page.alto.xml" \
     "localhost:8000/translate?source_lang=cs&target_lang=en&is_alto=true" \
     -o page_en.alto.xml
```

### Response

**200** with `Content-Type: application/xml` and
`Content-Disposition: attachment; filename="<doc>_<lang>.alto.xml"` — the body is
the translated document, structurally identical to the input.

When `document_json` is supplied the response is instead `multipart/mixed`: the
translated XML first, then the updated ATRIUM Document JSON, each with its own
`Content-Disposition` filename.

The response carries no JSON envelope by design — the document is the payload, so
the endpoint composes with `curl -o` and with the pipeline's other stages.

### Errors

Harmonised across all five ATRIUM services (`agent_skill_strategy.md` §4.4), so a
client can treat them uniformly:

| Status | Meaning                      | When                                                                                                                                                        |
|--------|------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `413`  | Payload too large            | Upload exceeds `MAX_UPLOAD_MB`, or the declared envelope exceeds the cap. Enforced *during* the read, so an oversized body is refused rather than buffered. |
| `415`  | Unsupported media type       | `Content-Type` is neither `multipart/form-data` nor `application/json`.                                                                                     |
| `422`  | Unusable input               | Missing filename, or a filename not ending in `.xml`.                                                                                                       |
| `500`  | Translation failed           | The pipeline raised — malformed XML, or the backend failed after retries.                                                                                   |
| `503`  | Warming up, or shutting down | Before the backend is warm, or after `SIGTERM`. **Retryable** against another replica.                                                                      |

Error bodies are FastAPI's `{"detail": ...}`. `detail` is a **string** for the
errors this service raises itself (the table above), and a **list of validation
objects** when FastAPI rejects the request before the handler runs — a `POST` with
`Content-Type: application/json` and no `file` part returns `422` in that second
shape. A client should not assume `detail` is a string.

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
4. **Record** — a paradata JSON is written for the run, including the translation
   endpoint *actually resolved* (issue #63) rather than a literal, and the
   effective licence computed from the components the run exercised.
5. **Return** — the rewritten XML streams back as an attachment; the
   `TemporaryDirectory` is removed as the request ends.

## Configuration (environment)

| Variable                    | Default           | Meaning                                                                                                                                                                                                                                                   |
|-----------------------------|-------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `MAX_UPLOAD_MB`             | `50`              | canonical upload limit                                                                                                                                                                                                                                    |
| `ALLOWED_ORIGINS`           | `*`               | CSV of CORS origins. The code default is the `*` wildcard (credentials are then disabled, per the CORS spec); `.env.example` ships it commented (`# ALLOWED_ORIGINS=*`) so the default stays `*` until an operator narrows it.                            |
| `TRANSLATION_BACKEND`       | `lindat`          | backend seam shared with the CLI (issue #4)                                                                                                                                                                                                               |
| `OUTPUT_MODE`               | `replace`         | `replace` overwrites the source-language field; `append` keeps it and adds an `xml:lang`-marked sibling (ALTO: keeps every `String`'s `CONTENT` and adds `<ALTERNATIVE PURPOSE="translation:<lang>">`). Shared with the CLI's `--output-mode` (issue #46) |
| `DEFAULT_SOURCE_LANG`       | `cs`              | with `source_lang=auto` (the `/translate` default): the language used when detection cannot be trusted and neither the element's label nor the document's language settles it                                                                             |
| `LANG_ID_MIN_CONFIDENCE`    | `0.5`             | FastText score a detected language needs before it is used (`auto` only)                                                                                                                                                                                  |
| `LANG_ID_MIN_LETTERS`       | `20`              | texts with fewer letters are not sent to FastText; they inherit their label / the document language (`auto` only)                                                                                                                                         |
| `LINDAT_GUARD_RETRIES`      | `2`               | re-requests of an HTTP-200 LINDAT reply that is degenerate (repetition loop, empty, runaway)                                                                                                                                                              |
| `TRANSLATION_RERUN_ROUNDS`  | `1`               | end-of-document re-run rounds for segments still degenerate after the retries; `0` keeps them as source immediately                                                                                                                                       |
| `TRANSLATION_RERUN_DELAY_S` | `10.0`            | cool-down before each re-run round — **added to the request's duration** whenever a document has a flagged segment                                                                                                                                        |
| `AMCR_FIELDS_PATH`          | `amcr-fields.txt` | file of AMCR XPath targets for metadata mode, one per line; relative paths resolve against the repo root. Absent ⇒ metadata requests are refused 422, ALTO unaffected (issue #46)                                                                         |
| `TRANSLATION_URL`           | LINDAT            | translation API base URL for the `lindat` backend; `LINDAT_BASE_URL` is an alias (issue #63)                                                                                                                                                              |
| `UDPIPE_URL`                | LINDAT            | UDPipe 2 endpoint for vocabulary lemma matching; same name as atrium-nlp-enrich (issue #63)                                                                                                                                                               |
| `PORT`                      | `8000`            | port the service **binds**, and the one `service/healthcheck.py` probes (issues #55, #58)                                                                                                                                                                 |
| `HOST`                      | `0.0.0.0`         | bind address (issue #58). ⚠️ see the warning below                                                                                                                                                                                                        |
| `GRACEFUL_SHUTDOWN_S`       | `20`              | seconds uvicorn waits for in-flight requests (issue #55)                                                                                                                                                                                                  |
| `RELOAD`                    | `false`           | filesystem auto-reload — development only, never in a deployment                                                                                                                                                                                          |
| `LOG_LEVEL`                 | `INFO`            | root logger level for the `python -m service.api` start path (issue #61)                                                                                                                                                                                  |

`TRANSLATION_URL` and `UDPIPE_URL` make the two LINDAT-hosted backing services
attachable (12-factor IV): set either to reach a self-hosted or stubbed instance
without a code change. Unset, both reach the same hosts as before. The endpoint
the `lindat` backend actually resolved is what `/translate` writes into the
`translation_api` paradata field — the record names the host the request went
to, never a literal, since a provenance claim that is confidently wrong is worse
than one that is absent. The `LINDAT_MIN_INTERVAL_S` / `LINDAT_MAX_RETRIES` /
`LINDAT_BACKOFF_BASE_S` transport dials are separate and unchanged.

**Degenerate replies cost time, not correctness.** A LINDAT reply that comes back as a repetition loop is
re-requested (`LINDAT_GUARD_RETRIES`, with back-off), and a segment still degenerate after that is re-run once
the document is done (`TRANSLATION_RERUN_ROUNDS` × `TRANSLATION_RERUN_DELAY_S`); one that never recovers keeps
its source text. All of that happens inside the synchronous `/translate` request, so on a bad day for the
backend a request takes longer — size `GRACEFUL_SHUTDOWN_S` (and the orchestrator's grace period) with that in
mind, or lower `TRANSLATION_RERUN_DELAY_S` for the service.

`PORT` and `HOST` are read by `service/api.py`'s `__main__` block, which is what the `api`
image's `ENTRYPOINT` (`python -m service.api`) runs. Before issue #58 the entrypoint baked
`--port 8000` into an exec-form array — which runs no shell, so `$PORT` could not expand —
while `service/healthcheck.py` read it. Setting `PORT` therefore moved the health *probe*
and not the listener, and the container reported unhealthy forever.

> ⚠️ `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`, so a
> loopback bind passes every probe while being unreachable from outside the container.

## Shutdown behavior (issue #55)

The published `api` image (`ghcr.io/ufal/atrium-translator:<version>-api`, new in that
issue — before it this service was only reachable via a compose entrypoint override, so no
API image existed to deploy) declares `HEALTHCHECK` (shallow `GET /health`, via the
vendored `service/healthcheck.py`) and `STOPSIGNAL SIGTERM`, and sets
`ENV GRACEFUL_SHUTDOWN_S=20`, which `service/api.py`'s `__main__` block passes to uvicorn
as `timeout_graceful_shutdown`. (It was the `--timeout-graceful-shutdown 20` CLI flag until
issue #58 moved the whole start command into that block so `$PORT` could be honoured.)

On `SIGTERM` the service flips `GET /ready` to **503** at once so an orchestrator stops
routing to it, answers new `/translate` calls with 503, and lets in-flight translation
finish before `models.clear()` tears the backend down. `GET /health` deliberately stays
200 throughout — a liveness probe failing mid-shutdown would get the container killed
before the drain completed.

Translation now runs in a worker thread (`asyncio.to_thread`) rather than inline on the
event loop. That was a prerequisite, not a tidy-up: uvicorn's `SIGTERM` handler is an
event-loop callback, so while a synchronous `process_single_file()` held the loop the
signal could not be processed at all.

⚠️ This is the fleet's slowest request shape: one **retried** LINDAT call per chunk, so a
large document can legitimately run for minutes and outlive the 20s drain budget. Raise
`GRACEFUL_SHUTDOWN_S` and the deployment's grace period together for that workload — see
`docs/k8s_deployment.md` ("Known limits") in the hub.

A clean shutdown exits **143** (128 + SIGTERM), not 0: uvicorn re-raises the captured
signal on purpose so a supervisor sees the real cause. That is a normal stop, not a crash.

## Tests

```bash
pytest -q tests/test_api_contract.py tests/test_service_api_contract.py tests/test_api.py
```

`tests/test_api_contract.py` asserts the §4 meta-contract (including `/ready` and the
liveness-stays-200-while-draining rule) against the in-process app; the container-level
equivalent runs in CI via `docker-tool.reusable.yml`'s `probe-targets: '["api"]'`.
