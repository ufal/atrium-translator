# Translation backends: what was measured

For anyone judging the translator's output or choosing a backend. It gives the evidence of issue
[#4](https://github.com/ufal/atrium-translator/issues/4) ("Translation base model to use"): the bake-off of
2026-10-19 that kept LINDAT as the default, and the survey of candidate models it started from. The decision itself
is in [decisions/backend-choice.md](../decisions/backend-choice.md); the backends as they are now, in
[how-it-works.md](../how-it-works.md#the-three-backends).

On this page: [What it shows, and what it does not](#what-it-shows-and-what-it-does-not) ·
[The bake-off](#the-bake-off-evalbakeoffpy) · [The result](#the-result-2026-10-19) ·
[Suggested next steps](#suggested-next-steps) · [The candidate survey](#the-candidate-survey-june-2026)

## What it shows, and what it does not

Archival OCR text rarely comes with a reference translation, so the comparison is **reference-less**: it measures
failures, empty outputs, kept numbers and terms, length and divergence between backends, and speed. It is also
**small**: 37 metadata fields and 1,193 ALTO blocks. No human has scored the translations yet. The repository's test
suite checks that the code does what it says, on stubs and fixtures; it says nothing about translation quality.

## The bake-off (`eval/bakeoff.py`)

[eval/bakeoff.py](../../eval/bakeoff.py) translates a sample with each backend and writes:

- `--out`, one row per segment and backend: the output and its per-segment scores;
- `--summary-out` (default `<out>_summary.csv`), one row per backend: `segments`, `failed`, `empty` and
  `empty_rate`, `term_hits`, `term_expected` and `term_hit_rate`, the mean `number_preservation` and
  `length_ratio`, the mean `baseline_char_sim`, `refs_used`, corpus `chrF` and `BLEU`, `COMET` and `COMET_QE`
  system scores, and the time: `seconds` and `chars_per_s` (source characters per second);
- `<out>_by_kind.csv`, the same per backend and kind of segment (`metadata`, `alto`), so the AMČR fields and the OCR
  slice are never averaged together;
- `<out>.meta.json`, what ran: each backend's description (model, device, quantisation; never a key), the
  vocabulary's SHA-256, the commit, the warm-up time.

**The metrics.** Reference-less signals are always reported; reference-based scores only when a references TSV is
given with `--refs`:

- with references: chrF and BLEU (sacreBLEU), per segment and for the corpus; COMET (`--comet-model`, default
  `Unbabel/wmt22-comet-da`), per segment and for the system;
- reference-free neural quality estimation, opt-in: COMET-QE with `--comet-qe-model` (e.g.
  `Unbabel/wmt22-cometkiwi-da`, a gated Hugging Face model);
- reference-less checks: whether numbers, dates and codes survive, the rate of empty outputs, the output-to-input
  length ratio (a guard against truncation and invented text);
- terminology: the share of the expected glossary targets present in the output;
- divergence: the character similarity of each backend's output to the first backend's (the baseline).

`failed` counts segments where the backend raised, `empty` those it answered with blank text: an outage or refusal
is a different thing from a model that dropped the content. The heuristic columns describe the outputs a backend
actually produced, so failed segments are left out of them; the reference-based scores use the same segment set for
every backend, where a failed segment scores as an empty output. The source language is explicit (`--source_lang cs`,
the default; `auto` is refused): the pipeline's language detection is not part of what is compared. sacreBLEU and
COMET are optional (`eval/requirements-eval.txt`) and load lazily; without them the harness warns and goes on.

**One variant per run, combined afterwards.** On a cluster each variant usually needs its own environment (a `ct2`
model per `CT2_MODEL_DIR`, a language-model server per `LLM_*`, network for `lindat`), so the bake-off runs one
variant at a time, named with `--label`, and `--combine` merges the per-segment CSVs, recomputing
`baseline_char_sim` against the first file's variant:

| Variant            | Backend             | Model or setting                                           |
|--------------------|---------------------|------------------------------------------------------------|
| `lindat`           | `lindat`            | LINDAT CUBBITT, Tag-and-Protect with the vocabulary        |
| `ct2-eurollm-1.7b` | `ct2`               | EuroLLM-1.7B-Instruct, int8                                |
| `ct2-eurollm-9b`   | `ct2`               | EuroLLM-9B-Instruct, int8                                  |
| `llm-desc0`        | `openai_compatible` | Qwen3.8-27B on a local vLLM, glossary without descriptions |
| `llm-desc300`      | `openai_compatible` | the same, `LLM_GLOSSARY_DESCRIPTION_CHARS=300`             |

**The sample** is the 15 AMČR records of `data_samples/my_documents` (37 `metadata-en` free-text fields, the
production shape of issue #46), with the ALTO sample as a separate slice. 37 fields is a small sample, and the result
comment on #4 says so. A manual read of the outputs completes the comparison.

A variant that measures nothing ends the run with exit status 2 and says why: it cannot start (`warm()` fails), it
fails `--max-consecutive-failures` segments in a row with the same error (default 10; nothing is written), or every
one of its segments failed (the CSVs are written). The script calls the real backends over the network, so it is
run by hand against a chosen sample, not in CI. Its flags: [the command-line reference](../reference/cli.md#other-entry-points).

## The result (2026-10-19)

Jobs 7143997 (LINDAT), 7147048 (Qwen3.8-27B in bf16 on an RTX PRO 6000) and 7147059 (EuroLLM int8 on an RTX A4000),
translator `a048708` with the curated vocabulary (4,715 pairs). The per-segment table and the summaries are in
[`eval/results/issue4_bakeoff_2610/`](../../eval/results/issue4_bakeoff_2610/).

| Variant            | Failed | Terms kept      | Numbers kept | Length ratio | Similarity to LINDAT | Characters/s |
|--------------------|--------|-----------------|--------------|--------------|----------------------|--------------|
| `lindat`           | 0      | 229/232 (0.987) | 0.993        | 1.03         | —                    | 20           |
| `llm-desc0`        | 0      | 227/232 (0.978) | 0.997        | 1.03         | 0.944                | 57           |
| `llm-desc300`      | 0      | 226/232 (0.974) | 0.997        | 1.03         | 0.944                | 57           |
| `ct2-eurollm-9b`   | 96     | 157/230 (0.683) | 0.991        | 1.14         | 0.919                | 49           |
| `ct2-eurollm-1.7b` | 584    | 87/141 (0.617)  | 0.994        | 1.64         | 0.794                | 38           |

1,230 segments each: 37 metadata fields and 1,193 ALTO blocks. Terms count only on segments that did not fail.

- **LINDAT** kept the most glossary terms (229 of 232) and failed no segment.
- **Qwen3.8-27B** (`openai_compatible`) came close on every measured check (terms 227 of 232, numbers 0.997, length
  ratio 1.03, 94 % character similarity to LINDAT's output), failed no segment, and ran about three times as fast.
  On the 37 metadata fields alone, LINDAT and both Qwen arms keep 49 of 49 terms, but Qwen's fields are longer
  (length ratio 1.18 against LINDAT's 1.11) and differ more from LINDAT's (similarity 0.68): it rephrases short
  fields, which a bilingual reader should look at.
- **Descriptions** (`LLM_GLOSSARY_DESCRIPTION_CHARS=300`): 1,210 of 1,230 outputs were the same, and one term fewer
  was kept.
- **EuroLLM-9B** (`ct2`) failed 96 segments (8 %) and kept 157 of 230 terms; 89 of its 96 failures are ALTO blocks
  of at most ten characters (a number, a single word), where the model runs on past the source and the output-length
  guards (`CT2_GUARD_*`) refuse the reply; a check also rejects the source copied back unchanged. **EuroLLM-1.7B**
  failed 584 (47 %). The guard turns each failure into an `untranslated` field rather than a wrong one.
- **The vocabulary**: the bake-off has no arm without it, so it shows that the curated list costs nothing (LINDAT and
  Qwen keep 98 % of its terms), not what it adds.

The speeds come from different GPUs, and LINDAT's from a public endpoint over the network.

## Suggested next steps

1. A bilingual read of the metadata fields where Qwen3.8 and LINDAT differ most (similarity 0.68 on the 37 fields),
   with AMČR, before Qwen is offered for `metadata-en`.
2. `Qwen/Qwen3.8-27B-FP8` on two 24 GB cards, to size a deployment that has no 80 GB card.
3. AMČR's written permission for LINDAT; until then a run that must avoid CC BY-NC-SA components uses Qwen3.8.
4. Further model exploration (Qwen with the vocabulary fed whole, other sizes, MADLAD-400) after the pilot, as decided
   on 30 September 2026.

## The candidate survey (June 2026)

The comparison posted on #4 when the repository opened, before any measurement. It chose an architecture rather than
a model: the pluggable backend interface, with the choice left to the bake-off above. Prices and model facts are as
they stood then.

### Why alternatives were sought

The pipeline translated archaeological archival text (Czech to English, ALTO OCR pages and AMČR metadata) through
one hard-coded backend, the LINDAT/CUBBITT bilingual NMT REST API (`processors/translator.py → LindatTranslator`).
Since 2026-09 the backend is no longer hard-coded: `processors/backend.py` registers three, selected with
`--backend` or `TRANSLATION_BACKEND`, and `ct2` needs `requirements-ct2.txt` and the `CT2_*` variables only when it is
used, its heavy imports deferred to the first translation.

The pipeline's contract with a backend is narrow, `translate(text, src_lang, tgt_lang="en") -> str` plus the
vocabulary attributes and `supported_models`, which made the translator the natural seam for pluggable backends
([the backend interface](../development.md#the-backend-interface)). Two properties of the setup motivated the
search:

1. **No native terminology control.** CUBBITT cannot accept a glossary, so the repository carries the
   Tag-and-Protect workaround: UDPipe lemmatisation (`processors/lemmatizer.py`) finds vocabulary terms, replaces them
   with placeholders (`Xtermzzz<N>z`), sends the tagged text to CUBBITT, and restores the vocabulary translations
   afterwards. That is brittle: a model can mangle, reorder or split placeholders.
2. **The licences.** `para_licenses.py` computes the most restrictive component licence as the run's summary, and
   CUBBITT (CC BY-NC-SA 4.0) with the UDPipe models (CC BY-NC-SA 4.0) make it CC BY-NC-SA 4.0 for every run
   ([licences](../reference/licences.md)).

### At a glance

| Backend                        | Type                             | Deploy                            | Languages (vs the repository's set)                           | Native glossary         | Licence (weights / API)                                       | Approx. cost                         |
|--------------------------------|----------------------------------|-----------------------------------|---------------------------------------------------------------|-------------------------|---------------------------------------------------------------|--------------------------------------|
| **LINDAT/CUBBITT** (baseline)  | Bilingual NMT                    | Free ÚFAL API                     | cs + major European pairs                                     | No                      | CC BY-NC-SA 4.0                                               | Free                                 |
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

The repository's `LanguageIdentifier` supported cs, en, fr, de, ru, pl, uk, sk, bg, hr, sl, lv, lt, et, hu, ro, es,
it, nl and hi when the survey was written (the map now has 43 languages:
[Source-language identification](../how-it-works.md#source-language-identification)). Of the candidates:

- **Full coverage (all 20):** MADLAD-400, NLLB-200, EuroLLM, Google Cloud Translation.
- **Good coverage (about 15 of 20):** Cohere Command A Translate (missing sk, bg, hr, sl, lv, lt, et, hu).
- **Partial:** GLM-5.2 (a general LLM, not pair-specific), Tower+ (22 languages, but a different subset).
- **Pair-specific:** CUBBITT, Opus-MT (one model per pair).

A backend's `supported_languages()` lets the pipeline check coverage; falling back to another backend for an
uncovered pair is not wired: such a pair is refused ([The three backends](../how-it-works.md#the-three-backends)).

### Detailed assessments

#### Cohere Command A Translate

- **Released:** August 2025. A 111B-parameter LLM built for translation.
- **Languages:** 23, including Czech, Ukrainian, Polish, Russian, German, French, Spanish, Italian, Dutch, Romanian,
  Greek and Hindi.
- **Key strength: native glossary and document mode.** Glossary support could retire Tag-and-Protect: vocabulary
  terms would be passed as structured glossary entries rather than injected as placeholders. Document mode keeps the
  context across paragraphs, which benefits ALTO page-level translation.
- **Deployment:** a hosted Cohere API endpoint, and self-hostable research weights on Hugging Face
  ([CohereLabs/command-a-translate-08-2025](https://huggingface.co/CohereLabs/command-a-translate-08-2025));
  self-hosting needs one or two A100/H100 GPUs.
- **Licence:** research weights under **CC-BY-NC** (non-commercial); commercial use needs the paid Cohere API or a
  private deployment agreement.
- **Gaps:** 8 of the 20 repository languages missing (sk, bg, hr, sl, lv, lt, et, hu); those pairs would need
  CUBBITT or MADLAD-400.
- **References:** [Cohere announcement](https://docs.cohere.com/changelog/2025-08-28-command-a-translate),
  [model documentation](https://docs.cohere.com/docs/command-a-translate),
  [Hugging Face weights](https://huggingface.co/CohereLabs/command-a-translate-08-2025),
  [Slator coverage](https://slator.com/cohere-enterprise-ai-translation-command-a-translate/).

#### GLM-5.2 (Zhipu / Z.ai)

- **Released:** June 2026. A 744B MoE general-purpose LLM from Zhipu AI.
- **Languages:** broadly multilingual, but trained mainly on English and Chinese.
- **Key strength: MIT-licensed open weights.** The computed summary would drop to whatever the *other* most
  restrictive component is, possibly free of NC-SA if UDPipe is replaced too.
- **Deployment:** API only in practice; at 744B parameters (MoE) self-hosting needs multi-node GPU clusters. Z.ai API
  pricing: about $1.40 input and $4.40 output per million tokens.
- **Terminology:** no native glossary; terminology must go into the system prompt, which is less reliable than a
  structured glossary API.
- **Risks for this domain:** not specialised in translation, so a general LLM can hallucinate content that was not
  in the source, unacceptable for archival translation; archaeological OCR text has fragments, diacritics errors and
  layout artefacts that a generalist LLM may "helpfully" correct, altering the source meaning. Guardrails
  (output-length validation, source-faithfulness checks) would be needed.
- **Upside:** if ATRIUM adopted one frontier LLM for several NLP tasks (NER, summarisation, translation), GLM-5.2
  could serve all of them through one API integration.
- **References:** [Hugging Face: zai-org/GLM-5.2](https://huggingface.co/zai-org/GLM-5.2) (MIT),
  [GLM-5 blog](https://huggingface.co/blog/mlabonne/glm-5).

#### MADLAD-400 (Google)

- **Released:** 2023. T5-based multilingual NMT in 3B, 7B and 10B sizes.
- **Languages:** 400+, including **all** the languages of the repository's identifier.
- **Key strength: Apache-2.0 and the broadest coverage.** The only self-hostable option at the time that allowed a
  fully permissive set of components. The 3B variant is CPU-viable for moderate throughput.
- **Deployment:** self-host only (no hosted API); 3B fits on one GPU or a CPU, 10B needs a mid-range GPU.
- **Terminology:** no native glossary; Tag-and-Protect would still be needed (or a prompt-based approach if the model
  is used for text generation).
- **Quality:** strong on high-resource pairs (cs-en); competitive with or better than Google Translate on many
  benchmarks. Robust to noisy OCR input, since it is a seq2seq NMT model, not a generative LLM.
- **References:** [MADLAD-400 paper (NeurIPS 2023)](https://proceedings.neurips.cc/paper_files/paper/2023/file/d49042a5d49818711c401d34172f9900-Paper-Datasets_and_Benchmarks.pdf),
  [Hugging Face: google/madlad400-3b-mt](https://huggingface.co/google/madlad400-3b-mt).

#### EuroLLM (UTTER project)

- **Released:** 2024. Decoder-only multilingual LLMs (Llama architecture) in 1.7B and 9B sizes, with
  instruction-tuned variants trained for translation.
- **Languages:** 35, every EU official language plus uk, ru, hi and others: **all** 20 of the identifier's languages.
- **Licence:** **Apache-2.0**, like MADLAD-400. EuroLLM-9B-Instruct is gated on Hugging Face (accept the terms before
  downloading).
- **Deployment:** the `ct2` backend's default family (`CT2_MODEL_FAMILY=eurollm`): converted to CTranslate2
  (`ct2-transformers-converter --quantization int8`) and run as a `Generator` with the checkpoint's own Hugging Face
  tokenizer and chat template (`CT2_TOKENIZER_DIR`; unset, `CT2_MODEL_DIR` when converted with
  `--copy_files tokenizer_config.json tokenizer.model special_tokens_map.json`, else the folder of `CT2_SP_MODEL`);
  `requirements-ct2.txt` brings Transformers and Jinja2 for the template. Generation stops at the tokenizer's EOS and
  at the chat markers it knows (`<|im_end|>`, `<|im_start|>`). 1.7B is CPU-viable at int8; 9B wants a GPU.
- **On a GPU:** CTranslate2 4.x runs on CUDA 12 and loads `libcublas.so.12`, which a CUDA 13 PyTorch in the same
  environment does not bring: `pip install nvidia-cublas-cu12` and put its `lib` folder on `LD_LIBRARY_PATH`
  (`requirements-ct2.txt` has the line), or use a CUDA 12 runtime. A Blackwell GPU (compute capability 12.x) needs
  cuBLAS 12.8 or newer: CUDA 12.3's answered `CUBLAS_STATUS_NOT_SUPPORTED` to every prompt on an RTX PRO 6000 (job
  7144164). `int8_float16` needs compute capability 7.0 or newer. Without a working cuBLAS the model loads and every
  generation fails, as in the bake-off jobs of 2026-10-18 (1,230 of 1,230 segments), so `warm()` runs a few tokens of
  a record-sized prompt, built as a translation's is, after the load; the service and the CLI stop there with the fix
  (or `CT2_DEVICE=cpu`).
- **Prompt:** the user turn is EuroLLM-Instruct's documented translation format, with the languages in words
  (`Translate the following Czech source text to English:\nCzech: … \nEnglish: `); the instructions sit in the system
  turn.
- **Terminology:** no glossary API, but instructable: the `ct2` backend puts the matching vocabulary pairs in the
  system turn (`supports_glossary = True`), so Tag-and-Protect and UDPipe are not needed. A glossary the model writes
  back (`Use these exact terms: bod = point …`, `term = term` lines, the prompt's `Czech:` / `English:` labels) is cut
  from the start and end of the reply; a reply that was only the glossary, or the untranslated source, is requested
  once more without the glossary. In the first AMČR run (2026-09-27, glossary still in the user turn) EuroLLM-1.7B
  wrote the glossary into 13 of 30 fields.
- **Quality:** measured in the bake-off ([the result](#the-result-2026-10-19)): 9B failed 96 of 1,230 segments and
  kept 157 of 230 glossary terms, 1.7B failed 584.
- **References:** [Hugging Face: utter-project/EuroLLM-1.7B-Instruct](https://huggingface.co/utter-project/EuroLLM-1.7B-Instruct),
  [utter-project/EuroLLM-9B-Instruct](https://huggingface.co/utter-project/EuroLLM-9B-Instruct).

#### NLLB-200 (Meta)

- **Released:** 2022. Multilingual NMT for 200 languages, 0.6B to 3.3B.
- **Languages:** 200+, including all the repository's languages.
- **Licence:** **CC-BY-NC 4.0**, the same non-commercial constraint as the setup it would replace.
- **Quality:** strong on low-resource pairs; competitive on cs-en.
- **Notes:** a solid self-hosted alternative that does not improve the licence situation; useful as a lightweight
  fallback for pairs other backends do not cover.

#### Tower+ (Unbabel)

- **Released:** 2024–2025. A translation-tuned LLM built on Qwen2.5, in 9B and 72B sizes; 22 languages including
  Czech.
- **Quality:** strong WMT results; tuned for translation quality metrics (COMET, BLEURT).
- **Licence:** research-oriented, subject to the Qwen base model's licence terms.
- **Deployment:** self-host on a GPU; 9B fits on one A100, 72B needs several GPUs.
- **References:** [Hugging Face: Unbabel/Tower-Plus-9B](https://huggingface.co/Unbabel/Tower-Plus-9B),
  [Unbabel/Tower-Plus-72B](https://huggingface.co/Unbabel/Tower-Plus-72B).

#### Opus-MT (Helsinki-NLP)

- **A light, permissive fallback.** Marian NMT bilingual models for many pairs, including cs-en; CPU-viable (about
  200M parameters per pair); lower quality than the larger models but very fast and easy to deploy; useful offline or
  at the edge.
- **Licence split:** the original checkpoints (such as `opus-mt-cs-en`) are Apache-2.0, the newer
  `opus-mt-tc-big-*` line **CC BY 4.0**. Both are permissive and rank the same in `para_licenses.py`, but CC BY adds
  an attribution duty, so `para_config.txt` records `opus_mt` as CC BY 4.0 whichever checkpoint is converted.
- **References:** [GitHub: Helsinki-NLP/Opus-MT](https://github.com/Helsinki-NLP/Opus-MT).

#### DeepL API

- **The highest European translation quality** in independent benchmarks. A native glossary API; EU-hosted (data
  residency); proprietary and paid (about $25 per million characters on the Pro plan). Glossary support would replace
  Tag-and-Protect. A strong candidate if the budget allows.
- **References:** [DeepL API](https://www.deepl.com/en/products/api).

#### Google Cloud Translation

- **Broad coverage, glossary support, paid.** It had recently added an LLM-based translation tier. A good baseline,
  but DeepL did better on European pairs.

### API or self-hosted

| Factor               | API-based                                          | Self-hosted                                     |
|----------------------|----------------------------------------------------|-------------------------------------------------|
| Setup cost           | Low (API key + HTTP client)                        | High (GPU infra, model download, serving stack) |
| Per-unit cost        | Per token or character (ongoing)                   | Fixed infra (amortised over volume)             |
| Data residency       | Depends on provider (DeepL: EU; Cohere/GLM: check) | Full control (EU servers)                       |
| Latency              | Network-bound; provider SLAs                       | Local; predictable                              |
| Maintenance          | Provider handles updates                           | Model updates, serving, scaling are yours       |
| Offline / air-gapped | Not possible                                       | Possible (Opus-MT, MADLAD, NLLB)                |
| Best for             | Prototyping, variable volume, no GPU budget        | High volume, data-sensitive, EU-only, offline   |

### The terminology opportunity

Backends with native glossary support (Command A Translate, the DeepL API, Google Cloud Translation) could replace the
whole Tag-and-Protect pipeline:

| With CUBBITT                                  | With a glossary-native backend |
|-----------------------------------------------|--------------------------------|
| `processors/lemmatizer.py` (UDPipe API calls) | not needed                     |
| Placeholders (`Xtermzzz<N>z`)                 | not needed                     |
| Tolerant placeholder restoration              | not needed                     |
| `_scrub_placeholder_fragments()`              | not needed                     |
| Number-agreement guard (plurals skipped)      | handled by the model           |
| Homonym misalignment risk                     | eliminated                     |
| UDPipe's CC BY-NC-SA models                   | removed from the run           |

The vocabulary CSV would go directly to the backend's glossary API. The backend interface has a `supports_glossary`
flag so the pipeline decides at run time whether to run Tag-and-Protect or to hand the terms to the backend; the
language-model backends (`openai_compatible`, `ct2` EuroLLM) use it today for a prompt glossary.

A glossary-native backend that retires Tag-and-Protect also removes the UDPipe dependency, so it changes the computed
licence summary in one move ([the candidate backends' licences](../reference/licences.md#the-candidate-backends)).
