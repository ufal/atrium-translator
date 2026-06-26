"""
utils.py – ALTO and metadata XML processing utilities for the ATRIUM translation pipeline.

Security note (review finding #2)
---------------------------------
Every input document is parsed with ``_SECURE_PARSER``, which disables external
entity resolution and network access for DTDs and caps tree size, so the tool is
safe to point at untrusted / semi-trusted XML (including files fetched by URL).
XSD schema documents (an explicit, trusted ``--xsd`` input) are parsed with
``_XSD_PARSER``, which still disables entity resolution but permits the network
access that ``xs:import``-based schemas may require.
"""

import difflib
import sys
import urllib.request

from lxml import etree

# ──────────────────────────────────────────────────────────────────────────────
# Hardened parsers
# ──────────────────────────────────────────────────────────────────────────────

# For untrusted input documents: no external entities, no network DTD fetches.
_SECURE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)

# For the trusted, explicitly-supplied XSD schema document: still refuse to
# expand entities, but allow network so xs:import/xs:include can resolve.
_XSD_PARSER = etree.XMLParser(
    resolve_entities=False,
    huge_tree=False,
)


# ──────────────────────────────────────────────────────────────────────────────
# XSD validation
# ──────────────────────────────────────────────────────────────────────────────


def load_xsd(xsd_url_or_path: str) -> "etree.XMLSchema":
    """Fetch and compile an XSD schema into an ``etree.XMLSchema`` object.

    Separating network I/O from per-file validation means the schema is
    fetched exactly once per run rather than once per document (M2).
    Raises on any error so callers can abort the run cleanly.
    """
    if not xsd_url_or_path:
        raise ValueError("xsd_url_or_path must be a non-empty string.")
    if xsd_url_or_path.startswith("http"):
        req = urllib.request.Request(
            xsd_url_or_path,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # 30-second timeout to prevent infinite network hangs.
        with urllib.request.urlopen(req, timeout=30) as f:
            xmlschema_doc = etree.parse(f, parser=_XSD_PARSER)
    else:
        xmlschema_doc = etree.parse(xsd_url_or_path, parser=_XSD_PARSER)
    return etree.XMLSchema(xmlschema_doc)


def validate_xml_with_xsd(xml_tree, xmlschema: "etree.XMLSchema") -> tuple:
    """Validate *xml_tree* against a precompiled *xmlschema*.

    Accepts an ``etree.XMLSchema`` produced by :func:`load_xsd` rather than a
    URL or path, so the caller controls when and how often the schema is
    compiled.
    """
    try:
        if xmlschema.validate(xml_tree):
            return True, ""
        return False, xmlschema.error_log
    except Exception as e:
        return False, f"Validation error: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# Metadata XML processing
# ──────────────────────────────────────────────────────────────────────────────

_AMCR_NS_FALLBACK = "https://api.aiscr.cz/schema/amcr/2.2/"


def _resolve_namespaces(root) -> dict:
    xpath_ns: dict = {}

    for elem in root.iter():
        for prefix, uri in (elem.nsmap or {}).items():
            if not uri:
                continue
            if "amcr" in uri and "amcr" not in xpath_ns:
                xpath_ns["amcr"] = uri
            if "OAI-PMH" in uri and "oai" not in xpath_ns:
                xpath_ns["oai"] = uri
        if "amcr" in xpath_ns and "oai" in xpath_ns:
            break

    if "amcr" not in xpath_ns:
        xpath_ns["amcr"] = _AMCR_NS_FALLBACK
        print(f"[WARN] AMCR namespace not detected in document; falling back to '{_AMCR_NS_FALLBACK}'.")

    return xpath_ns


def process_metadata_xml(
    input_path, output_path, xpaths, translator, src_lang, tgt_lang, xsd_schema=None, csv_writer=None, identifier=None
):
    try:
        tree = etree.parse(str(input_path), parser=_SECURE_PARSER)
        root = tree.getroot()
        xpath_ns = _resolve_namespaces(root)

        for xpath in xpaths:
            try:
                elements = root.xpath(xpath, namespaces=xpath_ns)
                for elem in elements:
                    original_text = elem.text
                    if not original_text or not original_text.strip():
                        continue

                    actual_src_lang = src_lang
                    if src_lang == "auto":
                        if identifier:
                            detected_lang, conf = identifier.detect(original_text)
                            actual_src_lang = detected_lang if conf > 0.2 else "cs"
                        else:
                            actual_src_lang = "cs"

                    translated = translator.translate(original_text, actual_src_lang, tgt_lang)
                    elem.text = translated

                    if csv_writer:
                        doc_name = input_path.name.split(".")[0]
                        csv_writer.writerow([doc_name, "", xpath, original_text, translated])

            except etree.XPathError as e:
                print(f"[WARN] XPath error for '{xpath}': {e}")

        if xsd_schema:
            print(f"[INFO] Validating {output_path.name} against XSD …")
            is_valid, error_log = validate_xml_with_xsd(tree, xsd_schema)
            if is_valid:
                print(f"[SUCCESS] XSD validation passed for {output_path.name}")
            else:
                print(f"[WARN] XSD validation failed:\n{error_log}")

        # pretty_print is intentionally OFF: it reflows whitespace and can perturb
        # significant whitespace in mixed-content elements (finding #10). Leaving
        # it off keeps the output diff minimal against the source tree.
        tree.write(
            str(output_path),
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=False,
        )
        print(f"[SUCCESS] Saved metadata translation → {output_path}")

    except Exception as e:
        print(f"[ERROR] Failed to process metadata XML '{input_path}': {e}")
        raise


# ──────────────────────────────────────────────────────────────────────────────
# ALTO XML processing Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _align_tokens_to_lines(block_text, line_translations):
    """
    Partitions the high-quality block translation into buckets corresponding
    to physical XML lines, using the lower-quality line translations as anchors.
    """
    block_tokens = block_text.split() if block_text else []
    if not block_tokens:
        return [[] for _ in line_translations]
    if len(line_translations) <= 1:
        return [block_tokens]

    assigned_buckets = []
    remaining_tokens = block_tokens

    for line_tgt in line_translations[:-1]:
        line_tokens = line_tgt.split() if line_tgt else []
        expected_len = len(line_tokens)

        # If the original line had no text, assign 0 tokens
        if expected_len == 0 or not remaining_tokens:
            assigned_buckets.append([])
            continue

        # Define a sliding window search range (+/- 50% of expected words)
        min_idx = max(0, int(expected_len * 0.5) - 1)
        max_idx = min(len(remaining_tokens), int(expected_len * 1.5) + 2)

        best_idx = 0
        best_ratio = -1.0

        # Find the split point that maximizes similarity to the line translation anchor
        for split_idx in range(min_idx, max_idx + 1):
            candidate_str = " ".join(remaining_tokens[:split_idx])
            ratio = difflib.SequenceMatcher(None, candidate_str, line_tgt).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = split_idx

        assigned_buckets.append(remaining_tokens[:best_idx])
        remaining_tokens = remaining_tokens[best_idx:]

    # The final line gets whatever tokens are left over
    assigned_buckets.append(remaining_tokens)
    return assigned_buckets


def _align_tokens_proportional(block_text, source_line_texts):
    """
    Anchor-free alternative to :func:`_align_tokens_to_lines` (review finding #4).

    Distributes the Pass-1 block tokens across physical lines in proportion to
    each line's *source* word count, so no per-line translation API call is
    needed. Honours the same invariants the reconstruction relies on:
      * token conservation (no token lost, reordered, or duplicated);
      * one bucket per line;
      * empty source line → empty bucket;
      * the final line absorbs the remainder.

    Used only when ``process_alto_xml(..., line_anchors=False)`` (the ``--fast-align``
    CLI flag). The default path still uses the similarity-anchored aligner.
    """
    block_tokens = block_text.split() if block_text else []
    if not block_tokens:
        return [[] for _ in source_line_texts]
    if len(source_line_texts) <= 1:
        return [block_tokens]

    counts = [len(t.split()) if t else 0 for t in source_line_texts]
    total = sum(counts)

    # No source words anywhere: dump everything into the last line.
    if total == 0:
        return [[] for _ in source_line_texts[:-1]] + [block_tokens]

    buckets = []
    remaining = block_tokens
    for cnt in counts[:-1]:
        if cnt == 0 or not remaining:
            buckets.append([])
            continue
        take = round(len(block_tokens) * cnt / total)
        take = max(0, min(take, len(remaining)))
        buckets.append(remaining[:take])
        remaining = remaining[take:]
    buckets.append(remaining)  # final line takes the remainder
    return buckets


# ──────────────────────────────────────────────────────────────────────────────
# ALTO XML processing
# ──────────────────────────────────────────────────────────────────────────────


def process_alto_xml(
    input_path, output_path, translator, src_lang, tgt_lang, csv_writer=None, identifier=None, line_anchors=True
):
    """
    Translate an ALTO XML document in place (dual-pass reconstruction).

    Implements Page-Level Batching (Issue #16): Pools block and line translation
    requests per page to eliminate heavy API call overhead, falling back to
    1-by-1 processing if the NMT model modifies layout boundaries.
    """
    try:
        tree = etree.parse(str(input_path), parser=_SECURE_PARSER)
        root = tree.getroot()

        nsmap = root.nsmap
        ns = {"alto": nsmap[None]} if None in nsmap else nsmap
        use_ns = "alto" in ns

        pages = root.xpath("//alto:Page", namespaces=ns) if use_ns else root.xpath("//Page")
        total_pages = len(pages)

        for page_idx, page in enumerate(pages, 1):
            text_blocks = page.xpath(".//alto:TextBlock", namespaces=ns) if use_ns else page.xpath(".//TextBlock")
            page_lines = page.xpath(".//alto:TextLine", namespaces=ns) if use_ns else page.xpath(".//TextLine")

            num_blocks = len(text_blocks)
            num_lines = len(page_lines)

            print(f"[INFO] Page {page_idx}/{total_pages} - Found {num_blocks} text blocks and {num_lines} text lines.")

            # ──────────────────────────────────────────────────────────────────
            # PHASE 1: Gather original line structures and block text
            # ──────────────────────────────────────────────────────────────────
            page_blocks_data = []
            for block_idx, block in enumerate(text_blocks, 1):
                lines = block.xpath(".//alto:TextLine", namespaces=ns) if use_ns else block.xpath(".//TextLine")

                all_strings = []
                lines_data = []

                for line_idx, line in enumerate(lines, 1):
                    line_id = line.get("ID", str(line_idx))
                    strings = line.xpath(".//alto:String", namespaces=ns) if use_ns else line.xpath(".//String")

                    orig_line_text = " ".join(s.get("CONTENT", "") for s in strings if s.get("CONTENT")).strip()
                    lines_data.append(
                        {
                            "id": line_id,
                            "strings": strings,
                            "orig_text": orig_line_text,
                            "trans_line_text": "",
                        }
                    )
                    all_strings.extend(strings)

                block_text = " ".join(ld["orig_text"] for ld in lines_data if ld["orig_text"]).strip()
                if not block_text or not all_strings:
                    continue

                actual_src_lang = src_lang
                if src_lang == "auto":
                    if identifier:
                        detected_lang, _ = identifier.detect(block_text)
                        actual_src_lang = detected_lang
                    else:
                        actual_src_lang = "cs"

                page_blocks_data.append(
                    {
                        "block_idx": block_idx,
                        "lines_data": lines_data,
                        "block_text": block_text,
                        "actual_src_lang": actual_src_lang,
                        "block_tgt": "",
                    }
                )

            if not page_blocks_data:
                continue

            # ──────────────────────────────────────────────────────────────────
            # PHASE 2: Page-Level Batch Translation (Grouped by Language)
            # ──────────────────────────────────────────────────────────────────
            lang_groups = {}
            for bdata in page_blocks_data:
                lang_groups.setdefault(bdata["actual_src_lang"], []).append(bdata)

            def _translate_batch(texts, lang):
                """Helper to join texts with newlines, translate, and validate boundaries."""
                if not texts:
                    return []

                # Filter out empty line/block placeholders to preserve spacing structures
                valid_map = [(i, t) for i, t in enumerate(texts) if t.strip()]
                if not valid_map:
                    return [""] * len(texts)

                valid_indices, valid_texts = zip(*valid_map)
                joined_text = "\n".join(valid_texts)

                try:
                    translated_joined = translator.translate(joined_text, lang, tgt_lang)
                    translated_lines = [t.strip() for t in translated_joined.split("\n")]

                    # Validate the layout structure matches original elements exactly
                    if len(translated_lines) == len(valid_texts):
                        results = [""] * len(texts)
                        for idx, res_line in zip(valid_indices, translated_lines):
                            results[idx] = res_line
                        return results
                except Exception:
                    pass

                # Safe fallback: revert to 1-by-1 requests for this batch if layout breaks
                return [translator.translate(t, lang, tgt_lang) if t.strip() else "" for t in texts]

            for lang, group in lang_groups.items():
                # Pass 1: Batch translate full blocks
                block_texts = [b["block_text"] for b in group]
                translated_blocks = _translate_batch(block_texts, lang)
                for bdata, tgt in zip(group, translated_blocks):
                    bdata["block_tgt"] = tgt

                # Pass 2: Batch translate lines as structural anchors
                if line_anchors:
                    line_texts = []
                    line_refs = []
                    for bdata in group:
                        for ld in bdata["lines_data"]:
                            line_texts.append(ld["orig_text"])
                            line_refs.append(ld)

                    translated_lines = _translate_batch(line_texts, lang)
                    for ld, tgt in zip(line_refs, translated_lines):
                        ld["line_tgt"] = tgt

            # ──────────────────────────────────────────────────────────────────
            # PHASE 3: Redistribution & Logging (Downstream Logic Untouched)
            # ──────────────────────────────────────────────────────────────────
            for bdata in page_blocks_data:
                sys.stdout.write(
                    f"\r[INFO] Page {page_idx}/{total_pages} | Processing block {bdata['block_idx']}/{num_blocks}"
                )
                sys.stdout.flush()

                block_tgt = bdata["block_tgt"]
                lines_data = bdata["lines_data"]

                if line_anchors:
                    line_translations = [ld.get("line_tgt", "") for ld in lines_data]
                    aligned_token_buckets = _align_tokens_to_lines(block_tgt, line_translations)
                else:
                    aligned_token_buckets = _align_tokens_proportional(
                        block_tgt, [ld["orig_text"] for ld in lines_data]
                    )

                for ld, assigned_tokens in zip(lines_data, aligned_token_buckets):
                    num_strings = len(ld["strings"])
                    if num_strings == 0:
                        continue

                    for i, string_elem in enumerate(ld["strings"]):
                        if i < num_strings - 1:
                            if i < len(assigned_tokens):
                                string_elem.set("CONTENT", assigned_tokens[i])
                            else:
                                string_elem.set("CONTENT", "")
                        else:
                            string_elem.set("CONTENT", " ".join(assigned_tokens[i:]))

                    ld["trans_line_text"] = " ".join(assigned_tokens)

                if csv_writer:
                    doc_name = input_path.name.split(".")[0]
                    for ld in lines_data:
                        if ld["orig_text"] or ld["trans_line_text"]:
                            csv_writer.writerow([doc_name, page_idx, ld["id"], ld["orig_text"], ld["trans_line_text"]])

            if num_blocks > 0:
                print()

        tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
        print(f"[SUCCESS] Saved ALTO translation → {output_path}")

    except Exception as e:
        print(f"\n[ERROR] Failed to process ALTO XML '{input_path}': {e}")
        raise
