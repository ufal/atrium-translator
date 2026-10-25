# Licences of the components a run uses

For anyone who publishes or reuses the translator's output, or chooses a backend with licences in mind. It answers
which components a run uses, what licence or terms each comes under, and what the `license` field of the paradata
and the document record means and does not mean.

On this page: [What a run records](#what-a-run-records) · [The components](#the-components) ·
[A default run](#a-default-run) · [A run with only permissive components](#a-run-with-only-permissive-components) ·
[The candidate backends](#the-candidate-backends)

## What a run records

The code is MIT ([LICENSE](../../LICENSE)). Each run records the components it used, the models, data and web
services, each with its own licence or, for a web service, the terms of use it is offered under. The list is in the
paradata's `license_detail.components` and in each document record's `provenance.license_detail`
([paradata.md](paradata.md)).

From that list the shared module [para_licenses.py](../../para_licenses.py) computes one more field, `license`: the
most restrictive of the component licences. **That field is a calculated summary of the components, not the licence
of the translation.** A licence covers what it is attached to, a program, a model or a dataset; a web service such
as LINDAT has terms of use, not a licence for its output; and the rights in an output depend first on the source
document, which no tool knows. They are stated by whoever holds them: for the AMČR pilot, AMČR states the rights of
the outputs it publishes (agreed on 22 October 2026, [atrium-project#6](https://github.com/ufal/atrium-project/issues/6)),
and no tool makes an automatic or default licence assertion. Until the shared modules stop stating the summary as a
licence (the `notes` sentence still says "Effective output license"), read `license` as "the most restrictive licence
among the components used", nothing more. The hub explains it without code:
[Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/).

## The components

[para_config.txt](../../para_config.txt) declares each component, its licence and whether it counts in every run
(`always`) or only when the run uses it (`conditional`). All of the translator's are conditional:

| Component              | Licence or terms                                                                                                                  | Recorded when                                                                        |
|------------------------|-----------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `fasttext`             | CC BY-NC 4.0 (the language-identification weights)                                                                                | the source language is detected (`auto`); an explicit `--source_lang` never loads it |
| `lindat_cubbitt`       | the LINDAT service, under the [LINDAT Terms of Use](https://lindat.mff.cuni.cz/en/terms-of-use); CUBBITT's models CC BY-NC-SA 4.0 | the `lindat` backend is used                                                         |
| `udpipe2_engine`       | MPL 2.0                                                                                                                           | LINDAT applies a vocabulary: single words are matched by their UDPipe lemma          |
| `udpipe2_models`       | the LINDAT UDPipe service, under the same terms; its models CC BY-NC-SA 4.0                                                       | as above                                                                             |
| `amcr_vocab`           | CC0                                                                                                                               | a vocabulary with AMČR terms is applied                                              |
| `teater_data`          | CC0                                                                                                                               | a vocabulary with TEATER terms is applied                                            |
| `llm_api`              | "LLM provider ToS": the provider's terms of service                                                                               | `openai_compatible` is used without `LLM_LICENSE_COMPONENT`                          |
| `qwen3_8`              | Apache-2.0 (Qwen3.8-27B and -FP8, both model cards)                                                                               | `openai_compatible` with `LLM_LICENSE_COMPONENT=qwen3_8`                             |
| `ctranslate2`          | MIT                                                                                                                               | the `ct2` backend is used                                                            |
| `eurollm`, `madlad400` | Apache-2.0                                                                                                                        | a `ct2` model of these families                                                      |
| `nllb200`              | CC BY-NC 4.0, **non-commercial**                                                                                                  | a `ct2` NLLB-200 model                                                               |
| `opus_mt`              | CC BY 4.0                                                                                                                         | a `ct2` Opus-MT model                                                                |

Notes on the table:

- **The AMČR and TEATER vocabularies are CC0,** as their rights holder AMČR stated on 2026-09-28
  ([atrium-project#6](https://github.com/ufal/atrium-project/issues/6#issuecomment-5867861653)). They were labelled
  CC BY-NC 4.0 before, after the OAI-PMH `Identify` response for the whole repository; paradata written before that
  date says so. Loading the vocabulary therefore adds no restriction. With LINDAT, though, Tag-and-Protect brings
  UDPipe's CC BY-NC-SA models.
- **`llm_api` carries an unrecognised licence string on purpose.** The terms of an arbitrary language-model API are
  the provider's and not a CC or open-source licence, so `para_licenses` treats it as the most restrictive (ranked
  highest, with the non-commercial and share-alike flags set) and warns. That is the safe default for a backend
  whose provider terms nobody has recorded. For a model the deployment serves itself and whose licence is listed in
  para_config.txt, set `LLM_LICENSE_COMPONENT` to that component, such as `qwen3_8`.
- **Each `CT2_MODEL_FAMILY` maps to one model component**, logged by the `ct2` backend with `ctranslate2`; a family
  without one would be logged as `UNKNOWN`, which is treated the same way as `llm_api`.
- **Opus-MT is recorded at the conservative end of its split:** the `opus-mt-tc-big-*` checkpoints are CC BY 4.0, the
  older ones (such as `opus-mt-cs-en`) Apache-2.0. Both rank as permissive, but CC BY adds an attribution duty.
- **The record states the same components as the run.** The backend's components are recorded before a document
  record takes its licence block, so a one-file run (one pipeline stage, one `/translate` call) lists the same as a
  batch. Before v1.2.1-beta the record of such a run named only FastText, or nothing.
- When records from several tools are merged (`merge_paradata_files()`), the merged record lists the union of the
  components all of them used.

## A default run

A run with the `lindat` backend lists `lindat_cubbitt`; with a vocabulary also `udpipe2_engine`, `udpipe2_models`,
`amcr_vocab` and `teater_data`; with `source_lang = auto` (the shipped config.txt) also `fasttext`. Its computed
summary is CC BY-NC-SA 4.0, because CUBBITT's and UDPipe's models are CC BY-NC-SA and FastText's weights CC BY-NC.
AMČR's use of the default backend waits on LINDAT's written permission
([the backend decision](../decisions/backend-choice.md)).

## A run with only permissive components

A run whose components are all under permissive licences has to be assembled deliberately, and needs both:

1. **A permissively licensed backend.** The recommended one is Qwen3.8-27B, self-hosted, through
   `--backend openai_compatible` with `LLM_LICENSE_COMPONENT=qwen3_8` (Apache-2.0). A `ct2` model of the EuroLLM or
   MADLAD-400 (Apache-2.0) or Opus-MT (CC BY 4.0) family qualifies too, **not** NLLB-200, whose weights are CC BY-NC
   4.0. A hosted API through `openai_compatible` stays `llm_api`, an unrecognised licence, until the provider's actual
   terms are recorded for it in para_config.txt.
2. **An explicit `--source_lang`,** so the CC BY-NC FastText model is never loaded and never listed.

The AMČR and TEATER vocabularies may stay: they are CC0. With `ct2` EuroLLM, a vocabulary and an explicit
`--source_lang`, the summary is MIT (the engine; the model is Apache-2.0). Check the `license_detail` block of the
run's paradata: it lists which components were used and how the summary was computed. This chooses components; it
does not decide the rights in the translated documents, which depend on the source ([above](#what-a-run-records)).

## The candidate backends

How each backend considered in issue #4 would change the computed summary, for information
([the candidate survey](../evaluation/backends.md#the-candidate-survey-june-2026)):

| Backend                             | Component licence  | Summary if used alone | With UDPipe (CC BY-NC-SA)   |
|-------------------------------------|--------------------|-----------------------|-----------------------------|
| LINDAT/CUBBITT                      | CC BY-NC-SA 4.0    | CC BY-NC-SA 4.0       | CC BY-NC-SA 4.0             |
| Cohere Command A (research weights) | CC BY-NC 4.0       | CC BY-NC 4.0          | CC BY-NC-SA 4.0             |
| GLM-5.2                             | MIT                | MIT                   | CC BY-NC-SA 4.0             |
| MADLAD-400                          | Apache-2.0         | Apache-2.0            | CC BY-NC-SA 4.0             |
| EuroLLM                             | Apache-2.0         | Apache-2.0            | CC BY-NC-SA 4.0             |
| NLLB-200                            | CC BY-NC 4.0       | CC BY-NC 4.0          | CC BY-NC-SA 4.0             |
| Opus-MT                             | CC BY 4.0          | CC BY 4.0             | CC BY-NC-SA 4.0             |
| Qwen3.8-27B                         | Apache-2.0         | Apache-2.0            | CC BY-NC-SA 4.0             |
| DeepL API                           | proprietary (paid) | per agreement         | per agreement + CC BY-NC-SA |

A glossary-native backend that retires Tag-and-Protect also removes the UDPipe dependency, so a permissive summary
needs both the backend and the lemmatiser replaced, or the vocabulary feature disabled. The language-model backends
(`openai_compatible`, `ct2` EuroLLM) take the vocabulary in the prompt and call no UDPipe.
