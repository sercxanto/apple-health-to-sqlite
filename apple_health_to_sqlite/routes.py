"""GPX workout routes into `workout_points`.

Outdoor workouts store their track in `workout-routes/*.gpx`, referenced from
the `<WorkoutRoute><FileReference path="…"/>` inside `Export.xml`. Matching is
by filename, case-insensitively, because the reference and the archive entry do
not always agree on case.

Apple writes speed, course and accuracy into `<extensions>`; depending on iOS
version those children sit in the GPX namespace or in a Garmin
TrackPointExtension namespace, so namespaces are stripped rather than matched.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree

from .archive import ExportArchive
from .importer import Importer, as_number
from .timestamps import parse_gpx_time

GPX_NAMESPACE = "{http://www.topografix.com/GPX/1/1}"
TRACK_POINT = GPX_NAMESPACE + "trkpt"


def import_routes(importer: Importer, archive: ExportArchive) -> int:
    """Import every GPX file that belongs to an already-imported workout.

    Returns the number of route files imported. Routes whose workout was
    filtered out (by `--since`) are skipped, not orphaned.
    """
    imported = 0
    for member in archive.route_members():
        filename = member.rsplit("/", 1)[-1].lower()
        workout_id = importer.route_owners.get(filename)
        if workout_id is None:
            continue
        with archive.open(member) as stream:
            _import_one(importer, workout_id, stream)
        imported += 1
    importer.flush()
    return imported


def _import_one(importer: Importer, workout_id: int, stream) -> None:
    seq = 0
    for _event, element in ElementTree.iterparse(stream, events=("end",)):
        if element.tag != TRACK_POINT:
            continue
        values = {
            child.tag.rpartition("}")[2]: child.text
            for child in element.iter()
            if child is not element and child.text
        }
        time_text = values.get("time")
        importer.add(
            "workout_points",
            (
                workout_id,
                seq,
                as_number(element.get("lat")),
                as_number(element.get("lon")),
                as_number(values.get("ele")),
                time_text,
                parse_gpx_time(time_text),
                as_number(values.get("speed")),
                as_number(values.get("course")),
                as_number(values.get("hAcc")),
                as_number(values.get("vAcc")),
            ),
        )
        seq += 1
        element.clear()
