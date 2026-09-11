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

Full request/response schemas: `GET /openapi.json`, or the Swagger UI at `/docs`, from a
running server. The repo-root `README.md` covers the CLI and the translation logic itself.

## Configuration (environment)

| Variable              | Default   | Meaning                                                                                   |
|-----------------------|-----------|-------------------------------------------------------------------------------------------|
| `MAX_UPLOAD_MB`       | `50`      | canonical upload limit                                                                    |
| `ALLOWED_ORIGINS`     | `*`       | CSV of CORS origins                                                                       |
| `TRANSLATION_BACKEND` | `lindat`  | backend seam shared with the CLI (issue #4)                                               |
| `TRANSLATION_URL`     | LINDAT    | translation API base URL for the `lindat` backend; `LINDAT_BASE_URL` is an alias (issue #63) |
| `UDPIPE_URL`          | LINDAT    | UDPipe 2 endpoint for vocabulary lemma matching; same name as atrium-nlp-enrich (issue #63) |
| `PORT`                | `8000`    | port the service **binds**, and the one `service/healthcheck.py` probes (issues #55, #58) |
| `HOST`                | `0.0.0.0` | bind address (issue #58). ⚠️ see the warning below                                        |
| `GRACEFUL_SHUTDOWN_S` | `20`      | seconds uvicorn waits for in-flight requests (issue #55)                                  |
| `RELOAD`              | `false`   | filesystem auto-reload — development only, never in a deployment                          |
| `LOG_LEVEL`           | `INFO`    | root logger level for the `python -m service.api` start path (issue #61)                  |

`TRANSLATION_URL` and `UDPIPE_URL` make the two LINDAT-hosted backing services
attachable (12-factor IV): set either to reach a self-hosted or stubbed instance
without a code change. Unset, both reach the same hosts as before. The endpoint
the `lindat` backend actually resolved is what `/translate` writes into the
`translation_api` paradata field — the record names the host the request went
to, never a literal, since a provenance claim that is confidently wrong is worse
than one that is absent. The `LINDAT_MIN_INTERVAL_S` / `LINDAT_MAX_RETRIES` /
`LINDAT_BACKOFF_BASE_S` transport dials are separate and unchanged.

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
