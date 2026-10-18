import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import load_vocab


def test_pick_field_resolves_correctly():
    fields = ["identifier", "name_cs", "url", "term"]
    assert load_vocab._pick_field(fields, "id") == "identifier"
    assert load_vocab._pick_field(fields, "name") == "name_cs"
    assert load_vocab._pick_field(fields, "missing") is None


def test_extract_label_handles_structures():
    # Direct language key
    assert load_vocab._extract_label({"cs": " direct_val "}, "cs") == "direct_val"

    # Suffix matching
    assert load_vocab._extract_label({"name_cze": "cze_val"}, "cs") == "cze_val"

    # Nested GraphQL-style list
    nested_item = {"labels": [{"lang": "cs", "value": " nested_val "}]}
    assert load_vocab._extract_label(nested_item, "cs") == "nested_val"

    # Missing or empty
    assert load_vocab._extract_label({}, "en") == ""


@patch("load_vocab.requests.get")
def test_harvest_amcr_yields_term_pairs(mock_get):
    mock_response = MagicMock()
    mock_response.content = b"""<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <ListRecords>
        <record>
          <metadata>
             <amcr:heslo xmlns:amcr="https://api.aiscr.cz/schema/amcr/2.2/">
                <amcr:heslo xml:lang="cs" xmlns:xml="http://www.w3.org/XML/1998/namespace">Keramika</amcr:heslo>
                <amcr:heslo_en>Ceramics</amcr:heslo_en>
             </amcr:heslo>
          </metadata>
        </record>
      </ListRecords>
    </OAI-PMH>
    """
    mock_get.return_value = mock_response

    vocab = load_vocab.harvest_amcr(delay=0)
    assert vocab == {"keramika": "Ceramics"}
    mock_get.assert_called_once()


@patch("load_vocab.requests.get")
def test_harvest_amcr_handles_network_failure(mock_get):
    mock_get.side_effect = requests.RequestException("Timeout")
    vocab = load_vocab.harvest_amcr(delay=0)
    assert vocab == {}


@patch("load_vocab.requests.Session.post")
def test_gql_valid_request(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"exportAll": "url"}}
    mock_post.return_value = mock_resp

    res = load_vocab._gql(requests.Session(), "{ exportAll }")
    assert res == {"exportAll": "url"}


@patch("load_vocab.requests.Session.post")
def test_gql_raises_on_errors(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"errors": [{"message": "Invalid query"}]}
    mock_post.return_value = mock_resp

    with pytest.raises(RuntimeError, match="GraphQL errors"):
        load_vocab._gql(requests.Session(), "{ exportAll }")


@patch("load_vocab._gql")
def test_harvest_via_search_aggregates_terms(mock_gql):
    def side_effect(session, query, variables=None):
        if variables and variables.get("lang") == "CS":
            return {"search": [{"id": "123", "name": "Keramika"}]}
        elif variables and variables.get("lang") == "EN":
            return {"search": [{"id": "123", "name": "Ceramics"}]}
        return {"search": []}

    mock_gql.side_effect = side_effect
    search_field = {"args": [{"name": "language"}, {"name": "limit"}]}
    all_types = [{"name": "ItemType", "fields": [{"name": "id"}, {"name": "name"}]}]

    vocab = load_vocab._harvest_via_search(requests.Session(), search_field, all_types)
    assert vocab == {"keramika": "Ceramics"}


# ════════════════════════════════════════════════════════════════════════════
# Extension suite (Phase 2 / hub issue #10) — processors/vocab.py coverage and
# harvester edge cases beyond the seed tests above.
# ════════════════════════════════════════════════════════════════════════════

from processors.vocab import get_matching_terms, load_vocabulary  # noqa: E402


