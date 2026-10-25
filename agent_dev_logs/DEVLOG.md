# 📓 atrium-translator — agent_dev_logs/DEVLOG.md (timeline index)
> _XML in-place translation (AMČR metadata records, ALTO). No open issue: #46 closed 2026-10-25, #4 2026-10-19,
> #52 2026-10-13; Dependabot's #54 closed 2026-10-21. `test` = `0d496b7` (2026-10-25) · **v1.5.0-beta**
> (2026-10-19, `379252e`; an immutable GitHub release, atrium-project#72). Latest entry: 2026-10-25 (evening), the
> CONTRIBUTING checklists and the documentation check in CI. Twelve-factor detail lives in the hub:
> `ufal/atrium-project/agent_dev_logs/{digests,plans}/53.*`._
> _Per-issue detail: `digests/{4,46}.digest.md` · `plans/{4,46}.plan.md` · `issues/` exports (source of truth). Cross-repo/hub
> history lives in `ufal/atrium-project/agent_dev_logs/DEVLOG.md` (deduplicated out of this file)._

## 2026-06-20
- **#4 Translation base model to use** — Opened by K4TEL: explore base models for text-to-text translation (GLM-5.2,
Cohere Command A, and the open-source space).

## 2026-06-21
- **#4** — Posted a candidate comparison (CUBBITT baseline, Command A, GLM-5.2, MADLAD-400, NLLB-200, Tower+, Opus-MT,
DeepL, Google) and a pluggable `TranslationBackend` design; recommendation: prototype Command A (native glossary retires
Tag-and-Protect), MADLAD-400 (Apache-2.0) as the permissive self-host path, keep CUBBITT as default.

## 2026-06-22
- **#4** — Refined free + low-resource plan (adds EuroLLM; phases 0–3; full licensing recipe incl. FastText/vocab NC
traps). Implementation landed: the previously-dead `get_backend` seam wired in (default still `lindat`, zero behaviour
change), the missing `docs/translation-backends.md` written, an `openai_compatible` LLM adapter (one OpenAI-compatible
client → many free providers, prompt-glossary, OCR-faithfulness guards), `eval/bakeoff.py`, and a CTranslate2 scaffold;
217 tests pass; PR body "Closes #4".

## 2026-06-23
- **#4** — Hardening/remediation cycle: anti-truncation `max_tokens`/`max_decoding_length`, CT2 length-ratio
faithfulness guards, word-boundary glossary matching, a fix for the FastAPI `/translate` streaming race (buffer
to in-memory `Response`), one-time XSD schema compilation, `int4` CT2 default for VRAM safety, backend-aware paradata
licensing, and Dockerfile immutability; 220/220 tests green; released **v0.7.0** for practical testing.

## 2026-06-27
- **#4** — Digest+plan refreshed on `test` (`40ff9be`). **v0.8.0** cut around this refresh: per-page ALTO calls instead
of per-block, paradata scripts synced to the atrium-project template, `agent_dev_logs/` added.

## 2026-07-12

