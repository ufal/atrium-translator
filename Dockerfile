# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

# Provenance: build-time ARGs -> ENV, read by atrium_paradata.py
ARG ATRIUM_RUNNER_IMAGE=""
ARG ATRIUM_RUNNER_REPO="https://github.com/ufal/atrium-translator"
ARG ATRIUM_RUNNER_REF=""
ENV ATRIUM_RUNNER_IMAGE=${ATRIUM_RUNNER_IMAGE} \
    ATRIUM_RUNNER_REPO=${ATRIUM_RUNNER_REPO} \
    ATRIUM_RUNNER_REF=${ATRIUM_RUNNER_REF} \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/cache/huggingface

WORKDIR /app

# Install all requirements excluding test dependencies for the production image
COPY requirements.txt ./
COPY service/requirements.txt ./service/requirements.txt
RUN pip install -r requirements.txt -r service/requirements.txt

COPY . .

# Non-root runtime user owning only the HF cache and data mountpoint.
# /app remains owned by root to enforce source immutability.
RUN useradd --create-home --uid 10001 atrium \
    && mkdir -p /cache/huggingface /data \
    && chown -R atrium:atrium /cache /data
USER atrium

ENTRYPOINT ["python", "main.py"]
CMD ["/data/input", "--alto", "--formats", "alto.xml", "--target_lang", "en", "-o", "/data/output"]


# ---------------------------------------------------------------------------
# API surface — published as :<version>-api (issue #55)
#
# Before this stage existed, translator's FastAPI service was reachable only through a
# docker-compose `entrypoint:` override on the BATCH image, so no runnable API image was
# ever published and there was nothing for ARÚP/ARÚB to deploy on Kubernetes.
# service/requirements.txt is already installed in `base` (see the pip install above),
# so this stage only has to declare how to serve.
#
# NOTE ON PERMISSIONS: unlike the other four repos, `base` deliberately leaves /app
# root-owned to enforce source immutability (see the useradd comment above — only
# /cache and /data are chowned). The healthcheck script therefore needs to be readable
# and executable by `atrium` without making the tree writable: chmod a+rx as root, no
# chown. A HEALTHCHECK whose probe cannot be read would report `unhealthy` forever
# while the service itself was perfectly fine.
# ---------------------------------------------------------------------------
FROM base AS api

USER root
RUN chmod a+rx /app/service/healthcheck.py
USER atrium

# EXPOSE tracks the DEFAULT port: it is image metadata and cannot read $PORT at
# runtime. Set PORT to move the listener, and publish with `-p <port>:<port>` to
# match. (issue #58)
EXPOSE 8000

# STOPSIGNAL is the default (SIGTERM) — declared explicitly so a future edit cannot
# change it silently; service/api.py's lifespan chains to uvicorn's own handler for it
# via serve_lifecycle (service/atrium_service.py).
STOPSIGNAL SIGTERM

# PORT and HOST are read by service/api.py's __main__ block; PORT is also the port
# service/healthcheck.py probes, which is why setting it used to make the container
# permanently unhealthy — the probe moved and the listener did not. Declared here so
# `docker inspect` is self-documenting and so the probe still has a value if the code
# default ever drifts. (issue #58)
#
# GRACEFUL_SHUTDOWN_S carries the `--timeout-graceful-shutdown 20` that used to sit on
# the ENTRYPOINT line. It bounds uvicorn's wait for in-flight requests.
# Note this service's slow work happens INSIDE the request — one retried LINDAT call
# per chunk — so a large document can legitimately outlive this budget and be cut
# short. Raise it together with the deployment's grace period for that workload
# (docs/k8s_deployment.md, "Known limits").
ENV PORT=8000 GRACEFUL_SHUTDOWN_S=20

# `python -m service.api`, NOT `python service/api.py`: a script launch puts
# sys.path[0] at /app/service with no package context, so `from main import ...` — this
# service's own repo-root import — raises ModuleNotFoundError before the app is
# built. `-m` keeps sys.path[0] at /app — byte for byte the environment the old
# `uvicorn service.api:app` entrypoint ran in, so every repo-root import still
# resolves. (issue #58)
ENTRYPOINT ["python", "-m", "service.api"]
CMD []
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "/app/service/healthcheck.py"]