class TestLoadVocabulary:
    """processors/vocab.py — the shared two-column CSV loader."""

    def _write(self, tmp_path, text):
        p = tmp_path / "vocab.csv"
        p.write_text(text, encoding="utf-8")
        return p

    def test_basic_mapping_lowercases_keys_keeps_values_verbatim(self, tmp_path):
        p = self._write(tmp_path, "Hrad,Castle\nKOSTEL,Church\n")
        assert load_vocabulary(p) == {"hrad": "Castle", "kostel": "Church"}

    @pytest.mark.parametrize("header", ["source_lemma", "Source", "SRC", "term", "lemma", "cs"])
    def test_header_row_variants_are_skipped(self, tmp_path, header):
        p = self._write(tmp_path, f"{header},target_translation\nhrad,castle\n")
        assert load_vocabulary(p) == {"hrad": "castle"}

    def test_first_row_kept_when_not_header_like(self, tmp_path):
        p = self._write(tmp_path, "hrad,castle\nkost,bone\n")
        assert load_vocabulary(p) == {"hrad": "castle", "kost": "bone"}

    def test_header_like_key_kept_when_not_in_first_row(self, tmp_path):
        """Header detection applies to row 0 only — a legitimate term that
        happens to equal a header key survives elsewhere."""
        p = self._write(tmp_path, "hrad,castle\ncs,czech\n")
        assert load_vocabulary(p) == {"hrad": "castle", "cs": "czech"}

    def test_short_rows_and_empty_sources_are_ignored(self, tmp_path):
        p = self._write(tmp_path, "loner\nhrad,castle\n,orphan\n\n")
        assert load_vocabulary(p) == {"hrad": "castle"}

    def test_cells_are_whitespace_stripped(self, tmp_path):
        p = self._write(tmp_path, "  hrad  ,  castle  \n")
        assert load_vocabulary(p) == {"hrad": "castle"}

    def test_missing_file_warns_and_returns_empty(self, tmp_path, capsys):
        result = load_vocabulary(tmp_path / "nope.csv")
        assert result == {}
        assert "[WARN]" in capsys.readouterr().out

    def test_duplicate_keys_last_wins(self, tmp_path):
        p = self._write(tmp_path, "hrad,castle\nHRAD,fortress\n")
        assert load_vocabulary(p) == {"hrad": "fortress"}


class TestGetMatchingTerms:
    """processors/vocab.py — whole-word Tag-and-Protect matching."""

    def test_whole_word_match_case_insensitive(self):
        assert get_matching_terms("KOST byla nalezena", {"kost": "bone"}) == [("kost", "bone")]

    def test_substring_of_longer_word_is_rejected(self):
        """The documented guard: 'kost' must not fire inside 'kostel'."""
        assert get_matching_terms("kostel je velky", {"kost": "bone"}) == []

    def test_multiword_key_with_regex_special_dot(self):
        vocab = {"sv. jan": "st. john"}
        assert get_matching_terms("kostel sv. jan stoji", vocab) == [("sv. jan", "st. john")]

    def test_key_ending_in_non_word_char_is_a_known_limitation(self):
        r"""Keys ending in a non-word character (e.g. 'c++') never match: the
        trailing \b cannot sit between '+' and a space. Documented so any
        future fix must consciously change this assertion."""
        assert get_matching_terms("we use c++ daily", {"c++": "cpp"}) == []

    def test_inflected_forms_do_not_match(self):
        vocab = {"hrad": "castle", "kost": "bone"}
        assert dict(get_matching_terms("u hradu byla kost", vocab)) == {"kost": "bone"}

    def test_empty_inputs(self):
        assert get_matching_terms("", {"hrad": "castle"}) == []
        assert get_matching_terms("hrad", {}) == []


class TestExtractLabelEdges:
    """_extract_label branches not exercised by the seed test."""

    def test_language_alias_as_direct_key(self):
        assert load_vocab._extract_label({"cze": "hrad"}, "cs") == "hrad"

    def test_nested_entries_honour_langcode_key(self):
        item = {"translations": [{"langCode": "EN", "term": "castle"}]}
        assert load_vocab._extract_label(item, "en") == "castle"

    def test_whitespace_only_values_are_rejected(self):
        assert load_vocab._extract_label({"cs": "   "}, "cs") == ""


