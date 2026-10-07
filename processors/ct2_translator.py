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
"Licensing matrix (informational)" section of docs/translation-backends.md).
Status
------
**Registered** in ``processors/backend._ensure_registry`` as ``ct2``, so
``--backend ct2`` / ``TRANSLATION_BACKEND=ct2`` select it. Registration is cheap:
this module imports only the standard library and sibling modules at import
time, so importing ``processors/backend.py`` still never pulls in the heavy
``ctranslate2`` / ``sentencepiece`` / ``transformers`` dependencies. To *use*
it, install ``requirements-ct2.txt``, convert a model to the CTranslate2 format
and set the ``CT2_*`` env vars; until then ``translate`` raises a
:class:`TranslationError` naming what is missing.
The two generation paths differ by model family:
  * ``eurollm`` (decoder-only, instructable) → ``ctranslate2.Generator`` with
    the checkpoint's Hugging Face tokenizer + chat template; ``supports_glossary
    = True`` (prompt-injected glossary);
  * ``madlad`` / ``nllb`` / ``opus`` (encoder-decoder NMT) →
    ``ctranslate2.Translator`` with a target-language token; ``supports_glossary
    = False``.
Heavy imports (``ctranslate2``, ``sentencepiece``, ``transformers``) are deferred
to first use, so constructing the backend and checking its Protocol conformance
needs neither the libraries nor a converted model.
Configuration (env, or constructor kwargs)
------------------------------------------
    CT2_MODEL_DIR      Path to the converted CTranslate2 model directory (required).
    CT2_MODEL_FAMILY   "eurollm" (default) | "madlad" | "nllb" | "opus".
    CT2_SP_MODEL       Path to the SentencePiece model (required for NMT families).
    CT2_TOKENIZER_DIR  Path to the Hugging Face tokenizer files (EuroLLM). Unset: CT2_MODEL_DIR
                       when the converter copied them there (--copy_files), else the
                       directory of CT2_SP_MODEL when it holds a Hugging Face checkpoint.
    CT2_DEVICE         "cpu" (default) | "cuda".
    CT2_COMPUTE_TYPE   "int8" (default) | "default" | "auto" | any type the device
                       supports: ``ctranslate2.get_supported_compute_types(device)``
                       (CPU: int8, int8_float32, int16, float32; CUDA adds e.g.
                       int8_float16, float16, bfloat16). Checked when the model
                       loads; an unsupported value raises TranslationError listing
                       the valid ones. CTranslate2 has no 4-bit type.
    CT2_LANGUAGES      Comma-separated ISO codes advertised by supported_languages().
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

from tool_limits import CT2_MAX_DECODING_TOKENS, CT2_MAX_GLOSSARY_TERMS, CT2_MAX_INPUT_TOKENS

from .chunking import chunk_for_translation, chunk_text
from .limit_notes import note
from .quality import degeneration_reason
from .translator import DegenerateTranslationError, TranslationError
from .vocab import get_matching_terms, load_vocabulary

logger = logging.getLogger(__name__)


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
# A limit since atrium-project#53 (CT2_MAX_GLOSSARY_TERMS in tool_limits.py, read per use);
# this name is its value at import, kept for the callers and tests that read it.
_MAX_GLOSSARY_TERMS = CT2_MAX_GLOSSARY_TERMS.get()


# Languages named in words for the EuroLLM prompt: its instruction data says
# "Translate the following Czech source text to English", not "from cs to en".
_LANGUAGE_NAMES = {
    "bg": "Bulgarian",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "hr": "Croatian",
    "hu": "Hungarian",
    "it": "Italian",
    "la": "Latin",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mt": "Maltese",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sv": "Swedish",
    "uk": "Ukrainian",
}

# Decoding budget of one EuroLLM reply: this many times its source's tokens, plus the slack.
_RUNAWAY_FACTOR = 4
_RUNAWAY_SLACK = 32

