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

DEFAULT_BASE_URL = os.environ.get("ATRIUM_TR_URL", "http://localhost:8000")
MAX_UPLOAD_MB = 50  # mirrors the server's MAX_UPLOAD_MB default
RETRY_STATUS = {502, 503, 504}
RETRY_ATTEMPTS = 3
RETRY_WAIT_S = 10


def build_multipart(file_field: str, file_path: Path) -> tuple[bytes, str]:
    """Encode one file as multipart/form-data using only the stdlib."""
    boundary = uuid.uuid4().hex
    lines = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"'.encode(),
        b"Content-Type: application/xml",
        b"",
        file_path.read_bytes(),
        f"--{boundary}--".encode(),
        b"",
    ]
    body = b"\r\n".join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


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


def translate_file(base_url: str, path: Path, source_lang: str, target_lang: str, is_alto: bool):
    """Upload one XML to POST /translate; returns (xml_bytes, server_filename)."""
    if path.suffix.lower() != ".xml":
        print(f"Skipping {path}: only .xml files are supported", file=sys.stderr)
        return None, None
    size = path.stat().st_size
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        print(
            f"Skipping {path}: {size} bytes exceeds the {MAX_UPLOAD_MB} MB server upload limit - "
            "split the document first",
            file=sys.stderr,
        )
        return None, None

    query = urllib.parse.urlencode(
        {"source_lang": source_lang, "target_lang": target_lang, "is_alto": str(is_alto).lower()}
    )
    body, content_type = build_multipart(file_field="file", file_path=path)
    content, headers = http_request(f"{base_url}/translate?{query}", data=body, content_type=content_type)
    fallback = f"{path.stem}_{target_lang}{path.suffix}"
    return content, attachment_name(headers, fallback)


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

    paths = [Path(f) for f in args.files]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print(f"File(s) not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        sys.exit(1)

    produced = 0
    for path in paths:
        content, out_name = translate_file(
            base_url, path, source_lang=args.source_lang, target_lang=args.target_lang, is_alto=args.alto
        )
        if content is None:
            continue
        if args.output == "-":
            sys.stdout.write(content.decode("utf-8", errors="replace"))
        else:
            out_path = Path(args.output) if args.output else Path(out_name)
            out_path.write_bytes(content)
            print(f"Translated XML saved to {out_path}")
        produced += 1

    if not produced:
        print("No results produced.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
