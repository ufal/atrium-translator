# 📓 atrium-translator — agent_dev_logs/DEVLOG.md (timeline index)
> _XML in-place translation. 1 open issue (#4). `test`==`master` HEAD `dae197a` (2026-09-03) · **v0.10.5**._
> _Per-issue detail: `digests/4.digest.md` · `plans/4.plan.md` · `issues/` export (source of truth). Cross-repo/hub
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

---

*Timeline index refreshed 2026-09-07 against live `test`/`master` HEAD, the `CONTRIBUTING.md` changelog table, and
open-issue state via the GitHub API. Nothing removed from the issue itself (per hub #29); this file is a derived
reading aid in `agent_dev_logs/`.*
