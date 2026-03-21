# 🏛️ ATRIUM - LINDAT Translation Wrapper 🌍

A modular Python wrapper specifically designed for the **LINDAT Translation API** [^1].
Following project scope requirements, this tool is strictly focused on processing 
**XML and its direct derivatives** (ALTO XML and AMCR metadata records). It identifies the 
source language using **FastText** [^5], translates the content to English (or other target languages),
optionally overrides domain-specific terms using a **Tag-and-Protect vocabulary** strategy
backed by **UDPipe lemmatisation** [^6], and safely reconstructs the original XML structure.

## 📚 Table of Contents

- [✨ Features](#-features)
- [🛠️ Prerequisites](#-prerequisites)
- [📂 Project Structure](#-project-structure)
- [💻 Usage](#-usage)
  - [📖 ALTO XML Mode](#-alto-xml-mode)
  - [🏛️ AMCR Metadata Mode](#-amcr-metadata-mode)
  - [📘 Vocabulary / Tag-and-Protect](#-vocabulary--tag-and-protect)
  - [🗂️ Harvesting the Vocabulary](#-harvesting-the-vocabulary)
  - [⚙️ Configuration File Support](#-configuration-file-support)
  - [⚙️ Supported Arguments](#-supported-arguments)
- [🧠 Logic Overview](#-logic-overview)
- [Paradata logs](#paradata-logs)
- [🙏 Acknowledgements](#-acknowledgements)

---

## ✨ Features

* 🎯 **Dedicated XML Processing**: Narrowly defined and optimised exclusively for ALTO XML and AMCR metadata to ensure universal, safe, and easy usage.
* 📖 **ALTO Translation Mode**: Translates only the `CONTENT` attributes natively. Tied to a simple flag (`--alto`) so users don't need to provide complex configurations.
* 🏛️ **AMCR Metadata Mode**: Translates specific elements based on a provided list of XPaths (e.g., [amcr-fields.txt](amcr-fields.txt) 📎), safely puts them back into the XML, 
and features deep recursive namespace extraction to handle OAI-PMH envelopes.
* ✅ **XSD Validation**: Optionally validates AMCR outputs against an XSD schema (e.g., `https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd`) to guarantee structural integrity.
* 📊 **Supplementary CSV Logging**: Automatically produces a supplementary QA CSV file with columns:
  `file, page_num, line_num, text_<source_lang>, text_<target_lang>`
  for easy manual checking of translations.
* 🕵️ **Language Detection with Intelligent Fallback**: Automatically identifies the source language using **FastText** (Facebook) [^5]. If the detection confidence is 
below `0.2`, it defaults to Czech (`cs`) to ensure the pipeline continues seamlessly.
* 🔤 **Tag-and-Protect Vocabulary Overriding**: When a vocabulary CSV is supplied, domain-specific terms are protected before translation using unique placeholder tags. 
Single-word terms are matched by lemma via the **LINDAT UDPipe API** [^6]; multi-word phrases use case-insensitive substring matching (longest match first). 
Vocabulary translations are then restored after the NMT call, ensuring controlled terminology is never garbled.
* 🗂️ **Automated Vocabulary Harvesting**: The bundled `load_vocab.py` script downloads Czech→English term pairs from both the
**AMCR OAI-PMH API** and the **TEATER GraphQL API** and merges them into a single ready-to-use CSV.
* 🔗 **LINDAT API Integration**: Seamlessly connects to the LINDAT Translation API (v2) [^1]. Uses smart, **space-aware chunking** (max 4,000 characters) 
to protect word boundaries and prevent API truncation errors.

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
├── main.py                    # 🚀 Entry point – CLI routing for ALTO vs. AMCR processing
├── load_vocab.py              # 🗂️ Vocabulary harvester (AMCR OAI-PMH + TEATER GraphQL → CSV)
├── atrium_paradata.py         # 📊 Unified provenance/paradata logger
├── requirements.txt           # 📦 Python dependencies
├── config.txt                 # ⚙️ Configuration parameters
├── amcr-fields.txt            # 📄 List of AMCR XPath targets for XML translation
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
│   └── translated_files/      # 📂 Output directory for translated XML files and their logs
│       ├── MTX201501307_en.alto.xml  # 📎 Translated ALTO XML output file
│       ├── MTX201501307_log.csv      # 📎 Supplementary CSV log for the translated ALTO XML file
│       └── ...
├── paradata/
│   ├── <date>-<time>_translator.json  # 📊 Aggregated log of all translations for analysis
│   └── ...
└── utils.py                   # 🔧 ALTO & AMCR parsing, CSV logging, XSD validation, and XML tree reconstruction
```

---

## 💻 Usage

Run the wrapper from the command line. The default target language is English (`en`).

### 📖 ALTO XML Mode

Use the `--alto` flag together with `--formats alto.xml` (or set `formats = alto.xml` in
`config.txt`). This processes ALTO files by strictly targeting their `String` `CONTENT` attributes.

```bash
python main.py ./data_samples/my_documents --alto --formats alto.xml --target_lang en
```

Example of ALTO XML processing:
- **Input**: [MTX201501307.alto.xml](data_samples/my_documents/MTX201501307.alto.xml) 📎 
- **Output**: [MTX201501307_en.alto.xml](data_samples/translated_files/MTX201501307_en.alto.xml) 📎 

The translation is performed per `TextBlock`, and the translated words are redistributed back into the
individual `CONTENT` attributes of each `String` element within a `TextLine`.

### 🏛️ AMCR Metadata Mode

Process AMCR records by passing your list of XPaths and optionally providing an XSD URL for validation.

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd --target_lang en
```
OR
```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt --target_lang en
```

Examples of **input** files are downloaded into [my_documents](data_samples/my_documents) 📂
and their filenames start with `C-` according to the [amcr-inputs.txt](amcr-inputs.txt) 📎 list of input files.

Examples of **output** files are saved in [translated_files](data_samples/translated_files) 📂
and include `.csv` log files (containing only processed lines) alongside `.xml` files translated to the target language.

### 📘 Vocabulary / Tag-and-Protect

Provide a two-column CSV (`source_lemma,target_translation`) to activate the **Tag-and-Protect** strategy.
When enabled, domain-specific terms are shielded from the NMT model and replaced with guaranteed vocabulary translations instead.

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt --vocabulary data_samples/vocabulary.csv --target_lang en
```

Or set the path in `config.txt`:

```ini
vocabulary = data_samples/vocabulary.csv
```

**How it works:**

1. **Multi-word phrase pass** – phrases containing spaces (e.g. `fotografie události`) are matched case-insensitively, longest match first, and replaced with `__TERM_N__` placeholder tags.
2. **Single-word lemma pass** – the remaining text is lemmatised via the LINDAT UDPipe API [^6]. Tokens whose base form appears in the vocabulary are similarly tagged.
3. **Translation** – the tagged text (with unknown-looking placeholders) is sent to the LINDAT Translation API. NMT models leave unrecognised tokens untouched.
4. **Restoration** – all `__TERM_N__` tags in the translated output are replaced with the corresponding vocabulary translations.

If no vocabulary file is provided, the translator behaves exactly as before (no UDPipe calls are made).

### 🗂️ Harvesting the Vocabulary

The `load_vocab.py` script downloads term pairs automatically from two sources and merges them into a single CSV:

| Source     | Endpoint                                 | Method                                                         |
|------------|------------------------------------------|----------------------------------------------------------------|
| **AMCR**   | `https://api.aiscr.cz/2.2/oai?set=heslo` | OAI-PMH `ListRecords` with resumption token paging             |
| **TEATER** | `https://teater.aiscr.cz/api/graphql`    | GraphQL introspection → `exportAll` or `search`-based fallback |

```bash
# Full harvest (both sources):
python load_vocab.py

# Skip one source:
python load_vocab.py --skip-teater
python load_vocab.py --skip-amcr

# Custom output path and request delay:
python load_vocab.py --out my_vocab.csv --delay 0.5
```

The merged vocabulary is written to `data_samples/vocabulary.csv` by default (AMCR entries take 
precedence over TEATER on key collision).

### ⚙️ Configuration File Support

Instead of passing all arguments via the command line, you can use a configuration 
file `config.txt` to define default paths and parameters. **CLI arguments always take
precedence over config file values** — the config file supplies defaults only for
arguments that are not explicitly passed on the command line.

Example [config.txt](config.txt):
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

### ⚙️ Supported Arguments

* `input_path`: Path to a single source file, a directory containing XML files, or a `.txt` file listing URLs.
* `--output`, `-o`: Output file path (for single-file mode) or output directory (for batch mode).
* `--source_lang`, `-src`: Source language code (e.g., `cs`, `fr`). Use `auto` to auto-detect. Default: `cs`.
* `--target_lang`, `-tgt`: Target language code (e.g., `en`, `cs`). Default: `en`.
* `--formats`: Comma-separated list of file extensions to process (e.g., `alto.xml,txt` or `xml,txt`). Default: `xml`.
* `--config`, `-c`: Path to the configuration file (default: `config.txt`).
* `--alto`: Flag to enable ALTO XML in-place translation mode.
* `--xpaths`: Path to a `.txt` file containing XPaths for AMCR metadata translation.
* `--xsd`: Optional URL or local path to an XSD file for AMCR output validation.
* `--vocabulary`: Path to a CSV vocabulary file (`source_lemma,target_translation`) to activate Tag-and-Protect term overriding.

---

## 🧠 Logic Overview

1. **Routing**: The script determines if it is running in ALTO mode (`--alto`) or AMCR mode (`--xpaths`).
2. **Extraction & Translation**:
   * **ALTO**: Iterates through `Page` → `TextLine` → `String`. Extracts the `CONTENT` attribute, reconstructs the entire line for contextual API translation, and perfectly redistributes the translated words back into the `CONTENT` attributes.
   * **AMCR**: Uses deep recursive namespace extraction (vital for OAI-PMH API envelopes). Finds elements matching the provided XPaths, translates their text content, and replaces it in the tree.
3. **Language Identification**: The text is analysed by **FastText** [^5] to determine the source language. If the confidence score is below `0.2`, the system automatically defaults to Czech (`cs`).
4. **Vocabulary Overriding** *(optional)*: When a vocabulary CSV is loaded, the **Tag-and-Protect** strategy is applied before the NMT call. Multi-word phrases are matched first (longest-first substring), then single-word terms are matched via **UDPipe lemmatisation** [^6]. Matched terms are replaced with `__TERM_N__` placeholders, translated safely through the API, and then restored with the controlled vocabulary translations.
5. **Translation**: Text (with any protected placeholders) is passed to the **LINDAT Translation API** [^1]. Texts longer than 4,000 characters are safely chunked at the nearest space to prevent mid-word cuts.
6. **Output**: Generates the translated `.xml` file preserving all original tags/namespaces, alongside a supplementary `_log.csv` file containing the line-by-line translation data for manual QA review. Optionally validates AMCR output against an XSD schema.

---

## Paradata logs

The wrapper generates a supplementary CSV log file for each processed XML file, named with the
pattern `<original_filename>_log.csv`. This log contains the following columns:

| Column               | ALTO value              | AMCR value             |
|----------------------|-------------------------|------------------------|
| `file`               | source filename (stem)  | source filename (stem) |
| `page_num`           | page index (1-based)    | *(empty)*              |
| `line_num`           | `TextLine` element ID   | full XPath expression  |
| `text_<source_lang>` | original `CONTENT` text | original element text  |
| `text_<target_lang>` | translated text         | translated text        |

The column names for the source and target text are dynamic: they reflect the actual language
codes in use (e.g., `text_auto` / `text_en` when running with `--source_lang auto --target_lang en`).

Moreover, the [paradata](paradata) 📂 directory contains aggregated JSON logs of all processed
files, allowing run-level metadata (timing, counts, skipped files) to be queried for analysis
and reporting.

## 🙏 Acknowledgements

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^2] 🔗

- **Developed by** UFAL [^3] 👥
- **Funded by** ATRIUM [^4] 💰
- **Shared by** ATRIUM [^4] & UFAL [^3] 🔗
- **Translation API**: LINDAT/CLARIAH-CZ Translation Service [^1] 🔗
- **Lemmatisation API**: LINDAT/CLARIAH-CZ UDPipe Service [^6] 🔗
- **Language Identification**: Facebook FastText [^5] 🔗

**©️ 2026 UFAL & ATRIUM**

[^1]: https://lindat.mff.cuni.cz/services/translation/
[^2]: https://github.com/ufal/atrium-translator
[^3]: https://ufal.mff.cuni.cz/home-page
[^4]: https://atrium-research.eu/
[^5]: https://huggingface.co/facebook/fasttext-language-identification
[^6]: https://lindat.mff.cuni.cz/services/udpipe/