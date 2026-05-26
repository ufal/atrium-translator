"""
tests/test_lemmatizer.py
========================
Unit tests for processors/lemmatizer.py — LindatLemmatizer._parse_conllu.

``_parse_conllu`` is a pure static method that converts raw CoNLL-U text into
``(word, lemma)`` pairs.  No network, no ML, no file I/O.
"""

import pytest

from processors.lemmatizer import LindatLemmatizer


# ── CoNLL-U test documents ────────────────────────────────────────────────────

# Two complete sentences: 3 + 4 = 7 regular tokens.
TWO_SENTENCES = """\
# sent_id = 1
# text = Dobrý den.
1\tDobrý\tdobrý\tADJ\t_\t_\t_\t_\t_\t_
2\tden\tden\tNOUN\t_\t_\t_\t_\t_\t_
3\t.\t.\tPUNCT\t_\t_\t_\t_\t_\t_

# sent_id = 2
# text = Jak se máte?
1\tJak\tjak\tADV\t_\t_\t_\t_\t_\t_
2\tse\tse\tPRON\t_\t_\t_\t_\t_\t_
3\tmáte\tmít\tVERB\t_\t_\t_\t_\t_\t_
4\t?\t?\tPUNCT\t_\t_\t_\t_\t_\t_
"""

# CoNLL-U multi-word token (MWT): the "1-2" range line must be skipped.
WITH_MULTIWORD_TOKEN = """\
# text = vánoční den
1-2\tvánoční den\t_\t_\t_\t_\t_\t_\t_\t_
1\tvánoční\tvánoční\tADJ\t_\t_\t_\t_\t_\t_
2\tden\tden\tNOUN\t_\t_\t_\t_\t_\t_
"""

# CoNLL-U empty node (enhanced UD): the "1.1" line must be skipped.
WITH_EMPTY_NODE = """\
1\tslovo\tslovo\tNOUN\t_\t_\t_\t_\t_\t_
1.1\tempty\tempty\tNOUN\t_\t_\t_\t_\t_\t_
"""


# ════════════════════════════════════════════════════════════════════════════
# LindatLemmatizer._parse_conllu
# ════════════════════════════════════════════════════════════════════════════

class TestParseConllu:
    """Tests for the pure static CoNLL-U parser."""

    def test_empty_string_yields_empty_list(self):
        assert LindatLemmatizer._parse_conllu("") == []

    def test_comment_lines_are_excluded(self):
        """Lines beginning with '#' must not appear in the output."""
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        words = [w for w, _ in result]
        assert all(not w.startswith("#") for w in words)

    def test_blank_lines_between_sentences_are_ignored(self):
        """Blank sentence separators must not cause extra entries."""
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        # Only actual token lines contribute; blank lines do not
        assert all(isinstance(w, str) and w for w, _ in result)

    def test_word_and_lemma_extracted_from_correct_columns(self):
        """CoNLL-U col-2 (FORM) and col-3 (LEMMA) must be read in order."""
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        assert result[0] == ("Dobrý", "dobrý")   # first token of sentence 1
        assert result[2] == (".", ".")            # punctuation token

    def test_all_tokens_from_two_sentences_collected(self):
        """3 tokens in s1 + 4 tokens in s2 = 7 total (punctuation included)."""
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        assert len(result) == 7

    def test_multiword_token_range_line_skipped(self):
        """
        A MWT line (ID contains '-', e.g. '1-2') must be omitted;
        only the individual component lines ('1', '2') are kept.
        """
        result = LindatLemmatizer._parse_conllu(WITH_MULTIWORD_TOKEN)
        ids_seen = [w for w, _ in result]
        assert len(result) == 2
        assert ids_seen == ["vánoční", "den"]

    def test_empty_node_line_skipped(self):
        """
        An enhanced-UD empty node (ID contains '.', e.g. '1.1') must be omitted.
        """
        result = LindatLemmatizer._parse_conllu(WITH_EMPTY_NODE)
        assert len(result) == 1
        assert result[0] == ("slovo", "slovo")

    def test_line_with_fewer_than_three_tab_columns_skipped(self):
        """Malformed / incomplete lines must not raise; they are silently skipped."""
        malformed = "1\tonly_two_cols\n"
        result = LindatLemmatizer._parse_conllu(malformed)
        assert result == []