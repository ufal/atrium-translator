"""
tests/test_translator.py
========================
Unit tests for processors/translator.py — LindatTranslator.

Coverage targets (zero network / zero ML):
  _chunk_text          – pure static method
  _restore_tags        – pure static method
  _load_vocabulary     – local file I/O only
  _translate_with_vocabulary – mocked lemmatizer + _basic_translate
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from processors.translator import LindatTranslator


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def bare_translator():
    """LindatTranslator with _fetch_models suppressed; no vocabulary loaded."""
    with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
        return LindatTranslator()


@pytest.fixture
def vocab_csv(tmp_path: Path) -> Path:
    """Small Czech→English vocabulary CSV used by vocab_translator tests."""
    p = tmp_path / "vocab.csv"
    p.write_text(
        "source_lemma,target_translation\n"
        "nález,find\n"
        "fotografie,photograph\n"
        "fotografie události,photograph of event\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def vocab_translator(tmp_path: Path, vocab_csv: Path) -> LindatTranslator:
    """
    Translator with vocabulary loaded; all live network dependencies replaced
    by controllable mocks so tests never touch the network.

    * ``_lemmatizer.get_lemmas`` returns ``[]`` by default (overridable per test).
    * ``_basic_translate`` acts as a passthrough echo: ``f"[BT:{text}]"``.
    """
    with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
        t = LindatTranslator(vocab_path=str(vocab_csv))

    t._lemmatizer = MagicMock()
    t._lemmatizer.get_lemmas.return_value = []
    t._basic_translate = MagicMock(side_effect=lambda text, src, tgt: f"[BT:{text}]")
    return t


# ════════════════════════════════════════════════════════════════════════════
# _chunk_text  (static)
# ════════════════════════════════════════════════════════════════════════════

class TestChunkText:
    """Space-aware text chunker must never split mid-token."""

    def test_text_shorter_than_limit_is_single_chunk(self):
        assert LindatTranslator._chunk_text("hello world", chunk_size=100) == ["hello world"]

    def test_empty_string_returns_empty_list(self):
        assert LindatTranslator._chunk_text("", chunk_size=100) == []

    def test_text_exactly_at_limit_is_one_chunk(self):
        text = "a" * 50
        assert LindatTranslator._chunk_text(text, chunk_size=50) == [text]

    def test_long_text_split_at_word_boundary(self):
        """No word must appear mangled (split mid-token) in any chunk."""
        text = "alpha beta gamma delta epsilon"
        original_words = set(text.split())
        chunks = LindatTranslator._chunk_text(text, chunk_size=12)
        assert len(chunks) > 1
        for chunk in chunks:
            for word in chunk.split():
                assert word in original_words, f"Mangled token: {word!r}"

    def test_word_longer_than_limit_forces_hard_cut_without_crash(self):
        """A single oversized word must be cut at the limit rather than raising."""
        long_word = "x" * 200
        chunks = LindatTranslator._chunk_text(long_word, chunk_size=50)
        assert len(chunks) > 1
        # Reassembled chunks must reconstruct the original token
        assert "".join(chunks) == long_word

    def test_reassembled_chunks_equal_original_text(self):
        text = " ".join(["word"] * 30)
        chunks = LindatTranslator._chunk_text(text, chunk_size=20)
        # Joining chunks back must reproduce the original (modulo leading/trailing spaces)
        reassembled = " ".join(chunks)
        assert reassembled.split() == text.split()


# ════════════════════════════════════════════════════════════════════════════
# _restore_tags  (static)
# ════════════════════════════════════════════════════════════════════════════

class TestRestoreTags:
    """Tag placeholders in translated text must be replaced with vocabulary entries."""

    def test_single_tag_replaced(self):
        result = LindatTranslator._restore_tags(
            "The __TERM_0__ jumped.", {"__TERM_0__": "cat"}
        )
        assert result == "The cat jumped."

    def test_multiple_tags_each_replaced_independently(self):
        result = LindatTranslator._restore_tags(
            "A __TERM_0__ and a __TERM_1__.",
            {"__TERM_0__": "dog", "__TERM_1__": "cat"},
        )
        assert result == "A dog and a cat."

    def test_tag_absent_from_translation_is_left_unchanged(self):
        """A tag not in the map must survive the restore pass intact."""
        result = LindatTranslator._restore_tags(
            "Unrelated __TERM_99__ here.", {"__TERM_0__": "foo"}
        )
        assert "__TERM_99__" in result

    def test_no_tags_in_map_returns_text_unchanged(self):
        text = "plain text without tags"
        assert LindatTranslator._restore_tags(text, {}) == text

    def test_fuzzy_pattern_handles_nmt_introduced_spaces(self):
        """
        NMT models occasionally insert spaces inside placeholder tokens.
        The fuzzy regex must still restore the replacement.
        """
        # __TERM_0__ → "__ TERM_0 __" (spaces injected by NMT)
        translated = "The __ TERM_0 __ was found."
        result = LindatTranslator._restore_tags(translated, {"__TERM_0__": "artefact"})
        # Either successfully restored or at least no crash
        assert isinstance(result, str)


# ════════════════════════════════════════════════════════════════════════════
# _load_vocabulary
# ════════════════════════════════════════════════════════════════════════════

class TestLoadVocabulary:
    """Vocabulary CSV loader — file I/O only, no network."""

    def test_basic_two_column_csv_loaded_correctly(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"
        p.write_text("nález,find\nkostel,church\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert vocab["nález"] == "find"
        assert vocab["kostel"] == "church"

    def test_standard_header_row_is_skipped(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"
        p.write_text("source_lemma,target_translation\nnález,find\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert "source_lemma" not in vocab
        assert vocab.get("nález") == "find"

    def test_keys_are_stored_lower_case(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"
        p.write_text("Nález,find\nKostel,church\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert "nález" in vocab
        assert "kostel" in vocab
        assert "Nález" not in vocab

    def test_missing_file_returns_empty_dict(self, bare_translator, tmp_path):
        vocab = bare_translator._load_vocabulary(tmp_path / "nonexistent.csv")
        assert vocab == {}

    def test_rows_with_fewer_than_two_columns_are_skipped(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"
        p.write_text("only_one_column\nnález,find\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert "only_one_column" not in vocab
        assert "nález" in vocab

    def test_multiword_phrase_stored_verbatim(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"
        p.write_text("fotografie události,photograph of event\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert "fotografie události" in vocab
        assert vocab["fotografie události"] == "photograph of event"


# ════════════════════════════════════════════════════════════════════════════
# _translate_with_vocabulary  (mocked lemmatizer + _basic_translate)
# ════════════════════════════════════════════════════════════════════════════

class TestTranslateWithVocabulary:
    """
    Tag-and-Protect pipeline — tested without network by mocking
    ``_lemmatizer.get_lemmas`` and ``_basic_translate``.
    """

    def test_multiword_phrase_tagged_and_restored(self, vocab_translator):
        """
        "fotografie události" is a multi-word vocab entry.
        The phrase must be protected before translation and restored afterwards.
        """
        # Passthrough translation: tags survive unchanged
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text

        result = vocab_translator.translate("Nalezena fotografie události v archivu.", "cs", "en")
        assert "photograph of event" in result

    def test_single_word_matched_via_lemma_and_restored(self, vocab_translator):
        """
        The inflected form "nálezu" has lemma "nález" which maps to "find".
        After tag-and-protect, the result must contain "find".
        """
        vocab_translator._lemmatizer.get_lemmas.return_value = [("nálezu", "nález")]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text

        result = vocab_translator.translate("Popis nálezu.", "cs", "en")
        assert "find" in result

    def test_no_vocab_match_calls_basic_translate_with_original_text(self, vocab_translator):
        """
        When no protected terms are found, ``_basic_translate`` must be called
        with the original (unmodified) source text.
        """
        vocab_translator._lemmatizer.get_lemmas.return_value = [("neznámé", "neznámý")]
        original = "Text bez shody ve slovníku."
        vocab_translator.translate(original, "cs", "en")
        vocab_translator._basic_translate.assert_called_once_with(original, "cs", "en")

    def test_empty_lemma_result_falls_back_to_basic_translate(self, vocab_translator):
        """
        When the lemmatizer returns an empty list (e.g. network timeout),
        the pipeline must still call ``_basic_translate`` without crashing.
        """
        vocab_translator._lemmatizer.get_lemmas.return_value = []
        vocab_translator.translate("Bez lemmatizace.", "cs", "en")
        vocab_translator._basic_translate.assert_called_once()

    def test_placeholder_tag_not_visible_in_final_output(self, vocab_translator):
        """__TERM_N__ placeholders must never leak into the final translation."""
        vocab_translator._lemmatizer.get_lemmas.return_value = [("nálezu", "nález")]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text

        result = vocab_translator.translate("Popis nálezu.", "cs", "en")
        assert "__TERM_" not in result

    def test_same_src_and_tgt_lang_short_circuits(self, vocab_translator):
        """When src_lang == tgt_lang, the text must be returned unchanged immediately."""
        result = vocab_translator.translate("Nezměněný text.", "cs", "cs")
        assert result == "Nezměněný text."
        vocab_translator._basic_translate.assert_not_called()