class TestGqlPayloads:
    """_gql request construction beyond the seed happy/error paths."""

    def test_posts_variables_when_given(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"data": {"ok": 1}}
        session.post.return_value = resp
        assert load_vocab._gql(session, "{ ok }", {"x": 2}) == {"ok": 1}
        assert session.post.call_args.kwargs["json"] == {"query": "{ ok }", "variables": {"x": 2}}

    def test_omits_variables_key_when_absent(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"data": {}}
        session.post.return_value = resp
        load_vocab._gql(session, "{ ok }")
        assert "variables" not in session.post.call_args.kwargs["json"]


_OAI = "http://www.openarchives.org/OAI/2.0/"
_AMCR = "https://api.aiscr.cz/schema/amcr/2.2/"


def _oai_page(pairs, token=None):
    records = "".join(
        f"""<record><metadata><amcr:heslo>
              <amcr:heslo xml:lang="cs">{cs}</amcr:heslo>
              <amcr:heslo_en>{en}</amcr:heslo_en>
            </amcr:heslo></metadata></record>"""
        for cs, en in pairs
    )
    rt = f"<resumptionToken>{token}</resumptionToken>" if token else ""
    return (f'<OAI-PMH xmlns="{_OAI}" xmlns:amcr="{_AMCR}"><ListRecords>{records}{rt}</ListRecords></OAI-PMH>').encode(
        "utf-8"
    )


class TestHarvestAmcrEdges:
    """Pagination, partial-failure, and malformed-input behaviour."""

    def _get_factory(self, pages):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            resp = MagicMock()
            resp.content = pages[len(calls) - 1]
            resp.raise_for_status.return_value = None
            return resp

        return fake_get, calls

    def test_paginates_via_resumption_token_and_quotes_it(self, monkeypatch):
        pages = [_oai_page([("Hrad", "Castle")], token="tok&amp;1"), _oai_page([("kost", "bone")])]
        fake_get, calls = self._get_factory(pages)
        monkeypatch.setattr(load_vocab.requests, "get", fake_get)
        monkeypatch.setattr(load_vocab.time, "sleep", lambda _s: None)

        vocab = load_vocab.harvest_amcr()

        assert vocab == {"hrad": "Castle", "kost": "bone"}
        assert len(calls) == 2
        assert "resumptionToken=tok%261" in calls[1]  # parsed 'tok&1', URL-quoted

    def test_mid_harvest_network_error_returns_partial(self, monkeypatch):
        pages = [_oai_page([("hrad", "castle")], token="tok")]

        def fake_get(url, **kwargs):
            if "resumptionToken" in url:
                raise requests.RequestException("down")
            resp = MagicMock()
            resp.content = pages[0]
            resp.raise_for_status.return_value = None
            return resp

        monkeypatch.setattr(load_vocab.requests, "get", fake_get)
        monkeypatch.setattr(load_vocab.time, "sleep", lambda _s: None)
        assert load_vocab.harvest_amcr() == {"hrad": "castle"}

    def test_xml_garbage_stops_cleanly(self, monkeypatch):
        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.content = b"this is not xml"
            resp.raise_for_status.return_value = None
            return resp

        monkeypatch.setattr(load_vocab.requests, "get", fake_get)
        assert load_vocab.harvest_amcr() == {}

    def test_incomplete_pairs_are_skipped(self, monkeypatch):
        page = _oai_page([("hrad", "castle"), ("bezen", ""), ("", "orphan")])
        fake_get, _calls = self._get_factory([page])
        monkeypatch.setattr(load_vocab.requests, "get", fake_get)
        assert load_vocab.harvest_amcr() == {"hrad": "castle"}


_SCHEMA_WITH = {
    "__schema": {
        "types": [
            {
                "name": "Query",
                "kind": "OBJECT",
                "fields": [
                    {"name": "exportAll", "args": [], "type": {"name": "String"}},
                    {"name": "search", "args": [], "type": {"name": "Item"}},
                ],
            },
            {"name": "Item", "kind": "OBJECT", "fields": [{"name": "id"}, {"name": "name"}]},
        ]
    }
}


