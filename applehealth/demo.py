"""Assembling a directory Datasette can serve, and handing it over.

Datasette is configured through a *directory*, not through flags: point it at a
folder holding `metadata.yml`, `settings.json` and the database, and it picks
up all three. So serving means building that folder first — copying the two
configuration files out of the package next to the database. See
docs/datasette.md.

This used to be `mkdir -p`, `cp` and a `datasette` invocation in the Makefile,
which is three things a Windows machine does not have. Doing it in Python means
the task runner only ever has to invoke Python.

Datasette itself is a dev dependency and is never imported here — it runs as a
subprocess, so the package keeps needing nothing but the standard library.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

CONFIG_FILES = ("metadata.yml", "settings.json")

# Datasette keys metadata on the database's filename, so the canned queries in
# metadata.yml only resolve for a database called exactly this.
DATABASE_NAME = "health.db"

DEFAULT_DIRECTORY = "demo"
DEFAULT_PORT = 8001


class DatasetteMissing(RuntimeError):
    """Raised instead of letting `python -m datasette` fail with a traceback."""


def config_dir() -> Path:
    """The configuration bundled with the package.

    Inside the package rather than at the repository root for the same reason
    the DTD is: so it is found both from a checkout and from an installed
    wheel.
    """
    return Path(__file__).parent / "datasette"


def prepare(directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Create the serving directory and refresh its configuration."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        shutil.copyfile(config_dir() / name, target / name)
    return target


def database_path(directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    return Path(directory) / DATABASE_NAME


def datasette_command() -> list[str]:
    """How Datasette is invoked on this machine, or raise `DatasetteMissing`.

    The two installations CONTRIBUTING.md offers need two different answers,
    and neither can stand in for the other. `pip install` puts Datasette in
    the environment we are running in, where `-m` reaches it through the same
    interpreter as everything else. `pipx install datasette` — the route for
    people who would rather not have a virtualenv — puts it in a virtualenv of
    its own and only its script on the PATH, so importing it from here fails
    however well `datasette` works in the same shell.

    Hence: prefer the module, because it pins the interpreter, and fall back
    to the script, because with pipx that is all there is.
    """
    if find_spec("datasette") is not None:
        return [sys.executable, "-m", "datasette"]
    if (executable := shutil.which("datasette")) is not None:
        return [executable]
    raise DatasetteMissing(
        "datasette is not installed. It is a development dependency: "
        'pip install -e ".[dev]" from a checkout, pip install datasette, '
        "or pipx install datasette."
    )


def require_datasette() -> None:
    """Check for Datasette before doing work whose only point is to serve it."""
    datasette_command()


def serve(directory: str | Path = DEFAULT_DIRECTORY, port: int = DEFAULT_PORT) -> int:
    """Serve `directory` with Datasette, returning its exit code."""
    database = database_path(directory)
    if not database.exists():
        raise FileNotFoundError(
            f"{database} does not exist. Build it with "
            f"'python -m applehealth demo {directory}', or import an export "
            f"into that path yourself — the filename has to be {DATABASE_NAME}."
        )
    datasette = datasette_command()

    target = prepare(directory)
    print(f"Serving {target}/ on http://localhost:{port}/ — Ctrl-C to stop")
    command = [*datasette, str(target), "-p", str(port)]
    try:
        return subprocess.run(command).returncode
    except KeyboardInterrupt:
        return 0
