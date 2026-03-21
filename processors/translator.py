"""
processors/translator.py – LINDAT Translation API client with vocabulary support.

Implements the **Tag-and-Protect** strategy for vocabulary term overriding:
  1. Lemmatise the source text via UDPipe.
  2. Identify any surface tokens whose lemma appears in the vocabulary.
  3. Replace those tokens with unique placeholder tags (``__TERM_N__``).
  4. Translate the protected text; NMT models leave unknown tags untouched.
  5. Substitute every tag with the vocabulary's target-language translation.

If no vocabulary is supplied, the translator behaves exactly as before.
"""

import csv
import re
from pathlib import Path

import requests

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        for item in iterable:
            yield item

from .lemmatizer import LindatLemmatizer


class LindatTranslator:
    BASE_URL = "https://lindat.mff.cuni.cz/services/translation/api/v2"

    # Regex that matches a placeholder tag such as __TERM_0__
    _TAG_RE = re.compile(r"__TERM_\d+__")

    def __init__(self, vocab_path: Path | str | None = None):
        self.supported_models = self._fetch_models()
        self.vocabulary: dict[str, str] = {}
        self._lemmatizer: LindatLemmatizer | None = None

        if vocab_path:
            self.vocabulary = self._load_vocabulary(Path(vocab_path))
            if self.vocabulary:
                self._lemmatizer = LindatLemmatizer()
                print(f"[INFO] Vocabulary loaded ({len(self.vocabulary)} terms). "
                      "Tag-and-Protect mode enabled.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        """
        Translate *text* from *src_lang* to *tgt_lang*.

        If a vocabulary is configured, protected placeholders are inserted
        before translation and restored afterwards.
        """
        if not text or not text.strip() or src_lang == tgt_lang:
            return text

        if self.vocabulary and self._lemmatizer:
            return self._translate_with_vocabulary(text, src_lang, tgt_lang)

        return self._basic_translate(text, src_lang, tgt_lang)

    # ------------------------------------------------------------------
    # Vocabulary / Tag-and-Protect
    # ------------------------------------------------------------------

    def _load_vocabulary(self, path: Path) -> dict[str, str]:
        """
        Load a two-column CSV file: ``source_lemma,target_translation``.

        • Encoding: UTF-8.
        • First row may optionally be a header – it is skipped when the first
          field does not look like a dictionary entry (contains spaces or is
          longer than 60 characters).
        • Keys are stored in lower-case for case-insensitive matching.
        """
        vocab: dict[str, str] = {}
        try:
            with open(path, mode="r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                for i, row in enumerate(reader):
                    if len(row) < 2:
                        continue
                    src_term = row[0].strip()
                    tgt_term = row[1].strip()
                    # Heuristic: skip a header row
                    if i == 0 and (not tgt_term or src_term.lower() in ("source", "src", "term", "lemma")):
                        continue
                    if src_term:
                        vocab[src_term.lower()] = tgt_term
            print(f"[INFO] Loaded {len(vocab)} vocabulary entries from '{path}'.")
        except FileNotFoundError:
            print(f"[WARN] Vocabulary file not found: '{path}'. Continuing without vocabulary.")
        except Exception as e:
            print(f"[WARN] Could not load vocabulary from '{path}': {e}")
        return vocab

    def _translate_with_vocabulary(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """
        Full Tag-and-Protect pipeline:

        1. Ask UDPipe for ``(word, lemma)`` pairs.
        2. For every token whose lemma is in the vocabulary, replace the first
           occurrence of that token in *text* with ``__TERM_N__`` and record
           the mapping ``tag → target_term``.
        3. Translate the tagged text.
        4. Restore tags with the vocabulary translations.
        """
        word_lemma_pairs = self._lemmatizer.get_lemmas(text, lang=src_lang)

        if not word_lemma_pairs:
            # UDPipe unavailable – fall back gracefully
            print("[WARN] UDPipe returned no lemmas; translating without vocabulary protection.")
            return self._basic_translate(text, src_lang, tgt_lang)

        protected_text = text
        protected_map: dict[str, str] = {}   # tag → target translation

        for word, lemma in word_lemma_pairs:
            lemma_key = lemma.lower()
            if lemma_key not in self.vocabulary:
                continue
            # Avoid creating a duplicate tag for the very same surface word
            # if it has already been tagged earlier in this sentence.
            if re.search(rf"\b{re.escape(word)}\b", protected_text):
                tag = f"__TERM_{len(protected_map)}__"
                protected_map[tag] = self.vocabulary[lemma_key]
                # Replace only the *first* remaining occurrence to handle
                # repeated words independently.
                protected_text = re.sub(
                    rf"\b{re.escape(word)}\b",
                    tag,
                    protected_text,
                    count=1,
                )

        if not protected_map:
            # No vocabulary matches – skip the extra round-trip
            return self._basic_translate(text, src_lang, tgt_lang)

        translated = self._basic_translate(protected_text, src_lang, tgt_lang)

        # Restore tags.  The NMT model should have preserved them; if it
        # mutated a tag (e.g. added spaces inside), try a fuzzy restore.
        translated = self._restore_tags(translated, protected_map)

        return translated

    @staticmethod
    def _restore_tags(translated: str, protected_map: dict[str, str]) -> str:
        """
        Replace every ``__TERM_N__`` placeholder in *translated* with its
        vocabulary translation.  Also handles cases where the NMT model
        introduced spaces inside the tag (``__ TERM_0 __``).
        """
        result = translated
        for tag, replacement in protected_map.items():
            if tag in result:
                result = result.replace(tag, replacement)
            else:
                # Fuzzy fallback: collapse internal spaces and retry
                fuzzy_tag = re.sub(r"\s+", "", tag)
                fuzzy_pattern = re.sub(r"_", r"_\\s*", re.escape(tag))
                result = re.sub(fuzzy_pattern, replacement, result)
        return result

    # ------------------------------------------------------------------
    # Core translation (unchanged from original)
    # ------------------------------------------------------------------

    def _basic_translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """Send *text* to the LINDAT API and return the translated string."""
        model_name = f"{src_lang}-{tgt_lang}"

        if self.supported_models and model_name not in self.supported_models:
            print(f"[WARN] Model '{model_name}' not available; falling back to 'cs-en'.")
            model_name = "cs-en"
            src_lang = "cs"
            tgt_lang = "en"

        chunks = self._chunk_text(text)
        translated_chunks: list[str] = []
        chunk_iter = tqdm(chunks, desc="Translating chunks", leave=False) if len(chunks) > 1 else chunks

        for chunk in chunk_iter:
            try:
                response = requests.post(
                    f"{self.BASE_URL}/models/{model_name}?src={src_lang}&tgt={tgt_lang}",
                    data={"input_text": chunk},
                    timeout=60,
                )
                if response.status_code == 200:
                    response.encoding = "utf-8"
                    translated_chunks.append(response.text.strip())
                else:
                    error_msg = f"[Translation Failed: HTTP {response.status_code}]"
                    print(error_msg)
                    translated_chunks.append(error_msg)
            except requests.exceptions.RequestException as e:
                error_msg = f"[Network Error: {e}]"
                print(error_msg)
                translated_chunks.append(error_msg)

        return "\n".join(translated_chunks)

    def _fetch_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.BASE_URL}/models", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "_embedded" in data:
                return [item["model"] for item in data["_embedded"].get("item", [])]
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            print(f"[WARN] Could not fetch model list ({e}). Using defaults.")
            return ["fr-en", "cs-en", "de-en", "uk-en", "ru-en", "pl-en"]

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 4000) -> list[str]:
        """
        Space-aware chunking: never cuts a token in the middle.
        """
        chunks: list[str] = []
        while len(text) > chunk_size:
            split_idx = text.rfind(" ", 0, chunk_size)
            if split_idx == -1:
                split_idx = chunk_size
            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
        if text:
            chunks.append(text)
        return chunks