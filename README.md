<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.8+-blue.svg" title="Python Version"></a>
  <a href="https://lindat.mff.cuni.cz/services/translation/"><img src="https://img.shields.io/badge/API-LINDAT%20Translation-0055A4.svg" title="LINDAT Translation API"></a>
  <a href="https://lindat.mff.cuni.cz/services/udpipe/"><img src="https://img.shields.io/badge/API-UDPipe2-0055A4.svg" title="UDPipe2"></a>
  <a href="https://huggingface.co/facebook/fasttext-language-identification"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20HF-fasttext--langID-yellow.svg" title="FastText Language Identification"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-translator" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---


# 🏛️ ATRIUM - LINDAT Translation Wrapper 🌍

A modular Python wrapper specifically designed for the **LINDAT Translation API** [^1].
Following project scope requirements, this tool is strictly focused on processing
**XML and its direct derivatives**.  It supports two input modes:

| Mode             | Input                                                      | Key flag   |
|------------------|------------------------------------------------------------|------------|
| **ALTO XML**     | Scanned-document ALTO XML                                  | `--alto`   |
| **XML Metadata** | Any structured XML (AMCR [^7], OAI-PMH, or custom schemas) | `--xpaths` |

The wrapper identifies the source language using **FastText** [^5], translates the
content to English (or any other target language supported by the LINDAT API),
optionally overrides domain-specific terms using a **Tag-and-Protect vocabulary**
strategy backed by **UDPipe lemmatisation** [^6], and safely reconstructs the original
XML structure without altering tags, namespaces, or OAI-PMH envelopes.

## 📚 Table of Contents