class TestHarvestTeater:
    """Strategy selection: exportAll first, search fallback, clean failure."""

    def test_strategy_a_export_url_preferred_and_localhost_rewritten(self, monkeypatch):
        def fake_gql(session, query, variables=None):
            if "__schema" in query:
                return _SCHEMA_WITH
            return {"exportAll": "http://localhost:8080/export.csv"}

        seen = {}

        def fake_download(session, url):
            seen["url"] = url
            return {"hrad": "castle"}

        monkeypatch.setattr(load_vocab, "_gql", fake_gql)
        monkeypatch.setattr(load_vocab, "_download_and_parse_export", fake_download)

        assert load_vocab.harvest_teater() == {"hrad": "castle"}
        assert seen["url"] == "https://teater.aiscr.cz/export.csv"

    def test_falls_back_to_search_when_export_empty(self, monkeypatch):
        def fake_gql(session, query, variables=None):
            if "__schema" in query:
                return _SCHEMA_WITH
            return {"exportAll": ""}

        monkeypatch.setattr(load_vocab, "_gql", fake_gql)
        monkeypatch.setattr(load_vocab, "_harvest_via_search", lambda *a, **k: {"kost": "bone"})
        assert load_vocab.harvest_teater() == {"kost": "bone"}

    def test_total_failure_returns_empty(self, monkeypatch):
        def fake_gql(session, query, variables=None):
            raise RuntimeError("api down")

        monkeypatch.setattr(load_vocab, "_gql", fake_gql)
        assert load_vocab.harvest_teater() == {}


class TestHarvestViaSearchEdges:
    """Parameterized-query construction and the empty-result guard."""

    def _search_field(self, args):
        return {
            "name": "search",
            "args": [{"name": a} for a in args],
            "type": {"kind": "LIST", "ofType": {"name": "Item"}},
        }

    def _all_types(self):
        return [{"name": "Item", "fields": [{"name": "id"}, {"name": "name"}]}]

    def test_uses_parameterized_variables_with_language_and_limit(self, monkeypatch):
        captured = []

        def fake_gql(session, query, variables=None):
            captured.append((query, variables))
            lang = (variables or {}).get("lang", "")
            if lang == "CS":
                return {"search": [{"id": 1, "name": "hrad"}, {"id": 2, "name": "kost"}]}
            return {"search": [{"id": 1, "name": "Castle"}]}

        monkeypatch.setattr(load_vocab, "_gql", fake_gql)
        vocab = load_vocab._harvest_via_search(
            MagicMock(), self._search_field(["language", "limit"]), self._all_types()
        )

        assert vocab == {"hrad": "Castle"}  # id 2 has no EN counterpart
        query, variables = captured[0]
        assert "$lang: Language!" in query and "$limit: Int!" in query
        assert variables == {"lang": "CS", "limit": 99999}

    def test_no_cs_items_returns_empty(self, monkeypatch):
        monkeypatch.setattr(load_vocab, "_gql", lambda *a, **k: {"search": []})
        vocab = load_vocab._harvest_via_search(MagicMock(), self._search_field(["limit"]), self._all_types())
        assert vocab == {}


# ── The CLI README.md has documented since v0.4.0 ────────────────────────────
#
# load_vocab.py used to end with a comment stating that the
# `--skip-teater/--out/--delay` entry point the README documents "has never
# existed here". The commands in README's "Harvesting the Vocabulary" section
# were therefore unrunnable. These tests pin the documented surface so the
# documentation cannot drift back out of truth.


def test_cli_writes_the_merged_csv(tmp_path, monkeypatch):
    out = tmp_path / "vocab.csv"
    monkeypatch.setattr(
        load_vocab, "harvest_amcr_records", lambda delay=0.3: {"mohyla": load_vocab.VocabEntry("barrow")}
    )
    monkeypatch.setattr(
        load_vocab, "harvest_teater_records", lambda delay=0.3: {"sidliste": load_vocab.VocabEntry("settlement")}
    )

    assert load_vocab.main(["--out", str(out)]) == 0

    body = out.read_text(encoding="utf-8")
    assert "mohyla" in body and "sidliste" in body


def test_the_csv_is_written_with_lf_line_endings(tmp_path):
    """CRLF (the csv default) changed the committed file's sha256, which /info reports (translator#4)."""
    out = tmp_path / "vocab.csv"
    load_vocab.write_vocabulary_csv({"mohyla": load_vocab.VocabEntry("barrow")}, out)
    assert b"\r" not in out.read_bytes()


