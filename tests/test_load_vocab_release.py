"""
tests/test_load_vocab_release.py
================================
``load_vocab.py --from-release`` (atrium-project#72, #51): the translator's vocabulary CSV built
from the CC0 vocabulary atrium-keyword-extract attaches to each release, instead of a harvest of
the live AMČR and TEATER APIs.  The pairs are the asset's curated ``vocabulary.csv`` (one list
for the project, atrium-project#72 of 13 October), with the identity of the record that won each
pair and the concept's description (translator#4).

The asset is built here the way keyword-extract's ``release.yml`` packs it (a zip of the flat
harvests, the curated glossary, the nested union and the flat JSON harvests, and a ``.sha256``
file beside it), so nothing here needs the network or the other repository.
"""

from __future__ import annotations

import csv
import hashlib
import json
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
    # excluded by keyword-extract, so the TEATER record wins the pair in the nested union
    ["papír", "paper", "amcr", "HES-000500", "https://api.aiscr.cz/id/HES-000500", "materiál", "", "", "3"],
]
TEATER = [
    ["archeologie", "archaeology", "teater", "2", "https://teater.aiscr.cz/id/2", "1", "", "1", ""],
    ["kostel", "church building", "teater", "1333", "https://teater.aiscr.cz/id/1333", "1", "", "", ""],
    ["papír", "paper", "teater", "900", "https://teater.aiscr.cz/id/900", "9", "", "", ""],
    # harvested, but not in the curated list
    ["hrob", "grave", "teater", "77", "https://teater.aiscr.cz/id/77", "7", "", "", ""],
]
#: keyword-extract's curated glossary: "novotvar" is in no harvest, "hrob" was excluded.
CURATED = [
    ("obývání", "residential activity"),
    ("kostel", "church"),
    ("papír", "paper"),
    ("archeologie", "archaeology"),
    ("novotvar", "neologism"),
]
#: The nested union names each winner; "archeologie" stands for the TEATER headings it does not list.
NESTED = {
    "Activity Area": {
        "obývání": {"cs": "obývání", "en": "residential activity", "source": "amcr", "source_id": "HES-000001"}
    },
    "Feature": {"kostel": {"cs": "kostel", "en": "church", "source": "amcr", "source_id": "HES-000021"}},
    "Material": {"papír": {"cs": "papír", "en": "paper", "source": "teater", "source_id": "900"}},
}
LONG_NOTE = "Materiál z rostlinných vláken, " * 40
FLAT_JSON = {
    "amcr": [
        {
            "cs": "kostel",
            "source_id": "HES-000021",
            "note_en": "A building for  Christian\nworship.",
            "note_cs": "Stavba.",
        },
        {"cs": "papír", "source_id": "HES-000500", "note_en": "the excluded record's note"},
    ],
    "teater": [
        {"cs": "papír", "source_id": "900", "note_en": None, "note_cs": LONG_NOTE},
        {"cs": "archeologie", "source_id": "2", "note_en": "The study of the human past."},
    ],
}


def _flat(rows):
    return "\n".join(",".join(r) for r in [FLAT_HEADER, *rows]) + "\n"


def _curated(pairs):
    return "source_lemma,target_translation\n" + "".join(f"{lemma},{target}\n" for lemma, target in pairs)


