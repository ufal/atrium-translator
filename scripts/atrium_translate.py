#!/usr/bin/env python3
"""Zero-dependency client for the ATRIUM Translator API.

Uploads ALTO XML pages or AMCR metadata XML files to a running instance of the
FastAPI service in `service/api.py` and saves the translated XML the server
returns (local server by default, remote via --base-url or the ATRIUM_TR_URL
env variable).

Only the Python 3 standard library is used - no pip installs required.

Usage:
    python3 scripts/atrium_translate.py page.alto.xml
    python3 scripts/atrium_translate.py record.xml --no-alto --source-lang cs
    python3 scripts/atrium_translate.py page.alto.xml --target-lang de -o out.xml
    python3 scripts/atrium_translate.py page.alto.xml -o -          # XML to stdout
    python3 scripts/atrium_translate.py --info

    # ATRIUM Document JSON accretion (docs/document_schema.md, issue #13): upload a
    # baseline and get it back with `translations` / `entities[].translation_en` updated,
    # delivered alongside the XML as a multipart/mixed response
    python3 scripts/atrium_translate.py page.alto.xml --document-json in.document.json \
        --document-json-out-file out.document.json

Exit codes:
    0 - success
    1 - client-side error (bad arguments, unreadable file)
    2 - server unreachable (connection refused / timeout)
    3 - server-side error (HTTP 4xx/5xx)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

DEFAULT_BASE_URL = os.environ.get("ATRIUM_TR_URL", "http://localhost:8000")
MAX_UPLOAD_MB = 50  # mirrors the server's MAX_UPLOAD_MB default
RETRY_STATUS = {502, 503, 504}
RETRY_ATTEMPTS = 3
RETRY_WAIT_S = 10


def build_multipart(files: dict) -> tuple[bytes, str]:
    """Encode one or more files as multipart/form-data using only the stdlib.

    `files` maps the form field name to a `Path` (e.g. `{"file": page.xml,
    "document_json": baseline.json}` for the accretion contract).
    """
    boundary = uuid.uuid4().hex
    lines = []
    for field_name, file_path in files.items():
        content_type = b"Content-Type: application/json" if field_name == "document_json" else b"Content-Type: application/xml"
        lines.append(f"--{boundary}".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"'.encode()
        )
        lines.append(content_type)
        lines.append(b"")
        lines.append(file_path.read_bytes())
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def http_request(url: str, data: bytes = None, content_type: str = None, timeout: int = 900):
    """POST (or GET when data is None); returns (bytes, headers), retrying 502/503/504."""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), dict(response.headers)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code in RETRY_STATUS and attempt < RETRY_ATTEMPTS:
                print(
                    f"[retry {attempt}/{RETRY_ATTEMPTS}] HTTP {e.code}, waiting {RETRY_WAIT_S}s...",
                    file=sys.stderr,
                )
                time.sleep(RETRY_WAIT_S)
                last_error = f"HTTP {e.code}: {detail}"
                continue
            print(f"Server error - HTTP {e.code}: {detail}", file=sys.stderr)
            sys.exit(3)
        except (urllib.error.URLError, TimeoutError) as e:
            print(
                f"Cannot reach the API at {url} ({e}).\nIs the server running? Start it with: bash scripts/server.sh",
                file=sys.stderr,
            )
            sys.exit(2)
    print(f"Server error after {RETRY_ATTEMPTS} attempts - {last_error}", file=sys.stderr)
    sys.exit(3)


def attachment_name(headers: dict, fallback: str) -> str:
    """Extract the filename from a Content-Disposition header."""
    disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    match = re.search(r'filename="?([^";]+)"?', disposition)
    return match.group(1) if match else fallback


def split_multipart_mixed(content: bytes, content_type: str) -> "dict[str, tuple[str, bytes]]":
    """Split a `multipart/mixed` response body into {content_type: (filename, bytes)}.

    Only what `/translate`'s accretion path (§ document_json) actually produces: one
    `application/xml` part and one `application/json` part, each with a Content-Disposition
    filename. Returns {} if `content_type` is not multipart/mixed.
    """
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        return {}
    boundary = ("--" + match.group(1)).encode()
    parts = {}
    for chunk in content.split(boundary)[1:-1]:
        chunk = chunk.strip(b"\r\n")
        if not chunk:
            continue
        header_blob, _, body = chunk.partition(b"\r\n\r\n")
        headers = {}
        for line in header_blob.decode("utf-8", errors="replace").splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip()] = value.strip()
        ctype = headers.get("Content-Type", "").split(";")[0].strip()
        fname = attachment_name(headers, fallback=f"part.{ctype.split('/')[-1]}")
        parts[ctype] = (fname, body.rstrip(b"\r\n"))
    return parts


def translate_file(
    base_url: str,
    path: Path,
    source_lang: str,
    target_lang: str,
    is_alto: bool,
    document_json: Optional[Path] = None,
):
    """Upload one XML (and optional document_json baseline) to POST /translate.

    Returns (xml_bytes, server_filename, document_record_or_None).
    """
    if path.suffix.lower() != ".xml":
        print(f"Skipping {path}: only .xml files are supported", file=sys.stderr)
        return None, None, None
    size = path.stat().st_size
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        print(
            f"Skipping {path}: {size} bytes exceeds the {MAX_UPLOAD_MB} MB server upload limit - "
            "split the document first",
            file=sys.stderr,
        )
        return None, None, None

    query = urllib.parse.urlencode(
        {"source_lang": source_lang, "target_lang": target_lang, "is_alto": str(is_alto).lower()}
    )
    files = {"file": path}
    if document_json is not None:
        files["document_json"] = document_json
    body, content_type = build_multipart(files)
    content, headers = http_request(f"{base_url}/translate?{query}", data=body, content_type=content_type)

    response_type = headers.get("Content-Type") or headers.get("content-type") or ""
    if response_type.startswith("multipart/mixed"):
        parts = split_multipart_mixed(content, response_type)
        xml_name, xml_bytes = parts.get("application/xml", (None, None))
        _, json_bytes = parts.get("application/json", (None, None))
        fallback = f"{path.stem}_{target_lang}{path.suffix}"
        record = json.loads(json_bytes.decode("utf-8")) if json_bytes else None
        return xml_bytes, (xml_name or fallback), record

    fallback = f"{path.stem}_{target_lang}{path.suffix}"
    return content, attachment_name(headers, fallback), None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="ALTO XML page(s) or AMCR metadata XML file(s) to translate")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default: {DEFAULT_BASE_URL}, env: ATRIUM_TR_URL)"
    )
    parser.add_argument(
        "--source-lang", default="auto", help="source language ISO code, or 'auto' for detection (default: auto)"
    )
    parser.add_argument("--target-lang", default="en", help="target language ISO code (default: en)")
    alto_group = parser.add_mutually_exclusive_group()
    alto_group.add_argument(
        "--alto", dest="alto", action="store_true", default=True, help="treat input as ALTO XML (default)"
    )
    alto_group.add_argument(
        "--no-alto", dest="alto", action="store_false", help="treat input as AMCR metadata XML instead"
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help="output path for the translated XML ('-' for stdout); only with a single input file. "
        "Default: save next to the current directory under the server-proposed name",
    )
    parser.add_argument("--info", action="store_true", help="print service capabilities and limits, then exit")
    parser.add_argument(
        "--document-json",
        metavar="PATH",
        help="baseline ATRIUM Document JSON to accrete this tool's translations/entities[].translation_en "
        "onto (docs/document_schema.md); requires exactly one input file",
    )
    parser.add_argument(
        "--document-json-out-file",
        metavar="PATH",
        help="save the returned document_json record to PATH (default: next to the XML output, "
        "using the server-proposed name)",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    if args.info:
        content, _ = http_request(f"{base_url}/info", timeout=60)
        print(json.dumps(json.loads(content.decode("utf-8")), indent=2))
        return

    if not args.files:
        parser.error("no input files given (or use --info)")
    if args.output and len(args.files) != 1:
        parser.error("-o/--output requires exactly one input file")

    document_json_path = None
    if args.document_json:
        if len(args.files) != 1:
            parser.error("--document-json accretes onto a single document; pass exactly one input file")
        document_json_path = Path(args.document_json)
        if not document_json_path.is_file():
            print(f"--document-json file not found: {document_json_path}", file=sys.stderr)
            sys.exit(1)
    if args.document_json_out_file and document_json_path is None:
        parser.error("--document-json-out-file requires --document-json (translator accretes, it does not originate)")

    paths = [Path(f) for f in args.files]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print(f"File(s) not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        sys.exit(1)

    produced = 0
    for path in paths:
        content, out_name, record = translate_file(
            base_url,
            path,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            is_alto=args.alto,
            document_json=document_json_path,
        )
        if content is None:
            continue
        if args.output == "-":
            sys.stdout.write(content.decode("utf-8", errors="replace"))
        else:
            out_path = Path(args.output) if args.output else Path(out_name)
            out_path.write_bytes(content)
            print(f"Translated XML saved to {out_path}")
        if record is not None:
            record_path = Path(args.document_json_out_file) if args.document_json_out_file else Path(
                f"{path.stem}.document.json"
            )
            record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            print(f"Document JSON record written to {record_path}")
        produced += 1

    if not produced:
        print("No results produced.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