def test_the_committed_vocabulary_has_lf_line_endings():
    committed = Path(__file__).resolve().parents[1] / "data_samples" / "vocabulary.csv"
    assert b"\r" not in committed.read_bytes()


def test_cli_can_skip_a_source(tmp_path, monkeypatch):
    out = tmp_path / "vocab.csv"
    called = {"teater": False}

    def _teater(delay=0.3):
        called["teater"] = True
        return {}

    monkeypatch.setattr(
        load_vocab, "harvest_amcr_records", lambda delay=0.3: {"mohyla": load_vocab.VocabEntry("barrow")}
    )
    monkeypatch.setattr(load_vocab, "harvest_teater_records", _teater)

    assert load_vocab.main(["--out", str(out), "--skip-teater"]) == 0
    assert called["teater"] is False, "--skip-teater still harvested TEATER"


def test_cli_refuses_to_skip_everything():
    with pytest.raises(SystemExit) as excinfo:
        load_vocab.main(["--skip-amcr", "--skip-teater"])
    assert excinfo.value.code != 0


def test_cli_reports_an_empty_harvest_without_writing(tmp_path, monkeypatch):
    """An empty harvest must not silently truncate an existing vocabulary."""
    out = tmp_path / "vocab.csv"
    out.write_text("source_lemma,target_translation,source,source_id,uri\nmohyla,barrow,,,\n", encoding="utf-8")

    monkeypatch.setattr(load_vocab, "harvest_amcr_records", lambda delay=0.3: {})
    monkeypatch.setattr(load_vocab, "harvest_teater_records", lambda delay=0.3: {})

    assert load_vocab.main(["--out", str(out)]) == 2
    assert "mohyla" in out.read_text(encoding="utf-8"), "an empty harvest overwrote the existing CSV"


def test_cli_delay_paces_both_sources(tmp_path, monkeypatch):
    """--delay reaches the TEATER harvest too, not only AMCR's page loop."""
    seen = {}

    def _amcr(delay=0.3):
        seen["amcr"] = delay
        return {"mohyla": load_vocab.VocabEntry("barrow")}

    def _teater(delay=0.3):
        seen["teater"] = delay
        return {}

    monkeypatch.setattr(load_vocab, "harvest_amcr_records", _amcr)
    monkeypatch.setattr(load_vocab, "harvest_teater_records", _teater)

    assert load_vocab.main(["--out", str(tmp_path / "v.csv"), "--delay", "1.5"]) == 0
    assert seen == {"amcr": 1.5, "teater": 1.5}


# ── TEATER request pacing ────────────────────────────────────────────────────