* **#4** — API version drift **resolved** (`953e780` "fix version reading"): `_read_tool_version()` at `service/api.py:29-39`
reads `para_config.txt [tool] version`, so `/info` can no longer drift from the release tag.
* Released **v0.8.1** (automatic version reading + the shared `tests/test_para_licenses.py`, dependency bumps); suite at **228 tests** green.
* Digest re-verified against code: the backend architecture is verified live, and what genuinely remains is the
**model-selection answer itself** — run `eval/bakeoff.py` (CUBBITT vs LLM ±glossary) on AMCR + ALTO samples,
live-smoke the LLM backend via CLI + `/translate`, finish the `ct2` permissive recipe (EuroLLM-1.7B / MADLAD-400-3B
conversion) — plus the `docs/translation-backends.md` relocation (verified `docs/` still doesn't exist).

## 2026-07-26

* Hub template sync: `atrium_document.py` test coverage expanded and renamed (`7e7e416`, `36e5a4e`); GHA build test
coverage pushed to 70% via a new 593-line `tests/test_main_high_impact.py` (`4d130b6`); the paradata main-flow logger
fixed then cleaned up (`db2d3c8`/`1de0961`). Version bumped.

## 2026-07-27

* HTTP retry policy tuned for the LLM/CT2 backends: max retries and backoff raised to 10 attempts / 2s (`16e197f`).
Several GHA test-timing fixes (`dd0c00f`, `8bf4b5b`, `6a28704`) — this repo's suite was intermittently timing out in
CI around the same window every other tool repo was hardening its own workflows.

## 2026-07-30 – 2026-07-31: the hub-wide GHA overhaul lands here too, and translator pilots the release path

* The 07-30 "Opus" GHA hardening pass (`ee1c57f`, `5b51738`, `80bc05e`, `32a15cf`) lands cleanly on `test` — this
repo's history has none of the dangling-tag gaps seen in alto-postprocess/nlp-enrich for the same window. The
reusable-workflow references are repinned from `@test` to the hub's tagged **`@v1`** the same evening (`68d4f7f`,
confirmed and version-bumped as **v0.10.2** the next day, `52db490`).
* **v0.10.1** ships first (07-30/07-31), and it is the one the hub's own cross-repo audit had been waiting for: per
`CONTRIBUTING.md`'s changelog, this release **exercises the release path end to end** — the version guard via the
shared `check_version.py`, the post-publish Trivy digest scan + SARIF upload, and the buildkit SBOM/provenance
attestations — with no functional change to the tool itself. `project_state_3007.md` (the hub's 07-30 cross-repo
digest, finding N7) had recommended piloting exactly this, on exactly this repo, for exactly this reason ("lightest
repo — no GPU, no torch, single build target"); translator got there the same day.
* Hub template (`atrium_document.py`) synced twice more (`b95c6f2`, `ac704ad`, `f9ac9ea`, the last adding `doc_id`
handling for JSON).

## 2026-08-01 – 2026-08-02

* Issue logs refreshed twice (`213909d`, `e68b3a7`); **v0.10.3** ships — the end-to-end GHA pipeline for
`atrium_document` JSON input/output is refined and tested against the draft schema.
* **#4** — K4TEL restates the outstanding bakeoff task as a structured research comment: the pluggable backend
architecture (CUBBITT / OpenAI-compatible / CTranslate2) is verified and shipped, but no comparison has actually
been *run* yet. Tasks: execute `eval/bakeoff.py` on AMCR + ALTO samples across CUBBITT / Command A / a chosen
permissive open model; finalize the `ct2` conversion recipe; live-smoke the LLM backend under load. Digest + plan
refreshed to match (`1275b57`).

## 2026-08-03 – 2026-08-06

* Hub template (`atrium_document.py`) synced twice more (`b800613`, `6a2c0c5`, `5ea50da`); `9467f37` fixes a Docker
GHA failure. **v0.10.4** ships (08-06): **`doc_id` is now inherited, never re-derived** — `record_doc_id()` takes
its key from the `--document-json` baseline (falling back to `canonical_doc_id()` only for a standalone run),
because the input here is never the original document: alto-postprocess hands translator
`PAGE_ALTO/<doc>/<doc>-1.alto.xml`, and every downstream artifact (the record, the log's `file` column, the default
output filename, the paradata key, `service/api.py`'s returned filename) needs to agree on the *document's* id, not
the page-file's. Same day: a further "LLM review+fix round by Opus" (`0475809`) plus an e2e-assertion fix
(`b2e3e48`, touching `atrium_document.py`/`main.py`/`service/api.py` and adding `tests/test_atrium_document.py`);
`ruff.toml` hardened (`2e12b28`).

## 2026-08-18 – 2026-08-20

* Routine dependency bumps (pip-deps group, `067bc7a`/`a39e08c`). GHA hardening cluster matching the same pattern
across the ecosystem this week: `103bb1d` fixes workflows, `f25ad8d` re-aligns the hub template, `0f367b0` is a
further Opus-reviewed round. **v0.10.5** ships (08-19/08-20): re-vendored `atrium_document.py` (`set_source()` now
fills sub-keys a partial first write left unset); `.coveragerc` stops excluding `service/*` — **the exact directory
where a `backend`-less `/translate` had been returning HTTP 500** — with the coverage floor held at 81% against a
re-measured 82.87%. `scheduled-smoke.yml`'s docstring corrected to say what it actually checks (fresh-install
dependency drift via the same hermetic suite as the PR lane — **not** integration coverage; the live-backend gap
that let the 500 ship in the first place is still open) and made skips visible with `-rs`. Concurrency scoped by
`github.event_name`; `release.yml` gets its own concurrency group.

## 2026-09-03

* Routine dependency/action bumps (pip-deps group `#43`, `softprops/action-gh-release` `#44`). No functional change;
`test`/`master` converge at `dae197a`.

## 2026-09-07

* **State**: 1 open issue (#4), unchanged in scope since the 08-02 research comment — the bakeoff has not been run.
`test` and `master` both at `dae197a`, **v0.10.5**. Confirmed live: the hub reusable-workflow reference is pinned to
`@v1`. This repo was the first in the ecosystem to actually exercise its tag-gated release guards (v0.10.1,
07-30/31) — the hub's own audit had flagged that path as universally untested the same day. The two things worth
tracking forward: the still-unrun `eval/bakeoff.py` comparison (#4's actual remaining work), and the known
"`backend`-less `/translate` → HTTP 500" gap that `.coveragerc` now measures but which no live-backend integration
test yet catches.

## 2026-09-07 – 2026-09-12: the twelve-factor sub-issues land, none of them visible on GitHub

Five of `ufal/atrium-project#53`'s sub-issues were implemented here in six days, straight onto `test` — no PR, so
nothing auto-closed or referenced them and every one still reads `open`. Recorded here because the issue tracker
does not record it. Full audit in the hub's `agent_dev_logs/digests/53.digest.md`.

* **`e731e55` (09-07) — factor IX, hub #55.** The `api` Dockerfile stage, so a runnable API image exists to publish
at all: before this the service was reachable only through a compose `entrypoint:` override on the batch image.
`STOPSIGNAL SIGTERM`, `HEALTHCHECK` against the vendored `service/healthcheck.py`, and `serve_lifecycle`'s drain —
`/ready` flips to 503 the instant SIGTERM arrives, in-flight translation finishes before `models.clear()`.
* **`b0116d9`/`30d986e` (09-09) — hub #54.** `atrium_rocrate.py` vendored and brought under `para-drift`.
* **`6e77466` (09-11) — factor VII, hub #58.** `$PORT` honoured. The entrypoint baked `--port 8000` into an
exec-form array where no shell exists to expand a variable, while the manifest handed to ARÚP/ARÚB declares
`env: PORT` and `healthcheck.py` already read it — so setting `PORT` moved the *probe* and not the listener, and
the container reported unhealthy forever. `ENTRYPOINT ["python", "-m", "service.api"]` with `HOST`/`PORT`/`RELOAD`/
`GRACEFUL_SHUTDOWN_S` read in `__main__`. `-m` and not a script path: a script launch puts `sys.path[0]` at
`/app/service`, and `from main import ...` then fails before the app is built.
* **`ca1bd7d` (09-11) — factor IV, hub #63.** `TRANSLATION_URL` (with `LINDAT_BASE_URL` as an alias) and
`UDPIPE_URL` resolved at **construction**, not import — which is why the integration lane added later needs no
`importlib.reload`. `service/api.py` reports the *resolved* endpoint in paradata rather than a literal.
* **`d8155b7` (09-11) — factor III, hub #60.** The first `.env.example`.
* **`8aeac5f`..`8ed9fc8` (09-12) — factor XI, hub #61.** The logging contract: one `basicConfig` in `__main__`,
stdout, `LOG_LEVEL`, and the vendored `tests/test_logging_contract.py` guarding it by AST rather than at runtime.

## 2026-09-13→15: the base image blocked a release, twice, in two repos

Not a code defect — a defect in what the build *inherits*. `python:3.11-slim` is a floating tag and nothing in this
ecosystem bumps it (no repo declares a `docker` dependabot ecosystem; `docker_gha_roadmap.md` H6), so the base layer
is whatever Docker Hub last rebuilt. On 2026-09-13 that layer carried `perl-base` 5.40.1-6 with three FIXABLE
CRITICAL CVEs — CVE-2026-13221, CVE-2026-42496, CVE-2026-8376, all fixed upstream in 5.40.1-6+deb13u1.

The hub's release gate (`docker-tool.reusable.yml`, "Fail the release on fixable CRITICAL vulnerabilities") blocks
exactly that class, and because the promotion step is `if: success()`, **a blocked release still publishes — by
digest only, with no `:<version>` and no `:latest` tag**. That is what happened to `v1.0.0-beta` here, on both
matrix targets.

Two things made it hard to see, and both are worth remembering:

* **The gate is `if: startsWith(github.ref, 'refs/tags/')`.** The identical commit passes on `master` and on `test`.
  Only a release is stopped, so the failure appears at the worst possible moment and never during development.
* **A cache-served apt layer looks correct and patches nothing.** The build uses `cache-from: type=gha`, so the
  `apt-get upgrade` had to sit *below* the `ENV` block embedding `ATRIUM_RUNNER_REF` — CI passes that as
  `github.ref_name`, unique per release tag, which busts the layer on every release and only on a release.
  `tests/test_dockerfile_security_layer.py` pins that ordering, because it is invisible on inspection.

`v1.1.0-beta` (`d0c1572`) released green on 2026-09-14 with the fix — run 34865984128, gate and promotion both
green on both targets. **This repo was the only one that got the fix.** On 2026-09-15 `atrium-nlp-enrich` hit the
identical three CVEs on `v0.20.2` (run 34970419474) across all three of its matrix targets, for the same reason.
The layer has since been ported to the remaining four repos and the test promoted to a para-drift canonical file
(`docs/templates/shared/MANIFEST.json` row 17), so a repo that loses it goes red before a release rather than
during one. (atrium-project#53)

## 2026-09-13: pre-production hardening

Prompted by a production-readiness review rather than by a filed issue. Five defects that the test strategy could
not see, because each lived in a place the suite was structurally not looking at; each fix was confirmed by
reintroducing the defect and watching the new guard go red. Detail and evidence in the hub's
`agent_dev_logs/digests/53.digest.md` (the `digests/12factor.digest.md` this line used to cite was never written).

* **The retry policy was not configuration.** `http_retry.py` clamped its own arguments *upward*
(`max_retries = max(10, max_retries)`), so `LINDAT_MAX_RETRIES`, `LINDAT_BACKOFF_BASE_S` and their `LLM_*` twins
were read and discarded below the floor — factor III, in the code that `.env.example` had just documented. The
effective policy was 11 attempts backing off 2+4+…+1024 = **2046 s for one failing chunk**, which no `/translate`
could survive against `GRACEFUL_SHUTDOWN_S=20`: the drain contract #55 had just built was unreachable in the
failure case it exists for. `test_translator.py` and `test_llm_backend.py` had both been updated to assert the
clamped 11 and to call it "10 default retries"; the declared default was always 4.
* **`/translate` read the whole upload before checking its size**, so the 413 was unreachable for exactly the
inputs it existed to refuse. Now read in bounded chunks, with a `Content-Length` envelope pre-check.
* **The batch CLI always exited 0** — every failure path was a bare `return`. A Kubernetes `Job` reported success
for a run that translated nothing. Now `0`/`1`/`2`/`3`.
* **A failed FastText load was swallowed**, after which `detect()` answered `("en", 0.0)` for every document while
the service reported itself healthy — the failure mode of an egress-restricted cluster specifically. Now recorded,
logged once, and reported by `/health?deep=true`. Declared rather than fatal, since a deployment that always passes
`--source_lang` never consults it.
* **The release zip could not start.** It omitted `atrium_document.py` and `service/atrium_service.py`, both
imported by the entry points — `ModuleNotFoundError` on the primary download path for anyone not using the
container (factor V, in the part #62 did not reach).

Also: the ecosystem's last 3.12 CI lane moved to 3.11 (**hub #64** — grepping `python-version` across all six repos
now returns 3.11 and nothing else); `.env` actually reaches the container (`env_file`, verified by rendering
`docker compose config` — the previous file silently dropped `LOG_LEVEL`, `ALLOWED_ORIGINS` and `MAX_UPLOAD_MB`);
`tests/integration/` closes the live-backend gap `scheduled-smoke.yml` had been naming in its own docstring since
August; and the README finally documents Docker, the API, the environment and deployment, having contained none of
them.


## 2026-09-26

- **#46 — ALTO alignment regression, root-caused and fixed.** The `8522167` sample refresh ("TODO: fix alto alignment")
produced 2489 blanked `String`s in replace mode and 1300 in append, against 224 in the June v0.5.0 sample. Three causes,
measured on the committed logs:
  * **LINDAT answered HTTP 200 with garbage** — one Czech word (`"pravidla"`) repeated up to ~150 times, for headings and
    whole sentences, on the ALTO *and* the metadata path (11–12 of 37 fields), **nondeterministically** (the same field
    was garbage in one run and fine in the next; the four sample runs overlapped in time). No backend checked content.
  * **The aligner trusted unvalidated anchors.** Page-level batching (v0.8.0, after the June run: 2084 s then vs 614 s
    now) sends all line anchors of a page as one request and checked only its line count. Degenerate anchors are never
    logged, so the damage was invisible: in the append run only 21 blocks had garbage block text, yet 368 lines got
    0 tokens next to lines holding 60–70.
  * **Append mode lost the source** (it was replace + `LANG="en"`), and replace left ABBYY's `LANG="cs"` on English.
- Fix: `processors/quality.py::degeneration_reason` (0 false flags on June's 1193 blocks / 2069 lines / 37 fields;
  every looping block and field of both September runs caught); `LindatTranslator` re-requests degenerate replies
  (`LINDAT_GUARD_RETRIES`); LLM/CT2 guards raise the new `DegenerateTranslationError`; batches are accepted only when
  every item is plausible; line anchors are validated against their own source line and replaced by a proportional share
  when unusable; failed blocks/anchors/fields are **flagged and re-run at the end of the same document**
  (`TRANSLATION_RERUN_ROUNDS`, `TRANSLATION_RERUN_DELAY_S`) and kept as source if they never recover; the `_log.csv`
  gained a `status` column (`ok` / `rerun` / `approx_alignment` / `untranslated`) and is written in document order.
  ALTO append now keeps `CONTENT` and adds `<ALTERNATIVE PURPOSE="translation:en">`; replace relabels existing `LANG`.
  `data_samples/` still need a refresh against the live endpoint (unreachable from the development sandbox).
- **#46 — source-language identification refined.** A real `--source_lang auto` refresh logged FastText naming
`krc`, `yue`, `bod`, `epo` and `swh` for short blocks of the Czech sample. Causes: unmapped ISO 639-3 codes passed
straight through as the source language; the ALTO path ignored the confidence score (only metadata applied a
hard-coded `0.2`, which paradata recorded for both); and any detection failure answered `en` — the target — leaving the
block silently untranslated (every block, on a host where the model download fails). New `processors/language.py`
resolves each block/field as *detected* (≥ 20 letters, score ≥ 0.5, a language the backend can translate) → its own
`LANG`/`xml:lang` *hint* → the *document* language (resolved once) → the *default source language*
(`--default-source-lang` / `default_source_lang` / `DEFAULT_SOURCE_LANG`, `cs`). Per-document log line of what was
overridden; `translations.detected_source_lang` in the Document JSON; the real policy in paradata;
`eval/langid_report.py` to tune the thresholds where the model is available. Offline run with a FastText stand-in:
0 UDPipe "no model" warnings (HEAD: `bod, epo, krc, swh, yue`) and no bogus `LANG` in append output (HEAD: 8 blocks).
- **#46 — why LINDAT "fails from time to time".** Sequential runs after the fixes showed the first request of every
batch degenerate and its byte-identical re-request succeed, two runs agreeing reply for reply (same inputs, same token
counts). Deterministic garbage cured by an identical retry = **one broken replica behind a round-robin balancer**;
the "random ~30 %" of the concurrent 2026-09-26 sample runs was the same thing with the alternation scrambled.
`eval/lindat_probe.py` sends one text N times (fresh connections and one keep-alive session) and prints the ✓/✗
pattern and a verdict to hand to the LINDAT operators. Client side: the first re-request is now immediate (back-off
only from the second — sleeping cannot reach another replica), recovered rejections log at INFO, and `main.py` reports
one `LINDAT: N degenerate reply(ies) re-requested` line per document (+ `lindat_degenerate_replies` in paradata).
- **#46 — the broken replica, confirmed.** `eval.lindat_probe` against the live `cs-en` model (11:51 UTC): fresh
connections and one keep-alive session both `✗✓✗✓✗✓✗✓✗✓✗✓`; bad replies 109 tokens of `pravidla` in ~1.6 s, good
ones ~0.3 s, `Server: nginx/1.30.1`. The balancer rotates per request even inside a connection, so the client cannot
avoid the replica; the immediate first re-request always reaches the healthy one (append run: 101 re-requested, all
recovered). Left for the LINDAT operators.
- **#46 — the `_log.csv` was empty for the whole run.** It was opened with `"w"` when a document started and filled
when it finished (rows are buffered for document order), so `4a44fb6` committed both ALTO logs as 0-byte files
mid-run. `main.py` now writes `<doc>_log.csv.partial` and `os.replace`s it once the XML is written; a failed
document keeps its previous log. `*.partial` is git-ignored.
- **#46 — merged table cells shifted a column.** The first complete ALTO log (`b334b65`, append) had 2095 rows all
`ok`, 7 with no translation — all on page 76, in two 42-line table columns whose block translation merged repeated
cells (39 and 38 words). A split only cuts the block translation, so from the first merged cell every line showed its
neighbour's number, and the last lines none. `utils.py::_block_is_starved` (fewer words than lines of text — prose
never gets there) now sends such a block line by line, each cell from its own Pass-2 translation
(`_line_by_line_buckets`); a cell without a usable one keeps its source (`untranslated`); `--fast-align` flags the
block `approx_alignment`, and no line with text is ever empty *and* `ok`. Offline replay of the sample with a
cell-merging stub: only page 76 changes, every number on its own row.
- **`v1.2.0-beta` released** (`3f2f1b6`); both ALTO and both AMCR sample sets refreshed from the live endpoint
(ALTO 2095 rows each, 0 blank, page-76 cells in place; AMCR 15/15 per mode, 37 degenerate replies recovered per run).
- **#46 — production-readiness review.** Four things that would have gone wrong in the production run:
  * **Records understated their licence.** The record took the paradata licence block before the backend's
    components were logged (after the first file in `main()`, after the call in `/translate`), so the first record of
    a run — every record of a one-page pipeline stage or a service call — said CC BY-NC 4.0 (FastText only, or "no
    components recorded") for a CC BY-NC-SA 4.0 run. Visible in the committed ALTO record and the first AMCR record
    of each run. `main.log_backend_components()` now runs before `doc.add_license_detail()`; both callers share it.
  * **`--xsd` could not load AMCR 2.2**: its `xs:import` of `http://www.w3.org/2001/03/xml.xsd` needs HTTP, which
    lxml 6.1.3's libxml2 2.14.6 does not have — every `--xsd` run on AMCR exited "XSD schema load failed".
    `load_xsd` now resolves imports in Python (`_SchemaImportResolver`; the XML-namespace schema served locally).
  * **`--xsd` validated the OAI-PMH envelope**, so even an untouched record failed at the root; each `oai:metadata`
    payload is validated now.
  * **The API image name in the docs does not exist**: `atrium-translator:<version>-api` → the published
    `atrium-translator-api:<version>` (README, `service/README.md`, compose; the hub's K8s template too).
  With `--xsd` working, the `maxOccurs` question is answered: AMCR 2.2 accepts the samples as source (15/15) and as
  replace output (15/15), and rejects append output (0/15 — `xml:lang` not declared on the free-text fields, the
  repeated element not allowed). ALTO append validates against ALTO 3.1. Kept both modes and the `replace` default;
  append on AMCR records warns once per run.

## 2026-09-26 (AMČR baseline) — atrium-project#67 and motyc on #46

- **What arrived:** [atrium-project#67](https://github.com/ufal/atrium-project/issues/67) (motyc, AMČR): close #46
  *"after our production run on 1.2.1-beta"*; finish #4. On #46 (16:32): the run will check **`metadata-en` only** —
  free-text fields through their XPaths, **`replace`**, `--xsd` AMČR 2.2; vocabulary fields take AMČR's own English
  labels and are not translated; ALTO is not in the pilot; please run the **`live-backend`** job once; the self-hosted
  **`ct2`** backend to discuss on 30 September. **Adopted by ÚFAL as binding.**
- **Dev logs:** `46.*` 🔒 **close-out after AMČR's run** — the corpus, mode, schema, vocabulary and artifact questions
  marked answered; three steps left (ÚFAL dispatches `live-backend`; AMČR runs; close). The digest's "opener only" is
  replaced by the seven comments. `4.*` ✅ **finish** — target 2026-10-15 (K4TEL, 09-08); bake-off weighted to AMČR
  `metadata-en`; `ct2` as a 30 September decision step; versions → v1.2.1-beta.
- **Carried to the hub:** the translator's hardcoded limits (chunk 4000, language-id 2000 characters, ct2 2048 / 40,
  LLM timeout 120 s) are in hub #53's limits table; the seed and `CreateAction` asks in hub #67.
- **Not pushed: files delivered in chat.**

## 2026-09-27 — limits, seed test, ct2 on the cluster

- `88a926b` (hub #68): a `/translate` test that a seed keyed unlike the upload is accreted onto, not returned
  untouched — the translator already did it right. `0551e34` / `160f4a4` (hub #53): every limit declared in
  `tool_limits.py` (`TRANSLATION_CHUNK_CHARS`, `LANG_ID_*`, `LINDAT_*`, `LLM_*`, `CT2_*`), reported in `/info`,
  echoed as `limits_applied` / `X-Atrium-Limits-Applied`; the `/translate` error body is `{status, reason, detail,
  errors|limit}` now. `4afa1e7` / `4964e76`: `process_metadata_xml` logs instead of printing.
- `4341b8f`: the EuroLLM path uses the checkpoint's Hugging Face tokenizer and chat template. `1e1ce67`: K4TEL's
  first real `ct2` run (EuroLLM-1.7B int8, CPU) committed as the AMCR replace samples — 15/15 valid against AMCR 2.2,
  7 fields `untranslated`, and (found 2026-09-28) the prompt glossary inside 13 of the 30 `ok` ones.

## 2026-09-28 — #46 close-out check; hub #6 CC0; hub #70 reserved

- **#46, ÚFAL side: nothing left in the code.** A four-dimension review of `test` (limits · production path ·
  docs/config · `ct2`), each finding checked by a verifier (28 of 31 agents finished; the 3 cut off were settled by
  hand), found the shipped `config.txt`'s `formats = alto.xml` turning a `--xpaths` run without `--formats` into an
  ALTO run that exits 2 (in v1.2.1-beta too — AMČR passes `--formats xml` there), and the CLI without a log handler
  since `4afa1e7` (the `--xsd` verdict invisible). Fixed, with the `ct2` prompt leak, `jinja2`, the tokenizer
  directory, NMT inputs, ct2 paradata, the decoding budget, a declared `UDPIPE_TIMEOUT_S`, an end-to-end limits test,
  and the docs/config contradictions (precedence, append validity, 413 wording, `/translate` without vocabulary).
  Detail: `digests/46.digest.md` "ÚFAL-side close-out check". Suite 896 → **934 passed**.
- **Hub #6:** AMČR stated the `heslo` vocabularies and TEATER are CC0 → `amcr_vocab`, `teater_data` = `CC0` in
  `para_config.txt`; a EuroLLM + glossary run resolves to MIT, LINDAT stays CC BY-NC-SA 4.0.
- **Hub #70:** `entities[].translation_en` documented as reserved (unwritten until #70 decides after the pilot).
- **Samples:** the AMCR replace folder stays the `ct2` run, documented as such; the stale LINDAT record left its
  `paradata/`.
- **Version:** `para_config.txt` / `CITATION.cff` → v1.2.2-beta (`check_version.py --tag v1.2.2-beta` passes).
- **Not pushed: files delivered in chat.**
- **v1.2.2-beta tagged** (`d8d72b5`, K4TEL): the files above plus a LINDAT refresh of the AMCR replace samples in the
  production shape (`--source_lang cs`, `--xsd` AMCR 2.2; run `260928-110452`, 15/15, all `ok`). Verified green.
- **After the tag (on `test`):** `data_samples/README.md` describes that LINDAT run (it still described the `ct2` one)
  and the stale `ct2` record left `paradata/`; `--xsd` verdicts recorded (`xsd_validation`, a summary line,
  `--xsd-strict` / `XSD_STRICT`); `/translate` refuses a declared over-size request before the body is parsed; the
  `split` note counts segments; the service loads a `ct2` model at startup under a load lock. Files delivered in chat.

## 2026-09-30 — atrium-project#72 round 1: the production image declared
* `.github/production-image.json` (the `api` target: `service/api.py`, `main.py`, `utils.py`, `tool_limits.py`,
  `processors/*.py`), checked by the hub's `tools/ci/image_closure.py` in workflow-lint; `docker.yml` names `api` as
  the production target. Revendored `atrium_openapi.py`, `service/atrium_service.py`, `tests/test_openapi_contract.py`
  (the release gate's declared-rename rule).
* Tag draft: `v1.3.1-beta`. **Not pushed: files delivered in chat.**

## 2026-10-07 — Dangling section references (#4)
* `processors/ct2_translator.py` and `eval/bakeoff.py` cited "§5" and "§6" of `docs/translation-backends.md`, which
  has no numbered sections (milestone T3.4.1 checklist). They now name *Licensing matrix (informational)* and
  *Suggested next steps* under *Recommendation*.
* **For atrium-project#58:** the deliberate red run's `Dockerfile` (a baked `--port 8000` in the `api` ENTRYPOINT,
  `ENV PORT=8000` kept), for a throwaway pull request into `test`, closed unmerged.
* **Checks:** `-k "ct2 or bakeoff"`: 82 passed, 1 skipped; ruff clean.

  Files delivered in chat; pushed as `b546888`.

## 2026-10-07 (evening) — #4: two of the findings of 09-28 that the guard missed
* **The detector reads one more thing.** `processors/quality.py::mixed_script_word`: a word whose letters are both
  Latin and Cyrillic (`Ostrůв`, logged `ok` in the 09-28 `ct2` run) is degenerate for every backend, so it is
  re-requested, flagged, re-run and, if it never recovers, kept as source. A word the source itself contains is
  exempt, other scripts are left alone (`α-křemen`, `ΔT`), and text is compared composed, so a decomposed `ů` does not
  split a word. Calibration on real output: of the 4,264 logged rows of `data_samples/` it rejects one, the `Ostrůв`
  row (the same single word is the only mixed run in 693,902 letter runs of the repo's text files).
* **The re-run no longer repeats the request.** `ct2` decodes deterministically (EuroLLM greedy, NMT beam 4), so a
  flagged segment asked for again after the cool-down returned the same reply. `CT2Translator.retrying(round)`
  (thread-local, because the service shares one backend object) is entered by `utils._translate_one(…, attempt)` from
  both re-run loops. Inside it the beam widens: NMT 4 + 2n (cap 12), EuroLLM beam 2 + 2n in place of greedy.
  Backends without the method are called as before. **Not measured:** whether it recovers more on real EuroLLM
  output. There are no model weights here and the stub engines record only how they are called; that is for the
  bake-off.
* **Still open for the bake-off, unchanged:** "klášter" as "convent", "Terénní rampa" left in the middle of a text,
  the 4 short fields kept in Czech.
* **Checks:** without the new rule 8 tests fail, without the hook 5, without the widening 2; whole suite 1068 passed,
  3 skipped; ruff check and format clean. README, `service/README.md` and `docs/translation-backends.md` say the same.
* **Dev logs:** the pairs of #4 and #46 refreshed.

  Files delivered in chat; pushed as `a9ad269`.

## 2026-10-08 — Digest and plan for #52
* **#52** (an unsupported source language is translated as Czech without telling the caller): read on `ef3b311`.
  A confident detection of a language the backend cannot translate (`la` 0.97, `hu` 0.99 under LINDAT) takes the
  same fallback as an untrustworthy guess and ends as `default` → `cs`, logged `ok`; the only trace is the
  per-document WARNING line. The plan separates the two cases (basis `unsupported`) and, for option (b), keeps the
  source text, logs `untranslated` and notes it in `limits_applied` as `skipped` — the path a degenerate reply
  already takes. Option (a) has no `limits_applied` effect to use without a shared-module change.
* **Dev logs:** `digests/52.digest.md`, `plans/52.plan.md`.

  Files delivered in chat; pushed as `dfbee95`.

## 2026-10-09 — #52 reported (option (a)); `load_vocab.py --from-release`; v1.4.0-beta
* **#52**, option (a): report, still translate from the fallback.
  * `processors/language.py`: `Resolution.unsupported` is the first confident two-letter candidate the backend cannot
    translate, when no accepted candidate was found; the language used is unchanged. `LanguageTally` lists them in
    the log line ("identified as a language the backend cannot translate, translated from the fallback: la×1").
  * `utils.py`: the `_log.csv` status `unsupported_lang` (metadata rows; every line of an ALTO block; an
    `untranslated` row stays `untranslated`) and the record's `translations.unsupported_source_langs`, on `auto` runs.
  * Tests: `tests/test_unsupported_language.py` (7), `tests/test_language.py` (4 new).
* **`load_vocab.py --from-release VERSION [--asset PATH|URL] [--sha256 HEX]`** (atrium-project#72): builds the CSV from
  atrium-keyword-extract's `atrium-vocabulary-<version>.zip` (`amcr_flat.csv`, `teater_flat.csv`), verified against
  the `.sha256` beside it; exit 3 on a mismatch. `tests/test_load_vocab_release.py` (7). `data_samples/vocabulary.csv`
  is not regenerated: from today's flat files it would hold 4,952 pairs instead of 5,087 (159 fewer, 24 new, 11
  retranslated), which is AMČR's call.
* **Docs:** README (language identification, the status table, `--from-release`), `docs/translation-backends.md`.
* `atrium_document.py`, `atrium_document.schema.json`, `service/atrium_service.py`,
  `tests/test_document_originators.py` and `tests/test_schema_freeze.py` re-vendored from the hub;
  `service/openapi.json` regenerated.
* **Version `v1.4.0-beta`:** `CITATION.cff`, the `CONTRIBUTING.md` row, `para_config.txt`, the spec's `info.version`.
* **Checks:** 1102 passed, 3 skipped; ruff check and format clean; spec current; image closure OK.
* **Dev logs:** the pair of #52 refreshed.

  Files delivered in chat; pushed as `860a140`, tagged `v1.4.0-beta` at `05e869c`.

## 2026-10-13 — Shared modules re-vendored (atrium-project#51, atrium-nlp-enrich#41)
* `atrium_rocrate.py`, `atrium_vocab.py`, `atrium_document.schema.json`, `service/atrium_service.py`,
  `tests/test_atrium_rocrate.py` and `tests/test_schema_freeze.py` re-vendored from the hub:
  * the crate names AMČR/TEATER concepts by their source URIs, from `teater_category_ids`, and no longer mints
    `w3id.org/atrium/theme/<label>`;
  * the schema declares `enrichment.items[].teater_category_ids`;
  * the registry's theme maps follow keyword-extract's taxonomy;
  * the shared 502 text says when to retry.
* `service/openapi.json` regenerated (the record schema and its sha, the 502 description). No behaviour change, no
  release.
* **#52's docs, before closing:** `README.md` lists `translations.unsupported_source_langs` and says the vocabulary
  switch now waits on AMČR accepting the asset's list, not on a release; `service/README.md` says where the signal
  is (the record sent back, not `limits_applied`) and that `source_lang=auto` reports it; `main.py`'s status comment
  and `data_samples/README.md` list `unsupported_lang`.
* **Checks:** the API-contract and vendored shared tests, 219 passed; `tests/test_unsupported_language.py` and
  `tests/test_language.py`, 49 passed; the tests that read the READMEs pass; ruff clean.

  Files delivered in chat; pushed as `9a8b722`.

## 2026-10-15 — Issue logs of #4 and #46 consolidated
* **#4:** one Status and one "Where it stands" section; the dated sections collapsed into History.
  * Since 10-07: the two guard changes are released in `v1.4.0-beta`.
  * AMČR's answer on atrium-project#72 (10-13) adds a bake-off arm: the OpenAI-compatible backend as a translation
    alternative to LINDAT, fed the vocabulary with its descriptions. Today it injects bare pairs
    (`processors/llm_translator.py:219-247`), and `/translate` loads no vocabulary (`service/api.py:317`). The plan's
    phase B carries both.
  * The milestone's 15 October date is passed; the thread moved the target to the end of October.
* **#46:** the AMČR recipe is checked on `v1.4.0-beta` (the batch image, `--xsd --xsd-strict`). `live-backend` has
  never been dispatched: `integration.yml` has run on push and pull-request events only.
* **Found:** `data_samples/in-place_translated_files/xml/` holds the `ct2` EuroLLM run `260928-113756` (`0962214`).
  `data_samples/README.md:23` names the LINDAT run `260928-110452`. Both pairs carry the fix: restore the LINDAT
  folder, or let the README name the `ct2` run (#4 plan A.2).
* No code change.

  Files delivered in chat.

## 2026-10-18 — #4 phase B: the curated vocabulary with descriptions, refused pairs, the bake-off kit
* **The vocabulary is keyword-extract's curated list** (atrium-project#72: AMČR, 13 October; K4TEL, 15 October).
  * `load_vocab.py --from-release` keeps only the pairs of the asset's `vocabulary.csv`. Each pair takes the identity of
    the concept that won it in `union_nested.json`; the flat match is used only for the 12 TEATER headings the union does
    not list. That fixes `papír`, `archeolog` and `latina`, whose first flat match is a record keyword-extract excluded.
  * A new optional sixth column, `description`: `note_en`, else `note_cs`, from `*_flat.json`, on one line, cut at a
    word to `--description-chars` (default 500). `processors/vocab.py` reads it **by header name only**, so a
    headerless wider file (keyword-extract's `scheme` sits in column 6) never passes for one.
  * An asset without the curated list is refused (exit 3). A curated pair in no harvest is kept without provenance,
    with a warning.
  * `data_samples/vocabulary.csv` rebuilt from `v1.3.0-beta` (asset sha256 `948d7d37…`): 4,715 pairs (was 5,087;
    396 dropped, 24 added, 18 retranslated, 93 with new provenance), 1,961 with a description.
* **The LLM backend gets the descriptions** (AMČR: "the vocab (including descriptions) as one of the inputs").
  * `LLM_GLOSSARY_DESCRIPTION_CHARS` (a limit, default 0 = none, in `/info`): a matched term with a description reads
    `kostel = church (description: …)`, and the prompt says the description is context only. A longer one is cut at a
    word, with a `trimmed` note. `ct2` never gets descriptions (test).
  * `LLM_EXTRA_BODY`: a JSON object merged into every request. The client's own fields win, and a malformed value
    stops the backend. It is how the bake-off turns Qwen3.8's thinking off.
  * `LLM_LICENSE_COMPONENT`: the `para_config.txt` component recorded for a self-hosted model instead of `llm_api`.
    Qwen3.8's row waits for its licence to be read off the model card in the bake-off prep.
  * `LLMTranslator.describe()` (model, provider, endpoint host, description cap, extra-body keys; never the key) goes
    into the paradata of the CLI and of `/translate`, which recorded no LLM model before.
* **`/translate` can apply a vocabulary:** `VOCABULARY_PATH` (default empty: unchanged). A value that names no usable
  CSV stops the service at startup with the fix in the message. `/info` reports `vocabulary {file, terms,
  with_description, sha256}`, an additive spec change (compare: compatible). Whether the image sets it waits on the
  bake-off.
* **An untranslatable language pair is refused, never rerouted.** `_basic_translate` sent any pair LINDAT lacks to
  `cs-en` without a word: `cs→it` wrote English into `_it.xml`, `hu→en` translated Hungarian as Czech (the defect AMČR
  named in its report of 30 September, item 8).
  * `processors/language.py` `language_pair_problem()`.
  * CLI: exit 1, naming the pairs offered (in `auto` mode it checks the fallback language); also for a missing
    `--vocabulary` and a backend that cannot start.
  * Service: 422 with `reason: null`, `cause: "unsupported_language_pair"` and the offered sources in `errors`. No new
    reason code, no shared module touched.
  * `UnsupportedLanguagePairError` (a `TranslationError`, never per segment) is the backstop. Codes such as `CS` and
    `ces` are read as `cs`.
* **The samples say what they are** (#46, #4 plan A.2). The `ct2` run `260928-113756` moved to
  `data_samples/ct2_translated_files/xml/`; `in-place_translated_files/xml/` is the LINDAT run `260928-110452` again,
  restored from `d8d72b5`. Updated: `data_samples/README.md`, `.dockerignore`, a test comment.
* **`eval/bakeoff.py`, ready for the cluster.**
  * Run options: `--label` (one variant per run), `--kinds`, and `--combine` (merge the runs; the first is the baseline).
  * Outputs: `seconds` and `chars_per_s`, `<out>_by_kind.csv` (the metadata fields and the ALTO blocks are never
    averaged together), and `<out>.meta.json` (what ran: `describe()`, vocabulary sha, commit, warm-up).
  * Changed defaults: `--source_lang` is `cs`, and `auto` is refused; terminology is counted at word boundaries.
* **The bake-off kit (job T1, not committed):**
  * `issue4_bakeoff_prep.sh`, for a networked node: a venv, EuroLLM-1.7B/9B, Qwen3.8-27B and -FP8, Qwen's licence
    printed, the vLLM check.
  * `issue4_bakeoff_job.sh`, the staged Slurm job: LINDAT if reachable; ct2 1.7B and 9B; Qwen3.8 on a local vLLM
    without and with descriptions; combine; the AMČR samples again; the live-backend lane if reachable; a report.
  * Dry-run here with stubs: the skip paths, the markers, combine and report work.
* **Docs:** README (the refusal, `--vocabulary`, the `description` column, `--from-release` on `v1.3.0-beta`),
  `service/README.md` (`VOCABULARY_PATH`, `LLM_EXTRA_BODY`, `LLM_LICENSE_COMPONENT`, the new limit; `UDPIPE_URL`'s row),
  `.env.example`, `docs/translation-backends.md` (the curated vocabulary and descriptions, the refusal; Recommendation
  and next steps rewritten for the bake-off).
* **Checks:** 1158 passed, 3 skipped; ruff check and format clean; spec current; `bash -n` and shellcheck clean on
  both scripts.
* **Not done:** no version bump. v1.5.0-beta follows the bake-off, once immutable releases are on in this repository
  (atrium-project#72). The milestone's date (15 October) is not moved here.

  Files delivered in chat.

## 2026-10-18 (evening) — #4 job T1 read; the runtimes fail fast now; T1b
* **Job T1** (7143997, dll-4gpu1; the round above, pushed as `97a7bb3`/`0a64328`):
  * `lindat`, the baseline: 1,230 segments (37 metadata fields + 1,193 ALTO blocks), 0 failed, terms 229/232, numbers
    0.993, length ratio 1.03, 2,508 s. The live lane (#46): 4 passed, 3 stub-only skips.
  * `ct2` 1.7B and 9B: every segment failed with `libcublas.so.12 is not found` (CTranslate2 4.8.3 needs CUDA 12
    cuBLAS; the venv's torch is CUDA 13). `warm()` had loaded the models, so both stages were marked ok.
  * LLM: vLLM 0.31 refused Qwen3.8's default `max_num_seqs` (1024 against 639 Mamba cache blocks; a hybrid model).
  * Also: a second job (7143998) wrote into the same folder; `test` lacks the LINDAT replace folder its README
    lists; the cluster's vocabulary sha (`1d7af821…`) is the CRLF file's LF form.
* **Fail fast, with the fix:**
  * `CT2Translator.warm()` runs one short generation after the load; a failure is a `TranslationError` naming CUDA
    12's cuBLAS (`nvidia-cublas-cu12` + `LD_LIBRARY_PATH`) or `CT2_DEVICE=cpu`. The CLI now calls `warm()` after the
    pair check (exit 1), as the service does at startup.
  * `eval/bakeoff.py` exits 2 when a variant cannot start, fails `--max-consecutive-failures` (default 10) segments in a
    row with the same error (nothing written), or fails every segment.
  * `load_vocab.py` writes LF (`lineterminator="\n"`); a test pins the committed file.
* **Docs:** the cuBLAS note in `requirements-ct2.txt`, `.env.example`, `service/README.md` and
  `docs/translation-backends.md`; the refused pair's row in `service/README.md`'s error table (atrium-project#77).
* **The LINDAT replace samples** restored from `d8d72b5` again.
* **Job T1b** (scripts re-delivered): prep installs `nvidia-cublas-cu12`; the job takes a lock per `OUT`, puts the
  venv's cuBLAS 12 on `LD_LIBRARY_PATH` (else `CUDA_MODULE`; without it the `ct2` stages are skipped), serves vLLM with
  `--max-num-seqs 64`, clears a label's old outputs, combines only finished runs (`runs/<label>.done`) and records
  Qwen's model-card licence. Re-run: `FORCE=1 ONLY="20 21 30 40 50 90"`.
* **Checks:** 1170 passed, 3 skipped; ruff clean; `bash -n` and shellcheck clean; the job dry-run here with a stub
  interpreter (lock, skip, `.done`, combine).

  Files delivered in chat.

## 2026-10-19 — #4 job T1b read: no glossary ever reached Qwen; ct2 on a Blackwell GPU
* **Job T1b** (7144164, dll-4gpu1, `54d81cf`):
  * LLM arms (Qwen3.8-27B bf16, vLLM 0.31): 99 of 1,230 segments failed in both, exactly the 99 with a
    vocabulary term, each `HTTP 400.`. The glossary went as a second `system` message, which Qwen3's chat template
    refuses, so terms were 0/232 and desc0 equalled desc300. The 1,131 others: numbers 0.999, length ratio 1.01,
    0.96 char similarity to LINDAT, 30 chars/s (LINDAT 20).
  * `ct2` 1.7B/9B: `CUBLAS_STATUS_NOT_SUPPORTED` on every segment; the job had fallen back to `module cuda/12.3`,
    whose cuBLAS predates Blackwell. The bake-off's guard stopped both after 10 identical errors and wrote nothing
    (exit 2). `warm()`'s 4-token probe had passed.
  * Samples: the same; the LINDAT part never ran (the stage stopped at the ct2 failure).
  * Qwen3.8-27B and -FP8: `license: apache-2.0` on the model card.
* **Fixes:**
  * `LLMTranslator._build_messages`: one system message, instructions then glossary (new test);
  * `processors/http_retry.provider_message`: a refusal names what the server said (`error.message`, `detail`,
    or the body's start, 300 characters), for LINDAT too (tests);
  * `CT2Translator._probe`: 4 tokens of a record-sized Czech sentence built as a translation's (EuroLLM: the chat
    template and the end tokens); `NOT_SUPPORTED` names the Blackwell fix (cuBLAS ≥ 12.8, an Ampere/Hopper GPU, or
    `CT2_DEVICE=cpu`) (tests);
  * docs: `requirements-ct2.txt`, `docs/translation-backends.md` (the GPU note, the next steps).
* **T1c** (scripts re-delivered): the samples stage runs LINDAT first, so the missing
  `in-place_translated_files/xml/` is regenerated; `code_state` records the GPU's compute capability. Submitted in
  two parts: the LLM arms on dll-4gpu1, then `ct2` and the samples on an Ampere GPU.
* **Checks:** 1177 passed, 3 skipped; ruff clean.
* **Dev logs:** `{4}` (T1b, T1c).

  Files delivered in chat.

## 2026-10-19 (evening) — #4: the bake-off decided; v1.5.0-beta prepared; #46's sample folder
* **Jobs T1c (7147048) and T1d (7147059)**, translator `a048708` on the cluster, with R2c on `test` (`a32d5bf`):
  * LLM arms (Qwen3.8-27B bf16, RTX PRO 6000): 1,230 segments, 0 failed, terms 227/232 (desc0) and 226/232
    (desc300), numbers 0.997, 0.944 similarity to LINDAT, 57 chars/s. Descriptions change 20 of 1,230 outputs.
  * `ct2` first landed on a Quadro P5000 (Pascal: no `int8_float16`) and refused at start; on the RTX A4000:
    EuroLLM-9B 96 failed (89 of them ALTO blocks of at most 10 characters), terms 157/230, 49 chars/s; 1.7B 584
    failed. The job now picks the compute type the GPU offers and stages each sample set until its run succeeds.
  * LINDAT (T1, 18 October): 0 failed, 229/232, 20 chars/s.
* **Decision** (`docs/translation-backends.md`): `lindat` stays the default; `openai_compatible` with Qwen3.8-27B
  self-hosted is the alternative (Apache-2.0: new `qwen3_8` component); EuroLLM is not recommended; descriptions
  off; `VOCABULARY_PATH` unset by default. The result table is in `eval/results/issue4_bakeoff_2610/` (all five arms).
* **Samples** (pushed as `355c709`): both AMČR replace folders regenerated with the curated vocabulary (LINDAT
  `261019-101714`, 37 `ok`; `ct2` `261019-101905`); `data_samples/README.md` follows. #46's folder is the
  production shape again. A failed sample run copied into `test` (`a048708`) had emptied the `ct2` logs; T1d's set
  replaced them. Four stale paradata files are to be removed.
* **v1.5.0-beta** prepared: `para_config.txt`, `CITATION.cff`, the spec (version only; compatible with
  v1.4.0-beta), the CONTRIBUTING row; a Known limits section in `service/README.md` (atrium-project#77 B).
  Immutable releases were switched on here the same day (atrium-project#72, @stranak), so v1.5.0-beta is this
  repository's first immutable release; v1.4.0-beta stays mutable.
* **Checks:** 1,177 passed, 3 skipped; ruff clean; `check_version` agrees.
* **Dev logs:** `{4,46}`.

  Files delivered in chat.

## 2026-10-19 (late) — Two doc statements corrected after the release
* `docs/translation-backends.md` Recommendation: Qwen3.8-27B "comes close to" LINDAT (terms 227 against 229 of
  232), not "level with" it.
* `README.md` (§ the service's replace example): the service applies a vocabulary when `VOCABULARY_PATH` is set
  (`v1.5.0-beta`); the sentence still said it never does.
* Docs only, no release. **Checks:** 1,177 passed, 3 skipped.

## 2026-10-21 — FastText in the image at a pinned revision; the vocabulary pin; #46's stale paradata
* **atrium-project#77 A.3:** `processors/identifier.py` reads `LANG_ID_MODEL_PATH`; without it, it downloads
  `facebook/fasttext-language-identification` at revision `3af127d…`, not at `main`. The `Dockerfile` downloads that
  file at build time into `/opt/models/fasttext-lid.bin`, checks its size and sha256, and sets
  `LANG_ID_MODEL_PATH`. `.env.example` and `service/README.md` (the runbook sections) say so. 3 tests in
  `tests/test_identifier.py`.
* **atrium-project#82 C.3:** `tests/test_vocabulary_pin.py` pins the bundled vocabulary to keyword-extract's
  `v1.3.0-beta` asset (owner, release, asset and CSV sha256) and checks the README cites it. keyword-extract has
  `v1.4.0-beta` since: newer upstream is advice, not a defect.
* **#46:** the four paradata files of earlier sample runs are removed from `data_samples/`.
* **Checks:** 1,175 passed on Python 3.11. The image was not built (no Docker in this session).

---

## 2026-10-21 (round 2) — #46's paradata again; the agent-skill branch (atrium-project#77 C)
* The four stale paradata files of the previous entry are still on `test`; deleted again (listed in chat).
* **The agent-skill branch:** the client retries a 502 only when it is not final and exits 1 on a usage error;
  `scripts/server.sh` waits for `/ready`; `SKILL.md` says which 502 is retried; `service/README.md` is this repo's
  runbook (the tests pointed at `test`, hub paths as links). **Checks:** skill-validate steps 1–4a; drift check
  aligned. Delivered as `atrium-translator-agent-skill.zip`, not pushed.

*Timeline index refreshed 2026-09-28 (2026-09-27/28 entries, header); 2026-09-26 (AMČR baseline entry and header); earlier 2026-09-13 against live `test` HEAD. Entries through 2026-09-07 were verified against the
`CONTRIBUTING.md` changelog table and open-issue state via the GitHub API. Nothing removed from the issue itself
(per hub #29); this file is a derived reading aid in `agent_dev_logs/`.*
## 2026-10-21 (round 3) — The skill branch carries the vocabulary; #52's pair removed
* **The agent-skill branch carries what the image carries** (atrium-project#77): `data_samples/vocabulary.csv`
  (the runbook's "bundled" vocabulary) and `data_samples/my_documents/` with the README and LICENSE, 19 files; the
  sample outputs stay off, as in the image. The hub's `skill_ify.py` now follows `.dockerignore`.
* `plans/46.plan.md`: the four stale paradata files were removed on 10-21 (`c6f844d`). The closed #52's pair is
  removed.

## 2026-10-22 (round 4) — The daily LINDAT check (atrium-project#78); #46 to close
* **`lindat-daily.yml`** (new, its own concurrency group): every day `eval/lindat_probe.py` sends one Czech sentence
  24 times (12 fresh connections, 12 over one session) and fails on any bad reply; a scheduled failure opens or
  updates the lane's issue through the hub's `lane-failure-issue` action. It installs only `requests`.
* **`eval/lindat_probe.py`:** `--max-bad N` gives a failing exit (without it the probe only reports, as before);
  `content_reason()` adds what the guard cannot check without the languages: every number kept, not the source
  repeated, no Czech-only letter left in the reply; `--text daily` is `DAILY_TEXT` ("Výzkum v roce 1932 odkryl 3 hroby
  a 120 střepů."). 8 new tests (19 in the file). LINDAT is unreachable from the sandbox, so a first live run is the
  user's.
* **#46:** motyc agrees to close once atrium-project#77 records AMČR's metadata acceptance test; the hub's acceptance
  runbook has it now. Dev logs `{46}` updated.
* **Also:** `scheduled-smoke.yml` uses the lane notifier (atrium-project#81); `atrium_vocab.py` and its tests re-vendored
  (atrium-project#51).
* **Checks:** 1,197 passed, 3 skipped (Python 3.11); `workflow_lint.py` and actionlint clean.

## 2026-10-25 — The documentation on the skeleton, the hub's section folded back (atrium-project#79); #46 to close
* **Shared modules (hub re-vendor, atrium-project#13, #6, #79):** `atrium_rocrate.py` writes David Novák's
  correct ORCID (`0000-0001-6592-6291`) as a tool creator in every crate; `atrium_document.schema.json` describes
  `provenance.license*` as calculated from the components, not the output's licence (descriptions only, no
  `POST_FREEZE_CHANGES` entry); an `atrium_vocab.py` comment names page-classification's new categories page.
  `service/openapi.json` regenerated: description-only, and the `x-atrium-record-schema` digest.
* **`CITATION.cff`:** the ORCID corrected; each author's affiliation in its ROR name (Charles University;
  Czech Academy of Sciences, Institute of Archaeology, Prague). Authors unchanged until they confirm (#13).
* **atrium-project#81:** `release.yml` gains the `image` job (the hub's `docker-tool.reusable.yml` with this
  repository's `docker.yml` inputs and `release-gate: true`), the `simulate-probe-failure` dispatch input, and
  `images.json` in the release; `docker.yml` no longer runs on tags. It takes effect at the next tag, after
  digital-convert's dispatched demonstration. No version bump.
* **atrium-project#79:** README 1,230 → 164 lines on the hub's template; the rest, the hub's five
  `tools/translator/` pages and the backend evaluation moved into `docs/` (usage, configuration, how-it-works,
  development, reference/, evaluation/, decisions/); `docs/translation-backends.md` stays as a pointer, since
  code, config and samples name it. Map: the hub's `plans/79.translator_relocation_map.md`. The licence text
  follows #6 (a calculated summary, not the translation's licence). CONTRIBUTING: plain headings, the release
  arcs from the hub's changelog, the table's rows and columns as before.
* **#46:** the closing comment is drafted: AMČR's metadata run is in atrium-project#77's runbook.
* **Checks:** 1,189 passed, 3 skipped (a `fasttext` stand-in, which does not build in the sandbox); docs_check,
  runbook_check OK.

## 2026-10-25 (evening) — CONTRIBUTING links the two checklists; the documentation check in CI (atrium-project#79, #81)
* **CONTRIBUTING (atrium-project#79, #81):** the "Repository Documentation Management" section links the
  hub's documentation style guide and `ci_policy.md#changing-ci`, the questions a new or changed CI check
  answers, as the hub's CONTRIBUTING template does. Nothing else in the file changed.
* **The documentation check in CI (atrium-project#79 E):** Workflow Policy Lint runs the hub's
  `tools/docs_check.py` over this repository, advisory until the newcomer review, from the pin to the hub commit
  that carries it (atrium-project#72 step B). It passes today.
* **Quick start:** every endpoint, field, flag and sample file it shows exists at `v1.5.0-beta`, the release it
  names (a static check; running it needs Docker and the Hugging Face Hub or LINDAT).
* **atrium-project#81:** the release gate gets one clean dry run (`release.yml` dispatched on `test`)
  before the next tag.
* **#46** closed on 10-25 (AMČR's metadata run is an acceptance test of atrium-project#77's runbook);
  with #4 (10-19) and #52 (10-13) closed, the repository has no open issue.

## 2026-10-25 (night) — The hub's reusables by commit (atrium-project#72)
* **Hub by commit (atrium-project#72 E.4):** every hub reusable caller (8) and hub-action step (2) runs at
  `@18f06680dfb6…  # v1`; `para-drift` and `workflow-lint` read the hub at the same `hub-ref`, and the lint has
  `require-sha-pins: true`, so a caller or action left at `@v1` fails it. A hub change reaches this repository only
  through a pin move (`scripts/pin_hub_reusables.py` in the hub).
* **The documentation check (atrium-project#79 E)** comes with this hub commit: Workflow Policy Lint now runs
  `docs_check.py` over this repository, advisory.
* **#46:** its export left `issues/` with the closing (the maintainer's refresh); the digest and plan are marked
  closed and stay, because `utils.py`, `tests/test_output_mode.py` and `docs/decisions/history.md` cite the digest.
* **`docs/decisions/history.md`:** its link to `agent_dev_logs/issues/` broke when that folder emptied (git keeps no
  empty folder), which `docs_check` reported; it now says the exports cover open issues and links the closed ones on
  GitHub.
