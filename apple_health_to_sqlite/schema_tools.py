"""Reading and comparing the schema of a real export.

These two commands are how someone checks their own export without handing the
file to anyone: both print declarations only — element and attribute names —
never a single measurement.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import dtd
from .archive import ExportArchive


def extract_schema(
    export_path: str | os.PathLike[str], output: str | os.PathLike[str] | None = None
) -> str:
    """Return the DTD embedded in an export, optionally writing it to a file."""
    with ExportArchive(export_path) as archive, archive.open_export() as stream:
        text = dtd.extract(stream)
    if output is not None:
        Path(output).write_text(text, encoding="utf-8")
    return text


def compare_schema(export_path: str | os.PathLike[str]) -> tuple[dtd.Schema, list[str]]:
    """Compare an export's schema with the bundled one.

    Returns the export's parsed schema and the list of difference lines, which
    is empty when the two agree.
    """
    with ExportArchive(export_path) as archive, archive.open_export() as stream:
        theirs = dtd.parse(dtd.extract(stream))
    return theirs, dtd.diff(dtd.bundled(), theirs)
