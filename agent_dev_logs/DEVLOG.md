# 📓 atrium-translator — agent_dev_logs/DEVLOG.md (timeline index)
> _XML in-place translation. 2 open issues (#4, #46). `test` HEAD `03fc15d` (2026-09-15) · **v1.1.0-beta**,
> released green after the base-image security fix (see 2026-09-13 below). Twelve-factor detail lives in the hub:
> `ufal/atrium-project/agent_dev_logs/{digests,plans}/53.*` — the `digests/12factor.*` / `plans/12factor.*` this
> line used to cite were never written._
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

---

*Timeline index refreshed 2026-09-13 against live `test` HEAD. Entries through 2026-09-07 were verified against the
`CONTRIBUTING.md` changelog table and open-issue state via the GitHub API. Nothing removed from the issue itself
(per hub #29); this file is a derived reading aid in `agent_dev_logs/`.*
