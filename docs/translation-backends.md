# Translation backends: where this page went

For readers who followed a link or an error message here. Until 2026-10-25 this page held the backend evaluation and
design of issue [#4](https://github.com/ufal/atrium-translator/issues/4). Its content now lives by kind
(atrium-project#79); this page stays because error messages, `.env.example`, `para_config.txt` and
`data_samples/README.md` point to it.

| You are looking for                                                             | It is now in                                                                                                                                     |
|---------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| which backend to use, and how to configure `openai_compatible` or `ct2`         | [configuration.md](configuration.md#the-backend-and-its-endpoints) and [how-it-works.md, The three backends](how-it-works.md#the-three-backends) |
| the recommendation: LINDAT the default, Qwen3.8-27B the self-hosted alternative | [decisions/backend-choice.md](decisions/backend-choice.md) ([below](#recommendation))                                                            |
| the bake-off, its result and the suggested next steps                           | [evaluation/backends.md](evaluation/backends.md#the-bake-off-evalbakeoffpy)                                                                      |
| the candidate comparison, language coverage gaps, detailed assessments          | [evaluation/backends.md, The candidate survey](evaluation/backends.md#the-candidate-survey-june-2026)                                            |
| EuroLLM on a GPU (CUDA 12, cuBLAS, Blackwell), its prompt and glossary          | [evaluation/backends.md, EuroLLM](evaluation/backends.md#eurollm-utter-project)                                                                  |
| API or self-hosted; the terminology opportunity                                 | [evaluation/backends.md](evaluation/backends.md#api-or-self-hosted)                                                                              |
| the licensing matrix and the run with only permissive components                | [reference/licences.md](reference/licences.md#the-candidate-backends)                                                                            |
| the vocabulary with descriptions (atrium-project#72)                            | [reference/vocabulary.md](reference/vocabulary.md) and [usage.md, Build the vocabulary](usage.md#build-the-vocabulary)                           |
| the backend interface, the output guard contract, `retrying()`                  | [development.md, The backend interface](development.md#the-backend-interface)                                                                    |
| a pair the backend cannot translate: refused, never rerouted                    | [how-it-works.md, The three backends](how-it-works.md#the-three-backends)                                                                        |

## Recommendation

LINDAT (`lindat`) stays the default; the self-hosted alternative is `openai_compatible` with Qwen3.8-27B on a local
vLLM (`LLM_LICENSE_COMPONENT=qwen3_8`); `ct2` with EuroLLM is no longer recommended; descriptions stay off; and
`VOCABULARY_PATH` stays unset by default. Decided by the bake-off of 2026-10-19: the full decision, with its reasons,
is [decisions/backend-choice.md](decisions/backend-choice.md); the evidence, [evaluation/backends.md](evaluation/backends.md).