# Files by which a directory is recognised as holding a Hugging Face tokenizer.
_TOKENIZER_FILES = ("tokenizer_config.json", "tokenizer.json", "tokenizer.model")

# Chat markers that close the assistant turn (see CT2Translator._end_tokens).
_TURN_END_TOKENS = ("<|im_end|>", "<|im_start|>", "</s>", "<|endoftext|>")

# The first line of a glossary written back into the reply — this prompt's wording and
# the one before it ("Use these exact terms: bod = point; …").
_GLOSSARY_HEADER = re.compile(r"^\s*(?:use these exact terms|terminology)\b[^\n]*:", re.IGNORECASE)
# One "term = term" item of that glossary.
_GLOSSARY_PAIR = re.compile(r"^[^=]+\s=\s[^=]+$")


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get((code or "").lower().split("-")[0], code)


def _until_turn_end(tokens: list) -> list:
    """*tokens* up to the first chat marker: what follows it is not this answer."""
    for i, tok in enumerate(tokens):
        if tok in _TURN_END_TOKENS:
            return tokens[:i]
    return tokens


def _is_glossary_line(line: str, source: str) -> bool:
    """A line of ``term = term`` pairs, in a reply to a source that has no ``=`` of its own."""
    if "=" in source:
        return False
    items = [item.strip() for item in line.split(";") if item.strip()]
    return bool(items) and all(_GLOSSARY_PAIR.match(item) for item in items)


def _strip_prompt_echo(source: str, translated: str, src_name: str, tgt_name: str) -> str:
    """Remove prompt material an instruction model wrote around its translation.

    Seen with EuroLLM-1.7B on the AMCR samples (2026-09-27), logged as ``ok``: the
    glossary before the translation (``"Use these exact terms: bod = point\\n\\nNavigation
    point: …"``) or in place of it (``"Terrain edge = terrain edge; Hillfort = hillfort"``).
    Removed here, at the start and at the end of the reply only:

    * a glossary header line, unless the source itself opens with those words;
    * lines of ``term = term`` pairs, unless the source has an ``=``;
    * the ``<Source>:`` line and ``<Target>:`` label of the prompt's own format.

    The result can be empty; the caller decides what an empty or copied reply means.
    """
    lines = translated.strip().split("\n")

    # "<Source>: …" repeated, then "<Target>: translation" → keep what follows the last label.
    tgt_label, src_label = f"{tgt_name}:", f"{src_name}:"
    labelled = [i for i, line in enumerate(lines) if line.strip().startswith(tgt_label)]
    if labelled and (labelled[-1] > 0 or not source.strip().startswith(tgt_label)):
        i = labelled[-1]
        lines = [lines[i].strip()[len(tgt_label) :].strip()] + lines[i + 1 :]

    header_in_source = bool(_GLOSSARY_HEADER.match(source))
    label_in_source = source.strip().startswith(src_label)

    def is_prompt(line: str) -> bool:
        bare = line.strip()
        if not bare:
            return True
        if not header_in_source and _GLOSSARY_HEADER.match(bare):
            return True
        if not label_in_source and bare.startswith(src_label):
            return True
        return _is_glossary_line(bare, source)

    start, end = 0, len(lines)
    while start < end and is_prompt(lines[start]):
        start += 1
    while end > start and is_prompt(lines[end - 1]):
        end -= 1
    return "\n".join(lines[start:end]).strip()


def _copies_source(source: str, translated: str) -> bool:
    """True when *translated* is *source* again: the model did not translate at all.

    Judged only for text of three or more words, one of them a lower-case word of four
    or more letters — a heading or a list of names is legitimately the same in English.
    """
    if " ".join(source.casefold().split()) != " ".join(translated.casefold().split()):
        return False
    words = re.findall(r"[^\W\d_]+", source)
    return len(words) >= 3 and any(w.islower() and len(w) >= 4 for w in words)


