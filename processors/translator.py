"""
processors/translator.py – LINDAT Translation API client with vocabulary support.

Implements the **Tag-and-Protect** strategy for vocabulary term overriding.

Chunking strategy (v0.5+)
--------------------------
Text longer than ``chunk_size`` characters is split at the highest-priority
boundary found within the window, with the priority order **actually enforced**
(see ``processors/chunking.chunk_text``):

  1. Newline  – paragraph / OCR-line boundary; best context preservation.
  2. Sentence-terminal punctuation + space (``'. '``, ``'! '``, ``'? '``).
  3. Clause-level punctuation + space (``'; '``, ``', '``).
  4. Any space – word boundary; same as the previous behaviour.
  5. Hard cut at ``chunk_size`` – last resort for oversized single tokens.

The chunker is shared with ``processors/lemmatizer.py`` so the two sites cannot
drift apart.

Network failure handling (fix for review finding #1)
----------------------------------------------------
Earlier revisions appended ``"[Network Error: …]"`` / ``"[Translation Failed:
HTTP …]"`` strings *into* the translated text on failure.  Those strings were
then written into the ALTO ``String`` ``CONTENT`` attributes and the QA CSV, and
because no exception propagated, ``main.py`` still counted the file as
``successfully_processed`` — silent corruption of archival output.

This module now:
  * retries transient failures (network errors, HTTP 429/5xx) with bounded
    exponential back-off, and
  * **raises** ``TranslationError`` when a chunk cannot be translated, so the
    per-file handler in ``main.py`` logs a skip and the broken file is never
    written.

Rate limiting (fix for review finding #4)
-----------------------------------------
An optional minimum inter-request interval throttles the shared public LINDAT
endpoint.  It is configured via the ``LINDAT_MIN_INTERVAL_S`` environment
variable (default ``0`` = disabled).  Setting e.g. ``LINDAT_MIN_INTERVAL_S=0.1``
keeps the dual-pass ALTO mode (1 + N calls per block) from flooding the API.

Placeholder design (v0.5.1+)
----------------------------
Protected terms use an **all-alphabetic sentinel** with no internal punctuation::

    __TERM_3__   →   Xtermzzz3z

NMT models pass such letter-only tokens through untouched.  Restoration matches
the sentinel tolerantly (case-insensitive, optional stray spaces) and, as a
final safety net, ``_scrub_placeholder_fragments`` removes any residual sentinel
debris so a malformed placeholder can never reach the output or the logs.

Pluggable backend (issue #4)
----------------------------
``LindatTranslator`` is the default implementation of the
``processors.backend.TranslationBackend`` Protocol.  It exposes ``name``,
``supports_glossary`` (``False`` — CUBBITT has no glossary API, so terminology
is handled by the in-process Tag-and-Protect pipeline below) and
``supported_languages()`` so the pipeline can select a backend and check
language coverage at runtime.  See ``docs/translation-backends.md``.

KNOWN LIMITATIONS:
  - Single-word term replacement uses a regex search (re.sub with count=1)
    after extracting lemmas from UDPipe.  If a sentence contains homonyms
    (the same surface word appearing multiple times but with different lemmas),
    the regex blindly replaces the *first* textual occurrence of that surface
    word.  This may result in misaligned tags in rare edge cases.
"""

import os
import re
import time
from pathlib import Path

import requests

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable, *args, **kwargs):
        for item in iterable:
            yield item


from .chunking import chunk_text
from .http_retry import request_with_retry
from .lemmatizer import LindatLemmatizer
from .vocab import load_vocabulary

# ── failure / retry / throttle configuration ──────────────────────────────────


class TranslationError(RuntimeError):
    """Raised when a chunk cannot be translated after all retries.

    Propagates up through ``translate`` → ``process_*_xml`` → ``main`` so the
    offending file is logged as a skip instead of being written with corrupt
    content.
    """


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