- [✨ Features](#-features)
- [🛠️ Prerequisites](#-prerequisites)
- [📂 Project Structure](#-project-structure)
- [💻 Usage](#-usage)
  - [📖 ALTO XML Mode](#-alto-xml-mode)
  - [📄 XML Metadata Mode](#-xml-metadata-mode)
  - [📘 Vocabulary / Tag-and-Protect](#-vocabulary--tag-and-protect)
  - [🗂️ Harvesting the Vocabulary](#-harvesting-the-vocabulary)
  - [⚙️ Configuration File Support](#-configuration-file-support)
  - [⚙️ Supported Arguments](#-supported-arguments)
- [🧠 Logic Overview](#-logic-overview)
- [📊 Translation CSV Logs](#-translation-csv-logs)
- [🗄️ Paradata JSON Logs](#-paradata-json-logs)
- [🙏 Acknowledgements](#-acknowledgements)

---

## ✨ Features

* 🎯 **Dedicated XML Processing**: Narrowly defined and optimised exclusively for ALTO XML and structured metadata 
records, ensuring safe, universal usage without tag or namespace corruption.
* 📖 **ALTO Translation Mode**: Translates only the `CONTENT` attributes natively. Tied to a simple flag (`--alto`) 
so users do not need complex configuration.
* 📄 **XML Metadata Mode**: Translates specific elements based on a user-provided list of XPaths (e.g., 
[amcr-fields.txt](amcr-fields.txt) 📎), safely reconstructs the document tree, and handles deep recursive 
namespace extraction for OAI-PMH envelopes.  Works with **any conformant XML**, not only AMCR [^7] records.
* ✅ **XSD Validation**: Optionally validates metadata outputs against an XSD schema (e.g., 
`https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd`) to guarantee structural integrity after translation.
* 📊 **Per-document Translation CSV Logs**: Automatically produces a supplementary QA CSV file with columns 
`file, page_num, line_num, text_<source_lang>, text_<target_lang>` for easy manual review.
* 🗄️ **Run-level Paradata JSON Logs**: Each pipeline run appends a structured provenance record (timing, counts, 
configuration snapshot) to the [paradata](paradata)📁 directory for auditing and performance reporting.
* 🕵️ **Language Detection with Intelligent Fallback**: Automatically identifies the source language using 
**FastText** (Facebook) [^5]. If the detection confidence is below `0.2`, it defaults to Czech (`cs`) to 
ensure the pipeline continues seamlessly.
* ✂️ **Sentence-Aware Chunking**: Long texts are split at sentence boundaries (`\n`, `. `, `! `, `? `) before being 
sent to the translation API, preserving sentence context and improving NMT quality. Word and clause boundaries serve 
as secondary fallbacks.
* 🔤 **Tag-and-Protect Vocabulary Overriding**: When a vocabulary CSV is supplied, domain-specific terms are protected
before translation using unique placeholder tags. Single-word terms are matched by lemma via the **LINDAT UDPipe API** [^6]; 
multi-word phrases use case-insensitive substring matching (longest match first). Vocabulary translations are restored 
after the NMT call, ensuring controlled terminology is never garbled.
* 🗂️ **Automated Vocabulary Harvesting**: The bundled [load_vocab.py](load_vocab.py)📎 script downloads Czech→English term pairs from 
both the **AMCR OAI-PMH API** [^7] and the **TEATER GraphQL API** [^8] and merges them into a single ready-to-use CSV.
* 🔗 **LINDAT API Integration**: Seamlessly connects to the LINDAT Translation API (v2) [^1].

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

## 📂 Project Structure

```text
atrium-translator/
├── main.py                    # 🚀 Entry point – CLI routing for ALTO vs. XML Metadata processing
├── load_vocab.py              # 🗂️ Vocabulary harvester (AMCR OAI-PMH + TEATER GraphQL → CSV)
├── atrium_paradata.py         # 🗄️ Unified provenance/paradata logger
├── requirements.txt           # 📦 Python dependencies
├── config.txt                 # ⚙️ Configuration parameters
├── amcr-fields.txt            # 📄 Example XPath list for AMCR metadata translation
├── amcr-inputs.txt            # 📄 List of AMCR metadata input files (XML) to be processed
├── processors/
│   ├── __init__.py            # 📦 Package marker
│   ├── identifier.py          # 🌍 FastText language identification (ISO 639-3 to 639-1 mapping)
│   ├── lemmatizer.py          # 🔤 UDPipe-based lemmatizer for vocabulary term matching
│   └── translator.py          # 🔄 LINDAT API client with Tag-and-Protect vocabulary support
├── data_samples/
│   ├── vocabulary.csv         # 📘 Czech→English domain vocabulary (AMCR/TEATER thesaurus terms)
│   ├── my_documents/          # 📂 Sample input files (ALTO XML and downloaded AMCR metadata XMLs)
│   │   ├── MTX201501307.alto.xml  # 📎 Sample ALTO XML file for testing
│   │   └── ...
│   └── translated_files/      # 📂 Output directory for translated XML files and their CSV logs
│       ├── MTX201501307_en.alto.xml  # 📎 Translated ALTO XML output file
│       ├── MTX201501307_log.csv      # 📎 Per-document translation CSV log
│       └── ...
├── paradata/
│   ├── <date>-<time>_translator.json  # 🗄️ Run-level provenance JSON log
│   └── ...
└── utils.py                   # 🔧 ALTO & XML metadata parsing, CSV logging, XSD validation
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

Example of ALTO XML processing:
- **Input**: [MTX201501307.alto.xml](data_samples/my_documents/MTX201501307.alto.xml) 📎
- **Output**: [MTX201501307_en.alto.xml](data_samples/translated_files/MTX201501307_en.alto.xml) 📎

The translation is performed per `TextBlock`, and the translated words are redistributed back into the
individual `CONTENT` attributes of each `String` element within a `TextLine`.

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
   are matched case-insensitively, longest match first, and replaced with
   `__TERM_N__` placeholder tags.
2. **Single-word lemma pass** – the remaining text is lemmatised via the LINDAT UDPipe
   API [^6].  Tokens whose base form appears in the vocabulary are similarly tagged.
3. **Translation** – the tagged text is sent to the LINDAT Translation API.  NMT models
   leave unrecognised tokens untouched.
4. **Restoration** – all `__TERM_N__` tags in the translated output are replaced with
   the corresponding vocabulary translations.

If no vocabulary file is provided, the translator behaves exactly as before (no UDPipe
calls are made, no lemmatization is performed - just the basic translation preserving input file structure).

#### Vocabulary CSV format



The vocabulary file must be a UTF-8 encoded CSV with two columns:

```
source_lemma,target_translation
kostel,church
pohřebiště,burial ground
fotografie události,photograph of event
```


| Column               | Content                                                                                                                                                                                                              |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `source_lemma`       | The **lemmatised (dictionary) form** of the source term. For single-word terms this must match what UDPipe returns for the source language (see table below).  For multi-word phrases, any surface form is accepted. |
| `target_translation` | The canonical translation — typically the preferred English term from a controlled vocabulary or thesaurus.                                                                                                          |


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

> **Tip for non-Czech archives:** If your source XML is in a language other than Czech 🇨🇿,
> pass the corresponding `--source_lang` code and supply a matching vocabulary CSV whose
> `source_lemma` column uses that language's lemma conventions. The vocabulary harvesting
> script ([load_vocab.py](load_vocab.py)📎) currently targets Czech (**AMCR**[^7]/**TEATER**[^8]); for other languages
> you will need to compile the vocabulary manually or from your own thesaurus.

---

### 🗂️ Harvesting the Vocabulary

The [load_vocab.py](load_vocab.py)📎 script downloads term pairs automatically from two sources and
merges them into a single CSV:

| Source           | Endpoint                                 | Method                                                         |
|------------------|------------------------------------------|----------------------------------------------------------------|
| **AMCR** [^7]    | `https://api.aiscr.cz/2.2/oai?set=heslo` | OAI-PMH `ListRecords` with resumption token paging             |
| **TEATER**  [^8] | `https://teater.aiscr.cz/api/export`     | GraphQL introspection → `exportAll` or `search`-based fallback |

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
take precedence over TEATER [^8] on key collision).

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
formats = xml,txt
fields = amcr-fields.txt
output = ./data_samples/translated_files

# Optional: path to a vocabulary CSV file (source_lemma,target_translation).
# Leave blank or comment out to disable.
vocabulary = data_samples/vocabulary.csv
```

---

### ⚙️ Supported Arguments

* `input_path`: Path to a single source file, a directory containing XML files, or a `.txt` file listing URLs.
* `--output`, `-o`: Output file path (single-file mode) or output directory (batch mode).
* `--source_lang`, `-src`: Source language code (e.g., `cs`, `fr`). Use `auto` to auto-detect. Default: `cs`.
* `--target_lang`, `-tgt`: Target language code (e.g., `en`, `cs`). Default: `en`.
* `--formats`: Comma-separated list of file extensions to process (e.g., `alto.xml,txt` or `xml,txt`). Default: `xml`.
* `--config`, `-c`: Path to the configuration file (default: `config.txt`).
* `--alto`: Flag to enable ALTO XML in-place translation mode.
* `--xpaths`: Path to a `.txt` file containing XPaths for XML metadata translation (works with any XML schema).
* `--xsd`: Optional URL or local path to an XSD file for output validation.
* `--vocabulary`: Path to a CSV vocabulary file (`source_lemma,target_translation`) to activate Tag-and-Protect term overriding.

---

## 🧠 Logic Overview

1. **Routing**: The script determines if it is running in ALTO mode (`--alto`) or XML Metadata mode (`--xpaths`).
2. **Extraction & Translation**:
   * **ALTO**: Iterates through `Page` → `TextLine` → `String`. Extracts the `CONTENT` attribute, reconstructs the
   entire line for contextual API translation, and perfectly redistributes the translated words back into the `CONTENT` attributes.
   * **XML Metadata**: Uses deep recursive namespace extraction (essential for OAI-PMH envelopes and custom schema 
   wrappers). Finds elements matching the user-provided XPaths, translates their text content, and replaces it in the tree.  
   Compatible with any well-formed XML.
3. **Language Identification**: The text is analysed by **FastText** [^5] to determine the source language. 
If the confidence score is below `0.2`, the system automatically defaults to Czech🇨🇿 (`cs`).
4. **Vocabulary Overriding** *(optional)*: When a vocabulary CSV is loaded, the **Tag-and-Protect** strategy 
is applied before the NMT call.  Multi-word phrases are matched first (longest-first substring), then single-word 
terms are matched via **UDPipe lemmatisation** [^6].  Matched terms are replaced with `__TERM_N__` placeholders, 
translated safely through the API, and then restored with the controlled vocabulary translations.
5. **Sentence-Aware Chunking**: Texts longer than 4,000 characters are split at sentence 
boundaries (`\n`, `. `, `! `, `? `), falling back to clause and word boundaries. This preserves sentence context 
for the NMT model, improving translation quality compared to raw word-boundary splitting.
6. **Output**: Generates the translated `.xml` file preserving all original tags and namespaces, 
alongside a per-document `_log.csv` file for manual QA review.  Optionally validates against an XSD schema.

---

## 📊 Translation CSV Logs

The wrapper generates a **per-document** CSV log for every processed XML file, named
`<original_filename>_log.csv` (e.g., [MTX201501307_log.csv](data_samples/translated_files/MTX201501307_log.csv)📎). These logs are written to the same output directory
as the translated XML files and are intended for **line-by-line manual QA review**.

| Column               | ALTO value              | XML Metadata value     |
|----------------------|-------------------------|------------------------|
| `file`               | source filename (stem)  | source filename (stem) |
| `page_num`           | page index (1-based)    | *(empty)*              |
| `line_num`           | `TextLine` element ID   | full XPath expression  |
| `text_<source_lang>` | original `CONTENT` text | original element text  |
| `text_<target_lang>` | translated text         | translated text        |

The column names for the source and target text are dynamic: they reflect the actual
language codes in use (e.g., `text_auto` / `text_en` when running with
`--source_lang auto --target_lang en`).

**Example** ([C-TX-202500252.xml](data_samples/my_documents/C-TX-202500252.xml)📎):

```
file,page_num,line_num,text_auto,text_en
C-TX-202500252,,//amcr:amcr/amcr:dokument/amcr:popis,"Stará Boleslav - odvodnění ohradní kamenné zdi …","Old Boleslav - drainage of enclosure stone wall …"
```

---

## 🗄️ Paradata JSON Logs

The wrapper generates a **run-level** JSON provenance record after every execution.
These records are written to the [paradata](paradata)📁 directory (created automatically) and
are named with the pattern `YYMMDD-HHmmss_translator.json`.

They are separate from the per-document translation CSV logs above: CSV logs capture
what was translated line by line; paradata JSONs capture *how the run was configured
and what it produced in aggregate*.

<details>
<summary>Paradata fields and Example paradata JSON structure 👀</summary>

### Fields of the paradata JSON

| Key                                 | Description                                                |
|-------------------------------------|------------------------------------------------------------|
| `program`                           | Always `"translator"`                                      |
| `run_id`                            | Timestamp-based unique run identifier                      |
| `start_time` / `end_time`           | ISO 8601 UTC timestamps                                    |
| `duration_seconds`                  | Wall-clock runtime                                         |
| `config`                            | Snapshot of all CLI / config-file parameters used          |
| `statistics.input_files_total`      | Number of input files submitted                            |
| `statistics.successfully_processed` | Number of files that produced output                       |
| `statistics.skipped_files`          | Number of files skipped due to errors                      |
| `statistics.output_counts_by_type`  | Per-type file counts (`xml`, `csv`)                        |
| `statistics.performance_per_minute` | Files produced per minute per output type                  |
| `skipped_files_detail`              | List of `{file, reason, timestamp}` objects for every skip |


### Example paradata JSON structure

```json
{
  "program": "translator",
  "run_id": "260321-102451",
  "duration_seconds": 63.017,
  "config": {
    "source_lang": "auto",
    "target_lang": "en",
    "vocabulary": "data_samples/vocabulary.csv",
    "mode": "amcr"
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


The [paradata](paradata)📁 directory accumulates one JSON per run; each file is written by the
[atrium_paradata.py](atrium_paradata.py)📎 logger (shared across all ATRIUM pipeline repositories).

---

## 🙏 Acknowledgements

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^2] 🔗

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
[^7]: https://api.aiscr.cz/2.2/oai?set=heslo 
[^8]: https://teater.aiscr.cz/