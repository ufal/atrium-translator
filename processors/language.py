"""
processors/language.py – Source-language resolution policy (dependency-free).

The pipeline never uses a raw language-ID guess. It asks
:func:`resolve_source_language`, which accepts a detection only when it is
trustworthy and otherwise falls back, in this order:

1. **detected** — the text has at least ``LANG_ID_MIN_LETTERS`` letters, and one of
   the identifier's top candidates scores at least ``LANG_ID_MIN_CONFIDENCE`` *and*
   is a language the translation backend can actually translate;
2. **hint** — the element's own label (ALTO ``LANG``/``language``, metadata
   ``xml:lang``), if the backend can translate that language;
3. **context** — the language of the whole document, resolved once by the same rule;
4. **default** — the default source language (``--default-source-lang`` → config
   ``default_source_lang`` → ``DEFAULT_SOURCE_LANG`` → ``cs``).

Why (issue #46 follow-up): a real ``--source_lang auto`` run over the Czech ALTO
sample had FastText answer ``krc``, ``yue``, ``bod``, ``epo`` and ``swh`` for short
OCR blocks. Unknown codes used to pass straight through as the block's source
language, the ALTO path ignored the confidence score, UDPipe had no model for them,
and append mode would have written them into the output as ``LANG``. Worse, when
detection could not run at all the answer was ``"en"`` — the TARGET language — so
the block was returned untranslated. Nothing here can produce a language the backend
cannot translate, and nothing produces ``en`` merely because detection failed.

Kept free of FastText / Hugging Face imports so ``utils.py`` can use it without
loading the model stack; :mod:`processors.identifier` is the FastText side.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple


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


#: Characters dropped before detection: everything that is not a letter. Digits and
#: punctuation dominate OCR noise ("parc. č. 41/1, 41/5") and carry no language signal.
_NOT_LETTER_RE = re.compile(r"[\W\d_]+", re.UNICODE)

#: Candidates requested from the identifier per text.
DEFAULT_TOP_K = 5

#: How much of a document is used to resolve its overall language.
DOCUMENT_SAMPLE_CHARS = 20000

# ISO 639-3 (FastText / NLLB LID labels) → ISO 639-1 (what translation backends and
# UDPipe are keyed on). Covers the European languages an archival corpus from the
# region can plausibly contain, including NLLB's own individual-language codes
# (`lvs` Latvian, `ekk` Estonian, `als` Albanian). Anything not listed stays an
# ISO 639-3 code, which no backend lists — so it is rejected rather than guessed.
ISO3_TO_ISO1 = {
    "ces": "cs",
    "slk": "sk",
    "eng": "en",
    "fra": "fr",
    "deu": "de",
    "rus": "ru",
    "pol": "pl",
    "ukr": "uk",
    "bel": "be",
    "bul": "bg",
    "hrv": "hr",
    "srp": "sr",
    "bos": "bs",
    "slv": "sl",
    "mkd": "mk",
    "lav": "lv",
    "lvs": "lv",
    "lit": "lt",
    "est": "et",
    "ekk": "et",
    "fin": "fi",
    "swe": "sv",
    "dan": "da",
    "nob": "no",
    "nno": "nn",
    "isl": "is",
    "nld": "nl",
    "ltz": "lb",
    "hun": "hu",
    "ron": "ro",
    "ita": "it",
    "spa": "es",
    "cat": "ca",
    "glg": "gl",
    "por": "pt",
    "eus": "eu",
    "gle": "ga",
    "cym": "cy",
    "mlt": "mt",
    "als": "sq",
    "sqi": "sq",
    "ell": "el",
    "tur": "tr",
    "lat": "la",
    "epo": "eo",
    "hin": "hi",
}


# ──────────────────────────────────────────────────────────────────────────────
# Resolution policy
# ──────────────────────────────────────────────────────────────────────────────


def normalise_for_detection(text) -> str:
    """Lowercased letters and single spaces only — what FastText is given."""
    if not text:
        return ""
    return " ".join(_NOT_LETTER_RE.sub(" ", str(text).lower()).split())


def letter_count(text) -> int:
    """Number of letters in *text* (the "is this long enough to judge" measure)."""
    return sum(1 for ch in str(text or "") if ch.isalpha())


def normalise_lang_code(code) -> str | None:
    """``"en-US"`` → ``"en"``, ``"CES"`` → ``"cs"``, ``"cs"`` → ``"cs"``; empty → ``None``."""
    if not code:
        return None
    base = re.split(r"[-_]", str(code).strip().lower())[0]
    if not base:
        return None
    return ISO3_TO_ISO1.get(base, base)


#: The language a document falls back to when nothing better is known.
FALLBACK_SOURCE_LANG = "cs"


@dataclass(frozen=True)
class SourceLanguagePolicy:
    """What counts as a trustworthy detection, and what to use when there is none."""

    #: Used when neither detection, the element's label nor the document says otherwise.
    default: str = FALLBACK_SOURCE_LANG
    #: A FastText candidate below this score is never used.
    min_confidence: float = 0.5
    #: Texts with fewer letters are not sent to FastText at all.
    min_letters: int = 20
    #: Languages a detection or label may resolve to; ``None`` = no restriction.
    #: The default is always accepted.
    allowed: frozenset | None = None

    def accepts(self, lang) -> bool:
        if not lang:
            return False
        if lang == self.default:
            return True
        return self.allowed is None or lang in self.allowed

    @classmethod
    def from_env(cls, *, default=None, allowed=None) -> "SourceLanguagePolicy":
        """Build the policy from the environment.

        *default* (already resolved from CLI / config by the caller) wins over
        ``DEFAULT_SOURCE_LANG``. *allowed* is the backend's translatable set (see
        :func:`allowed_source_languages`); ``LANG_ID_LANGUAGES`` narrows it further.
        """
        resolved_default = normalise_lang_code(default or os.environ.get("DEFAULT_SOURCE_LANG", "cs"))
        restrict = {
            code
            for code in (normalise_lang_code(part) for part in os.environ.get("LANG_ID_LANGUAGES", "").split(","))
            if code
        }
        if restrict:
            allowed = (set(allowed) & restrict) if allowed is not None else restrict
        return cls(
            default=resolved_default or FALLBACK_SOURCE_LANG,
            min_confidence=_env_float("LANG_ID_MIN_CONFIDENCE", 0.5),
            min_letters=_env_int("LANG_ID_MIN_LETTERS", 20),
            allowed=frozenset(allowed) if allowed is not None else None,
        )

    def describe(self) -> dict:
        """JSON-friendly snapshot for paradata."""
        return {
            "default_source_lang": self.default,
            "fasttext_confidence_threshold": self.min_confidence,
            "lang_id_min_letters": self.min_letters,
            "lang_id_languages": sorted(self.allowed) if self.allowed is not None else "any",
        }


def allowed_source_languages(translator, tgt_lang) -> frozenset | None:
    """Source languages *translator* can translate into *tgt_lang*, plus *tgt_lang* itself.

    LINDAT exposes model pairs (``supported_models = ["cs-en", "de-en", …]``), so
    the set is the source side of every pair that ends in the target — ``uk-cs``
    does not make Ukrainian a source for an English run. Other backends expose
    ``supported_languages()``. ``None`` (no restriction) when the backend says
    nothing. The target language is included because a block that is already in
    the target language is correctly left as it is.
    """
    langs: set[str] = set()
    models = getattr(translator, "supported_models", None)
    if isinstance(models, (list, tuple, set, frozenset)):
        for pair in models:
            parts = str(pair).split("-")
            if len(parts) == 2 and parts[1].strip() == tgt_lang and parts[0].strip():
                langs.add(parts[0].strip())
    if not langs:
        getter = getattr(translator, "supported_languages", None)
        if callable(getter):
            try:
                listed = getter()
            except Exception:
                listed = None
            if isinstance(listed, (list, tuple, set, frozenset)):
                langs = {code for code in (normalise_lang_code(c) for c in listed) if code}
    if not langs:
        return None
    if tgt_lang:
        langs.add(tgt_lang)
    return frozenset(langs)


class Resolution(NamedTuple):
    #: The language the pipeline will translate from.
    lang: str
    #: ``detected`` | ``hint`` | ``context`` | ``default``
    basis: str
    #: FastText's top guess ``(lang, score)``, or ``None`` if detection did not run.
    raw: tuple | None


def _identifier_candidates(identifier, text, max_chars):
    """Candidates from *identifier*, tolerating identifiers that only offer ``detect()``."""
    getter = getattr(identifier, "candidates", None)
    if callable(getter):
        found = getter(text, k=DEFAULT_TOP_K, max_chars=max_chars)
        if isinstance(found, (list, tuple)):
            return [(normalise_lang_code(lang), float(score)) for lang, score in found if lang]
    detect = getattr(identifier, "detect", None)
    if callable(detect):
        found = detect(text)
        if isinstance(found, (list, tuple)) and len(found) == 2 and found[0]:
            return [(normalise_lang_code(found[0]), float(found[1]))]
    return []


def resolve_source_language(identifier, text, policy, *, hint=None, context=None, max_chars=2000) -> Resolution:
    """The language to translate *text* from — see the module docstring for the order."""
    raw = None
    if identifier is not None and letter_count(text) >= policy.min_letters:
        candidates = _identifier_candidates(identifier, text, max_chars)
        if candidates:
            raw = candidates[0]
        for lang, score in candidates:
            if score < policy.min_confidence:
                break
            if policy.accepts(lang):
                return Resolution(lang, "detected", raw)

    hint_code = normalise_lang_code(hint)
    if hint_code and policy.accepts(hint_code):
        return Resolution(hint_code, "hint", raw)
    if context:
        return Resolution(context, "context", raw)
    return Resolution(policy.default, "default", raw)


class LanguageTally:
    """Per-document record of how each block / field got its language."""

    def __init__(self):
        self.resolved: Counter = Counter()
        self.overridden: Counter = Counter()

    def add(self, resolution: Resolution) -> None:
        self.resolved[(resolution.lang, resolution.basis)] += 1
        if resolution.raw and resolution.raw[0] != resolution.lang:
            self.overridden[resolution.raw[0]] += 1

    def summary(self) -> str:
        per_lang: dict[str, Counter] = {}
        for (lang, basis), count in self.resolved.items():
            per_lang.setdefault(lang, Counter())[basis] += count
        parts = []
        for lang, bases in sorted(per_lang.items(), key=lambda item: -sum(item[1].values())):
            detail = ", ".join(f"{basis} {count}" for basis, count in bases.most_common())
            parts.append(f"{lang} {sum(bases.values())} ({detail})")
        text = "; ".join(parts) if parts else "nothing to resolve"
        if self.overridden:
            guesses = ", ".join(f"{lang}×{count}" for lang, count in self.overridden.most_common())
            text += f"; FastText guesses not used: {guesses}"
        return text
