# History: why the translator is shaped the way it is

For contributors and reviewers who want the reasons behind the current design, in the order things happened. It is
condensed from [agent_dev_logs/DEVLOG.md](../../agent_dev_logs/DEVLOG.md), itself a timeline index over the issue
exports, digests and plans in [agent_dev_logs/](../../agent_dev_logs/), which remain the source of truth; those raw
records are linked, not repeated. The release-by-release view is the Release History in
[CONTRIBUTING.md](../../CONTRIBUTING.md#release-history); the history across the six tools is the hub's
[development history](https://ufal.github.io/atrium-project/development-history/). Moved here from the hub's
translator section (written at v1.2.1-beta) on 2026-10-25 and brought up to date (atrium-project#79).

On this page: [2026-06, choosing a model](#2026-06-choosing-a-translation-model-4) ·
[2026-06 to 07, ALTO output](#2026-06-to-07-making-alto-output-structurally-honest) ·
[2026-09, replace or append](#2026-09-replace-or-append-and-trusting-the-output-46) ·
[2026-09, twelve-factor work](#2026-09-twelve-factor-work-driven-from-the-hub) ·
[the base image](#the-base-image-that-blocked-a-release) ·
[five inert contracts](#2026-09-13-five-contracts-that-were-announced-but-inert) ·
[2026-09 to 10, production standards](#2026-09-to-10-production-standards-for-the-pilot) ·
[2026-10, the bake-off](#2026-10-the-bake-off-and-what-followed-4) · [Branches](#branches) ·
[The raw records](#the-raw-records)

## 2026-06: choosing a translation model (#4)

The repository opened with one question: which base model should do the translating? A candidate comparison was
posted within a day, CUBBITT as the incumbent baseline against Cohere Command A, GLM-5.2, MADLAD-400, NLLB-200,
Tower+, Opus-MT and the commercial APIs, scored on language coverage, licence and cost. EuroLLM was added as a
self-hostable option, and the whole was written up as a phased plan with a full licensing recipe.

What came out of it was an architecture rather than a single answer: a pluggable `TranslationBackend` interface with
`lindat` and `openai_compatible` registered, and a CTranslate2 backend for self-hosted models whose heavy
dependencies load only when it is first used. The choice between models was left to measurement, the
`eval/bakeoff.py` harness, so that CUBBITT could stay the default until another backend was shown to do better on
this material ([the decision record](backend-choice.md)).

## 2026-06 to 07: making ALTO output structurally honest

The hard problem in this tool is not translation; it is that ALTO stores text *spatially*. Each `TextLine` holds a
sequence of `String` elements, and each `String` carries one token plus its position on the page. Translating line
by line loses the context across lines and gives poor output; translating the block gives fluent text and discards
the structure that has to be kept.

The resolution, the [dual-pass reconstruction](../how-it-works.md#alto-dual-pass-reconstruction) of v0.5.0, is to
do both and to use the second pass only as a ruler. It is the most consequential design decision in the repository,
and the reason some output boxes end up empty or holding several words: the word-to-box correspondence in a
translated ALTO is *constructed by the bucketing*, not observed.

That fact shaped the first `append` for ALTO: a per-`String` English alternative would not be an alternative reading
of that word, only whichever token the bucketing happened to land there, so v1.1.0-beta labelled the block instead,
and overwrote `CONTENT`, which lost the scanned text. v1.2.0-beta reversed that: the scanned `CONTENT` stays, and each
`String` gains an `ALTERNATIVE` saying which English landed on that box, the same value `replace` writes, read the
same way, line by line ([the decision record](output-mode.md)).

v0.8.0 then moved the requests from per-block to per-page batches, which cut their number by about twenty times.

## 2026-09: replace or append, and trusting the output (#46)

Issue #46 asked two things at once: run the tool in production, and decide whether English should **replace** the
Czech or be **appended** beside it. The design answer was a switch rather than a verdict,
`--output-mode replace|append` with `replace` by default, so that a real corpus, not an argument, would decide.

The first refresh of the shipped samples against the live service then showed why output has to be checked, not
trusted. Some replies came back as one Czech word repeated to a length cap: deterministic for a given input, cured
by asking again, the signature of one faulty server behind a load balancer. Nothing looked at reply content, so the
garbage reached the documents and, through the never-logged line anchors, the alignment of whole pages. v1.2.0-beta
answered with a guard on every reply, a re-run at the end of the document, and the rule that what never recovers
keeps its source text and is marked in the log ([LINDAT's replies](../evaluation/lindat-replies.md)). The same refresh
exposed FastText's guesses at rare languages for short OCR blocks, which is why detection now falls back rather than
guesses.

A production-readiness review before the run found three more things, fixed in v1.2.1-beta: document records were
written before the backend's components were recorded, so a one-page pipeline stage understated its components;
`--xsd` could not load the AMČR schema at all; and the published API image was named wrongly in the documentation.
With `--xsd` working, the schema answered the question the switch had left open: AMČR 2.2 accepts `replace` output
and rejects `append` output for the free-text fields. ALTO in either mode is valid ALTO. Where an AMČR record must
validate it is `replace`, and `replace` became AMČR's production mode.

## 2026-09: twelve-factor work, driven from the hub

Between 2026-09-07 and 09-12 a run of commits landed against hub issues rather than this repository's own tracker:
the `api` Dockerfile stage, so that a runnable API image exists to publish; `atrium_rocrate.py` vendored under drift
control; `$PORT` honoured rather than baked into the entry point; `TRANSLATION_URL` and `UDPIPE_URL` made
attachable; the first `.env.example`; and the logging contract (one `basicConfig`, in `__main__` only). The same work
landed in every tool at once, which is why it is tracked centrally.

## The base image that blocked a release

On 2026-09-13 a release was stopped by something the build *inherited* rather than anything it contained.

`python:3.11-slim` is a floating tag, and at the time no repository in the ecosystem declared a `docker` Dependabot
ecosystem to bump it, so the base layer was whatever Docker Hub had last rebuilt. That day it carried `perl-base`
5.40.1-6 with three fixable CRITICAL CVEs (CVE-2026-13221, CVE-2026-42496, CVE-2026-8376), all already fixed
upstream.

The hub's release gate blocks exactly that class. And because the promotion step runs `if: success()`, **a blocked
release still publishes, by digest only, with no `:<version>` and no `:latest` tag.** That is what happened to
v1.0.0-beta, on both matrix targets.

Two things made it hard to see, and both generalise:

- **The gate runs only on tags.** The identical commit passes on `master` and on `test`; the failure appears at the
  worst possible moment and never during development.
- **A cache-served apt layer looks correct and patches nothing.** The build uses `cache-from: type=gha`, so the
  `apt-get upgrade` has to sit *below* the `ENV` block embedding `ATRIUM_RUNNER_REF`, which CI passes as
  `github.ref_name`, unique per release tag: that busts the layer on every release, and only on a release.

v1.1.0-beta released green on 2026-09-14 with the fix. **This repository was the only one that had it**, and two
days later atrium-nlp-enrich hit the identical three CVEs for the identical reason. The layer has since been ported
to the other four repositories, and `tests/test_dockerfile_security_layer.py` promoted to a canonical shared file, so
a repository that loses the ordering goes red *before* a release rather than during one.

## 2026-09-13: five contracts that were announced but inert

A production-readiness review, not a filed issue, found five defects that the test suite could not see, because each
lived somewhere it was structurally not looking:

| Defect                                     | What was actually happening                                                                                                                                                                                                                                |
|--------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **The retry policy was not configuration** | `http_retry.py` clamped its arguments *upward*, so `LINDAT_MAX_RETRIES` and its `LLM_*` twins were read and discarded. The effective policy was 11 attempts backing off to **2,046 s for one failing chunk**, unreachable against a 20-second drain window |
| **413 was unreachable**                    | `/translate` read the whole upload before checking its size, for exactly the inputs the limit existed to refuse                                                                                                                                            |
| **The CLI always exited 0**                | every failure path was a bare `return`; a Kubernetes `Job` reported success for a run that translated nothing                                                                                                                                              |
| **A failed FastText load was swallowed**   | `detect()` then answered `("en", 0.0)` for every document while the service reported itself healthy: the failure mode of an egress-restricted cluster specifically                                                                                         |
| **The release zip could not start**        | it omitted `atrium_document.py` and `service/atrium_service.py`, both imported by the entry points: `ModuleNotFoundError` on the primary download path for anyone not using the container                                                                  |

Each was a *declared* behaviour that no test exercised end to end. The retry limits were read and then clamped away.
The upload limit was checked after the read it was supposed to prevent. The exit codes existed in the documentation
and nowhere in the control flow. The release zip was assembled from a list nobody had re-derived from the imports. In
each case the documentation was right about the intent and wrong about the system.

The fix in each case was a guard, and each guard was confirmed by reintroducing the defect and watching it go red,
which is the standard the ecosystem holds itself to and the reason these are recorded rather than quietly patched.
v1.0.0-beta shipped them; **breaking:** the batch CLI exits non-zero on failure.

The same round closed the live-backend gap that `scheduled-smoke.yml` had named in its own docstring since August,
moved the last 3.12 CI lane to 3.11, checked that `.env` reaches the container by rendering `docker compose config`,
and brought Docker, the API, the environment and deployment into the README.

## 2026-09 to 10: production standards for the pilot

- **v1.2.2-beta (09-28).** The AMČR run's command line was fixed (`--xpaths` without `--formats` is a metadata run;
  the shipped `formats = alto.xml` had made it exit 2), and the `--xsd` verdict is logged again. AMČR declared its
  vocabularies CC0 (atrium-project#6). Every limit became an environment setting reported in `/info`, and
  `/translate`'s errors took the shared `{status, reason, detail}` body. `entities[].translation_en` was reserved
  (atrium-project#70).
- **v1.3.0-beta to v1.3.2-beta (09-29 to 10-07).** The shared service standards: health and readiness, graceful
  shutdown, versioned OpenAPI assets with a breaking-change check, reproducible release bundles; then the shared
  record: a production-image declaration, and each call's `CreateAction` returned as `paradata` (atrium-project#71).
- **v1.4.0-beta (10-09).** Source languages identified with confidence but not translatable are reported, not
  hidden (#52); `load_vocab.py --from-release` reads atrium-keyword-extract's vocabulary release (atrium-project#72);
  a word mixing Latin and Cyrillic letters counts as degenerate output.

## 2026-10: the bake-off and what followed (#4)

- **10-18.** The curated vocabulary with descriptions (4,715 pairs, from keyword-extract `v1.3.0-beta`); an
  explicit language pair the backend cannot translate is refused instead of being sent through `cs-en`; the bake-off
  kit (`--label`, `--combine`, per-kind summaries, a stop after repeated identical failures).
- **10-19.** The cluster jobs ran. The first LLM run had sent the glossary as a second system message, which Qwen3's
  chat template refuses (every segment with a term failed with HTTP 400), so the glossary became one system message;
  the first `ct2` run had fallen back to CUDA 12.3, whose cuBLAS predates Blackwell, so `warm()` now runs a
  record-sized probe and names the fix. The final jobs decided it: LINDAT stays the default, Qwen3.8-27B through
  `openai_compatible` is the self-hosted alternative, EuroLLM is not recommended
  ([the decision](backend-choice.md), [the evidence](../evaluation/backends.md)). v1.5.0-beta was released the same
  day, this repository's first immutable release.
- **10-21.** #4 closed. The images carry the FastText model at a pinned revision, checked by SHA-256 at build
  (`LANG_ID_MODEL_PATH`, atrium-project#77); the bundled vocabulary is pinned to its release asset by
  `tests/test_vocabulary_pin.py` (atrium-project#82).
- **10-22.** A daily check of the LINDAT endpoint, `lindat-daily.yml` (atrium-project#78). AMČR agreed that no tool
  makes an automatic licence assertion about outputs: the `license` field is a summary of the components
  (atrium-project#6, [licences](../reference/licences.md)).
- **10-25.** The documentation was reorganised by layer, and the hub's translator section folded into this
  repository (atrium-project#79).

## Branches

| Branch        | What it is                                                                                                                         |
|---------------|------------------------------------------------------------------------------------------------------------------------------------|
| `master`      | the default branch and the released code; merged by maintainers only                                                               |
| `test`        | the integration branch that changes are staged on; every pull request targets it                                                   |
| `main`        | the repository's initial commit, not the default branch and not used for development                                               |
| `agent-skill` | a reduced repackaging for coding agents, with a `SKILL.md`, a stdlib-only client and a web frontend that exists on no other branch |
| `gh-pages`    | the landing card at [ufal.github.io/atrium-translator](https://ufal.github.io/atrium-translator/)                                  |

## The raw records

- [agent_dev_logs/DEVLOG.md](../../agent_dev_logs/DEVLOG.md): the timeline index this page condenses.
- [agent_dev_logs/digests/4.digest.md](../../agent_dev_logs/digests/4.digest.md): the backend choice;
  [46.digest.md](../../agent_dev_logs/digests/46.digest.md): replace or append.
- [agent_dev_logs/plans/](../../agent_dev_logs/plans/): the plans. The verbatim exports in `agent_dev_logs/issues/`
  cover open issues only, and this repository has none since #46 closed; the closed issues are on
  [GitHub](https://github.com/ufal/atrium-translator/issues?q=is%3Aissue+is%3Aclosed).
- [evaluation/backends.md](../evaluation/backends.md): the nine-candidate comparison behind #4.
