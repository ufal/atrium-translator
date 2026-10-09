"""
tests/test_load_vocab_release.py
================================
``load_vocab.py --from-release`` (atrium-project#72, #51): the translator's vocabulary CSV built
from the CC0 vocabulary atrium-keyword-extract attaches to each release, instead of a harvest of
the live AMČR and TEATER APIs.

The asset is built here the way keyword-extract's ``release.yml`` packs it (a zip of the flat
harvests, whose first five columns are this repository's CSV columns, and a ``.sha256`` file
beside it), so nothing here needs the network or the other repository.
"""

from __future__ import annotations

import csv
import hashlib
import zipfile

import pytest

import load_vocab

FLAT_HEADER = ["source_lemma", "target_translation", "source", "source_id", "uri", "scheme", "sub", "broader", "sort"]
AMCR = [
    [
        "obývání",
        "residential activity",
        "amcr",
        "HES-000001",
        "https://api.aiscr.cz/id/HES-000001",
        "aktivita",
        "",
        "",
        "1",
    ],
    ["kostel", "church", "amcr", "HES-000021", "https://api.aiscr.cz/id/HES-000021", "objekt", "", "", "2"],
]
TEATER = [
    ["archeologie", "archaeology", "teater", "2", "https://teater.aiscr.cz/id/2", "1", "", "1", ""],
    ["kostel", "church building", "teater", "1333", "https://teater.aiscr.cz/id/1333", "1", "", "", ""],
]


def _flat(rows):
    return "\n".join(",".join(r) for r in [FLAT_HEADER, *rows]) + "\n"


def _asset(tmp_path, version="v9.9.9-beta", *, teater=True, sidecar=True):
    path = tmp_path / f"atrium-vocabulary-{version}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("README.txt", "ATRIUM vocabulary\n")
        archive.writestr("amcr_flat.csv", _flat(AMCR))
        if teater:
            archive.writestr("teater_flat.csv", _flat(TEATER))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if sidecar:
        (tmp_path / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return path, digest


def _rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def test_the_release_asset_becomes_the_translators_csv(tmp_path):
    asset, _ = _asset(tmp_path)
    out = tmp_path / "vocabulary.csv"
    assert load_vocab.main(["--from-release", "v9.9.9-beta", "--asset", str(asset), "--out", str(out)]) == 0
    rows = _rows(out)
    assert rows[0] == list(load_vocab.CSV_COLUMNS)
    by_lemma = {row[0]: row for row in rows[1:]}
    assert sorted(by_lemma) == ["archeologie", "kostel", "obývání"]
    # AMČR wins a collision, exactly as in a harvest (merge_records)
    assert by_lemma["kostel"] == ["kostel", "church", "amcr", "HES-000021", "https://api.aiscr.cz/id/HES-000021"]
    assert by_lemma["archeologie"][2:] == ["teater", "2", "https://teater.aiscr.cz/id/2"]


def test_a_pinned_digest_is_used_and_a_wrong_one_refused(tmp_path, capsys):
    asset, digest = _asset(tmp_path, sidecar=False)
    out = tmp_path / "vocabulary.csv"
    args = ["--from-release", "v9.9.9-beta", "--asset", str(asset), "--out", str(out)]
    assert load_vocab.main([*args, "--sha256", digest]) == 0
    out.unlink()
    assert load_vocab.main([*args, "--sha256", "0" * 64]) == load_vocab.EXIT_CHECKSUM
    assert "refusing it" in capsys.readouterr().err and not out.exists()


def test_an_unverifiable_asset_is_refused(tmp_path, capsys):
    asset, _ = _asset(tmp_path, sidecar=False)
    out = tmp_path / "vocabulary.csv"
    assert load_vocab.main(["--from-release", "v9.9.9-beta", "--asset", str(asset), "--out", str(out)]) == 3
    assert "no checksum" in capsys.readouterr().err and not out.exists()


def test_an_asset_without_both_harvests_is_refused(tmp_path, capsys):
    asset, _ = _asset(tmp_path, teater=False)
    assert load_vocab.main(["--from-release", "v9", "--asset", str(asset), "--out", str(tmp_path / "o.csv")]) == 3
    assert "teater_flat.csv" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["v9.9.9-beta", "9.9.9-beta"])
def test_the_release_is_fetched_from_keyword_extract(tmp_path, monkeypatch, version):
    asset, _ = _asset(tmp_path)
    files = {
        "https://github.com/ufal/atrium-keyword-extract/releases/download/v9.9.9-beta/atrium-vocabulary-v9.9.9-beta.zip": asset,
    }
    files[next(iter(files)) + ".sha256"] = tmp_path / f"{asset.name}.sha256"
    fetched = []

    class _Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def get(url, timeout):
        fetched.append(url)
        return _Response(files[url].read_bytes())

    monkeypatch.setattr(load_vocab.requests, "get", get)
    out = tmp_path / "vocabulary.csv"
    assert load_vocab.main(["--from-release", version, "--out", str(out)]) == 0
    assert fetched == list(files) and len(_rows(out)) == 4


def test_asset_and_sha256_need_from_release(tmp_path):
    with pytest.raises(SystemExit):
        load_vocab.main(["--asset", str(tmp_path / "x.zip")])
    with pytest.raises(SystemExit):
        load_vocab.main(["--from-release", "v1", "--skip-amcr"])
