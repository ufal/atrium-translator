"""
main.py – Entry point for the ATRIUM LINDAT Translation Wrapper.
"""

import argparse
import configparser
import csv
import os
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
                sys.stdout.write(f"\r[INFO] {desc}: {i}/{total} ({i / total * 100:.1f}%)")
            else:
                sys.stdout.write(f"\r[INFO] {desc}: {i} items")
            sys.stdout.flush()
            yield item
        print()


from atrium_document import DocumentRecord, canonical_doc_id, load_document, validate_document
from atrium_paradata import ParadataLogger
from processors.backend import TranslationBackend, get_backend
from processors.chunking import DEFAULT_CHUNK_SIZE
from processors.identifier import LanguageIdentifier
from utils import load_xsd, process_alto_xml, process_metadata_xml

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _build_paradata_config(args, config: configparser.ConfigParser) -> dict:
    """Return a JSON-serialisable snapshot of all run-time parameters."""
    return {
        "input_path": str(args.input_path),
        "output_dir": str(args.output or config.get("DEFAULT", "output", fallback="")),
        "source_lang": str(args.source_lang),
        "target_lang": str(args.target_lang),
        "formats": str(args.formats),
        "mode": "alto" if args.alto else "metadata",
        "translation_backend": str(getattr(args, "backend", "") or "lindat"),
        "xpaths_file": str(args.xpaths or ""),
        "xsd_url": str(args.xsd or ""),
        "vocabulary": str(args.vocabulary or ""),
        "chunk_limit": DEFAULT_CHUNK_SIZE,
        "lang_id_model": "facebook/fasttext-language-identification",
        "translation_api": "https://lindat.mff.cuni.cz/services/translation/api/v2/",
        "fasttext_confidence_threshold": 0.2,
    }


#: (atrium-project#10, D4) One-shot latch for the "validation is unavailable" warning.
#: The gate below runs once per input file and a batch run walks a whole directory, so
#: repeating the line per document would bury every other diagnostic of the run. Loud
#: once is the point; loud once per file is noise that gets filtered.
_VALIDATION_UNAVAILABLE_WARNED = False


def _doc_warn(message: str) -> None:
    """stderr, in atrium_document's own ``[document]`` voice.

    These lines interleave with the shared module's unconditional stderr diagnostics
    ("baseline … not found", "contributed no block"), and the accretion trace of a run is
    only readable if all of it lands in one stream with one prefix — hence stderr and the
    ``[document]`` tag rather than this file's usual ``[WARN]`` on stdout.
    """
    print(f"[document] WARNING – {message}", file=sys.stderr)


def _warn_validation_unavailable(reason: str) -> None:
    """(D4) The gate could not run at all. Announced ONCE, loudly, never silently.

    ``validate_document()`` deliberately raises rather than passing when ``jsonschema`` is
    absent, because a gate that quietly becomes a no-op is indistinguishable from a passing
    one. Degrading loudly preserves that property while honouring rule 3 — a missing optional
    dependency must not stop a standalone run from producing its output.
    """
    global _VALIDATION_UNAVAILABLE_WARNED
    if _VALIDATION_UNAVAILABLE_WARNED:
        return
    _VALIDATION_UNAVAILABLE_WARNED = True
    _doc_warn(
        f"schema validation is DISABLED for this run — {reason}. This is a DEGRADED gate, "
        f"not a pass: records are being written unchecked. Install the missing dependency "
        f"(requirements.txt declares jsonschema for exactly this call)."
    )


def _baseline_is_invalid(baseline: Path | None) -> bool:
    """Validate the INHERITED baseline before the translator accretes onto it (D4).

    Warns and returns True on a schema failure rather than refusing to run: the defect
    belongs to whichever upstream tool wrote it, and turning one bad record into a stalled
    pipeline is worse than passing it through (rule 6 already commits to carrying unknown
    content forward). The flag downgrades the own-output gate below from raise to warn, so
    this stage is not blamed for a defect it inherited.

    A baseline that cannot be READ at all is not this function's problem —
    ``DocumentRecord.open()`` reports on it a few lines later, with the right message.
    """
    if not baseline or not Path(baseline).exists():
        return False
    try:
        record = load_document(str(baseline))
    except Exception:
        return False
    try:
        validate_document(record)
    except (RuntimeError, FileNotFoundError) as exc:
        # RuntimeError = jsonschema missing; FileNotFoundError = the schema itself was not
        # vendored next to the module. Neither means "the record is bad".
        _warn_validation_unavailable(str(exc))
        return False
    except Exception as exc:
        _doc_warn(
            f"inherited baseline {Path(baseline).name} does not validate against "
            f"atrium_document.schema.json — {exc}. Accreting onto it anyway; this stage's "
            f"own output gate is downgraded to a warning as a result."
        )
        return True
    return False


