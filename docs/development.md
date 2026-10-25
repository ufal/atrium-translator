# Development

For contributors: how to run the checks, where things are in the repository, and how a translation backend plugs
in. How to contribute (branches, pull requests, commit messages), the release procedure and the release history are
in [CONTRIBUTING.md](../CONTRIBUTING.md); the reasons behind the design, in [decisions/](decisions/).

On this page: [Checks](#checks) · [Project structure](#project-structure) · [Code map](#code-map) ·
[The backend interface](#the-backend-interface)

## Checks

```bash
pip install -r requirements.txt -r service/requirements.txt -r requirements-test.txt
pytest -m "not slow" --tb=short                     # the suite; no model, GPU or network needed
ruff check . && ruff format --check .
python atrium_openapi.py export --app service.api:app --out service/openapi.json   # after an API change
python3 ../atrium-project/tools/docs_check.py --repo atrium-translator   # links, anchors, headings (a hub checkout beside this one)
python3 ../atrium-project/tools/runbook_check.py --repo .                # service/README.md follows the runbook outline
```

`tests/integration/` is the live-backend lane: `tests/integration/stub_lindat.py` stands in for LINDAT offline, and
`ATRIUM_LIVE_BACKEND=1 pytest -m integration` runs it against the real hosts, by hand before a release. Tests that load
a model, call an external API or need a GPU are marked `slow`. Passing tests show that the code does what it
describes; they do not measure translation quality ([evaluation](evaluation/backends.md)). More in CONTRIBUTING's
[Code conventions and testing](../CONTRIBUTING.md#code-conventions--testing).

## Project structure

```text
atrium-translator/
├── main.py                    # batch CLI entry point: ALTO or metadata routing
├── utils.py                   # ALTO and XML parsing, dual-pass alignment, CSV logs, XSD validation
├── load_vocab.py              # vocabulary builder (keyword-extract release, or AMČR OAI-PMH + TEATER GraphQL → CSV)
├── config.txt                 # per-run configuration (paths, languages, formats, backend)
├── para_config.txt            # the tool version (single source of truth) and the component → licence table
├── amcr-fields.txt            # the XPath list for AMČR metadata translation
├── amcr-inputs.txt            # AMČR OAI-PMH GetRecord URLs, a sample URL-list input
├── .env.example               # the environment contract; copy to .env
├── Dockerfile                 # two stages: `base` (batch CLI) and `api` (HTTP service)
├── docker-compose.yml         # local deployment: `translator` and `api` (profile)
│
│   # Shared canonical files (vendored from ufal/atrium-project, never edited here)
├── atrium_paradata.py         # run-level provenance (paradata) logger
├── atrium_document.py         # the cross-tool ATRIUM document record (+ .schema.json)
├── atrium_vocab.py            # SKOS controlled-label registry (+ .schema.json)
├── atrium_rocrate.py          # RO-Crate (JSON-LD) export of document records; the service's CreateAction
├── atrium_limits.py           # the limits contract: declaration, parsing, /info report (atrium-project#53)
├── atrium_openapi.py          # OpenAPI export, digest and compatibility check
├── para_licenses.py           # the licence summary computed from the components a run used
├── check_version.py           # release gate: tag == CITATION.cff == para_config.txt
│
├── tool_limits.py             # this tool's limits (TRANSLATION_CHUNK_CHARS, LINDAT_*, LLM_*, CT2_*)
│
├── processors/                # (namespace package, no __init__.py)
│   ├── backend.py             # TranslationBackend protocol + get_backend() registry
│   ├── translator.py          # LINDAT CUBBITT client + Tag-and-Protect vocabulary
│   ├── llm_translator.py      # OpenAI-compatible LLM backend (prompt glossary, guards)
│   ├── ct2_translator.py      # CTranslate2 self-hosted backend (`--backend ct2`)
│   ├── lemmatizer.py          # UDPipe-based lemmatiser for vocabulary term matching
│   ├── identifier.py          # FastText language identification (ISO 639-3 → 639-1)
│   ├── language.py            # source-language policy: detected → label → document → default; pair checks
│   ├── chunking.py            # shared sentence-aware text chunker (priority-ordered)
│   ├── http_retry.py          # shared throttle + bounded exponential back-off
│   ├── quality.py             # degenerate-output detector (loops, empty, runaway, truncation, mixed script)
│   ├── limit_notes.py         # collects the `limits_applied` notes of a run or request
│   └── vocab.py               # vocabulary CSV loader
├── service/                   # the HTTP surface; see service/README.md
│   ├── api.py                 # FastAPI app: /translate, /info, /health, /ready
│   ├── openapi.json           # the committed API contract
│   ├── atrium_service.py      # (canonical) shared meta-contract helpers
│   ├── healthcheck.py         # (canonical) stdlib-only Docker HEALTHCHECK probe
│   └── requirements.txt       # service-only dependencies (fastapi, uvicorn)
├── tests/                     # pytest suite; tests/integration/ is the live-backend lane
├── eval/                      # bakeoff.py (backend comparison, #4) · langid_report.py (language-ID tuning)
│                              # · lindat_probe.py (is one LINDAT replica broken?) · results/
├── docs/                      # this documentation
├── agent_dev_logs/            # derived timeline, per-issue digests and plans
└── data_samples/
    ├── vocabulary.csv         # Czech → English domain vocabulary (AMČR/TEATER terms)
    ├── my_documents/          # sample inputs (an anonymised ALTO page, AMČR metadata records)
    ├── in-place_translated_files/   # replace-mode outputs: alto/ and xml/, each with logs, records and paradata/
    ├── appended_translated_files/   # append-mode outputs, the same layout
    └── ct2_translated_files/        # the AMČR records through ct2 (EuroLLM-1.7B); see data_samples/README.md
```

## Code map

| Module                                    | What it does                                                                                     |
|-------------------------------------------|--------------------------------------------------------------------------------------------------|
| `main.py`                                 | the CLI: argument parsing, input discovery and URL download, the per-file loop, record, paradata |
| `utils.py`                                | ALTO dual-pass processing, metadata processing, XSD validation, output modes                     |
| `processors/backend.py`                   | the `TranslationBackend` protocol and the backend registry                                       |
| `processors/translator.py`                | `LindatTranslator`: CUBBITT over HTTP, with Tag-and-Protect                                      |
| `processors/llm_translator.py`            | `LLMTranslator`: any OpenAI-compatible chat API, glossary in the prompt                          |
| `processors/ct2_translator.py`            | `CT2Translator`: self-hosted CTranslate2 models                                                  |
| `processors/identifier.py`                | FastText language identification                                                                 |
| `processors/language.py`                  | the ISO 639-3 → 639-1 map, the source-language rules, the language-pair check                    |
| `processors/quality.py`                   | the degenerate-output check applied to every reply                                               |
| `processors/lemmatizer.py`                | UDPipe lemmatisation over HTTP                                                                   |
| `processors/vocab.py`                     | vocabulary CSV loading and term matching                                                         |
| `processors/chunking.py`, `http_retry.py` | text chunking and the shared retry and throttle policy                                           |
| `service/api.py`                          | the FastAPI service around the same processing functions                                         |
| `load_vocab.py`                           | building the vocabulary from keyword-extract's release, or from AMČR and TEATER                  |

## The backend interface

A `TranslationBackend` `typing.Protocol` in `processors/backend.py` defines what every backend must offer:

```python
class TranslationBackend(Protocol):
    name: str
    supports_glossary: bool

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str: ...

    def supported_languages(self) -> list[str]: ...
```

`get_backend(name)` returns the implementation; the default is `"lindat"` (`LindatTranslator`), and
`"openai_compatible"` and `"ct2"` are the other registered names. A new backend is an adapter class and a registry
entry, with no change to `main.py`, `utils.py` or `service/api.py`. Registering `ct2` costs nothing at import time:
its module defers `ctranslate2` and `sentencepiece` to first use. The protocol is `@runtime_checkable` and declares
data members, so `isinstance(obj, TranslationBackend)` also checks that `name` and `supports_glossary` exist.

Beyond the protocol, `main.process_single_file` relies on a small surface every backend exposes: `vocabulary` (a
dict), `reset_protected_count()` and the `protected_count` property, and, optionally, `license_components(vocab_loaded)`
so the paradata lists the components the backend used ([licences](reference/licences.md)). `supported_models` and
`supported_languages()` say which pairs the backend translates: LINDAT's are the source side of its model pairs. They
are what the language-pair check holds an explicit pair to (a pair outside is refused, never rerouted) and what
`--source_lang auto` checks a detection against. `supports_glossary` decides whether the pipeline runs Tag-and-Protect
or hands the matching terms to the backend. A backend with a model to load offers `warm()`, which loads it and runs
one short generation at start-up.

### The failure contract every backend shares

An HTTP 200 is not a translation ([the guard](how-it-works.md#the-degenerate-output-guard)). The contract:

- `processors/quality.py::degeneration_reason(source, translated)` is the single judge: empty output, runaway
  length, truncation, repetition loops and a word mixing Latin and Cyrillic letters, each compared with the source.
  It has no dependencies, so any backend can use it.
- A backend that gives up on a reply raises `DegenerateTranslationError` (a `TranslationError` subclass, in
  `processors/translator.py`). `LindatTranslator` re-requests first (`LINDAT_GUARD_RETRIES`, default 2); the LLM and
  `ct2` guards raise it for their ratio checks and for loops the ratio cannot see.
- `utils.py` treats that error, and any reply that fails the detector (which also covers a backend with no guard of
  its own), as a **per-segment** failure: flagged, re-run after the whole document (`TRANSLATION_RERUN_ROUNDS`,
  `TRANSLATION_RERUN_DELAY_S`), and kept as source text with `status=untranslated` in the log if it never recovers.
  Any other `TranslationError` (transport, configuration) still skips the whole file.

A new backend therefore needs no guard to be safe, but should raise `DegenerateTranslationError` rather than return
text it knows is bad.

A backend whose decoding is deterministic should also offer `retrying(round)`, a context manager that `utils.py`
enters around the requests of the end-of-document re-run (round 1, 2, …): asked the same thing again, it would return
the same reply. `CT2Translator` widens its beam inside the block (thread-local, since one backend object serves every
request). A backend without the method is called as before: asking LINDAT again reaches another replica, and the
OpenAI-compatible backend (temperature 0, a remote model) has shown no such need.
