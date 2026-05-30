"""
utils.py – ALTO and AMCR XML processing utilities for the ATRIUM translation pipeline.
"""

from lxml import etree
import urllib.request
import sys
import difflib

import xml.etree.ElementTree as ET

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
            # FIX: Added a 30-second timeout to prevent infinite network hangs
            with urllib.request.urlopen(req, timeout=30) as f:
                xmlschema_doc = etree.parse(f)
        else:
            xmlschema_doc = etree.parse(xsd_url_or_path)

        xmlschema = etree.XMLSchema(xmlschema_doc)
        if xmlschema.validate(xml_tree):
            return True, ""
        return False, xmlschema.error_log
    except Exception as e:
        return False, f"Validation error: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# AMCR XML processing
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


def process_amcr_xml(
    input_path, output_path, xpaths, translator, src_lang, tgt_lang,
    xsd_url=None, csv_writer=None, identifier=None
):
    try:
        tree = etree.parse(str(input_path))
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

        tree.write(
            str(output_path),
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=True,
        )
        print(f"[SUCCESS] Saved AMCR translation → {output_path}")

    except Exception as e:
        print(f"[ERROR] Failed to process AMCR XML '{input_path}': {e}")
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


# ──────────────────────────────────────────────────────────────────────────────
# ALTO XML processing
# ──────────────────────────────────────────────────────────────────────────────

def process_alto_xml(
        input_path, output_path, translator, src_lang, tgt_lang,
        csv_writer=None, identifier=None
):
    try:
        tree = etree.parse(str(input_path))
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
                        "orig_text": orig_line_text
                    })
                    all_strings.extend(strings)

                # 2. Aggregate the full paragraph/block
                block_text = " ".join(ld["orig_text"] for ld in lines_data if ld["orig_text"]).strip()
                if not block_text or not all_strings:
                    continue

                # Detect language once at the block level for consistency
                actual_src_lang = src_lang
                if src_lang == "auto" and identifier:
                    detected_lang, _ = identifier.detect(block_text)
                    actual_src_lang = detected_lang

                # 3. PASS ONE: Translate the FULL block (High semantic quality)
                block_tgt = translator.translate(block_text, actual_src_lang, tgt_lang)

                # 4. PASS TWO: Translate INDIVIDUAL lines (Structural anchors)
                line_translations = []
                for ld in lines_data:
                    if ld["orig_text"]:
                        line_tgt = translator.translate(ld["orig_text"], actual_src_lang, tgt_lang)
                        line_translations.append(line_tgt)
                    else:
                        line_translations.append("")

                # 5. ALIGNMENT: Partition the high-quality block tokens into line buckets
                aligned_token_buckets = _align_tokens_to_lines(block_tgt, line_translations)

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