"""
download_vocabularies.py
────────────────────────
Harvests controlled-vocabulary term pairs (Czech → English) from two sources:

  1. AMCR OAI-PMH API  – https://api.aiscr.cz/2.2/oai?set=heslo
  2. TEATER            – https://teater.aiscr.cz/api/graphql
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.parse
import urllib3
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── constants ────────────────────────────────────────────────────────────────

AMCR_OAI_BASE  = "https://api.aiscr.cz/2.2/oai"
AMCR_NS = {
    "oai":  "http://www.openarchives.org/OAI/2.0/",
    "amcr": "https://api.aiscr.cz/schema/amcr/2.2/",
}

TEATER_GRAPHQL = "https://teater.aiscr.cz/api/graphql"

DEFAULT_OUT   = Path("data_samples/vocabulary.csv")
DEFAULT_DELAY = 0.3


# ─────────────────────────────────────────────────────────────────────────────
# AMCR harvester
# ─────────────────────────────────────────────────────────────────────────────

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

    print(f"[AMCR] Done – {len(vocab)} term pairs collected across {page} page(s).")
    return vocab


# ─────────────────────────────────────────────────────────────────────────────
# TEATER harvester
# ─────────────────────────────────────────────────────────────────────────────

_LANG_PREFS = {
    "cs": ("cs", "cze", "czech", "čeština"),
    "en": ("en", "eng", "english"),
}

def _gql(session: requests.Session, query: str, variables: dict | None = None) -> dict:
    payload: dict = {"query": query}
    if variables: payload["variables"] = variables
    resp = session.post(TEATER_GRAPHQL, json=payload, timeout=30, verify=False)
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
            print(f"  [TEATER] Queries available: {list(query_fields.keys())}")
    except Exception as e:
        print(f"  [TEATER] Schema introspection failed: {e}")

    # ── Strategy A ──
    if "exportAll" in query_fields:
        print("  [TEATER] Strategy A: calling exportAll …")
        try:
            data = _gql(session, "{ exportAll }")
            export_url = data.get("exportAll", "")
            if isinstance(export_url, str) and export_url.startswith("http"):
                export_url = export_url.replace("http://localhost:8080", "https://teater.aiscr.cz")
                print(f"  [TEATER] Export URL: {export_url}")
                vocab = _download_and_parse_export(session, export_url)
                if vocab:
                    print(f"[TEATER] Strategy A succeeded – {len(vocab)} term pairs.")
                    return vocab
            else:
                print(f"  [TEATER] exportAll returned unexpected value: {repr(export_url)[:120]}")
        except Exception as e:
            print(f"  [TEATER] Strategy A failed: {e}")

    # ── Strategy B ──
    if "search" in query_fields:
        print("  [TEATER] Strategy B: search-based harvest …")
        try:
            vocab = _harvest_via_search(session, query_fields["search"], all_types)
            if vocab:
                print(f"[TEATER] Strategy B succeeded – {len(vocab)} term pairs.")
                return vocab
        except Exception as e:
            print(f"  [TEATER] Strategy B failed: {e}")

    print("[TEATER] All strategies exhausted – no term pairs collected.")
    return {}

def _download_and_parse_export(session: requests.Session, url: str) -> dict[str, str]:
    resp = session.get(url, timeout=60, verify=False)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    text = resp.text.strip()

    print(f"  [TEATER] Export Format detected: {content_type}")
    print(f"  [TEATER] Export Preview (first 200 chars): {text[:200]!r}")

    vocab: dict[str, str] = {}

    # JSON export
    if "json" in content_type or text.startswith(("[", "{")):
        try:
            import json as _json
            data = _json.loads(text)

            # Recursively crawl the JSON for ANY dict containing {"cs": "...", "en": "..."}
            def extract_pairs_recursive(obj):
                if isinstance(obj, dict):
                    # Check for direct cs/en keys
                    keys = {k.lower(): k for k in obj.keys()}
                    if "cs" in keys and "en" in keys:
                        cs_val, en_val = obj[keys["cs"]], obj[keys["en"]]
                        if isinstance(cs_val, str) and isinstance(en_val, str):
                            vocab[cs_val.strip().lower()] = en_val.strip()

                    # Continue crawling deeper
                    for v in obj.values():
                        extract_pairs_recursive(v)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_pairs_recursive(item)

            extract_pairs_recursive(data)
            return vocab
        except Exception as e:
            print(f"  [TEATER] JSON parse error: {e}")

    # CSV export
    import io
    try:
        # Sniff delimiter (handles ';' commonly used in Czech datasets)
        dialect = csv.Sniffer().sniff(text[:2048])
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:
        reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        return {}

    headers = [h.lower().strip() for h in reader.fieldnames]
    print(f"  [TEATER] Parsed CSV columns: {reader.fieldnames}")

    cs_col = _pick_field(headers, "cs", "czech", "cze", "term_cs")
    en_col = _pick_field(headers, "en", "english", "eng", "term_en")

    for row in reader:
        row_lower = {k.lower().strip(): v for k, v in row.items() if k}
        cs = (row_lower.get(cs_col) or "").strip() if cs_col else ""
        en = (row_lower.get(en_col) or "").strip() if en_col else ""
        if not cs: cs = _extract_label(row_lower, "cs")
        if not en: en = _extract_label(row_lower, "en")
        if cs and en:
            vocab[cs.lower()] = en

    return vocab

def _harvest_via_search(session: requests.Session, search_field: dict, all_types: list[dict]) -> dict[str, str]:
    t = search_field.get("type", {})
    while t.get("ofType"): t = t["ofType"]
    item_type_name = t.get("name")

    item_type = next((x for x in all_types if x["name"] == item_type_name), None) if item_type_name else None
    field_names = [f["name"] for f in (item_type.get("fields") or [])] if item_type else []

    if field_names:
        print(f"  [TEATER] search returns [{item_type_name}], fields: {field_names}")
    else:
        field_names = ["id", "name", "url"]

    fields_gql = " ".join(field_names)

    def do_search(language: str) -> list[dict]:
        arg_names = {a["name"] for a in search_field.get("args", [])}
        lang_enum = language.upper() # Fixed: Use Enum (CS/EN) instead of String ("cs"/"en")

        if "language" in arg_names and "limit" in arg_names:
            q = f'{{ search(value: "", limit: 99999, language: {lang_enum}) {{ {fields_gql} }} }}'
        elif "language" in arg_names:
            q = f'{{ search(value: "", language: {lang_enum}) {{ {fields_gql} }} }}'
        elif "limit" in arg_names:
            q = f'{{ search(value: "", limit: 99999) {{ {fields_gql} }} }}'
        else:
            q = f'{{ search(value: "") {{ {fields_gql} }} }}'

        data = _gql(session, q)
        result = data.get("search", [])
        return result if isinstance(result, list) else []

    cs_items = do_search("cs")
    en_items = do_search("en")
    print(f"  [TEATER] search returned {len(cs_items)} CS items, {len(en_items)} EN items")

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

def merge_and_save(amcr_vocab: dict[str, str], teater_vocab: dict[str, str], out_path: Path) -> int:
    merged = {**teater_vocab, **amcr_vocab}
    rows = sorted(merged.items(), key=lambda kv: kv[0])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_lemma", "target_translation"])
        writer.writerows(rows)

    return len(rows)

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download AMCR + TEATER vocabularies")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.add_argument("--skip-amcr", action="store_true")
    p.add_argument("--skip-teater", action="store_true")
    return p.parse_args()

def main() -> None:
    args = parse_args()
    amcr_vocab: dict[str, str] = {}
    teater_vocab: dict[str, str] = {}

    if not args.skip_amcr: amcr_vocab = harvest_amcr(delay=args.delay)
    if not args.skip_teater: teater_vocab = harvest_teater()

    total = merge_and_save(amcr_vocab, teater_vocab, args.out)

    print(f"\n✓ Vocabulary saved → {args.out}  ({total} entries)")
    print(f"  AMCR:   {len(amcr_vocab):>6} terms")
    print(f"  TEATER: {len(teater_vocab):>6} terms")

if __name__ == "__main__":
    main()