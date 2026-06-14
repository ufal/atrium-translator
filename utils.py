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

import sys
import difflib
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

def validate_xml_with_xsd(xml_tree, xsd_url_or_path):
    """
    Validate *xml_tree* against an XSD schema.
    """
    try:
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

        xmlschema = etree.XMLSchema(xmlschema_doc)
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
        print(
            f"[WARN] AMCR namespace not detected in document; "
            f"falling back to '{_AMCR_NS_FALLBACK}'."
        )

    return xpath_ns


def process_metadata_xml(
    input_path, output_path, xpaths, translator, src_lang, tgt_lang,
    xsd_url=None, csv_writer=None, identifier=None
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

                    translated = translator.translate(
                        original_text, actual_src_lang, tgt_lang
                    )
                    elem.text = translated

                    if csv_writer:
                        doc_name = input_path.name.split(".")[0]
                        csv_writer.writerow(
                            [doc_name, "", xpath, original_text, translated]
                        )

            except etree.XPathError as e:
                print(f"[WARN] XPath error for '{xpath}': {e}")

        if xsd_url:
            print(f"[INFO] Validating {output_path.name} against XSD …")
            is_valid, error_log = validate_xml_with_xsd(tree, xsd_url)
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
        input_path, output_path, translator, src_lang, tgt_lang,
        csv_writer=None, identifier=None, line_anchors=True
):
    """
    Translate an ALTO XML document in place (dual-pass reconstruction).

    *line_anchors* (default ``True``) reproduces the original behaviour: each
    line is translated individually to anchor the alignment, costing ``1 + N``
    API calls per block. Pass ``False`` (CLI ``--fast-align``) to skip the
    per-line calls and distribute block tokens by source word count instead —
    far fewer requests at the cost of slightly coarser line splits.
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

            for block_idx, block in enumerate(text_blocks, 1):
                sys.stdout.write(f"\r[INFO] Page {page_idx}/{total_pages} | Processing block {block_idx}/{num_blocks}")
                sys.stdout.flush()

                lines = block.xpath(".//alto:TextLine", namespaces=ns) if use_ns else block.xpath(".//TextLine")

                all_strings = []
                lines_data = []

                # 1. Gather original line structures and text
                for line_idx, line in enumerate(lines, 1):
                    line_id = line.get("ID", str(line_idx))
                    strings = line.xpath(".//alto:String", namespaces=ns) if use_ns else line.xpath(".//String")

                    orig_line_text = " ".join(s.get("CONTENT", "") for s in strings if s.get("CONTENT")).strip()
                    lines_data.append({
                        "id": line_id,
                        "strings": strings,
                        "orig_text": orig_line_text,
                        # Pre-seed so CSV logging never KeyErrors on a TextLine
                        # that has no <String> children (finding #5).
                        "trans_line_text": "",
                    })
                    all_strings.extend(strings)

                # 2. Aggregate the full paragraph/block
                block_text = " ".join(ld["orig_text"] for ld in lines_data if ld["orig_text"]).strip()
                if not block_text or not all_strings:
                    continue

                # Detect language once at the block level for consistency.
                # When auto is requested but no identifier is available, default
                # to Czech — mirroring process_metadata_xml (finding #9).
                actual_src_lang = src_lang
                if src_lang == "auto":
                    if identifier:
                        detected_lang, _ = identifier.detect(block_text)
                        actual_src_lang = detected_lang
                    else:
                        actual_src_lang = "cs"

                # 3. PASS ONE: Translate the FULL block (High semantic quality)
                block_tgt = translator.translate(block_text, actual_src_lang, tgt_lang)

                # 4 + 5. Partition the block tokens into one bucket per line.
                if line_anchors:
                    # PASS TWO: translate individual lines as structural anchors.
                    line_translations = []
                    for ld in lines_data:
                        if ld["orig_text"]:
                            line_tgt = translator.translate(ld["orig_text"], actual_src_lang, tgt_lang)
                            line_translations.append(line_tgt)
                        else:
                            line_translations.append("")
                    aligned_token_buckets = _align_tokens_to_lines(block_tgt, line_translations)
                else:
                    # Anchor-free: proportional to source word counts (no API calls).
                    aligned_token_buckets = _align_tokens_proportional(
                        block_tgt, [ld["orig_text"] for ld in lines_data]
                    )

                # 6. REDISTRIBUTION: Place aligned tokens strictly within their physical lines
                for ld, assigned_tokens in zip(lines_data, aligned_token_buckets):
                    num_strings = len(ld["strings"])
                    if num_strings == 0:
                        continue

                    for i, string_elem in enumerate(ld["strings"]):
                        if i < num_strings - 1:
                            # 1-to-1 greedy mapping
                            if i < len(assigned_tokens):
                                string_elem.set("CONTENT", assigned_tokens[i])
                            else:
                                string_elem.set("CONTENT", "")
                        else:
                            # Cram any remaining text into the last string element of THIS line
                            string_elem.set("CONTENT", " ".join(assigned_tokens[i:]))

                    # Save the distributed text for CSV logging
                    ld["trans_line_text"] = " ".join(assigned_tokens)

                # 7. LOGGING: Write to the QA CSV per TextLine
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