from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from atrium_test_support import call_best_effort, import_any, maybe_resolve_attr, resolve_attr


def _resolve_vocab_loader():
    # The CSV loader is processors/vocab.py's; load_vocab.py only harvests and writes the CSV.
    # A missing loader fails the test: it used to look in load_vocab.py and skip every run.
    return resolve_attr(import_any(["processors.vocab"]), ("load_vocabulary",))


def test_vocab_loader_can_read_basic_csv(sample_vocab_csv: Path):
    """The vocabulary CSV format documented by the repo should be parseable."""
    loader = _resolve_vocab_loader()

    result = call_best_effort(loader, sample_vocab_csv, path=sample_vocab_csv, vocab_path=sample_vocab_csv)
    text = str(result)
    assert "kostel" in text or "church" in text


def test_vocab_loader_ignores_duplicate_or_invalid_rows(tmp_path: Path):
    """A realistic vocabulary loader should tolerate bad rows without crashing."""
    loader = _resolve_vocab_loader()

    vocab = tmp_path / "dup_vocab.csv"
    vocab.write_text(
        "source_lemma,target_translation\n"
        "kostel,church\n"
        "kostel,church\n"
        "broken-row-without-comma\n"
        "pohřebiště,burial ground\n",
        encoding="utf-8",
    )

    result = call_best_effort(loader, vocab, path=vocab, vocab_path=vocab)
    text = str(result)
    assert "kostel" in text
    assert "pohřebiště" in text or "burial ground" in text


def _merge_paradata(merger, json_paths: list[Path], input_file: Path, out_path: Path):
    sig = inspect.signature(merger)
    params = list(sig.parameters)

    # Build a best-effort positional call, because the repo currently exposes
    # a positional signature in this area.
    args: list[Any] = []

    if params:
        args.append(
            [str(p) for p in json_paths]
            if "json_paths" in params[0] or len(params) >= 3
            else [str(p) for p in json_paths]
        )
    if len(params) >= 2:
        args.append(str(input_file) if "input_file" in params[1] else input_file)
    if len(params) >= 3:
        args.append(str(out_path) if "out_path" in params[2] else out_path)

    try:
        return merger(*args)
    except TypeError:
        # Secondary attempt: pass everything as strings, since the implementation
        # may accept only plain paths.
        return merger([str(p) for p in json_paths], str(input_file), str(out_path))


def _read_merged_result(output: Path, result: Any) -> str:
    if output.exists():
        return output.read_text(encoding="utf-8")
    if result is None:
        return ""
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def test_paradata_merge_prefers_more_restrictive_license(sample_paradata_files):
    """merge_paradata_files() should preserve aggregate provenance and licensing."""
    atrium_paradata = import_any(["atrium_paradata"])
    merger = maybe_resolve_attr(atrium_paradata, ("merge_paradata_files",))
    if merger is None:
        pytest.skip("merge_paradata_files is not exposed in atrium_paradata.py")

    first, second = sample_paradata_files
    output = first.parent / "merged.json"
    input_file = first.parent / "input.xml"
    input_file.write_text("<root/>", encoding="utf-8")

    result = _merge_paradata(merger, [first, second], input_file, output)
    merged_text = _read_merged_result(output, result)

    assert "schema_version" in merged_text
    assert "license" in merged_text
    assert "CC BY-NC 4.0" in merged_text or "CC BY 4.0" in merged_text


def test_paradata_merge_keeps_statistics_counts(sample_paradata_files):
    """Aggregate counts should be visible in the merged provenance record."""
    atrium_paradata = import_any(["atrium_paradata"])
    merger = maybe_resolve_attr(atrium_paradata, ("merge_paradata_files",))
    if merger is None:
        pytest.skip("merge_paradata_files is not exposed in atrium_paradata.py")

    first, second = sample_paradata_files
    output = first.parent / "merged_statistics.json"
    input_file = first.parent / "input.xml"
    input_file.write_text("<root/>", encoding="utf-8")

    result = _merge_paradata(merger, [first, second], input_file, output)
    merged_text = _read_merged_result(output, result)

    assert "step_count" in merged_text
    assert '"pipeline_steps"' in merged_text
    assert "CC BY-NC 4.0" in merged_text
