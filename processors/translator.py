"""
processors/translator.py – LINDAT Translation API client with vocabulary support.

Implements the **Tag-and-Protect** strategy for vocabulary term overriding.

KNOWN LIMITATIONS:
  - Single-word term replacement uses a regex search (`re.sub` with `count=1`)
    after extracting lemmas from UDPipe. If a sentence contains homonyms
    (the same surface word appearing multiple times but with different lemmas),
    the regex blindly replaces the *first* textual occurrence of that surface
    word. This may result in misaligned tags in rare edge cases.
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

    _TAG_RE = re.compile(r"__TERM_\d+__")

    def __init__(self, vocab_path=None):
        self.supported_models = self._fetch_models()
        self.vocabulary: dict = {}
        self._multiword_terms: list = []
        self._lemmatizer = None

        if vocab_path:
            self.vocabulary = self._load_vocabulary(Path(vocab_path))
            if self.vocabulary:
                self._multiword_terms = sorted(
                    [(k, v) for k, v in self.vocabulary.items() if " " in k],
                    key=lambda kv: len(kv[0]),
                    reverse=True,
                )
                self._lemmatizer = LindatLemmatizer()
                print(f"[INFO] Vocabulary loaded. Tag-and-Protect enabled.")

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        if not text or not text.strip() or src_lang == tgt_lang:
            return text

        if self.vocabulary and self._lemmatizer:
            return self._translate_with_vocabulary(text, src_lang, tgt_lang)

        return self._basic_translate(text, src_lang, tgt_lang)

    def _load_vocabulary(self, path: Path) -> dict:
        vocab: dict = {}
        try:
            with open(path, mode="r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                for i, row in enumerate(reader):
                    if len(row) < 2:
                        continue
                    src, tgt = row[0].strip(), row[1].strip()
                    if i == 0 and src.lower() in ("source_lemma", "source", "src", "term", "lemma", "cs"):
                        continue
                    if src:
                        vocab[src.lower()] = tgt
        except Exception as e:
            print(f"[WARN] Could not load vocabulary from '{path}': {e}")
        return vocab

    def _translate_with_vocabulary(self, text: str, src_lang: str, tgt_lang: str) -> str:
        protected_text = text
        protected_map: dict = {}

        # Pass 1: multi-word phrases
        for phrase, translation in self._multiword_terms:
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            if pattern.search(protected_text):
                tag = f"__TERM_{len(protected_map)}__"
                protected_map[tag] = translation
                protected_text = pattern.sub(tag, protected_text, count=1)

        # Pass 2: single-word lemma matching
        word_lemma_pairs = self._lemmatizer.get_lemmas(protected_text, lang=src_lang)

        if word_lemma_pairs:
            for word, lemma in word_lemma_pairs:
                if self._TAG_RE.search(word):
                    continue
                lemma_key = lemma.lower()
                if lemma_key not in self.vocabulary:
                    continue

                # NOTE: Risk of homonym misalignment here (documented in module string)
                if re.search(rf"\b{re.escape(word)}\b", protected_text):
                    tag = f"__TERM_{len(protected_map)}__"
                    protected_map[tag] = self.vocabulary[lemma_key]
                    protected_text = re.sub(
                        rf"\b{re.escape(word)}\b",
                        tag,
                        protected_text,
                        count=1,
                    )

        if not protected_map:
            return self._basic_translate(text, src_lang, tgt_lang)

        # Pass 3: translate
        translated = self._basic_translate(protected_text, src_lang, tgt_lang)

        # Pass 4: restore
        translated = self._restore_tags(translated, protected_map)
        return translated

    @staticmethod
    def _restore_tags(translated: str, protected_map: dict) -> str:
        result = translated
        for tag, replacement in protected_map.items():
            if tag in result:
                result = result.replace(tag, replacement)
            else:
                fuzzy_pattern = re.sub(r"_", r"_\\s*", re.escape(tag))
                result = re.sub(fuzzy_pattern, replacement, result)
        return result

    def _basic_translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        model_name = f"{src_lang}-{tgt_lang}"

        if self.supported_models and model_name not in self.supported_models:
            model_name, src_lang, tgt_lang = "cs-en", "cs", "en"

        chunks = self._chunk_text(text)
        translated_chunks = []
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
                    translated_chunks.append(f"[Translation Failed: HTTP {response.status_code}]")
            except requests.exceptions.RequestException as e:
                translated_chunks.append(f"[Network Error: {e}]")

        return "\n".join(translated_chunks)

    def _fetch_models(self) -> list:
        try:
            resp = requests.get(f"{self.BASE_URL}/models", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "_embedded" in data:
                return [item["model"] for item in data["_embedded"].get("item", [])]
            if isinstance(data, list): return data
            return []
        except Exception:
            return ["fr-en", "cs-en", "de-en", "uk-en", "ru-en", "pl-en"]

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 4000) -> list:
        chunks = []
        while len(text) > chunk_size:
            split_idx = text.rfind(" ", 0, chunk_size)
            if split_idx == -1: split_idx = chunk_size
            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
        if text: chunks.append(text)
        return chunks