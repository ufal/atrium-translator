# Translation Backend Evaluation

> Issue: [#4 — Translation base model to use](https://github.com/ufal/atrium-translator/issues/4)

## Current architecture

The `atrium-translator` pipeline translates archaeological archival text
(Czech → English, ALTO OCR pages and AMCR metadata XML) through a single
hard-coded backend: the **LINDAT/CUBBITT** bilingual NMT REST API
(`processors/translator.py → LindatTranslator`).

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
| **NLLB-200** (Meta)            | Multilingual NMT (0.6–3.3B)      | Self-host                         | 200+ incl. all repo langs                                     | No                      | CC-BY-NC-4.0                                                  | Infra only                           |
| **Tower+ 9B / 72B** (Unbabel)  | Translation LLM (Qwen2.5-based)  | Self-host GPU                     | 22 langs incl. cs                                             | Instructable via prompt | Research / Qwen license terms                                 | GPU infra                            |
| **Opus-MT** (Helsinki-NLP)     | Bilingual NMT (Marian)           | Self-host (CPU-viable)            | Many pairs incl. cs-en                                        | No                      | **Apache-2.0**                                                | Minimal infra                        |
| **DeepL API**                  | Commercial NMT/LLM               | API (EU-hosted)                   | European langs incl. cs (top quality)                         | **Yes** (glossary API)  | Proprietary, paid                                             | ~$25/M chars (Pro)                   |
| **Google Cloud Translation**   | Commercial NMT + LLM             | API                               | Broad                                                         | Yes (glossary)          | Proprietary, paid                                             | ~$20/M chars                         |

### Language coverage gaps

The repo's `LanguageIdentifier` supports: cs, en, fr, de, ru, pl, uk, sk, bg,
hr, sl, lv, lt, et, hu, ro, es, it, nl, hi.  Of the candidates:

- **Full coverage (all 20):** MADLAD-400, NLLB-200, Google Cloud Translation
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
  pairs including cs-en.  Apache-2.0 licensed.  CPU-viable (~200M params per
  pair).  Lower quality than the larger models but extremely fast and easy to
  deploy.  Useful as an offline/edge fallback.
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
| NLLB-200                            | CC BY-NC 4.0       | CC BY-NC 4.0                   | CC BY-NC-SA 4.0             |
| Opus-MT                             | Apache-2.0         | Apache-2.0                     | CC BY-NC-SA 4.0             |
| DeepL API                           | Proprietary (paid) | Per agreement                  | Per agreement + CC BY-NC-SA |

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
default is `"lindat"` (the existing `LindatTranslator`).  New backends are
registered by adding an adapter class and a registry entry — no changes to
`main.py`, `utils.py`, or `service/api.py` are needed.

---

## Recommendation

1. **Primary candidate to prototype: Cohere Command A Translate.**
   Best language/domain fit for cs→en archival text, native glossary support
   (can retire Tag-and-Protect + UDPipe), and available as both a hosted API
   and self-hostable research weights.

2. **Permissive self-host alternative: MADLAD-400 (Apache-2.0).**
   The path to commercially-licensed output and the broadest language coverage.
   Best fit if NC licensing becomes a blocker or if offline/air-gapped
   deployment is needed.

3. **API generalist: GLM-5.2 (MIT).**
   If ATRIUM adopts a single frontier LLM for multiple NLP tasks, GLM-5.2
   could serve translation alongside NER/summarisation via one API.  Requires
   OCR-faithfulness guardrails and prompt-based terminology (less reliable).

4. **Keep CUBBITT as the default backend.**
   Free, EU-hosted, proven for cs→en, zero infrastructure.  Should remain the
   default; alternatives are opt-in via configuration.

### Suggested next steps

1. Merge the `TranslationBackend` protocol (`processors/backend.py`) into the
   codebase.
2. Implement one reference adapter (Cohere Command A Translate recommended)
   behind a `TRANSLATION_BACKEND=cohere` config flag.
3. Run a small-scale quality comparison on sample AMCR + ALTO data: CUBBITT vs
   the new adapter, with and without glossary, measuring BLEU/COMET + manual
   review of terminology accuracy.
4. Based on results, decide whether to make the new backend the default or
   offer it as an opt-in alternative.
