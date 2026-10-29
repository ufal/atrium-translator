# Using the translator

For users and integrators who want to translate their own documents. It answers how to install the translator,
how to run it on ALTO pages and on metadata records, which output mode to choose, how to use and build the
vocabulary, how to run the Docker images and the service, and what to do when something goes wrong. Every flag is in
[the command-line reference](reference/cli.md); every setting in [configuration.md](configuration.md); deploying and
operating the service, with every error and limit, in [service/README.md](../service/README.md).

On this page: [Setup](#setup) · [ALTO pages](#translate-alto-pages) ·
[AMČR metadata records](#translate-amčr-metadata-records) ·
[Replace or append](#choose-the-output-mode-replace-or-append) · [Use a vocabulary](#use-a-vocabulary) ·
[Build the vocabulary](#build-the-vocabulary) · [Docker and Compose](#docker-and-compose) ·
[The HTTP service](#the-http-service) · [As one stage of the pipeline](#as-one-stage-of-the-pipeline) ·
[From a coding agent](#from-a-coding-agent) · [Troubleshooting](#troubleshooting)

## Setup

Python 3.11, the version the images and CI run.

```bash
git clone https://github.com/ufal/atrium-translator.git
cd atrium-translator
python -m venv venv
source venv/bin/activate              # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` installs `fasttext-wheel`, a pre-built FastText, so no C++ compiler is needed where a wheel
exists for your Python. Where none exists, pip builds it from source and needs one.

The repository is not a pip-installable package: every command runs from the repository root, as
`python main.py`. The optional backends have their own requirement files: `requirements-llm.txt` for
`openai_compatible`, `requirements-ct2.txt` for `ct2`. The service needs `service/requirements.txt` as well.

## ALTO or metadata: who decides

A file's **root element** decides when the command line names no mode: `<alto>` (any ALTO version) is translated
as an ALTO page, any other root as a metadata record, for which the XPath targets come from config.txt's
`fields =` key. Naming `--alto`, `--xpaths` or `--formats` decides for the whole run instead; a file whose root
says otherwise is still translated in the named mode, with a warning. `/translate` decides the same way, by the
same function (`utils.xml_kind`), when `is_alto` is not sent (since v1.6.0-beta; before, the command line needed
a mode flag and the service took every upload for ALTO).

In a folder scanned without a mode flag, config.txt's `formats` only says which files are picked up; each file's
root then decides. A file named on the command line without a mode flag is taken when it is XML, whatever its name.

Since v1.6.0-beta, also:

- `-o page_en.xml` with **one input file** names the translated file (it used to be created as a directory of that
  name, with the translation inside); `-o` with a folder or a URL list is the output directory, as before;
- a `.txt` file **inside a folder** is a list of URLs, as a `.txt` named on the command line is (it used to be
  parsed as XML and fail);
- in a **batch** (a folder or a URL list), `--document-json` and `--document-json-out` name directories: each
  document's baseline is `<dir>/<doc_id>.document.json` when it exists, and its record is written to
  `<out dir>/<doc_id>.document.json`. One file for a whole batch is refused (exit 1): its `doc_id` keyed every
  record, and each record overwrote the last.

## Translate ALTO pages

An `<alto>` root is enough: without a mode flag each file's root element decides. `--alto`, or `--formats` with
`alto.xml` on the command line, makes the whole run ALTO. ALTO mode translates the `CONTENT` attributes of the
`String` elements and nothing else.

```bash
python main.py ./data_samples/my_documents --alto --formats alto.xml --target_lang en
```

In config.txt, `formats = alto.xml` (the shipped value) only says which files a folder scan picks up. The default
target language is English (`en`).

An example:

- Input: [MTX201501307_anon.alto.xml](../data_samples/my_documents/MTX201501307_anon.alto.xml)
- Output, `replace`: [MTX201501307_anon_en.alto.xml](../data_samples/in-place_translated_files/alto/MTX201501307_anon_en.alto.xml)
- Output, `append`: [MTX201501307_anon_en.alto.xml](../data_samples/appended_translated_files/alto/MTX201501307_anon_en.alto.xml)

Each `TextBlock` is translated as a whole, for the quality of the translation, and the words are then distributed
back over the `String` elements of each `TextLine`, so the layout of the page is kept. How:
[ALTO dual-pass reconstruction](how-it-works.md#alto-dual-pass-reconstruction).

`--fast-align` places each line's words by its source word count instead of translating each line as an anchor:
far fewer requests, slightly coarser line splits.

## Translate AMČR metadata records

Metadata mode translates the text of the elements that a list of XPaths names, one per line. It was designed for
[AMČR](https://api.aiscr.cz/) records, bare or in OAI-PMH envelopes, and works with any well-formed XML and any
namespace.

AMČR's production run translates the records in `replace` mode and validates them against AMČR 2.2
(translator#46):

```bash
python main.py ./data_samples/my_documents --xpaths amcr-fields.txt --formats xml \
    --source_lang cs --target_lang en --output-mode replace \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd -o out
```

- **`--formats`.** The shipped config.txt sets `formats = alto.xml`, so a folder scan picks up only `*.alto.xml`.
  A command line with `--xpaths` and no `--formats` scans `xml,txt` instead and is a metadata run.
- **A directory scan in metadata mode leaves `*.alto.xml` files out** and says so; a file named on the command
  line is still processed.
- **`--source_lang cs`** skips language detection, and with it the CC BY-NC FastText model, on a Czech-only
  corpus.
- **`--xsd`** validates each record (inside an OAI-PMH envelope, each `oai:metadata` payload), logs each verdict
  and ends with one `XSD: N/M record(s) valid` line. It warns; with `--xsd-strict` an invalid record counts as a
  failed document (exit 3; the XML is still written). The service validates against a local `XSD_PATH` set by its
  deployment (since v1.6.0-beta).

[amcr-fields.txt](../amcr-fields.txt) holds ten XPaths: `popis`, `poznamka`, `lokalizace`,
`lokalizace_okolnosti`, `souhrn_upresneni`, two `nalez_*/poznamka`, and the three `lokalita/chranene_udaje/*`
fields. For example:

```text
//amcr:amcr/amcr:dokument/amcr:popis
//amcr:amcr/amcr:dokument/amcr:poznamka
//amcr:amcr/amcr:archeologicky_zaznam/amcr:lokalita/amcr:chranene_udaje/amcr:popis
```

The namespace prefix (`amcr:`) is resolved from the document: the AMČR and OAI-PMH namespace URIs are bound to
`amcr:` and `oai:` whatever prefixes the file uses, so the same XPaths work on a bare record and on one in an
envelope.

**Straight from AMČR.** An input can be a `.txt` file of URLs, such as the OAI-PMH `GetRecord` requests in
[amcr-inputs.txt](../amcr-inputs.txt); each is downloaded to `--download-dir` (default
`<output>/downloaded_inputs`) and translated like a local file. `txt` must be among the formats:

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt --formats xml,txt \
    --source_lang cs --output-mode replace \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd --target_lang en
```

**Other XML.** For another schema, write your own XPath list and pass it with `--xpaths`:

```text
# my-fields.txt
//tei:TEI/tei:teiHeader/tei:fileDesc/tei:titleStmt/tei:title
//tei:TEI/tei:text/tei:body//tei:p[@type='abstract']
```

```bash
python main.py ./my_xml_files --xpaths my-fields.txt --target_lang en
```

The namespace prefixes that appear in the document are extracted from it; use the same prefixes in your XPaths
as the XML uses.

Each translated file is written to the output directory with a companion `_log.csv`
([the outputs](reference/outputs.md)).

## Choose the output mode: replace or append

A translation can be written into a document in two ways. This was issue
[#46](https://github.com/ufal/atrium-translator/issues/46)'s central question; it is a switch, so a real corpus
could settle it ([the decision](decisions/output-mode.md)).

| Mode                | What the output contains                                                                                                                                                                                              |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `replace` (default) | The source-language field is **overwritten**. The output is a monolingual mirror of the input; the original text survives only in the sidecar `*_log.csv`. This is what the tool has always done.                     |
| `append`            | The source field is **kept**, and a sibling element carrying `xml:lang="<target>"` is inserted directly after it. The source element is stamped with its own `xml:lang` too, so both halves of the pair are labelled. |

Set it with `--output-mode`, `output_mode` in config.txt, or `OUTPUT_MODE`, in that order of precedence.
`append` follows AMČR's own multilingual convention: an AMČR thesaurus record stores a concept as
`<amcr:heslo xml:lang="cs">` beside `<amcr:heslo_en>` under one `@id`, and `load_vocab.py` reads that pair on
every glossary build.

```bash
python main.py ./data_samples/my_documents \
    --xpaths amcr-fields.txt --source_lang cs \
    --output-mode append \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd
```

**AMČR 2.2 does not accept `append` output.** Validated against the published schema, the 15 shipped records are
valid as source and as `replace` output, and **none** is valid as `append` output, for both halves of the pair:

| Samples (`data_samples/`)        | Valid against AMČR 2.2 | Why not                                                                                                                       |
|----------------------------------|------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `my_documents/` (source)         | 15 / 15                |                                                                                                                               |
| `in-place_translated_files/xml/` | 15 / 15                |                                                                                                                               |
| `appended_translated_files/xml/` | **0 / 15**             | `xml:lang` is not declared on `nazev` / `popis` / `poznamka` / `lokalizace_okolnosti`; the repeated element is "not expected" |

Leaving `xml:lang` off the pair would not help: the repeated element alone is invalid. Where the output must be a
valid AMČR record, use `replace`. `append` stays available for consumers that read the pair without validating, and
a run that appends to AMČR records logs one warning saying so.

`--xsd` resolves the schema's own imports: AMČR 2.2 imports W3C's `xml.xsd` for `xml:lang`, which is served
locally, since lxml's libxml2 fetches nothing over HTTP. Validation applies to metadata output only.

**ALTO in `append` mode keeps the source and adds the translation as an `ALTERNATIVE`.** Every `String` keeps its
`CONTENT`, so the scanned text and its geometry are untouched, and gains ALTO's own element for "an alternative for
the word", carrying the same English that `replace` would have written into that box:

```xml
<TextBlock ID="B1" LANG="cs">
  <TextLine ID="L1">
    <String CONTENT="Záchranný" HPOS="…" …><ALTERNATIVE PURPOSE="translation:en">Rescue</ALTERNATIVE></String>
    <String CONTENT="výzkum" HPOS="…" …><ALTERNATIVE PURPOSE="translation:en">archaeological research</ALTERNATIVE></String>
  </TextLine>
</TextBlock>
```

The block keeps, or gains, its **source** `LANG`, because its `CONTENT` is still in the source language; the
translation half is labelled by the `PURPOSE`. The `String` inventory stays one to one with the source. The split
per `String` is still one block translation distributed over the scanned boxes
([ALTO dual-pass reconstruction](how-it-works.md#alto-dual-pass-reconstruction)), not a word-by-word translation.
`PURPOSE` exists on `ALTERNATIVE` from ALTO 2.1 on. ALTO `append` output **is** schema-valid: the source, `replace`
and `append` ALTO samples all validate against ALTO 3.1.

**ALTO in `replace` mode** writes the English into `CONTENT` and moves any language label the block already
carries (ABBYY writes `LANG="cs"` or `"sk"`) to the target language, so the output no longer claims Czech for
English text. An unlabelled input stays unlabelled.

**`append` is idempotent.** Its output describes itself, so a second pass over an already translated document
skips those fields, and ALTO blocks that already carry a translation `ALTERNATIVE`, instead of translating English
into English. `replace` output carries no such marker.

The mode is recorded in the paradata and in the document record's `translations` block, so an output always says
which contract produced it.

## Use a vocabulary

A vocabulary keeps domain terms at their agreed translation. Pass a CSV with `--vocabulary`, or name it in
config.txt; the shipped config.txt names the bundled list, so a checkout applies it unless you clear the key:

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt \
    --vocabulary data_samples/vocabulary.csv --target_lang en
```

```ini
vocabulary = data_samples/vocabulary.csv
```

A `--vocabulary` path that does not exist stops the run (exit 1); a config.txt key that names a missing file is
ignored. Clear `vocabulary =` in config.txt to translate without one.
The service applies a vocabulary only when the deployment sets `VOCABULARY_PATH`.

With the `lindat` backend the terms are protected by Tag-and-Protect, which calls UDPipe to match single words by
lemma; the `openai_compatible` and `ct2` backends get the matching terms as a glossary in the prompt. Without a
vocabulary no UDPipe call is made. How each works:
[Vocabulary protection](how-it-works.md#vocabulary-protection); the CSV format and the lemma forms per language:
[reference/vocabulary.md](reference/vocabulary.md).

## Build the vocabulary

[load_vocab.py](../load_vocab.py) builds the vocabulary CSV, from a release of atrium-keyword-extract or by
harvesting two sources and merging them:

| Source                                    | Endpoint                                                                          | Method                                                                                                     |
|-------------------------------------------|-----------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| [AMČR](https://api.aiscr.cz/)             | `https://api.aiscr.cz/2.2/oai?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo` | OAI-PMH `ListRecords` with resumption token paging; one pair per `heslo` with a Czech and an English label |
| [TEATER](https://teater.aiscr.cz/)        | `https://teater.aiscr.cz/api/graphql`                                             | GraphQL `exportAll` → JSON export, one pair per concept with `cs` and `en` names; `search`-based fallback  |

```bash
python load_vocab.py                                # full harvest, both sources
python load_vocab.py --skip-teater                  # skip one source
python load_vocab.py --skip-amcr
python load_vocab.py --out my_vocab.csv --delay 0.5 # another output path and request delay
```

The merged vocabulary is written to [data_samples/vocabulary.csv](../data_samples/vocabulary.csv) by default; AMČR
entries take precedence over TEATER on a key collision. `--delay` paces both sources: the pause between AMČR pages
and the minimum gap between TEATER requests.

**From a release instead of a harvest** (atrium-project#72). atrium-keyword-extract owns the project's one
vocabulary and attaches it, CC0, to each of its releases as `atrium-vocabulary-<version>.zip`, with a `.sha256`
beside it. The same CSV can be built from that asset, so the translator's vocabulary is a pinned release rather
than what the live APIs answered on the day of a harvest:

```bash
python load_vocab.py --from-release v1.3.0-beta                     # download, verify the .sha256, write the CSV
python load_vocab.py --from-release v1.3.0-beta --sha256 <digest>   # pin the digest yourself
python load_vocab.py --from-release v1.3.0-beta --asset ./atrium-vocabulary-v1.3.0-beta.zip   # a local copy
python load_vocab.py --from-release v1.3.0-beta --description-chars 0   # no descriptions
```

The pairs are the asset's curated `vocabulary.csv`, keyword-extract's list with its exclusions applied, so the
project curates one list rather than two (agreed with AMČR on
[atrium-project#72](https://github.com/ufal/atrium-project/issues/72), 13 October 2026). Each pair takes the
identity of the concept that won it in the asset's `union_nested.json` (its URI from `amcr_flat.csv` /
`teater_flat.csv`) and that concept's description from `amcr_flat.json` / `teater_flat.json`. An asset that does
not match its checksum, or lacks the curated list or either flat CSV, is refused (exit 3) and the output is left
untouched; a curated pair found in no harvest is kept without provenance and reported.

The committed `vocabulary.csv` is built from `v1.3.0-beta`: 4,715 pairs (the September harvest had 5,087), 1,961 of
them with a description. `tests/test_vocabulary_pin.py` pins it to that asset by checksum. atrium-keyword-extract
has released newer vocabularies since; adopting one is a deliberate change of the CSV, the pin and this paragraph
together.

The vocabulary workflow across the tools, from keyword-extract's curation to this CSV, is
[W7 on the hub's pipelines page](https://ufal.github.io/atrium-project/pipelines/#w7--vocabulary-harvesting--review-the-translators-half).

**TEATER's certificate.** As of 2026-09, `teater.aiscr.cz` serves its TLS certificate without the *RapidSSL TLS
RSA CA G1* intermediate, so `requests` cannot verify it, and the TEATER harvest logs an SSL error and yields
nothing. Do not disable verification: point `REQUESTS_CA_BUNDLE` at a CA bundle that also contains that
intermediate. `api.aiscr.cz` serves the same intermediate in its full chain
(`openssl s_client -showcerts -connect api.aiscr.cz:443`).

## Docker and Compose

Two images are published, one per entry point. Both are built from the same `Dockerfile` and run as a non-root user
(`atrium`, uid 10001) by default:

| Image                                          | Stage  | Entry point             | Purpose      |
|------------------------------------------------|--------|-------------------------|--------------|
| `ghcr.io/ufal/atrium-translator:<version>`     | `base` | `python main.py`        | batch CLI    |
| `ghcr.io/ufal/atrium-translator-api:<version>` | `api`  | `python -m service.api` | HTTP service |

`<version>` is the release without its leading `v` (`1.7.1-beta` for release `v1.7.1-beta`), or `latest`. The
`-api` suffix belongs to the image **name**, not the tag: `ghcr.io/ufal/atrium-translator-api:1.7.1-beta`. A tag
such as `atrium-translator:1.6.1-beta-api` is never published. The images carry `data_samples/my_documents/` and
the vocabulary, not the sample outputs, and neither includes the `ct2` dependencies.

### Batch translation

```bash
# ./data/input holds the ALTO XML; results land in ./data/output
mkdir -p data/input data/output

docker run --rm --user "$(id -u):0" -v "$PWD/data:/data" \
  ghcr.io/ufal/atrium-translator:latest \
  /data/input --alto --formats alto.xml --target_lang en -o /data/output

# AMČR metadata records (./data/input holds the *.xml records), the production shape of issue #46:
# replace mode, validated against AMČR 2.2. The service validates against its deployment's XSD_PATH instead.
docker run --rm --user "$(id -u):0" -v "$PWD/data:/data" \
  ghcr.io/ufal/atrium-translator:latest \
  /data/input --xpaths amcr-fields.txt --formats xml --source_lang cs --target_lang en \
  --output-mode replace --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd -o /data/output
```

**Run as yourself, so the container can write `./data`.** The image runs as uid 10001 by default, and `./data`
belongs to you. `--user "$(id -u):0"` runs the container as your uid with group 0; every path the image writes
(`/data`, `/cache`, `$HOME`) is group-0 writable for exactly this. Docker Desktop (macOS, Windows) needs neither.
Under Compose, `./data` is in the clone (`data/.gitkeep`) and the services run as `user: "${ATRIUM_UID:-10001}:0"`:
on Linux, put your uid in `.env` once, `echo "ATRIUM_UID=$(id -u)" >> .env` (atrium-project#69, roadmap B6; this
replaces the old `sudo chown -R 10001 data` step).

The batch entry point exits **non-zero** on failure (`1` usage, `2` nothing matched the formats, `3` one or more
documents failed), so it can be wrapped in a cron job or a Kubernetes `Job` and monitored
([exit codes](reference/cli.md#exit-codes)).

### Compose

`docker-compose.yml` defines two services, `translator` (batch) and `api` (profile `api`); both read the
repository's `.env` ([Environment variables](configuration.md#environment-variables)). Volumes: `./data:/data`,
`hf-cache:/cache/huggingface` (the FastText model) and `./config.txt:/app/config.txt:ro`.

```bash
docker compose run --rm translator          # batch, ./data:/data
docker compose --profile api up -d          # the service, http://localhost:8000
curl -sf localhost:8000/health
curl -sf localhost:8000/ready
```

`PORT` moves the listener, the published port and the container's own `HEALTHCHECK` together:

```bash
PORT=9000 docker compose --profile api up -d
```

A clean stop of the service exits 143 (128 + SIGTERM), which Compose and Kubernetes report as non-zero; that is
the expected value.

## The HTTP service

The same pipeline behind `POST /translate`. Its quick start, request fields, responses, errors and limits are in
[service/README.md](../service/README.md); the contract is [service/openapi.json](../service/openapi.json).

### The command line and the service

Both entry points run one code path: the mode decision (`utils.xml_kind`), the file naming
(`main.translated_filename`), `main.process_single_file` for the translation, the log and the record, and the
paradata's keys (`main.build_run_config` for the run's parameters, `main.run_outcome_config` for what it counted:
protected vocabulary terms, LINDAT's degenerate replies, the XSD verdicts). A scratch harness ran 256 combinations
through both on 2026-10-28 (ALTO named `.alto.xml` and `.xml`, an AMČR record; the mode detected and named; `cs`
and `auto`; `replace` and `append`; `--fast-align`; a vocabulary; a seed record; no XSD, a passing one, a failing
one, a strict one), with one stub backend and language detector on both sides. The XML, the log, the record and the
paradata's keys and values were equal in every case but the strict-XSD one in the table.
[tests/test_cli_service_parity.py](../tests/test_cli_service_parity.py) keeps the main cases.

| Input or setting     | Command line                                                     | Service                                                                                          | Same?                                                                                        |
|----------------------|------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| How much per run     | a file, a folder, a `.txt` list of URLs                          | one XML document per request                                                                     | differs: the service is stateless and fetches no URLs                                        |
| ALTO or metadata     | the root element, unless `--alto`, `--xpaths` or `--formats`     | the root element, unless `is_alto`                                                               | the same                                                                                     |
| Source language      | `--source_lang`, config.txt `source_lang`, else `cs`             | `source_lang`, else `auto`                                                                       | the same when given; the defaults differ                                                     |
| Fallback language    | `--default-source-lang`, config.txt, `DEFAULT_SOURCE_LANG`, `cs` | `DEFAULT_SOURCE_LANG`, `cs`                                                                      | the same rule, set per deployment                                                            |
| Output mode          | `--output-mode`, config.txt, `OUTPUT_MODE`, `replace`            | `output_mode`, `OUTPUT_MODE`, `replace`                                                          | the same; an unknown value: `--output-mode` refuses it, the service warns and uses `replace` |
| `--fast-align`       | a flag                                                           | `fast_align=true`                                                                                | the same                                                                                     |
| XPath targets        | `--xpaths`, or config.txt `fields`                               | `AMCR_FIELDS_PATH`; without it a metadata request is refused (422)                               | differs: set per deployment                                                                  |
| Backend, vocabulary  | `--backend`, `--vocabulary` or config.txt                        | `TRANSLATION_BACKEND`, `VOCABULARY_PATH`                                                         | differs: set per deployment                                                                  |
| XSD validation       | `--xsd` (a URL or a path), `--xsd-strict`                        | `XSD_PATH` (a local file, loaded at startup), `xsd_strict`; the verdict in the answer            | the same verdict; the service never fetches a schema                                         |
| A strict XSD failure | the XML is written, the document counts as failed (exit 3)       | 422 `xsd_validation_failed`, no XML                                                              | differs: an answer is all or nothing                                                         |
| The record           | always written (`<doc_id>.document.json`)                        | returned when `document_json` is sent, or with `include_record=true`                             | the same record                                                                              |
| The log              | `<doc>_log.csv` beside the XML                                   | with `include_log=true`: `log_csv`, or a part of the multipart answer                            | the same rows                                                                                |
| An invalid baseline  | a warning on stderr; the record is still written                 | passed through; `document_json_schema_error` in the JSON answer (the multipart one has none)     | the same record                                                                              |
| Paradata             | a file in `<output>/paradata/`, program `translator`             | the `CreateAction` of the JSON and multipart answers, program `translator-api`; none in bare XML | the same keys and values; a path on the command line is a file name in the service           |

```bash
curl -s -X POST localhost:8000/translate \
     -F "file=@data_samples/my_documents/C-N1000019.xml" -F "is_alto=false" \
     -F "source_lang=cs" -F "output_mode=replace" -o C-N1000019_en.xml
```

`output_mode=append` works the same way, but its AMČR output does not validate against AMČR 2.2 (above).
`is_alto`, `source_lang`, `target_lang`, `output_mode`, `response_format`, `fast_align`, `include_record`,
`include_log` and `xsd_strict` are accepted **either** as multipart form fields or as query-string parameters. A
boolean that cannot be read is refused (422) from either place; before v1.7.0-beta a query value fell back to its
default, so `?is_alto=ture` translated a record as ALTO.

The service is stateless: per-request scratch lives in a temporary directory that ends with the request, so
replicas scale horizontally. It has no authentication or rate limiting: `/translate` does unbounded outbound work
against a translation backend for any caller, so deploy it behind your own gateway or on a trusted network, not
directly on the public internet.

## As one stage of the pipeline

Each pipeline stage runs as its own container, and the document record passed between them with
`--document-json` / `--document-json-out` is the hand-off. This is the translator stage of the hub's end-to-end
smoke workflow:

```bash
docker run --rm \
  -v "$PWD":/workspace \
  -w /workspace/work/atrium-translator-main \
  ghcr.io/ufal/atrium-translator:latest \
  /workspace/work/PAGE_ALTO/CTX000000003/CTX000000003-1.alto.xml \
  --alto --formats alto.xml --source_lang cs --target_lang en \
  -o /workspace/work/translated \
  --document-json /workspace/work/doc_json/2_alto.json \
  --document-json-out /workspace/work/doc_json/3_translate.json
```

The baseline record comes in, only the `translations` block (and `derived_from.translated_xml`) is written, and
everything else is carried through. The record's `doc_id` is **inherited from the baseline**, never re-derived from
the page's file name, because the input is a page (`…-1.alto.xml`), not the document. The stages before and after,
and how the AMČR deployment chains them: the
[hub's workflow page](https://ufal.github.io/atrium-project/workflows/translator/).

## From a coding agent

The `agent-skill` branch packages the HTTP service as an Agent Skill, so a coding agent can translate a file by
calling a running service. Installation and the contract it relies on:
[Agent skills](https://ufal.github.io/atrium-project/agent-skills/) on the hub.

## Troubleshooting

| Symptom                                              | Cause                                                                                                                                                                                                                                               | Fix                                                                                                     |
|------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| `.env` values have no effect under `python main.py`  | `.env` is read by Compose (`env_file`) for the container; `main.py` reads only the process environment                                                                                                                                              | export the variables (`set -a; . ./.env; set +a`), or run through Compose                               |
| `EACCES` writing to `./data` under Docker            | the host directory is not writable by uid 10001, the image's default user                                                                                                                                                                           | run with `--user "$(id -u):0"`; under Compose set `ATRIUM_UID` in `.env`                                |
| A metadata run finds nothing and exits 2             | a folder of records scanned with config.txt's `formats = alto.xml`, which picks up only `*.alto.xml`                                                                                                                                                | pass `--xpaths`, or `--formats xml` (`xml,txt` for a URL list)                                          |
| The run exits 1 before translating                   | a refused language pair, a missing vocabulary file, an XSD that cannot be loaded, or a backend that cannot start; the `[ERROR]` line names which                                                                                                    | fix what the line names                                                                                 |
| Container reports healthy, nothing can reach it      | `HOST=127.0.0.1` binds inside the container only                                                                                                                                                                                                    | leave `HOST` unset (it defaults to `0.0.0.0`)                                                           |
| Every block is translated as the default language    | the FastText model could not be downloaded or loaded, so nothing is detected and the fallbacks decide                                                                                                                                               | check `GET /health?deep=true`, which reports it; or pass `--source_lang`, which skips detection         |
| `/translate` answers 422 for a metadata file         | no readable `AMCR_FIELDS_PATH`, so there are no fields to translate                                                                                                                                                                                 | point `AMCR_FIELDS_PATH` at `amcr-fields.txt` or your own XPath list                                    |
| A translation run is slow, with no error             | page batches fell back to one request per item, after a line-count mismatch, an implausible reply or a transport error                                                                                                                              | read the per-document `WARNING` summary, which counts each cause                                        |
| `LINDAT: N degenerate reply(ies) re-requested`       | the translation service answered some requests with a looping or empty reply; each was asked again                                                                                                                                                  | nothing, if the log has no `untranslated` rows: the guard recovered them, at the cost of time           |
| Rows marked `untranslated` in `_log.csv`             | a segment stayed degenerate after the end-of-document re-run; its source text was kept                                                                                                                                                              | re-run that document later; raise `LINDAT_GUARD_RETRIES` / `TRANSLATION_RERUN_ROUNDS` if it recurs      |
| Rows marked `unsupported_lang` in `_log.csv`         | with `--source_lang auto`, FastText was confident the segment is a language the backend cannot translate (Latin, Hungarian on LINDAT); it was translated from the fallback language, as the record's `translations.unsupported_source_langs` counts | review those rows; translate them with a backend that has the language (`ct2`), or pass `--source_lang` |
| `XSD validation failed` on an `append` AMČR record   | AMČR 2.2 accepts neither `xml:lang` on the translated field nor its repeated sibling                                                                                                                                                                | expected for `append`; use `replace` where the record must validate                                     |
| The TEATER harvest yields nothing, with an SSL error | `teater.aiscr.cz` serves an incomplete certificate chain                                                                                                                                                                                            | point `REQUESTS_CA_BUNDLE` at a bundle with the missing intermediate ([above](#build-the-vocabulary))   |
| Container exits 143                                  | a clean SIGTERM drain                                                                                                                                                                                                                               | expected; not a failure                                                                                 |
