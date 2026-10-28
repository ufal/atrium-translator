"""
tests/test_release_bundle.py — the published zip must be able to start.

`.github/workflows/release.yml` builds the GitHub release artifact from a
hand-maintained `cp` list, and that list had fallen behind the code. It omitted
`atrium_document.py` (imported at `main.py:31`), `atrium_document.schema.json`
(resolved next to that module at runtime), `service/atrium_service.py` (imported
at `service/api.py:29`) and `service/healthcheck.py`. For anyone not using the
container the zip is the primary download path, and it raised
`ModuleNotFoundError` on first run — build/release/run failing in the direction
that matters (atrium-project#53, factor V).

The point of these tests is that they cannot rot the way the list did: they read
the list out of the workflow itself and recompute the import closure from the
source, so the next canonical module to land is a red test here rather than a
broken download months later.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from atrium_test_support import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
ENTRY_POINTS = ("main.py", "service/api.py")


def _bundle_paths() -> list[str]:
    """The repo-relative paths release.yml copies into dist/."""
    spec = yaml.safe_load(WORKFLOW.read_text())
    steps = spec["jobs"]["create-release"]["steps"]
    build = next(s for s in steps if s.get("name") == "Build deployment package")

    paths: list[str] = []
    for line in build["run"].splitlines():
        line = line.strip()
        if not line.startswith("cp "):
            continue
        # `cp [-r] SRC... DEST` — drop the command, the flags and the destination.
        tokens = [t for t in line.split()[1:] if not t.startswith("-")]
        paths.extend(tokens[:-1])
    return paths


def _bundled_files() -> set[str]:
    """Expand the cp list to the set of files that end up in the zip."""
    files: set[str] = set()
    for entry in _bundle_paths():
        source = REPO_ROOT / entry
        if source.is_dir():
            files.update(str(f.relative_to(REPO_ROOT)) for f in source.rglob("*.py"))
        else:
            files.add(entry)
    return files


def _import_closure() -> set[str]:
    """Every repo-local .py reachable by import from the two entry points."""
    local_modules = {p.stem for p in REPO_ROOT.glob("*.py")}
    packages = {"processors", "service"}
    seen: set[str] = set()

    def walk(rel: str) -> None:
        if rel in seen:
            return
        path = REPO_ROOT / rel
        if not path.exists():
            return
        seen.add(rel)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # `from .atrium_service import ...`
                    sibling = Path(rel).parent / f"{(node.module or '').split('.')[0]}.py"
                    walk(str(sibling))
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in local_modules:
                    walk(f"{name}.py")
                elif name in packages:
                    for module in sorted((REPO_ROOT / name).glob("*.py")):
                        walk(str(module.relative_to(REPO_ROOT)))

    for entry in ENTRY_POINTS:
        walk(entry)
    return seen


def test_bundle_covers_the_whole_import_closure():
    missing = _import_closure() - _bundled_files()
    assert not missing, (
        "release.yml's cp list does not ship these modules, which the entry points import: "
        f"{sorted(missing)}. The published zip would raise ModuleNotFoundError."
    )


@pytest.mark.parametrize(
    "data_file",
    [
        "atrium_document.schema.json",  # atrium_document.py resolves it beside itself
        "para_config.txt",  # atrium_paradata.py reads [tool] version from it
        "config.txt",  # main.py's --config default
        "requirements.txt",  # nothing installs without it
    ],
)
def test_bundle_ships_runtime_data_files(data_file):
    assert data_file in _bundle_paths(), f"{data_file} is needed at runtime but is not in the release bundle"


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "dist"
    for entry in _bundle_paths():
        source = REPO_ROOT / entry
        target = stage / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    return stage


@pytest.mark.parametrize("entry", ["import main", "import service.api"])
def test_staged_bundle_imports(tmp_path, entry):
    """Stage exactly what release.yml copies, then import it from a clean cwd.

    Asserting the file list is necessary but not sufficient — a data file resolved
    by a relative path fails only when something actually runs. This runs it.
    """
    stage = _stage(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", entry],
        cwd=stage,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"`{entry}` failed inside the release bundle:\n{result.stderr}"


def _image_python() -> str:
    """The Python the images run: the Dockerfile's `FROM python:X.Y...`."""
    match = re.search(r"^FROM python:(\d+\.\d+)", (REPO_ROOT / "Dockerfile").read_text(), re.MULTILINE)
    assert match, "the Dockerfile no longer starts from a python:X.Y image; update this test"
    return match.group(1)


def test_the_bundle_is_installed_with_the_python_of_the_images():
    """v1.6.0-beta installed the bundle with the runner's own Python, 3.14 by then, which
    fasttext-wheel 0.9.2 has no wheel for: the images were published, the release was not.
    Every release step that runs `pip install` must follow a setup-python of the images' version.
    """
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["create-release"]["steps"]
    python, installs = None, []
    for step in steps:
        if str(step.get("uses", "")).startswith("actions/setup-python"):
            python = str(step.get("with", {}).get("python-version"))
        elif "pip install" in step.get("run", ""):
            installs.append((step.get("name"), python))
    assert installs, "the release job installs nothing any more; update this test"
    wrong = [(name, found) for name, found in installs if found != _image_python()]
    assert not wrong, (
        f"these release steps install packages with Python {wrong}, not the images' {_image_python()}: "
        "move the setup-python step above them"
    )
