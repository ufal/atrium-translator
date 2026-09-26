"""
processors/ct2_translator.py – CTranslate2 self-host translation backend
(issue #4, Phase 3).

Goal
----
The free LLM API (Phase 1) is the prototype; for full-corpus runs without
free-tier rate limits the target is a **permissive, low-resource self-host**
stack: CTranslate2 int8 (≈4× smaller, 2–8× faster on CPU) running an Apache-2.0
model — **EuroLLM-1.7B/9B** (instruction-tuned, all 20 repo languages) or
**MADLAD-400** (multilingual NMT). Combined with an explicit ``--source_lang``
and a permissive/empty glossary this yields a CC-BY-NC-free output (see the
"permissive recipe" in docs/translation-backends.md §5).

Status
------
**Registered** in ``processors/backend._ensure_registry`` as ``ct2``, so
``--backend ct2`` / ``TRANSLATION_BACKEND=ct2`` select it.  Registration is cheap:
this module imports only the standard library and sibling modules at import
time, so importing ``processors/backend.py`` still never pulls in the heavy
``ctranslate2`` / ``sentencepiece`` dependencies.  To *use* it, install
``requirements-ct2.txt``, convert a model to the CTranslate2 format and set the
``CT2_*`` env vars; until then ``translate`` raises a :class:`TranslationError`
naming what is missing.

The two generation paths differ by model family:
  * ``eurollm`` (decoder-only, instructable)  → ``ctranslate2.Generator`` with a
    chat-style prompt; ``supports_glossary = True`` (prompt-injected glossary);
  * ``madlad`` / ``nllb`` / ``opus`` (encoder-decoder NMT) →
    ``ctranslate2.Translator`` with a target-language token; ``supports_glossary
    = False``.

Heavy imports (``ctranslate2``, ``sentencepiece``) are deferred to first use, so
constructing the backend and checking its Protocol conformance needs neither the
libraries nor a converted model.

Configuration (env, or constructor kwargs)
------------------------------------------
    CT2_MODEL_DIR      Path to the converted CTranslate2 model directory (required).
    CT2_MODEL_FAMILY   "eurollm" (default) | "madlad" | "nllb" | "opus".
    CT2_SP_MODEL       Path to the SentencePiece model (required for NMT families).
    CT2_DEVICE         "cpu" (default) | "cuda".
    CT2_COMPUTE_TYPE   "int8" (default) | "default" | "auto" | any type the device
                       supports: ``ctranslate2.get_supported_compute_types(device)``
                       (CPU: int8, int8_float32, int16, float32; CUDA adds e.g.
                       int8_float16, float16, bfloat16).  Checked when the model
                       loads; an unsupported value raises TranslationError listing
                       the valid ones.  CTranslate2 has no 4-bit type.
    CT2_LANGUAGES      Comma-separated ISO codes advertised by supported_languages().
"""

from __future__ import annotations

import os
from pathlib import Path

from .chunking import chunk_text
from .quality import degeneration_reason
from .translator import DegenerateTranslationError, TranslationError
from .vocab import get_matching_terms, load_vocabulary


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Output-faithfulness guardrail thresholds (see _guard).
_MIN_RATIO_CHARS = _env_int("CT2_GUARD_MIN_CHARS", 16)
_MIN_LEN_RATIO = _env_float("CT2_GUARD_MIN_RATIO", 0.25)
_MAX_LEN_RATIO = _env_float("CT2_GUARD_MAX_RATIO", 4.0)

# Model family -> para_config.txt component carrying that model's licence.
# Every family must map to a declared component: an undeclared name is logged as
# "UNKNOWN" by atrium_paradata, which is not a licence anyone can comply with.
_FAMILY_COMPONENTS = {
    "eurollm": "eurollm",
    "madlad": "madlad400",
    "nllb": "nllb200",
    "opus": "opus_mt",
}
# Families that use the encoder-decoder NMT path (vs. decoder-only LLM path).
_NMT_FAMILIES = {"madlad", "nllb", "opus"}
# Accepted by CTranslate2 on every device, in addition to the device's own types.
_PORTABLE_COMPUTE_TYPES = ("default", "auto")
_MAX_GLOSSARY_TERMS = 40


