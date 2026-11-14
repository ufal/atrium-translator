# Strategy — [atrium-project#84](https://github.com/ufal/atrium-project/issues/84) (doc-schema-v2): the translator's metadata-en job

> _**Refreshed 2026-11-13**: "Status — round 2" (the code, pushed 11-13 to `test`) and round 4's notes. The file keeps
> the name `doc-schema-v2.plan.md` (it was `84.plan.md` on 11-12 and 11-13)._
> _**Refreshed 2026-11-12** on ARÚP's output contract of 12 November (D34, D35, D36) and atrium-project#84 §5 (motyc,
> 11-12); code read at `55e178e` (= `test`). The change as a whole is the hub's:
> [plan](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md) ·
> [digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/doc-schema-v2.digest.md) ·
> [contract digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/project_state_1211.md).
> This file is this repository's slice only; the repository has no open issue. Its 11-11 version
> (written before the issue existed) is superseded; the section "2026-11-12" is the current reading._

**Status (2026-11-13)**: 🟡 **Implemented, pushed 11-13 to `test`; see "Status — round 2". Deferred items are listed there.**

**Status (2026-11-12)**: 📋 **Planned, not started. The field list comes with each request; there is no default
list, and metadata mode refuses a request without one (D34). `amcr-fields.txt` stays only as a documented example
that nothing loads (K4TEL, 11-12; #84 §5 says the file goes — an example that is never read meets D34's "no default
list"). `metadata-en` runs on archiving, follows `metadata`'s protection in the Digital Archive (D36), and is not
published until atrium-project#78 is settled (D35).**

## 2026-11-12 — what the contract of 12 November changes here

| Point              | Today                                                                                                                         | Under D34                                                                                                                                              |
|--------------------|-------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| Service field list | loaded once at startup from `AMCR_FIELDS_PATH` (default `amcr-fields.txt`, `service/api.py:332`; `_load_xpaths()` 425; 472)   | a form field of each metadata request (XPaths inline, not a URL); no startup load, no `AMCR_FIELDS_PATH`                                               |
| No list            | 422 "requires XPath targets, and none are configured" (`service/api.py:791-800`), reached only when the startup file is empty | the same 422 for a metadata request without the field; the message names the field                                                                     |
| CLI                | `--xpaths`, else config `fields = amcr-fields.txt` (`config.txt:34`; `main.py:563-564`)                                       | `--xpaths` required in metadata mode; the `fields =` key goes                                                                                          |
| `amcr-fields.txt`  | the default list; copied into the release's `dist/` (`.github/workflows/release.yml:161`)                                     | a documented example only (e.g. under `docs/examples/`), never loaded, not shipped as configuration; AMČR's list is authoritative (aiscr-webamcr#3570) |
| Paradata           | —                                                                                                                             | the list used (XPaths only) in `paradata/metadata-en` `parameters`; no field text, no per-field log                                                    |
| Publication        | —                                                                                                                             | `metadata-en` not published until #78 (D35); served under `metadata`'s protection (D36): AMČR's side                                                   |

Tests that pin the startup list (`tests/test_api.py`, `tests/test_cli_and_routing.py`, `tests/test_limits.py`,
`tests/openapi_contract_data.py`) and the docs that name the default (`README.md`, `service/README.md`,
`docs/configuration.md`, `docs/usage.md`, `docs/how-it-works.md`, `docs/development.md`, `docs/reference/cli.md`,
`docs/decisions/output-mode.md`, `CONTRIBUTING.md`, `data_samples/README.md`) move with the change; `eval/bakeoff.py`
and `eval/langid_report.py` pass the example explicitly.

**Samples** (atrium-project#16, motyc 11-12): the answer names keyword-extract and digital-convert, not this
repository; `data_samples/my_documents/` (15 AMČR records), their translations and
`eval/results/issue4_bakeoff_2610/` keep #20's status of 06-08 unless the next meeting decides otherwise (hub #16
plan, action 7).

Unchanged from 11-11: nothing in the record, D13 in the job, SHA-512 and distribution names, components as facts,
docs without ALTO or full-text translation in the pipeline (tasks 2–6 below).

**Status (2026-11-11)**: _(superseded 11-12 where the section above differs)_ 📋 **Planned, not started. The translator leaves the document chain: a separate
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

1. **The field list per job** _(11-12: per request, no default, refusal without one — the table above)_: the service takes the list inline with each request. Not a URL: the reason
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

## Status — round 2 (11-13)

Local branch from `test`, on the re-vendored shared modules (record 2.0, paradata 3.0,
`ContentLimitError`); delivered as an archive with the commands to run; pushed 11-13 (below). Nothing posted.

- **Task 1, field list per request (D34):** the service takes an inline `xpaths` form field, one XPath per line,
  required in metadata mode (`422 invalid_request` without it). The CLI requires `--xpaths FILE` in metadata mode.
  `AMCR_FIELDS_PATH`, the startup load and config `fields =` are gone. `amcr-fields.txt` is a documented example:
  out of the release bundle and `.env.example`, listed in `.dockerignore`.
- **Task 2, nothing in the record:** `document_json` is still accepted ("just in case"): validated, `doc_id`
  inherited, returned unchanged. No `translations`, no `derived_from`, no licence.
- **Task 3, D13:** a segment kept as source and a UDPipe timeout raise; the glossary caps are `sampled`;
  `main.py` lets `ContentLimitError` fail the file.
- **Task 4, paradata:** metadata mode names `metadata` (input) and `metadata-en` (output) by SHA-512, with the
  field list, backend, model and vocabulary version as parameters; no field text. Full-text and ALTO modes (research
  use) name `original` and store nothing.
- **Tasks 5, 6:** `effective_license` and the licence wording go; full-text and ALTO translation are not presented
  as pipeline work in the README and docs.
- **Tests:** 1160 passed, 4 skipped (offline lane, -m "not slow").
- **Pushed** to `test` (11-13). Round 4 re-vendors the addendum's shared files; the suite then passes (1160, offline
  lane).
- **[atrium-project#78](https://github.com/ufal/atrium-project/issues/78#issuecomment-6827422711)** (11-12): LINDAT's
  Terms of Use set aside for the pilot; whether D35 holds in the pilot is the hub's Q14. The docs keep D35 until it is
  answered.

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
