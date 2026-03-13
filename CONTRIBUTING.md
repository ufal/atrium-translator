
# 🤝 Contributing to the LINDAT Translation Wrapper of the ATRIUM project

Welcome! Thank you for your interest in contributing. This repository [^2] provides a 
robust workflow for translating archival XML records (specifically ALTO XML and AMCR 
metadata) into English and other target languages. It addresses common challenges in 
digital archives, such as safely translating highly nested XMLs without breaking tags, 
namespaces, or OAI-PMH envelopes.

This document describes the project's capabilities, development workflow, code 
conventions, and rules for contributors.

---

## 🏗️ Project Contributions & Capabilities

This pipeline contributes 4 major capabilities to the data translation lifecycle, 
as detailed in the section of the main [README 🧠 Logic Overview](README.md#-logic-overview).

### 1. Dedicated Archival XML Processing

The pipeline allows archives to safely translate structured documents without altering their 
spatial coordinates or metadata schemas.

* **ALTO XML Handling:** Specifically targets and translates only the `CONTENT` 
attributes within `TextBlock` and `TextLine` elements natively.
* **AMCR Metadata Handling:** Uses deep recursive namespace extraction to parse specific 
elements based on custom XPaths and safely replace the text content.

### 2. Multi-Mode Translation Execution

Archive managers can choose processing modes based on their specific document types and workflows:

| Mode                      | Best For...               | Key Feature                                                                              |
|---------------------------|---------------------------|------------------------------------------------------------------------------------------|
| **ALTO XML Mode**         | Scanned document archives | Perfect redistribution of translated words back into exact spatial `CONTENT` attributes. |
| **AMCR Mode**             | Highly nested metadata    | Safely handles OAI-PMH envelopes and translates specific targeted XPath fields.          |
| **Batch & URL Ingestion** | Large-scale collections   | Scans entire directories or downloads/sanitizes XMLs directly from REST URLs.            |

### 3. Automated Language & Quality Controls

A core contribution of this project is minimizing manual preprocessing and providing immediate review tools:

* **Language Identification:** Source text is automatically analyzed using 
**FastText** [^5]. If the confidence score is low (< 0.2), the system safely defaults
to Czech (`cs`) to keep the pipeline moving.
* **Space-Aware Chunking:** Intelligently chunks long texts at word boundaries (max 
4,000 characters) before sending them to the translation API, preventing mid-word 
truncation errors.
* **QA Logging:** Automatically produces a supplementary CSV file (`file, page_num, 
line_num, text_src, text_tgt`) for easy line-by-line manual QA review.
* **Schema Validation:** Optionally validates AMCR outputs against an XSD schema to 
guarantee post-translation structural integrity.

### 4. Seamless API & Configuration Integration

The project includes streamlined interfaces for reproducible archival processing:

* **LINDAT Integration:** Direct connection to the LINDAT/CLARIAH-CZ Translation Service
API (v2) [^1].
* **Standardized Configs:** Support for `config.txt` to define default input paths, 
target languages, and XPath lists, ensuring consistency across different archival teams.

---

## 🌿 Branches & Environments

| Branch   | Environment          | Rule                                                                            |
|----------|----------------------|---------------------------------------------------------------------------------|
| `test`   | Staging              | Base for all development. Always branch from `test`.                            |
| `master` | Stable / Integration | Merged exclusively by a human reviewer. Do not open PRs directly into `master`. |

```text
test    ←  feature/<issue>
test    ←  bugfix/<issue>
master  ←  (humans only, after test stabilises)

```

### 🏷️ Branch Naming

| Type             | Pattern           | Example                      |
|------------------|-------------------|------------------------------|
| New feature      | `feature/<issue>` | `feature/12-amcr-validation` |
| Bug fix          | `bugfix/<issue>`  | `bugfix/18-chunk-truncation` |
| Hotfix on master | `hotfix/<issue>`  | `hotfix/99-api-timeout`      |

---

## 🔁 Contributor Workflow

1. **Create an issue** (or find an existing one) describing the problem or feature.
2. **Branch from `test`:**
```bash
git checkout test
git pull origin test
git checkout -b feature/<issue-number>
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

**Do not open PRs into `master**` — merging into `master` is exclusively the 
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

* **Formatting:** Python code should be formatted using `black` (line length 120) and `isort`.
* **Linting:** Ensure compliance with `flake8`.
* **Docstrings:** Use descriptive, concrete language avoiding generic templates.

### Minimum checks before every commit

Always run basic validation locally before pushing:

```bash
# 1. Python compilation check
python -m compileall -q .

# 2. Pre-commit hooks (runs black, isort, flake8)
pre-commit run --all-files
```

*Note: If specific translation handlers or chunking logic are updated, please run a 
smoke-test to verify XML schema integrity and ensure the spatial coordinates 
(`CONTENT` bounding boxes) remain unaltered.*

---

## 📁 Repository Documentation Management

Each documentation file has one target audience and one responsibility. Rules are not 
repeated — cross-references are used instead.

| File              | Audience        | Responsibility                                 |
|-------------------|-----------------|------------------------------------------------|
| `README.md`       | GitHub visitors | Project overview, workflow stages, quick start |
| `CONTRIBUTING.md` | Developers      | Code conventions, branches, PRs, testing       |

Rules:

1. **Do not duplicate rules** — if a rule is defined in `CONTRIBUTING.md`, other files 
reference it rather than copying it.
2. **Single canonical source** — for each type of information, exactly one canonical 
file exists (see table above).
3. **When changing a rule**, update the canonical source and verify that referencing 
files still point correctly.

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
