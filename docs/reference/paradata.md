# Paradata and the record schema

For integrators and auditors who read a run's provenance. It answers where the paradata record is written, what
each field means, and which schema the document record follows. What the records list about licences:
[licences.md](licences.md).

## The paradata record

The batch CLI writes a **run-level** JSON provenance record after every run, named `YYMMDD-HHmmss_translator.json`,
into `paradata/` in the run's output directory, beside the translated files. The two AMČR replace folders under
[data_samples/](../../data_samples/README.md) hold the records of their sample runs in `paradata/`. The `/translate`
service writes no
such file: it returns each call's run as a Process Run Crate `CreateAction`, the response's `paradata`
([service/README.md](../../service/README.md#response)). The format is paradata 3.0
([atrium-project#84](https://github.com/ufal/atrium-project/issues/84)): no licence is computed, and what a run read
and wrote is named by AMČR distribution name and SHA-512.

Paradata is separate from the translation logs: a log records what was translated, line by line; the paradata
records *how the run was configured, what it used and what it produced* in aggregate.

Where one input passes through several tools, the per-tool records can be fused into one per input file with
`merge_paradata_files()`; the merged record lists the union of the components every tool used.

In the AMČR pilot the service's record is the paradata of the `metadata-en` distribution (`paradata/metadata-en`):
one call, one record, which names `metadata` and `metadata-en` by SHA-512, records the field list as a parameter,
and holds no field text and no per-field log. A command-line batch writes one file per run instead: it lists a
`metadata` input and a `metadata-en` output for each record translated, without naming the document each belongs
to.

The logger is [atrium_paradata.py](../../atrium_paradata.py), shared across the ATRIUM tools, which reads this
repository's [para_config.txt](../../para_config.txt) for the tool version and the component table, and normalises
the component licences with [para_licenses.py](../../para_licenses.py). Every field of the shared format is in the hub's
[paradata schema](https://github.com/ufal/atrium-project/blob/test/docs/paradata_schema.md).

### The fields

| Key                                 | Description                                                                                                                                                                                             |
|-------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `schema_version`                    | the paradata schema version, `"3.0"`                                                                                                                                                                    |
| `program`                           | `translator` from the command line, `translator-api` from the service                                                                                                                                   |
| `tool_version`                      | the tool's version tag, from `para_config.txt` (e.g. `v2.0.0-beta`)                                                                                                                                     |
| `repository`                        | the runner repository (`ATRIUM_RUNNER_REPO` when set)                                                                                                                                                   |
| `runner_ref`                        | the git ref the running container was built from (`ATRIUM_RUNNER_REF`)                                                                                                                                  |
| `docker_image`                      | the running image (`ATRIUM_RUNNER_IMAGE`); empty if unset                                                                                                                                               |
| `python_version`                    | the Python that ran                                                                                                                                                                                     |
| `run_id`                            | the timestamp identifier of the run, also the file's name                                                                                                                                               |
| `run_uuid`                          | `urn:uuid:…`, minted when the run starts: the `@id` of the run's `CreateAction`                                                                                                                         |
| `run_agent`                         | the operating organisation's IRI (`ATRIUM_RUN_AGENT`), or empty                                                                                                                                         |
| `action_status`                     | `completed`, or `failed` with `error` when a limit left content untranslated or the run raised                                                                                                          |
| `inputs`, `outputs`                 | by AMČR distribution name and SHA-512: `metadata` in and `metadata-en` out for a metadata record; `original` in for ALTO (research use, no stored output)                                               |
| `parameters`                        | what shaped the output: `xpaths` (the field list), and `translations` per document (the language pair, the backend, the output mode, the language detected)                                             |
| `components`                        | each component the run used with its own licence or terms, as facts ([licences.md](licences.md)); nothing is combined into one licence                                                                  |
| `start_time`, `end_time`            | ISO 8601 UTC timestamps                                                                                                                                                                                 |
| `duration_seconds`                  | wall-clock run time                                                                                                                                                                                     |
| `config`                            | the run's settings (below)                                                                                                                                                                              |
| `statistics.input_files_total`      | the number of input files                                                                                                                                                                               |
| `statistics.successfully_processed` | the number of files that produced output                                                                                                                                                                |
| `statistics.skipped_files`          | the number of files skipped after an error                                                                                                                                                              |
| `statistics.output_counts_by_type`  | file counts per output type (`xml`, `csv`, `json`)                                                                                                                                                      |
| `statistics.performance_per_minute` | files produced per minute, per output type                                                                                                                                                              |
| `skipped_files_detail`              | `{file, reason, timestamp}` for every skipped file                                                                                                                                                      |
| `limits`                            | the sampling and splitting notes (`limit`, `effect`, `value`, count, detail): a segment split at `TRANSLATION_CHUNK_CHARS`, a language decided on a sample, a glossary's selection of terms in a prompt |

`config` holds the run's settings only (`source_lang`, `target_lang`, `formats`, `mode`: `alto`, `metadata` or
`detect`, `output_mode`, `translation_backend`, `xsd_url`, `xsd_strict`, `fast_align`, `chunk_limit`), the
language-ID model and the translation endpoint
actually resolved (`lang_id_model`, `translation_api`), the source-language policy (`default_source_lang`,
`fasttext_confidence_threshold`, `lang_id_min_letters`, `lang_id_languages`: the languages the backend translates
into the target), and the counts of the run: `detected_kind` per document when the root element decided,
`vocabulary_protected_terms` per document and its `_total` with a vocabulary, `lindat_degenerate_replies` per
document and its `_total` with LINDAT, and, with an XSD, `xsd_validation` (`valid`, `invalid` doc ids, `strict`).
The service's `paradataRecord.config` has the same keys, built by the same functions (`main.build_run_config`,
`main.run_outcome_config`). No path and no file name of the run is in `config` (atrium-project#84 decision 1(c)):
the records are the run's `inputs`/`outputs` by distribution name (`metadata` in, `metadata-en` out), and the
vocabulary, a local XSD and the command line's field list (`--xpaths`) are `inputs` entries by file name and SHA-512,
`stored: false` (decision 2; the vocabulary was fingerprinted by SHA-256 in `config` before). The command line also
names each `<doc>_log.csv` it writes as an `outputs` entry the same way, by name and SHA-512 only: no row of a log
enters the paradata, and the service, whose record is `paradata/metadata-en`, names none. `xsd_url` holds an XSD
given as a URL, a setting; it is empty for a local one. The service's `/info` still reports the vocabulary's SHA-256.

### An example

Abridged, from a metadata run with LINDAT and the vocabulary:

```json
{
  "schema_version": "3.0",
  "program": "translator",
  "tool_version": "v2.0.0-beta",
  "run_id": "261113-101714",
  "run_uuid": "urn:uuid:a0e8bec6-f6c3-4c37-b489-2921e6a5e4c8",
  "action_status": "completed",
  "inputs": [{ "name": "metadata", "sha512": "5c1f…", "media_type": "application/xml", "stored": true }],
  "outputs": [{ "name": "metadata-en", "sha512": "9a07…", "media_type": "application/xml", "stored": true }],
  "parameters": {
    "xpaths": ["//amcr:amcr/amcr:dokument/amcr:popis", "…"],
    "translations": { "C-TX-201750164": { "source_lang": "cs", "target_lang": "en", "backend": "lindat", "output_mode": "replace" } }
  },
  "components": [
    { "name": "lindat_cubbitt", "license": "CC BY-NC-SA 4.0" },
    { "name": "udpipe2_engine", "license": "MPL 2.0" },
    { "name": "udpipe2_models", "license": "CC BY-NC-SA 4.0" },
    { "name": "amcr_vocab",     "license": "CC0" },
    { "name": "teater_data",    "license": "CC0" }
  ],
  "config": { "mode": "metadata", "translation_backend": "lindat", "vocabulary_protected_terms_total": 102 },
  "limits": []
}
```

The records committed in `data_samples/in-place_translated_files/xml/paradata/` and
`data_samples/ct2_translated_files/xml/paradata/` are paradata 3.0, as a run of this version writes them. The ALTO
and append folders keep no paradata: their earlier records predate paradata 3.0 (git history up to `v1.7.8-beta`).

## The record schema

A document record this tool is given, `<doc_id>.document.json`, follows
[atrium_document.schema.json](../../atrium_document.schema.json), schema version **`2.0`**; the translator writes
nothing into it. A
1.x record is read and migrated. `doc-schema-v2` is tagged only after the end-to-end proof; until then the published
reference is `doc-schema-v1`, frozen as the hub tag
[`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1) (ufal/atrium-project@`544298b`):

- [tests/test_schema_freeze.py](../../tests/test_schema_freeze.py) checks this repository's schema against the frozen
  copy beside it, [atrium_document.schema.doc-schema-v1.json](../../atrium_document.schema.doc-schema-v1.json): the
  copy is exactly the tagged file, nothing declared at the freeze has been removed or renamed, and every change made
  since is registered;
- all three files, like `atrium_document.py`, are vendored from the hub and kept byte-identical to the hub commit the
  `para-drift` CI check pins, so they are never edited in this repository. The hub also checks that every record shape the
  tools write validates under both the frozen and the current schema.

What the run records about each document: [outputs.md](outputs.md#the-document-record). What may change after the
freeze, and what a new major version takes, is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).
[CITATION.cff](../../CITATION.cff) carries the same reference under `references`.
