# Translation Backend Evaluation

> Issue: [#4 — Translation base model to use](https://github.com/ufal/atrium-translator/issues/4)

## Current architecture

The `atrium-translator` pipeline translates archaeological archival text
(Czech → English, ALTO OCR pages and AMCR metadata XML) through a single
hard-coded backend: the **LINDAT/CUBBITT** bilingual NMT REST API
(`processors/translator.py → LindatTranslator`).

> **Status (2026-09):** the backend is no longer hard-coded. `processors/backend.py`
> registers three backends, selected with `--backend` / `TRANSLATION_BACKEND`:
> `lindat` (the default, `LindatTranslator`), `openai_compatible` (`LLMTranslator`,
> any OpenAI-compatible chat API) and `ct2` (`CT2Translator`, a self-hosted
> CTranslate2 model: EuroLLM, MADLAD-400, NLLB-200 or Opus-MT). `ct2` needs
> `requirements-ct2.txt` and the `CT2_*` variables only when it is actually used;
> its heavy imports are deferred to the first translation.

The pipeline contract is narrow — callers depend only on:

```
translate(text: str, src_lang: str, tgt_lang: str = "en") -> str
```

plus the vocabulary-related attributes (`vocabulary`, `reset_protected_count()`,
`protected_count`) and `supported_models`.  This makes the translator the
natural seam for a pluggable-backend design (see
[Backend interface design](#backend-interface-design) below).

Two properties of the current setup motivate exploring alternatives:

1. **No native terminology control.**  CUBBITT cannot accept a glossary, so the
   repo carries a *Tag-and-Protect* workaround: UDPipe lemmatisation
   (`processors/lemmatizer.py`) identifies vocabulary terms, replaces them with
   NMT-safe placeholder sentinels (`Xtermzzz<N>z`), sends the tagged text to
   CUBBITT, and restores the vocabulary translations afterwards.  This is
   brittle — NMT models can mangle, reorder, or split sentinels.

2. **Licensing lock-in.**  `para_licenses.py` propagates the *most-restrictive*
   component license to every output file.  CUBBITT (`CC BY-NC-SA 4.0`) +
   UDPipe models (`CC BY-NC-SA 4.0`) force the effective output license to
   **CC BY-NC-SA 4.0** (non-commercial, share-alike).

---

## Candidate comparison

### At a glance

| Backend                        | Type                             | Deploy                            | Languages (vs repo set)                                       | Native glossary         | License (weights / API)                                       | Approx. cost                         |
|--------------------------------|----------------------------------|-----------------------------------|---------------------------------------------------------------|-------------------------|---------------------------------------------------------------|--------------------------------------|
| **LINDAT/CUBBITT** (baseline)  | Bilingual NMT                    | Free UFAL API                     | cs + major European pairs                                     | No                      | CC BY-NC-SA 4.0                                               | Free                                 |
| **Cohere Command A Translate** | Translation LLM (111B)           | API or self-host (1–2× A100/H100) | 23 langs incl. cs, uk, pl, ru, de, fr, es, it, nl, ro, el, hi | **Yes** + document mode | HF weights: **CC-BY-NC**; commercial: Cohere API (paid)       | API: per-token; self-host: GPU infra |
| **GLM-5.2** (Zhipu/Z.ai)       | General LLM (744B MoE)           | API only (self-host impractical)  | Broad, en/zh-centric                                          | Prompt-only             | Weights: **MIT**; API: ~$1.40/$4.40 per M input/output tokens | ~$1–4 per M tokens                   |
| **MADLAD-400** (Google)        | Multilingual NMT (T5, 3B/7B/10B) | Self-host (3B CPU-viable)         | 400+ incl. **all** repo langs                                 | No                      | **Apache-2.0**                                                | Infra only                           |
| **EuroLLM** (UTTER)            | Multilingual LLM (1.7B/9B)       | Self-host (`ct2` backend)         | 35 incl. **all** repo langs                                   | Instructable via prompt | **Apache-2.0**                                                | Infra only                           |
| **NLLB-200** (Meta)            | Multilingual NMT (0.6–3.3B)      | Self-host                         | 200+ incl. all repo langs                                     | No                      | CC-BY-NC-4.0                                                  | Infra only                           |
| **Qwen3.8-27B** (Qwen)         | General LLM (27B, hybrid)        | Self-host GPU (vLLM)              | Broad, incl. cs                                               | Prompt glossary         | **Apache-2.0**                                                | GPU infra                            |
| **Tower+ 9B / 72B** (Unbabel)  | Translation LLM (Qwen2.5-based)  | Self-host GPU                     | 22 langs incl. cs                                             | Instructable via prompt | Research / Qwen license terms                                 | GPU infra                            |
| **Opus-MT** (Helsinki-NLP)     | Bilingual NMT (Marian)           | Self-host (CPU-viable)            | Many pairs incl. cs-en                                        | No                      | Apache-2.0 (older) / **CC BY 4.0** (tc-big)                   | Minimal infra                        |
| **DeepL API**                  | Commercial NMT/LLM               | API (EU-hosted)                   | European langs incl. cs (top quality)                         | **Yes** (glossary API)  | Proprietary, paid                                             | ~$25/M chars (Pro)                   |
| **Google Cloud Translation**   | Commercial NMT + LLM             | API                               | Broad                                                         | Yes (glossary)          | Proprietary, paid                                             | ~$20/M chars                         |

### Language coverage gaps

The repo's `LanguageIdentifier` supports: cs, en, fr, de, ru, pl, uk, sk, bg,
hr, sl, lv, lt, et, hu, ro, es, it, nl, hi.  Of the candidates:

- **Full coverage (all 20):** MADLAD-400, NLLB-200, EuroLLM, Google Cloud Translation
- **Good coverage (~15/20):** Cohere Command A Translate (missing sk, bg, hr, sl, lv, lt, et, hu)
- **Partial:** GLM-5.2 (general LLM, not pair-specific), Tower+ (22 langs but subset differs)
- **Pair-specific:** CUBBITT, Opus-MT (need one model per pair)

---

## Detailed assessments

### Cohere Command A Translate

- **Released:** August 2025.  111B-parameter LLM purpose-built for translation.
- **Languages:** 23 including Czech, Ukrainian, Polish, Russian, German, French,
  Spanish, Italian, Dutch, Romanian, Greek, Hindi.
- **Key strength: native glossary + document mode.**  Glossary support could
  retire the Tag-and-Protect machinery entirely — vocabulary terms would be
  passed as structured glossary entries rather than injected as placeholder
  sentinels.  Document mode preserves context across paragraphs, which benefits
  ALTO page-level translation.
- **Deployment:** Available as both a hosted Cohere API endpoint and
  self-hostable research weights on HuggingFace
  ([CohereLabs/command-a-translate-08-2025](https://huggingface.co/CohereLabs/command-a-translate-08-2025)).
  Self-hosting requires 1–2× A100/H100 GPUs.
- **License:** Research weights under **CC-BY-NC** (non-commercial); commercial
  use requires the paid Cohere API or a private deployment agreement.
- **Gaps:** Missing 8 of the 20 repo languages (sk, bg, hr, sl, lv, lt, et,
  hu).  For those pairs, a fallback to CUBBITT or MADLAD-400 would be needed.
- **References:**
  - [Cohere announcement](https://docs.cohere.com/changelog/2025-08-28-command-a-translate)
  - [Model documentation](https://docs.cohere.com/docs/command-a-translate)
  - [HuggingFace weights](https://huggingface.co/CohereLabs/command-a-translate-08-2025)
  - [Slator coverage](https://slator.com/cohere-enterprise-ai-translation-command-a-translate/)

### GLM-5.2 (Zhipu / Z.ai)

- **Released:** June 2026.  744B MoE general-purpose LLM from Zhipu AI.
- **Languages:** Broad multilingual, but primarily trained on English and Chinese.
- **Key strength: MIT-licensed open weights.**  Permissive license means the
  effective output license via `para_licenses.py` would drop to whatever the
  *other* most-restrictive component is — potentially freeing outputs from
  NC-SA if UDPipe is also replaced.
- **Deployment:** API only in practice.  At 744B parameters (MoE), self-hosting
  requires multi-node GPU clusters.  Z.ai API pricing: ~$1.40 input / $4.40
  output per million tokens.
- **Terminology:** No native glossary — terminology must be injected via system
  prompt instructions, which is less reliable than a structured glossary API.
- **Risks for this domain:**
  - **Not translation-specialized** — general LLMs can hallucinate content that
    was not in the source, which is unacceptable for archival translation.
  - **OCR noise sensitivity** — archaeological OCR text contains fragments,
    diacritics errors, and layout artifacts that a generalist LLM may
    "helpfully" correct, altering the source meaning.
  - Guardrails (output-length validation, source-faithfulness checks) would be
    needed.
- **Upside:** If ATRIUM adopts a single frontier LLM for multiple NLP tasks
  (NER, summarisation, translation), GLM-5.2 could serve all of them via one
  API integration.
- **References:**
  - [HuggingFace: zai-org/GLM-5.2](https://huggingface.co/zai-org/GLM-5.2) (MIT)
  - [GLM-5 blog](https://huggingface.co/blog/mlabonne/glm-5)

### MADLAD-400 (Google)

- **Released:** 2023.  T5-based multilingual NMT in 3B, 7B, and 10B sizes.
- **Languages:** 400+ including **all** languages in the repo's identifier.
- **Key strength: Apache-2.0 license + broadest coverage.**  The only
  self-hostable option that enables fully commercial-permissive output.  The 3B
  variant is CPU-viable for moderate throughput.
- **Deployment:** Self-host only (no hosted API).  3B fits on a single GPU or
  CPU; 10B needs a mid-range GPU.
- **Terminology:** No native glossary.  Tag-and-Protect would still be needed
  (or a prompt-based approach if using the model in a text-generation mode).
- **Quality:** Strong on high-resource pairs (cs-en); competitive with or
  better than Google Translate on many benchmarks.  Robust to noisy OCR input
  since it's a seq2seq NMT model, not a generative LLM.
- **References:**
  - [MADLAD-400 paper (NeurIPS 2023)](https://proceedings.neurips.cc/paper_files/paper/2023/file/d49042a5d49818711c401d34172f9900-Paper-Datasets_and_Benchmarks.pdf)
  - [HuggingFace: google/madlad400-3b-mt](https://huggingface.co/google/madlad400-3b-mt)

### EuroLLM (UTTER project)

- **Released:** 2024.  Decoder-only multilingual LLMs (Llama architecture) in
  1.7B and 9B sizes, with instruction-tuned variants trained for translation.
- **Languages:** 35, covering every EU official language plus uk, ru, hi and
  others: **all** 20 languages in the repo's identifier.
- **License:** **Apache-2.0**, like MADLAD-400, so it keeps the output
  permissive.  EuroLLM-9B-Instruct is gated on Hugging Face (accept the terms
  before download).
- **Deployment:** The `ct2` backend's default family (`CT2_MODEL_FAMILY=eurollm`):
  converted to CTranslate2 (`ct2-transformers-converter --quantization int8`) and
  run as a `Generator` with the checkpoint's own Hugging Face tokenizer and chat
  template (`CT2_TOKENIZER_DIR`; unset, `CT2_MODEL_DIR` when converted with
  `--copy_files tokenizer_config.json tokenizer.model special_tokens_map.json`,
  else the folder of `CT2_SP_MODEL`); `requirements-ct2.txt` brings Transformers
  and Jinja2 for the template. Generation stops at the tokenizer's EOS and at the chat
  markers (`<|im_end|>`, `<|im_start|>`) it knows. 1.7B is CPU-viable at int8;
  9B wants a GPU.
- **On a GPU:** CTranslate2 4.x runs on CUDA 12 and loads `libcublas.so.12`, which a CUDA 13
  PyTorch in the same environment does not bring: `pip install nvidia-cublas-cu12` and put its
  `lib` folder on `LD_LIBRARY_PATH` (`requirements-ct2.txt` has the line), or use a CUDA 12
  runtime. A Blackwell GPU (compute capability 12.x) needs cuBLAS 12.8 or newer: CUDA 12.3's
  answered `CUBLAS_STATUS_NOT_SUPPORTED` to every prompt on an RTX PRO 6000 (job 7144164).
  Without a working cuBLAS the model loads and every generation fails, as in the bake-off jobs
  of 2026-10-18 (1,230 of 1,230 segments). `warm()` therefore runs a few tokens of a record-sized
  prompt, built as a translation's is, after the load; the service and the CLI stop there with
  the fix (or `CT2_DEVICE=cpu`).
- **Prompt:** the user turn is EuroLLM-Instruct's documented translation format,
  languages in words (`Translate the following Czech source text to
  English:\nCzech: … \nEnglish: `); the instructions sit in the system turn.
- **Terminology:** No glossary API, but instructable: the `ct2` backend puts the
  matching vocabulary pairs in the system turn (`supports_glossary = True`), so
  Tag-and-Protect and UDPipe are not needed. A glossary the model writes back
  (`Use these exact terms: bod = point …`, `term = term` lines, the prompt's
  `Czech:` / `English:` labels) is cut from the start and end of the reply; a
  reply that was only the glossary, or the untranslated source, is requested once
  more without the glossary. In the first AMCR run (2026-09-27, glossary still in
  the user turn) EuroLLM-1.7B wrote the glossary into 13 of 30 fields.
- **Quality:** Measured in the bake-off of 2026-10-19 ([Recommendation](#recommendation)):
  9B failed 96 of 1,230 segments and kept 157 of 230 glossary terms, 1.7B failed 584.
  89 of 9B's 96 failures are ALTO blocks of at most ten characters (a number, a
  single word), where the model runs on past the source and the output-length
  guards (`CT2_GUARD_*`) refuse the reply; a check also rejects the source copied
  back unchanged.
- **References:**
  - [HuggingFace: utter-project/EuroLLM-1.7B-Instruct](https://huggingface.co/utter-project/EuroLLM-1.7B-Instruct)
  - [HuggingFace: utter-project/EuroLLM-9B-Instruct](https://huggingface.co/utter-project/EuroLLM-9B-Instruct)

### NLLB-200 (Meta)

- **Released:** 2022.  Multilingual NMT covering 200 languages, 0.6B to 3.3B sizes.
- **Languages:** 200+ including all repo languages.
- **License:** **CC-BY-NC 4.0** — same NC constraint as the current setup.
- **Quality:** Strong on low-resource pairs; competitive on cs-en.
- **Notes:** A solid self-hosted alternative but does not improve the licensing
  situation.  Useful as a lightweight fallback for language pairs that other
  backends don't cover.

### Tower+ (Unbabel)

- **Released:** 2024–2025.  Translation-tuned LLM built on Qwen2.5, in 9B and
  72B sizes.  Covers 22 languages including Czech.
- **Quality:** Strong WMT benchmark results; specifically tuned for translation
  quality metrics (COMET, BLEURT).
- **License:** Research-oriented; subject to Qwen base model license terms.
- **Deployment:** Self-host GPU.  9B fits on a single A100; 72B needs multi-GPU.
- **References:**
  - [HuggingFace: Unbabel/Tower-Plus-9B](https://huggingface.co/Unbabel/Tower-Plus-9B)
  - [HuggingFace: Unbabel/Tower-Plus-72B](https://huggingface.co/Unbabel/Tower-Plus-72B)

### Opus-MT (Helsinki-NLP)

- **Lightweight, permissive fallback.**  Marian-NMT bilingual models, many
  pairs including cs-en.  CPU-viable (~200M params per pair).  Lower quality
  than the larger models but extremely fast and easy to deploy.  Useful as an
  offline/edge fallback.
- **License split:** the original checkpoints (e.g. `opus-mt-cs-en`) are
  Apache-2.0; the newer `opus-mt-tc-big-*` line is **CC BY 4.0**.  Both are
  permissive and rank the same in `para_licenses.py`, but CC BY adds an
  attribution duty, so `para_config.txt` records the `opus_mt` component as
  CC BY 4.0 whichever checkpoint is converted.
- **References:**
  - [GitHub: Helsinki-NLP/Opus-MT](https://github.com/Helsinki-NLP/Opus-MT)

### DeepL API

- **Highest European translation quality** in independent benchmarks.  Native
  glossary API.  EU-hosted (data residency).  Proprietary and paid (~$25/M
  characters on Pro plan).  Glossary support would replace Tag-and-Protect.
  Strong candidate if budget allows.
- **References:**
  - [DeepL API](https://www.deepl.com/en/products/api)

### Google Cloud Translation

- **Broad coverage, glossary support, paid.**  Recently added an LLM-based
  translation tier.  Good baseline but DeepL outperforms on European pairs.

---

## API vs self-hosted trade-offs

| Factor                   | API-based                                          | Self-hosted                                     |
|--------------------------|----------------------------------------------------|-------------------------------------------------|
| **Setup cost**           | Low (API key + HTTP client)                        | High (GPU infra, model download, serving stack) |
| **Per-unit cost**        | Per-token/character (ongoing)                      | Fixed infra (amortised over volume)             |
| **Data residency**       | Depends on provider (DeepL: EU; Cohere/GLM: check) | Full control (EU servers)                       |
| **Latency**              | Network-bound; provider SLAs                       | Local; predictable                              |
| **Maintenance**          | Provider handles updates                           | Model updates, serving, scaling are yours       |
| **Offline / air-gapped** | Not possible                                       | Possible (Opus-MT, MADLAD, NLLB)                |
| **Best for**             | Prototyping, variable volume, no GPU budget        | High volume, data-sensitive, EU-only, offline   |

---

## Licensing matrix (informational)

How each backend changes the effective output license via `para_licenses.py`:

| Backend                             | Component license  | Effective output if used alone | With UDPipe (CC BY-NC-SA)   |
|-------------------------------------|--------------------|--------------------------------|-----------------------------|
| LINDAT/CUBBITT                      | CC BY-NC-SA 4.0    | CC BY-NC-SA 4.0                | CC BY-NC-SA 4.0             |
| Cohere Command A (research weights) | CC BY-NC 4.0       | CC BY-NC 4.0                   | CC BY-NC-SA 4.0             |
| GLM-5.2                             | MIT                | MIT                            | CC BY-NC-SA 4.0             |
| MADLAD-400                          | Apache-2.0         | Apache-2.0                     | CC BY-NC-SA 4.0             |
| EuroLLM                             | Apache-2.0         | Apache-2.0                     | CC BY-NC-SA 4.0             |
| NLLB-200                            | CC BY-NC 4.0       | CC BY-NC 4.0                   | CC BY-NC-SA 4.0             |
| Opus-MT                             | CC BY 4.0 †        | CC BY 4.0                      | CC BY-NC-SA 4.0             |
| DeepL API                           | Proprietary (paid) | Per agreement                  | Per agreement + CC BY-NC-SA |

The AMCR/TEATER vocabulary (`amcr_vocab`, `teater_data`) is **CC0** (rights holder AMČR, atrium-project#6,
2026-09-28), so a glossary never changes the result: a `ct2` EuroLLM run with `--vocabulary` and an explicit
`--source_lang` resolves to MIT (the engine; the model is Apache-2.0). FastText (`--source_lang auto`) is CC BY-NC 4.0.

† Recorded conservatively: `opus-mt-tc-big-*` checkpoints are CC BY 4.0, older ones
Apache-2.0 (same rank). The `ct2` backend logs `nllb200` / `opus_mt` /
`eurollm` / `madlad400` from `para_config.txt` according to `CT2_MODEL_FAMILY`.

**Key insight:** To achieve a permissive output license, both the translation
backend *and* the lemmatiser/UDPipe dependency must be replaced (or the
vocabulary feature disabled).  A glossary-native backend (Command A, DeepL)
that retires Tag-and-Protect also removes the UDPipe dependency, opening the
path to permissive outputs in a single move.

---

## Terminology opportunity

Backends with native glossary support (Command A Translate, DeepL API, Google
Cloud Translation) could replace the entire Tag-and-Protect pipeline:

| Current (CUBBITT)                             | With glossary-native backend |
|-----------------------------------------------|------------------------------|
| `processors/lemmatizer.py` (UDPipe API calls) | Not needed                   |
| Placeholder sentinels (`Xtermzzz<N>z`)        | Not needed                   |
| Fuzzy sentinel restoration                    | Not needed                   |
| `_scrub_placeholder_fragments()`              | Not needed                   |
| Number-agreement guard (Plur skip)            | Handled by the model         |
| Homonym misalignment risk                     | Eliminated                   |
| UDPipe CC BY-NC-SA license                    | Removed from the stack       |

The vocabulary CSV (`source_lemma,target_translation`) would be passed directly
to the backend's glossary API.  The `TranslationBackend` protocol (below)
includes a `supports_glossary` flag so the pipeline can decide at runtime
whether to use Tag-and-Protect or delegate to the backend.

### The vocabulary: one curated list, with descriptions (atrium-project#72)

Since 2026-10-18 the shipped `data_samples/vocabulary.csv` is built from
atrium-keyword-extract's release asset (`python load_vocab.py --from-release v1.3.0-beta`):
the 4,715 pairs of its curated `vocabulary.csv`, so the project curates one list rather
than two (AMČR, 13 October). Each pair keeps its concept identity (`source`, `source_id`,
`uri`), and 1,961 carry the concept's description (`note_en`, else `note_cs`, cut to 500
characters) in a sixth column, `description`, which a loader reads by header name only.

AMČR asked for the LLM backend to translate "with the vocab, including descriptions, as one
of the inputs". `LLM_GLOSSARY_DESCRIPTION_CHARS` (a limit, default 0 = none) gives a matched
term's description to the model beside the pair, `kostel = church (description: …)`, as
context for choosing the sense; the prompt says it never belongs in the translation. A
description longer than the limit is cut at a word (`trimmed` note). Only `openai_compatible`
uses descriptions: EuroLLM-1.7B (`ct2`) already echoed the bare glossary into its output
before the prompt fix of 2026-09-28, and its input budget is small. Most descriptions are
Czech (TEATER has them for about half its terms, AMČR for few).

`/translate` applies a vocabulary only when `VOCABULARY_PATH` names one (default: none, the
behaviour before). A path that names no usable CSV stops the service at startup; `/info`
reports the file, its term count and sha256. Whether the image should default to the shipped
list is decided from the bake-off below.

---

## Backend interface design

A `TranslationBackend` typing.Protocol defines the contract that all backends
must satisfy.  See `processors/backend.py` for the reference implementation.

```python
class TranslationBackend(Protocol):
    name: str
    supports_glossary: bool

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str: ...

    def supported_languages(self) -> list[str]: ...
```

A `get_backend(name)` factory returns the appropriate implementation.  The
default is `"lindat"` (the existing `LindatTranslator`); `"openai_compatible"`
and `"ct2"` are the other two registered names.  New backends are registered by
adding an adapter class and a registry entry — no changes to `main.py`,
`utils.py`, or `service/api.py` are needed.

**A pair the backend cannot translate is refused, never rerouted** (2026-10-18). LINDAT
used to send any pair it has no model for to `cs-en` without a word, so `cs→it` wrote English
into `_it.xml` and `hu→en` translated Hungarian as Czech. Now `language_pair_problem()`
(`processors/language.py`) checks an explicit pair before anything is translated: the CLI
exits 1 and the service answers 422 (`cause: "unsupported_language_pair"`, the offered source
languages in `errors`), each naming what the backend offers; in `auto` mode the fallback
source language is checked the same way. LINDAT is held to its exact `src-tgt` models, another
backend to `supported_languages()` when that lists anything (the LLM backend lists nothing
unless `LLM_LANGUAGES` is set, so it refuses nothing). `_basic_translate` raises
`UnsupportedLanguagePairError` as the backstop.

`supported_languages()` (for LINDAT, the source side of its `supported_models`
pairs) is also what `--source_lang auto` checks a detection against. A segment
FastText places confidently in a language outside it is translated from the
fallback language and reported as `unsupported_lang` (translator#52; README,
"Source-language identification"), so the narrower a backend's set, the more
such rows an `auto` run over mixed material has: LINDAT lacks `la` and `hu`,
EuroLLM and MADLAD (`ct2`) have both.

### Output guard: the failure contract every backend shares

An HTTP 200 is not a translation. On the 2026-09-26 sample refresh roughly a third of
all LINDAT replies were one Czech word repeated up to ~150 times, nondeterministically,
on the ALTO and the metadata path alike (issue #46 follow-up, see
`agent_dev_logs/digests/46.digest.md`). The contract is now:

* `processors/quality.py::degeneration_reason(source, translated)` is the single
  judge — empty output, runaway length, truncation, repetition loops and a word that
  mixes Latin and Cyrillic letters, each compared with the source. It is
  dependency-free, so any backend can use it.
* A backend that gives up on a reply raises `DegenerateTranslationError` (a
  `TranslationError` subclass, in `processors/translator.py`). `LindatTranslator`
  re-requests first (`LINDAT_GUARD_RETRIES`, default 2); the LLM and CT2 guards raise
  it for their ratio checks and for loops the ratio cannot see.
* `utils.py` treats that error — and any reply that fails the detector, which also
  covers backends with no guard of their own — as a **per-segment** failure: flagged,
  re-run after the whole document (`TRANSLATION_RERUN_ROUNDS`,
  `TRANSLATION_RERUN_DELAY_S`), and kept as source text with `status=untranslated` in
  the CSV log if it never recovers. Any other `TranslationError` (transport, config)
  still skips the whole file.

A new backend therefore needs no guard to be safe, but should raise
`DegenerateTranslationError` rather than return text it knows is bad.

A backend whose decoding is deterministic should also offer `retrying(round)`, a context
manager that `utils.py` enters around the requests of the end-of-document re-run (round 1,
2, …): asked the same thing again, it would return the same reply. `CT2Translator` widens
its beam inside the block (thread-local, since one backend object serves every request).
A backend without the method is called as before: asking LINDAT again reaches another
replica, and the OpenAI-compatible backend (temperature 0, a remote model) has no such finding.

---

## Recommendation

Decided by the bake-off of 2026-10-19 (issue #4; [the result](#the-result-2026-10-19) below), within the pilot
decision of 30 September (the self-hosted backend is the fallback and alternative to LINDAT):

1. **LINDAT (`lindat`) stays the default.** It kept the most glossary terms (229 of 232) and failed no segment.
   Its output is CC BY-NC-SA 4.0 (the CUBBITT models; UDPipe too when a vocabulary is applied), and AMČR's use
   of it waits on a written permission (Pavel Straňák).
2. **The self-hosted alternative is `openai_compatible` with Qwen3.8-27B on a local vLLM.** It comes close
   to LINDAT on every measured check (terms 227 of 232 against LINDAT's 229, numbers 0.997, length ratio 1.03,
   94 % character similarity to LINDAT's output), failed no segment, and ran about three times as fast. The
   model is Apache-2.0: record it with `LLM_LICENSE_COMPONENT=qwen3_8`. Measured in bf16 on one 96 GB GPU
   (RTX PRO 6000), served with `--language-model-only --max-num-seqs 64` (vLLM 0.31; Qwen3.8 is a hybrid model) and
   `LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'`. `Qwen/Qwen3.8-27B-FP8` (about 28 GB,
   tensor parallel 2 on two 24 GB cards) is not measured yet.
3. **Descriptions stay off** (`LLM_GLOSSARY_DESCRIPTION_CHARS=0`): with 300 characters, 1,210 of 1,230 outputs
   were the same and one term fewer was kept.
4. **`ct2` with EuroLLM is no longer the recommended fallback.** EuroLLM-9B failed 96 segments (8 %; 89 of
   them ALTO blocks of at most ten characters that the model runs on past) and kept 157 of 230 terms; 1.7B
   failed 584 (47 %).
   The guard turns each failure into an `untranslated` field rather than a wrong one. The backend stays for
   MADLAD-400, NLLB-200 and Opus-MT, which the pilot does not measure.
5. **`VOCABULARY_PATH` stays unset by default.** The bake-off has no arm without the vocabulary, so it shows
   that the curated list costs nothing (LINDAT and Qwen keep 98 % of its terms), not what it adds; AMČR's
   `metadata-en` recipe uses none (issue #46). A deployment that wants it sets
   `VOCABULARY_PATH=data_samples/vocabulary.csv`, which the image carries.

### The bake-off (`eval/bakeoff.py`)

One variant per run, each named with `--label`, combined with `--combine` (the first file is
the baseline for `baseline_char_sim`):

| Variant            | Backend             | Model / setting                                            |
|--------------------|---------------------|------------------------------------------------------------|
| `lindat`           | `lindat`            | LINDAT CUBBITT, Tag-and-Protect with the vocabulary        |
| `ct2-eurollm-1.7b` | `ct2`               | EuroLLM-1.7B-Instruct, int8                                |
| `ct2-eurollm-9b`   | `ct2`               | EuroLLM-9B-Instruct, int8                                  |
| `llm-desc0`        | `openai_compatible` | Qwen3.8-27B on a local vLLM, glossary without descriptions |
| `llm-desc300`      | `openai_compatible` | the same, `LLM_GLOSSARY_DESCRIPTION_CHARS=300`             |

The sample is the 15 AMČR records of `data_samples/my_documents` (37 `metadata-en` free-text
fields, the production shape of issue #46) with the ALTO sample as a separate slice
(`<out>_by_kind.csv`). There are no reference translations, so the comparison is
reference-less: failed / empty segments, number preservation, length ratio, terminology hits,
divergence from LINDAT, speed, optionally COMET-QE, and a manual read of the outputs. 37
fields is a small sample; the result comment says so.

### The result (2026-10-19)

Jobs 7143997 (LINDAT), 7147048 (Qwen3.8-27B bf16 on an RTX PRO 6000) and 7147059 (EuroLLM int8 on an
RTX A4000), translator `a048708` with the curated vocabulary (4,715 pairs); the per-segment table and the
summaries are in [`eval/results/issue4_bakeoff_2610/`](../eval/results/issue4_bakeoff_2610/).

| Variant            | Failed | Terms kept      | Numbers kept | Length ratio | Similarity to LINDAT | Characters/s |
|--------------------|--------|-----------------|--------------|--------------|----------------------|--------------|
| `lindat`           | 0      | 229/232 (0.987) | 0.993        | 1.03         | —                    | 20           |
| `llm-desc0`        | 0      | 227/232 (0.978) | 0.997        | 1.03         | 0.944                | 57           |
| `llm-desc300`      | 0      | 226/232 (0.974) | 0.997        | 1.03         | 0.944                | 57           |
| `ct2-eurollm-9b`   | 96     | 157/230 (0.683) | 0.991        | 1.14         | 0.919                | 49           |
| `ct2-eurollm-1.7b` | 584    | 87/141 (0.617)  | 0.994        | 1.64         | 0.794                | 38           |

1,230 segments each: 37 metadata fields and 1,193 ALTO blocks. Terms count only on segments that did not fail.
On the 37 metadata fields alone, LINDAT and both Qwen arms keep 49 of 49 terms, but Qwen's fields are longer
(length ratio 1.18 against LINDAT's 1.11) and differ more from LINDAT's (similarity 0.68): it rephrases short
fields, which a bilingual reader should look at. The speeds come from different GPUs, and LINDAT's from a
public endpoint over the network.

A variant that measures nothing ends the run with exit status 2 and says why: it cannot start
(`warm()` fails), it fails `--max-consecutive-failures` segments in a row with the same error
(default 10; nothing is written), or every one of its segments failed (the CSVs are written).

### Suggested next steps

1. A bilingual read of the metadata fields where Qwen3.8 and LINDAT differ most (similarity 0.68 on the 37
   fields), with AMČR, before Qwen is offered for `metadata-en`.
2. `Qwen/Qwen3.8-27B-FP8` on two 24 GB cards, to size a deployment that has no 80 GB card.
3. AMČR's written permission for LINDAT; until then a run that must avoid CC BY-NC-SA output uses Qwen3.8.
4. Further model exploration (Qwen with the vocabulary fed whole, other sizes, MADLAD-400) after the pilot, as
   decided on 30 September.
