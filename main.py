"""
main.py – Entry point for the ATRIUM LINDAT Translation Wrapper.

Corrections applied:
  - Removed duplicate `import configparser` (was imported twice)
  - Replaced fragile regex-based config preprocessing with native configparser
    read; a [DEFAULT] header is prepended only when the file lacks one,
    preventing bracket characters in values from being silently stripped
  - Fixed source_lang / target_lang default handling: argparse defaults are now
    None so that config-file values are never shadowed by the parser's own
    fallback string
  - ParadataLogger is now used as a context manager so finalize() is guaranteed
    to run on every exit path (including early returns and uncaught exceptions)
  - Removed the redundant second mkdir for out_dir (was created twice)
  - Raised exceptions from process_*_xml so that the per-file error handler in
    main() receives them (utils.py now re-raises after printing)
"""

import argparse
import configparser
import csv
import sys
from pathlib import Path

import requests

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        total = kwargs.get("total", len(iterable) if hasattr(iterable, "__len__") else None)
        desc = kwargs.get("desc", "Processing")
        for i, item in enumerate(iterable, 1):
            if total:
                sys.stdout.write(
                    f"\r[INFO] {desc}: {i}/{total} ({i / total * 100:.1f}%)"
                )
            else:
                sys.stdout.write(f"\r[INFO] {desc}: {i} items")
            sys.stdout.flush()
            yield item
        print()

from atrium_paradata import ParadataLogger
from processors.identifier import LanguageIdentifier
from processors.translator import LindatTranslator
from utils import process_alto_xml, process_amcr_xml


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_paradata_config(args, config: configparser.ConfigParser) -> dict:
    """Return a JSON-serialisable snapshot of all run-time parameters."""
    return {
        "input_path":                    str(args.input_path),
        "output_dir":                    str(args.output or config.get("DEFAULT", "output", fallback="")),
        "source_lang":                   str(args.source_lang),
        "target_lang":                   str(args.target_lang),
        "formats":                       str(args.formats),
        "mode":                          "alto" if args.alto else "amcr",
        "xpaths_file":                   str(args.xpaths or ""),
        "xsd_url":                       str(args.xsd or ""),
        "vocabulary":                    str(args.vocabulary or ""),
        "chunk_limit":                   4000,
        "lang_id_model":                 "facebook/fasttext-language-identification",
        "translation_api":               "https://lindat.mff.cuni.cz/services/translation/api/v2/",
        "fasttext_confidence_threshold": 0.2,
    }


def fetch_xml_from_url(url: str, download_dir: Path) -> Path | None:
    """
    Download a single XML URL to *download_dir* and return the local path.

    The filename is derived from the OAI identifier at the end of the URL
    (everything after the last ``=`` sign, with the AISCR base URI stripped).
    Returns ``None`` if the download fails.
    """
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        response.raise_for_status()

        # URL ends with …identifier=https://api.aiscr.cz/id/C-TX-202400594
        raw_id = url.split("=")[-1].replace("https://api.aiscr.cz/id/", "")
        safe_name = "".join(
            c for c in raw_id if c.isalpha() or c.isdigit() or c in ("-", "_")
        ).rstrip()
        local_path = download_dir / f"{safe_name}.xml"

        local_path.write_bytes(response.content)
        return local_path

    except Exception as e:
        print(f"[ERROR] Failed to download {url}: {e}")
        return None


