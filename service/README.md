# ATRIUM Translator API service 🌐

Structure-preserving translation of ALTO/AMCR XML. Chunks the document, translates each
chunk through the configured backend (LINDAT CUBBITT by default), and returns the
rewritten XML with its markup intact. The service version is read from
`para_config.txt` `[tool]` (single source of truth, never hard-coded).

## Quick start

```bash
pip install -r requirements.txt -r service/requirements.txt
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

| Variable               | Default  | Meaning                                              |
|------------------------|----------|------------------------------------------------------|
| `MAX_UPLOAD_MB`        | `50`     | canonical upload limit                               |
| `ALLOWED_ORIGINS`      | `*`      | CSV of CORS origins                                  |
| `TRANSLATION_BACKEND`  | `lindat` | backend seam shared with the CLI (issue #4)          |
| `PORT`                 | `8000`   | port `service/healthcheck.py` probes (issue #55)     |

## Shutdown behavior (issue #55)

The published `api` image (`ghcr.io/ufal/atrium-translator:<version>-api`, new in that
issue — before it this service was only reachable via a compose entrypoint override, so no
API image existed to deploy) declares `HEALTHCHECK` (shallow `GET /health`, via the
vendored `service/healthcheck.py`) and `STOPSIGNAL SIGTERM`, and its `ENTRYPOINT` passes
`--timeout-graceful-shutdown 20`.

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
`--timeout-graceful-shutdown` and the deployment's grace period together for that
workload — see `docs/k8s_deployment.md` ("Known limits") in the hub.

A clean shutdown exits **143** (128 + SIGTERM), not 0: uvicorn re-raises the captured
signal on purpose so a supervisor sees the real cause. That is a normal stop, not a crash.

## Tests

```bash
pytest -q tests/test_api_contract.py tests/test_service_api_contract.py tests/test_api.py
```

`tests/test_api_contract.py` asserts the §4 meta-contract (including `/ready` and the
liveness-stays-200-while-draining rule) against the in-process app; the container-level
equivalent runs in CI via `docker-tool.reusable.yml`'s `probe-targets: '["api"]'`.
