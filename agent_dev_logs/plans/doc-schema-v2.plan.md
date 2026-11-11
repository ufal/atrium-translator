# Strategy — doc-schema-v2 (atrium-project, issue to be opened): the translator's metadata-en job

> _Written **2026-11-11** after the meeting of that day (ARÚP's output contract; K4TEL's points in session). The
> change as a whole is the hub's: [plan](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md)
> · [digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/doc-schema-v2.digest.md) (today's
> fields and writers) · [meeting digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/project_state_1111.md)
> (variant E, the paradata per step). This file is this repository's slice only; the repository has no open issue.
> The issue is not opened; rename this file to the issue number once it is. Code read at the checkout of 11-11._

**Status (2026-11-11)**: 📋 **Planned, not started. The translator leaves the document chain: a separate
record-level job turns AMČR's `metadata` into `metadata-en` (variant E) and writes nothing into the record. Code
follows the hub's shared modules.**

## The job

- **Input:** AMČR's `metadata` (AMČR 2.2 XML) and the XPath list, which belongs to AMČR (aiscr-webamcr#3570) and
  travels with each job. `amcr-fields.txt` is an example only.
- **Output:** `metadata-en` in `replace` mode (the default, `utils.py:60`), keeping the AMČR 2.2 structure, so
  AMČR's existing protected-field filter applies unchanged. `metadata-en` follows exactly the access rules of
  `metadata`; that is AMČR's side.
- **Protected fields** (`chranene_udaje`) may go to LINDAT: a secure service (contract §5.7).
- **The job type** is added by aiscr-docs-pipeline#13.
- **In the record:** nothing. `translations` and `entities[].translation_en` are reserved (D19).
- **The per-field `_log.csv`** (`[doc_id, "", xpath, source, translation, status]`) is never stored. The service
  already discards it; the CLI writes it locally only (`main.py:681`).
- **A segment left untranslated** after the retries fails the job (D13): no partial `metadata-en`.

## Tasks (next rounds)

1. **The field list per job:** the service takes the list inline with each request. Not a URL: the reason
   `service/api.py:337` gives for the XSD holds, since a URL from a request would make the service fetch. Today it
   loads the list once at startup from `AMCR_FIELDS_PATH` (`service/api.py:332`, `_load_xpaths()` at `:425`, `:472`);
   the CLI takes `--xpaths` (`main.py:453`) or config `fields =` (`main.py:563-564`).
2. **Nothing in the record in pipeline mode** (D1, D19): no `translations` block (`utils.py:976-990`,
   `:1876-1890`, with fields the schema does not declare), no `derived_from.translated_xml` (`main.py:778`).
3. **D13 in the job:** an untranslated segment fails it (today kept as source and noted `skipped`,
   `utils.py:809-817`). So does a UDPipe timeout that leaves a chunk's terms unprotected
   (`processors/lemmatizer.py:204-210`, `skipped`), once the hub's limits make `skipped` raise. Language sampling
   (`utils.py:411-417`) and segment splitting (`processors/chunking.py:68-72`) stay recorded.
4. **Paradata in the stored form** (§5.5): the action (`service/api.py:1127-1134`) names the upload and the
   translated XML by SHA-256 and adds record blocks. The job's action names `metadata` and `metadata-en` by
   distribution name and SHA-512, records the field list as a parameter, holds no field text and no record blocks.
5. **Components as facts** (D2): `para_config.txt` keeps its rows with their terms; no calculated licence (hub
   task 2). `docs/reference/paradata.md:79-89` shows an `effective_license`; it goes with the docs.
6. **Docs:** the README and user docs stop presenting full-text or ALTO translation as part of the pipeline
   (`README.md:98-103`, `docs/how-it-works.md:58-65`, `docs/usage.md:397-420`); ALTO mode stays a tool feature
   outside it. `amcr-fields.txt` is documented as an example (`README.md:44`, `:54`, `docs/usage.md:100`, `:116`,
   `docs/configuration.md:51`, `:69`, `docs/how-it-works.md:305`, `docs/development.md:36`).

## Paradata

One action per job in `paradata/metadata-en`, with no field text:

- **object:** `metadata` (distribution name, SHA-512);
- **result:** `metadata-en` (SHA-512);
- **parameters:** the field list (XPaths only), the backend, the vocabulary version;
- **limits:** `sampled`, `split`;
- **components**, facts with their terms: CUBBITT via LINDAT (LINDAT's terms: atrium-project#78, postponed to the
  next meeting), UDPipe (lemma matching for term protection), the AMČR/TEATER vocabulary, and FastText when the
  source language is `auto` (`main.py:617-618`). No run licence such as "a LINDAT run is CC BY-NC-SA 4.0" (D2).

## Depends on

- Hub tasks 1 (the grants: `translations` reserved), 2 (no licence block), 3 (`file_entity` with SHA-512 and the
  distribution name), 4 (`skipped` raises); then re-vendoring.
- aiscr-docs-pipeline#13 (the job type; the distribution names come with the job); aiscr-webamcr#3570 (the XPath
  list); atrium-project#78 (LINDAT's terms).
- Hub task 7: the hub's E2E smoke runs this tool on an ALTO page with a record (`docs/usage.md:397-420`); the lanes
  move to the new order there.
- None of Q1–Q7 is the translator's. The meeting digest already answers whether the service takes the field list
  per request (not today).

## Superseded in this repository's logs

All in closed issues' files (listed, not edited):

- `plans/46.plan.md:25`, `:32`, `digests/46.digest.md:32`, `:55`: `amcr-fields.txt` as the list → AMČR's list, sent
  with each job.
- `digests/46.digest.md:63`, `digests/4.digest.md:125`: a licence per run or backend → components with their
  terms (D2).
- `digests/52.digest.md:23`, `digests/46.digest.md:62`: `translations.*` record fields → nothing in the record
  (D19).
- `digests/46.digest.md:60`, `digests/52.digest.md:91`: a segment kept as source with a `skipped` note → the job
  fails (D13); likewise option (b) in `digests/52.digest.md:37`, `:56`, `plans/52.plan.md:19`, `:32`.
- `plans/46.plan.md:29`: acceptance reads the `_log.csv` statuses → the CSV is never stored; the job's outcome
  and its paradata.

## Not in scope

- Full-text and ALTO translation in the pipeline: a later decision.
- AMČR's access rules for `metadata-en`, its protected-field filter and the record's RO-Crate (AMČR's side).
- CI schedules: `lindat-daily.yml` and `scheduled-smoke.yml` go with atrium-project#81 (CI on pull requests and
  releases only, security updates excepted); `codeql.yml` and `security.yml` keep theirs.
- Galaxy work beyond the 30 September agreement: parked. The 11-06 entry moved the hub's Galaxy wrapper pin
  (`DEVLOG.md:919`); no action.
