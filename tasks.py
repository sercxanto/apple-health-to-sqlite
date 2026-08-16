"""The development tasks, called from `[tool.poe.tasks]` in pyproject.toml.

Why Python functions and not plain command strings: a `cmd` task looks up its
executable on the PATH, and `python` is not on the PATH on every machine that
has Python — a Homebrew install offers only `python3`, a Windows one only
`python`. Poe resolves that for `script` tasks (python, then python3, then its
own interpreter) and not for `cmd` tasks, so anything that runs Python runs
from here. Ruff is invoked as a `cmd` task, because `ruff` is `ruff`
everywhere.

Everything below therefore shells out through `sys.executable`, which by then
is the interpreter poe resolved. The other half of the portability story is in
`applehealth/demo.py`: the file shuffling `demo` and `serve` need is Python, not
`mkdir -p` and `cp`.

Not part of the package — pyproject.toml lists its packages explicitly, so this
file is never installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).parent

# Directories that are output, never source. `demo/` holds a synthesized export
# and its database, both rebuildable with `poe demo`.
ARTIFACTS = ("demo", "build", "dist", ".ruff_cache")


def _python(*arguments: str) -> NoReturn:
    """Run `python ...` with the interpreter poe resolved, and exit with its status.

    `SystemExit` and not `return`: poe discards whatever a script task returns,
    so a returned exit code would make every task green however the child ended.
    That failure mode is invisible — a red test suite reported as a pass.
    """
    completed = subprocess.run([sys.executable, *arguments], cwd=ROOT)
    raise SystemExit(completed.returncode)


def _extra_args() -> list[str]:
    """Whatever followed the task name: `poe serve -- --port 9000`.

    The `--` keeps poe from claiming flags of its own (`-v` is poe's verbosity),
    and poe hands it on to us, so drop it before argparse sees it as its own
    end-of-options marker and reads `--port` as a positional.
    """
    arguments = sys.argv[1:]
    return arguments[1:] if arguments[:1] == ["--"] else arguments


def test() -> None:
    _python("-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v")


def test_slow() -> None:
    """Only the large-export test — what the `slow` CI job runs."""
    _python("-m", "unittest", "tests.test_large_export", "-v")


def demo() -> None:
    _python("-m", "applehealth", "demo", *_extra_args())


def serve() -> None:
    _python("-m", "applehealth", "serve", *_extra_args())


def refresh_catalog() -> None:
    """Rebuild `applehealth/catalog/` from Apple's HealthKit documentation.

    The only task here that goes near the network, and the only one whose
    output is committed rather than thrown away. Review the diff: it is the
    documentation moving, not code.
    """
    _python("tools/refresh_catalog.py", *_extra_args())


def clean() -> None:
    """Remove build artefacts, caches and the demo directory."""
    removed = []
    for name in ARTIFACTS:
        target = ROOT / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(name)

    for pattern in ("*.egg-info", "**/__pycache__"):
        for target in ROOT.glob(pattern):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                removed.append(str(target.relative_to(ROOT)))

    print(f"Removed {len(removed)} path(s)" if removed else "Nothing to remove")
