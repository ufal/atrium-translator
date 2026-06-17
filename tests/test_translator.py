from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from requests.exceptions import RequestException

from processors.translator import LindatTranslator, TranslationError
import processors.translator as translator_module


@patch("processors.translator.requests.post")
def test_post_with_retry_success_after_429(mock_post):
    """Verify that a 429 Too Many Requests triggers a retry and succeeds."""
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.raise_for_status.side_effect = RequestException("429 Too Many Requests")

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.text = "Success"

    mock_post.side_effect = [mock_resp_429, mock_resp_200]

    translator = LindatTranslator(vocab_path=None)
    # Bypass the throttle mechanism and speed up the backoff for fast testing
    translator._throttle = MagicMock()
    translator_module._BACKOFF_BASE_S = 0.01

    result = translator._post_with_retry("http://fake-api.cz", data={"text": "test"})

    assert result == "Success"
    assert mock_post.call_count == 2


@patch("processors.translator.requests.post")
def test_post_with_retry_max_retries_exceeded(mock_post):
    """Verify the translator gives up after max retries on persistent 5xx errors."""
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.raise_for_status.side_effect = RequestException("500 Internal Server Error")

    mock_post.side_effect = [mock_resp_500] * 5  # Persistently fail

    translator = LindatTranslator(vocab_path=None)
    translator._throttle = MagicMock()
    translator_module._BACKOFF_BASE_S = 0.01

    with pytest.raises(TranslationError):
        translator._post_with_retry("http://fake-api.cz", data={"text": "test"})

    assert mock_post.call_count == 5  # 1 initial attempt + 4 retries


def test_homonym_single_word_lemma_protection():
    """
    Regression test: Ensure single-word homonyms are correctly matched via UDPipe
    lemma and protected without capturing adjacent punctuation.
    """
    translator = LindatTranslator(vocab_path=None)
    translator.vocabulary = {"zamek": "castle"}
    translator._multiword_terms = []

    # Mock the lemmatizer to force a match
    mock_lemmatizer = MagicMock()
    mock_lemmatizer.get_lemmas_with_features.return_value = [
        ("Přijeli", "přijet", ""), ("jsme", "být", ""), ("k", "k", ""),
        ("zámku", "zamek", "Sing"), (".", ".", "")
    ]
    translator._lemmatizer = mock_lemmatizer

    # Intercept the NMT call so we can inspect the placeholder insertion
    captured_nmt_input = []

    def mock_basic_translate(text, src, tgt):
        captured_nmt_input.append(text)
        return text  # Act as an identity translation

    translator._basic_translate = mock_basic_translate

    source_text = "Přijeli jsme k zámku."

    # Run the full pipeline
    final_text = translator.translate(source_text, src_lang="cs", tgt_lang="en")

    # Assert NMT received the Tag
    assert len(captured_nmt_input) == 1
    assert "Xtermzzz" in captured_nmt_input[0]
    assert "zámku" not in captured_nmt_input[0]
    assert translator.protected_count == 1

    # Assert the Tag was properly swapped out in the final output
    assert "castle" in final_text


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

    # ── Boundary-priority regression tests (issue #3) ─────────────────────────
    # These pin the fix to the priority bug: the original implementation kept
    # the rightmost candidate across ALL separators, so a later, lower-priority
    # separator could override an earlier, higher-priority one. The corrected
    # shared helper enforces the tier order (newline > sentence > clause > word).

    def test_sentence_terminal_wins_over_later_comma(self):
        """THE bug guard: a period must beat a later comma in the same window.

        Fails on the pre-fix code (it would split at '...word,'); passes after
        the tiered-priority fix (splits right after 'Short start.').
        """
        text = "Short start. word word word word word, tail"
        chunks = LindatTranslator._chunk_text(text, chunk_size=40)
        assert chunks[0] == "Short start."

    def test_sentence_boundary_preferred_over_later_comma_simple(self):
        """Sentence punctuation beats a clause comma sitting further right."""
        text = "One sentence here. then some, more clause text follows on"
        chunks = LindatTranslator._chunk_text(text, chunk_size=40)
        assert chunks[0] == "One sentence here."

    def test_newline_preferred_and_excluded_keep_zero(self):
        """A newline (keep=0) outranks a later period and is itself dropped."""
        text = "First line here\nthen a sentence. and yet more words after that"
        chunks = LindatTranslator._chunk_text(text, chunk_size=40)
        # newline wins -> first chunk is the text before it, with no trailing '\n'
        assert chunks[0] == "First line here"
        assert "\n" not in chunks[0]

    def test_clause_preferred_over_later_bare_space(self):
        """A clause comma (tier 3) beats a plain word space (fallback tier)."""
        text = "alpha beta, gamma delta epsilon zeta eta theta iota"
        chunks = LindatTranslator._chunk_text(text, chunk_size=30)
        # comma kept with the left chunk; the later spaces do not override it
        assert chunks[0] == "alpha beta,"

    def test_lossless_reassembly_with_mixed_punctuation(self):
        """No token is lost or duplicated across mixed-punctuation splits.

        Compare on word sets, not exact strings: chunking strips the separator
        whitespace between chunks.
        """
        text = ("Intro clause, with comma. A second sentence here! And a third "
                "one? plus a tail\nafter newline and more and more words again")
        chunks = LindatTranslator._chunk_text(text, chunk_size=40)
        assert len(chunks) > 1
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
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nálezů", "nález", "Plur")
        ]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nálezů.", "cs", "en")
        assert "find" not in result
        vocab_translator._basic_translate.assert_called_once_with("Popis nálezů.", "cs", "en")

    def test_singular_surface_form_is_still_protected(self, vocab_translator):
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nálezu", "nález", "Sing")
        ]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nálezu.", "cs", "en")
        assert "find" in result

    def test_number_neutral_token_is_protected(self, vocab_translator):
        vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nález", "nález", "")
        ]
        vocab_translator._basic_translate.side_effect = lambda text, src, tgt: text
        result = vocab_translator.translate("Popis nález.", "cs", "en")
        assert "find" in result

    def test_legacy_lemmatizer_without_features_still_works(self, tmp_path, vocab_csv):
        from unittest.mock import MagicMock, patch
        from processors.translator import LindatTranslator
        with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
            t = LindatTranslator(vocab_path=str(vocab_csv))

        class LegacyLemmatizer:
            def get_lemmas(self, text, lang="cs"):
                return [("nálezu", "nález")]

        t._lemmatizer = LegacyLemmatizer()
        t._basic_translate = MagicMock(side_effect=lambda text, src, tgt: text)
        result = t.translate("Popis nálezu.", "cs", "en")
        assert "find" in result