def _read_config(config_path: Path) -> configparser.ConfigParser:
    """
    Parse *config_path* with configparser.

    If the file does not start with a section header (legacy flat format),
    a ``[DEFAULT]`` header is prepended in-memory so the parser accepts it.
    This approach is safe for values that contain ``[`` or ``]`` characters.
    """
    cfg = configparser.ConfigParser()
    if not config_path.exists():
        return cfg

    content = config_path.read_text(encoding="utf-8")
    # Detect whether the first non-blank, non-comment line is a section header
    has_header = any(
        line.strip().startswith("[")
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    if not has_header:
        content = "[DEFAULT]\n" + content

    cfg.read_string(content)
    return cfg


def parse_arguments():
    """
    Parse CLI arguments and merge with config-file defaults.

    Precedence (highest → lowest): CLI flag > config file > built-in default.

    Note: ``--source_lang`` and ``--target_lang`` use ``None`` as the argparse
    default so that a config-file entry is never overridden by the parser's own
    fallback string.
    """
    parser = argparse.ArgumentParser(
        description="ATRIUM – LINDAT Translation Wrapper (XML-focused)"
    )
    parser.add_argument("input_path", type=Path, nargs="?", default=None)
    parser.add_argument("--output", "-o", type=Path, default=None)
    parser.add_argument(
        "--source_lang", "-src", type=str, default=None,
        help="Source language code (e.g. cs, fr) or 'auto' for detection. "
             "Default: value from config.txt, or 'cs'.",
    )
    parser.add_argument(
        "--target_lang", "-tgt", type=str, default=None,
        help="Target language code (e.g. en). Default: 'en'.",
    )
    parser.add_argument(
        "--formats", type=str, default=None,
        help="Comma-separated list of file extensions to process (e.g. alto.xml,txt).",
    )
    parser.add_argument("--config", "-c", type=Path, default=Path("config.txt"))
    parser.add_argument("--alto", action="store_true",
                        help="Enable ALTO XML in-place translation mode.")
    parser.add_argument("--xpaths", type=Path, default=None,
                        help="Path to a file listing AMCR XPath targets.")
    parser.add_argument("--xsd", type=str, default=None,
                        help="URL or path to XSD schema for output validation.")
    parser.add_argument(
        "--vocabulary", type=Path, default=None,
        help="Path to a CSV vocabulary file (source_lemma,target_translation).",
    )

    args = parser.parse_args()
    config = _read_config(args.config)
    defaults = config["DEFAULT"] if "DEFAULT" in config else {}

    # Merge config-file values for any argument still set to None
    if args.input_path is None and "input_path" in defaults:
        args.input_path = Path(defaults["input_path"])
    if args.output is None and "output" in defaults:
        args.output = Path(defaults["output"])
    if args.source_lang is None:
        args.source_lang = defaults.get("source_lang", "cs")
    if args.target_lang is None:
        args.target_lang = defaults.get("target_lang", "en")
    if args.formats is None:
        args.formats = defaults.get("formats", "xml")
    if args.xpaths is None and "fields" in defaults:
        args.xpaths = Path(defaults["fields"])
    # Vocabulary: CLI flag takes precedence; fall back to config entry.
    if args.vocabulary is None and "vocabulary" in defaults:
        vocab_candidate = Path(defaults["vocabulary"])
        if vocab_candidate.exists():
            args.vocabulary = vocab_candidate

    return args, config


def generate_output_path(input_file: Path, base_output: Path, args, is_batch: bool = False) -> Path:
    """Build the destination path for a translated XML file."""
    if input_file.name.endswith(".alto.xml"):
        base_name = input_file.name[: -len(".alto.xml")]
        new_filename = f"{base_name}_{args.target_lang}.alto.xml"
    else:
        new_filename = f"{input_file.stem}_{args.target_lang}{input_file.suffix}"

    if is_batch:
        return base_output / new_filename
    if base_output:
        if base_output.is_dir():
            return base_output / new_filename
        return base_output
    return input_file.with_name(new_filename)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    args, config = parse_arguments()

    print(f"\n{'=' * 60}")
    print(" ATRIUM XML TRANSLATOR ".center(60, "="))
    print(f"{'=' * 60}")

    input_path = args.input_path
    if not input_path or (not input_path.is_dir() and not input_path.is_file()):
        print("[ERROR] Input path does not exist. Provide a valid file or directory.")
        return

    # Use the logger as a context manager so finalize() is always called,
    # even on early returns or unexpected exceptions.
    with ParadataLogger(
        program="translator",
        config=_build_paradata_config(args, config),
        paradata_dir="paradata",
        output_types=["xml", "csv"],
    ) as _logger:

        if not args.alto and not args.xpaths:
            print(
                "[ERROR] Specify either the --alto flag or provide "
                "--xpaths / 'fields' in config."
            )
            return

        # Build translator and (optionally) language identifier
        translator = LindatTranslator(vocab_path=args.vocabulary)
        identifier = LanguageIdentifier() if args.source_lang == "auto" else None

        # Load XPath targets for AMCR mode
        xpaths_list: list[str] = []
        if args.xpaths and args.xpaths.exists():
            with open(args.xpaths, "r", encoding="utf-8") as f:
                xpaths_list = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.startswith("#")
                ]

        # ── Collect files to process ───────────────────────────────────
        files_to_process: list[Path] = []
        allowed_formats = [fmt.strip() for fmt in args.formats.split(",")]

        out_dir = args.output or Path.cwd() / f"translated_{args.target_lang}"
        out_dir.mkdir(parents=True, exist_ok=True)

        if input_path.is_file() and input_path.suffix == ".txt" and "txt" in allowed_formats:
            print("[INFO] Text file detected – reading URLs …")
            with open(input_path, "r", encoding="utf-8") as f:
                urls = [
                    line.strip()
                    for line in f
                    if line.strip() and line.startswith("http")
                ]

            input_save_dir = Path("data_samples/my_documents")
            input_save_dir.mkdir(parents=True, exist_ok=True)

            for url in urls:
                print(f"[INFO] Downloading: {url}")
                local_file = fetch_xml_from_url(url, input_save_dir)
                if local_file:
                    files_to_process.append(local_file)

        elif input_path.is_dir():
            for fmt in allowed_formats:
                pattern = f"*.{fmt}" if not fmt.startswith(".") else f"*{fmt}"
                files_to_process.extend(
                    f for f in input_path.rglob(pattern) if f.is_file()
                )
            # Deduplicate (a file could match multiple format patterns)
            files_to_process = list(dict.fromkeys(files_to_process))

        else:
            if any(input_path.name.endswith(fmt) for fmt in allowed_formats):
                files_to_process = [input_path]
            else:
                print(
                    f"[WARN] Input file '{input_path.name}' does not match "
                    f"allowed formats: {args.formats}"
                )

        if not files_to_process:
            print(
                f"[WARN] No files found matching allowed formats ({args.formats})."
            )
            return

        # ── Process each file ──────────────────────────────────────────
        total_inputs = len(files_to_process)
        is_batch = input_path.is_dir() or (input_path.suffix == ".txt")

        for i, file_path in enumerate(files_to_process, 1):
            print(f"\n[FILE {i}/{total_inputs}] Processing: {file_path.name}")
            output_file = generate_output_path(
                file_path, out_dir, args, is_batch=is_batch
            )

            # CSV log lives beside the translated XML, named <stem>_log.csv
            csv_log_path = output_file.with_name(
                f"{file_path.name.split('.')[0]}_log.csv"
            )
            with open(csv_log_path, "w", encoding="utf-8", newline="") as csv_file:
                csv_writer = csv.writer(csv_file)
                csv_writer.writerow(
                    [
                        "file",
                        "page_num",
                        "line_num",
                        f"text_{args.source_lang}",
                        f"text_{args.target_lang}",
                    ]
                )

                try:
                    if args.alto:
                        process_alto_xml(
                            file_path,
                            output_file,
                            translator,
                            args.source_lang,
                            args.target_lang,
                            csv_writer,
                            identifier,
                        )
                    else:
                        process_amcr_xml(
                            file_path,
                            output_file,
                            xpaths_list,
                            translator,
                            args.source_lang,
                            args.target_lang,
                            args.xsd,
                            csv_writer,
                            identifier,
                        )

                    _logger.log_success("xml")
                    _logger.log_success("csv")

                except Exception as e:
                    print(f"[ERROR] Failed processing '{file_path.name}': {e}")
                    _logger.log_skip(str(file_path), str(e))

        # Explicit finalize with the correct input count; the context manager
        # __exit__ will no-op because _finalised is already True.
        _logger.finalize(input_total=total_inputs)

    print(f"\n{'=' * 60}")
    print(" PROCESSING COMPLETE ".center(60, "="))
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()