def record_doc_id(file_path: Path, baseline: Path | None) -> str:
    """The doc_id the RECORD is keyed on — inherited from the baseline, not guessed (D1/D3).

    The translator is the one stage whose input is never the original document. ALTO
    postprocess splits pages out as ``PAGE_ALTO/<doc>/<doc>-1.alto.xml`` and that is what the
    pipeline hands us, so ``canonical_doc_id()`` — which strips pipeline SUFFIXES and knows
    nothing about page labels — correctly answers ``<doc>-1``. Correct for the file; wrong for
    the record, and the E2E gate is where that showed up (hub run 31076188660): stage 3 wrote
    ``CTX000000003-1`` into a chain whose other four stages all said ``CTX000000003``. Every
    upstream block was still carried through, but under a key nothing downstream would ever
    look up again — an orphan, which is exactly what `assert_doc_id_stable()` exists to catch.

    Stripping a trailing ``-<n>`` here would be the wrong repair: ``sbn.2019-1`` is a legal
    document name, so no filename rule can tell a page label from the document's own last
    segment. The baseline does not have to guess — the originator already wrote the answer
    into it. So: inherit when there is a baseline, fall back to `canonical_doc_id()` on the
    filename when there is not (rule 3, a standalone run has no document context to inherit).

    ``DocumentRecord`` applies the same rule to the record it writes, so this function is not
    what keeps the id honest — it is what keeps the CSV log's `file` column and the paradata
    key, both computed OUTSIDE the record, agreeing with what lands inside it.

    An unreadable or id-less baseline falls back to the filename rather than raising:
    ``DocumentRecord.open()`` reports that case a few lines later, with the right message.
    """
    derived = canonical_doc_id(file_path)
    if not baseline or not Path(baseline).exists():
        return derived
    try:
        inherited = canonical_doc_id(load_document(str(baseline)))
    except Exception:
        return derived
    return inherited or derived


def _validate_own_output(doc: DocumentRecord, baseline_was_invalid: bool) -> None:
    """The Layer D gate on the translator's own output, called before ``finalize()`` (D4).

    Raises on a schema failure so the record is never emitted — ``DocumentRecord``'s context
    manager only finalises when the body leaves without an exception, so raising here is what
    makes "no doc.json is emitted if validation fails" true. The one exception is an
    already-invalid baseline: the failure is then almost certainly the inherited one, and
    refusing to write would discard this stage's work along with the upstream stage's.
    """
    try:
        validate_document(doc.to_dict())
    except (RuntimeError, FileNotFoundError) as exc:
        _warn_validation_unavailable(str(exc))
    except Exception as exc:
        if baseline_was_invalid:
            _doc_warn(
                f"translator output for {doc.doc_id} does not validate — {exc}. Emitting it "
                f"anyway: the inherited baseline was already invalid, so this is very likely "
                f"not our defect to refuse."
            )
            return
        raise


def fetch_xml_from_url(url: str, download_dir: Path) -> Path | None:
    """
    Download a single XML URL to *download_dir* and return the local path.
    """
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        response.raise_for_status()

        raw_id = url.split("=")[-1].replace("https://api.aiscr.cz/id/", "")
        safe_name = "".join(c for c in raw_id if c.isalpha() or c.isdigit() or c in ("-", "_")).rstrip()
        local_path = download_dir / f"{safe_name}.xml"

        local_path.write_bytes(response.content)
        return local_path

    except Exception as e:
        print(f"[ERROR] Failed to download {url}: {e}")
        return None


