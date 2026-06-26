"""
tests/test_edge_cases.py
========================
Unit tests for the chunking and alignment edge cases identified in Issue #17.
"""

from processors.chunking import chunk_text
from utils import _align_tokens_to_lines


class TestChunkingEdgeCases:
    def test_exact_limit(self):
        """Text of exactly 4000 characters (no newline) is returned as a single chunk."""
        text = "x" * 4000
        assert chunk_text(text, chunk_size=4000) == [text]

    def test_long_sentence(self):
        """Splits at the sentence boundary closest to the 4000-window."""
        text = "A" * 1500 + ". " + "B" * 3000
        chunks = chunk_text(text, chunk_size=4000)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 1500 + "."
        assert chunks[1] == "B" * 3000

    def test_fallback_early(self):
        """No punctuation >1000, space at pos <1000. Splits at space."""
        text = "A" * 100 + " " + "B" * 4000
        chunks = chunk_text(text, chunk_size=4000)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 100
        assert chunks[1] == "B" * 4000
        # Ensure no data is lost except the delimiter space itself
        assert " ".join(chunks) == text

    def test_no_whitespace(self):
        """Single token >4000 chars splits exactly at 4000 (hard cut)."""
        text = "A" * 5000
        chunks = chunk_text(text, chunk_size=4000)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 4000
        assert chunks[1] == "A" * 1000
        # Ensure no data is lost in the hard cut
        assert "".join(chunks) == text

    def test_dense_text(self):
        """Splits at highest-priority punctuation found (e.g., commas) within limit."""
        text = "A" * 1500 + ", " + "B" * 2600
        chunks = chunk_text(text, chunk_size=4000)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 1500 + ","
        assert chunks[1] == "B" * 2600


class TestAlignmentEdgeCases:
    def test_alignment_simple(self):
        """Assigns exactly the original words to each line based on 1-to-1 similarity."""
        block = "A B C D E"
        lines = ["A B", "C D", "E"]
        buckets = _align_tokens_to_lines(block, lines)
        assert buckets == [["A", "B"], ["C", "D"], ["E"]]

    def test_alignment_unbalanced(self):
        """Empty line anchors receive empty buckets; remaining tokens are absorbed by valid lines."""
        block = "A B C D E"
        lines = ["A B", "", "C D E"]
        buckets = _align_tokens_to_lines(block, lines)
        assert buckets == [["A", "B"], [], ["C", "D", "E"]]
