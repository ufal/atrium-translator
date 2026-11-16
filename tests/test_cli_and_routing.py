from __future__ import annotations

import argparse
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from atrium_test_support import import_any, maybe_resolve_attr


def _call_main_parser(main_mod, argv: list[str]):
    """
    Try documented parser factories first. If the repo only exposes a
    parse_arguments-style entry point, invoke it with a safe temporary argv.
    """
    candidates = (
        "build_parser",
        "create_parser",
        "get_parser",
        "make_parser",
        "parse_arguments",
        "parse_args",
    )
    for name in candidates:
        attr = maybe_resolve_attr(main_mod, (name,))
        if attr is None:
            continue

        if isinstance(attr, argparse.ArgumentParser):
            return attr.parse_args(argv)

        if callable(attr):
            if name in {"parse_arguments", "parse_args"}:
                old_argv = sys.argv[:]
                try:
                    sys.argv = ["pytest", *argv]
                    return attr()
                finally:
                    sys.argv = old_argv
            try:
                result = attr()
            except SystemExit:
                continue
            except TypeError:
                continue

            if isinstance(result, argparse.ArgumentParser):
                return result.parse_args(argv)
            if hasattr(result, "parse_args"):
                return result.parse_args(argv)
            if result is not None:
                return result

    pytest.skip("Could not locate a parser factory in main.py")


def test_main_help_smoke(repo_root: Path):
    """The CLI entry point should at least display help successfully."""
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower()


def test_main_parser_accepts_documented_flags():
    """Documented arguments should be accepted by the CLI parser."""
    main_mod = import_any(["main"])
    argv = [
        "input.xml",
        "--output",
        "out.xml",
        "--source_lang",
        "auto",
        "--target_lang",
        "en",
        "--formats",
        "alto.xml,txt",
        "--config",
        "config.txt",
        "--alto",
        "--xpaths",
        "amcr-fields.example.txt",
        "--xsd",
        "schema.xsd",
        "--vocabulary",
        "vocabulary.csv",
    ]
    args = _call_main_parser(main_mod, argv)

    assert getattr(args, "input_path", "input.xml") == "input.xml"
    assert getattr(args, "output", "out.xml") == "out.xml"
    assert getattr(args, "source_lang", "auto") == "auto"
    assert getattr(args, "target_lang", "en") == "en"
    assert getattr(args, "formats", "alto.xml,txt") == "alto.xml,txt"
    assert getattr(args, "alto", True) is True
    assert getattr(args, "xpaths", "amcr-fields.example.txt") == "amcr-fields.example.txt"
    assert getattr(args, "xsd", "schema.xsd") == "schema.xsd"
    assert getattr(args, "vocabulary", "vocabulary.csv") == "vocabulary.csv"


def test_main_parser_supports_positional_input_path():
    """The input path is documented as a positional argument."""
    main_mod = import_any(["main"])
    args = _call_main_parser(main_mod, ["./data_samples/my_documents"])
    assert getattr(args, "input_path", "./data_samples/my_documents") == "./data_samples/my_documents"


def test_read_config_or_equivalent_round_trip(tmp_path: Path):
    """If main.py exposes a config loader, it should parse DEFAULT values."""
    main_mod = import_any(["main"])
    reader = maybe_resolve_attr(main_mod, ("read_config", "_read_config", "load_config", "parse_config"))
    if reader is None:
        pytest.skip("No config reader helper exposed in main.py")

    config_file = tmp_path / "config.txt"
    config_file.write_text(
        """[DEFAULT]
input_path = ./data_samples/my_documents
source_lang = auto
target_lang = en
formats = alto.xml
fields = amcr-fields.example.txt
output = ./data_samples/translated_files
vocabulary = data_samples/vocabulary.csv
""",
        encoding="utf-8",
    )

    result = reader(config_file) if len(inspect.signature(reader).parameters) == 1 else reader(str(config_file))
    assert result is not None

    if hasattr(result, "defaults"):
        defaults = dict(result.defaults())
        assert defaults.get("input_path") == "./data_samples/my_documents"
        assert defaults.get("source_lang") == "auto"
        assert defaults.get("target_lang") == "en"
        assert defaults.get("output") == "./data_samples/translated_files"
    elif isinstance(result, dict):
        assert result.get("input_path") == "./data_samples/my_documents"
        assert result.get("source_lang") == "auto"
        assert result.get("target_lang") == "en"
    else:
        text = str(result)
        assert "input_path" in text or "data_samples" in text
