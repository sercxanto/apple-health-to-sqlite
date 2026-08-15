"""Locating files inside an Apple Health export.

An export is a zip whose contents are, in practice, less predictable than they
look:

* The main file is spelled `Export.xml` on some devices and `export.xml` on
  others. Same for the enclosing `apple_health_export/` directory.
* People unzip the archive before pointing a tool at it, so a directory has to
  work as well as a zip.
* `export_cda.xml` sits right next to `Export.xml` and must not be mistaken
  for it.

This module hides all of that behind one small class, so the rest of the code
can just ask for the export stream and the route files.
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO

EXPORT_BASENAME = "export.xml"
CDA_BASENAME = "export_cda.xml"
ROUTES_DIRECTORY = "workout-routes"


class ExportNotFound(Exception):
    """Raised when an archive contains no recognisable `Export.xml`."""


class ExportArchive:
    """A Health export, opened from a zip, a directory, or a bare XML file.

    Member lookup is case-insensitive throughout.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._zip: zipfile.ZipFile | None = None
        self._single_file: Path | None = None
        self._members: dict[str, str] = {}

        if not self.path.exists():
            raise FileNotFoundError(f"No such export: {self.path}")

        if self.path.is_dir():
            for entry in sorted(self.path.rglob("*")):
                if entry.is_file():
                    relative = entry.relative_to(self.path).as_posix()
                    self._members[relative.lower()] = relative
        elif zipfile.is_zipfile(self.path):
            self._zip = zipfile.ZipFile(self.path)
            for name in self._zip.namelist():
                self._members[name.lower()] = name
        elif self.path.suffix.lower() == ".xml":
            self._single_file = self.path
        else:
            raise ExportNotFound(
                f"{self.path} is neither a zip archive, a directory, nor an XML file."
            )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> ExportArchive:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- members -----------------------------------------------------------

    @property
    def export_member(self) -> str:
        """Path of the main XML file inside the archive."""
        if self._single_file is not None:
            return self._single_file.name

        candidates = [
            actual
            for lowered, actual in self._members.items()
            if lowered.rsplit("/", 1)[-1] == EXPORT_BASENAME
        ]
        if not candidates:
            near_miss = sorted(
                actual
                for lowered, actual in self._members.items()
                if lowered.endswith(".xml")
            )
            hint = f" Found instead: {', '.join(near_miss[:5])}" if near_miss else ""
            raise ExportNotFound(
                f"No Export.xml inside {self.path}. Point this at the zip the "
                f"Health app produced, or at the directory you unzipped it "
                f"into.{hint}"
            )
        # Shallowest path wins, so a stray copy in a subdirectory cannot
        # shadow the real one.
        return min(candidates, key=lambda name: (name.count("/"), len(name)))

    def open_export(self) -> IO[bytes]:
        """Open the main XML file for streaming."""
        return self.open(self.export_member)

    def open(self, member: str) -> IO[bytes]:
        if self._single_file is not None:
            return open(self._single_file, "rb")
        if self._zip is not None:
            return self._zip.open(self._members.get(member.lower(), member))
        return open(self.path / self._members.get(member.lower(), member), "rb")

    def route_members(self) -> list[str]:
        """Paths of the GPX workout routes, if the export has any."""
        return sorted(
            actual
            for lowered, actual in self._members.items()
            if f"{ROUTES_DIRECTORY}/" in lowered and lowered.endswith(".gpx")
        )

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._members.values()))
