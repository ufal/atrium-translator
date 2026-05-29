from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from processors.translator import LindatTranslator

@pytest.fixture
def bare_translator():
    with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
        return LindatTranslator()

@pytest.fixture
def vocab_csv(tmp_path):
    p = tmp_path / "vocab.csv"
    p.write_text("source_lemma,target_translation\nnález,find\nfotografie,photograph\nfotografie události,photograph of event\n", encoding="utf-8")
    return p

@pytest.fixture
def vocab_translator(tmp_path, vocab_csv):
    with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
        t = LindatTranslator(vocab_path=str(vocab_csv))
    t._lemmatizer = MagicMock()
    t._lemmatizer.get_lemmas_with_features.return_value = []
    t._lemmatizer.get_lemmas.return_value = []
    t._basic_translate = MagicMock(side_effect=lambda text, src, tgt: f"[BT:{text}]")
    return t

class TestChunkText:
    def test_text_shorter_than_limit_is_single_chunk(self):
        assert LindatTranslator._chunk_text("hello world", chunk_size=100) == ["hello world"]
    def test_empty_string_returns_empty_list(self):
        assert LindatTranslator._chunk_text("", chunk_size=100) == []
    def test_text_exactly_at_limit_is_one_chunk(self):
        text = "a" * 50
        assert LindatTranslator._chunk_text(text, chunk_size=50) == [text]
    def test_long_text_split_at_word_boundary(self):
        text = "alpha beta gamma delta epsilon"
        original_words = set(text.split())
        chunks = LindatTranslator._chunk_text(text, chunk_size=12)
        assert len(chunks) > 1
        for chunk in chunks:
            for word in chunk.split():
                assert word in original_words
    def test_word_longer_than_limit_forces_hard_cut_without_crash(self):
        long_word = "x" * 200
        chunks = LindatTranslator._chunk_text(long_word, chunk_size=50)
        assert len(chunks) > 1
        assert "".join(chunks) == long_word
    def test_reassembled_chunks_equal_original_text(self):
        text = " ".join(["word"] * 30)
        chunks = LindatTranslator._chunk_text(text, chunk_size=20)
        assert " ".join(chunks).split() == text.split()

class TestRestoreTags:
    def test_single_tag_replaced(self):
        tag = LindatTranslator._make_tag(0)
        result = LindatTranslator._restore_tags(f"The {tag} jumped.", {tag: "cat"})
        assert result == "The cat jumped."
    def test_multiple_tags_each_replaced_independently(self):
        t0, t1 = LindatTranslator._make_tag(0), LindatTranslator._make_tag(1)
        result = LindatTranslator._restore_tags(f"A {t0} and a {t1}.", {t0: "dog", t1: "cat"})
        assert result == "A dog and a cat."
    def test_no_tags_in_map_returns_text_unchanged(self):
        text = "plain text without tags"
        assert LindatTranslator._restore_tags(text, {}) == text
    def test_fuzzy_pattern_handles_nmt_introduced_spaces(self):
        tag = LindatTranslator._make_tag(0)
        translated = "The " + " ".join(list(tag)) + " was found."
        result = LindatTranslator._restore_tags(translated, {tag: "artefact"})
        assert isinstance(result, str)
        assert "artefact" in result

class TestLoadVocabulary:
    def test_basic_two_column_csv_loaded_correctly(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"; p.write_text("nález,find\nkostel,church\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert vocab["nález"] == "find" and vocab["kostel"] == "church"
    def test_standard_header_row_is_skipped(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"; p.write_text("source_lemma,target_translation\nnález,find\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert "source_lemma" not in vocab and vocab.get("nález") == "find"
    def test_keys_are_stored_lower_case(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"; p.write_text("Nález,find\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert "nález" in vocab and "Nález" not in vocab
    def test_missing_file_returns_empty_dict(self, bare_translator, tmp_path):
        assert bare_translator._load_vocabulary(tmp_path / "nope.csv") == {}
    def test_multiword_phrase_stored_verbatim(self, bare_translator, tmp_path):
        p = tmp_path / "v.csv"; p.write_text("fotografie události,photograph of event\n", encoding="utf-8")
        vocab = bare_translator._load_vocabulary(p)
        assert vocab["fotografie události"] == "photograph of event"

class TestTranslateWithVocabulary:
    def test_multiword_phrase_tagged_and_restored(self, vocab_translator):
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Nalezena fotografie události v archivu.", "cs", "en")
        assert "photograph of event" in result
    def test_single_word_matched_via_lemma_and_restored(self, vocab_translator):
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [("nálezu", "nález", "Sing")]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nálezu.", "cs", "en")
        assert "find" in result
    def test_no_vocab_match_calls_basic_translate_with_original_text(self, vocab_translator):
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [("neznámé", "neznámý", "Sing")]
        original = "Text bez shody ve slovníku."
        vocab_translator.translate(original, "cs", "en")
        vocab_translator._basic_translate.assert_called_once_with(original, "cs", "en")
    def test_placeholder_tag_not_visible_in_final_output(self, vocab_translator):
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [("nálezu", "nález", "Sing")]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nálezu.", "cs", "en")
        assert "__TERM_" not in result and "xterm" not in result.lower()
    def test_same_src_and_tgt_lang_short_circuits(self, vocab_translator):
        result = vocab_translator.translate("Nezměněný text.", "cs", "cs")
        assert result == "Nezměněný text."
        vocab_translator._basic_translate.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# Number-agreement guard (plural source tokens are not frozen to singular vocab)
# ════════════════════════════════════════════════════════════════════════════

class TestNumberAgreementGuard:
    """A plural Czech surface form must NOT be protected with a singular term."""

    def test_plural_surface_form_is_not_protected(self, vocab_translator):
        # "nálezů" is plural; lemma "nález" is in vocab → must be left for the NMT
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nálezů", "nález", "Plur")
        ]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nálezů.", "cs", "en")
        # vocabulary translation "find" must NOT have been forced in
        assert "find" not in result
        # and _basic_translate received the ORIGINAL text (no placeholder)
        vocab_translator._basic_translate.assert_called_once_with("Popis nálezů.", "cs", "en")

    def test_singular_surface_form_is_still_protected(self, vocab_translator):
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nálezu", "nález", "Sing")
        ]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nálezu.", "cs", "en")
        assert "find" in result

    def test_number_neutral_token_is_protected(self, vocab_translator):
        """Empty Number feature (unknown) defaults to protect."""
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nález", "nález", "")
        ]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nález.", "cs", "en")
        assert "find" in result

    def test_legacy_lemmatizer_without_features_still_works(self, tmp_path, vocab_csv):
        """A lemmatizer exposing only get_lemmas (no features) must not crash."""
        from unittest.mock import MagicMock, patch
        from processors.translator import LindatTranslator
        with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
            t = LindatTranslator(vocab_path=str(vocab_csv))

        class LegacyLemmatizer:  # only the old 2-tuple API
            def get_lemmas(self, text, lang="cs"):
                return [("nálezu", "nález")]

        t._lemmatizer = LegacyLemmatizer()
        t._basic_translate = MagicMock(side_effect=lambda text, src, tgt: text)
        result = t.translate("Popis nálezu.", "cs", "en")
        assert "find" in result