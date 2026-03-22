"""
download_vocabularies.py
────────────────────────
Harvests controlled-vocabulary term pairs (Czech → English) from two sources.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

# ── constants ────────────────────────────────────────────────────────────────

AMCR_OAI_BASE  = "https://api.aiscr.cz/2.2/oai"
AMCR_NS = {
    "oai":  "http://www.openarchives.org/OAI/2.0/",
    "amcr": "https://api.aiscr.cz/schema/amcr/2.2/",
}

TEATER_GRAPHQL = "https://teater.aiscr.cz/api/graphql"

DEFAULT_OUT   = Path("data_samples/vocabulary.csv")
DEFAULT_DELAY = 0.3


def harvest_amcr(delay: float = DEFAULT_DELAY) -> dict[str, str]:
    vocab: dict[str, str] = {}
    url   = f"{AMCR_OAI_BASE}?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo"
    page = 0
    print("[AMCR] Starting OAI-PMH harvest …")

    while url:
        page += 1
        print(f"  [AMCR] Fetching page {page}: {url[:120]}")

        try:
            resp = requests.get(url, timeout=60, headers={"User-Agent": "ATRIUM-vocabulary-harvester/1.1"})
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [AMCR] Network error on page {page}: {exc}")
            break

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            print(f"  [AMCR] XML parse error on page {page}: {exc}")
            break

        amcr_ns = AMCR_NS["amcr"]
        xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"

        for record in root.iter(f"{{{AMCR_NS['oai']}}}record"):
            for heslo_block in record.iter(f"{{{amcr_ns}}}heslo"):
                cs_text = en_text = ""
                for child in heslo_block:
                    if child.tag == f"{{{amcr_ns}}}heslo" and child.get(xml_lang) == "cs":
                        cs_text = (child.text or "").strip()
                    elif child.tag == f"{{{amcr_ns}}}heslo_en":
                        en_text = (child.text or "").strip()
                if cs_text and en_text:
                    vocab[cs_text.lower()] = en_text

        rt_elem = root.find(f".//{{{AMCR_NS['oai']}}}resumptionToken")
        if rt_elem is not None and rt_elem.text and rt_elem.text.strip():
            token = rt_elem.text.strip()
            url = f"{AMCR_OAI_BASE}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
            time.sleep(delay)
        else:
            url = None

    print(f"[AMCR] Done – {len(vocab)} term pairs collected.")
    return vocab


_LANG_PREFS = {
    "cs": ("cs", "cze", "czech", "čeština"),
    "en": ("en", "eng", "english"),
}

def _gql(session: requests.Session, query: str, variables: dict | None = None) -> dict:
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    # FIX: verify=False removed to enforce standard SSL verification
    resp = session.post(TEATER_GRAPHQL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})

def _pick_field(fields: list[str], *hints: str) -> str | None:
    for hint in hints:
        for f in fields:
            if hint.lower() in f.lower(): return f
    return None

def _extract_label(item: dict, lang: str) -> str:
    prefs = _LANG_PREFS.get(lang, (lang,))
    for pref in prefs:
        for key, val in item.items():
            if pref == key.lower() or key.lower().endswith(pref):
                if isinstance(val, str) and val.strip(): return val.strip()

    for list_key in ("labels", "translations", "names", "terms", "equivalents"):
        entries = item.get(list_key)
        if not isinstance(entries, list): continue
        for entry in entries:
            if not isinstance(entry, dict): continue
            entry_lang = (entry.get("lang") or entry.get("language") or entry.get("langCode") or "").lower()
            if entry_lang in prefs:
                for val_key in ("value", "label", "name", "term", "text"):
                    v = entry.get(val_key, "")
                    if isinstance(v, str) and v.strip(): return v.strip()
    return ""


def harvest_teater() -> dict[str, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": "ATRIUM-harvester/1.1", "Content-Type": "application/json"})
    print("[TEATER] Connecting to GraphQL API …")

    all_types: list[dict] = []
    query_fields: dict[str, dict] = {}
    try:
        schema_data = _gql(session, """
        { __schema { types { name kind fields { name args { name type { name kind ofType { name } } } type { name kind ofType { name kind ofType { name kind } } } } } } }
        """)
        all_types = schema_data.get("__schema", {}).get("types", [])
        qt = next((t for t in all_types if t["name"] == "Query"), None)
        if qt:
            query_fields = {f["name"]: f for f in (qt.get("fields") or [])}
    except Exception as e:
        print(f"  [TEATER] Schema introspection failed: {e}")

    if "exportAll" in query_fields:
        try:
            data = _gql(session, "{ exportAll }")
            export_url = data.get("exportAll", "")
            if isinstance(export_url, str) and export_url.startswith("http"):
                export_url = export_url.replace("http://localhost:8080", "https://teater.aiscr.cz")
                vocab = _download_and_parse_export(session, export_url)
                if vocab:
                    print(f"[TEATER] Strategy A succeeded – {len(vocab)} term pairs.")
                    return vocab
        except Exception as e:
            pass

    if "search" in query_fields:
        try:
            vocab = _harvest_via_search(session, query_fields["search"], all_types)
            if vocab:
                print(f"[TEATER] Strategy B succeeded – {len(vocab)} term pairs.")
                return vocab
        except Exception as e:
            print(f"  [TEATER] Strategy B failed: {e}")

    return {}

def _download_and_parse_export(session: requests.Session, url: str) -> dict[str, str]:
    resp = session.get(url, timeout=60) # verify=False removed
    resp.raise_for_status()
    # (parsing logic unchanged)
    return {}

def _harvest_via_search(session: requests.Session, search_field: dict, all_types: list[dict]) -> dict[str, str]:
    t = search_field.get("type", {})
    while t.get("ofType"): t = t["ofType"]
    item_type_name = t.get("name")

    item_type = next((x for x in all_types if x["name"] == item_type_name), None) if item_type_name else None
    field_names = [f["name"] for f in (item_type.get("fields") or [])] if item_type else []
    if not field_names: field_names = ["id", "name", "url"]

    fields_gql = " ".join(field_names)

    def do_search(language: str) -> list[dict]:
        arg_names = {a["name"] for a in search_field.get("args", [])}
        lang_enum = language.upper()

        # FIX: Switched to parameterized GraphQL variables for robustness
        variables = {}
        if "language" in arg_names and "limit" in arg_names:
            q = f'query GetSearch($lang: Language!, $limit: Int!) {{ search(value: "", limit: $limit, language: $lang) {{ {fields_gql} }} }}'
            variables = {"lang": lang_enum, "limit": 99999}
        elif "language" in arg_names:
            q = f'query GetSearch($lang: Language!) {{ search(value: "", language: $lang) {{ {fields_gql} }} }}'
            variables = {"lang": lang_enum}
        elif "limit" in arg_names:
            q = f'query GetSearch($limit: Int!) {{ search(value: "", limit: $limit) {{ {fields_gql} }} }}'
            variables = {"limit": 99999}
        else:
            q = f'query GetSearch {{ search(value: "") {{ {fields_gql} }} }}'

        data = _gql(session, q, variables)
        result = data.get("search", [])
        return result if isinstance(result, list) else []

    cs_items = do_search("cs")
    en_items = do_search("en")

    if not cs_items: return {}

    vocab: dict[str, str] = {}
    id_field = _pick_field(field_names, "id")
    val_field = _pick_field(field_names, "name", "term")

    if id_field and val_field and en_items:
        en_by_id = {str(item.get(id_field, "")): (item.get(val_field) or "").strip() for item in en_items}
        for item in cs_items:
            cs_val = (item.get(val_field) or "").strip()
            en_val = en_by_id.get(str(item.get(id_field, "")), "")
            if cs_val and en_val: vocab[cs_val.lower()] = en_val

    return vocab

# ... (main and merge omitted for brevity)