# Decision: which translation backend (issue #4)

For anyone who asks why LINDAT is the default and what replaces it where LINDAT cannot be used. Dated decisions of
issue [#4](https://github.com/ufal/atrium-translator/issues/4) ("Translation base model to use"), closed on
2026-10-21; the evidence is in [evaluation/backends.md](../evaluation/backends.md). Moved here from
`docs/translation-backends.md` on 2026-10-25 (atrium-project#79).

## 2026-06: an architecture, not a model

The repository opened with one question: which base model should translate? A candidate comparison was posted within
a day, CUBBITT as the incumbent against Cohere Command A, GLM-5.2, MADLAD-400, NLLB-200, Tower+, Opus-MT, the
commercial APIs and, later, EuroLLM, scored on language coverage, licence and cost
([the survey](../evaluation/backends.md#the-candidate-survey-june-2026)). The answer was an architecture: a pluggable
`TranslationBackend` interface with `lindat` and `openai_compatible` registered, and a CTranslate2 backend for
self-hosted models whose heavy dependencies load only on first use. The choice between models was left to
measurement, the `eval/bakeoff.py` harness, so that CUBBITT stays the default until another backend is shown to do
better on this material. v0.7.0 called the multi-backend support "finalized in theory (not tested in practice)".

## 2026-09-30: the pilot's frame

For the S2 pilot, the self-hosted backend is the fallback and the alternative to LINDAT; further model exploration
waits until after the pilot.

## 2026-10-18: a pair the backend cannot translate is refused, never rerouted

LINDAT used to send any pair it has no model for to `cs-en` without a word, so `cs→it` wrote English into `_it.xml`
and `hu→en` translated Hungarian as Czech. Decided: an explicit pair is checked before anything is translated, and
refused (CLI exit 1, HTTP 422 `unsupported_language_pair`), naming what the backend offers; in `auto` mode the
fallback source language is checked the same way. No automatic fallback to another backend
([The three backends](../how-it-works.md#the-three-backends)).

## 2026-10-19: LINDAT stays the default

Decided by the bake-off of 2026-10-19 ([the result](../evaluation/backends.md#the-result-2026-10-19)), within the
pilot's frame of 30 September:

1. **LINDAT (`lindat`) stays the default.** It kept the most glossary terms (229 of 232) and failed no segment. Its
   models are CC BY-NC-SA 4.0 (the CUBBITT models; UDPipe's too when a vocabulary is applied), offered under the
   LINDAT Terms of Use, and AMČR's use of it waits on a written permission (Pavel Straňák).
2. **The self-hosted alternative is `openai_compatible` with Qwen3.8-27B on a local vLLM.** It comes close to LINDAT
   on every measured check (terms 227 of 232 against LINDAT's 229, numbers 0.997, length ratio 1.03, 94 % character
   similarity to LINDAT's output), failed no segment, and ran about three times as fast. The model is Apache-2.0:
   record it with `LLM_LICENSE_COMPONENT=qwen3_8`. Measured in bf16 on one 96 GB GPU (RTX PRO 6000), served with
   `--language-model-only --max-num-seqs 64` (vLLM 0.31; Qwen3.8 is a hybrid model) and
   `LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'`. `Qwen/Qwen3.8-27B-FP8` (about 28 GB,
   tensor parallel 2 on two 24 GB cards) is not measured yet.
3. **Descriptions stay off** (`LLM_GLOSSARY_DESCRIPTION_CHARS=0`): with 300 characters, 1,210 of 1,230 outputs were
   the same and one term fewer was kept.
4. **`ct2` with EuroLLM is no longer the recommended fallback.** EuroLLM-9B failed 96 segments (8 %; 89 of them ALTO
   blocks of at most ten characters that the model runs on past) and kept 157 of 230 terms; 1.7B failed 584 (47 %).
   The guard turns each failure into an `untranslated` field rather than a wrong one. The backend stays for
   MADLAD-400, NLLB-200 and Opus-MT, which the pilot does not measure.
5. **`VOCABULARY_PATH` stays unset by default.** The bake-off has no arm without the vocabulary, so it shows that the
   curated list costs nothing (LINDAT and Qwen keep 98 % of its terms), not what it adds; AMČR's `metadata-en` recipe
   uses none (issue #46). A deployment that wants it sets `VOCABULARY_PATH=data_samples/vocabulary.csv`, which the
   image carries. (The batch CLI applies the vocabulary that config.txt names.)

Open after the decision: a bilingual read of the fields where Qwen3.8 and LINDAT differ most, with AMČR, before Qwen
is offered for `metadata-en`; the FP8 model on two 24 GB cards; LINDAT's written permission
([Suggested next steps](../evaluation/backends.md#suggested-next-steps)).
