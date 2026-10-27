# Outputs

For users and reviewers who read what a run produced. It answers which files a run writes, what each column and
`status` of the translation log means, and what the translator writes into the document record. The paradata is in
[paradata.md](paradata.md). The service returns the same document and provenance in its response instead of writing
files, and the same record and log when the request sends a record or asks for them (`include_record`, `include_log`,
since v1.6.0-beta; [service/README.md](../../service/README.md#response)).

| Output              | Name                                                                 | Contents                                                                                       |
|---------------------|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| Translated document | `<stem>_<target_lang><suffix>`, e.g. `MTX201501307_anon_en.alto.xml` | the same document, the same tags and coordinates; the text translated or the translation added |
| Translation log     | `<file>_log.csv`                                                     | one row per ALTO line or metadata field ([below](#translation-logs))                           |
| Document record     | `<doc_id>.document.json`, or `--document-json-out`                   | the ATRIUM record with this tool's blocks ([below](#the-document-record))                      |
| Paradata            | `paradata/YYMMDD-HHmmss_translator.json` in the output directory     | the run's provenance ([paradata.md](paradata.md))                                              |
| RO-Crate            | `ro-crate-metadata.json`                                             | only from `atrium_rocrate.py`'s own CLI, run over finished records                             |

The sample outputs in [data_samples/](../../data_samples/README.md) show each of them, in both output modes.

## Translation logs

A **per-document** CSV for every processed XML file, named `<original_filename>_log.csv` (for example
[MTX201501307_anon_log.csv](../../data_samples/in-place_translated_files/alto/MTX201501307_anon_log.csv)), written to
the output directory beside the translated XML, for line-by-line review.

| Column               | ALTO value                                            | Metadata value              |
|----------------------|-------------------------------------------------------|-----------------------------|
| `file`               | the source file name (stem)                           | the source file name (stem) |
| `page_num`           | the page index (1-based)                              | empty                       |
| `line_num`           | the `TextLine` element id                             | the full XPath              |
| `text_<source_lang>` | the original `CONTENT` text of the line               | the original element text   |
| `text_<target_lang>` | the translated text **as redistributed to that line** | the translated text         |
| `status`             | how the line's translation was obtained (below)       | the same                    |

| `status`           | Meaning                                                                                                                                                                                                                       |
|--------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `ok`               | Translated and aligned normally.                                                                                                                                                                                              |
| `rerun`            | Flagged during processing (a degenerate reply) and recovered by the end-of-document re-run.                                                                                                                                   |
| `approx_alignment` | ALTO only: the line's anchor was unusable, so its words were placed by source word count (with `--fast-align`: its block came back with fewer words than lines).                                                              |
| `untranslated`     | Still degenerate after the re-run: the **source text was kept** in the output and the target cell is empty (ALTO: also a line that needed its own line translation and had no usable one).                                    |
| `unsupported_lang` | `--source_lang auto` only: the segment was identified as a language the backend cannot translate, and was translated from the fallback language (translator#52); review it. `untranslated` outranks it; it outranks the rest. |

**ALTO.** The target column holds the tokens *aligned and redistributed* to each physical line, not a separate
translation of the line, so it shows exactly what was written into that line's `String` elements (or their
`ALTERNATIVE`s in `append` mode): the log is a faithful record of the reconstruction.

**Written once per document, in document order.** The log is written to `<doc>_log.csv.partial` and moved into
place when the document is done, so the previous log stays readable, and consistent with the previous XML, for the
whole run, and a failed or interrupted run never leaves an empty log.

The two text columns are named after the run's language codes: `text_cs` / `text_en` with `--source_lang cs`,
`text_auto` / `text_en` with `--source_lang auto`.

A metadata row looks like this (an invented record, for illustration; the real logs of the 15 sample records are in
[data_samples/in-place_translated_files/xml/](../../data_samples/in-place_translated_files/xml/)):

```text
file,page_num,line_num,text_cs,text_en,status
C-TX-000000000,,//amcr:amcr/amcr:dokument/amcr:popis,"Záchranný výzkum při stavbě rodinného domu.","Rescue excavation during the construction of a family house.",ok
C-TX-000000000,,//amcr:amcr/amcr:dokument/amcr:poznamka,"Nálezy uloženy v muzeu.",,untranslated
```

The second row is a field whose reply stayed degenerate after the re-run: the record keeps its Czech text, and the
target cell is empty.

## The document record

The per-document record this tool reads and writes, `<doc_id>.document.json`, follows the hub's
[document contract](https://ufal.github.io/atrium-project/ecosystem/document-contract/); its schema is described in
[paradata.md](paradata.md#the-record-schema). This tool writes:

```json
"translations": {
  "source_lang": "auto",
  "target_lang": "en",
  "backend": "lindat",
  "output_mode": "replace",
  "detected_source_lang": "cs",
  "unsupported_source_langs": {"la": 1, "hu": 1}
}
```

- **`translations`**: `source_lang`, `target_lang`, `backend` and `output_mode`; `detected_source_lang`, the language
  the document resolved to, only when detection ran (with `source_lang: "auto"` the literal says nothing about the
  record); and `unsupported_source_langs`, only when an `auto` run met a language it identified with confidence but
  could not translate (those segments were translated from the fallback language, and their log rows say
  `unsupported_lang`). `output_mode` is there because a consumer that finds English in a field needs to know whether
  the Czech was kept beside it or overwritten. The extra keys are allowed because `additionalProperties` is true on
  this block.
- **`derived_from.translated_xml`**: the name of the translated file. The translated **text** is not stored in the
  record.
- **The licence block** (`provenance.license`, `license_url`, `license_detail`): the components of the run
  ([licences.md](licences.md)). The backend's components are recorded before the record takes it, so a one-file run
  (one pipeline stage, one `/translate` call) states the same as a batch.

**`entities[].translation_en` is reserved:** the schema assigns it to the translator, but `entities[]` is written by
nlp-enrich, which runs after the translator, so no code path here writes it. It stays reserved
([atrium-project#70](https://github.com/ufal/atrium-project/issues/70), decided 2026-09-30): the AMČR pilot needs
English only for free-text metadata, so nothing asks for English entity names.

**The `doc_id` is inherited** from the baseline (`--document-json`) and never re-derived from the input's file name,
because in the pipeline the input is a page (`<doc>-1.alto.xml`), not the document. The record, the log's `file`
column, the default record file name and the paradata key all take the document's id; the translated file and the
log keep the per-file name. A standalone run derives the id from the file name. The record is validated before it is
written, so an invalid record is never emitted.
