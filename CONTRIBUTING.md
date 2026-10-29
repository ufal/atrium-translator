# Contributing to the LINDAT Translation Wrapper of the ATRIUM project

Welcome! Thank you for your interest in contributing. This repository [^2] provides a
robust workflow for translating archival XML records (specifically ALTO XML and AMCR
metadata) into English and other target languages. It addresses common challenges in
digital archives, such as safely translating highly nested XMLs without breaking tags,
namespaces, or OAI-PMH envelopes.

This document describes the project's capabilities, development workflow, code
conventions, and rules for contributors.

## Release History

| Version         | Key Features & Fixes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Release Type |
|-----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------|
| **v1.7.0-beta** | **One behaviour from either entry point** (atrium-project#72). The service's paradata now records the run's counts as the CLI's does (`xsd_validation`, `vocabulary_protected_terms`, `lindat_degenerate_replies`, one helper for both); the CLI records the backend's language set, counts the record it writes, and finds `para_config.txt` beside `main.py` (run from another folder, it recorded version `unknown` and UNKNOWN licences). A query boolean the service cannot read is a 422, as the form field is (`?is_alto=ture` meant ALTO). Docs: one parity table in `docs/usage.md`; `formats` in config.txt picks files, not the mode.                                                                                                                                                                                                                                                                                                                                                               | Pre-release  |
| **v1.6.1-beta** | The release of v1.6.0-beta (atrium-project#72). v1.6.0-beta published its images but no GitHub release: the release job installed the bundle with the runner's own Python, 3.14 by then, for which `fasttext-wheel` 0.9.2 has no wheel. The job now sets up the images' Python, 3.11, first, and `tests/test_release_bundle.py` fails an install placed before it. No change to the code or the API.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Pre-release  |
| **v1.6.0-beta** | **The command line and the service read and return alike** (atrium-project#72). One function decides ALTO or metadata by the root element (`utils.xml_kind`): `/translate` uses it when `is_alto` is not sent (unset meant ALTO, so a metadata record came back untranslated), the CLI when no `--alto`/`--xpaths`/`--formats` is given; a named mode wins, with a warning. One file-naming function and one paradata builder serve both, so both record the same keys. **Service, opt-in:** `fast_align`, `include_record`, `include_log`, and XSD validation with `XSD_PATH` and `xsd_strict`; `document_json_schema_error`; 422 `malformed_xml` and `xsd_validation_failed`. **CLI:** `-o page.xml` with one input names the file; a `.txt` in a folder is a URL list; in a batch, record paths are directories. `tests/test_cli_service_parity.py`.                                                                                                                                                        | Pre-release  |
| **v1.5.0-beta** | The translation bake-off (#4): `eval/bakeoff.py` compares backends by label and by kind of text, with speed and a `meta.json`; its 19 October result (`eval/results/issue4_bakeoff_2610/`) keeps LINDAT as the default and makes Qwen3.8-27B, self-hosted through `openai_compatible`, the alternative (`qwen3_8` licence component, Apache-2.0). The vocabulary is atrium-keyword-extract's curated list (4,715 pairs) with descriptions, which the LLM glossary can carry (`LLM_GLOSSARY_DESCRIPTION_CHARS`, off by default); `/translate` applies one when `VOCABULARY_PATH` is set (`/info` `vocabulary`). A language pair the backend cannot translate is refused (CLI exit 1, HTTP 422 `unsupported_language_pair`) instead of being translated from Czech. `ct2` and the LLM backend check at start-up that they can run and name the fix; a provider's refusal is quoted; the LLM glossary is one system message. `LLM_EXTRA_BODY`. The AMČR sample folders regenerated. Shared modules of 13 October. | Pre-release  |
| **v1.4.0-beta** | A source language identified with confidence but not translatable is reported: `_log.csv` status `unsupported_lang`, record field `translations.unsupported_source_langs` (#52). `load_vocab.py --from-release` reads atrium-keyword-extract's vocabulary asset. Mixed Latin/Cyrillic words count as degenerate, and `ct2` re-runs widen the beam (#4). Shared modules of 9 October.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Pre-release  |
| **v1.3.2-beta** | The service returns each call's `CreateAction` as `paradata` (atrium-project#71 R2); shared-module rounds of 1–5 October (seed profile, `sha512`, `run_uuid`, RO-Crate 1.2). No translation change.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Pre-release  |
| **v1.3.1-beta** | `entities[].translation_en` reserved in the shared document schema; production-image declaration; release gate accepts declared service renames. No behaviour change.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Pre-release  |
| **v1.3.0-beta** | **12-Factor/API hardening + OpenAPI release contract.** Centralized environment limits, standardized health/readiness and graceful shutdown, reproducible release bundles, versioned OpenAPI assets with breaking-change checks, and expanded service/release verification tests.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Pre-release  |
| **v1.2.2-beta** | **AMCR run fixes, CC0 vocabularies, limits (#46, hub #6, #53, #70).** `--xpaths` without `--formats` is a metadata run (the shipped `formats = alto.xml` made it exit 2); the CLI logs the `--xsd` verdict again; AMCR/TEATER vocabularies CC0; every limit an environment setting reported in `/info` and `limits_applied`, incl. the new `UDPIPE_TIMEOUT_S` — **breaking:** a malformed limit stops startup, and `/translate` errors are `{status, reason, detail[, errors\|limit]}` (422 `detail` is a string); `ct2` EuroLLM prompt, echo guard, stop tokens, decode budget, NMT `</s>`, model in paradata; `entities[].translation_en` reserved.                                                                                                                                                                                                                                                                                                                                                          | Pre-release  |
| **v1.2.1-beta** | **Licence in every record; `--xsd` works on AMCR.** Document records state the run's licence (one-file runs said CC BY-NC 4.0); `--xsd` resolves the AMCR schema's imports and validates the record inside its OAI-PMH envelope — AMCR 2.2 accepts `replace` output and rejects `append` (now warned); API image name corrected to `atrium-translator-api:<version>`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Pre-release  |
| **v1.2.0-beta** | **LINDAT output guard, ALTO alignment restored ([#46](https://github.com/ufal/atrium-translator/issues/46)).** Garbage replies are re-requested, re-run at the end of the document or kept as source; line alignment is checked against each source line; ALTO append keeps the source text and adds the translation beside each word; the CSV log gains `status` and is never left empty; unclear `auto` languages fall back to `cs`; `ct2` backend enabled. Also: vocabulary provenance columns (`source`, `source_id`, `uri`); a metadata-mode directory scan leaves `*.alto.xml` out; log rows in document order.                                                                                                                                                                                                                                                                                                                                                                                          | Pre-release  |
| **v1.1.0-beta** | **The replace/append switch.** `--output-mode replace\|append` — also `config.txt`, `OUTPUT_MODE` and a `/translate` field — decides whether the source-language field is overwritten or kept beside an `xml:lang`-marked sibling, the shape AMCR's own thesaurus uses for `heslo`/`heslo_en`. ALTO labels rather than duplicates: per-`String` append would multiply a fabricated word-to-box alignment. `replace` stays the default, byte-identical across all 16 shipped samples. Fixes: `/translate` answered HTTP 200 with metadata untranslated (a hard-coded empty XPath list, now loaded from `AMCR_FIELDS_PATH`, with 422 when unconfigured); request parameters were read from the query string while callers sent them in the body, and both are now accepted; the page-batch fallback degraded ~20× in call count behind a silent `except`, and is now counted and logged.                                                                                                                         | Pre-release  |
| **v1.0.0-beta** | **Pre-production hardening.** Configured retry limits, upload bounds and CLI exit codes now take effect instead of being announced; a degraded model load is reported, not silent; the release zip runs; `.env` reaches the container; CI runs the 3.11 that ships; `/translate` covered end to end. **Breaking:** the batch CLI exits non-zero on failure.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release  |
| **v0.10.5**     | Re-vendored `atrium_document.py` (`set_source()` fills sub-keys absent from a partial first write, so §1a's deferred origin checks resolve). `.coveragerc` stops omitting `service/*` — where the `backend`-less `/translate` HTTP 500 lived — with the floor held at **81** against a re-measured 82.87%. `scheduled-smoke.yml` now states what it actually validates: it re-runs the same hermetic suite as the PR lane, so its signal is fresh-install dependency drift rather than integration coverage — the live-backend gap that let that 500 ship is still open — and `-rs` makes skips visible. Concurrency scoped by `github.event_name`; `release.yml` declares one. Bumps: httpx2, huggingface-hub.                                                                                                                                                                                                                                                                                                | Pre-release  |
| **v0.10.4**     | **`doc_id` is inherited, never re-derived.** Input is never the original document: alto-postprocess hands it `PAGE_ALTO/<doc>/<doc>-1.alto.xml`, `record_doc_id()` now takes the key from the `--document-json` baseline and falls back to `canonical_doc_id()` only for a standalone run. Per-file outputs keep the per-file name while the record, the log's `file` column, the default record filename and the paradata key all take the document's id; `service/api.py` derives the record filename it returns the same way. Re-vendored `atrium_document.py`, `tests/test_document_originators.py` -> canonical shared set, and dependency bumps.                                                                                                                                                                                                                                                                                                                                                         | Pre-release  |
| **v0.10.3**     | End-to-end GHA pipeline for JSON input-output by `atrium_document` standard is refined, and tested for the draft JSON schema design.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Pre-release  |
| **v0.10.2**     | Major GHA update with references to `@v1` on the hub repo.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release  |
| **v0.10.1**     | GHA: Exercises the release path end to end: version guard via the shared check_version.py, post-publish Trivy digest scan + SARIF, and buildkit SBOM/provenance attestations. No functional changes to the tool itself.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Pre-release  |
| **v0.10.0**     | Added draft of `atrium_document` integrated into pipeline of data processing here. Refreshed paradata template.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Pre-release  |
| **v0.9.0**      | Updated and bumped dependencies versions, edited GHA release workflow. Standartazied API service to the OpenAPI rules - aligned with `agent-skill` branch service as well.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release  |
| **v0.8.1**      | Added version reading and license tests (cross-repository template). Dependency versions updated. LLM review performed with Fable. Added new tests and fixed existing ones.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release  |
| **v0.8.0**      | Added agent_dev_log directory with issue logs and their digests+plans. Updated the paradata-related scripts with the atrium-project template. Updated processing to per-page call instead of per-block calls for ALTO XML inputs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Pre-release  |
| **v0.7.0**      | Multi-option backend finalized in theory (not tested in practice), added tests, and Docker GHA was updated.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release  |
| **v0.6.2**      | Multi-option backend translation model (draft) and Docker GH Actions alignment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Pre-release  |
| **v0.6.1**      | Next round LLM review edits and Docker GH Actions alignment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Pre-release  |
| **v0.6.0**      | **Security:** Hardened XML parsing with lxml `_SECURE_PARSER` to prevent XXE. **Reliability:** Raised `TranslationError` with exponential back-off instead of logging corrupt output strings. Pinned all dependencies. **Performance:** Added `--fast-align` CLI flag to reduce ALTO API calls and `LINDAT_*` env vars for rate limiting. **Fixes:** Fixed empty-line KeyErrors, whitespace reflow issues in metadata, and improved file-saving directories for URL downloads.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Pre-release  |
| **v0.5.1**      | Docker wrapper and small dependencies swap - fasttext-wheel                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release  |
| **v0.5.0**      | Dual-pass ALTO reconstruction (block + line translation with similarity-based token alignment); NMT-safe vocabulary sentinels + number-agreement guard; per-run license resolution & paradata logging                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Pre-release  |
| **v0.4.1**      | Pytest added for main functionality (Tests added to the repository)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Pre-release  |
| **v0.4.0**      | Vocabulary added and overall enhancement (Added examples of updated files)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release  |
| **v0.3.0**      | AMCR samples added + documentation expanded + paradata (Added paradata of outputs logging)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Pre-release  |
| **v0.2.1**      | Draft version of ALTO/AMCR XML inputs only (Draft of AMCR is ready, Example of ALTO is included, Wrapped up with citation, license, and contribution draft)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Pre-release  |
| **v0.1.0**      | Broad inputs support (and AMCR XML) (No logging of ALTO lines translation, AMCR XML support draft is included, No strict input format narrowing done yet)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Pre-release  |
| **v0.0.2**      | Various input formats and focus on ALTO (No AMCR XML paths config, Broad inputs optionation like txt, pdf, xml..., Working draft version)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Pre-release  |

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

Releases are numbered as pre-releases, `0.x` and then `-beta`. From v0.10.1 on, each release publishes its container
images through the hub's release gate. Dates are `date-released` in `CITATION.cff`; before v0.8.1 that field was not updated, so the date is that of the
commit that set the version. v1.0.0-beta was blocked by the release gate and published by digest only
([the base image that blocked a release](docs/decisions/history.md#the-base-image-that-blocked-a-release)); v1.5.0-beta
is the first immutable release.

### The releases in arcs

1. **Getting XML in and out intact** (v0.0.2 to v0.4.1). The early releases narrow the problem: v0.0.2 accepts almost
   anything (txt, pdf, xml), v0.1.0 adds a draft AMČR path without line-level logging, and v0.2.1 is the first to say
   what the tool is *for*: ALTO and AMČR XML only. v0.3.0 adds paradata, v0.4.0 the controlled vocabulary, v0.4.1 the
   first tests.
2. **Making ALTO output structurally honest** (v0.5.0 to v0.8.0). v0.5.0 is the technical centre of the project: the
   dual-pass reconstruction, with placeholder-safe vocabulary terms and the number-agreement guard. v0.6.0 hardens it
   (XXE-proof parsing, `TranslationError` with back-off instead of logging corrupt output, pinned dependencies,
   `--fast-align`); v0.8.0 moves ALTO from per-block to per-page requests, about twenty times fewer.
3. **Backends** (v0.6.2 to v0.7.0, and issue #4). A pluggable `TranslationBackend` interface replaces the hard-coded
   LINDAT call; v0.7.0 calls it "finalized in theory (not tested in practice)": the architecture first, the choice of
   model left to a measurement harness, `eval/bakeoff.py`.
4. **Production readiness** (v0.9.0 to v1.1.0-beta). OpenAPI conformance, shared version and licence tests, the
   `atrium_document` integration, the hub's reusable workflows at `@v1`; then v1.0.0-beta makes five announced but
   inert contracts real, and v1.1.0-beta adds the replace/append switch and fixes three defects only real use found.
5. **Trusting the output** (v1.2.0-beta to v1.2.1-beta). The first sample refresh against the live service found
   replies that were not translations, an aligner that trusted them, and an ALTO `append` that lost the source;
   v1.2.0-beta checks every reply, re-runs or keeps as source what fails, keeps the scanned text in ALTO `append`,
   makes `auto` fall back instead of guessing, and gives the log a `status`. v1.2.1-beta makes each record list the
   run's components, makes `--xsd` work on AMČR records (AMČR 2.2 accepts `replace`, not `append`), and corrects the
   API image name.
6. **Production standards for the pilot** (v1.2.2-beta to v1.4.0-beta). The AMČR run's command line, CC0
   vocabularies, every limit a setting in `/info` with one error shape; the shared service standards and record;
   untranslatable languages reported, the vocabulary read from keyword-extract's release, mixed-script words caught.
7. **Measured backends** (v1.5.0-beta). The bake-off keeps LINDAT as the default and makes a self-hosted Qwen3.8-27B
   the alternative; language pairs a backend cannot translate are refused.

Why each step was taken: [docs/decisions/history.md](docs/decisions/history.md).

---

## Project Contributions & Capabilities

This pipeline contributes 4 major capabilities to the data translation lifecycle,
as detailed in [How the translator works](docs/how-it-works.md#the-processing-flow).

### 1. Dedicated Archival XML Processing

The pipeline allows archives to safely translate structured documents without altering their
spatial coordinates or metadata schemas.

* **ALTO XML Handling:** Specifically targets and translates only the `CONTENT`
attributes within `TextBlock` and `TextLine` elements natively. Reconstruction uses a
dual-pass block/line translation plus similarity-based token alignment so that the
original `String` positions are preserved (see the main
[ALTO dual-pass reconstruction](docs/how-it-works.md#alto-dual-pass-reconstruction)).
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

* **Language Identification:** With `--source_lang auto`, source text is analyzed using
**FastText** [^5] once per ALTO `TextBlock` (so every line in a block shares one source
language) or metadata field, plus once for the whole document. A guess is used only if
the text is long enough, the score high enough and the language one the backend can
translate; otherwise the element's own label (`LANG` / `xml:lang`), then the document's
language, then the default source language (`cs`) apply — so an exotic guess on a short
OCR fragment never becomes a source language (`processors/language.py`).
* **Sentence-Aware Chunking:** Long texts are split at the highest-priority boundary found
in each window, tried in strict order — newline (`\n`) → sentence-terminal punctuation
(`. `, `! `, `? `) → clause-level punctuation (`; `, `, `) → word boundary — before being sent
to the translation API. Keeping whole sentences together preserves NMT context; the word
boundary is a fallback and a hard cut is the last resort, so mid-word truncation never occurs.
The same shared chunker (`processors/chunking.py`) feeds the UDPipe lemmatiser.
* **QA Logging:** Automatically produces a supplementary CSV file (`file, page_num,
line_num, text_src, text_tgt, status`) for easy line-by-line manual QA review; `status`
marks lines recovered by the end-of-document re-run, placed by word count, or left
untranslated because the backend kept returning degenerate output.
* **Schema Validation:** Optionally validates metadata outputs against an XSD schema to
guarantee post-translation structural integrity.

### 4. Seamless API & Configuration Integration

The project includes streamlined interfaces for reproducible archival processing:

* **LINDAT Integration:** Direct connection to the LINDAT/CLARIAH-CZ Translation Service
API (v2) [^1].
* **Standardized Configs:** Support for `config.txt` to define default input paths,
target languages, and XPath lists, ensuring consistency across different archival teams.

> **Backends:** LINDAT CUBBITT is the default. An OpenAI-compatible LLM API
> (`--backend openai_compatible`) and a self-hosted CTranslate2 model (`--backend ct2`:
> EuroLLM, MADLAD-400, NLLB-200 or Opus-MT) ship as alternatives. The bake-off of issue #4
> (`eval/bakeoff.py`, 2026-10-19) kept LINDAT as the default and made Qwen3.8-27B, self-hosted
> through `openai_compatible`, the alternative ([the decision](docs/decisions/backend-choice.md)).

---

## Branches & Environments

| Branch   | Environment          | Rule                                                                            |
|----------|----------------------|---------------------------------------------------------------------------------|
| `test`   | Staging              | Base for all development. Always branch from `test`.                            |
| `master` | Stable / Integration | Merged exclusively by a human reviewer. Do not open PRs directly into `master`. |

```text
test    ←  feature-<name>
test    ←  bugfix-<name>
master  ←  (humans only, after test stabilises)

```

### Branch Naming

| Type             | Pattern          | Example                   |
|------------------|------------------|---------------------------|
| New feature      | `feature-<name>` | `feature-amcr-validation` |
| Bug fix          | `bugfix-<name>`  | `bugfix-chunk-truncation` |
| Hotfix on master | `hotfix-<name>`  | `hotfix-api-timeout`      |

---

## Contributor Workflow

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

## Pull Request Format

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

## Commit Messages

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

## Code Conventions & Testing


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

# 2. Pre-commit hooks (whitespace, YAML, ruff check + ruff format)
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


## Repository Documentation Management

Each documentation file has one target audience and one responsibility. Rules are not repeated — cross-references are used instead.

| File                    | Audience               | Responsibility                                                      |
|-------------------------|------------------------|---------------------------------------------------------------------|
| `README.md`             | GitHub visitors        | What it does, inputs and outputs, quick start, limits, the docs map |
| `docs/usage.md`         | Users, integrators     | Setup, ALTO and metadata runs, output modes, vocabulary, Docker     |
| `service/README.md`     | Deployers, integrators | The service runbook: endpoints, errors, limits, configuration       |
| `docs/configuration.md` | Users, deployers       | config.txt, precedence, environment variables                       |
| `docs/how-it-works.md`  | Anyone                 | Languages, the guard, ALTO reconstruction, vocabulary, backends     |
| `docs/reference/`       | Developers, experts    | CLI, outputs, paradata and record schema, vocabulary, licences      |
| `docs/evaluation/`      | Anyone judging results | The backend bake-off, LINDAT's replies                              |
| `docs/decisions/`       | Contributors           | Dated decisions and the history                                     |
| `docs/development.md`   | Developers             | Checks, project structure, code map, the backend interface          |
| `CONTRIBUTING.md`       | Developers             | Code conventions, branches, PRs, testing, the release history       |

The documentation follows the ATRIUM
[documentation style](https://github.com/ufal/atrium-project/blob/test/docs/documentation-style.md); check links and
anchors with `python3 ../atrium-project/tools/docs_check.py --repo atrium-translator`; Workflow Policy
Lint runs the same check on every push. What each CI check proves, and the checklist for a new one:
[`docs/ci_policy.md`](https://github.com/ufal/atrium-project/blob/test/docs/ci_policy.md#changing-ci).

* **Do not duplicate rules:** if a rule is defined in `CONTRIBUTING.md`, other files
reference it rather than copying it.
* **When changing a rule:** update the canonical source and verify that referencing files
still point correctly.

---

## Contacts & Acknowledgements


For support or specific archival integration questions, contact **lutsai.k@gmail.com** responsible for this GitHub repository [^2].

* **Developed by:** UFAL [^3]
* **Funded by:** ATRIUM [^4]
* **APIs & Models:**
  * LINDAT/CLARIAH-CZ Translation Service [^1]
  * Facebook's FastText model [^5]

With the support of the LINDAT/CLARIAH-CZ research infrastructure and the AIS CR infrastructure (LM2023031).

**© 2026 UFAL & ATRIUM**


[^1]: https://lindat.mff.cuni.cz/services/translation/
[^2]: https://github.com/ufal/atrium-translator
[^3]: https://ufal.mff.cuni.cz/home-page
[^4]: https://atrium-research.eu/
[^5]: https://huggingface.co/facebook/fasttext-language-identification
