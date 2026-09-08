"""
download_vocabularies.py
────────────────────────
Harvests controlled-vocabulary term pairs (Czech → English) from two sources.

Every harvested pair keeps the identity of the thesaurus concept it came from:
the AMCR ``heslo`` id (``HES-…``) or the TEATER concept id, plus the
dereferenceable URI built from it.  That is what lets a translated term be
traced back to its concept — see :data:`CSV_COLUMNS` for the CSV shape.
"""

from __future__ import annotations

import csv
import time
import urllib.parse
from pathlib import Path
from typing import NamedTuple

from lxml import etree

_SECURE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
)

import requests  # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────

AMCR_OAI_BASE = "https://api.aiscr.cz/2.2/oai"
AMCR_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "amcr": "https://api.aiscr.cz/schema/amcr/2.2/",
}

TEATER_GRAPHQL = "https://teater.aiscr.cz/api/graphql"

# Concept-URI bases, identical to ``atrium_vocab.NS["amcr"]`` / ``["teater"]``.
# Never minted here: an id is only ever appended to them.
AMCR_ID_BASE = "https://api.aiscr.cz/id/"
TEATER_ID_BASE = "https://teater.aiscr.cz/id/"

DEFAULT_OUT = Path("data_samples/vocabulary.csv")
DEFAULT_DELAY = 0.3

# Output columns.  The first two are the historical vocabulary CSV that
# ``processors.vocab.load_vocabulary`` reads; the rest carry concept identity and
# are the leading columns of the ``*_flat.csv`` written by atrium-nlp-enrich
# (``…,source,source_id,uri,scheme,sub,broader,sort``), so the two repos' files
# are prefix-compatible.
CSV_COLUMNS = ("source_lemma", "target_translation", "source", "source_id", "uri")


class VocabEntry(NamedTuple):
    """A harvested translation plus the identity of its source concept."""

    target: str
    source: str = ""
    source_id: str = ""
    uri: str = ""


def _split_identifier(ident: str, base: str) -> tuple[str, str]:
    """Return ``(source_id, uri)`` for *ident* resolved against *base*.

    Identifiers arrive either bare (AMCR's ``heslo/@id`` = ``HES-…``, a TEATER
    concept id) or already as a URI (the OAI ``<identifier>`` is
    ``https://api.aiscr.cz/id/HES-…``).  Both forms yield the same pair.
    """
    ident = (ident or "").strip()
    if not ident:
        return "", ""
    if ident.startswith(("http://", "https://")):
        return ident.rstrip("/").rsplit("/", 1)[-1], ident
    return ident, base + ident


def _oai_identifier(record: etree._Element) -> str:
    """The OAI header ``<identifier>`` of *record*, or ``""`` when absent."""
    oai = AMCR_NS["oai"]
    elem = record.find(f"./{{{oai}}}header/{{{oai}}}identifier")
    return (elem.text or "").strip() if elem is not None else ""


def _targets_only(records: dict[str, VocabEntry]) -> dict[str, str]:
    """Project records onto the legacy ``{source_lemma: target}`` shape."""
    return {key: entry.target for key, entry in records.items()}


def harvest_amcr_records(delay: float = DEFAULT_DELAY) -> dict[str, VocabEntry]:
    vocab: dict[str, VocabEntry] = {}
    url = f"{AMCR_OAI_BASE}?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo"
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
            root = etree.fromstring(resp.content, parser=_SECURE_PARSER)
        except etree.XMLSyntaxError as exc:
            print(f"  [AMCR] XML parse error on page {page}: {exc}")
            break

        amcr_ns = AMCR_NS["amcr"]
        xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"

        for record in root.iter(f"{{{AMCR_NS['oai']}}}record"):
            # Fallback identity for the record as a whole; the per-heslo @id wins.
            record_ident = _oai_identifier(record)
            for heslo_block in record.iter(f"{{{amcr_ns}}}heslo"):
                cs_text = en_text = ""
                for child in heslo_block:
                    if child.tag == f"{{{amcr_ns}}}heslo" and child.get(xml_lang) == "cs":
                        cs_text = (child.text or "").strip()
                    elif child.tag == f"{{{amcr_ns}}}heslo_en":
                        en_text = (child.text or "").strip()
                if cs_text and en_text:
                    ident = (heslo_block.get("id") or "").strip() or record_ident
                    source_id, uri = _split_identifier(ident, AMCR_ID_BASE)
                    vocab[cs_text.lower()] = VocabEntry(en_text, "amcr", source_id, uri)

        rt_elem = root.find(f".//{{{AMCR_NS['oai']}}}resumptionToken")
        if rt_elem is not None and rt_elem.text and rt_elem.text.strip():
            token = rt_elem.text.strip()
            url = f"{AMCR_OAI_BASE}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
            time.sleep(delay)
        else:
            url = None

    print(f"[AMCR] Done – {len(vocab)} term pairs collected.")
    return vocab


def harvest_amcr(delay: float = DEFAULT_DELAY) -> dict[str, str]:
    """Legacy two-column view of :func:`harvest_amcr_records`."""
    return _targets_only(harvest_amcr_records(delay))


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
            if hint.lower() in f.lower():
                return f
    return None


