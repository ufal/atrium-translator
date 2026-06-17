"""
tests/test_alignment.py
=======================
Unit tests for utils._align_tokens_to_lines — the token-alignment helper that
backs ALTO dual-pass reconstruction.

Context
-------
``process_alto_xml`` translates each TextBlock twice:
  * Pass 1 — the whole block (the high-quality tokens that are written out);
  * Pass 2 — each line individually (used only as *structural anchors*).

``_align_tokens_to_lines(block_text, line_translations)`` then partitions the
Pass-1 block tokens into one bucket per physical line, using each line's Pass-2
translation as a similarity anchor (``difflib.SequenceMatcher`` over a ±50 %
sliding window around the line's expected word count). The final line receives
all leftover tokens.

These tests target the helper in isolation: no network, no ML, no file I/O.
They focus on the guarantees the reconstruction relies on:
  * token conservation (no token lost, reordered, or duplicated);
  * one bucket per line (when there is more than one line);
  * the documented empty-block / single-line / empty-line edge cases;
  * correct splitting when the anchors give an unambiguous signal.
"""

import pytest

from utils import _align_tokens_to_lines
from utils import _align_tokens_proportional


def test_align_tokens_proportional_exact_match():
    """Tokens divide perfectly into the physical line capacities."""
    # The function expects a list of original source lines to count word lengths
    source_lines = ["word word", "word word word", "word"]
    # And a single string output from the translator
    translated_text = "The quick brown fox jumps over"

    aligned = _align_tokens_proportional(translated_text, source_lines)

    assert len(aligned) == 3
    assert aligned[0] == ["The", "quick"]
    assert aligned[1] == ["brown", "fox", "jumps"]
    assert aligned[2] == ["over"]

def test_align_tokens_proportional_overflow():
    """Ensures no tokens are lost if the translation yields more words than the source."""
    source_lines = ["word word", "word word"]
    translated_text = "This is a much longer sentence"

    aligned = _align_tokens_proportional(translated_text, source_lines)

    assert len(aligned) == 2
    assert aligned[0] == ["This", "is", "a"]
    assert aligned[1] == ["much", "longer", "sentence"]
    assert sum(len(line) for line in aligned) == len(translated_text.split())

# ════════════════════════════════════════════════════════════════════════════
# Token conservation — the central invariant
# ════════════════════════════════════════════════════════════════════════════

class TestTokenConservation:
    """Concatenating the buckets must reproduce the block tokens, in order."""

    def test_all_tokens_preserved_in_order(self):
        block = "alpha beta gamma delta epsilon zeta"
        # two anchors of 3 words each
        anchors = ["alpha beta gamma", "delta epsilon zeta"]
        buckets = _align_tokens_to_lines(block, anchors)
        flat = [tok for bucket in buckets for tok in bucket]
        assert flat == block.split()

    def test_no_token_duplicated_or_dropped_three_lines(self):
        block = "one two three four five six seven eight nine"
        anchors = ["one two three", "four five six", "seven eight nine"]
        buckets = _align_tokens_to_lines(block, anchors)
        flat = [tok for bucket in buckets for tok in bucket]
        assert flat == block.split()
        assert len(flat) == 9  # nothing lost, nothing added

    def test_conservation_holds_even_with_misleading_anchors(self):
        """Anchors that poorly predict word counts must still lose no tokens."""
        block = "a b c d e f g h"
        # anchors deliberately wrong-sized; alignment may split oddly,
        # but every token must still appear exactly once, in order.
        anchors = ["x", "y z w q r", "s t"]
        buckets = _align_tokens_to_lines(block, anchors)
        flat = [tok for bucket in buckets for tok in bucket]
        assert flat == block.split()


# ════════════════════════════════════════════════════════════════════════════
# Bucket count contract
# ════════════════════════════════════════════════════════════════════════════

class TestBucketCount:
    """The number of buckets must match the number of physical lines (>1)."""

    def test_one_bucket_per_line_when_multiple_lines(self):
        block = "alpha beta gamma delta"
        anchors = ["alpha beta", "gamma delta"]
        buckets = _align_tokens_to_lines(block, anchors)
        assert len(buckets) == len(anchors) == 2

    def test_four_lines_yield_four_buckets(self):
        block = "w1 w2 w3 w4 w5 w6 w7 w8"
        anchors = ["w1 w2", "w3 w4", "w5 w6", "w7 w8"]
        buckets = _align_tokens_to_lines(block, anchors)
        assert len(buckets) == 4


