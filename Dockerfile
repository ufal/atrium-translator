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