# Minimum seconds between outbound LINDAT requests (0 = disabled).
_MIN_INTERVAL_S = _env_float("LINDAT_MIN_INTERVAL_S", 0.0)
# Retries on transient failure (network error, HTTP 429/5xx).
_MAX_RETRIES = _env_int("LINDAT_MAX_RETRIES", 4)
# Base for exponential back-off (seconds): sleep = base * 2**attempt + jitter.
_BACKOFF_BASE_S = _env_float("LINDAT_BACKOFF_BASE_S", 1.0)
# HTTP status codes worth retrying.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LindatTranslator:
    BASE_URL = "https://lindat.mff.cuni.cz/services/translation/api/v2"

    # ── NMT-safe placeholder sentinel ─────────────────────────────────────────
    # A purely alphabetic marker (no '_' / digits-as-punctuation) that NMT models
    # leave untouched.  The numeric index is rendered between letter guards
    # ("z<index>z") so a bare integer never sits at a token edge.
    _TAG_PREFIX = "Xtermzzz"
    _TAG_SUFFIX = "z"

    # Exact form, e.g. "Xtermzzz0z"
    @classmethod
    def _make_tag(cls, index: int) -> str:
        return f"{cls._TAG_PREFIX}{index}{cls._TAG_SUFFIX}"

    # Matches a clean sentinel; used to skip already-protected tokens.
    _TAG_RE = re.compile(rf"{_TAG_PREFIX}\d+{_TAG_SUFFIX}", re.IGNORECASE)

    # Tolerant matcher for restoration: allows stray spaces the NMT model may
    # inject between the letters/digits of the sentinel.
    @classmethod
    def _tag_fuzzy_re(cls, index: int) -> re.Pattern:
        body = r"\s*".join(list(f"{cls._TAG_PREFIX}{index}{cls._TAG_SUFFIX}"))
        return re.compile(body, re.IGNORECASE)

    # Catches any *leftover* sentinel debris (a fragment that could not be
    # mapped back), so it can be scrubbed from the final output and the CSV.
    # NOTE (finding #14): keyed off the FULL sentinel stem ("Xtermzzz"), not the
    # broad 5-char "Xterm" prefix, so legitimate source text that merely begins
    # with "Xterm" can never be eaten.
    _TAG_FRAGMENT_RE = re.compile(rf"{_TAG_PREFIX}[A-Za-z0-9]*z*\d*z*", re.IGNORECASE)
    # Orphaned guard-letter clusters (e.g. a stray "zzz") that may remain after
    # the main fragment is removed.
    _GUARD_DEBRIS_RE = re.compile(r"\bz{2,}\b", re.IGNORECASE)

    # ── pluggable-backend conformance (see processors/backend.py) ──────────────
    # Satisfy the TranslationBackend Protocol. That Protocol is
    # @runtime_checkable and declares ``name`` / ``supports_glossary`` as data
    # members, so isinstance(translator, TranslationBackend) also requires these
    # attributes to exist — they are class-level so every instance has them.
    name: str = "lindat"
    # CUBBITT has no native glossary API; terminology is handled by the
    # in-process Tag-and-Protect pipeline, so the pipeline must NOT delegate
    # glossary handling to this backend.
    supports_glossary: bool = False

    def supported_languages(self) -> list[str]:
        """Language codes derivable from the LINDAT model pairs (e.g. 'cs-en').

        Flattens ``supported_models`` (``['cs-en', 'fr-en', ...]``) into a sorted
        set of ISO codes. Empty when the model list is unknown.
        """
        langs: set[str] = set()
        for pair in self.supported_models or []:
            for code in str(pair).split("-"):
                code = code.strip()
                if code:
                    langs.add(code)
        return sorted(langs)

    def license_components(self, vocab_loaded: bool = False) -> list[str]:
        """Paradata component names this backend contributes (see para_config.txt).

        CUBBITT maps to ``lindat_cubbitt``.  When a vocabulary is loaded the
        Tag-and-Protect path also exercises UDPipe and the AMCR/TEATER vocab
        data, so those components are recorded too.  ``main.py`` calls this so
        the effective output license reflects the backend actually used rather
        than a hard-coded LINDAT assumption (issue #4 — keeps paradata correct
        once the backend can be swapped).
        """
        comps = ["lindat_cubbitt"]
        if vocab_loaded:
            comps += ["udpipe2_engine", "udpipe2_models", "amcr_vocab", "teater_data"]
        return comps

    def __init__(self, vocab_path=None):
        self.supported_models = self._fetch_models()
        self.vocabulary: dict = {}
        self._multiword_terms: list = []
        self._lemmatizer = None

        # Running tally of vocabulary terms protected via Tag-and-Protect.
        # main.py resets this per input file (reset_protected_count) and reads
        # it afterwards (protected_count) to record per-document statistics.
        self._protected_count: int = 0

        # Monotonic timestamp of the last outbound request (for throttling).
        self._last_call_ts: float = 0.0

        if vocab_path:
            self.vocabulary = self._load_vocabulary(Path(vocab_path))
            if self.vocabulary:
                self._multiword_terms = sorted(
                    [(k, v) for k, v in self.vocabulary.items() if " " in k],
                    key=lambda kv: len(kv[0]),
                    reverse=True,
                )
                self._lemmatizer = LindatLemmatizer()
                print("[INFO] Vocabulary loaded. Tag-and-Protect enabled.")

    # ── public translation entry point ────────────────────────────────────────

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        if not text or not text.strip() or src_lang == tgt_lang:
            return text

        if self.vocabulary and self._lemmatizer:
            result = self._translate_with_vocabulary(text, src_lang, tgt_lang)
        else:
            result = self._basic_translate(text, src_lang, tgt_lang)

        # Final safety net: a malformed sentinel must never reach the caller
        # (which writes both the XML element text and the QA CSV log).
        return self._scrub_placeholder_fragments(result)

    # ── vocabulary loading ────────────────────────────────────────────────────

    def _load_vocabulary(self, path: Path) -> dict:
        """Delegate to the shared loader (``processors/vocab.py``).

        Retained as an instance method so the existing
        ``LindatTranslator._load_vocabulary(...)`` call/test API is preserved
        while the parsing rules live in exactly one place (shared with the LLM
        backend's prompt-glossary loader).
        """
        return load_vocabulary(path)

    # ── Tag-and-Protect pipeline ──────────────────────────────────────────────

    def _translate_with_vocabulary(self, text: str, src_lang: str, tgt_lang: str) -> str:
        protected_text = text
        protected_map: dict = {}

        # Pass 1: multi-word phrases (longest-first, case-insensitive)
        for phrase, translation in self._multiword_terms:
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            if pattern.search(protected_text):
                tag = self._make_tag(len(protected_map))
                protected_map[tag] = translation
                protected_text = pattern.sub(tag, protected_text, count=1)

        # Pass 2: single-word lemma matching via UDPipe.
        #
        # Number-agreement guard: vocabulary translations are stored as singular
        # English terms.  Freezing a singular term onto a PLURAL Czech surface
        # form prevents the NMT from pluralising the slot and yields broken
        # English ("several feature", "4 larger trench", "ranks fence").  We
        # therefore protect singular (and number-neutral) occurrences only, and
        # let the NMT translate plural occurrences naturally — which keeps
        # terminology control where it is safe while restoring agreement where
        # it was being broken.
        #
        # ``get_lemmas_with_features`` adds the CoNLL-U Number feature; if an
        # older lemmatizer without it is injected (e.g. a test double), fall back
        # to the plain 2-tuple API and treat every token as number-neutral.
        if hasattr(self._lemmatizer, "get_lemmas_with_features"):
            word_feat_triples = self._lemmatizer.get_lemmas_with_features(protected_text, lang=src_lang)
        else:
            word_feat_triples = [
                (w, lemma, "") for w, lemma in self._lemmatizer.get_lemmas(protected_text, lang=src_lang)
            ]
        if word_feat_triples:
            for word, lemma, number in word_feat_triples:
                if self._TAG_RE.search(word):
                    continue
                lemma_key = lemma.lower()
                if lemma_key not in self.vocabulary:
                    continue

                # Skip plural source tokens: let the NMT inflect the English.
                if number == "Plur":
                    continue

                # NOTE: Risk of homonym misalignment here (documented in module docstring)
                if re.search(rf"\b{re.escape(word)}\b", protected_text):
                    tag = self._make_tag(len(protected_map))
                    protected_map[tag] = self.vocabulary[lemma_key]
                    protected_text = re.sub(
                        rf"\b{re.escape(word)}\b",
                        tag,
                        protected_text,
                        count=1,
                    )

        if not protected_map:
            return self._basic_translate(text, src_lang, tgt_lang)

        # Record how many vocabulary terms were protected in this call so that
        # per-document statistics can be reported (see main.py).
        self._protected_count += len(protected_map)

        # Pass 3: translate the protected text
        translated = self._basic_translate(protected_text, src_lang, tgt_lang)

        # Pass 4: restore vocabulary translations
        translated = self._restore_tags(translated, protected_map)
        return translated

    # ── protected-term statistics ─────────────────────────────────────────────

    def reset_protected_count(self) -> None:
        """Zero the protected-term tally (call before each input document)."""
        self._protected_count = 0

    @property
    def protected_count(self) -> int:
        """Number of vocabulary terms protected since the last reset."""
        return self._protected_count

    @classmethod
    def _restore_tags(cls, translated: str, protected_map: dict) -> str:
        """
        Replace every sentinel placeholder with its vocabulary translation.

        Matching is tolerant: first an exact replacement is attempted, then a
        fuzzy one that absorbs stray spaces the NMT model may have injected
        between the sentinel's characters.  Any sentinel that still cannot be
        mapped is left for ``_scrub_placeholder_fragments`` to remove.
        """
        result = translated
        for tag, replacement in protected_map.items():
            if tag in result:
                result = result.replace(tag, replacement)
                continue

            # Case-insensitive exact match (NMT may change letter case).
            ci = re.compile(re.escape(tag), re.IGNORECASE)
            if ci.search(result):
                result = ci.sub(lambda _m: replacement, result)
                continue

            # Fuzzy match: spaces injected between sentinel characters.
            # Recover the numeric index from the known tag form.
            m = re.search(r"(\d+)", tag)
            if m:
                fuzzy = cls._tag_fuzzy_re(int(m.group(1)))
                if fuzzy.search(result):
                    result = fuzzy.sub(lambda _m: replacement, result)
        return result

    @classmethod
    def _scrub_placeholder_fragments(cls, text: str) -> str:
        """
        Remove any residual placeholder debris from *text*.

        A correctly restored translation contains no sentinels.  If a sentinel
        was mangled badly enough that restoration missed it, the leftover
        fragment must not surface in the translated XML or the QA CSV, so it is
        deleted here and surrounding whitespace is tidied.
        """
        if not text or cls._TAG_PREFIX.lower() not in text.lower():
            return text

        cleaned = cls._TAG_FRAGMENT_RE.sub("", text)
        cleaned = cls._GUARD_DEBRIS_RE.sub("", cleaned)
        # Collapse whitespace left behind by removed fragments and tidy spacing
        # before punctuation.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+([)\].,;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"\(\s+", "(", cleaned)
        return cleaned.strip(" ") if cleaned.strip() else text

    # ── HTTP with throttle + retry/back-off ───────────────────────────────────

    def _throttle(self) -> None:
        """Enforce an optional minimum interval between outbound requests."""
        if _MIN_INTERVAL_S <= 0:
            return
        now = time.monotonic()
        wait = self._last_call_ts + _MIN_INTERVAL_S - now
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _post_with_retry(self, url: str, data: dict) -> str:
        """
        POST *data* to *url*, returning the response text on HTTP 200.

        Delegates the retry/throttle/back-off policy to the shared
        ``processors.http_retry.request_with_retry`` helper (issue #4), so the
        LINDAT client and the LLM backend cannot drift apart.  Raises
        :class:`TranslationError` on a non-retryable status or once retries are
        exhausted, so the caller fails loudly instead of embedding an error
        string in the document.
        """
        response = request_with_retry(
            lambda: requests.post(url, data=data, timeout=60),
            max_retries=_MAX_RETRIES,
            backoff_base_s=_BACKOFF_BASE_S,
            retryable_status=_RETRYABLE_STATUS,
            throttle=self._throttle,
            error_cls=TranslationError,
            label=f"LINDAT translation @ {url}",
        )
        response.encoding = "utf-8"
        return response.text.strip()

    # ── core translation (chunked) ────────────────────────────────────────────

    def _basic_translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        model_name = f"{src_lang}-{tgt_lang}"

        if self.supported_models and model_name not in self.supported_models:
            model_name, src_lang, tgt_lang = "cs-en", "cs", "en"

        chunks = self._chunk_text(text)
        translated_chunks = []
        chunk_iter = tqdm(chunks, desc="Translating chunks", leave=False) if len(chunks) > 1 else chunks

        url = f"{self.BASE_URL}/models/{model_name}?src={src_lang}&tgt={tgt_lang}"
        for chunk in chunk_iter:
            # _post_with_retry raises TranslationError on unrecoverable failure;
            # we deliberately do NOT catch it here so the per-file handler in
            # main.py can skip the file instead of writing corrupt content.
            translated_chunks.append(self._post_with_retry(url, {"input_text": chunk}))

        return "\n".join(translated_chunks)

    def _fetch_models(self) -> list:
        try:
            resp = requests.get(f"{self.BASE_URL}/models", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "_embedded" in data:
                return [item["model"] for item in data["_embedded"].get("item", [])]
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return ["fr-en", "cs-en", "de-en", "uk-en", "ru-en", "pl-en"]

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = None) -> list[str]:
        """
        Split *text* into chunks no longer than *chunk_size* characters.

        Thin wrapper around :func:`processors.chunking.chunk_text` (shared with
        the lemmatizer) so the boundary-priority logic lives in exactly one
        place.  Retained as a static method to preserve the
        ``LindatTranslator._chunk_text(...)`` call/test API.
        """
        if chunk_size is None:
            return chunk_text(text)
        return chunk_text(text, chunk_size)