class CT2Translator:
    """CTranslate2 self-host backend (EuroLLM / MADLAD-400 / NLLB-200 / Opus-MT).

    Configuration: see the module docstring. ``CT2_COMPUTE_TYPE`` defaults to
    ``int8``, CTranslate2's smallest quantisation and the one supported on every
    CPU and CUDA device; ``int8_float16`` / ``float16`` trade memory for speed
    on a GPU with headroom. There is no 4-bit type in CTranslate2.
    """

    name: str = "ct2"

    def __init__(
        self,
        vocab_path=None,
        *,
        model_dir: str | None = None,
        family: str | None = None,
        sp_model: str | None = None,
        tokenizer_dir: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        languages: list | None = None,
    ) -> None:
        self.model_dir = model_dir if model_dir is not None else os.environ.get("CT2_MODEL_DIR", "")
        self.family = (family if family is not None else os.environ.get("CT2_MODEL_FAMILY", "eurollm")).lower().strip()
        self.sp_model = sp_model if sp_model is not None else os.environ.get("CT2_SP_MODEL", "")
        self.tokenizer_dir = tokenizer_dir if tokenizer_dir is not None else os.environ.get("CT2_TOKENIZER_DIR", "")
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
        self._tokenizer = None
        # One backend object serves every request of the service, and the first requests can
        # arrive together: without the lock each would load its own copy of the model.
        self._load_lock = threading.RLock()

    # ── TranslationBackend Protocol ───────────────────────────────────────────
    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        if not text or not text.strip() or src_lang == tgt_lang:
            return text
        self._ensure_loaded()
        chunks = chunk_for_translation(text)  # records a `split` note (atrium-project#53)
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

    def describe(self) -> dict:
        """Which model translated, for the paradata record (the CT2_* settings in force).

        ``ct2_model`` is the converted model directory's name only (e.g.
        ``eurollm-1.7b-int8``), not its path on the host.
        """
        return {
            "ct2_model_family": self.family,
            "ct2_model": Path(self.model_dir).name if self.model_dir else "",
            "ct2_device": self.device,
            "ct2_compute_type": self.compute_type,
        }

    def license_components(self, vocab_loaded: bool = False) -> list:
        """Permissive component stack (see para_config.txt).
        The CTranslate2 engine is MIT; the model component follows the family
        (:data:`_FAMILY_COMPONENTS`). EuroLLM and MADLAD-400 are Apache-2.0, so
        such a run is permissive *as long as* FastText langid (CC-BY-NC) is
        avoided via an explicit ``--source_lang``; the AMCR/TEATER glossary is
        CC0 (atrium-project#6). NLLB-200 is CC BY-NC 4.0, so choosing it makes the
        output non-commercial whatever else the run does. Opus-MT is recorded
        as CC BY 4.0 (the tc-big line; older checkpoints are Apache-2.0, which
        ranks the same), so it stays permissive but requires attribution.
        """
        # An unknown family never gets this far in a real run (_ensure_loaded
        # refuses it); passing the raw name through makes paradata report an
        # unrecognised licence, which para_licenses treats as maximally
        # restrictive, rather than silently claiming a permissive one.
        model_comp = _FAMILY_COMPONENTS.get(self.family, self.family)
        comps = ["ctranslate2", model_comp]
        if vocab_loaded and self.supports_glossary:
            # Only a family that puts the glossary in its prompt uses the vocabulary; the
            # NMT families load --vocabulary and never read it, so it is not their component.
            comps += ["amcr_vocab", "teater_data"]
        return comps

    # ── lazy model loading ────────────────────────────────────────────────────
    def warm(self) -> None:
        """Load the model — and EuroLLM's tokenizer — now instead of on the first request.

        The service calls it at startup (``service/api.py`` lifespan), so a configuration
        error stops the service and ``/ready`` means the model is loaded.
        """
        self._ensure_loaded()
        if self.family not in _NMT_FAMILIES:
            self._get_tokenizer()

    def _ensure_loaded(self) -> None:
        if self._engine is not None:
            return
        with self._load_lock:
            if self._engine is None:  # another thread may have loaded it meanwhile
                self._load_engine()

    def _get_tokenizer(self):
        if self._tokenizer is None:
            with self._load_lock:
                if self._tokenizer is None:
                    self._tokenizer = self._load_tokenizer()
        return self._tokenizer

    def _load_engine(self) -> None:
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
            ) from e
        self._check_compute_type(ctranslate2)
        engine_cls = ctranslate2.Translator if self.family in _NMT_FAMILIES else ctranslate2.Generator
        try:
            engine = engine_cls(self.model_dir, device=self.device, compute_type=self.compute_type)
        except ValueError as e:
            # CTranslate2 reports a bad device or compute type as a bare
            # ValueError; surface it as the backend's own failure type.
            raise TranslationError(
                f"CTranslate2 rejected device={self.device!r} compute_type={self.compute_type!r}: {e}"
            ) from e
        if self.family in _NMT_FAMILIES:
            self._sp = self._load_sp()
        # Published last: a thread that sees the engine also sees what it needs.
        self._engine = engine

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
            raise TranslationError(f"sentencepiece is not installed (install requirements-ct2.txt): {e}") from e
        return spm.SentencePieceProcessor(model_file=self.sp_model)

    def _tokenizer_source(self) -> str:
        """The directory to load the EuroLLM tokenizer from.

        ``CT2_TOKENIZER_DIR`` when set; else ``CT2_MODEL_DIR`` if the converter copied the
        tokenizer there (``--copy_files``); else the directory of ``CT2_SP_MODEL`` when it
        is a Hugging Face checkpoint (the recipe that points it at ``tokenizer.model``).
        """
        if self.tokenizer_dir:
            return self.tokenizer_dir
        candidates = [self.model_dir]
        if self.sp_model:
            candidates.append(str(Path(self.sp_model).parent))
        for directory in candidates:
            if directory and any((Path(directory) / name).is_file() for name in _TOKENIZER_FILES):
                return directory
        raise TranslationError(
            "No Hugging Face tokenizer for the 'eurollm' family: set CT2_TOKENIZER_DIR to the original "
            "model directory, or convert with `ct2-transformers-converter ... --copy_files "
            "tokenizer_config.json tokenizer.model special_tokens_map.json` so CT2_MODEL_DIR holds it."
        )

    def _load_tokenizer(self):
        try:
            from transformers import AutoTokenizer  # noqa: PLC0415
        except ImportError as e:
            raise TranslationError(
                "transformers is not installed. Install requirements-ct2.txt to use the "
                f"'{self.family}' CT2 backend ({e})."
            ) from e

        tokenizer_dir = self._tokenizer_source()
        try:
            return AutoTokenizer.from_pretrained(
                tokenizer_dir,
                use_fast=False,
                local_files_only=True,
            )
        except Exception as e:
            raise TranslationError(f"Could not load the Hugging Face tokenizer from {tokenizer_dir!r}: {e}") from e

    # ── generation paths (model-family specific) ──────────────────────────────
    def _translate_nmt(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """Encoder-decoder NMT (MADLAD/NLLB/Opus).

        The source is what the checkpoint's own Hugging Face tokenizer would produce, as in
        CTranslate2's conversion guides: SentencePiece pieces followed by ``</s>`` (which
        ``SentencePieceProcessor.encode`` does not add), MADLAD-400 with its ``<2xx>``
        target-language token in front, NLLB with the source-language code in front and
        the target-language code as the decoder prefix. Written from those guides; not
        run against a converted NMT checkpoint here.
        """
        tokens = self._sp.encode(text, out_type=str) + ["</s>"]
        target_prefix = None
        if self.family == "madlad":
            tokens = [f"<2{tgt_lang}>"] + tokens
        elif self.family == "nllb":
            tokens = [self._nllb_code(src_lang)] + tokens
            target_prefix = [[self._nllb_code(tgt_lang)]]

        # CTranslate2 truncates an input longer than `max_input_length` (1024 by default)
        # WITHOUT saying so, and the translation of the rest is simply missing. Pass the
        # limit explicitly, and split a chunk that is over it rather than let it be cut
        # (atrium-project#53).
        max_input = CT2_MAX_INPUT_TOKENS.get()
        if len(tokens) > max_input:
            parts = chunk_text(text, max(len(text) // 2, 1))
            if len(parts) < 2:
                raise DegenerateTranslationError(
                    f"A chunk of {len(tokens)} tokens is over CT2_MAX_INPUT_TOKENS={max_input} and has no "
                    "boundary to split it at."
                )
            note(
                CT2_MAX_INPUT_TOKENS,
                "split",
                1,
                f"a chunk of {len(tokens)} tokens (over {max_input}) was re-split and translated in full",
            )
            return " ".join(self._translate_nmt(part, src_lang, tgt_lang) for part in parts)

        max_decoding = CT2_MAX_DECODING_TOKENS.get()
        kwargs = {"beam_size": 4, "max_decoding_length": max_decoding, "max_input_length": max_input}
        if target_prefix is not None:
            kwargs["target_prefix"] = target_prefix
        result = self._engine.translate_batch([tokens], **kwargs)
        out_tokens = result[0].hypotheses[0]
        if len(out_tokens) >= max_decoding:
            raise DegenerateTranslationError(
                f"CTranslate2 reply reached CT2_MAX_DECODING_TOKENS={max_decoding}; it is cut, not complete."
            )
        if target_prefix is not None and out_tokens[: len(target_prefix[0])] == target_prefix[0]:
            out_tokens = out_tokens[len(target_prefix[0]) :]
        out_tokens = [tok for tok in out_tokens if tok != "</s>"]
        translated = self._sp.decode(out_tokens)
        self._guard(text, translated)
        return translated

    def _translate_llm(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """Decoder-only instruction model (EuroLLM): chat template → generate.

        The glossary goes in the system turn and the text in EuroLLM's own translation
        format (see :meth:`_llm_messages`). When the reply is nothing but the glossary, or
        the untranslated source, the chunk is asked for once more without the glossary:
        an ordinary translation is better than keeping the Czech.
        """
        self._get_tokenizer()

        glossary = self._glossary_lines(text)
        translated = self._generate_llm(text, src_lang, tgt_lang, glossary)
        if glossary and (not translated or _copies_source(text, translated)):
            logger.info(
                "CT2: the reply to a %d-character chunk was the prompt glossary or the source; "
                "re-requested without the glossary.",
                len(text),
            )
            glossary = []
            translated = self._generate_llm(text, src_lang, tgt_lang, glossary)

        self._guard(text, translated)
        self._protected_count += len(glossary)
        return translated

    def _llm_messages(self, text: str, src_lang: str, tgt_lang: str, glossary: list) -> list:
        """Chat messages for one chunk.

        The user turn is the format EuroLLM-Instruct is documented with
        ("Translate the following English source text to Portuguese:\nEnglish: … \nPortuguese: "),
        with languages named in words. The instructions and the glossary sit in the system
        turn: written into the user turn next to the text (before 2026-09-28), EuroLLM-1.7B
        answered with the glossary in 13 of 30 AMCR fields.
        """
        src, tgt = _language_name(src_lang), _language_name(tgt_lang)
        system = (
            f"You translate {src} archaeological records and archival OCR text into {tgt}. "
            f"Answer with the {tgt} translation only: no notes, labels, quotes or term lists. "
            "Keep numbers, dates, codes, identifiers and the line structure as they are."
        )
        if glossary:
            system += (
                f"\nTerminology — when one of these {src} terms occurs in the text, translate it as given; "
                "do not write this list in the answer:\n" + "\n".join(glossary)
            )
        user = f"Translate the following {src} source text to {tgt}:\n{src}: {text} \n{tgt}: "
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _generate_llm(self, text: str, src_lang: str, tgt_lang: str, glossary: list) -> str:
        """One generation for *text*; the decoded reply with any echoed prompt removed."""
        messages = self._llm_messages(text, src_lang, tgt_lang, glossary)
        try:
            prompt = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        except ImportError as e:  # transformers renders chat templates with Jinja2
            raise TranslationError(f"Install requirements-ct2.txt (jinja2) for the EuroLLM chat template: {e}") from e
        encoded = self._tokenizer(prompt, add_special_tokens=False)
        tokens = self._tokenizer.convert_ids_to_tokens(encoded["input_ids"])

        # max_length counts generated tokens only (the prompt is forwarded first). A reply
        # far longer than its source is rejected by the guard anyway, so it is stopped at
        # 4x the source's own tokens + 32 instead of running on to the cap (1840 tokens for
        # the one-word "zlomky" in the 2026-09-27 AMCR run).
        max_decoding = CT2_MAX_DECODING_TOKENS.get()
        source_tokens = len(self._tokenizer(text, add_special_tokens=False)["input_ids"])
        budget = min(max_decoding, _RUNAWAY_FACTOR * source_tokens + _RUNAWAY_SLACK)
        result = self._engine.generate_batch(
            [tokens],
            max_length=budget,
            sampling_temperature=0.0,
            include_prompt_in_result=False,
            end_token=self._end_tokens(),
        )

        generated = list(result[0].sequences[0])
        out_tokens = _until_turn_end(generated)
        if len(out_tokens) == len(generated) and len(generated) >= budget:
            if budget == max_decoding:
                raise DegenerateTranslationError(
                    f"CTranslate2 reply reached CT2_MAX_DECODING_TOKENS={max_decoding}; it is cut, not complete."
                )
            raise DegenerateTranslationError(
                f"CT2 output rejected — runaway length: still going at {budget} tokens for a "
                f"{source_tokens}-token source."
            )
        out_ids = self._tokenizer.convert_tokens_to_ids(out_tokens)
        # clean_up_tokenization_spaces is a WordPiece post-process; on EuroLLM's BPE
        # vocabulary it deletes the space before punctuation that the model wrote.
        decoded = self._tokenizer.decode(
            out_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        ).strip()
        return _strip_prompt_echo(text, decoded, _language_name(src_lang), _language_name(tgt_lang))

    def _end_tokens(self):
        """The tokens that end the assistant turn: the tokenizer's EOS and the chat markers it knows.

        A checkpoint whose tokenizer names ``</s>`` as EOS but closes a chat turn with
        ``<|im_end|>`` would otherwise go on writing the next turn into the translation.
        """
        eos = getattr(self._tokenizer, "eos_token", None)
        ends = [eos] if eos else []
        try:
            vocab = self._tokenizer.get_vocab()
        except (AttributeError, NotImplementedError, TypeError):
            vocab = {}
        ends += [t for t in _TURN_END_TOKENS if t in vocab and t not in ends]
        if not ends:
            return None  # CTranslate2 then stops on the model's own EOS
        return ends[0] if len(ends) == 1 else ends

    def _glossary_lines(self, text: str) -> list:
        if not self.vocabulary:
            return []
        # Shared word-boundary helper prevents short keys matching inside longer
        # unrelated words (mirrors the LLM backend fix, L1).
        pairs = get_matching_terms(text, self.vocabulary)
        pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
        cap = CT2_MAX_GLOSSARY_TERMS.get()
        if len(pairs) > cap:
            note(
                CT2_MAX_GLOSSARY_TERMS,
                "trimmed",
                1,
                f"{len(pairs) - cap} matched vocabulary term(s) left out of a prompt; the {cap} longest were kept",
            )
        return [f"{s} = {t}" for s, t in pairs[:cap]]

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
        if _copies_source(source, translated):
            raise DegenerateTranslationError("CT2 output repeats the source untranslated.")
