"""
tests/test_content_preservation.py
==================================
Intermediate input→output content-preservation tests for atrium-translator
(atrium-project issue #14, extension request).

Purpose
-------
These tests verify that the text-transformation steps of the translation
pipeline — sentence-aware chunking, Tag-and-Protect vocabulary substitution,
and UDPipe CoNLL-U lemmatization parsing — **reshape** text without ever
**dropping, merging, reordering, or silently losing** meaningful source
content.

They encode preservation *invariants* rather than exact outputs, so they keep
passing under legitimate rewrites (whitespace normalization, tokenization,
placeholder substitution, mock translation) but fail the moment a future change
loses a word, a number, a date, or a token.

Hermetic: no ML models, no GPU, no network.
  * ``_chunk_text`` / the CoNLL-U parsers are pure functions.
  * ``_basic_translate`` is mocked to *echo* its input so post-translation
    content is inspectable.
  * ``_lemmatizer`` is a MagicMock; ``_fetch_models`` is patched.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from processors.lemmatizer import LindatLemmatizer
from processors.translator import LindatTranslator

# ── fixtures (mirroring tests/test_translator.py) ─────────────────────────────


@pytest.fixture
def vocab_csv(tmp_path):
    p = tmp_path / "vocab.csv"
    p.write_text(
        "source_lemma,target_translation\n"
        "nález,find\n"
        "kostel,church\n"
        "fotografie,photograph\n"
        "fotografie události,photograph of event\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def echo_vocab_translator(tmp_path, vocab_csv):
    """Vocabulary translator whose ``_basic_translate`` echoes its input.

    The echo keeps every word the NMT *would* have seen visible in the output,
    so a content-loss regression in the Tag-and-Protect passes is detectable.
    The lemmatizer is mocked and returns no single-word lemmas by default
    (individual tests override it).
    """
    with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
        t = LindatTranslator(vocab_path=str(vocab_csv))
    t._lemmatizer = MagicMock()
    t._lemmatizer.get_lemmas_with_features.return_value = []
    t._lemmatizer.get_lemmas.return_value = []
    t._basic_translate = MagicMock(side_effect=lambda text, src, tgt: text)
    return t


def _nonspace(s: str) -> str:
    """Collapse all whitespace away, keeping only meaningful characters."""
    return "".join(s.split())


# ════════════════════════════════════════════════════════════════════════════
# Chunking — must only insert/relocate separators, never lose characters
# ════════════════════════════════════════════════════════════════════════════


class TestChunkingConservesContent:
    CHUNK_TEXTS = [
        "Stará Boleslav - odvodnění ohradní kamenné zdi v areálu baziliky.",
        "Sonda č. 5 o celkovém rozměru 1,5 x 1,5 m se nacházela v S části.",
        "Datováno do 14. - 16. stol.; nález z r. 1348, parc. č. 41/1, 41/5.",
        "WGS-84: 49°34'13.52\"N, 16°07'19.61\"E; nadmořská výška 672 m n. m.",
        " ".join(f"slovo{i}" for i in range(400)),
    ]

    @pytest.mark.parametrize("text", CHUNK_TEXTS)
    @pytest.mark.parametrize("chunk_size", [20, 40, 100, 4000])
    def test_chunking_conserves_all_nonspace_chars(self, text, chunk_size):
        """Reassembled chunks must hold exactly the same non-space characters."""
        chunks = LindatTranslator._chunk_text(text, chunk_size=chunk_size)
        assert _nonspace("".join(chunks)) == _nonspace(text)

    @pytest.mark.parametrize("chunk_size", [20, 50, 200])
    def test_chunking_keeps_numbers_and_dates(self, chunk_size):
        """Digits, years, coordinates and measurements survive intact."""
        text = (
            "V r. 1348 a 1358, později 1360; rozměr 1,5 x 2,2 m, výška 422 m n. m., 49°34'13.52\"N parc. 41/11 a 137/1."
        )
        chunks = LindatTranslator._chunk_text(text, chunk_size=chunk_size)
        joined = " ".join(chunks)
        for token in ["1348", "1358", "1360", "1,5", "2,2", "422", "41/11", "137/1", "49°34'13.52\"N"]:
            assert token in joined, f"{token!r} lost during chunking"

    @pytest.mark.parametrize("text", CHUNK_TEXTS)
    @pytest.mark.parametrize("chunk_size", [20, 40, 100])
    def test_no_chunk_exceeds_size_when_splittable(self, text, chunk_size):
        """No produced chunk exceeds chunk_size unless it is one unbreakable token."""
        chunks = LindatTranslator._chunk_text(text, chunk_size=chunk_size)
        for c in chunks:
            assert len(c) <= chunk_size or " " not in c

    def test_word_order_preserved_across_chunks(self):
        text = " ".join(f"w{i}" for i in range(120))
        chunks = LindatTranslator._chunk_text(text, chunk_size=40)
        assert " ".join(chunks).split() == text.split()


# ════════════════════════════════════════════════════════════════════════════
# Vocabulary (Tag-and-Protect) — replace terms without losing neighbours
# ════════════════════════════════════════════════════════════════════════════


class TestVocabularyPreservesContent:
    def test_vocab_application_keeps_neighbouring_words(self, echo_vocab_translator):
        """A protected multi-word term is applied AND every other word survives."""
        src = "Nalezena fotografie události v archivu obce."
        result = echo_vocab_translator.translate(src, "cs", "en")
        # vocab target present …
        assert "photograph of event" in result
        # … and the surrounding source words are not dropped
        for word in ["Nalezena", "v", "archivu", "obce"]:
            assert word in result, f"neighbour {word!r} lost around protected term"

    def test_applied_vocab_terms_appear_in_output(self, echo_vocab_translator):
        """Single-word lemma match: the controlled translation reaches the output."""
        echo_vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [("nálezu", "nález", "Sing")]
        result = echo_vocab_translator.translate("Popis nálezu zde.", "cs", "en")
        assert "find" in result
        # neighbouring words preserved
        assert "Popis" in result and "zde" in result

    def test_multiple_terms_all_applied_and_text_intact(self, echo_vocab_translator):
        # Surface forms must match the source text for the \b...\b regex to fire.
        echo_vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("kostel", "kostel", "Sing"),
            ("nález", "nález", "Sing"),
        ]
        result = echo_vocab_translator.translate("kostel a nález poblíž", "cs", "en")
        assert "church" in result
        assert "find" in result
        assert "poblíž" in result

    def test_no_placeholder_residue_after_restore(self, echo_vocab_translator):
        """No sentinel debris (Xtermzzz…) may survive into the final output."""
        echo_vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [("nálezu", "nález", "Sing")]
        result = echo_vocab_translator.translate("Popis nálezu a fotografie události.", "cs", "en")
        assert "xterm" not in result.lower()
        assert "__term_" not in result.lower()

    def test_no_match_leaves_source_completely_intact(self, echo_vocab_translator):
        """When nothing matches, the (echoed) output equals the source untouched."""
        echo_vocab_translator._lemmatizer.get_lemmas_with_features.return_value = [("něco", "něco", "Sing")]
        src = "Žádná shoda ve slovníku zde."
        result = echo_vocab_translator.translate(src, "cs", "en")
        assert result == src


# ════════════════════════════════════════════════════════════════════════════
# Lemmatization parsing — every FORM survives, in order, MWT range skipped
# ════════════════════════════════════════════════════════════════════════════

# 3 + 4 = 7 regular tokens across two sentences
TWO_SENTENCES = """\
# text = Dobrý den.
1\tDobrý\tdobrý\tADJ\t_\t_\t_\t_\t_\t_
2\tden\tden\tNOUN\t_\t_\t_\t_\t_\t_
3\t.\t.\tPUNCT\t_\t_\t_\t_\t_\t_