class TestPacedSession:
    """_PacedSession keeps min_interval seconds between the end of one request
    and the start of the next — the TEATER counterpart of AMCR's page delay."""

    @pytest.fixture
    def clock(self, monkeypatch):
        state = {"now": 100.0, "sleeps": []}

        def fake_sleep(seconds):
            state["sleeps"].append(seconds)
            state["now"] += seconds

        monkeypatch.setattr(load_vocab.time, "monotonic", lambda: state["now"])
        monkeypatch.setattr(load_vocab.time, "sleep", fake_sleep)
        return state

    def test_first_request_is_not_delayed(self, clock, monkeypatch):
        monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: "resp")
        assert load_vocab._PacedSession(0.5).post("https://example.org") == "resp"
        assert clock["sleeps"] == []

    def test_next_request_waits_out_the_remaining_interval(self, clock, monkeypatch):
        monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: "resp")
        session = load_vocab._PacedSession(0.5)
        session.post("https://example.org")
        clock["now"] += 0.2  # 0.2 s elapse between the two calls
        session.get("https://example.org")
        assert clock["sleeps"] == [pytest.approx(0.3)]

    def test_no_wait_once_the_interval_has_passed(self, clock, monkeypatch):
        monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: "resp")
        session = load_vocab._PacedSession(0.5)
        session.get("https://example.org")
        clock["now"] += 2.0
        session.get("https://example.org")
        assert clock["sleeps"] == []

    def test_interval_counts_from_the_end_of_a_slow_response(self, clock, monkeypatch):
        def slow_request(self, *a, **k):
            clock["now"] += 3.0  # the response itself takes 3 s
            return "resp"

        monkeypatch.setattr(requests.Session, "request", slow_request)
        session = load_vocab._PacedSession(0.5)
        session.get("https://example.org")
        session.get("https://example.org")
        assert clock["sleeps"] == [pytest.approx(0.5)]

    def test_a_failed_request_still_paces_the_next(self, clock, monkeypatch):
        calls = []

        def flaky(self, *a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise requests.ConnectionError("reset")
            return "resp"

        monkeypatch.setattr(requests.Session, "request", flaky)
        session = load_vocab._PacedSession(0.5)
        with pytest.raises(requests.ConnectionError):
            session.get("https://example.org")
        session.get("https://example.org")
        assert clock["sleeps"] == [pytest.approx(0.5)]

    def test_zero_interval_never_sleeps(self, clock, monkeypatch):
        monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: "resp")
        session = load_vocab._PacedSession(0)
        for _ in range(3):
            session.get("https://example.org")
        assert clock["sleeps"] == []

    def test_teater_harvest_uses_a_paced_session_with_the_given_delay(self, monkeypatch):
        sessions = []

        def fake_gql(session, query, variables=None):
            sessions.append(session)
            raise RuntimeError("stop after introspection")

        monkeypatch.setattr(load_vocab, "_gql", fake_gql)
        assert load_vocab.harvest_teater_records(delay=0.7) == {}
        assert isinstance(sessions[0], load_vocab._PacedSession)
        assert sessions[0].min_interval == 0.7


# ── TEATER Strategy A: the exportAll JSON export ─────────────────────────────
#
# tests/fixtures/teater_export_sample.json is a trimmed copy of the live
# https://teater.aiscr.cz/api/export response (2026-09-23): the real key layout,
# a nested chain, a Czech label shared by two concepts (geoarcheologie, ids 26
# and 317), and the live export's one node without an id (url ".../id/").

_EXPORT_FIXTURE = Path(__file__).parent / "fixtures" / "teater_export_sample.json"


def _export_sample() -> dict:
    return json.loads(_EXPORT_FIXTURE.read_text(encoding="utf-8"))