def _extract_label(item: dict, lang: str) -> str:
    prefs = _LANG_PREFS.get(lang, (lang,))
    for pref in prefs:
        for key, val in item.items():
            if pref == key.lower() or key.lower().endswith(pref):
                if isinstance(val, str) and val.strip():
                    return val.strip()

    for list_key in ("labels", "translations", "names", "terms", "equivalents"):
        entries = item.get(list_key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_lang = (entry.get("lang") or entry.get("language") or entry.get("langCode") or "").lower()
            if entry_lang in prefs:
                for val_key in ("value", "label", "name", "term", "text"):
                    v = entry.get(val_key, "")
                    if isinstance(v, str) and v.strip():
                        return v.strip()
    return ""


def _harvest_teater(export_fn, search_fn) -> dict:
    """Strategy selection shared by the two TEATER entry points.

    *export_fn* / *search_fn* are looked up on each call, so the record-keeping
    and the legacy two-column variants run exactly the same strategy ladder.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "ATRIUM-harvester/1.1", "Content-Type": "application/json"})
    print("[TEATER] Connecting to GraphQL API …")

    all_types: list[dict] = []
    query_fields: dict[str, dict] = {}
    try:
        schema_data = _gql(
            session,
            """
        { __schema { types { name kind fields { name args { name type { name kind ofType { name } } } type { name kind ofType { name kind ofType { name kind } } } } } } }
        """,
        )
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
                vocab = export_fn(session, export_url)
                if vocab:
                    print(f"[TEATER] Strategy A succeeded – {len(vocab)} term pairs.")
                    return vocab
        except Exception:
            pass

    if "search" in query_fields:
        try:
            vocab = search_fn(session, query_fields["search"], all_types)
            if vocab:
                print(f"[TEATER] Strategy B succeeded – {len(vocab)} term pairs.")
                return vocab
        except Exception as e:
            print(f"  [TEATER] Strategy B failed: {e}")

    return {}


def harvest_teater_records() -> dict[str, VocabEntry]:
    return _harvest_teater(
        lambda session, url: _download_and_parse_export_records(session, url),
        lambda session, field, types: _harvest_via_search_records(session, field, types),
    )


def harvest_teater() -> dict[str, str]:
    """Legacy two-column view of :func:`harvest_teater_records`."""
    return _harvest_teater(
        lambda session, url: _download_and_parse_export(session, url),
        lambda session, field, types: _harvest_via_search(session, field, types),
    )


def _download_and_parse_export_records(session: requests.Session, url: str) -> dict[str, VocabEntry]:
    resp = session.get(url, timeout=60)  # verify=False removed
    resp.raise_for_status()
    # (parsing logic unchanged — STUB: never yielded a term pair, and so cannot
    # carry concept ids either. Strategy B below is the one that harvests.)
    return {}


def _download_and_parse_export(session: requests.Session, url: str) -> dict[str, str]:
    return _targets_only(_download_and_parse_export_records(session, url))


def _harvest_via_search_records(
    session: requests.Session, search_field: dict, all_types: list[dict]
) -> dict[str, VocabEntry]:
    t = search_field.get("type", {})
    while t.get("ofType"):
        t = t["ofType"]
    item_type_name = t.get("name")

    item_type = next((x for x in all_types if x["name"] == item_type_name), None) if item_type_name else None
    field_names = [f["name"] for f in (item_type.get("fields") or [])] if item_type else []
    if not field_names:
        field_names = ["id", "name", "url"]

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

    if not cs_items:
        return {}

    vocab: dict[str, VocabEntry] = {}
    id_field = _pick_field(field_names, "id")
    val_field = _pick_field(field_names, "name", "term")

    if id_field and val_field and en_items:
        en_by_id = {str(item.get(id_field, "")).strip(): (item.get(val_field) or "").strip() for item in en_items}
        for item in cs_items:
            cs_val = (item.get(val_field) or "").strip()
            # The id that joins the cs/en pair *is* the concept identity: keep it.
            ident = str(item.get(id_field, "")).strip()
            en_val = en_by_id.get(ident, "")
            if cs_val and en_val:
                source_id, uri = _split_identifier(ident, TEATER_ID_BASE)
                vocab[cs_val.lower()] = VocabEntry(en_val, "teater", source_id, uri)

    return vocab


def _harvest_via_search(session: requests.Session, search_field: dict, all_types: list[dict]) -> dict[str, str]:
    return _targets_only(_harvest_via_search_records(session, search_field, all_types))


def merge_records(amcr: dict[str, VocabEntry] | None, teater: dict[str, VocabEntry] | None) -> dict[str, VocabEntry]:
    """Merge the two harvests, AMCR winning on a key collision (README)."""
    merged: dict[str, VocabEntry] = dict(teater or {})
    merged.update(amcr or {})
    return merged


def write_vocabulary_csv(records: dict, out_path: Path | str = DEFAULT_OUT) -> int:
    """Write *records* to *out_path* as :data:`CSV_COLUMNS`; return the row count.

    Rows are sorted by ``source_lemma`` so re-harvests produce a stable diff.
    A plain ``{lemma: target}`` mapping is accepted too, and writes empty
    provenance cells.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, mode="w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for lemma in sorted(records):
            value = records[lemma]
            entry = value if isinstance(value, VocabEntry) else VocabEntry(str(value))
            writer.writerow([lemma, entry.target, entry.source, entry.source_id, entry.uri])
    print(f"[OUT] Wrote {len(records)} term pairs to {out}")
    return len(records)


# ... (no argparse CLI in this file: the `python load_vocab.py --skip-teater/--out/--delay`
# entry point the README documents has never existed here. merge_records() and
# write_vocabulary_csv() above are the pieces a caller needs to assemble it.)
