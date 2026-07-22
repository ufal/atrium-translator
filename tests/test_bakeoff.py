"""
tests/test_bakeoff.py
=====================
Unit coverage for eval/bakeoff.py — the translation-model bake-off harness
(issue #4). The live bake-off (which base model?) has never been run, so this
locks down the *plumbing* — scoring metrics + segment collection — so that when
it is finally run against real backends, a failure points at the model, not the
harness.

All pure/deterministic: no live translation backend is loaded.
"""

from eval.bakeoff import (
    char_similarity,
    collect_segments,
    length_ratio,
    number_preservation,
    terminology_hits,
)

_ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page><PrintSpace>
    <TextBlock>
      <TextLine><String CONTENT="Archeologický"/><String CONTENT="výzkum"/></TextLine>
    </TextBlock>
    <TextBlock>
      <TextLine><String CONTENT="rok"/><String CONTENT="1998"/></TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>"""


# ── metrics ──────────────────────────────────────────────────────────────────


def test_number_preservation():
    assert number_preservation("nalezeno 12 mincí z roku 1998", "found 12 coins from 1998") == 1.0
    # One of two numbers dropped -> 0.5
    assert number_preservation("12 a 34", "only 12 here") == 0.5
    # No numbers in source -> not applicable ("")
    assert number_preservation("bez cisel", "no numbers") == ""


def test_length_ratio():
    assert length_ratio("abcd", "abcdefgh") == 2.0
    assert length_ratio("", "anything") == ""  # empty source guarded


def test_terminology_hits():
    vocab = {"mohyla": "barrow", "kostel": "church"}
    # "mohyla" present in src and its target "barrow" present in tgt -> 1 hit / 1 expected
    hit, expected = terminology_hits("velka mohyla", "large barrow", vocab)
    assert (hit, expected) == (1, 1)
    # term present in src but target missing in tgt -> 0 hit / 1 expected
    hit, expected = terminology_hits("stary kostel", "old building", vocab)
    assert (hit, expected) == (0, 1)


def test_char_similarity():
    assert char_similarity("identical", "identical") == 1.0
    assert 0.0 <= char_similarity("abcdef", "abcxyz") < 1.0


# ── segment collection ───────────────────────────────────────────────────────


def test_collect_segments_reads_alto_blocks(tmp_path):
    (tmp_path / "doc.alto.xml").write_text(_ALTO, encoding="utf-8")
    segments = collect_segments(tmp_path, xpaths=[], limit=None)
    assert [s["kind"] for s in segments] == ["alto", "alto"]
    assert segments[0]["src"] == "Archeologický výzkum"
    assert segments[1]["src"] == "rok 1998"


def test_collect_segments_respects_limit(tmp_path):
    (tmp_path / "doc.alto.xml").write_text(_ALTO, encoding="utf-8")
    segments = collect_segments(tmp_path, xpaths=[], limit=1)
    assert len(segments) == 1


def test_collect_segments_survives_malformed_xml(tmp_path):
    (tmp_path / "good.alto.xml").write_text(_ALTO, encoding="utf-8")
    (tmp_path / "bad.alto.xml").write_text("<alto><unclosed>", encoding="utf-8")
    # A malformed sample must not abort the run (it is logged and skipped).
    segments = collect_segments(tmp_path, xpaths=[], limit=None)
    assert len(segments) == 2
    assert all(s["file"] == "good.alto.xml" for s in segments)