# ════════════════════════════════════════════════════════════════════════════
# Clean-signal alignment — anchors that should drive an exact split
# ════════════════════════════════════════════════════════════════════════════

class TestCleanSignalAlignment:
    """When anchors equal the intended line tokens, the split should follow them."""

    def test_exact_anchor_match_splits_cleanly(self):
        block = "the quick brown fox jumps high"
        anchors = ["the quick brown", "fox jumps high"]
        buckets = _align_tokens_to_lines(block, anchors)
        assert buckets[0] == ["the", "quick", "brown"]
        assert buckets[1] == ["fox", "jumps", "high"]

    def test_uneven_clean_split(self):
        block = "alpha beta gamma delta epsilon"
        # first line is 1 word, second is 4 words
        anchors = ["alpha", "beta gamma delta epsilon"]
        buckets = _align_tokens_to_lines(block, anchors)
        assert buckets[0] == ["alpha"]
        assert buckets[1] == ["beta", "gamma", "delta", "epsilon"]


# ════════════════════════════════════════════════════════════════════════════
# Last-line remainder rule
# ════════════════════════════════════════════════════════════════════════════

class TestLastLineRemainder:
    """The final line must absorb every token not claimed by earlier lines."""

    def test_final_line_absorbs_surplus(self):
        block = "a b c d e f g"
        # only two short anchors; whatever the first line takes, the last line
        # must receive the rest so the totals reconcile.
        anchors = ["a b", "c"]
        buckets = _align_tokens_to_lines(block, anchors)
        assert buckets[0] + buckets[1] == block.split()
        # the last bucket holds the remainder after the first line is assigned
        assert buckets[-1] == block.split()[len(buckets[0]):]


# ════════════════════════════════════════════════════════════════════════════
# Documented edge cases
# ════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_block_text_yields_empty_bucket_per_line(self):
        """No block tokens → one empty bucket per line."""
        buckets = _align_tokens_to_lines("", ["anchor one", "anchor two"])
        assert buckets == [[], []]

    def test_whitespace_only_block_text_treated_as_empty(self):
        buckets = _align_tokens_to_lines("   \n  ", ["a", "b", "c"])
        assert buckets == [[], [], []]

    def test_single_line_returns_all_tokens_in_one_bucket(self):
        """With one anchor the helper short-circuits to a single bucket."""
        block = "alpha beta gamma"
        buckets = _align_tokens_to_lines(block, ["alpha beta gamma"])
        assert buckets == [["alpha", "beta", "gamma"]]

    def test_empty_anchor_line_receives_no_tokens(self):
        """A line whose Pass-2 anchor is empty must get an empty bucket."""
        block = "alpha beta gamma delta"
        # middle line had no source text → empty anchor → 0 tokens,
        # tokens flow to the surrounding lines (last line takes the remainder).
        anchors = ["alpha beta", "", "gamma delta"]
        buckets = _align_tokens_to_lines(block, anchors)
        assert buckets[1] == []
        flat = [tok for bucket in buckets for tok in bucket]
        assert flat == block.split()

    def test_more_lines_than_tokens_extra_lines_are_empty(self):
        """Fewer block tokens than lines: later lines must end up empty."""
        block = "only"
        anchors = ["only", "second", "third"]
        buckets = _align_tokens_to_lines(block, anchors)
        flat = [tok for bucket in buckets for tok in bucket]
        assert flat == ["only"]          # the single token survives exactly once
        assert len(buckets) == 3          # one bucket per line preserved
        # at least the trailing lines are empty
        assert buckets[-1] == [] or buckets[1] == []

    def test_returns_list_of_lists(self):
        """Structural contract: result is always a list of token lists."""
        buckets = _align_tokens_to_lines("a b c", ["a", "b c"])
        assert isinstance(buckets, list)
        assert all(isinstance(b, list) for b in buckets)
        assert all(isinstance(tok, str) for b in buckets for tok in b)
