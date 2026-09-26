"""
processors/identifier.py – FastText language identification for ``--source_lang auto``.

:class:`LanguageIdentifier` wraps the FastText language-ID model
(``facebook/fasttext-language-identification``, the NLLB LID model: 218 ISO 639-3
labels). ``candidates()`` returns its top guesses as ISO 639-1 codes.

The pipeline does not use those guesses directly: it calls
:func:`processors.language.resolve_source_language`, which accepts a guess only when
it is long enough, confident enough, and a language the backend can translate, and
otherwise falls back to the element's own label, the document's language, or the
default source language. See that module for why.
"""

from __future__ import annotations

import logging

import fasttext
from huggingface_hub import hf_hub_download

from .language import DEFAULT_TOP_K, ISO3_TO_ISO1, normalise_for_detection

# (12-factor XI) Library module: getLogger only, never basicConfig. The root
# logger is configured in service/api.py's __main__ block. (issue #61)
logger = logging.getLogger(__name__)


class LanguageIdentifier:
    # ISO 639-3 (FastText / NLLB LID labels) → ISO 639-1. Unlisted codes pass
    # through as ISO 639-3, which no backend lists, so the resolver rejects them.
    # The table lives in processors/language.py (dependency-free, shared).
    CODE_MAP = ISO3_TO_ISO1

    def __init__(self):
        """
        Initializes the LanguageIdentifier by downloading and loading
        the FastText language identification model from Hugging Face Hub.
        """
        # `load_error` is the declared half of a degraded start. Without it the
        # failure below was swallowed: self.model became None, detect() then
        # answered ("en", 0.0) for EVERY document, and nothing upstream could
        # tell that apart from a genuine English detection. In an egress-
        # restricted cluster hf_hub_download is exactly what fails, so the
        # service came up "healthy" and quietly mislabelled every source
        # language it was given. service/api.py's _deep_health() reads this.
        self.load_error: str | None = None
        self._warned_unavailable = False
        try:
            model_path = hf_hub_download(repo_id="facebook/fasttext-language-identification", filename="model.bin")
            self.model = fasttext.load_model(model_path)
        except Exception as e:
            self.load_error = f"{type(e).__name__}: {e}"
            logger.error(
                "Failed to load the FastText language-identification model (%s). "
                "Language detection is unavailable; with --source_lang auto every block falls back to its "
                "own language label or the default source language until this is fixed.",
                self.load_error,
            )
            self.model = None

    @classmethod
    def to_iso1(cls, label: str) -> str:
        """``__label__ces_Latn`` / ``ces_Latn`` / ``ces`` → ``cs``; unmapped codes pass through."""
        iso3 = label.replace("__label__", "").split("_")[0]
        return cls.CODE_MAP.get(iso3, iso3)

    def _warn_unavailable_once(self, default: str) -> None:
        # Once per process, not once per line: this path fires for every chunk
        # of every document, and a log line repeated thousands of times buries
        # the one at startup that says why.
        if not self._warned_unavailable:
            self._warned_unavailable = True
            logger.warning(
                "Language identification model is not loaded (%s); defaulting to %r.",
                self.load_error or "reason unrecorded",
                default,
            )

    def candidates(self, text, k: int = DEFAULT_TOP_K, max_chars: int = 2000) -> list[tuple[str, float]]:
        """FastText's top-*k* guesses for *text* as ``[(iso1_code, score), …]``, best first.

        The text is reduced to letters and single spaces first — digits and
        punctuation are most of an OCR fragment's characters and none of its
        language. Returns ``[]`` (never a made-up language) when the model is not
        loaded, the text has no letters, or prediction fails; the resolver then
        falls back to hint / context / default.
        """
        if not self.model:
            self._warn_unavailable_once("the block's label, the document language or the default")
            return []
        clean = normalise_for_detection(text)[:max_chars]
        if not clean:
            return []
        try:
            labels, scores = self.model.predict(clean, k=k)
        except Exception as e:
            logger.warning("Language detection prediction failed: %s: %s", type(e).__name__, e)
            return []
        return [(self.to_iso1(label), float(score)) for label, score in zip(labels, scores)]

    def detect(self, text):
        """
        Raw single best guess: ``(language_code, confidence_score)``.

        Kept for callers outside the pipeline. It is NOT what the pipeline uses:
        it returns any of FastText's 218 labels (unmapped ones as ISO 639-3) and
        answers ``("en", 0.0)`` when it cannot detect — use
        :func:`resolve_source_language` for a language that is safe to translate from.
        """
        if not self.model:
            self._warn_unavailable_once("en")
            return "en", 0.0

        if not text or not text.strip():
            return "en", 0.0

        # Lowercase for better detection
        clean_text = text.replace("\n", " ").lower()[:2000]

        try:
            labels, scores = self.model.predict(clean_text)

            raw_label = labels[0].replace("__label__", "")
            iso3_code = raw_label.split("_")[0]
            lang_code = self.CODE_MAP.get(iso3_code, iso3_code)

            score = scores[0]
            return lang_code, score
        except Exception as e:
            logger.warning("Language detection prediction failed: %s: %s", type(e).__name__, e)
            return "en", 0.0