class TestParseExportRecords:
    def test_every_node_with_cs_and_en_names_yields_a_pair(self):
        vocab = load_vocab._parse_export_records(_export_sample())
        assert len(vocab) == 15
        assert vocab["archeolog"] == load_vocab.VocabEntry(
            "archaeologist", "teater", "4", "https://teater.aiscr.cz/id/4"
        )
        # a top-level heading is a node like any other
        assert vocab["1) teorie a přístupy"].target == "1) Theory and approaches"

    def test_uris_use_the_canonical_https_base_not_the_export_url(self):
        """The export's own url field is http://…; the CSV carries the
        TEATER_ID_BASE form that atrium_vocab resolves."""
        vocab = load_vocab._parse_export_records(_export_sample())
        uris = [e.uri for e in vocab.values() if e.uri]
        assert uris and all(u.startswith(load_vocab.TEATER_ID_BASE) for u in uris)

    def test_label_shared_by_two_concepts_keeps_the_last_in_document_order(self):
        """Same collision rule as the AMCR loop and the search fallback."""
        vocab = load_vocab._parse_export_records(_export_sample())
        assert vocab["geoarcheologie"].source_id == "317"  # not 26

    def test_node_without_id_keeps_the_pair_but_claims_no_identity(self):
        """Its url is the bare '.../id/': the id must be empty, not 'id'."""
        vocab = load_vocab._parse_export_records(_export_sample())
        assert vocab["klimatická změna"] == load_vocab.VocabEntry("climate change", "teater", "", "")

    def test_children_of_an_id_less_node_are_still_harvested(self):
        vocab = load_vocab._parse_export_records(_export_sample())
        assert vocab["ekologická krize"].source_id == "4125"

    def test_descriptions_are_not_read(self):
        """Synonym lists under `descriptions` are not term pairs."""
        vocab = load_vocab._parse_export_records(_export_sample())
        assert all(e.target != "archaeologists" for e in vocab.values())

    def test_id_falls_back_to_the_url_segment(self):
        payload = {"categories": [{"name": {"cs": "hrad", "en": "castle"}, "url": "http://teater.aiscr.cz/id/1429"}]}
        vocab = load_vocab._parse_export_records(payload)
        assert vocab["hrad"] == load_vocab.VocabEntry("castle", "teater", "1429", "https://teater.aiscr.cz/id/1429")

    def test_incomplete_and_malformed_nodes_are_skipped(self):
        payload = {
            "categories": [
                {"id": "1", "name": {"cs": "hrad", "en": ""}},
                {"id": "2", "name": "not a dict"},
                "not a node",
                {"id": "3", "name": {"cs": "kost", "en": "bone"}, "children": None},
            ]
        }
        assert load_vocab._targets_only(load_vocab._parse_export_records(payload)) == {"kost": "bone"}

    @pytest.mark.parametrize("payload", [{}, {"categories": None}, [], None, {"categories": "x"}])
    def test_unexpected_payload_shapes_yield_nothing(self, payload):
        assert load_vocab._parse_export_records(payload) == {}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestStrategyAEndToEnd:
    """Introspection → exportAll → export download → parsed records."""

    def _gql_with_export(self, url):
        def fake_gql(session, query, variables=None):
            if "__schema" in query:
                return _SCHEMA_WITH
            return {"exportAll": url}

        return fake_gql

    def test_harvests_the_export_with_concept_ids(self, monkeypatch):
        requested = []

        def fake_request(self, method, url, *a, **k):
            requested.append((method, url))
            return _FakeResponse(_export_sample())

        monkeypatch.setattr(load_vocab, "_gql", self._gql_with_export("http://localhost:8080/api/export"))
        monkeypatch.setattr(requests.Session, "request", fake_request)

        records = load_vocab.harvest_teater_records(delay=0)

        assert requested == [("GET", "https://teater.aiscr.cz/api/export")]
        assert records["archeolog"].source_id == "4"
        assert load_vocab.harvest_teater(delay=0)["archeolog"] == "archaeologist"

    def test_export_failure_is_reported_and_falls_back_to_search(self, monkeypatch, capsys):
        def broken_request(self, method, url, *a, **k):
            raise requests.ConnectionError("export down")

        monkeypatch.setattr(load_vocab, "_gql", self._gql_with_export("http://localhost:8080/api/export"))
        monkeypatch.setattr(requests.Session, "request", broken_request)
        monkeypatch.setattr(
            load_vocab, "_harvest_via_search_records", lambda *a, **k: {"kost": load_vocab.VocabEntry("bone")}
        )

        assert load_vocab._targets_only(load_vocab.harvest_teater_records(delay=0)) == {"kost": "bone"}
        assert "Strategy A failed: export down" in capsys.readouterr().out


def test_description_is_read_by_header_name_only(tmp_path):
    """A ``description`` column counts only under a header naming it (translator#4).

    keyword-extract's ``*_flat.csv`` carries ``scheme`` in column 6, where this repository's CSV
    carries ``description``: without a header, the sixth column must never be read as one.
    """
    from processors.vocab import load_vocabulary_records

    named = tmp_path / "named.csv"
    named.write_text(
        "source_lemma,target_translation,source,source_id,uri,description\n"
        "kostel,church,amcr,HES-000021,https://api.aiscr.cz/id/HES-000021,A building for worship.\n",
        encoding="utf-8",
    )
    headerless = tmp_path / "headerless.csv"
    headerless.write_text("kostel,church,amcr,HES-000021,https://api.aiscr.cz/id/HES-000021,objekt\n", encoding="utf-8")

    assert load_vocabulary_records(named)["kostel"].description == "A building for worship."
    record = load_vocabulary_records(headerless)["kostel"]
    assert record.uri == "https://api.aiscr.cz/id/HES-000021" and record.description == ""
