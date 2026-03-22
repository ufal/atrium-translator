"""
utils.py – ALTO and AMCR XML processing utilities for the ATRIUM translation pipeline.
"""

from lxml import etree
import urllib.request
import sys


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

        for page_idx, page in enumerate(pages, 1):
            text_lines = page.xpath(".//alto:TextLine", namespaces=ns) if use_ns else page.xpath(".//TextLine")
            total_lines = len(text_lines)

            for line_idx, line in enumerate(text_lines, 1):
                sys.stdout.write(f"\r[INFO] Page {page_idx} | Line {line_idx}/{total_lines}")
                sys.stdout.flush()

                line_id = line.get("ID", str(line_idx))
                strings = line.xpath(".//alto:String", namespaces=ns) if use_ns else line.xpath(".//String")
                if not strings:
                    continue

                line_text = " ".join(s.get("CONTENT", "") for s in strings if s.get("CONTENT")).strip()
                if not line_text:
                    continue

                actual_src_lang = src_lang
                if src_lang == "auto" and identifier:
                    detected_lang, _ = identifier.detect(line_text)
                    actual_src_lang = detected_lang

                translated_text = translator.translate(line_text, actual_src_lang, tgt_lang)

                if csv_writer:
                    doc_name = input_path.name.split(".")[0]
                    csv_writer.writerow([doc_name, page_idx, line_id, line_text, translated_text])

                trans_words = translated_text.split()

                # FIX: Safeguard against empty translations dropping division by zero
                if not trans_words:
                    continue

                num_strings = len(strings)
                if num_strings == 0:
                    continue

                words_per_string = len(trans_words) // num_strings
                remainder = len(trans_words) % num_strings
                word_idx = 0

                for i, string_elem in enumerate(strings):
                    count = words_per_string + (1 if i < remainder else 0)
                    string_elem.set(
                        "CONTENT", " ".join(trans_words[word_idx: word_idx + count])
                    )
                    word_idx += count

            if total_lines > 0:
                print()

        tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
        print(f"[SUCCESS] Saved ALTO translation → {output_path}")

    except Exception as e:
        print(f"\n[ERROR] Failed to process ALTO XML '{input_path}': {e}")
        raise