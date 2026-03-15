# 🏛️ ATRIUM - LINDAT Translation Wrapper 🌍

A modular Python wrapper specifically designed for the **LINDAT Translation API** [^1].
Following project scope requirements, this tool is strictly focused on processing 
**XML and its direct derivatives** (ALTO XML and AMCR metadata records). It identifies the 
source language using **FastText** [^5], translates the content to English (or other target languages), 
and safely reconstructs the original XML structure.

## 📚 Table of Contents

- [✨ Features](#-features)
- [🛠️ Prerequisites](#-prerequisites)
- [📂 Project Structure](#-project-structure)
- [💻 Usage](#-usage)
  - [📖 ALTO XML Mode](#-alto-xml-mode)
  - [🏛️ AMCR Metadata Mode](#-amcr-metadata-mode)
  - [⚙️ Configuration File Support](#-configuration-file-support)
  - [⚙️ Supported Arguments](#-supported-arguments)
- [🧠 Logic Overview](#-logic-overview)
- [Paradata logs](#paradata-logs)
- [🙏 Acknowledgements](#-acknowledgements)

---

## ✨ Features

* 🎯 **Dedicated XML Processing**: Narrowly defined and optimized exclusively for ALTO XML and AMCR metadata to ensure universal, safe, and easy usage. 
* 📖 **ALTO Translation Mode**: Translates only the `CONTENT` attributes natively. Tied to a simple flag (`--alto`) so users don't need to provide complex configurations.
* 🏛️ **AMCR Metadata Mode**: Translates specific elements based on a provided list of XPaths (e.g., [amcr-fields.txt](amcr-fields.txt) 📎), safely puts them back into the XML, and features deep recursive namespace extraction to handle OAI-PMH envelopes.
* ✅ **XSD Validation**: Optionally validates AMCR outputs against an XSD schema (e.g., `https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd`) to guarantee structural integrity.
* 📊 **Supplementary CSV Logging**: Automatically produces a supplementary QA CSV file with columns: `file, page_num, line_num, text_src, text_tgt` for easy manual checking of translations.
* 🕵️ **Language Detection with Intelligent Fallback**: Automatically identifies the source language using **FastText** (Facebook) [^5]. If the detection confidence is low (< 0.2), it defaults to Czech (`cs`) to ensure the pipeline continues seamlessly.
* 🔗 **LINDAT API Integration**: Seamlessly connects to the LINDAT Translation API (v2) [^1]. Uses smart, **space-aware chunking** (max 4,000 characters) to protect word boundaries and prevent API truncation errors.

---

## 🛠️ Prerequisites

1. Clone the project files:
```bash
git clone https://github.com/ufal/atrium-translator.git
```
2. Create virtual environment and activate it (optional but recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```
3. Install the required Python packages:
```bash
cd atrium-translator
pip install -r requirements.txt
```

---

## 📂 Project Structure

```text
lindat-wrapper/
├── main.py                 # 🚀 Entry point for the CLI routing ALTO vs. AMCR processing
├── requirements.txt        # 📦 Python dependencies
├── config.txt              # ⚙️ Configuration parameters
├── amcr-fields.txt         # 📄 List of AMCR XPath targets for XML translation
├── amcr-inputs.txt         # 📄 List of AMCR metadata input files (XML) to be processed
├── processors/
│   ├── identifier.py       # 🌍 FastText language identification (ISO 639-3 to 639-1 mapping)
│   └── translator.py       # 🔄 LINDAT API client with space-aware chunking
├── data_samples/
│   ├── my_documents/       # 📂 Sample input files (ALTO XML and downloaded AMCR metadata XMLs)
│   │   ├── MTX201501307.alto.xml # 📎 Sample ALTO XML file for testing
│   │   └── ...
│   └── translated_files/   # 📂 Output directory for translated XML files and their logs
│       ├── MTX201501307_en.alto.xml # 📎 Translated ALTO XML output file
│       ├── MTX201501307_log.csv     # 📎 Supplementary CSV log for the translated ALTO XML file
│       └── ...
├── paradata/
│   ├── <date>-<time>_translator.json # 📊 Aggregated log of all translations for analysis
│   └── ...
└── utils.py                # 🔧 ALTO & AMCR parsing, CSV logging, XSD validation, and XML tree reconstruction
```

---

## 💻 Usage

Run the wrapper from the command line. The default target language is English (`en`).

### 📖 ALTO XML Mode

Use the `--alto` flag. This acts as a default setup to process ALTO files by strictly targeting their
`String`'s `CONTENT` attributes.

```bash
python main.py ./data_samples/my_documents --alto --target_lang en
```

Example of ALTO XML processing:
- **Input**: [MTX201501307.alto.xml](data_samples/my_documents/MTX201501307.alto.xml) 📎 
- **Output**: [MTX201501307.alto_en.xml](data_samples/translated_files/MTX201501307_en.alto.xml) 📎 

The translation is performed in a per-`TextBlock` manner, and reconstruction of XML elements structure is
performed on per-`TextLine` manner (Each text line has a `String` element with `CONTENT` attribute).

### 🏛️ AMCR Metadata Mode

Process AMCR records by passing your list of XPaths and optionally providing an XSD URL for validation.

```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt --xsd https://api.aiscr.cz/schema/amcr/2.2/amcr.xsd --target_lang en
```
OR
```bash
python main.py amcr-inputs.txt --xpaths amcr-fields.txt --target_lang en
```

Examples of **inputs** files are downloaded in [my_documents](data_samples/my_documents) 📂 
and their filenames start with `C-` according to the [amcr-inputs.txt](amcr-inputs.txt) 📎 
list of input files.

Examples of **output** files are saved in [translated_files](data_samples/translated_files) 📂
and include `.csv` log files (contain only processed lines) with `.xml` translated 
to the target language source files.


### ⚙️ Configuration File Support

Instead of passing all arguments via the command line, you can use a configuration 
file `config.txt` to define default paths and parameters. Console arguments take precedence 
and will override config file parameters.

Example [config.txt](config.txt):
```ini
[DEFAULT]
input_path = ./my_documents
source_lang = auto
target_lang = en
formats = xml,txt
fields = amcr-fields.txt
output = ./translated_files
```

### ⚙️ Supported Arguments

* `input_path`: Path to a single source file or a directory containing XML files.
* `--output`, `-o`: Output file path (for single file mode) or output directory (for batch mode).
* `--source_lang`, `-src`: Source language code (e.g., `cs`, `fr`). Use `auto` to auto-detect. Default is `cs`.
* `--target_lang`, `-tgt`: Target language code (e.g., `en`, `cs`). Default is `en`.
* `--formats`, `-f`: Comma-separated list of file formats to process (e.g., `alto.xml,txt`). Default is `xml`.'
* `--config`, `-c`: Path to configuration file. Settings here override console flags.
* `--alto`: Flag to enable ALTO XML in-place translation mode.
* `--xpaths`: Path to a `.txt` file containing XPaths for AMCR metadata translation.
* `--xsd`: Optional URL or local path to an XSD file for AMCR output validation.

---

## 🧠 Logic Overview

1. **Routing**: The script determines if it is running in ALTO mode (`--alto`) or AMCR mode (`--xpaths`).
2. **Extraction & Translation**:
   * **ALTO**: Iterates through `Page` -> `TextLine` -> `String`. Extracts the `CONTENT` attribute, reconstructs the entire line for contextual API translation, and perfectly redistributes the translated words back into the `CONTENT` attributes.
   * **AMCR**: Uses deep recursive namespace extraction (vital for OAI-PMH API envelopes). Finds elements matching the provided XPaths, translates their text content, and replaces it in the tree.
3. **Identification**: The text is analyzed by **FastText** [^5] to determine the source language. If the confidence score is below `0.2`, the system automatically defaults to Czech (`cs`).
4. **Translation**: Text is passed to the **LINDAT Translation API** [^1]. Texts longer than 4,000 characters are safely chunked at the nearest space to prevent mid-word cuts.
5. **Output**: Generates the translated `.xml` file preserving all original tags/namespaces, alongside a supplementary `_log.csv` file containing the line-by-line translation data for manual QA review. Optionally validates AMCR output against an XSD schema.

---

## Paradata logs

The wrapper generates a supplementary CSV log file for each processed XML file, named with the pattern `<original_filename>_log.csv`. This log contains the following columns:
- `file`: The name of the original XML file being processed.
- `page_num`: The page number (for ALTO XML) or `N/A` for AMCR metadata.
- `line_num`: The line number within the page (for ALTO XML) or type for AMCR metadata.
- `text_src`: The original text extracted for translation.
- `text_tgt`: The translated text returned by the LINDAT API.
- `translation_status`: Status of the translation (e.g., `success`, `failed`, `skipped`).

Moreover, directory [paradata](paradata) 📂 contains aggregated logs of all processed files, 
allowing for outputs' metadata of the program run to be easily accessible for further analysis and reporting.

## 🙏 Acknowledgements

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^2] 🔗

- **Developed by** UFAL [^3] 👥
- **Funded by** ATRIUM [^4]  💰
- **Shared by** ATRIUM [^4] & UFAL [^3] 🔗
- **Translation API**: LINDAT/CLARIAH-CZ Translation Service [^1] 🔗
- **Language Identification**: Facebook FastText [^5] 🔗

**©️ 2026 UFAL & ATRIUM**

[^1]: https://lindat.mff.cuni.cz/services/translation/
[^2]: https://github.com/ufal/atrium-translator
[^4]: https://atrium-research.eu/
[^5]: https://huggingface.co/facebook/fasttext-language-identification
[^3]: https://ufal.mff.cuni.cz/home-page
