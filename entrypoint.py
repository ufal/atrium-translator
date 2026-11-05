"""
entrypoint.py — the batch image's ENTRYPOINT (`python /app/entrypoint.py`).

Arguments go to the translator, as before: `docker run IMAGE /data/input --alto -o /data/output` runs
`python /app/main.py /data/input --alto -o /data/output`.

An interpreter goes to the interpreter. When the first argument names a shell or Python (`/bin/sh`,
`bash`, `python3`), it runs as given. Galaxy starts a tool's container as
`docker run … IMAGE /bin/sh …/tool_script.sh`; with every argument forwarded to main.py, `/bin/sh`
became the input path and the script an unrecognised argument, exit 2 (atrium-project#66). This is
the convention of page-classification's entrypoint.py and of the official images' docker-entrypoint
scripts.

Only the interpreters in INTERPRETERS run, unlike page-classification's entry point, which runs any
program: main.py takes a positional input, and an input file that happens to be executable (common on
files copied from a Windows share) must still be translated, not executed.

main.py is named by its absolute path. `python main.py` resolved it against the working directory,
and Galaxy starts the container in the job's directory, not /app.

The process is replaced with os.execv, no shell in between, so the program is PID 1 and receives
signals directly, as under the old exec-form `ENTRYPOINT ["python", "main.py"]`.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional, Sequence

MAIN_PY = "/app/main.py"

# The programs a first argument may name. A container job script needs no more.
INTERPRETERS = frozenset({"sh", "bash", "dash", "python", "python3"})


def command_for(args: Sequence[str], python: Optional[str] = None) -> List[str]:
    """The argv the container runs for its arguments `args`.

    The first argument runs as a program when its base name is one of INTERPRETERS and it resolves
    to an executable (an absolute path, or a name on PATH). Anything else is the translator's
    arguments.
    """
    if args and os.path.basename(args[0]) in INTERPRETERS:
        program = shutil.which(args[0])
        if program:
            return [program, *args[1:]]
    python = python or sys.executable
    return [python, MAIN_PY, *args]


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    command = command_for(args)
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
