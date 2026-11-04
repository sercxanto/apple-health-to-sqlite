"""The directory `import` writes: a database and Datasette's configuration.

Datasette is configured through a *directory*, not through flags: point it at a
folder holding `metadata.yml`, `settings.json` and the database, and it picks
up all three. So that folder is what `import` builds, by copying the two
configuration files out of the package next to the database. See
docs/datasette.md.

Datasette itself is never imported or started here; the package keeps needing
nothing but the standard library.
"""

from __future__ import annotations

import shutil
from pathlib import Path

CONFIG_FILES = ("metadata.yml", "settings.json")

# Datasette keys metadata on the database's filename, so the canned queries in
# metadata.yml only resolve for a database called exactly this.
DATABASE_NAME = "health.db"


class UnusableDirectory(ValueError):
    """Raised when `import` is pointed at something it must not write into."""


def config_dir() -> Path:
    """The configuration bundled with the package.

    Inside the package rather than at the repository root for the same reason
    the DTD is: so it is found both from a checkout and from an installed
    wheel.
    """
    return Path(__file__).parent / "datasette"


def database_path(directory: str | Path) -> Path:
    return Path(directory) / DATABASE_NAME


def check_target(directory: str | Path) -> None:
    """Refuse a target `import` would damage, before it changes anything.

    The copies of the configuration may be overwritten once they sit next to
    our database: that is how they stay current. Without a database, a
    `metadata.yml` is somebody else's file, in a directory that was probably
    never meant for this.
    """
    target = Path(directory)
    if target.is_file():
        raise UnusableDirectory(
            f"{target} is a file. 'import' takes a directory and creates "
            f"{DATABASE_NAME} in it."
        )
    if database_path(target).exists():
        return
    foreign = [str(target / name) for name in CONFIG_FILES if (target / name).exists()]
    if foreign:
        raise UnusableDirectory(
            f"{' and '.join(foreign)} would be overwritten, and there is no "
            f"{DATABASE_NAME} beside them to say they came from here. Choose "
            f"another directory, or delete them if they are left over."
        )


def copy_configuration(directory: str | Path) -> None:
    """Refresh the copies of the bundled configuration in `directory`."""
    for name in CONFIG_FILES:
        shutil.copyfile(config_dir() / name, Path(directory) / name)


def view_command(directory: str | Path) -> str:
    """The command that opens `directory` in Datasette, ready to paste."""
    path = str(directory)
    return f'datasette "{path}"' if " " in path else f"datasette {path}"