# text = Jak se máte?
1\tJak\tjak\tADV\t_\t_\t_\t_\t_\t_
2\tse\tse\tPRON\t_\t_\t_\t_\t_\t_
3\tmáte\tmít\tVERB\t_\t_\t_\t_\t_\t_
4\t?\t?\tPUNCT\t_\t_\t_\t_\t_\t_
"""

WITH_MULTIWORD_TOKEN = """\
# text = vánoční den
1-2\tvánoční den\t_\t_\t_\t_\t_\t_\t_\t_
1\tvánoční\tvánoční\tADJ\t_\t_\t_\t_\t_\t_
2\tden\tden\tNOUN\t_\t_\t_\t_\t_\t_
"""


class TestLemmatizationPreservesForms:
    def test_all_forms_preserved(self):
        forms = [w for w, _ in LindatLemmatizer._parse_conllu(TWO_SENTENCES)]
        assert forms == ["Dobrý", "den", ".", "Jak", "se", "máte", "?"]

    def test_token_order_preserved(self):
        triples = LindatLemmatizer._parse_conllu_with_features(TWO_SENTENCES)
        forms = [w for w, _, _ in triples]
        assert forms == ["Dobrý", "den", ".", "Jak", "se", "máte", "?"]

    def test_no_form_dropped(self):
        forms = [w for w, _ in LindatLemmatizer._parse_conllu(TWO_SENTENCES)]
        assert len(forms) == 7

    def test_mwt_range_skipped_but_components_present(self):
        """The 1-2 range line is dropped, but both sub-tokens survive."""
        forms = [w for w, _ in LindatLemmatizer._parse_conllu(WITH_MULTIWORD_TOKEN)]
        assert "vánoční den" not in forms  # the range line itself is skipped
        assert forms == ["vánoční", "den"]  # components preserved, in order

    def test_features_parser_agrees_with_plain_on_forms(self):
        """The two parsers must never disagree on which FORMs survive."""
        plain = [w for w, _ in LindatLemmatizer._parse_conllu(TWO_SENTENCES)]
        feat = [w for w, _, _ in LindatLemmatizer._parse_conllu_with_features(TWO_SENTENCES)]
        assert plain == feat


# ════════════════════════════════════════════════════════════════════════════
# End-to-end: important source pieces survive the whole translate() pipeline
# ════════════════════════════════════════════════════════════════════════════


class TestSourceInfoSurvivesPipeline:
    """Run the real translate() path (real _chunk_text, echo _basic_translate,
    mock lemmatizer) and assert every important piece of source information —
    digits, dates, coordinates, and controlled-vocabulary targets — is present
    in the output. This is the direct encoding of issue #14's requirement."""

    @pytest.fixture
    def pipeline_translator(self, tmp_path, vocab_csv):
        with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
            t = LindatTranslator(vocab_path=str(vocab_csv))
        # Real chunking + real Tag-and-Protect, only the network call is faked.
        t._basic_translate = MagicMock(side_effect=lambda text, src, tgt: text)
        t._lemmatizer = MagicMock()
        t._lemmatizer.get_lemmas_with_features.return_value = [
            ("nálezu", "nález", "Sing"),
        ]
        return t

    def test_numbers_dates_and_vocab_all_survive(self, pipeline_translator):
        src = (
            "Popis nálezu z r. 1348, parc. č. 41/5, rozměr 1,5 x 2,2 m, "
            "fotografie události na souřadnici 49°34'13.52\"N."
        )
        out = pipeline_translator.translate(src, "cs", "en")

        # controlled vocabulary applied
        assert "find" in out  # nález → find (singular)
        assert "photograph of event" in out  # multi-word phrase

        # raw source information preserved verbatim
        for piece in ["1348", "41/5", "1,5", "2,2", "49°34'13.52\"N"]:
            assert piece in out, f"source detail {piece!r} lost in pipeline"

    def test_no_sentinel_debris_in_pipeline_output(self, pipeline_translator):
        src = "Popis nálezu a fotografie události v obci."
        out = pipeline_translator.translate(src, "cs", "en")
        assert not re.search(r"xterm", out, re.IGNORECASE)

    def test_plural_source_not_frozen_but_word_survives(self, pipeline_translator):
        """A plural source token is left for the NMT (not frozen to singular),
        yet the word itself is never dropped from the output."""
        pipeline_translator._lemmatizer.get_lemmas_with_features.return_value = [
            ("nálezů", "nález", "Plur"),
        ]
        src = "Popis nálezů v lokalitě."
        out = pipeline_translator.translate(src, "cs", "en")
        assert "find" not in out  # number-agreement guard held
        assert "nálezů" in out  # but the source word survived
        assert "lokalitě" in out
