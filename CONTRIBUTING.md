# 🤝 Contributing to the LINDAT Translation Wrapper of the ATRIUM project

Welcome! Thank you for your interest in contributing. This repository [^2] provides a 
robust workflow for translating archival XML records (specifically ALTO XML and AMCR 
metadata) into English and other target languages. It addresses common challenges in 
digital archives, such as safely translating highly nested XMLs without breaking tags, 
namespaces, or OAI-PMH envelopes.

This document describes the project's capabilities, development workflow, code 
conventions, and rules for contributors.

## 📦 Release History

| Version    | Release Type                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Key Features & Fixes |
|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------|
| **v0.6.1** | Docker GH Actions fix applied.                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Pre-release          |
| **v0.6.0** | **Security:** Hardened XML parsing with lxml `_SECURE_PARSER` to prevent XXE. **Reliability:** Raised `TranslationError` with exponential back-off instead of logging corrupt output strings. Pinned all dependencies. **Performance:** Added `--fast-align` CLI flag to reduce ALTO API calls and `LINDAT_*` env vars for rate limiting. **Fixes:** Fixed empty-line KeyErrors, whitespace reflow issues in metadata, and improved file-saving directories for URL downloads. | Pre-release          |
| **v0.5.1** | Docker wrapper and small dependencies swap - fasttext-wheel                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release          |
| **v0.5.0** | Dual-pass ALTO reconstruction (block + line translation with similarity-based token alignment); NMT-safe vocabulary sentinels + number-agreement guard; per-run license resolution & paradata logging                                                                                                                                                                                                                                                                          | Pre-release          |
| **v0.4.1** | Pytest added for main functionality (Tests added to the repository)                                                                                                                                                                                                                                                                                                                                                                                                            | Pre-release          |
| **v0.4.0** | Vocabulary added and overall enhancement (Added examples of updated files)                                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release          |
| **v0.3.0** | AMCR samples added + documentation expanded + paradata (Added paradata of outputs logging)                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release          |
| **v0.2.1** | Draft version of ALTO/AMCR XML inputs only (Draft of AMCR is ready, Example of ALTO is included, Wrapped up with citation, license, and contribution draft)                                                                                                                                                                                                                                                                                                                    | Pre-release          |
| **v0.1.0** | Broad inputs support (and AMCR XML) (No logging of ALTO lines translation, AMCR XML support draft is included, No strict input format narrowing done yet)                                                                                                                                                                                                                                                                                                                      | Pre-release          |
| **v0.0.2** | Various input formats and focus on ALTO (No AMCR XML paths config, Broad inputs optionation like txt, pdf, xml..., Working draft version)                                                                                                                                                                                                                                                                                                                                      | Pre-release          |

> **v0.5.0 — translation logic & structure preservation (detail).** ALTO documents are
> no longer translated with a single per-block pass. Each `TextBlock` is now translated
> **twice**: once as a whole block (the high-quality tokens that are written back) and
> once line-by-line (anchors only). `_align_tokens_to_lines` then partitions the block
> tokens into one bucket per physical `TextLine` using a ±50 % sliding-window
> `difflib.SequenceMatcher` search against each line anchor, and the tokens are
> redistributed across each line's `String` `CONTENT` attributes (greedy 1-to-1, last
> `String` absorbs the remainder). This preserves the original spatial layout while
> improving translation fluency. The change is covered by `tests/test_alignment.py`
> (token conservation, bucket count, clean-signal splits, edge cases) and the existing
> dual-pass assertions in `tests/test_utils.py`.

---

## 🏗️ Project Contributions & Capabilities