def _read_config(config_path: Path) -> configparser.ConfigParser:
    """
    Parse *config_path* with configparser.
    """
    cfg = configparser.ConfigParser()
    if not config_path.exists():
        return cfg

    content = config_path.read_text(encoding="utf-8")
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
    """
    parser = argparse.ArgumentParser(description="ATRIUM – LINDAT Translation Wrapper (XML-focused)")
    parser.add_argument("input_path", type=Path, nargs="?", default=None)
    parser.add_argument("--output", "-o", type=Path, default=None)
    parser.add_argument(
        "--source_lang",
        "-src",
        type=str,
        default=None,
        help="Source language code (e.g. cs, fr) or 'auto' for detection. Default: value from config.txt, or 'cs'.",
    )
    parser.add_argument(
        "--target_lang",
        "-tgt",
        type=str,
        default=None,
        help="Target language code (e.g. en). Default: 'en'.",
    )
    parser.add_argument(
        "--formats",
        type=str,
        default=None,
        help="Comma-separated list of file extensions to process (e.g. alto.xml,txt).",
    )
    parser.add_argument("--config", "-c", type=Path, default=Path("config.txt"))
    parser.add_argument("--alto", action="store_true", help="Enable ALTO XML in-place translation mode.")
    parser.add_argument("--xpaths", type=Path, default=None, help="Path to a file listing AMCR XPath targets.")
    parser.add_argument("--xsd", type=str, default=None, help="URL or path to XSD schema for output validation.")
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=None,
        help="Path to a CSV vocabulary file (source_lemma,target_translation).",
    )
    parser.add_argument(
        "--document-json",
        type=Path,
        default=None,
        help="Optional baseline ATRIUM Document JSON to append to (accretion model).",
    )
    parser.add_argument(
        "--document-json-out",
        type=Path,
        default=None,
        help="Destination path for the updated ATRIUM Document JSON.",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=None,
        help="Directory for URL-ingested inputs (default: <output>/downloaded_inputs).",
    )
    parser.add_argument(
        "--fast-align",
        action="store_true",
        help="ALTO only: distribute block tokens by source word count instead of "
        "translating each line as an anchor. Far fewer API calls; slightly "
        "coarser line splits.",
    )
    parser.add_argument(
        "--backend",
        type=str,
        default=None,
        help="Translation backend to use: 'lindat' (default, LINDAT CUBBITT) or "
        "'openai_compatible' (free/low-cost OpenAI-compatible LLM API, configured "
        "via LLM_* env vars). Default: config 'translation_backend', then env "
        "TRANSLATION_BACKEND, then 'lindat'. See docs/translation-backends.md.",
    )

    args = parser.parse_args()
    config = _read_config(args.config)
    defaults = config["DEFAULT"] if "DEFAULT" in config else {}

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
    if args.backend is None:
        args.backend = defaults.get("translation_backend") or os.environ.get("TRANSLATION_BACKEND") or "lindat"
    if args.xpaths is None and "fields" in defaults:
        args.xpaths = Path(defaults["fields"])

    if args.vocabulary is None and "vocabulary" in defaults:
        vocab_candidate = Path(defaults["vocabulary"])
        if vocab_candidate.exists():
            args.vocabulary = vocab_candidate

    if not args.alto and args.formats and "alto.xml" in args.formats.lower():
        args.alto = True

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


def process_single_file(
    file_path: Path,
    output_file: Path,
    args: argparse.Namespace,
    translator: TranslationBackend,
    identifier: LanguageIdentifier | None,
    xpaths_list: list[str],
    _logger: ParadataLogger,
    xsd_schema=None,
) -> tuple[bool, int]:
    """
    Process a single XML file (ALTO or metadata).
    Returns a tuple: (success: bool, protected_count: int)

    *xsd_schema* is a precompiled ``etree.XMLSchema`` (or ``None``).
    It is compiled once in ``main()`` via ``load_xsd`` rather than
    per-file to avoid redundant network round-trips (M2).
    """
    translator.reset_protected_count()

    # D3 (atrium-project#10): one derivation for the whole run, through the shared module.
    # The old `file_path.name.split(".")[0]` agreed with canonical_doc_id() only by luck of
    # the sample naming convention — a doc_id with an embedded dot (`CTX01.v2.alto.xml`)
    # truncated to `CTX01` here while every other tool kept `CTX01.v2`, forking this repo's
    # record away from the rest of the pipeline for the same physical document.
    #
    # `file_key` names what THIS INVOCATION READ; `doc_id` names the DOCUMENT the record is
    # keyed on, and the two are not the same thing whenever the input is a page split out of
    # a multi-page original — which, in the ecosystem pipeline, is always (see
    # record_doc_id). The CSV log keeps the per-FILE name because it holds per-line rows and
    # a document-level name would make page 2 of a batch truncate page 1's log. The record
    # takes `doc_id` everywhere: in its key, in its default filename (rule 1's
    # `<doc_id>.document.json`, and what DocumentRecord.finalize() would pick on its own), in
    # the log's `file` column, and in the paradata key — a record whose NAME disagreed with
    # the id INSIDE it is the same class of defect as the fork this derivation now avoids.
    file_key = canonical_doc_id(file_path)
    doc_id = record_doc_id(file_path, args.document_json)
    csv_log_path = output_file.with_name(f"{file_key}_log.csv")
    paradata_ref = str(Path(_logger.paradata_dir) / f"{_logger.run_id}_{_logger.program}.json")

    doc_json_out = args.document_json_out or output_file.with_name(f"{doc_id}.document.json")
    success = False

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
            # D4: validate what we INHERITED before writing onto it. Warn-and-continue, but
            # remember the verdict — it decides whether our own gate below raises or warns.
            baseline_was_invalid = _baseline_is_invalid(args.document_json)

            with DocumentRecord.open(
                doc_id=doc_id,
                program="translator",
                baseline=args.document_json,
                run_id=_logger.run_id,
                paradata_ref=paradata_ref,
            ) as doc:
                if args.alto:
                    process_alto_xml(
                        file_path,
                        output_file,
                        translator,
                        args.source_lang,
                        args.target_lang,
                        csv_writer,
                        identifier,
                        line_anchors=not args.fast_align,
                        doc=doc,
                        backend=args.backend,
                        doc_id=doc_id,
                    )
                else:
                    process_metadata_xml(
                        file_path,
                        output_file,
                        xpaths_list,
                        translator,
                        args.source_lang,
                        args.target_lang,
                        xsd_schema=xsd_schema,
                        csv_writer=csv_writer,
                        identifier=identifier,
                        doc=doc,
                        backend=args.backend,
                        doc_id=doc_id,
                    )

                # Append derived step outputs and licenses to the accretion model
                doc.add_derived_from("translated_xml", output_file.name)
                doc.add_license_detail(_logger.get_license_block())

                # D4: the Layer D gate, at this repo's single document-write chokepoint.
                # Raising here (rather than after finalize()) is what makes "no doc.json is
                # emitted if validation fails" true: DocumentRecord.__exit__ only finalises a
                # body that left without an exception.
                _validate_own_output(doc, baseline_was_invalid)

                doc.finalize(str(doc_json_out))

            _logger.log_success("xml")
            _logger.log_success("csv")
            if args.document_json or args.document_json_out:
                _logger.log_success("json")
            success = True

        except Exception as e:
            print(f"[ERROR] Failed processing '{file_path.name}': {e}")
            _logger.log_skip(str(file_path), str(e))

    protected = translator.protected_count if translator.vocabulary else 0
    return success, protected


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

    out_dir = args.output or Path.cwd() / f"translated_{args.target_lang}"
    out_dir.mkdir(parents=True, exist_ok=True)

    with ParadataLogger(
        program="translator",
        config=_build_paradata_config(args, config),
        paradata_dir=str(out_dir / "paradata"),
        output_types=["xml", "csv", "json"],
    ) as _logger:
        if not args.alto and not args.xpaths:
            print("[ERROR] Specify either the --alto flag or provide --xpaths / 'fields' in config.")
            return

        translator = get_backend(args.backend, vocab_path=args.vocabulary)
        identifier = LanguageIdentifier() if args.source_lang == "auto" else None

        if identifier is not None:
            _logger.log_component("fasttext")

        _components_logged = False
        protected_by_doc: dict[str, int] = {}

        xpaths_list: list[str] = []
        if args.xpaths and args.xpaths.exists():
            with open(args.xpaths, "r", encoding="utf-8") as f:
                xpaths_list = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        # M2: compile the XSD schema once before the batch loop.
        # The old design re-fetched from the network on every file, which is
        # wasteful and fragile. Any load failure is fatal — abort cleanly here
        # rather than silently skipping validation inside each file's handler.
        xsd_schema = None
        if args.xsd:
            print(f"[INFO] Compiling XSD schema from {args.xsd} …")
            try:
                xsd_schema = load_xsd(args.xsd)
            except Exception as exc:
                print(f"[ERROR] XSD schema load failed: {exc}")
                return

        # ── Collect files to process ───────────────────────────────────
        files_to_process: list[Path] = []
        allowed_formats = [fmt.strip() for fmt in args.formats.split(",")]

        if input_path.is_file() and input_path.suffix == ".txt" and "txt" in allowed_formats:
            print("[INFO] Text file detected – reading URLs …")
            with open(input_path, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f if line.strip() and line.startswith("http")]

            download_dir = args.download_dir or (out_dir / "downloaded_inputs")
            download_dir.mkdir(parents=True, exist_ok=True)

            for url in urls:
                print(f"[INFO] Downloading: {url}")
                local_file = fetch_xml_from_url(url, download_dir)
                if local_file:
                    files_to_process.append(local_file)

        elif input_path.is_dir():
            for fmt in allowed_formats:
                pattern = f"*.{fmt}" if not fmt.startswith(".") else f"*{fmt}"
                files_to_process.extend(f for f in input_path.rglob(pattern) if f.is_file())
            files_to_process = list(dict.fromkeys(files_to_process))

        else:
            if any(input_path.name.endswith(fmt) for fmt in allowed_formats):
                files_to_process = [input_path]
            else:
                print(f"[WARN] Input file '{input_path.name}' does not match allowed formats: {args.formats}")

        if not files_to_process:
            print(f"[WARN] No files found matching allowed formats ({args.formats}).")
            return

        # ── Process each file ──────────────────────────────────────────
        total_inputs = len(files_to_process)
        is_batch = input_path.is_dir() or (input_path.suffix == ".txt")

        for i, file_path in enumerate(files_to_process, 1):
            print(f"\n[FILE {i}/{total_inputs}] Processing: {file_path.name}")
            output_file = generate_output_path(file_path, out_dir, args, is_batch=is_batch)

            success, protected = process_single_file(
                file_path=file_path,
                output_file=output_file,
                args=args,
                translator=translator,
                identifier=identifier,
                xpaths_list=xpaths_list,
                _logger=_logger,
                xsd_schema=xsd_schema,
            )

            if success and not _components_logged:
                # Record the components the *selected* backend actually exercised
                # (issue #4). Backends expose license_components(vocab_loaded);
                # fall back to the historical LINDAT set for any backend that
                # predates the method, so paradata licensing stays correct after
                # a backend swap instead of hard-coding lindat_cubbitt.
                vocab_loaded = bool(getattr(translator, "vocabulary", None))
                components_fn = getattr(translator, "license_components", None)
                if callable(components_fn):
                    for comp in components_fn(vocab_loaded):
                        _logger.log_component(comp)
                else:
                    _logger.log_component("lindat_cubbitt")
                    if vocab_loaded:
                        for comp in (
                            "udpipe2_engine",
                            "udpipe2_models",
                            "amcr_vocab",
                            "teater_data",
                        ):
                            _logger.log_component(comp)
                _components_logged = True

            if translator.vocabulary:
                # D3: the paradata key must be the same doc_id the record and the CSV log
                # use, or `vocabulary_protected_terms` cannot be joined back to a document.
                # Same derivation as process_single_file's, baseline included — keyed on the
                # page a document was split into, this map joins to nothing.
                doc_name = record_doc_id(file_path, args.document_json)
                protected_by_doc[doc_name] = protected
                if getattr(translator, "supports_glossary", False):
                    print(f"[INFO] Prompt glossary: {protected} term(s) applied in {file_path.name}")
                else:
                    print(f"[INFO] Tag-and-Protect: {protected} term(s) protected in {file_path.name}")

        if protected_by_doc:
            self_cfg = getattr(_logger, "config", None)
            if isinstance(self_cfg, dict):
                self_cfg["vocabulary_protected_terms"] = dict(protected_by_doc)
                self_cfg["vocabulary_protected_terms_total"] = sum(protected_by_doc.values())

        _logger.finalize(input_total=total_inputs)

    print(f"\n{'=' * 60}")
    print(" PROCESSING COMPLETE ".center(60, "="))
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
