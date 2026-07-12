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
