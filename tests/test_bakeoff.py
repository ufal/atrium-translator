"""
tests/test_bakeoff.py
=====================
Unit coverage for eval/bakeoff.py — the translation-model bake-off harness
(issue #4). The live bake-off (which base model?) has never been run, so this
locks down the *plumbing* — scoring metrics + segment collection — so that when
it is finally run against real backends, a failure points at the model, not the
harness.

All deterministic: the end-to-end ``run()`` tests swap ``get_backend`` for
in-process fakes, and COMET / sacrebleu for stand-in modules, so no network,
model or GPU is touched.
"""

import csv
import json
import sys
import types

import pytest

from eval import bakeoff
from eval.bakeoff import (
    SUMMARY_COLUMNS,
    char_similarity,
    collect_segments,
    length_ratio,
    number_preservation,
    summarise,
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


# ── summarise ────────────────────────────────────────────────────────────────


def _row(backend, tgt, *, failed=0, np="", lr="", hits=0, exp=0, sim="", ref=None, src="src text"):
    row = {
        "backend": backend,
        "failed": failed,
        "tgt": tgt,
        "src": src,
        "number_preservation": np,
        "length_ratio": lr,
        "term_hits": hits,
        "term_expected": exp,
        "baseline_char_sim": sim,
    }
    if ref is not None:
        row["ref"] = ref
    return row


def test_summarise_counts_failed_apart_from_empty():
    rows = [
        _row("a", "one", np=1.0, lr=1.0, hits=1, exp=1),
        _row("a", "", lr=0.0),  # answered blank -> empty
        _row("a", "", failed=1),  # raised -> failed, not empty
        _row("a", "four", np=0.5, lr=2.0, hits=0, exp=1),
    ]
    (s,) = summarise(rows, ["a"])
    assert (s["segments"], s["failed"], s["empty"]) == (4, 1, 1)
    assert s["empty_rate"] == 0.25
    assert (s["term_hits"], s["term_expected"], s["term_hit_rate"]) == (1, 2, 0.5)
    # means over the answered rows that have a value; the failed row is left out
    assert s["number_preservation"] == 0.75
    assert s["length_ratio"] == 1.0
    assert s["baseline"] == "a" and s["baseline_char_sim"] == ""


def test_summarise_similarity_to_baseline_for_three_backends():
    rows = [
        _row("base", "x"),
        _row("b", "x", sim=0.8),
        _row("c", "x", sim=0.2),
        _row("base", "y"),
        _row("b", "y", sim=0.6),
        _row("c", "y", sim=0.4),
    ]
    by = {s["backend"]: s for s in summarise(rows, ["base", "b", "c"])}
    assert list(by) == ["base", "b", "c"]
    assert by["base"]["baseline_char_sim"] == ""
    assert by["b"]["baseline_char_sim"] == 0.7
    assert by["c"]["baseline_char_sim"] == 0.3
    assert {s["baseline"] for s in by.values()} == {"base"}


def test_summarise_blank_when_nothing_to_measure():
    (s,) = summarise([_row("a", "text")], ["a"])
    for col in ("term_hit_rate", "number_preservation", "length_ratio", "chrF", "BLEU", "COMET", "COMET_QE"):
        assert s[col] == "", col
    assert s["refs_used"] == 0


class _FakeSacre:
    """Records the corpus_* calls; returns a fixed score."""

    def __init__(self):
        self.calls = []

    def _score(self, name, value):
        def fn(hyps, refs):
            self.calls.append((name, list(hyps), [list(r) for r in refs]))
            return types.SimpleNamespace(score=value)

        return fn

    def __getattr__(self, name):
        return self._score(name, {"corpus_chrf": 55.554, "corpus_bleu": 33.336}.get(name, 1.0))


def test_summarise_corpus_scores_use_every_referenced_segment_failed_included():
    sacre = _FakeSacre()
    rows = [
        _row("a", "hyp one", ref="ref one"),
        _row("a", "", failed=1, ref="ref two"),  # scored as an empty hypothesis
        _row("a", "no ref"),
    ]
    (s,) = summarise(rows, ["a"], sacre=sacre)
    assert s["refs_used"] == 2
    assert (s["chrF"], s["BLEU"]) == (55.55, 33.34)
    assert ("corpus_chrf", ["hyp one", ""], [["ref one", "ref two"]]) in sacre.calls


def test_summarise_takes_comet_system_scores():
    comet_system = {"COMET": {"a": 0.812345}, "COMET_QE": {"a": 0.7}}
    (s,) = summarise([_row("a", "x")], ["a"], comet_system=comet_system)
    assert (s["COMET"], s["COMET_QE"]) == (0.8123, 0.7)


# ── run() end to end ─────────────────────────────────────────────────────────


class _Upper:
    name = "upper"
    supports_glossary = False

    def __init__(self, **kwargs):
        pass

    def translate(self, text, src, tgt="en"):
        return text.upper()


class _Echo(_Upper):
    name = "echo"

    def translate(self, text, src, tgt="en"):
        return text


class _Flaky(_Upper):
    name = "flaky"

    def translate(self, text, src, tgt="en"):
        if "1998" in text:
            raise RuntimeError("backend down")
        return ""


_FAKES = {"upper": _Upper, "echo": _Echo, "flaky": _Flaky}


@pytest.fixture
def samples(tmp_path, monkeypatch):
    (tmp_path / "doc.alto.xml").write_text(_ALTO, encoding="utf-8")
    monkeypatch.setattr(bakeoff, "get_backend", lambda name, **kw: _FAKES[name](**kw))
    return tmp_path


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _argv(samples, tmp_path, *extra):
    return [
        "--samples",
        str(samples),
        "--xpaths",
        "",
        "--out",
        str(tmp_path / "bake.csv"),
        *extra,
    ]


def test_run_writes_per_segment_and_summary_csvs(samples, tmp_path):
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo,upper,flaky"))

    rows = _read(tmp_path / "bake.csv")
    assert len(rows) == 6  # 2 segments x 3 backends
    summary = _read(tmp_path / "bake_summary.csv")
    assert list(summary[0]) == list(SUMMARY_COLUMNS)
    by = {s["backend"]: s for s in summary}
    assert list(by) == ["echo", "upper", "flaky"]

    # flaky: segment 2 raised (failed), segment 1 came back blank (empty)
    assert (by["flaky"]["failed"], by["flaky"]["empty"]) == ("1", "1")
    assert (by["echo"]["failed"], by["echo"]["empty"]) == ("0", "0")
    failed_row = next(r for r in rows if r["backend"] == "flaky" and r["failed"] == "1")
    assert failed_row["error"] == "backend down"
    # nothing was produced, so nothing is measured: blank, never a fake 0.0
    assert failed_row["length_ratio"] == "" and failed_row["number_preservation"] == ""

    # divergence from the baseline (echo, the first backend), for all three
    assert by["echo"]["baseline_char_sim"] == ""
    assert 0.0 < float(by["upper"]["baseline_char_sim"]) < 1.0
    assert float(by["flaky"]["baseline_char_sim"]) == 0.0  # only its blank answer counts
    upper_rows = [r for r in rows if r["backend"] == "upper"]
    assert all(r["baseline_char_sim"] for r in upper_rows)


def test_run_summary_out_overrides_the_default_path(samples, tmp_path):
    target = tmp_path / "elsewhere" / "sum.csv"
    target.parent.mkdir()
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--summary-out", str(target)))
    assert target.exists()
    assert not (tmp_path / "bake_summary.csv").exists()


def test_default_summary_path():
    assert bakeoff._default_summary_path("out/bakeoff.csv").as_posix() == "out/bakeoff_summary.csv"
    assert bakeoff._default_summary_path("bakeoff").as_posix() == "bakeoff_summary.csv"


def test_run_scores_terminology_with_a_vocabulary(samples, tmp_path):
    vocab = tmp_path / "v.csv"
    vocab.write_text("source_lemma,target_translation\nvýzkum,survey\n", encoding="utf-8")
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--vocabulary", str(vocab)))
    (s,) = _read(tmp_path / "bake_summary.csv")
    assert (s["term_hits"], s["term_expected"], s["term_hit_rate"]) == ("0", "1", "0.0")


# ── COMET / COMET-QE plumbing ────────────────────────────────────────────────


def _fake_comet(record):
    mod = types.ModuleType("comet")

    def download_model(name):
        record.setdefault("downloaded", []).append(name)
        return f"/ckpt/{name}"

    def load_from_checkpoint(path):
        class _Model:
            def predict(self, data, batch_size=8, gpus=0):
                record.setdefault("predict", []).append({"path": path, "data": data, "gpus": gpus})
                scores = [0.5 + 0.1 * i for i in range(len(data))]
                return types.SimpleNamespace(scores=scores, system_score=sum(scores) / len(scores))

        return _Model()

    mod.download_model = download_model
    mod.load_from_checkpoint = load_from_checkpoint
    return mod


@pytest.fixture
def refs_tsv(tmp_path):
    p = tmp_path / "refs.tsv"
    p.write_text("doc.alto.xml:block0\tArchaeological survey\ndoc.alto.xml:block1\tyear 1998\n", encoding="utf-8")
    return p


def test_reference_comet_runs_when_refs_are_given(samples, tmp_path, refs_tsv, monkeypatch):
    record = {}
    monkeypatch.setitem(sys.modules, "comet", _fake_comet(record))
    monkeypatch.setattr(bakeoff, "_load_sacrebleu", lambda: None)

    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo,upper", "--refs", str(refs_tsv)))

    assert record["downloaded"] == ["Unbabel/wmt22-comet-da", "Unbabel/wmt22-comet-da"]
    first = record["predict"][0]
    assert first["gpus"] == 0
    assert all(set(item) == {"src", "mt", "ref"} for item in first["data"])
    by = {s["backend"]: s for s in _read(tmp_path / "bake_summary.csv")}
    assert by["echo"]["COMET"] == "0.55" and by["echo"]["COMET_QE"] == ""
    assert by["echo"]["refs_used"] == "2"
    rows = _read(tmp_path / "bake.csv")
    assert [r["COMET"] for r in rows if r["backend"] == "echo"] == ["0.5", "0.6"]


def test_comet_qe_is_opt_in_and_reference_free(samples, tmp_path, monkeypatch):
    record = {}
    monkeypatch.setitem(sys.modules, "comet", _fake_comet(record))

    # no --refs: reference COMET must not run
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo"))
    assert "predict" not in record

    bakeoff.main(
        _argv(
            samples,
            tmp_path,
            "--backends",
            "echo",
            "--comet-qe-model",
            "Unbabel/wmt22-cometkiwi-da",
            "--comet-gpus",
            "1",
        )
    )
    (call,) = record["predict"]
    assert record["downloaded"] == ["Unbabel/wmt22-cometkiwi-da"]
    assert call["gpus"] == 1
    assert all(set(item) == {"src", "mt"} for item in call["data"])
    (s,) = _read(tmp_path / "bake_summary.csv")
    assert s["COMET_QE"] == "0.55" and s["COMET"] == ""


def test_empty_comet_model_disables_reference_comet(samples, tmp_path, refs_tsv, monkeypatch):
    record = {}
    monkeypatch.setitem(sys.modules, "comet", _fake_comet(record))
    monkeypatch.setattr(bakeoff, "_load_sacrebleu", lambda: None)
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--refs", str(refs_tsv), "--comet-model", ""))
    assert "predict" not in record


def test_missing_comet_package_degrades_to_a_warning(samples, tmp_path, refs_tsv, monkeypatch, capsys):
    monkeypatch.setattr(bakeoff, "_load_comet", lambda: None)
    monkeypatch.setattr(bakeoff, "_load_sacrebleu", lambda: None)
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--refs", str(refs_tsv)))
    assert "unbabel-comet not installed" in capsys.readouterr().out
    (s,) = _read(tmp_path / "bake_summary.csv")
    assert s["COMET"] == ""


def test_comet_model_failure_is_not_fatal(samples, tmp_path, refs_tsv, monkeypatch, capsys):
    broken = types.ModuleType("comet")

    def download_model(name):
        raise OSError("gated model: accept the licence first")

    broken.download_model = download_model
    broken.load_from_checkpoint = lambda path: None
    monkeypatch.setitem(sys.modules, "comet", broken)
    monkeypatch.setattr(bakeoff, "_load_sacrebleu", lambda: None)

    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--refs", str(refs_tsv)))

    assert "COMET (Unbabel/wmt22-comet-da) failed for backend 'echo'" in capsys.readouterr().out
    assert (tmp_path / "bake_summary.csv").exists()


# ── a real run on the cluster: one variant per run, then --combine (issue #4) ─


_AMCR = """<?xml version="1.0" encoding="UTF-8"?>
<amcr:amcr xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/">
  <amcr:popis>Výzkum v roce 1998 odkryl kostel.</amcr:popis>
</amcr:amcr>"""


def _with_metadata(samples):
    (samples / "rec.xml").write_text(_AMCR, encoding="utf-8")
    xpaths = samples / "fields.txt"
    xpaths.write_text("//amcr:popis\n", encoding="utf-8")
    return xpaths


def test_terminology_counts_whole_words_only():
    # "kost" is not expected inside "kostel", as in the backends' own matching
    assert terminology_hits("starý kostel", "old church", {"kost": "bone", "kostel": "church"}) == (1, 1)


def test_auto_source_is_refused(samples, tmp_path):
    with pytest.raises(SystemExit):
        bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--source_lang", "auto"))


def test_label_names_one_variant(samples, tmp_path):
    bakeoff.main(_argv(samples, tmp_path, "--backends", "upper", "--label", "upper-v2"))
    assert {r["backend"] for r in _read(tmp_path / "bake.csv")} == {"upper-v2"}
    with pytest.raises(SystemExit):
        bakeoff.main(_argv(samples, tmp_path, "--backends", "echo,upper", "--label", "x"))


def test_rows_carry_seconds_and_the_summary_the_speed(samples, tmp_path):
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo"))
    rows = _read(tmp_path / "bake.csv")
    assert all(float(r["seconds"]) >= 0.0 for r in rows)
    (s,) = _read(tmp_path / "bake_summary.csv")
    assert s["seconds"] != "" and list(s)[-2:] == ["seconds", "chars_per_s"]


def test_by_kind_summary_keeps_metadata_and_alto_apart(samples, tmp_path):
    xpaths = _with_metadata(samples)
    argv = _argv(samples, tmp_path, "--backends", "echo")
    argv[argv.index("--xpaths") + 1] = str(xpaths)
    bakeoff.main(argv)
    by_kind = _read(tmp_path / "bake_by_kind.csv")
    assert [(r["kind"], r["backend"], r["segments"]) for r in by_kind] == [
        ("metadata", "echo", "1"),
        ("alto", "echo", "2"),
    ]


def test_kinds_selects_what_is_collected(samples, tmp_path):
    xpaths = _with_metadata(samples)
    argv = _argv(samples, tmp_path, "--backends", "echo", "--kinds", "metadata")
    argv[argv.index("--xpaths") + 1] = str(xpaths)
    bakeoff.main(argv)
    assert {r["kind"] for r in _read(tmp_path / "bake.csv")} == {"metadata"}
    with pytest.raises(SystemExit):
        bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--kinds", "pdf"))


def test_meta_sidecar_says_what_ran(samples, tmp_path):
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo", "--label", "echo-1"))
    meta = json.loads((tmp_path / "bake.meta.json").read_text(encoding="utf-8"))
    assert meta["variants"] == {"echo-1": {"backend": "echo"}}
    assert meta["source_lang"] == "cs" and meta["segments"] == 2


def test_combine_merges_variants_and_recomputes_the_baseline(samples, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    for name in ("echo", "upper"):
        argv = _argv(samples, tmp_path, "--backends", name, "--label", f"{name}-run")
        argv[argv.index("--out") + 1] = str(runs / f"{name}.csv")
        bakeoff.main(argv)
    out = tmp_path / "combined.csv"
    bakeoff.main(["--combine", str(runs / "echo.csv"), str(runs / "upper.csv"), "--out", str(out)])

    by = {s["backend"]: s for s in _read(tmp_path / "combined_summary.csv")}
    assert list(by) == ["echo-run", "upper-run"]
    assert by["echo-run"]["baseline"] == "echo-run" and by["echo-run"]["baseline_char_sim"] == ""
    assert 0.0 < float(by["upper-run"]["baseline_char_sim"]) < 1.0
    assert (tmp_path / "combined_by_kind.csv").exists()
    meta = json.loads((tmp_path / "combined.meta.json").read_text(encoding="utf-8"))
    assert len(meta["runs"]) == 2


def test_combine_refuses_a_variant_named_twice(samples, tmp_path):
    bakeoff.main(_argv(samples, tmp_path, "--backends", "echo"))
    with pytest.raises(SystemExit):
        bakeoff.main(
            ["--combine", str(tmp_path / "bake.csv"), str(tmp_path / "bake.csv"), "--out", str(tmp_path / "c.csv")]
        )
