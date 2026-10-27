# Paradata and the record schema

For integrators and auditors who read a run's provenance. It answers where the paradata record is written, what
each field means, and which schema the document record follows. What the records list about licences, and what the
`license` field does not mean: [licences.md](licences.md).

## The paradata record

The batch CLI writes a **run-level** JSON provenance record after every run, named `YYMMDD-HHmmss_translator.json`,
into `paradata/` in the run's output directory, beside the translated files. The `paradata/` folders under
[data_samples/](../../data_samples/README.md) hold the records of the sample runs. The `/translate` service writes no
such file: it returns each call's run as a Process Run Crate `CreateAction`, the response's `paradata`
(atrium-project#71, [service/README.md](../../service/README.md#response)).

Paradata is separate from the translation logs: a log records what was translated, line by line; the paradata
records *how the run was configured, what it used and what it produced* in aggregate.

Where one input passes through several tools, the per-tool records can be fused into one per input file with
`merge_paradata_files()`; the merged record lists the union of the components every tool used.

The logger is [atrium_paradata.py](../../atrium_paradata.py), shared across the ATRIUM tools, which reads this
repository's [para_config.txt](../../para_config.txt) for the tool version and the component table, and computes the
licence summary with [para_licenses.py](../../para_licenses.py). Every field of the shared format is in the hub's
[paradata schema](https://github.com/ufal/atrium-project/blob/test/docs/paradata_schema.md).

### The fields

| Key                                 | Description                                                                                                                                                                                                                  |
|-------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `schema_version`                    | the paradata schema version, `"2.0"`                                                                                                                                                                                         |
| `program`                           | always `"translator"`                                                                                                                                                                                                        |
| `tool_version`                      | the tool's version tag, from `para_config.txt` (e.g. `v1.6.0-beta`)                                                                                                                                                          |
| `repository`                        | the runner repository (`ATRIUM_RUNNER_REPO` when set)                                                                                                                                                                        |
| `runner_ref`                        | the git ref the running container was built from (`ATRIUM_RUNNER_REF`)                                                                                                                                                       |
| `docker_image`                      | the running image (`ATRIUM_RUNNER_IMAGE`); empty if unset                                                                                                                                                                    |
| `python_version`                    | the Python that ran                                                                                                                                                                                                          |
| `run_id`                            | the timestamp identifier of the run, also the file's name                                                                                                                                                                    |
| `run_uuid`                          | `urn:uuid:…`, minted when the run starts; it also stamps every block the run writes into a record                                                                                                                            |
| `run_agent`                         | the operating organisation's IRI (`ATRIUM_RUN_AGENT`), or empty                                                                                                                                                              |
| `license`, `license_url`            | a **calculated summary** of the components the run used: the most restrictive of their licences. It is not the licence of the output ([licences.md](licences.md))                                                            |
| `license_detail`                    | the components with their licences (`components`), how the summary was computed (`determined_by`, `is_non_commercial`, `is_share_alike`, `unknown_licenses`) and a `notes` sentence                                          |
| `start_time`, `end_time`            | ISO 8601 UTC timestamps                                                                                                                                                                                                      |
| `duration_seconds`                  | wall-clock run time                                                                                                                                                                                                          |
| `config`                            | the run's settings (below)                                                                                                                                                                                                   |
| `statistics.input_files_total`      | the number of input files                                                                                                                                                                                                    |
| `statistics.successfully_processed` | the number of files that produced output                                                                                                                                                                                     |
| `statistics.skipped_files`          | the number of files skipped after an error                                                                                                                                                                                   |
| `statistics.output_counts_by_type`  | file counts per output type (`xml`, `csv`, `json`)                                                                                                                                                                           |
| `statistics.performance_per_minute` | files produced per minute, per output type                                                                                                                                                                                   |
| `skipped_files_detail`              | `{file, reason, timestamp}` for every skipped file                                                                                                                                                                           |
| `limits_applied`                    | every limit that shaped a result without refusing it (`limit`, `effect`, `value`, count, detail), e.g. a segment split at `TRANSLATION_CHUNK_CHARS`, fields kept in the source language after the re-run (atrium-project#53) |

`config` holds the run's settings (`input_path`, `output_dir`, `source_lang`, `target_lang`, `formats`, `mode`,
`output_mode`, `translation_backend`, `xpaths_file`, `xsd_url`, `vocabulary`, `chunk_limit`), the language-ID model
and the translation endpoint actually resolved (`lang_id_model`, `translation_api`), the source-language policy
(`default_source_lang`, `fasttext_confidence_threshold`, `lang_id_min_letters`, `lang_id_languages`), and the counts
of the run: `vocabulary_protected_terms` per document and its `_total`, `lindat_degenerate_replies` per document and
its `_total`, and, with `--xsd`, `xsd_validation` (`valid`, `invalid` doc ids, `strict`).

### An example

Abridged, from a metadata run with LINDAT and the vocabulary:

```json
{
  "schema_version": "2.0",
  "program": "translator",
  "tool_version": "v1.6.0-beta",
  "repository": "https://github.com/ufal/atrium-translator",
  "runner_ref": "",
  "docker_image": "",
  "run_id": "261019-101714",
  "run_uuid": "urn:uuid:a0e8bec6-f6c3-4c37-b489-2921e6a5e4c8",
  "run_agent": "",
  "license": "CC BY-NC-SA 4.0",
  "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
  "license_detail": {
    "effective_license": "CC BY-NC-SA 4.0",
    "is_non_commercial": true,
    "is_share_alike": true,
    "determined_by": ["lindat_cubbitt", "udpipe2_models"],
    "components": [
      { "name": "lindat_cubbitt", "license": "CC BY-NC-SA 4.0" },
      { "name": "udpipe2_engine", "license": "MPL 2.0" },
      { "name": "udpipe2_models", "license": "CC BY-NC-SA 4.0" },
      { "name": "amcr_vocab",     "license": "CC0" },
      { "name": "teater_data",    "license": "CC0" }
    ]
  },
  "duration_seconds": 111.0,
  "config": {
    "source_lang": "cs",
    "target_lang": "en",
    "mode": "metadata",
    "output_mode": "replace",
    "translation_backend": "lindat",
    "vocabulary": "data_samples/vocabulary.csv",
    "vocabulary_protected_terms_total": 102,
    "lindat_degenerate_replies_total": 35,
    "xsd_validation": { "valid": 15, "invalid": [], "strict": false }
  },
  "statistics": {
    "input_files_total": 15,
    "successfully_processed": 15,
    "skipped_files": 0,
    "output_counts_by_type": { "xml": 15, "csv": 15, "json": 0 },
    "performance_per_minute": { "xml": 8.11, "csv": 8.11, "json": 0.0 }
  },
  "skipped_files_detail": [],
  "limits_applied": []
}
```

In the field names `license_detail.effective_license` and in the `notes` sentence, the shared module still calls the
summary the "effective output license". Read it as the summary of the components, as above: the shared modules are
being changed to stop stating it as a licence ([Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/)).

## The record schema

The per-document record this tool reads and writes, `<doc_id>.document.json`, follows
[atrium_document.schema.json](../../atrium_document.schema.json), schema version **`1.0`**. That version is frozen as
the hub tag [`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)
(ufal/atrium-project@`544298b`), and it is the baseline this tool implements:

- [tests/test_schema_freeze.py](../../tests/test_schema_freeze.py) checks this repository's schema against the frozen
  copy beside it, [atrium_document.schema.doc-schema-v1.json](../../atrium_document.schema.doc-schema-v1.json): the
  copy is exactly the tagged file, nothing declared at the freeze has been removed or renamed, and every change made
  since is registered;
- all three files, like `atrium_document.py`, are vendored from the hub and kept byte-identical to its `v1` tag by the
  `para-drift` CI check, so they are never edited in this repository. The hub also checks that every record shape the
  tools write validates under both the frozen and the current schema.

What this tool writes into the record: [outputs.md](outputs.md#the-document-record). What may change after the
freeze, and what a new major version takes, is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).
[CITATION.cff](../../CITATION.cff) carries the same reference under `references`.
