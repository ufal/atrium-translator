"""
tests/test_container_entrypoint.py — the batch image's ENTRYPOINT (entrypoint.py).

Arguments go to /app/main.py, as they always did; a first argument that names a shell or Python
runs as given, which is how Galaxy starts a tool's container (`docker run IMAGE /bin/sh
tool_script.sh`, atrium-project#66). main.py takes a positional input, so a file that is merely
executable must still be translated. Pure-Python checks: no Docker.
"""

from __future__ import annotations

import shutil

import pytest

import entrypoint
from atrium_test_support import REPO_ROOT


def test_options_and_a_positional_input_go_to_the_translator():
    args = ["/data/input", "--alto", "--target_lang", "en", "-o", "/data/output"]
    assert entrypoint.command_for(args, python="py") == ["py", "/app/main.py", *args]


def test_no_arguments_run_the_translator():
    assert entrypoint.command_for([], python="py") == ["py", "/app/main.py"]


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell on this runner")
def test_galaxy_style_command_runs_as_given():
    shell = shutil.which("/bin/sh") or shutil.which("sh")
    command = entrypoint.command_for([shell, "/tmp/job/tool_script.sh"], python="py")
    assert command == [shell, "/tmp/job/tool_script.sh"]


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 not on PATH")
def test_an_interpreter_name_on_path_runs_as_given():
    command = entrypoint.command_for(["python3", "/app/main.py", "--help"], python="py")
    assert command == [shutil.which("python3"), "/app/main.py", "--help"]


def test_an_executable_input_file_is_translated_not_run(tmp_path):
    page = tmp_path / "page.alto.xml"
    page.write_text("<alto/>", encoding="utf-8")
    page.chmod(0o755)
    assert entrypoint.command_for([str(page), "--alto"], python="py") == ["py", "/app/main.py", str(page), "--alto"]


@pytest.mark.skipif(shutil.which("ls") is None, reason="no ls on this runner")
def test_a_program_that_is_no_interpreter_goes_to_the_translator():
    # `ls` is on PATH, but an input directory may be called that too: only interpreters run.
    assert entrypoint.command_for(["ls", "-o", "out"], python="py") == ["py", "/app/main.py", "ls", "-o", "out"]


def test_an_input_directory_goes_to_the_translator(tmp_path):
    folder = tmp_path / "sh"
    folder.mkdir()
    assert entrypoint.command_for([str(folder)], python="py") == ["py", "/app/main.py", str(folder)]


def test_main_execs_the_command(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint.os, "execv", lambda path, argv: calls.append((path, argv)))
    entrypoint.main(["in.xml", "--alto"])
    assert calls[0][1][1:] == ["/app/main.py", "in.xml", "--alto"]
    assert calls[0][0] == calls[0][1][0]

    calls.clear()
    shell = shutil.which("sh")
    if shell:
        entrypoint.main([shell, "-c", "true"])
        assert calls == [(shell, [shell, "-c", "true"])]


def test_the_dockerfile_runs_this_file():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    base = dockerfile.split("FROM base AS api")[0] if "FROM base AS api" in dockerfile else dockerfile
    assert 'ENTRYPOINT ["python", "/app/entrypoint.py"]' in base
    assert 'ENTRYPOINT ["python", "main.py"]' not in dockerfile
