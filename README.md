<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11-blue.svg" title="Python Version"></a>
  <a href="https://lindat.mff.cuni.cz/services/translation/"><img src="https://img.shields.io/badge/API-LINDAT%20Translation-0055A4.svg" title="LINDAT Translation API"></a>
  <a href="https://lindat.mff.cuni.cz/services/udpipe/"><img src="https://img.shields.io/badge/API-UDPipe2-0055A4.svg" title="UDPipe2"></a>
  <a href="https://huggingface.co/facebook/fasttext-language-identification"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20HF-fasttext--langID-yellow.svg" title="FastText Language Identification"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-translator" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---


# 🏛️ ATRIUM - LINDAT Translation Wrapper 🌍

A modular Python wrapper originally designed for the **LINDAT Translation API** [^1], now featuring a pluggable
architecture supporting local LLMs and CTranslate2 self-hosted models. Following project scope requirements,
this tool is strictly focused on processing **XML and its direct derivatives**.  It supports two input modes:

| Mode             | Input                                                      | Key flag   |
|------------------|------------------------------------------------------------|------------|
| **ALTO XML**     | Scanned-document ALTO XML                                  | `--alto`   |
| **XML Metadata** | Any structured XML (AMCR [^7], OAI-PMH, or custom schemas) | `--xpaths` |

The wrapper identifies the source language using **FastText** [^5], translates the
content to English (or any other target language supported by the LINDAT API),
optionally overrides domain-specific terms using a **Tag-and-Protect vocabulary**
strategy backed by **UDPipe lemmatisation** [^6], and safely reconstructs the original
XML structure without altering tags, namespaces, or OAI-PMH envelopes.

