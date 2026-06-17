"""
tests/test_main.py
Orchestration coverage for CLI arg merging, file handling, and configuration defaults.
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from main import parse_arguments, generate_output_path


def test_parse_arguments_cli_overrides_config(tmp_path):
    # Verify CLI inputs override file defaults but accept safe fallbacks
    config_path = tmp_path / "config.txt"
    config_path.write_text("source_lang = cs\ntarget_lang = de\nformats = xml\n", encoding="utf-8")

    test_args = ["main.py", str(tmp_path), "--source_lang", "auto", "-c", str(config_path)]
    with patch("sys.argv", test_args):
        args, config = parse_arguments()

        assert args.source_lang == "auto"  # CLI beat config
        assert args.target_lang == "de"  # Config fallback
        assert args.formats == "xml"  # Config fallback


def test_parse_arguments_handles_legacy_config(tmp_path):
    # Verify legacy flat-format config files prepend [DEFAULT] cleanly
    config_path = tmp_path / "legacy_config.txt"
    config_path.write_text("output = /custom/out\nformats = alto.xml,txt\n", encoding="utf-8")

    test_args = ["main.py", str(tmp_path), "-c", str(config_path)]
    with patch("sys.argv", test_args):
        args, config = parse_arguments()

        assert str(args.output) == "/custom/out"
        assert args.alto is True  # Automatically enabled by format detection


def test_generate_output_path_alto_xml():
    # Verifies ALTO-specific naming conventions
    args = MagicMock(target_lang="en")
    input_file = Path("page_1.alto.xml")
    base_output = Path("/out")

    out_path = generate_output_path(input_file, base_output, args, is_batch=True)
    assert out_path == Path("/out/page_1_en.alto.xml")


def test_generate_output_path_metadata_xml():
    # Verifies standard XML fallback naming
    args = MagicMock(target_lang="en")
    input_file = Path("record_001.xml")
    base_output = Path("/out")

    out_path = generate_output_path(input_file, base_output, args, is_batch=True)
    assert out_path == Path("/out/record_001_en.xml")