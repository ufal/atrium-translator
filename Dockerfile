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

# ── Distro security patches, applied at build time ───────────────────────────
# `python:3.11-slim` is a floating TAG, and nothing in this ecosystem bumps it:
# no repo declares a `docker` dependabot ecosystem (docker_gha_roadmap.md, H6),
# so the base layer is whatever Docker Hub last rebuilt. On 2026-09-13 that layer
# carried perl-base 5.40.1-6 with three FIXABLE CRITICAL CVEs — CVE-2026-13221,
# CVE-2026-42496 and CVE-2026-8376, all fixed in 5.40.1-6+deb13u1. The release
# gate in atrium-project's docker-tool.reusable.yml ("Fail the release on fixable
# CRITICAL vulnerabilities") therefore failed on both matrix targets, and because
# the promotion step is `if: success()`, v1.0.0-beta was published by DIGEST ONLY:
# the `:1.0.0-beta` and `:latest` tags were never applied.
#
# `upgrade` rather than `install --only-upgrade perl-base`, deliberately. The gate
# blocks on *fixable* CRITICALs — precisely those the distro already ships a patch
# for — so the fix that matches the gate's own definition is "apply the distro's
# available patches", not a package name that has to be edited by hand the next
# time a different one is announced.
#
# CACHE INTERACTION, which is what makes this hold rather than run once: the build
# uses `cache-from: type=gha`, so an apt layer high in the file would be served
# from cache forever and silently stop patching. It sits HERE, immediately after
# the ENV block that embeds ATRIUM_RUNNER_REF, because CI passes that as
# `github.ref_name` — a value unique to each release tag. The ENV layer therefore
# changes on every release, busting this layer with it, so every released image is
# scanned against a freshly patched base while day-to-day `test` pushes still hit
# the cache. Do not move this above the ENV block.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install all requirements excluding test dependencies for the production image
COPY requirements.txt ./
COPY service/requirements.txt ./service/requirements.txt
RUN pip install -r requirements.txt -r service/requirements.txt

# FastText language identification for `source_lang=auto`, baked in (atrium-project#77): the image
# needs no egress for it and cannot start loading another model than its release did. The same file
# and Hub commit as atrium-ocr-postprocess's image: facebook/fasttext-language-identification's
# model.bin, 1,176,355,829 bytes, CC BY-NC 4.0. The build fails unless size and SHA-256 match.
# To refresh: `curl -sI https://huggingface.co/facebook/fasttext-language-identification/resolve/main/model.bin`
# gives x-repo-commit (the revision), x-linked-etag (the SHA-256) and x-linked-size; change
# LANG_ID_REVISION in processors/identifier.py to the same commit.
ARG FASTTEXT_REVISION=3af127d4124fc58b75666f3594bb5143b9757e78
ARG FASTTEXT_SHA256=8ded5749a2ad79ae9ab7c9190c7c8b97ff20d54ad8b9527ffa50107238fc7f6a
ARG FASTTEXT_SIZE=1176355829
RUN mkdir -p /opt/models && python - <<'PY'
import hashlib, os, time, urllib.request
revision, sha256, size = os.environ["FASTTEXT_REVISION"], os.environ["FASTTEXT_SHA256"], int(os.environ["FASTTEXT_SIZE"])
url = f"https://huggingface.co/facebook/fasttext-language-identification/resolve/{revision}/model.bin?download=true"
target = "/opt/models/fasttext-lid.bin"
request = urllib.request.Request(url, headers={"User-Agent": "atrium-translator-docker-build/1.0"})
for attempt in range(1, 6):
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
            for block in iter(lambda: response.read(1 << 20), b""):
                digest.update(block)
                out.write(block)
        break
    except OSError as exc:
        print(f"download attempt {attempt} failed: {exc}", flush=True)
        if attempt == 5:
            raise
        time.sleep(10 * attempt)
if os.path.getsize(target) != size:
    raise SystemExit(f"{target} is {os.path.getsize(target)} bytes, expected {size}")
if digest.hexdigest() != sha256:
    raise SystemExit(f"{target} has SHA-256 {digest.hexdigest()}, expected {sha256}")
PY
# The file is written 0644 by root (umask 022), readable by the runtime user. No later chmod or
# chown touches it: on overlayfs that would copy the 1.18 GB file into a second layer.
ENV LANG_ID_MODEL_PATH=/opt/models/fasttext-lid.bin

COPY . .

# Non-root runtime user owning only the HF cache, the data mountpoint and its home.
# /app remains owned by root to enforce source immutability.
#
# Owned atrium:0 and group-writable (`g=u`): the arbitrary-UID convention (OpenShift's),
# atrium-project#69 / roadmap B6. docker-compose.yml runs this image as
# `user: "${ATRIUM_UID:-10001}:0"`, so on Linux the container can run as the uid that owns
# the ./data bind mount, and a uid with no passwd entry still reaches /cache, /data and
# $HOME through group 0. HOME is explicit because without a passwd entry it would be `/`.
# The default runtime -- uid 10001 as the owner -- is unchanged.
RUN useradd --create-home --uid 10001 atrium \
    && mkdir -p /cache/huggingface /data \
    && chown -R atrium:0 /cache /data /home/atrium \
    && chmod -R g=u /cache /data /home/atrium
ENV HOME=/home/atrium
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