class CT2Translator:
    """CTranslate2 self-host backend (EuroLLM / MADLAD-400 / NLLB-200 / Opus-MT).

    Configuration: see the module docstring.  ``CT2_COMPUTE_TYPE`` defaults to
    ``int8``, CTranslate2's smallest quantisation and the one supported on every
    CPU and CUDA device; ``int8_float16`` / ``float16`` trade memory for speed
    on a GPU with headroom.  There is no 4-bit type in CTranslate2.
    """

    name: str = "ct2"

    def __init__(
        self,
        vocab_path=None,
        *,
        model_dir: str | None = None,
        family: str | None = None,
        sp_model: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        languages: list | None = None,
    ) -> None:
        self.model_dir = model_dir if model_dir is not None else os.environ.get("CT2_MODEL_DIR", "")
        self.family = (family if family is not None else os.environ.get("CT2_MODEL_FAMILY", "eurollm")).lower().strip()
        self.sp_model = sp_model if sp_model is not None else os.environ.get("CT2_SP_MODEL", "")
        self.device = device if device is not None else os.environ.get("CT2_DEVICE", "cpu")
        self.compute_type = compute_type if compute_type is not None else os.environ.get("CT2_COMPUTE_TYPE", "int8")

        # Encoder-decoder NMT families have no glossary mechanism; EuroLLM (LLM)
        # accepts an instruction glossary, so it can own terminology like the LLM
        # API backend does.
        self.supports_glossary: bool = self.family not in _NMT_FAMILIES

        if languages is not None:
            self._languages = list(languages)
        else:
            env_langs = os.environ.get("CT2_LANGUAGES", "")
            self._languages = [c.strip() for c in env_langs.split(",") if c.strip()]

        self.vocabulary: dict = load_vocabulary(Path(vocab_path)) if vocab_path else {}
        self._protected_count: int = 0

        # Lazily-initialised heavy handles (see _ensure_loaded).
        self._engine = None
        self._sp = None

    # ── TranslationBackend Protocol ───────────────────────────────────────────

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        if not text or not text.strip() or src_lang == tgt_lang:
            return text
        self._ensure_loaded()
        chunks = chunk_text(text)
        if self.family in _NMT_FAMILIES:
            out = [self._translate_nmt(c, src_lang, tgt_lang) for c in chunks]
        else:
            out = [self._translate_llm(c, src_lang, tgt_lang) for c in chunks]
        return "\n".join(out)

    def supported_languages(self) -> list:
        return list(self._languages)

    # ── pipeline-compat surface ───────────────────────────────────────────────

    def reset_protected_count(self) -> None:
        self._protected_count = 0

    @property
    def protected_count(self) -> int:
        return self._protected_count

    def license_components(self, vocab_loaded: bool = False) -> list:
        """Permissive component stack (see para_config.txt).

        The CTranslate2 engine is MIT; the model component follows the family
        (:data:`_FAMILY_COMPONENTS`).  EuroLLM and MADLAD-400 are Apache-2.0, so
        such a run is permissive *as long as* FastText langid (CC-BY-NC) is
        avoided via an explicit ``--source_lang`` and the glossary is
        permissive/empty.  NLLB-200 is CC BY-NC 4.0, so choosing it makes the
        output non-commercial whatever else the run does.  Opus-MT is recorded
        as CC BY 4.0 (the tc-big line; older checkpoints are Apache-2.0, which
        ranks the same), so it stays permissive but requires attribution.
        """
        # An unknown family never gets this far in a real run (_ensure_loaded
        # refuses it); passing the raw name through makes paradata report an
        # unrecognised licence, which para_licenses treats as maximally
        # restrictive, rather than silently claiming a permissive one.
        model_comp = _FAMILY_COMPONENTS.get(self.family, self.family)
        comps = ["ctranslate2", model_comp]
        if vocab_loaded:
            # A loaded AMCR/TEATER glossary re-introduces the CC-BY-NC vocab data.
            comps += ["amcr_vocab", "teater_data"]
        return comps

    # ── lazy model loading ────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._engine is not None:
            return
        if not self.model_dir:
            raise TranslationError(
                "CT2Translator is not configured: set CT2_MODEL_DIR to a converted "
                "CTranslate2 model directory (see docs/translation-backends.md)."
            )
        if self.family not in _FAMILY_COMPONENTS:
            raise TranslationError(
                f"Unknown CT2_MODEL_FAMILY {self.family!r}; expected one of: {', '.join(sorted(_FAMILY_COMPONENTS))}."
            )
        try:
            import ctranslate2  # noqa: PLC0415
        except ImportError as e:
            raise TranslationError(
                f"ctranslate2 is not installed. Install requirements-ct2.txt to use the 'ct2' backend ({e})."
            )

        self._check_compute_type(ctranslate2)
        engine_cls = ctranslate2.Translator if self.family in _NMT_FAMILIES else ctranslate2.Generator
        try:
            self._engine = engine_cls(self.model_dir, device=self.device, compute_type=self.compute_type)
        except ValueError as e:
            # CTranslate2 reports a bad device or compute type as a bare
            # ValueError; surface it as the backend's own failure type.
            raise TranslationError(
                f"CTranslate2 rejected device={self.device!r} compute_type={self.compute_type!r}: {e}"
            ) from e
        if self.family in _NMT_FAMILIES:
            self._sp = self._load_sp()
        else:
            self._sp = self._load_sp() if self.sp_model else None

    def _check_compute_type(self, ctranslate2) -> None:
        """Refuse a compute type *device* cannot run, naming the ones it can.

        CTranslate2 only says ``Invalid compute type: int4`` for an unknown
        name, and nothing at all about which names are valid on this device.
        """
        try:
            supported = set(ctranslate2.get_supported_compute_types(self.device))
        except (RuntimeError, ValueError) as e:
            # e.g. CT2_DEVICE=cuda on a host without a usable CUDA driver.
            raise TranslationError(f"CT2_DEVICE={self.device!r} is not usable: {e}") from e
        valid = sorted(supported) + list(_PORTABLE_COMPUTE_TYPES)
        if self.compute_type not in valid:
            raise TranslationError(
                f"CT2_COMPUTE_TYPE={self.compute_type!r} is not supported on device {self.device!r}; "
                f"valid values: {', '.join(valid)}."
            )

    def _load_sp(self):
        if not self.sp_model:
            raise TranslationError(f"CT2_SP_MODEL is required for the '{self.family}' family but was not set.")
        try:
            import sentencepiece as spm  # noqa: PLC0415
        except ImportError as e:
            raise TranslationError(f"sentencepiece is not installed (install requirements-ct2.txt): {e}")
        return spm.SentencePieceProcessor(model_file=self.sp_model)

    # ── generation paths (model-family specific) ──────────────────────────────

    def _translate_nmt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """Encoder-decoder NMT (MADLAD/NLLB/Opus).

        MADLAD-400 prefixes the *source* with a ``<2xx>`` target-language token;
        NLLB uses a target prefix token on the decoder side. The exact tokens are
        model-specific to the converted checkpoint — adjust here per model.
        """
        tokens = self._sp.encode(text, out_type=str)
        if self.family == "madlad":
            tokens = [f"<2{tgt_lang}>"] + tokens
            target_prefix = None
        elif self.family == "nllb":
            target_prefix = [[self._nllb_code(tgt_lang)]]
        else:  # opus tc-big and similar
            target_prefix = None

        kwargs = {"beam_size": 4, "max_decoding_length": 2048}
        if target_prefix is not None:
            kwargs["target_prefix"] = target_prefix
        result = self._engine.translate_batch([tokens], **kwargs)
        out_tokens = result[0].hypotheses[0]
        if target_prefix is not None and out_tokens[: len(target_prefix[0])] == target_prefix[0]:
            out_tokens = out_tokens[len(target_prefix[0]) :]
        translated = self._sp.decode(out_tokens)
        self._guard(text, translated)
        return translated

    def _translate_llm(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """Decoder-only instruction model (EuroLLM): prompt → generate.

        Uses the model's own tokenizer when a SentencePiece model is supplied;
        otherwise relies on CTranslate2's built-in vocabulary mapping. The prompt
        template mirrors EuroLLM's instruction format; adjust to the exact chat
        template of the converted checkpoint.
        """
        glossary = self._glossary_lines(text)
        gloss = ("\nUse these exact terms: " + "; ".join(glossary)) if glossary else ""
        if glossary:
            self._protected_count += len(glossary)
        prompt = (
            f"Translate the following text from {src_lang} to {tgt_lang}. "
            f"Output only the translation, preserving numbers and line structure.{gloss}\n\n{text}\n\nTranslation:"
        )
        if self._sp is not None:
            tokens = self._sp.encode(prompt, out_type=str)
        else:
            tokens = self._engine.tokenize(prompt) if hasattr(self._engine, "tokenize") else list(prompt)
        result = self._engine.generate_batch(
            [tokens], max_length=2048, sampling_temperature=0.0, include_prompt_in_result=False
        )
        out_tokens = result[0].sequences[0]
        translated = (self._sp.decode(out_tokens) if self._sp is not None else "".join(out_tokens)).strip()
        self._guard(text, translated)
        return translated

    def _glossary_lines(self, text: str) -> list:
        if not self.vocabulary:
            return []
        # Shared word-boundary helper prevents short keys matching inside longer
        # unrelated words (mirrors the LLM backend fix, L1).
        pairs = get_matching_terms(text, self.vocabulary)
        pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
        return [f"{s} = {t}" for s, t in pairs[:_MAX_GLOSSARY_TERMS]]

    @staticmethod
    def _nllb_code(lang: str) -> str:
        """Map an ISO-639-1 code to an NLLB language token (extend as needed)."""
        table = {
            "en": "eng_Latn",
            "cs": "ces_Latn",
            "de": "deu_Latn",
            "fr": "fra_Latn",
            "pl": "pol_Latn",
            "sk": "slk_Latn",
            "ru": "rus_Cyrl",
            "uk": "ukr_Cyrl",
        }
        return table.get(lang, "eng_Latn")

    @staticmethod
    def _guard(source: str, translated: str) -> None:
        # DegenerateTranslationError is a TranslationError: callers that only know
        # the base class still fail loudly, while utils.py flags the segment, re-runs
        # it at the end of the document, and keeps the source if it never recovers.
        if not translated or not translated.strip():
            raise DegenerateTranslationError("CT2 backend returned an empty translation for a non-empty source chunk.")
        src_len = len(source.strip())
        if src_len >= _MIN_RATIO_CHARS:
            ratio = len(translated.strip()) / src_len
            if ratio < _MIN_LEN_RATIO or ratio > _MAX_LEN_RATIO:
                raise DegenerateTranslationError(
                    f"CT2 output/input length ratio {ratio:.2f} outside "
                    f"[{_MIN_LEN_RATIO}, {_MAX_LEN_RATIO}] — suspected hallucination or truncation."
                )
        # Repetition loops that fit the ratio, and any loop on a short source.
        reason = degeneration_reason(source, translated)
        if reason:
            raise DegenerateTranslationError(f"CT2 output rejected — {reason}.")
