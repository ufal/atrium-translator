"""Repo-local declarations for tests/test_openapi_contract.py (atrium-project#32 round 2).

Never vendored, never in para-drift, never in the ruff [format] exclude — unlike the
canonical test that reads it, this file's content is per repo by design: which services the
repo runs, where their committed specs live, which settings could reach a spec, and which
requirement files pin fastapi and pydantic. See the canonical test's docstring.
"""

from __future__ import annotations

#: One entry per HTTP service of this repo. `primary`: the domain endpoints whose JSON 200
#: must be a named model (strategy §4.2). /translate answers XML by default; its JSON 200 is
#: the opt-in `response_format=json` shape.
SERVICES = [
    {
        "service": "atrium-translator",
        "app": "service.api:app",
        "spec": "service/openapi.json",
        "primary": ["/translate"],
    },
]

#: Settings besides every [limit] variable (which the test perturbs from tool_limits.LIMITS)
#: that a deployment changes and that must not change the spec: the backend and its host, the
#: default output mode (resolved per request, never a declared default) and the CORS origins.
ENV_PERTURB = {
    "TRANSLATION_BACKEND": "llm",
    "TRANSLATION_URL": "https://translation.example.org/api/v2/",
    "OUTPUT_MODE": "append",
    "ALLOWED_ORIGINS": "https://example.org",
}

#: Every requirements file a lane or an image installs fastapi or pydantic from: the api image
#: (service/requirements.txt, with requirements.txt) and the light and integration test lanes
#: (requirements-test.txt).
PIN_FILES = ["service/requirements.txt", "requirements-test.txt"]

#: Run before the app is imported (``MODULE:FUNCTION``), or None: the backend and the language
#: identifier are built in the lifespan, which the test never enters.
PREPARE = None