def _asset(tmp_path, version="v9.9.9-beta", *, teater=True, sidecar=True, curated=True, nested=True, flat_json=True):
    path = tmp_path / f"atrium-vocabulary-{version}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("README.txt", "ATRIUM vocabulary\n")
        archive.writestr("amcr_flat.csv", _flat(AMCR))
        if teater:
            archive.writestr("teater_flat.csv", _flat(TEATER))
        if curated:
            archive.writestr("vocabulary.csv", _curated(CURATED))
        if nested:
            archive.writestr("union_nested.json", json.dumps(NESTED, ensure_ascii=False))
        if flat_json:
            for source, terms in FLAT_JSON.items():
                archive.writestr(f"{source}_flat.json", json.dumps({"_meta": {}, "terms": terms}, ensure_ascii=False))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if sidecar:
        (tmp_path / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return path, digest


def _rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def _build(tmp_path, *extra, **asset_kwargs):
    asset, _ = _asset(tmp_path, **asset_kwargs)
    out = tmp_path / "vocabulary.csv"
    code = load_vocab.main(["--from-release", "v9.9.9-beta", "--asset", str(asset), "--out", str(out), *extra])
    return code, out


def _by_lemma(out):
    rows = _rows(out)
    assert rows[0] == list(load_vocab.CSV_COLUMNS)
    return {row[0]: row for row in rows[1:]}


def test_the_release_asset_becomes_the_translators_csv(tmp_path):
    code, out = _build(tmp_path)
    assert code == 0
    by_lemma = _by_lemma(out)
    assert by_lemma["kostel"] == [
        "kostel",
        "church",
        "amcr",
        "HES-000021",
        "https://api.aiscr.cz/id/HES-000021",
        "A building for Christian worship.",
    ]
    assert by_lemma["obývání"][2:5] == ["amcr", "HES-000001", "https://api.aiscr.cz/id/HES-000001"]


def test_only_curated_pairs_are_kept(tmp_path):
    code, out = _build(tmp_path)
    assert code == 0
    # "hrob" was harvested but excluded by keyword-extract; "kostel" keeps the curated target only
    assert sorted(_by_lemma(out)) == ["archeologie", "kostel", "novotvar", "obývání", "papír"]
    assert _by_lemma(out)["kostel"][1] == "church"


def test_provenance_follows_the_nested_winner(tmp_path):
    # AMČR comes first in the flats, but keyword-extract excluded its record: the nested union names TEATER's
    code, out = _build(tmp_path)
    assert code == 0
    assert _by_lemma(out)["papír"][2:5] == ["teater", "900", "https://teater.aiscr.cz/id/900"]


def test_a_pair_the_nested_union_lacks_takes_its_first_flat_match(tmp_path):
    code, out = _build(tmp_path)
    assert code == 0
    assert _by_lemma(out)["archeologie"][2:] == [
        "teater",
        "2",
        "https://teater.aiscr.cz/id/2",
        "The study of the human past.",
    ]


def test_a_curated_pair_missing_from_the_harvests_is_kept_and_reported(tmp_path, capsys):
    code, out = _build(tmp_path)
    assert code == 0
    assert _by_lemma(out)["novotvar"] == ["novotvar", "neologism", "", "", "", ""]
    assert "1 curated pair(s) are in no harvest" in capsys.readouterr().out


def test_descriptions_prefer_note_en_and_are_capped(tmp_path):
    code, out = _build(tmp_path, "--description-chars", "40")
    assert code == 0
    by_lemma = _by_lemma(out)
    papir = by_lemma["papír"][5]
    assert papir.endswith("…") and len(papir) <= 40 and papir.startswith("Materiál z rostlinných")
    # note_en wins over note_cs, on one line; a short one is not cut
    assert by_lemma["kostel"][5] == "A building for Christian worship."


def test_zero_description_chars_writes_none(tmp_path):
    code, out = _build(tmp_path, "--description-chars", "0")
    assert code == 0
    assert {row[5] for row in _by_lemma(out).values()} == {""}


def test_an_asset_without_the_nested_union_still_builds(tmp_path, capsys):
    code, out = _build(tmp_path, nested=False, flat_json=False)
    assert code == 0
    # without the winners, papír takes its first flat match (AMČR), and there are no descriptions
    assert _by_lemma(out)["papír"][2:] == ["amcr", "HES-000500", "https://api.aiscr.cz/id/HES-000500", ""]
    assert "falls back to the flat harvests" in capsys.readouterr().out


def test_an_asset_without_the_curated_list_is_refused(tmp_path, capsys):
    code, out = _build(tmp_path, curated=False)
    assert code == load_vocab.EXIT_CHECKSUM and not out.exists()
    assert "vocabulary.csv" in capsys.readouterr().err


def test_a_negative_description_cap_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit):
        _build(tmp_path, "--description-chars", "-1")


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
    assert fetched == list(files) and len(_rows(out)) == 1 + len(CURATED)


def test_asset_and_sha256_need_from_release(tmp_path):
    with pytest.raises(SystemExit):
        load_vocab.main(["--asset", str(tmp_path / "x.zip")])
    with pytest.raises(SystemExit):
        load_vocab.main(["--from-release", "v1", "--skip-amcr"])
