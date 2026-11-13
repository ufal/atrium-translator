"""
tests/test_lemmatizer.py
========================
Unit tests for processors/lemmatizer.py — LindatLemmatizer._parse_conllu and the
shared sentence-aware chunker delegation (_chunk_text).

``_parse_conllu`` is a pure static method that converts raw CoNLL-U text into
``(word, lemma)`` pairs.  ``_chunk_text`` is a thin instance-method wrapper over
``processors.chunking.chunk_text`` (also used by the translator).  No network,
no ML, no file I/O.
"""

from unittest.mock import MagicMock, patch

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
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        words = [w for w, _ in result]
        assert all(not w.startswith("#") for w in words)

    def test_blank_lines_between_sentences_are_ignored(self):
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        assert all(isinstance(w, str) and w for w, _ in result)

    def test_word_and_lemma_extracted_from_correct_columns(self):
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        assert result[0] == ("Dobrý", "dobrý")
        assert result[2] == (".", ".")

    def test_all_tokens_from_two_sentences_collected(self):
        result = LindatLemmatizer._parse_conllu(TWO_SENTENCES)
        assert len(result) == 7

    def test_multiword_token_range_line_skipped(self):
        result = LindatLemmatizer._parse_conllu(WITH_MULTIWORD_TOKEN)
        ids_seen = [w for w, _ in result]
        assert len(result) == 2
        assert ids_seen == ["vánoční", "den"]

    def test_empty_node_line_skipped(self):
        result = LindatLemmatizer._parse_conllu(WITH_EMPTY_NODE)
        assert len(result) == 1
        assert result[0] == ("slovo", "slovo")

    def test_line_with_fewer_than_three_tab_columns_skipped(self):
        malformed = "1\tonly_two_cols\n"
        result = LindatLemmatizer._parse_conllu(malformed)
        assert result == []


# ════════════════════════════════════════════════════════════════════════════
# LindatLemmatizer._chunk_text — shared sentence-aware chunker delegation
# ════════════════════════════════════════════════════════════════════════════


class TestLemmatizerChunkText:
    """The lemmatizer must delegate to the same priority-correct chunker as the
    translator. LindatLemmatizer.__init__ is network-free, so this is hermetic."""

    def test_short_text_is_single_chunk(self):
        lem = LindatLemmatizer()
        assert lem._chunk_text("hello world", chunk_size=100) == ["hello world"]

    def test_sentence_terminal_wins_over_later_comma(self):
        """Same boundary-priority guarantee the translator relies on: the period
        beats a later comma (would fail under the old rightmost-wins logic)."""
        lem = LindatLemmatizer()
        text = "Short start. word word word word word, tail"
        chunks = lem._chunk_text(text, chunk_size=40)
        assert chunks[0] == "Short start."
        # and nothing is lost across the split
        assert " ".join(chunks).split() == text.split()


# ════════════════════════════════════════════════════════════════════════════
# Language coverage and the UDPipe request
# ════════════════════════════════════════════════════════════════════════════

_ONE_TOKEN = "1\tzámku\tzámek\tNOUN\t_\tCase=Gen|Number=Sing\t_\t_\t_\t_\n"


def _ok(conllu: str = _ONE_TOKEN) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"result": conllu}
    return resp


class TestLanguageCoverage:
    def test_supports_reflects_the_model_table(self):
        lem = LindatLemmatizer()
        assert lem.supports("cs") and lem.supports("de")
        assert not lem.supports("hu")
        assert not lem.supports("auto")

    @patch("processors.lemmatizer.requests.post")
    def test_unsupported_language_posts_nothing_and_warns_once(self, mock_post, capsys):
        """No Czech-model fallback: Czech lemmas of Hungarian tokens are noise."""
        lem = LindatLemmatizer()
        assert lem.get_lemmas("Egy régi vár romjai.", lang="hu") == []
        assert lem.get_lemmas_with_features("Egy régi vár romjai.", lang="hu") == []
        assert lem.get_lemmas("Még egy mondat.", lang="hu") == []

        mock_post.assert_not_called()
        warnings = [line for line in capsys.readouterr().out.splitlines() if "[WARN]" in line]
        assert len(warnings) == 1
        assert "'hu'" in warnings[0]

    @patch("processors.lemmatizer.requests.post")
    def test_each_unsupported_language_warns_separately(self, mock_post, capsys):
        lem = LindatLemmatizer()
        lem.get_lemmas("x", lang="hu")
        lem.get_lemmas("x", lang="ro")
        out = capsys.readouterr().out
        assert out.count("[WARN]") == 2

    @pytest.mark.parametrize("lang", sorted(LindatLemmatizer.MODELS))
    @patch("processors.lemmatizer.requests.post")
    def test_each_supported_language_uses_its_own_model(self, mock_post, lang):
        mock_post.return_value = _ok()
        LindatLemmatizer(url="http://udpipe.test").get_lemmas("slovo", lang=lang)
        assert mock_post.call_args.kwargs["data"]["model"] == LindatLemmatizer.MODELS[lang]


class TestUdpipeRequest:
    @patch("processors.lemmatizer.requests.post")
    def test_payload_requests_tokenizer_and_tagger_but_not_parser(self, mock_post):
        """LEMMA and FEATS come from the tagger; HEAD/DEPREL are never read."""
        mock_post.return_value = _ok()
        LindatLemmatizer(url="http://udpipe.test").get_lemmas_with_features("k zámku", lang="cs")

        data = mock_post.call_args.kwargs["data"]
        assert set(data) == {"model", "tokenizer", "tagger", "data"}
        assert "parser" not in data
        assert mock_post.call_args.args[0] == "http://udpipe.test"

    @patch("processors.lemmatizer.requests.post")
    def test_tagger_output_still_yields_lemma_and_number(self, mock_post):
        mock_post.return_value = _ok()
        items = LindatLemmatizer(url="http://udpipe.test").get_lemmas_with_features("k zámku", lang="cs")
        assert items == [("zámku", "zámek", "Sing")]


def test_a_udpipe_timeout_fails_the_job(monkeypatch):
    """UDPIPE_TIMEOUT_S is read per call. A timed-out chunk would go unprotected, so the note
    raises ContentLimitError and the job fails (D13, atrium-project#84): no partial result."""
    import pytest
    import requests

    from atrium_limits import ContentLimitError
    from processors.limit_notes import collecting

    monkeypatch.setenv("UDPIPE_TIMEOUT_S", "2.5")
    lem = LindatLemmatizer(url="https://udpipe.example.test/process")
    with patch("processors.lemmatizer.requests.post", side_effect=requests.exceptions.Timeout()) as post:
        with collecting():
            with pytest.raises(ContentLimitError) as raised:
                list(lem._request_conllu_chunks("Hrad stojí na kopci.", "czech"))
    assert post.call_args.kwargs["timeout"] == 2.5
    assert (raised.value.limit, raised.value.effect) == ("udpipe_timeout_s", "skipped")
