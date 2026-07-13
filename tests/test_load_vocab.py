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
