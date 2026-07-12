# 📓 atrium-translator — agent_dev_logs/DEVLOG.md (timeline index)
> _XML in-place translation. 1 open issue (#4). `test` HEAD `8e9b8a2` (2026-07-12) · **v0.8.1**._
> _Per-issue detail: `digests/4.digest.md` · `plans/4.plan.md` · `issues/` export (source of truth). Cross-repo/hub history lives in `ufal/atrium-project/agent_dev_logs/DEVLOG.md` (deduplicated out of this file)._

## 2026-06-20
- **#4 Translation base model to use** — Opened by K4TEL: explore base models for text-to-text translation (GLM-5.2, Cohere Command A, and the open-source space).

## 2026-06-21
- **#4** — Posted a candidate comparison (CUBBITT baseline, Command A, GLM-5.2, MADLAD-400, NLLB-200, Tower+, Opus-MT, DeepL, Google) and a pluggable `TranslationBackend` design; recommendation: prototype Command A (native glossary retires Tag-and-Protect), MADLAD-400 (Apache-2.0) as the permissive self-host path, keep CUBBITT as default.

## 2026-06-22
- **#4** — Refined free + low-resource plan (adds EuroLLM; phases 0–3; full licensing recipe incl. FastText/vocab NC traps). Implementation landed: the previously-dead `get_backend` seam wired in (default still `lindat`, zero behaviour change), the missing `docs/translation-backends.md` written, an `openai_compatible` LLM adapter (one OpenAI-compatible client → many free providers, prompt-glossary, OCR-faithfulness guards), `eval/bakeoff.py`, and a CTranslate2 scaffold; 217 tests pass; PR body "Closes #4".

## 2026-06-23
- **#4** — Hardening/remediation cycle: anti-truncation `max_tokens`/`max_decoding_length`, CT2 length-ratio faithfulness guards, word-boundary glossary matching, a fix for the FastAPI `/translate` streaming race (buffer to in-memory `Response`), one-time XSD schema compilation, `int4` CT2 default for VRAM safety, backend-aware paradata licensing, and Dockerfile immutability; 220/220 tests green; released **v0.7.0** for practical testing.

## 2026-06-27
- **#4** — Digest+plan refreshed on `test` (`40ff9be`). **v0.8.0** cut around this refresh: per-page ALTO calls instead of per-block, paradata scripts synced to the atrium-project template, `agent_dev_logs/` added.

## 2026-07-12
- **#4** — API version drift **resolved** (`953e780` "fix version reading"): `_read_tool_version()` at `service/api.py:29-39` reads `para_config.txt [tool] version`, so `/info` can no longer drift from the release tag. Released **v0.8.1** (automatic version reading + the shared `tests/test_para_licenses.py`, dependency bumps); suite at **228 tests** green. Digest re-verified against code: the backend architecture is verified live, and what genuinely remains is the **model-selection answer itself** — run `eval/bakeoff.py` (CUBBITT vs LLM ±glossary) on AMCR + ALTO samples, live-smoke the LLM backend via CLI + `/translate`, finish the `ct2` permissive recipe (EuroLLM-1.7B / MADLAD-400-3B conversion) — plus the `docs/translation-backends.md` relocation (verified `docs/` still doesn't exist).

---
_Timeline index refreshed 2026-07-12 against `test` HEAD and the refreshed digest/plan. Nothing removed from the issue itself (per hub #29); this file is a derived reading aid in `agent_dev_logs/`._
