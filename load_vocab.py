"""
load_vocab.py
─────────────
Harvests controlled-vocabulary term pairs (Czech → English) from two sources and
writes the merged Tag-and-Protect vocabulary CSV (``python load_vocab.py``).

AMCR
    OAI-PMH ``ListRecords`` (``metadataPrefix=oai_amcr``, ``set=heslo``) against
    :data:`AMCR_OAI_BASE`, following ``resumptionToken`` pages.  Every ``heslo``
    with a ``cs`` label and a ``heslo_en`` label yields one pair.

TEATER
    GraphQL at :data:`TEATER_GRAPHQL`, after schema introspection:

    A. ``exportAll`` returns the URL of the full thesaurus export (served as
       ``http://localhost:8080/api/export`` and rewritten to the public host).
       The export is JSON: a ``categories`` tree whose nodes carry ``id``,
       ``name`` (``cs`` / ``en`` / ``de``) and ``children``.  Every node with a
       Czech and an English name yields one pair (:func:`_parse_export_records`).
       This is the strategy that harvests.
    B. Fallback when A yields nothing: ``search(value: "")`` once per language
       (``CS``, ``EN``), joining the two result lists on the concept id.  As of
       2026-09 the live API answers an empty search value with ``{}``, so B
       returns nothing; it is kept in case the export endpoint disappears.

    teater.aiscr.cz currently serves its certificate without the RapidSSL
    intermediate, so a client that does not fetch missing intermediates (such as
    ``requests``) fails verification there.  Point ``REQUESTS_CA_BUNDLE`` at a
    bundle that includes it rather than disabling verification.

``--delay`` paces both sources: seconds between AMCR pages, and the minimum gap
between consecutive TEATER requests (:class:`_PacedSession`).

Every harvested pair keeps the identity of the thesaurus concept it came from:
the AMCR ``heslo`` id (``HES-…``) or the TEATER concept id, plus the
dereferenceable URI built from it.  That is what lets a translated term be
traced back to its concept — see :data:`CSV_COLUMNS` for the CSV shape.

``--from-release VERSION`` (atrium-project#72, #51) builds the same CSV from the
vocabulary atrium-keyword-extract publishes with each release instead of
harvesting: ``atrium-vocabulary-<VERSION>.zip``, checked against the
``.sha256`` published beside it (or ``--sha256``), whose ``amcr_flat.csv`` and
``teater_flat.csv`` start with this file's five columns.  One versioned, CC0
artefact for the whole project, so the translator's vocabulary is a pinned
release rather than whatever the live APIs answered on the day of a harvest.
``--asset`` reads a local copy of the zip (or another URL).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
import time
import urllib.parse
import zipfile
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

#: Where atrium-keyword-extract publishes the vocabulary of each release (atrium-project#72).
RELEASE_ASSET_URL = (
    "https://github.com/ufal/atrium-keyword-extract/releases/download/{version}/atrium-vocabulary-{version}.zip"
)
#: The asset's two harvests, by the ``source`` their rows carry.
ASSET_FLAT_FILES = {"amcr": "amcr_flat.csv", "teater": "teater_flat.csv"}
#: Exit code of ``--from-release`` when the asset is not the one its checksum names.
EXIT_CHECKSUM = 3

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


class _PacedSession(requests.Session):
    """A :class:`requests.Session` keeping *min_interval* seconds between requests.

    The gap is measured from the end of one response to the start of the next
    request, the same pacing ``harvest_amcr_records`` applies between OAI-PMH
    pages, so a slow response never lets the next request follow it at once.
    """

    def __init__(self, min_interval: float = DEFAULT_DELAY) -> None:
        super().__init__()
        self.min_interval = max(0.0, float(min_interval))
        self._last_done: float | None = None

    def request(self, *args, **kwargs):
        if self._last_done is not None and self.min_interval > 0:
            wait = self._last_done + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        try:
            return super().request(*args, **kwargs)
        finally:
            self._last_done = time.monotonic()


def _harvest_teater(export_fn, search_fn, delay: float = DEFAULT_DELAY) -> dict:
    """Strategy selection shared by the two TEATER entry points.

    *export_fn* / *search_fn* are looked up on each call, so the record-keeping
    and the legacy two-column variants run exactly the same strategy ladder.
    Every request goes through one :class:`_PacedSession`, *delay* seconds apart.
    """
    session = _PacedSession(delay)
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
                # The API advertises its internal address; the export is served
                # from the public host under the same path.
                export_url = export_url.replace("http://localhost:8080", "https://teater.aiscr.cz")
                print(f"  [TEATER] Strategy A: downloading export {export_url}")
                vocab = export_fn(session, export_url)
                if vocab:
                    print(f"[TEATER] Strategy A succeeded – {len(vocab)} term pairs.")
                    return vocab
                print("  [TEATER] Strategy A: the export yielded no term pairs.")
            else:
                print(f"  [TEATER] Strategy A: exportAll returned no URL ({export_url!r}).")
        except Exception as e:
            print(f"  [TEATER] Strategy A failed: {e}")

    if "search" in query_fields:
        try:
            vocab = search_fn(session, query_fields["search"], all_types)
            if vocab:
                print(f"[TEATER] Strategy B succeeded – {len(vocab)} term pairs.")
                return vocab
        except Exception as e:
            print(f"  [TEATER] Strategy B failed: {e}")

    return {}


def harvest_teater_records(delay: float = DEFAULT_DELAY) -> dict[str, VocabEntry]:
    return _harvest_teater(
        lambda session, url: _download_and_parse_export_records(session, url),
        lambda session, field, types: _harvest_via_search_records(session, field, types),
        delay,
    )


def harvest_teater(delay: float = DEFAULT_DELAY) -> dict[str, str]:
    """Legacy two-column view of :func:`harvest_teater_records`."""
    return _harvest_teater(
        lambda session, url: _download_and_parse_export(session, url),
        lambda session, field, types: _harvest_via_search(session, field, types),
        delay,
    )


def _iter_export_nodes(nodes):
    """Yield every category node of a TEATER export tree, depth-first in document order."""
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        yield node
        yield from _iter_export_nodes(node.get("children"))


def _export_node_identity(node: dict) -> tuple[str, str]:
    """``(source_id, uri)`` of an export node, built on :data:`TEATER_ID_BASE`.

    The node's ``id`` is authoritative.  Without one, the id is read from the
    last segment of its ``url`` (``http://teater.aiscr.cz/id/<id>``), and only if
    that segment is present: the live export contains a node whose ``url`` is
    the bare ``…/id/``, which must yield no id rather than the literal ``"id"``.
    """
    ident = str(node.get("id") or "").strip()
    if not ident:
        url = str(node.get("url") or "").strip()
        if url and not url.endswith("/"):
            ident = url.rsplit("/", 1)[-1]
    return _split_identifier(ident, TEATER_ID_BASE)


def _parse_export_records(payload: dict) -> dict[str, VocabEntry]:
    """Flatten TEATER's JSON export into ``{cs_label.lower(): VocabEntry}``.

    Shape, as served by ``/api/export`` (2026-09)::

        {"categories": [{"id": "4",
                         "name": {"cs": "archeolog", "en": "archaeologist", "de": "…"},
                         "url": "http://teater.aiscr.cz/id/4",
                         "descriptions": [...],
                         "children": [...]}, ...],
         "lastImport": "2023-01-23"}

    Every node with both a Czech and an English name yields a pair; the tree's
    top-level headings are nodes like any other.  ``descriptions`` (synonyms,
    quotes) are not read.  Some Czech labels name more than one concept (the
    same term filed under two branches, each with its own id): the last one in
    document order keeps the label, the same rule as the AMCR harvest and the
    search fallback (plain assignment), so every strategy resolves a collision
    alike and a re-harvest is stable.
    """
    vocab: dict[str, VocabEntry] = {}
    categories = payload.get("categories") if isinstance(payload, dict) else None
    for node in _iter_export_nodes(categories):
        name = node.get("name")
        if not isinstance(name, dict):
            continue
        cs_text = _extract_label(name, "cs")
        en_text = _extract_label(name, "en")
        if not (cs_text and en_text):
            continue
        source_id, uri = _export_node_identity(node)
        vocab[cs_text.lower()] = VocabEntry(en_text, "teater", source_id, uri)
    return vocab


def _download_and_parse_export_records(session: requests.Session, url: str) -> dict[str, VocabEntry]:
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return _parse_export_records(resp.json())


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


# ── the released vocabulary (atrium-keyword-extract) ────────────────────────


class AssetError(RuntimeError):
    """The release asset could not be read, or is not the file its checksum names."""


def _fetch_bytes(location: str, timeout: float = 120.0) -> bytes:
    if location.startswith(("http://", "https://")):
        response = requests.get(location, timeout=timeout)
        response.raise_for_status()
        return response.content
    return Path(location).read_bytes()


def _expected_digest(asset_location: str, sha256: str | None) -> str:
    """The pinned digest, else the first field of the ``<asset>.sha256`` published beside it."""
    if sha256:
        digest = sha256.strip().lower()
    else:
        try:
            digest = _fetch_bytes(f"{asset_location}.sha256").decode("utf-8").split()[0].lower()
        except (OSError, requests.RequestException, IndexError, UnicodeDecodeError) as exc:
            raise AssetError(
                f"no checksum for {asset_location}: pass --sha256 or publish {asset_location}.sha256 ({exc})"
            ) from exc
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AssetError(f"{digest!r} is not a SHA-256 digest")
    return digest


def _flat_records(data: bytes, name: str) -> dict[str, VocabEntry]:
    """``{source_lemma: VocabEntry}`` of one flat CSV of the asset (its first five columns)."""
    records: dict[str, VocabEntry] = {}
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    missing = [column for column in CSV_COLUMNS if column not in (reader.fieldnames or [])]
    if missing:
        raise AssetError(f"{name} has no column(s) {missing}")
    for row in reader:
        lemma, target = (row.get("source_lemma") or "").strip(), (row.get("target_translation") or "").strip()
        if lemma and target:
            records[lemma] = VocabEntry(
                target, row.get("source") or "", row.get("source_id") or "", row.get("uri") or ""
            )
    return records


def records_from_release(version: str, *, asset: str | None = None, sha256: str | None = None) -> dict[str, VocabEntry]:
    """The merged records of a published vocabulary release, verified by its SHA-256.

    *asset* is a path or URL of the zip (default: the release's download URL); *sha256* pins the
    digest (default: the ``.sha256`` file published beside the asset). Raises :class:`AssetError`.
    """
    tag = version if version.startswith("v") else f"v{version}"  # the release tags carry the v
    location = asset or RELEASE_ASSET_URL.format(version=tag)
    try:
        payload = _fetch_bytes(location)
    except (OSError, requests.RequestException) as exc:
        raise AssetError(f"cannot read {location}: {exc}") from exc
    expected = _expected_digest(location, sha256)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise AssetError(f"{location} has SHA-256 {actual}, not {expected}: refusing it")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            missing = sorted(set(ASSET_FLAT_FILES.values()) - names)
            if missing:
                raise AssetError(f"{location} lacks {missing}")
            per_source = {source: _flat_records(archive.read(name), name) for source, name in ASSET_FLAT_FILES.items()}
    except zipfile.BadZipFile as exc:
        raise AssetError(f"{location} is not a zip archive: {exc}") from exc
    print(
        f"[IN] {location} (sha256 {actual[:12]}…): "
        + ", ".join(f"{source} {len(records)}" for source, records in per_source.items())
    )
    return merge_records(per_source["amcr"], per_source["teater"])


def main(argv: list[str] | None = None) -> int:
    """Harvest both vocabulary sources and write the merged CSV.

    This entry point is the one README.md has documented since v0.4.0 and that
    did not exist: the file previously ended with a comment saying as much, which
    made the README's "Harvesting the Vocabulary" section a set of commands no
    one could run. The commands are the documented ones, unchanged, so the
    documentation became true rather than the other way round.

    Exit codes follow main.py's vocabulary (0 ok, 1 usage, 2 nothing harvested), plus
    :data:`EXIT_CHECKSUM` (3) when ``--from-release`` refuses an asset it cannot verify.
    """
    parser = argparse.ArgumentParser(
        prog="load_vocab.py",
        description=(
            "Harvest archaeological term pairs from the AMCR OAI-PMH endpoint and the "
            "TEATER GraphQL API, merge them, and write a Tag-and-Protect vocabulary CSV."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output CSV path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=(
            "seconds between consecutive requests to either source: AMCR OAI-PMH pages "
            f"and TEATER GraphQL/export calls (default: {DEFAULT_DELAY})"
        ),
    )
    parser.add_argument("--skip-amcr", action="store_true", help="do not harvest AMCR")
    parser.add_argument("--skip-teater", action="store_true", help="do not harvest TEATER")
    parser.add_argument(
        "--from-release",
        metavar="VERSION",
        help="build the CSV from atrium-keyword-extract's vocabulary of this release (e.g. v1.2.0-beta) "
        "instead of harvesting; the asset is verified against its .sha256",
    )
    parser.add_argument("--asset", metavar="PATH_OR_URL", help="with --from-release: the zip, if not the release's own")
    parser.add_argument(
        "--sha256", metavar="HEX", help="with --from-release: the digest to pin instead of the .sha256 file"
    )
    args = parser.parse_args(argv)

    if (args.asset or args.sha256) and not args.from_release:
        parser.error("--asset and --sha256 go with --from-release")
    if args.from_release:
        if args.skip_amcr or args.skip_teater:
            parser.error("--from-release takes both sources from the release; --skip-* applies to a harvest")
        try:
            records = records_from_release(args.from_release, asset=args.asset, sha256=args.sha256)
        except AssetError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return EXIT_CHECKSUM
        if not records:
            print("[WARN] The release's vocabulary has no term pairs; leaving the output file untouched.")
            return 2
        write_vocabulary_csv(records, args.out)
        return 0

    if args.skip_amcr and args.skip_teater:
        parser.error("--skip-amcr and --skip-teater together leave nothing to harvest")

    amcr = None if args.skip_amcr else harvest_amcr_records(delay=args.delay)
    teater = None if args.skip_teater else harvest_teater_records(delay=args.delay)

    records = merge_records(amcr, teater)
    if not records:
        print("[WARN] No term pairs harvested; leaving the output file untouched.")
        return 2

    write_vocabulary_csv(records, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