This pipeline contributes 4 major capabilities to the data translation lifecycle, 
as detailed in the section of the main [README 🧠 Logic Overview](README.md#-logic-overview).

### 1. Dedicated Archival XML Processing

The pipeline allows archives to safely translate structured documents without altering their 
spatial coordinates or metadata schemas.

* **ALTO XML Handling:** Specifically targets and translates only the `CONTENT` 
attributes within `TextBlock` and `TextLine` elements natively. Reconstruction uses a
dual-pass block/line translation plus similarity-based token alignment so that the
original `String` positions are preserved (see the main
[README → ALTO Dual-Pass Reconstruction](README.md#-alto-dual-pass-reconstruction)).
* **XML Metadata Handling:** Uses deep recursive namespace extraction to parse specific 
elements based on custom XPaths and safely replace the text content. Works with any
well-formed XML (AMCR/OAI-PMH or custom schemas).

### 2. Multi-Mode Translation Execution

Archive managers can choose processing modes based on their specific document types and workflows:

| Mode                      | Best For...               | Key Feature                                                                                                              |
|---------------------------|---------------------------|--------------------------------------------------------------------------------------------------------------------------|
| **ALTO XML Mode**         | Scanned document archives | Dual-pass translation + token alignment redistributes translated words back into the exact spatial `CONTENT` attributes. |
| **XML Metadata Mode**     | Highly nested metadata    | Safely handles OAI-PMH envelopes and translates specific targeted XPath fields in any well-formed XML.                   |
| **Batch & URL Ingestion** | Large-scale collections   | Scans entire directories or downloads/sanitizes XMLs directly from REST URLs.                                            |

### 3. Automated Language & Quality Controls

A core contribution of this project is minimizing manual preprocessing and providing immediate review tools:

* **Language Identification:** Source text is automatically analyzed using 
**FastText** [^5]. If the confidence score is low (< 0.2), the system safely defaults
to Czech (`cs`) to keep the pipeline moving. In ALTO mode, detection runs once per
`TextBlock` so every line in a block shares a consistent source language.
* **Sentence-Aware Chunking:** Long texts are split at the highest-priority boundary found
in each window, tried in strict order — newline (`\n`) → sentence-terminal punctuation
(`. `, `! `, `? `) → clause-level punctuation (`; `, `, `) → word boundary — before being sent
to the translation API. Keeping whole sentences together preserves NMT context; the word
boundary is a fallback and a hard cut is the last resort, so mid-word truncation never occurs.
The same shared chunker (`processors/chunking.py`) feeds the UDPipe lemmatiser.
* **QA Logging:** Automatically produces a supplementary CSV file (`file, page_num, 
line_num, text_src, text_tgt`) for easy line-by-line manual QA review.
* **Schema Validation:** Optionally validates metadata outputs against an XSD schema to 
guarantee post-translation structural integrity.

### 4. Seamless API & Configuration Integration

The project includes streamlined interfaces for reproducible archival processing:

* **LINDAT Integration:** Direct connection to the LINDAT/CLARIAH-CZ Translation Service
API (v2) [^1].
* **Standardized Configs:** Support for `config.txt` to define default input paths, 
target languages, and XPath lists, ensuring consistency across different archival teams.

> **Future work:** the LINDAT translation backend may eventually be supplemented or
> replaced by an alternative open-source / locally hosted NMT model. This is **not** in
> scope for the current contribution and is recorded here only as a planned direction.

---

## 🌿 Branches & Environments

| Branch   | Environment          | Rule                                                                            |
|----------|----------------------|---------------------------------------------------------------------------------|
| `test`   | Staging              | Base for all development. Always branch from `test`.                            |
| `master` | Stable / Integration | Merged exclusively by a human reviewer. Do not open PRs directly into `master`. |

```text
test    ←  feature-<name>
test    ←  bugfix-<name>
master  ←  (humans only, after test stabilises)

```

### 🏷️ Branch Naming

| Type             | Pattern          | Example                   |
|------------------|------------------|---------------------------|
| New feature      | `feature-<name>` | `feature-amcr-validation` |
| Bug fix          | `bugfix-<name>`  | `bugfix-chunk-truncation` |
| Hotfix on master | `hotfix-<name>`  | `hotfix-api-timeout`      |

---

## 🔁 Contributor Workflow

1. **Create an issue** (or find an existing one) describing the problem or feature.
2. **Branch from `test`:**
```bash
git checkout test
git pull origin test
git checkout -b feature-<name>
```
3. **Implement your changes** observing the project's code conventions.
4. **Run the minimum tests** (see the Testing section).
5. **Open a Pull Request** targeting the `test` branch.

---

## 📋 Pull Request Format

Every PR must include:

* **Issue link:** `Closes #<number>` or `Refs #<number>`
* **Motivation:** why the change is needed
* **Description of change:** what was changed and how
* **Testing:** what was run, what passed, what could not be executed

Use a **Draft PR** if the work is not ready for review.

**Do not open PRs into `master` — merging into `master` is exclusively the 
maintainers' responsibility.

> **Note on issue tracking:** Issues reference the commits and PRs that resolved 
> them — not the other way around. Commit messages describe *what changed*; the issue 
> is the place to record *why* and link the resulting commits together.

---

## ✏️ Commit Messages

Format:

```text
[type] concise description of what changed
```

Allowed types:

| Type       | When to use                           |
|------------|---------------------------------------|
| `add`      | Added content (general)               |
| `edit`     | Edited existing content (general)     |
| `remove`   | Removed existing content (general)    |
| `fix`      | Bug fix                               |
| `refactor` | Refactoring without behaviour change  |
| `test`     | Adding or updating tests              |
| `docs`     | Documentation only                    |
| `chore`    | Build, dependencies, CI configuration |
| `style`    | Formatting, no logic change           |
| `perf`     | Performance optimisation              |

---

## 🧪 Code Conventions & Testing


### Code Conventions

* **Comments:** informative but short, may be LLM-generated, added when function name does 
not explain its functionality in detail
* **Argument types:** set default type (e.g., `int`, `list`) for function arguments
* **Console flags:** when a new one added, provide help message for it
* **Config files:** when set of variables changes it should be reflected in repository documentation
* **Generated code:** always should be manually launched and checked for mistakes before pushing

### Minimum checks before every commit

Always run basic validation locally before pushing:

```bash
# 1. Python compilation check
python -m compileall -q .

# 2. Pre-commit hooks (runs black, isort, flake8, etc.)
pre-commit run --all-files

```

> [!NOTE]
>  If specific scripts or extraction modules are updated, please run a smoke-test 
> against the `data_samples/` directory to verify extraction integrity.

---


### Running the test suite

The repository ships a lightweight `pytest` harness that requires **no ML models or GPU**
for standard unit tests. Heavy tests that do require models or network access are marked
`slow` and are excluded from the default run.

```bash
pip install -r requirements-test.txt  # pytest>=8.0 and pytest-cov only
```

```bash
pytest -m "not slow" --tb=short                              # fast — use before every commit
pytest --tb=short                                            # full suite (requires model setup)
pytest -m "not slow" --cov=. --cov-report=term-missing      # with coverage
```

`tests/test_paradata.py` (`ParadataLogger`, `_sanitise`) is shared across all repos.
Repo-specific modules and GPU-heavy tests are marked `@pytest.mark.slow` and skipped by default.

<details>
<summary>Test layout, per-repo targets, and fixture conventions</summary>

```text
tests/
├── __init__.py              # empty
├── conftest.py              # shared fixtures (tmp_path wrappers, sample data loaders)
├── fixtures/                # small static test-data files committed to the repo
└── test_<module>.py         # repo-specific unit tests
```

**Per-repo targets:**

| Repository                | Test file            | Primary targets                                                                                                                                                      |
|---------------------------|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `atrium-nlp-enrich`       | `test_keywords.py`   | `_extract_surface_text`, `_extract_lemmas`, `_extract_legacy`, `extract_keywords`, `_sort_csv_file`                                                                  |
| `atrium-alto-postprocess` | `test_text_util.py`  | Density/ratio helpers, detectors, `pre_filter_line`, `parse_line_splits`, `categorize_line` (ppl passed directly, no GPU), `compute_quality_score`                   |
| `atrium-alto-postprocess` | `test_utils.py`      | `directory_scraper`, `dataframe_results` (Top-1 and Top-N), `collect_images`                                                                                         |
| `atrium-translator`       | `test_utils.py`      | `_resolve_namespaces`, `validate_xml_with_xsd`, `process_alto_xml` (incl. dual-pass call counts & redistribution), `process_metadata_xml` (mock translator injected) |
| `atrium-translator`       | `test_alignment.py`  | `_align_tokens_to_lines` — token conservation, bucket-per-line count, clean-signal splits, empty-block / single-line / empty-anchor edge cases                       |
| `atrium-translator`       | `test_translator.py` | `_chunk_text`, `_restore_tags`, `_load_vocabulary`, `_translate_with_vocabulary`, number-agreement guard, boundary-priority chunking                                 |
| `atrium-translator`       | `test_lemmatizer.py` | `_parse_conllu` (and `_parse_conllu_with_features`), shared `_chunk_text` delegation                                                                                 |

**Slow tests** — any test loading a model checkpoint, calling an external API, or requiring a GPU must be decorated with `@pytest.mark.slow`. Document in the PR description which resource it requires and how to enable it locally.

**Fixtures** — small, self-contained files committed under `tests/fixtures/`. Tests must not read from `data_samples/` directly. Add a minimal fixture file in the same commit as any test that needs new sample data.

</details>


We have transitioned from `black`/`isort`/`flake8` to **Ruff** for all linting and formatting, matching the 
overarching ATRIUM standard.

1. **Linting:** Run `ruff check .` locally before opening a pull request. The CI environment utilizes the shared `ruff.toml` template.
2. **Testing:** Our target is full structural test coverage. Execute tests using:
   ```bash
   pytest -m "not slow" --cov=. --cov-report=term-missing
    ```
   
> [!NOTE]: Network-dependent tests (e.g., LINDAT endpoint interactions) and heavy ML model downloads (e.g., 
> FastText weights) are marked @slow.

---


## 📁 Repository Documentation Management

Each documentation file has one target audience and one responsibility. Rules are not repeated — cross-references are used instead.

| File              | Audience        | Responsibility                                 |
|-------------------|-----------------|------------------------------------------------|
| `README.md`       | GitHub visitors | Project overview, workflow stages, quick start |
| `CONTRIBUTING.md` | Developers      | Code conventions, branches, PRs, testing       |

* **Do not duplicate rules:** if a rule is defined in `CONTRIBUTING.md`, other files 
reference it rather than copying it.
* **When changing a rule:** update the canonical source and verify that referencing files
still point correctly.

---

## 📞 Contacts & Acknowledgements


For support or specific archival integration questions, contact **lutsai.k@gmail.com** responsible for this GitHub repository [^2].

* **Developed by:** UFAL [^3]
* **Funded by:** ATRIUM [^4]
* **APIs & Models:** 
  * LINDAT/CLARIAH-CZ Translation Service [^1]
  * Facebook's FastText model [^5]

**©️ 2026 UFAL & ATRIUM**


[^1]: https://lindat.mff.cuni.cz/services/translation/
[^2]: https://github.com/ARUP-CAS/atrium-translator
[^3]: https://ufal.mff.cuni.cz/home-page
[^4]: https://atrium-research.eu/
[^5]: https://huggingface.co/facebook/fasttext-language-identification