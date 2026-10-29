# Configuration

For users and deployers who need to change a setting. It answers where a setting comes from and which source
wins, what each key of `config.txt` does, and which environment variables the backends, the language identification
and the guard read. The command line's flags are in [the command-line reference](reference/cli.md); the limits, each
an environment variable, in the service's [Limits](../service/README.md#limits) table; the service's own settings in
its [Configuration (environment)](../service/README.md#configuration-environment).

On this page: [Where a setting comes from](#where-a-setting-comes-from) · [config.txt](#configtxt) ·
[Environment variables](#environment-variables): [backend and endpoints](#the-backend-and-its-endpoints),
[`openai_compatible`](#openai_compatible-a-language-model-behind-an-openai-compatible-api),
[`ct2`](#ct2-a-self-hosted-ctranslate2-model), [language identification](#language-identification),
[the re-run](#the-guards-re-run), [provenance](#provenance)

## Where a setting comes from

Settings that vary per **run** (the input, the languages, the formats, the XPath targets, the vocabulary) live in
[config.txt](../config.txt). Settings that vary per **deployment** (the endpoints, the backend's connection, the
limits, the port) are environment variables, listed with their defaults in [.env.example](../.env.example).

For the settings that exist in more than one place the order is the same, highest first:

**command-line flag → `config.txt` key → environment variable → built-in default.**

| Setting                               | Flag                    | `config.txt` key      | Environment variable  | Default   |
|---------------------------------------|-------------------------|-----------------------|-----------------------|-----------|
| the backend                           | `--backend`             | `translation_backend` | `TRANSLATION_BACKEND` | `lindat`  |
| the output mode                       | `--output-mode`         | `output_mode`         | `OUTPUT_MODE`         | `replace` |
| the fallback source language          | `--default-source-lang` | `default_source_lang` | `DEFAULT_SOURCE_LANG` | `cs`      |
| a failed XSD check fails the document | `--xsd-strict`          | —                     | `XSD_STRICT`          | off       |
| the schema the service validates with | — (the CLI: `--xsd`)    | —                     | `XSD_PATH`            | none      |

The shipped config.txt sets `translation_backend`, `output_mode` and `default_source_lang`, so for a run that
reads it the environment variable applies only when the key is commented out. The service reads no config.txt
keys: its settings are all environment variables.

Instead of passing every argument on the command line, a run can take its defaults from `config.txt` (or the file
`--config` names). A command-line argument always wins; the file supplies defaults only for arguments that are not
passed.

## config.txt

```ini
[DEFAULT]
input_path = ./data_samples/my_documents
source_lang = auto
target_lang = en
default_source_lang = cs
formats = alto.xml
output = ./data_samples/in-place_translated_files/alto
fields = amcr-fields.txt

# Optional: path to a vocabulary CSV file (source_lemma,target_translation).
# Leave blank or comment out to disable.
vocabulary = data_samples/vocabulary.csv

translation_backend = lindat
output_mode = replace
```

| Key                   | Shipped value                                   | Flag                    | Without the key                                                       |
|-----------------------|-------------------------------------------------|-------------------------|-----------------------------------------------------------------------|
| `input_path`          | `./data_samples/my_documents`                   | the positional argument | the argument is required                                              |
| `output`              | `./data_samples/in-place_translated_files/alto` | `--output`, `-o`        | `./translated_<target_lang>`                                          |
| `source_lang`         | `auto`                                          | `--source_lang`         | `cs`                                                                  |
| `target_lang`         | `en`                                            | `--target_lang`         | `en`                                                                  |
| `default_source_lang` | `cs`                                            | `--default-source-lang` | `DEFAULT_SOURCE_LANG`, then `cs`                                      |
| `formats`             | `alto.xml`                                      | `--formats`             | `xml`; with `--xpaths` and `alto.xml` here, `xml,txt`                 |
| `fields`              | `amcr-fields.txt`                               | `--xpaths`              | no XPath targets: each record fails (exit 3); `--formats xml`, exit 1 |
| `vocabulary`          | `data_samples/vocabulary.csv`                   | `--vocabulary`          | no vocabulary (a key naming a missing file is ignored)                |
| `translation_backend` | `lindat`                                        | `--backend`             | `TRANSLATION_BACKEND`, then `lindat`                                  |
| `output_mode`         | `replace`                                       | `--output-mode`         | `OUTPUT_MODE`, then `replace`                                         |

**`formats` says which files a folder scan picks up,** and nothing more: the shipped `alto.xml` takes only
`*.alto.xml`, and each file's root element then decides ALTO or metadata. `--formats` on the command line also
names the mode for the whole run: a list with `alto.xml` is ALTO mode, so `--alto` becomes optional; any other list
is metadata mode. A command line with `--xpaths` and no `--formats` scans `xml,txt` when this key holds `alto.xml`.

**`source_lang = auto` means detection.** FastText identifies the language unless `--source_lang` is given, and a
guess is used only when the text is long enough, the guess confident enough and the language one the backend
translates; otherwise the element's own label, the document's language and then `default_source_lang` decide
([Source-language identification](how-it-works.md#source-language-identification)). On a single-language corpus,
set `source_lang = cs`: no detection runs, and the CC BY-NC FastText model is not loaded.

**The backing services are not keys.** `TRANSLATION_URL` and `UDPIPE_URL` are environment variables, because they
vary per deployment, not per run.

## Environment variables

[.env.example](../.env.example) is the contract: every variable a deployment can set, with its default and a
comment. Copy it to `.env` and edit. Values that vary per run belong in config.txt instead.

**How `.env` reaches the process.** `docker compose` injects it (both services declare `env_file`), but
`python -m service.api` and `python main.py` do **not** (nothing here calls `load_dotenv`), and neither does
Kubernetes. Outside a container, export the variables yourself:

```bash
set -a; . ./.env; set +a
```

The service's own settings (`PORT`, `HOST`, `MAX_UPLOAD_MB`, `ALLOWED_ORIGINS`, `GRACEFUL_SHUTDOWN_S`,
`AMCR_FIELDS_PATH`, `VOCABULARY_PATH`, `LOG_LEVEL`, `RELOAD`) are described in
[service/README.md](../service/README.md#configuration-environment), and every limit (`TRANSLATION_CHUNK_CHARS`,
`LINDAT_TIMEOUT_S`, `LINDAT_MAX_RETRIES`, `LINDAT_GUARD_RETRIES`, `TRANSLATION_RERUN_ROUNDS`, `UDPIPE_TIMEOUT_S`,
the `LLM_*` and `CT2_*` caps, …) in its [Limits](../service/README.md#limits) table. The batch CLI reads the same
variables. The rest, shared by the CLI and the service:

### The backend and its endpoints

| Variable                                    | Default                                                  | Effect                                                                                                                    |
|---------------------------------------------|----------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| `TRANSLATION_BACKEND`                       | `lindat`                                                 | `lindat`, `openai_compatible` (then set the `LLM_*` values) or `ct2` (then the `CT2_*` values and `requirements-ct2.txt`) |
| `TRANSLATION_URL` (alias `LINDAT_BASE_URL`) | `https://lindat.mff.cuni.cz/services/translation/api/v2` | the translation API of the `lindat` backend; point it at a self-hosted service or a stub                                  |
| `UDPIPE_URL`                                | `https://lindat.mff.cuni.cz/services/udpipe/api/process` | UDPipe 2, for the vocabulary's lemma matching (`lindat` with a vocabulary); the same name as in atrium-nlp-enrich         |
| `LINDAT_MIN_INTERVAL_S`                     | `0.0`                                                    | the minimum seconds between requests to LINDAT                                                                            |
| `LINDAT_BACKOFF_BASE_S`                     | `1.0`                                                    | a retry sleeps `base × 2^attempt` plus jitter; also between degenerate-reply re-requests from the second one on           |

Unset, both URLs reach the LINDAT hosts. The endpoint the `lindat` backend actually resolved is what the paradata
records as `translation_api`. Point both at a local stub and the pipeline runs offline with no code change, which
is what the repository's integration tests do (`tests/integration/stub_lindat.py`).

Each remote backend retries a network error, HTTP 429 or 5xx up to its `*_MAX_RETRIES` limit, sleeping
`*_BACKOFF_BASE_S × 2^attempt` plus jitter, and spaces requests at least `*_MIN_INTERVAL_S` apart.

### `openai_compatible`: a language model behind an OpenAI-compatible API

| Variable                | Default | Effect                                                                                                                                                                              |
|-------------------------|---------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `LLM_BASE_URL`          | —       | the API's base URL, e.g. a local vLLM server's `/v1`; required                                                                                                                      |
| `LLM_MODEL`             | —       | the model id; required. No model id is hard-coded                                                                                                                                   |
| `LLM_API_KEY`           | —       | a **secret**: keep it out of the image and the manifest. A local server may need none                                                                                               |
| `LLM_PROVIDER`          | —       | an optional provider label                                                                                                                                                          |
| `LLM_LANGUAGES`         | —       | a CSV of the language codes the backend may handle. Unset, it lists none, and refuses no pair                                                                                       |
| `LLM_EXTRA_BODY`        | —       | a JSON object merged into every request body, e.g. `{"chat_template_kwargs": {"enable_thinking": false}}` for a vLLM serving Qwen3.8; not a JSON object: the backend does not start |
| `LLM_LICENSE_COMPONENT` | —       | the `para_config.txt` component recorded for the model instead of `llm_api` (the provider's terms): `qwen3_8` for a self-served Qwen3.8-27B (Apache-2.0)                            |
| `LLM_MIN_INTERVAL_S`    | `0.0`   | the minimum seconds between requests                                                                                                                                                |
| `LLM_BACKOFF_BASE_S`    | `1.0`   | as `LINDAT_BACKOFF_BASE_S`                                                                                                                                                          |
| `LLM_GUARD_MIN_CHARS`   | `16`    | below this source length the ratio guards do not apply                                                                                                                              |
| `LLM_GUARD_MIN_RATIO`   | `0.25`  | reject output shorter than this multiple of the source                                                                                                                              |
| `LLM_GUARD_MAX_RATIO`   | `4.0`   | reject output longer than this multiple of the source                                                                                                                               |

The bake-off's recommended setup (Qwen3.8-27B in bf16 on a local vLLM) is in
[the backend decision](decisions/backend-choice.md).

### `ct2`: a self-hosted CTranslate2 model

Needs `pip install -r requirements-ct2.txt`; the published images do not include it.

| Variable              | Default   | Effect                                                                                                                                                 |
|-----------------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| `CT2_MODEL_DIR`       | —         | the converted CTranslate2 model; required                                                                                                              |
| `CT2_MODEL_FAMILY`    | `eurollm` | `eurollm`, `madlad`, `nllb` or `opus`; decides the prompt or tokenisation and the licence component recorded                                           |
| `CT2_SP_MODEL`        | —         | the SentencePiece model, for `madlad`, `nllb` and `opus`                                                                                               |
| `CT2_TOKENIZER_DIR`   | —         | `eurollm`: the Hugging Face tokenizer directory. Unset: `CT2_MODEL_DIR` if the converter copied the tokenizer there, else the folder of `CT2_SP_MODEL` |
| `CT2_DEVICE`          | `cpu`     | `cpu` or `cuda`. On `cuda`, CTranslate2 4.x needs CUDA 12's cuBLAS (12.8 or newer on a Blackwell card)                                                 |
| `CT2_COMPUTE_TYPE`    | `int8`    | `int8` runs on every device; `float16` and the like need `cuda`. Checked against the device before the model loads; a bad value names the valid ones   |
| `CT2_LANGUAGES`       | —         | a CSV of the language codes the backend may handle                                                                                                     |
| `CT2_GUARD_MIN_CHARS` | `16`      | as `LLM_GUARD_MIN_CHARS`                                                                                                                               |
| `CT2_GUARD_MIN_RATIO` | `0.25`    | as `LLM_GUARD_MIN_RATIO`                                                                                                                               |
| `CT2_GUARD_MAX_RATIO` | `4.0`     | as `LLM_GUARD_MAX_RATIO`                                                                                                                               |

The model, and EuroLLM's tokenizer, load at start-up, and one short generation follows: a model that cannot run
stops the service or the CLI there, naming the fix (or `CT2_DEVICE=cpu`). How to convert and run EuroLLM:
[the candidate survey](evaluation/backends.md#eurollm-utter-project).

### Language identification

Only with `source_lang=auto`.

| Variable                 | Default                                    | Effect                                                                                                                                                          |
|--------------------------|--------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DEFAULT_SOURCE_LANG`    | `cs`                                       | the language an untrusted detection falls back to, after the element's label and the document's language                                                        |
| `LANG_ID_MIN_CONFIDENCE` | `0.5`                                      | the FastText score a candidate needs                                                                                                                            |
| `LANG_ID_MIN_LETTERS`    | `20`                                       | shorter texts are not sent to FastText; they inherit their label or the document's language                                                                     |
| `LANG_ID_LANGUAGES`      | —                                          | a CSV of ISO 639-1 codes that narrows the accepted languages below what the backend supports                                                                    |
| `LANG_ID_MODEL_PATH`     | — (images: `/opt/models/fasttext-lid.bin`) | the FastText model file. Unset, a checkout downloads the pinned Hub revision into `HF_HOME` once. A path that is not a file: no detection, the fallbacks decide |
| `HF_HOME`                | — (images: `/cache/huggingface`)           | where the Hugging Face cache lives; Compose mounts a named volume there                                                                                         |

The published images carry the model since v1.6.0-beta; earlier images download it at
start ([Models and caches](../service/README.md#models-and-caches)).

### The guard's re-run

| Variable                    | Default | Effect                                                                                                                      |
|-----------------------------|---------|-----------------------------------------------------------------------------------------------------------------------------|
| `TRANSLATION_RERUN_DELAY_S` | `10.0`  | the cool-down before each end-of-document re-run round; added to a service request's duration whenever a segment is flagged |

The number of rounds, `TRANSLATION_RERUN_ROUNDS`, and the LINDAT re-requests, `LINDAT_GUARD_RETRIES`, are limits
([Limits](../service/README.md#limits)). How the guard works:
[The degenerate-output guard](how-it-works.md#the-degenerate-output-guard).

### Provenance

`ATRIUM_RUNNER_REPO`, `ATRIUM_RUNNER_REF` and `ATRIUM_RUNNER_IMAGE` stamp the paradata's `repository`, `runner_ref`
and `docker_image`; the images set them at build. `ATRIUM_REQUEST_ID` is copied into the paradata's `request_id`
(read once, so it identifies a batch run, not an HTTP request). `ATRIUM_RUN_AGENT`, the IRI of the organisation that
operates the deployment, is recorded as the run's agent: `run_agent` in the paradata, `agent` in the service's
`CreateAction`. `ATRIUM_UID` sets the uid the Compose services run
as ([Docker and Compose](usage.md#docker-and-compose)).