For ALTO documents the reconstruction is non-trivial: the spatial `String`
coordinates must be preserved while their `CONTENT` is replaced with fluent
translated text whose word count rarely matches the source. The wrapper solves
this with a **dual-pass block/line translation** followed by a **similarity-based
token-alignment** step (see [🧠 Logic Overview](#-logic-overview)).

## 📚 Table of Contents

- [Project Structure & Architecture](#project-structure--architecture)
- [✨ Features](#-features)
- [🛠️ Prerequisites](#-prerequisites)
- [🐳 Docker & Compose](#-docker--compose)
- [📂 Project Structure](#-project-structure)
- [💻 Usage](#-usage)
  - [📖 ALTO XML Mode](#-alto-xml-mode)
  - [📄 XML Metadata Mode](#-xml-metadata-mode)
  - [📘 Vocabulary / Tag-and-Protect](#-vocabulary--tag-and-protect)
  - [🗂️ Harvesting the Vocabulary](#-harvesting-the-vocabulary)
  - [⚙️ Configuration File Support](#-configuration-file-support)
  - [⚙️ Supported Arguments](#-supported-arguments)
- [🌐 API Service](#-api-service)
- [⚙️ Environment Variables](#-environment-variables)
- [☸️ Deployment](#-deployment)
- [🧠 Logic Overview](#-logic-overview)
  - [🧩 ALTO Dual-Pass Reconstruction](#-alto-dual-pass-reconstruction)
- [📊 Translation CSV Logs](#-translation-csv-logs)
- [🗄️ Paradata JSON Logs](#-paradata-json-logs)
- [📄 License & Citation](#-license--citation)
- [🙏 Acknowledgements](#-acknowledgements)

---

## Project Structure & Architecture ![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)

The `atrium-translator` operates as both a batch CLI tool and an exposed REST API service, bringing it into
architectural parity with the ATRIUM Layout and Enrichment pipelines.

* **Batch CLI (`main.py`)**: Designed for massive document directories. Generates per-file translation and execution CSV logs.
* **API Service (`service/api.py`)**: A FastAPI wrapper exposing a `/translate` endpoint. It leverages the exact same
core translation functions without duplicating application logic or model registries. Features full DoS guards
and file-size constraints.


## ✨ Features

* 🎯 **Dedicated XML Processing**: Narrowly defined and optimised exclusively for ALTO XML and structured metadata
records, ensuring safe, universal usage without tag or namespace corruption.
* 📖 **ALTO Translation Mode (Dual-Pass)**: Translates only the `CONTENT` attributes natively. Tied to a simple flag (`--alto`).
Each `TextBlock` is translated **twice** — once as a whole block (for semantic quality) and once line-by-line (as structural
anchors) — and the block translation is then realigned to the physical line/`String` layout (see [🧩 ALTO Dual-Pass Reconstruction](#-alto-dual-pass-reconstruction)).
* 📄 **XML Metadata Mode**: Translates specific elements based on a user-provided list of XPaths (e.g.,
[amcr-fields.txt](amcr-fields.txt) 📎), safely reconstructs the document tree, and handles deep recursive
namespace extraction for OAI-PMH envelopes.  Works with **any conformant XML**, not only AMCR [^7] records.
* ✅ **XSD Validation**: Optionally validates metadata outputs against an XSD schema (e.g.,
`https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd`) to guarantee structural integrity after translation.
* 📊 **Per-document Translation CSV Logs**: Automatically produces a supplementary QA CSV file with columns
`file, page_num, line_num, text_<source_lang>, text_<target_lang>, status` for easy manual review — `status` flags
lines that were re-run, placed by word count, or left untranslated.
* 🗄️ **Run-level Paradata JSON Logs**: Each pipeline run appends a structured provenance record (timing, counts,
configuration snapshot) to the [paradata](data_samples/in-place_translated_files/alto/paradata) 📁 directory for auditing and performance reporting.
* 🕵️ **Language Detection with Intelligent Fallback**: With `--source_lang auto` the source language is identified
with **FastText** (Facebook) [^5] — once per ALTO `TextBlock` / metadata field, plus once for the whole document — and a
guess is used only if it is **trustworthy**: enough letters, enough confidence, and a language the translation backend
can actually translate. Otherwise the block's own label (ALTO `LANG`, `xml:lang`) is used, then the document's language,
then the **default source language** (`cs`). See [Source-language identification](#source-language-identification).
* ✂️ **Sentence-Aware Chunking**: Long texts are split at the highest-priority boundary found in each window, tried in
strict order — newline (`\n`) → sentence-terminal punctuation (`. `, `! `, `? `) → clause-level punctuation (`; `, `, `) →
word boundary — before being sent to the translation API. Keeping whole sentences together preserves NMT context and
improves quality; the word boundary is a fallback and a hard cut is the last resort for oversized single tokens.
* 🔤 **Tag-and-Protect Vocabulary Overriding**: When a vocabulary CSV is supplied, domain-specific terms are protected
before translation using NMT-safe placeholder sentinels. Single-word terms are matched by lemma via the **LINDAT UDPipe API** [^6];
multi-word phrases are matched case-insensitively as whole words, longest phrase first, and every occurrence is protected.
Vocabulary translations are restored after the NMT call, ensuring controlled terminology is never garbled.
* 🗂️ **Automated Vocabulary Harvesting**: The bundled [load_vocab.py](load_vocab.py)📎 script downloads Czech→English term pairs from
both the **AMCR OAI-PMH API** [^7] and the **TEATER GraphQL API** [^8] and merges them into a single ready-to-use CSV.
* 🔗 **LINDAT API Integration**: Seamlessly connects to the LINDAT Translation API (v2) [^1].
* 🔌 **Pluggable Translation Backends**: Switch seamlessly between the LINDAT Translation API, OpenAI-compatible LLM
endpoints, and low-resource self-hosted CTranslate2 models (e.g., EuroLLM, MADLAD-400) using the `--backend` flag.


### Performance: Page-Level Batching
To minimize network latency and reduce overhead on translation backends (such as the LINDAT API or local LLMs),
`atrium-translator` implements dynamic **Page-Level Batching**.

Instead of translating each ALTO XML `TextBlock` and `TextLine` sequentially, the pipeline gathers all text elements on
a single page, groups them by their detected language, and consolidates them into unified payloads separated by newline (`\n`) delimiters.

* **API Efficiency:** This architecture reduces API calls from $1 + N$ (where $N$ is the number of text lines in a block)
down to as few as 2 requests per language group per page.
* **Zero-Regression Fallback:** A batched reply is accepted only if it keeps the request's line count **and** every
line is a plausible translation of its own item (see *Degenerate-output guard* below). If an NMT model merges or drops
line boundaries, or answers any item with a repetition loop, an empty line or a runaway/truncated one, the line
mapping of that reply is not trusted and the whole batch falls back to a 1-by-1 safe loop.
* **Anchors only where they matter:** the line-anchor pass is sent only for translated blocks that have at least two
text lines — a single-line block takes its block translation as it is, so its anchor was never used.

### Degenerate-output guard, flagging and re-run

A backend can answer HTTP 200 with something that is not a translation. On the 2026-09-26 sample refresh, roughly a
third of all LINDAT replies came back as one Czech word repeated up to ~150 times (`"pravidla pravidla …"`) — for
one-word headings and for whole sentences, on the ALTO and on the metadata path, and **nondeterministically** (the
same field was garbage in one run and correct in the next). Nothing looked at reply content, so the garbage went into
the XML and the QA log, and — through the ALTO line anchors, which are never logged — into the word-to-box alignment
of every block on the page (2489 blanked `String`s instead of 224).

Every translated segment is now checked by `processors/quality.py::degeneration_reason` against its own source
(empty output, runaway length, truncation, repetition loops — each rule compares with the source, so dot leaders,
number tables and names kept verbatim pass; calibrated at 0 false flags on all 1193 June blocks and 37 metadata
fields, 100 % of the looping ones caught):

1. **Backend re-request.** `LindatTranslator` re-requests a degenerate reply up to `LINDAT_GUARD_RETRIES` times
   (default 2) — the first time **immediately**, then with back-off; the LLM and CT2 backends reject it in their
   output guards.
2. **Flag.** A block, line anchor or metadata field that is still unusable is **flagged** and left untouched for now.
3. **Re-run during the same document.** After the whole document has been processed, the flagged segments are
   re-requested one by one after a cool-down (`TRANSLATION_RERUN_ROUNDS`, default 1; `TRANSLATION_RERUN_DELAY_S`,
   default 10 s).
4. **Keep the source.** A segment that never recovers keeps its source text (ALTO `String`s keep their `CONTENT` and
   geometry; a metadata field is not overwritten and gets no appended sibling) and is logged as `untranslated` in the
   `status` column of the `_log.csv`. The file is still written; one bad reply costs one segment, never the document.

A per-document summary line (WARNING when anything was flagged or fell back) reports batches accepted, fallbacks by
cause, segments flagged / recovered / left untranslated, and lines placed by word count. `main.py` adds one line per
document with the number of LINDAT replies that were rejected and re-requested (`[WARN] LINDAT: N degenerate
reply(ies) re-requested …`, also recorded in paradata as `lindat_degenerate_replies`); the individual rejections are
logged at INFO unless the retry fails too.

**What was found behind the failures.** Sequential runs made the pattern visible: the first request of every batch came
back degenerate, its byte-identical re-request succeeded, and two runs agreed reply for reply (the same inputs failed
with the same token counts). Garbage that is deterministic for a given input, yet cured by repeating the identical
request, is the signature of **one broken replica behind a round-robin load balancer** — every other request lands on
it. That is also why the concurrent sample runs of 2026-09-26 looked randomly ~30 % broken. The output is correct
either way (the guard recovers each reply, at the cost of a second request), but the fix belongs to the service. To
check the live endpoint and get a report you can hand to its operators:

```bash
python -m eval.lindat_probe            # e.g. "fresh: ✗✓✗✓✗✓… → alternating — one of two replicas broken"
```


---

## 🛠️ Prerequisites

1. Clone the project files:
```bash
git clone https://github.com/ufal/atrium-translator.git
```
2. Create a virtual environment and activate it (optional but recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```
3. Install the required Python packages:
```bash
cd atrium-translator
pip install -r requirements.txt
```

> **Note on `fasttext`:** The upstream package requires a C++ compiler at build time.
> If your environment lacks build tools, install the pre-built wheel instead:
> ```bash
> pip install fasttext-wheel
> ```

---

## 🐳 Docker & Compose

Published images, one per entry point. Both are built from the same `Dockerfile`
and run as a non-root user (`atrium`, uid 10001):

| Image                                          | Stage  | Entry point             | Purpose      |
|------------------------------------------------|--------|-------------------------|--------------|
| `ghcr.io/ufal/atrium-translator:<version>`     | `base` | `python main.py`        | batch CLI    |
| `ghcr.io/ufal/atrium-translator:<version>-api` | `api`  | `python -m service.api` | HTTP service |

### Batch translation

```bash
# ./data/input holds the ALTO XML; results land in ./data/output
mkdir -p data/input data/output && sudo chown -R 10001 data

docker run --rm -v "$PWD/data:/data" \
  ghcr.io/ufal/atrium-translator:latest \
  /data/input --alto --formats alto.xml --target_lang en -o /data/output
```

> ⚠️ **Create `./data` yourself first.** The container runs as uid 10001, and a
> bind-mount directory that Docker creates is owned by root — the write to
> `/data/output` then fails with `EACCES`. The `chown` above is the whole fix.

The batch entry point exits **non-zero** on failure (`1` usage · `2` nothing
matched the formats · `3` one or more documents failed), so it can be wrapped in
a cron job or a Kubernetes `Job` and actually be monitored.

### API service

```bash
docker compose --profile api up -d      # http://localhost:8000
curl -sf localhost:8000/health
curl -sf localhost:8000/ready
```

`docker-compose.yml` defines two services — `translator` (batch) and `api` — and
both read the repo-root `.env` (see [Environment Variables](#-environment-variables)).
`PORT` moves the listener, the published port and the container's own
`HEALTHCHECK` together:

```bash
PORT=9000 docker compose --profile api up -d
```

---

## 📂 Project Structure

```text
atrium-translator/
├── main.py                    # 🚀 Batch CLI entry point – ALTO vs. XML Metadata routing
├── utils.py                   # 🔧 ALTO & XML parsing, dual-pass alignment, CSV logs, XSD validation
├── load_vocab.py              # 🗂️ Vocabulary harvester (AMCR OAI-PMH + TEATER GraphQL → CSV)
├── config.txt                 # ⚙️ Per-run configuration (paths, languages, formats, backend)
├── para_config.txt            # 🏷️ Tool version (single source of truth) + component→license table
├── amcr-fields.txt            # 📄 Example XPath list for AMCR metadata translation
├── amcr-inputs.txt            # 📄 List of AMCR metadata input URLs to be processed
├── .env.example               # 🔑 The environment contract — copy to .env
├── Dockerfile                 # 🐳 Two stages: `base` (batch CLI) and `api` (HTTP service)
├── docker-compose.yml         # 🐳 Local deployment: `translator` + `api` (profile)
│
│   # ── Shared canonical files (vendored from ufal/atrium-project, never edited here) ──
├── atrium_paradata.py         # 🗄️ Run-level provenance/paradata logger
├── atrium_document.py         # 📑 Cross-tool ATRIUM Document record (+ .schema.json)
├── atrium_vocab.py            # 🏷️ SKOS controlled-label registry (+ .schema.json)
├── atrium_rocrate.py          # 📦 RO-Crate (JSON-LD) export of document records
├── para_licenses.py           # ⚖️ Effective-license resolution from exercised components
├── check_version.py           # 🚦 Release gate: tag == CITATION.cff == para_config.txt
│
├── processors/                # (namespace package — no __init__.py)
│   ├── backend.py             # 🔌 TranslationBackend protocol + get_backend() registry
│   ├── translator.py          # 🔄 LINDAT CUBBITT client + Tag-and-Protect vocabulary
│   ├── llm_translator.py      # 🤖 OpenAI-compatible LLM backend (prompt glossary, guards)
│   ├── ct2_translator.py      # 🧪 CTranslate2 self-hosted backend (`--backend ct2`)
│   ├── lemmatizer.py          # 🔤 UDPipe-based lemmatizer for vocabulary term matching
│   ├── identifier.py          # 🌍 FastText language identification (ISO 639-3 → 639-1)
│   ├── language.py            # 🧭 Source-language policy: detected → label → document → default
│   ├── chunking.py            # ✂️ Shared sentence-aware text chunker (priority-ordered)
│   ├── http_retry.py          # 🔁 Shared throttle + bounded exponential back-off
│   ├── quality.py             # 🛡️ Degenerate-output detector (loops, empty, runaway, truncation)
│   └── vocab.py               # 📘 Vocabulary CSV loader
├── service/                   # 🌐 The HTTP surface — see service/README.md
│   ├── api.py                 # FastAPI app: /translate, /info, /health, /ready
│   ├── atrium_service.py      # (canonical) shared meta-contract helpers
│   ├── healthcheck.py         # (canonical) stdlib-only Docker HEALTHCHECK probe
│   └── requirements.txt       # Service-only dependencies (fastapi, uvicorn)
├── tests/                     # 🧪 pytest suite; tests/integration/ is the live-backend lane
├── eval/                      # 📊 bakeoff.py (backend comparison, #4) · langid_report.py (language-ID tuning)
│                              #    · lindat_probe.py (is one LINDAT replica broken?)
├── docs/                      # 📚 translation-backends.md — backend evaluation & design
├── agent_dev_logs/            # 📓 Derived timeline, per-issue digests and plans
└── data_samples/
    ├── vocabulary.csv         # 📘 Czech→English domain vocabulary (AMCR/TEATER terms)
    ├── my_documents/          # 📂 Sample inputs (ALTO XML, downloaded AMCR metadata)
    ├── in-place_translated_files/   # 📂 replace-mode outputs: alto/ and xml/, each with CSV logs,
    │                                #    document records and paradata/
    └── appended_translated_files/   # 📂 append-mode outputs, same layout (see data_samples/README.md)
```

---

## 💻 Usage

Run the wrapper from the command line. The default target language is English (`en`).

### 📖 ALTO XML Mode

Use the `--alto` flag together with `--formats alto.xml` (or set `formats = alto.xml` in
[config.txt](config.txt)📎). This processes ALTO files by strictly targeting their `String` `CONTENT` attributes.

```bash
python main.py ./data_samples/my_documents --alto --formats alto.xml --target_lang en
```

> **Tip:** Specifying `alto.xml` in `formats` (CLI or config) now **auto-enables** ALTO
> mode even without the explicit `--alto` flag.

Example of ALTO XML processing:
- **Input**: [MTX201501307_anon.alto.xml](data_samples/my_documents/MTX201501307_anon.alto.xml) 📎
- **Output** (replace): [MTX201501307_anon_en.alto.xml](data_samples/in-place_translated_files/alto/MTX201501307_anon_en.alto.xml) 📎
- **Output** (append): [MTX201501307_anon_en.alto.xml](data_samples/appended_translated_files/alto/MTX201501307_anon_en.alto.xml) 📎

Translation is driven at the `TextBlock` level for semantic quality, but the resulting
words are **realigned and redistributed back into the individual `CONTENT` attributes**
of each `String` within each `TextLine`, so the original spatial layout is preserved.
See [🧩 ALTO Dual-Pass Reconstruction](#-alto-dual-pass-reconstruction) for the full algorithm.

---

### 📄 XML Metadata Mode

This mode translates specific text fields inside **any well-formed XML document**. You supply
a plain-text file listing XPaths — one per line — that identify the elements whose `.text`
content should be translated. The mode was originally designed for AMCR/OAI-PMH [^7] records
but is not tied to that schema; it works with any XML and any namespace.

#### AMCR example

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd \
    --target_lang en
```

[amcr-fields.txt](amcr-fields.txt)📎 contains XPaths such as:

```
//amcr:amcr/amcr:dokument/amcr:popis
//amcr:amcr/amcr:dokument/amcr:poznamka
//amcr:amcr/amcr:archeologicky_zaznam/amcr:lokalita/amcr:chranene_udaje/amcr:popis
```

The namespace prefix (`amcr:`) is resolved automatically from the document.

#### Custom XML example

For any other XML schema, create your own XPath list and pass it with `--xpaths`:

```bash
# my-fields.txt
//tei:TEI/tei:teiHeader/tei:fileDesc/tei:titleStmt/tei:title
//tei:TEI/tei:text/tei:body//tei:p[@type='abstract']
```

```bash
python main.py ./my_xml_files --xpaths my-fields.txt --target_lang en
```

Namespace prefixes that appear in the document are extracted automatically; you only
need to use the same prefix in your XPath expressions as appears in the XML.

**Output** files are saved in the configured output directory and include:
- A translated `.xml` file with all targeted fields replaced
- A companion `_log.csv` translation log (see [Translation CSV Logs](#-translation-csv-logs))

---

### 📘 Vocabulary / Tag-and-Protect

Provide a two-column CSV (`source_lemma,target_translation`) to activate the
**Tag-and-Protect** strategy.  When enabled, domain-specific terms are shielded
from the NMT model and replaced with guaranteed vocabulary translations instead.

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt \
    --vocabulary data_samples/vocabulary.csv --target_lang en
```

Or set the path in [config.txt](config.txt)📎:

```ini
vocabulary = data_samples/vocabulary.csv
```

#### How it works

1. **Multi-word phrase pass** – phrases containing spaces (e.g. `fotografie události`)
   are matched case-insensitively as whole words (never inside a longer word), longest
   phrase first. **Every** occurrence is replaced with its own NMT-safe placeholder
   sentinel and counted in the protected-term statistics. Phrases match on the surface
   form stored in the CSV, not by lemma.
2. **Single-word lemma pass** – the remaining text is lemmatised via the LINDAT UDPipe
   API [^6].  Tokens whose base form appears in the vocabulary are similarly tagged.
   Only source languages with a UDPipe model (table below) are lemmatised; for any
   other language this pass is skipped, with one warning per language.
   A **number-agreement guard** protects only singular / number-neutral occurrences;
   plural source tokens are left for the NMT to inflect, preventing broken English
   agreement (e.g. "several feature").
3. **Translation** – the tagged text is sent to the LINDAT Translation API.  NMT models
   leave the alphabetic sentinels untouched.
4. **Restoration** – every sentinel in the translated output is replaced with the
   corresponding vocabulary translation. Restoration is tolerant of stray spaces the
   NMT may inject, and any unrecoverable sentinel debris is scrubbed before output.

If no vocabulary file is provided, the translator behaves exactly as before (no UDPipe
calls are made, no lemmatization is performed - just the basic translation preserving input file structure).

> **Note on placeholders:** Earlier versions wrapped terms in `__TERM_N__`. Because NMT
> models frequently mangled the underscores/digits, the protected sentinel is now a
> purely alphabetic marker of the form `Xtermzzz<N>z`, which NMT models pass through intact.

#### Vocabulary CSV format

The vocabulary file must be a UTF-8 encoded CSV whose first two columns are the term pair:

```
source_lemma,target_translation
kostel,church
pohřebiště,burial ground
fotografie události,photograph of event
```

Columns 3–5 are **optional provenance** and are ignored by the translator. The harvested
[vocabulary.csv](data_samples/vocabulary.csv)📎 carries them so every term can be traced
to its thesaurus concept:

```
source_lemma,target_translation,source,source_id,uri
archeolog,archaeologist,teater,4,https://teater.aiscr.cz/id/4
```


| Column               | Content                                                                                                                                                                                                              |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `source_lemma`       | The **lemmatised (dictionary) form** of the source term. For single-word terms this must match what UDPipe returns for the source language (see table below).  For multi-word phrases, any surface form is accepted. |
| `target_translation` | The canonical translation — typically the preferred English term from a controlled vocabulary or thesaurus.                                                                                                          |
| `source` *(opt.)*    | Which thesaurus the pair came from: `amcr` or `teater`.                                                                                                                                                              |
| `source_id` *(opt.)* | The concept's id in that thesaurus: the AMCR `heslo` id (`HES-…`) or the TEATER concept id.                                                                                                                          |
| `uri` *(opt.)*       | The dereferenceable concept URI built from `source_id` (`https://api.aiscr.cz/id/…` or `https://teater.aiscr.cz/id/…`).                                                                                              |


> [!IMPORTANT]
> Single-word vocabulary entries should be provided as lemmas (singular/infinitive);
> matching is lemma-based and number-neutral.


**How to determine the correct lemma form by language**

The `source_lemma` column must match the form that UDPipe assigns as the base form
for the given language.  A quick way to check is to run any word through the
[online UDPipe demo](https://lindat.mff.cuni.cz/services/udpipe/) and read the
`LEMMA` column of the CoNLL-U output.

| Source Language (`--source_lang`) | UDPipe model used           | Lemma convention                                    | Example                                         |
|-----------------------------------|-----------------------------|-----------------------------------------------------|-------------------------------------------------|
| Czech `cs`                        | `czech-pdt-ud-2.15`         | Nominative singular for nouns; infinitive for verbs | `kostel` (not `kostela`), `kopat` (not `kopal`) |
| Slovak `sk`                       | `slovak-snk-ud-2.15`        | Nominative singular; infinitive                     | `kostol`, `kopať`                               |
| Polish `pl`                       | `polish-pdb-ud-2.15`        | Nominative singular; infinitive                     | `kościół`, `kopać`                              |
| German `de`                       | `german-gsd-ud-2.15`        | Nominative singular; infinitive                     | `Kirche`, `graben`                              |
| French `fr`                       | `french-gsd-ud-2.15`        | Nominative singular; infinitive                     | `église`, `fouiller`                            |
| Russian `ru`                      | `russian-syntagrus-ud-2.15` | Nominative singular; infinitive                     | `церковь`, `копать`                             |
| Ukrainian `uk`                    | `ukrainian-iu-ud-2.15`      | Nominative singular; infinitive                     | `церква`, `копати`                              |
| English `en`                      | `english-ewt-ud-2.15`       | Base form                                           | `church`, `dig`                                 |

Any other `--source_lang` has **no UDPipe model**: the single-word lemma pass is skipped for it
(one `[WARN]` per language) instead of running the text through the Czech model, whose lemmas
would be noise that can still collide with a Czech vocabulary key. Multi-word phrases are still
protected.

> **Tip for non-Czech archives:** If your source XML is in a language other than Czech 🇨🇿,
> pass the corresponding `--source_lang` code and supply a matching vocabulary CSV whose
> `source_lemma` column uses that language's lemma conventions. The vocabulary harvesting
> script ([load_vocab.py](load_vocab.py)📎) currently targets Czech (**AMCR**[^7]/**TEATER**[^8]); for other languages
> you will need to compile the vocabulary manually or from your own thesaurus.

---

### 🗂️ Harvesting the Vocabulary

The [load_vocab.py](load_vocab.py)📎 script downloads term pairs automatically from two sources and
merges them into a single CSV:

| Source           | Endpoint                                                                          | Method                                                                                                     |
|------------------|-----------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| **AMCR** [^7]    | `https://api.aiscr.cz/2.2/oai?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo` | OAI-PMH `ListRecords` with resumption token paging; one pair per `heslo` with a Czech and an English label |
| **TEATER**  [^8] | `https://teater.aiscr.cz/api/graphql`                                             | GraphQL `exportAll` → JSON export, one pair per concept with `cs` and `en` names; `search`-based fallback  |

```bash
# Full harvest (both sources):
python load_vocab.py

# Skip one source:
python load_vocab.py --skip-teater
python load_vocab.py --skip-amcr

# Custom output path and request delay:
python load_vocab.py --out my_vocab.csv --delay 0.5
```

The merged vocabulary is written to [vocabulary.csv](data_samples/vocabulary.csv)📎 by default (AMCR [^7] entries
take precedence over TEATER [^8] on key collision). `--delay` paces both sources: the pause between AMCR pages and the
minimum gap between TEATER requests.

> [!NOTE]
> As of 2026-09, `teater.aiscr.cz` serves its TLS certificate without the *RapidSSL TLS RSA CA G1* intermediate, so
> `requests` cannot verify it and the TEATER harvest logs an SSL error and yields nothing. Do not disable
> verification: point `REQUESTS_CA_BUNDLE` at a CA bundle that also contains that intermediate. `api.aiscr.cz`
> serves the same intermediate in its full chain (`openssl s_client -showcerts -connect api.aiscr.cz:443`).

---

### ⚙️ Configuration File Support

Instead of passing all arguments via the command line, you can use a configuration
file [config.txt](config.txt)📎 to define default paths and parameters.  **command-line arguments always take
precedence over config file values** — the config file supplies defaults only for
arguments that are not explicitly passed on the command line.

Example [config.txt](config.txt)📎:

```ini
[DEFAULT]
input_path = ./data_samples/my_documents
source_lang = auto
target_lang = en
formats = alto.xml
fields = amcr-fields.txt
output = ./data_samples/in-place_translated_files/alto

# Optional: path to a vocabulary CSV file (source_lemma,target_translation).
# Leave blank or comment out to disable.
vocabulary = data_samples/vocabulary.csv
```

> **Note:** Setting `formats = alto.xml` (or including `alto.xml` in a comma-separated
> `formats` list) automatically enables ALTO mode, so the `--alto` flag becomes optional.

---

### ⚙️ Supported Arguments

* `input_path`: Path to a single source file, a directory containing XML files, or a `.txt` file listing URLs.
* `--output`, `-o`: Output file path (single-file mode) or output directory (batch mode).
* `--source_lang`, `-src`: Source language code (e.g., `cs`, `fr`). Use `auto` to auto-detect. Default: `cs`.
* `--default-source-lang`: With `--source_lang auto`, the language used when detection cannot be trusted and neither the
element's label nor the document's language settles it. Resolution order: this flag → `default_source_lang` in
`config.txt` → `DEFAULT_SOURCE_LANG` → `cs`.
* `--target_lang`, `-tgt`: Target language code (e.g., `en`, `cs`). Default: `en`.
* `--formats`: Comma-separated list of file extensions to process (e.g., `alto.xml,txt` or `xml,txt`). Default: `xml`.
* `--config`, `-c`: Path to the configuration file (default: `config.txt`).
* `--alto`: Flag to enable ALTO XML in-place translation mode (auto-enabled when `formats` contains `alto.xml`).
* `--xpaths`: Path to a `.txt` file containing XPaths for XML metadata translation (works with any XML schema).
* `--xsd`: Optional URL or local path to an XSD file for output validation.
* `--vocabulary`: Path to a CSV vocabulary file (`source_lemma,target_translation`) to activate Tag-and-Protect term overriding.
* `--backend`: Translation backend — `lindat` (default, LINDAT CUBBITT), `openai_compatible` (any OpenAI-compatible LLM API, configured via the `LLM_*` variables) or `ct2` (a self-hosted CTranslate2 model; install `requirements-ct2.txt` and set the `CT2_*` variables). Resolution order: this flag → `translation_backend` in `config.txt` → `TRANSLATION_BACKEND` → `lindat`. See [docs/translation-backends.md](docs/translation-backends.md) 📎.
* `--fast-align`: ALTO only. Distribute block tokens by source word count instead of translating each line as an anchor — far fewer API calls, slightly coarser line splits.
* `--output-mode`: `replace` (default) or `append` — how the translation is written into the document. See [Output mode](#output-mode-replace-vs-append) below. Resolution order: this flag → `output_mode` in `config.txt` → `OUTPUT_MODE` → `replace`.

### Output mode: replace vs. append

The tool can write a translation into a document two ways. This is
[issue #46](https://github.com/ufal/atrium-translator/issues/46)'s central question, and it is a
switch rather than a decision baked into the code, so a real corpus can settle it.

| Mode                  | What the output contains                                                                                                                                                                                                     |
|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `replace` *(default)* | The source-language field is **overwritten**. The output is a monolingual mirror of the input; the original text survives only in the sidecar `*_log.csv`. This is what the tool has always done — the default is unchanged. |
| `append`              | The source field is **kept**, and a sibling element carrying `xml:lang="<target>"` is inserted directly after it. The source element is stamped with its own `xml:lang` too, so both halves of the pair are labelled.        |

Append follows AMCR's own multilingual convention rather than inventing one: an AMCR thesaurus
record stores a concept as `<amcr:heslo xml:lang="cs">` beside `<amcr:heslo_en>` under one `@id`, and
`load_vocab.py` reads exactly that pair on every glossary build.

```bash
python main.py ./data_samples/my_documents \
    --xpaths amcr-fields.txt --source_lang cs \
    --output-mode append \
    --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd
```

**Run append with `--xsd` the first time.** Whether the AMCR schema permits the repeated element
(its `maxOccurs`) is still an open question, and the validator answers it directly. A validation
failure there is the answer, not a defect in this feature.

**ALTO in append mode keeps the source and adds the translation as an `ALTERNATIVE`.** Every
`String` keeps its `CONTENT` (the scanned text and its geometry are untouched) and gains ALTO's own
element for "an alternative for the word", carrying the same English that replace mode would have
written into that box:

```xml
<TextBlock ID="B1" LANG="cs">
  <TextLine ID="L1">
    <String CONTENT="Záchranný" HPOS="…" …><ALTERNATIVE PURPOSE="translation:en">Rescue</ALTERNATIVE></String>
    <String CONTENT="výzkum" HPOS="…" …><ALTERNATIVE PURPOSE="translation:en">archaeological research</ALTERNATIVE></String>
  </TextLine>
</TextBlock>
```

The block keeps (or, when missing, gains) its **source** `LANG`, because its `CONTENT` is still in the
source language; the translation half is labelled by the `PURPOSE`. The `String` inventory stays 1:1
with the source. The per-`String` split is still one block translation distributed over the scanned
boxes (see [🧩 ALTO Dual-Pass Reconstruction](#-alto-dual-pass-reconstruction)), not a word-by-word
translation. `PURPOSE` exists on `ALTERNATIVE` from ALTO 2.1 on.

**ALTO in replace mode** writes the English into `CONTENT` and moves any language label the block
already carries (ABBYY writes `LANG="cs"` / `"sk"`) to the target language, so the output no longer
claims Czech for English text. An unlabelled input stays unlabelled.

Append mode is also **idempotent**: because its output is self-describing, a second pass over an
already-translated document skips those fields (and ALTO blocks that already carry a translation
`ALTERNATIVE`) instead of translating English into English.

The effective mode is recorded in the paradata record and in the document record's `translations`
block, so an artifact always says which contract produced it.

### Metadata mode through the HTTP API

`POST /translate` needs XPath targets for metadata mode, the same list the CLI reads from
`config.txt`'s `fields =` key. The service reads it from `AMCR_FIELDS_PATH` (default
`amcr-fields.txt`, already present in the image). With no readable file, ALTO requests are
unaffected and metadata requests are refused with `422` naming the variable — never a `200`
carrying an untranslated document.

```bash
curl -s -X POST localhost:8000/translate \
     -F "file=@C-N1000019.xml" -F "is_alto=false" \
     -F "source_lang=cs" -F "output_mode=append"
```

`is_alto`, `source_lang`, `target_lang` and `output_mode` are accepted **either** as multipart form
fields or as query-string parameters.

* `--document-json`: Optional baseline ATRIUM Document JSON to accrete onto (the cross-tool record passed along the pipeline).
* `--document-json-out`: Destination path for the updated ATRIUM Document JSON.
* `--download-dir`: Directory for URL-ingested inputs (default: `<output>/downloaded_inputs`).

**Exit codes** — `0` success · `1` usage or configuration error · `2` no input matched
the allowed formats · `3` one or more documents failed. Per-document failures are
logged and the batch continues, but the process still exits `3`, so a `Job` or cron
wrapper sees them.

---

## 🌐 API Service

The same pipeline behind an HTTP endpoint. Full operator documentation —
request/response schemas, the error table, shutdown behaviour — is in
**[service/README.md](service/README.md)** 📎; this is the overview.

```bash
docker compose --profile api up -d        # or: python -m service.api
curl -sf -F "file=@page.alto.xml" \
     "localhost:8000/translate?source_lang=cs&target_lang=en&is_alto=true" \
     -o page_en.alto.xml
```

| Method | Path         | Purpose                                                                       |
|--------|--------------|-------------------------------------------------------------------------------|
| `POST` | `/translate` | Translate one XML document (multipart upload; returns the rewritten XML)      |
| `GET`  | `/info`      | Service identity, version, endpoints, limits, available backends              |
| `GET`  | `/health`    | Liveness — 200 even mid-shutdown. `?deep=true` also checks the backing models |
| `GET`  | `/ready`     | Readiness — 503 until warm, and 503 the instant `SIGTERM` arrives             |
| `GET`  | `/docs`      | Swagger UI; machine-readable schema at `/openapi.json`                        |

The service is **stateless**: per-request scratch lives in a `TemporaryDirectory`
and dies with the request, so replicas scale horizontally with no shared state.

> ⚠️ **There is no authentication or rate limiting.** `/translate` performs
> unbounded outbound work against a translation backend on behalf of any caller.
> Deploy it behind your own gateway, or on a trusted network — do not expose it
> directly to the public internet.

---

## ⚙️ Environment Variables

**[.env.example](.env.example)** 📎 is the contract: every deployment-varying
variable, with its default and a one-line explanation. Copy it to `.env` and edit.
Configuration that varies per *run* rather than per *deployment* — input paths,
formats, vocabulary, XPath targets — lives in [config.txt](config.txt) 📎 instead.

The most commonly changed values:

| Variable                    | Default            | Effect                                                         |
|-----------------------------|--------------------|----------------------------------------------------------------|
| `TRANSLATION_URL`           | LINDAT             | Translation endpoint — point it at a self-hosted service       |
| `UDPIPE_URL`                | LINDAT             | UDPipe 2 endpoint used for vocabulary lemma matching           |
| `TRANSLATION_BACKEND`       | `lindat`           | `lindat` or `openai_compatible` (then set the `LLM_*` values)  |
| `PORT` / `HOST`             | `8000` / `0.0.0.0` | What the service binds, and what `healthcheck.py` probes       |
| `LOG_LEVEL`                 | `INFO`             | Root logger level; logs go to stdout as an event stream        |
| `MAX_UPLOAD_MB`             | `50`               | Upload limit, enforced while reading rather than after         |
| `ALLOWED_ORIGINS`           | `*`                | CORS origins (CSV). Narrow this for a deployment               |
| `GRACEFUL_SHUTDOWN_S`       | `20`               | How long uvicorn waits for in-flight requests on `SIGTERM`     |
| `LINDAT_GUARD_RETRIES`      | `2`                | Re-requests of a degenerate (looping / empty / runaway) reply  |
| `TRANSLATION_RERUN_ROUNDS`  | `1`                | End-of-document re-run rounds for flagged segments (`0` = off) |
| `TRANSLATION_RERUN_DELAY_S` | `10.0`             | Cool-down before each re-run round                             |
| `DEFAULT_SOURCE_LANG`       | `cs`               | `auto` runs: language an untrustworthy detection falls back to |
| `LANG_ID_MIN_CONFIDENCE`    | `0.5`              | `auto` runs: FastText score a candidate needs                  |
| `LANG_ID_MIN_LETTERS`       | `20`               | `auto` runs: shorter texts inherit label / document language   |

**How `.env` reaches the process** — the distinction matters: `docker compose`
injects it (both services declare `env_file`), but `python -m service.api` and
`python main.py` do **not** (nothing here calls `load_dotenv`), and neither does
Kubernetes. Outside a container, export them yourself:

```bash
set -a; . ./.env; set +a
```

---

## ☸️ Deployment

The reference Kubernetes manifest and its acceptance runbook live in the hub
repository, and are shared by all five ATRIUM services:

* **[ufal/atrium-project → docs/k8s_deployment.md](https://github.com/ufal/atrium-project/blob/master/docs/k8s_deployment.md)**
  — the manifest, the three probes, the port/bind configuration, and a *Known
  limits* section worth reading before promising anything from it.
* **[docs/templates/k8s/atrium-service.deployment.yaml](https://github.com/ufal/atrium-project/blob/master/docs/templates/k8s/atrium-service.deployment.yaml)**
  — the manifest itself. Substitute the image and size `resources.limits.memory`;
  everything else is identical across the five services by design.

What this image gives an orchestrator:

* `GET /ready` for `readinessProbe` and `startupProbe`, `GET /health` for
  `livenessProbe` — both declared, and the Dockerfile carries a `HEALTHCHECK`.
* `STOPSIGNAL SIGTERM`, after which `/ready` flips to 503 at once, new work is
  refused with 503, and in-flight translation is allowed to finish before the
  backend is torn down. A clean stop exits **143** (128 + SIGTERM), not 0.
* One uvicorn process per container and no shared state, so scale out by
  replica count.

> ⚠️ A single `/translate` call issues one retried backend request per chunk and
> can legitimately run for minutes. Raise `GRACEFUL_SHUTDOWN_S` **and** the
> deployment's `terminationGracePeriodSeconds` together for large documents —
> a request that outlives both is still cut short by `SIGKILL`.

---

## 🧠 Logic Overview

1. **Routing**: The script determines if it is running in ALTO mode (`--alto`, or `formats`
   containing `alto.xml`) or XML Metadata mode (`--xpaths`).
2. **Extraction & Translation**:
   * **ALTO**: Iterates `Page` → `TextBlock` → `TextLine` → `String`, and reconstructs each
     line's text from its `String` `CONTENT` attributes. Each block is translated with a
     **dual-pass** strategy and the result is **realigned** to the physical line/`String`
     layout — see [🧩 ALTO Dual-Pass Reconstruction](#-alto-dual-pass-reconstruction).
   * **XML Metadata**: Uses deep recursive namespace extraction (essential for OAI-PMH envelopes and custom schema
   wrappers). Finds elements matching the user-provided XPaths, translates their text content, and replaces it in the tree.
   Compatible with any well-formed XML.
3. **Language Identification** *(only with `--source_lang auto`)*: the document's language is resolved first, then
   each ALTO `TextBlock` (applied to every line in it) or metadata field — see
   [Source-language identification](#source-language-identification) below.
4. **Vocabulary Overriding** *(optional)*: When a vocabulary CSV is loaded, the **Tag-and-Protect** strategy
   is applied before each NMT call.  Multi-word phrases are matched first (longest-first substring), then single-word
   terms are matched via **UDPipe lemmatisation** [^6] (with a singular/plural number-agreement guard).  Matched terms
   are replaced with NMT-safe sentinels, translated, and then restored with the controlled vocabulary translations.
5. **Sentence-Aware Chunking**: Texts longer than 4,000 characters are split at the highest-priority boundary available
   in each window, in strict order: newline (`\n`) → sentence-terminal punctuation (`. `, `! `, `? `) → clause-level
   punctuation (`; `, `, `) → word boundary, with a hard cut as the last resort. The priority is now actually enforced
   (the highest tier with a match wins), so whole sentences are kept together for the NMT model, improving translation
   quality compared to raw word-boundary splitting.
6. **Output**: Generates the translated `.xml` file preserving all original tags and namespaces,
   alongside a per-document `_log.csv` file for manual QA review.  Optionally validates against an XSD schema.

---

### Source-language identification

With `--source_lang auto` the pipeline never uses a raw FastText guess. On a real run over the Czech ALTO sample FastText
answered `krc`, `yue`, `bod`, `epo` and `swh` for short OCR blocks — names, numbers, "Objednatel:" — and every such guess
used to become the block's source language (UDPipe had no model for it, page batches split per bogus language, append mode
would have written it into the output as `LANG`). Whenever detection could not run at all, the answer was `en` — the
*target* — so the block was returned untranslated.

`processors/language.py::resolve_source_language` decides instead, in this order:

| # | Basis      | Used when                                                                                                                                                                                    |
|---|------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 | `detected` | the text has at least `LANG_ID_MIN_LETTERS` (20) letters, and one of FastText's top-5 candidates scores at least `LANG_ID_MIN_CONFIDENCE` (0.5) **and** is a language the backend can translate |
| 2 | `hint`     | the element's own label — ALTO `LANG`/`language` (ABBYY writes one per block), metadata `xml:lang` — names a language the backend can translate                                            |
| 3 | `context`  | the language of the whole document, resolved once, up front, by rule 1 over its first ~20 000 characters                                                                                      |
| 4 | `default`  | the default source language: `--default-source-lang` → `default_source_lang` (config) → `DEFAULT_SOURCE_LANG` → `cs`                                                                         |

"Can translate" is derived from the backend: for LINDAT, the source side of every model pair that ends in the target
language (`cs, de, fr, pl, ru, uk` for English), plus the target itself (a block already in English is left as it is).
`LANG_ID_LANGUAGES` narrows that set further. The text given to FastText is reduced to letters — digits and punctuation
are most of an OCR fragment and none of its language.

Each document logs one line of what happened, as a WARNING when FastText guesses were overridden:

```
MTX201501307_anon: source language — document cs (detected, FastText top guess cs 0.97);
  blocks: cs 1193 (hint 988, context 158, detected 47); FastText guesses not used: eo×11, bod×6, swh×6, krc×5, yue×3.
```

The resolved document language is recorded in the Document JSON (`translations.detected_source_lang`) and the policy in
paradata. To tune the two thresholds on real data, `python -m eval.langid_report <file.alto.xml> > langid.csv` lists, per
block, FastText's top-3 guesses next to the resolved language and its basis (it honours the same environment variables).

> **On a single-language corpus, pass `--source_lang cs`.** No detection runs, and the CC BY-NC FastText model is not
> loaded (see the licensing note below).

### 🧩 ALTO Dual-Pass Reconstruction

ALTO stores text spatially: each `TextLine` holds a sequence of `String` elements, and each
`String` carries one token in its `CONTENT` attribute (plus its position). Translating naively
line-by-line loses cross-line context and produces poor NMT output; translating only the whole
block produces fluent text but discards the line/`String` structure that must be preserved.

The wrapper resolves this tension per `TextBlock` in six stages (implemented in
`process_alto_xml` and `_align_tokens_to_lines` in `utils.py`):

1. **Gather** — for every `TextLine` in the block, collect its `String` elements and
   reconstruct the original line text by joining their `CONTENT` values.
2. **Aggregate** — concatenate all line texts into a single block-level string.
3. **Resolve the language** — (when `--source_lang auto`) **once for the whole block**, so every line in the
   block is translated with a consistent source language; a block FastText cannot judge reliably takes its
   `LANG` label or the document's language (see
   [Source-language identification](#source-language-identification)).
4. **Pass 1 — block translation** — translate the full block text (all blocks of a page in one
   batched request, see [Page-Level Batching](#performance-page-level-batching)).
   This is the **high-quality semantic translation** whose tokens are written back to the document.
5. **Pass 2 — line translations** — translate each non-empty line of every translated block that has
   at least two text lines (batched per page). These per-line translations are *not* written to the
   output; they serve only as **structural anchors** that tell the aligner roughly how many words
   each physical line should receive.
6. **Validation, flagging, re-run** — every block translation and every anchor is checked against
   its own source (see [Degenerate-output guard](#degenerate-output-guard-flagging-and-re-run)). A block
   or anchor that is still unusable is **flagged**, and all flagged segments are re-run after the last
   page. A block that never recovers keeps its source text and is not redistributed at all.
7. **Alignment + redistribution**:
   * `_align_block` partitions the Pass-1 block tokens into one bucket per line. An anchor is used
     only if it is a plausible translation **of its own source line**; then the original rule
     applies — a sliding window of ±50 % around the anchor's word count, choosing the split point
     that maximises `difflib.SequenceMatcher` similarity against the anchor. A line whose anchor is
     empty, looping, runaway or truncated instead gets a **proportional share of the remaining
     tokens by the remaining source word counts** (logged `approx_alignment`), so one bad anchor can
     neither starve nor flood its neighbours. No line with source text is left empty while tokens
     remain, and the **last line with source text** receives the remainder.
   * Within each line, the bucket's tokens are distributed across that line's `String`
     elements with a **greedy 1-to-1 mapping**: each `String` except the last gets one token
     (empty string if the bucket is exhausted), and the **last `String` of the line absorbs
     all remaining tokens**.
   * The values go into `CONTENT` in `replace` mode, or into a
     `<ALTERNATIVE PURPOSE="translation:<lang>">` child of the untouched `String` in `append` mode.

This guarantees that translated words never cross line boundaries, that every `String`
element retains its original position, and that no token from the block translation is lost.

> **Why the anchors are validated:** the 2026-09-26 refresh showed what an unvalidated anchor does.
> With page-level batching the anchors are one request per page; a degenerate reply filled some anchor
> slots with 50+ words and left the others empty, and the anchor-trusting aligner turned that into
> lines holding 71 words next to lines holding none — invisibly, since anchors are never logged.

> **Per-page API cost:** normally **2** batched calls per language group (blocks + anchors). A
> fallback costs one call per block or line on that page; a flagged segment costs up to
> `LINDAT_GUARD_RETRIES` re-requests plus `TRANSLATION_RERUN_ROUNDS` re-runs. With a vocabulary loaded,
> each call also runs the Tag-and-Protect pipeline.

> **Edge cases:**
> * A block with a **single text line** skips the alignment search — all block tokens go to that line.
> * Lines whose original text is empty receive an empty bucket (and no anchor translation).
> * If Pass 1 yields **fewer** tokens than there are `String` elements in a line, the trailing
>   `String` elements are set to empty `CONTENT` (append: no `ALTERNATIVE`); if it yields **more**,
>   the surplus is crammed into the line's last `String`.
> * `--fast-align` skips Pass 2 and places every line by source word count.

---

## 📊 Translation CSV Logs

The wrapper generates a **per-document** CSV log for every processed XML file, named
`<original_filename>_log.csv` (e.g., [MTX201501307_anon_log.csv](data_samples/in-place_translated_files/alto/MTX201501307_anon_log.csv)📎). These logs are written to the same output directory
as the translated XML files and are intended for **line-by-line manual QA review**.

| Column               | ALTO value                                        | XML Metadata value     |
|----------------------|---------------------------------------------------|------------------------|
| `file`               | source filename (stem)                            | source filename (stem) |
| `page_num`           | page index (1-based)                              | *(empty)*              |
| `line_num`           | `TextLine` element ID                             | full XPath expression  |
| `text_<source_lang>` | original `CONTENT` text of the line               | original element text  |
| `text_<target_lang>` | translated text **as redistributed to that line** | translated text        |
| `status`             | how the line's translation was obtained (below)   | same                   |

| `status`           | Meaning                                                                                                     |
|--------------------|-------------------------------------------------------------------------------------------------------------|
| `ok`               | Translated and aligned normally.                                                                            |
| `rerun`            | Flagged during processing (degenerate reply) and recovered by the end-of-document re-run.                   |
| `approx_alignment` | ALTO only: the line's anchor was unusable, so its words were placed by source word count.                   |
| `untranslated`     | Still degenerate after the re-run: the **source text was kept** in the output and the target cell is empty. |

> **Note (ALTO):** Because the target column reflects the tokens *aligned and redistributed*
> to each physical line (not a standalone re-translation), it shows exactly what was written
> into that line's `String` elements (or their `ALTERNATIVE`s in append mode) — making the CSV a
> faithful audit of the reconstruction. Rows are written once per document, in document order: the log is written to
> `<doc>_log.csv.partial` and swapped into place when the document is done, so the previous log stays readable (and
> consistent with the previous XML) for the whole run, and a failed or interrupted run never leaves an empty log.

The column names for the source and target text are dynamic: they reflect the actual
language codes in use (e.g., `text_auto` / `text_en` when running with
`--source_lang auto --target_lang en`).

**Example** ([C-TX-202500252.xml](data_samples/my_documents/C-TX-202500252.xml)📎):

```
file,page_num,line_num,text_auto,text_en,status
C-TX-202500252,,//amcr:amcr/amcr:dokument/amcr:popis,"Stará Boleslav - odvodnění ohradní kamenné zdi …","Old Boleslav - drainage of enclosure stone wall …",ok
```

---

## 🗄️ Paradata JSON Logs

The wrapper generates a **run-level** JSON provenance record after every execution, named
`YYMMDD-HHmmss_translator.json`. It is written to the run's **output directory** alongside the
translated files (the in-repo [paradata](data_samples/in-place_translated_files/alto/paradata) 📁 directories
under `data_samples/` hold only example logs for development).

They are separate from the per-document translation CSV logs above: CSV logs capture what was
translated line by line; paradata JSONs capture *how the run was configured and what it produced in
aggregate*.

For single-file workflows, where one input passes through several tools or repositories, the
per-tool logs can be fused into one record per input file via `merge_paradata_files()`; the merged
record re-derives the end-to-end license from the union of all components used.

<details>
<summary>Paradata fields and Example paradata JSON structure 👀</summary>

### Fields of the paradata JSON

| Key                                 | Description                                                                                                 |
|-------------------------------------|-------------------------------------------------------------------------------------------------------------|
| `schema_version`                    | Paradata schema version (currently `"2.0"`)                                                                 |
| `program`                           | Always `"translator"`                                                                                       |
| `tool_version`                      | Tool version tag, from `para_config.txt` (e.g. `v0.5.0`)                                                    |
| `repository`                        | Runner repository; resolved dynamically (`ATRIUM_RUNNER_REPO` env if set)                                   |
| `runner_ref`                        | Git ref/SHA the running container was built from (`ATRIUM_RUNNER_REF`)                                      |
| `docker_image`                      | Running container image (`ATRIUM_RUNNER_IMAGE`); empty placeholder if unset                                 |
| `run_id`                            | Timestamp-based unique run identifier                                                                       |
| `license`                           | Effective output license, **computed** from the components actually used                                    |
| `license_url`                       | Canonical URL for the effective license                                                                     |
| `license_detail`                    | Resolution breakdown: per-component licenses, `is_non_commercial`, `is_share_alike`, `determined_by`, notes |
| `start_time` / `end_time`           | ISO 8601 UTC timestamps                                                                                     |
| `duration_seconds`                  | Wall-clock runtime                                                                                          |
| `config`                            | Snapshot of all CLI / config-file parameters used (incl. `vocabulary_protected_terms` when a vocab is used) |
| `statistics.input_files_total`      | Number of input files submitted                                                                             |
| `statistics.successfully_processed` | Number of files that produced output                                                                        |
| `statistics.skipped_files`          | Number of files skipped due to errors                                                                       |
| `statistics.output_counts_by_type`  | Per-type file counts (`xml`, `csv`)                                                                         |
| `statistics.performance_per_minute` | Files produced per minute per output type                                                                   |
| `skipped_files_detail`              | List of `{file, reason, timestamp}` objects for every skip                                                  |

> **Note on licensing:** the license is no longer a fixed value. It is the most restrictive license
> among the components used in the run. A run that exercises the LINDAT translation models and the
> UDPipe linguistic models resolves to **CC BY-NC 4.0** (non-commercial); the
> component→license mapping lives in this repository's [para_config.txt](para_config.txt) 📎.

### Example paradata JSON structure

```json
{
  "schema_version": "2.0",
  "program": "translator",
  "tool_version": "v0.5.0",
  "repository": "[https://github.com/ufal/atrium-translator](https://github.com/ufal/atrium-translator)",
  "runner_ref": "a1b2c3d",
  "docker_image": "ghcr.io/ufal/atrium-translator:v0.5.0",
  "run_id": "260321-102451",
  "license": "CC BY-NC 4.0",
  "license_url": "[https://creativecommons.org/licenses/by-nc/4.0/](https://creativecommons.org/licenses/by-nc/4.0/)",
  "license_detail": {
    "effective_license": "CC BY-NC 4.0",
    "is_non_commercial": true,
    "is_share_alike": false,
    "determined_by": ["lindat_cubbitt", "udpipe2_models"],
    "components": [
      { "name": "fasttext",       "license": "CC BY-NC 4.0" },
      { "name": "lindat_cubbitt", "license": "CC BY-NC 4.0" },
      { "name": "udpipe2_models", "license": "CC BY-NC 4.0" }
    ]
  },
  "duration_seconds": 63.017,
  "config": {
    "source_lang": "auto",
    "target_lang": "en",
    "vocabulary": "data_samples/vocabulary.csv",
    "mode": "alto"
  },
  "statistics": {
    "input_files_total": 16,
    "successfully_processed": 16,
    "skipped_files": 0,
    "output_counts_by_type": { "xml": 16, "csv": 16 },
    "performance_per_minute": { "xml": 15.23, "csv": 15.23 }
  },
  "skipped_files_detail": []
}
```
</details>

The logger is written by [atrium_paradata.py](atrium_paradata.py) 📎 (shared across all ATRIUM
pipeline repositories), which reads this repository's [para_config.txt](para_config.txt) 📎 for the
tool version and the component→license table, and resolves the effective license via
[para_licenses.py](para_licenses.py) 📎.

---

## 📄 License & Citation

The **code** is MIT licensed — see [LICENSE](LICENSE) 📎.

The **licence of what it produces is computed per run**, not fixed, because it
depends on which components a run actually exercised. `lindat_cubbitt` is
CC BY-NC-SA 4.0 and the FastText language-ID weights are CC BY-NC 4.0, so a
default run using CUBBITT yields **non-commercial** output. The component→licence
table is [para_config.txt](para_config.txt) 📎, the resolution lives in
[para_licenses.py](para_licenses.py) 📎, and the effective result is written into
every paradata record as `license`, `license_url` and `license_detail`.

> **If you need commercially usable output**:
>
> * select a permissively licensed backend: `--backend ct2` with
>   `CT2_MODEL_FAMILY=eurollm`, `madlad` or `opus` (**not** `nllb`, whose weights are
>   CC BY-NC 4.0), or `--backend openai_compatible` once the provider's actual terms
>   are recorded for `llm_api` in [para_config.txt](para_config.txt) 📎 — until then
>   that component is an unrecognised licence, which resolves as non-commercial and
>   share-alike;
> * pass an explicit `--source_lang`, so the CC BY-NC FastText model is never loaded;
> * run without the AMCR/TEATER vocabulary (CC BY-NC);
> * check the `license_detail` block of the paradata for the run — it records which
>   components were exercised and why the result resolved as it did.
>
> See [docs/translation-backends.md](docs/translation-backends.md) 📎 for the
> licensing matrix.

To cite this tool, use [CITATION.cff](CITATION.cff) 📎 — GitHub renders it as
*"Cite this repository"* in the sidebar.

---

## 🙏 Acknowledgements

**Support & questions:** open an issue on the repository [^2] 🔗, or write to **lutsai@ufal.mff.cuni.cz**.

- **Developed by** UFAL [^3] 👥
- **Funded by** ATRIUM [^4] 💰
- **Shared by** ATRIUM [^4] & UFAL [^3] 🔗
- **Translation API**: LINDAT/CLARIAH-CZ Translation Service [^1] 🔗
- **Lemmatisation API**: LINDAT/CLARIAH-CZ UDPipe Service [^6] 🔗
- **Language Identification**: Facebook FastText [^5] 🔗
- **Vocabulary Sources**: AMCR OAI-PMH API [^7] 🔗, TEATER GraphQL API [^8] 🔗

**©️ 2026 UFAL & ATRIUM**

[^1]: https://lindat.mff.cuni.cz/services/translation/
[^2]: https://github.com/ufal/atrium-translator
[^3]: https://ufal.mff.cuni.cz/home-page
[^4]: https://atrium-research.eu/
[^5]: https://huggingface.co/facebook/fasttext-language-identification
[^6]: https://lindat.mff.cuni.cz/services/udpipe/
[^7]: https://api.aiscr.cz/2.2/oai?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo
[^8]: https://teater.aiscr.cz/
