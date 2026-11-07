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

from .archive import DamagedExport, ExportArchive
from .importer import Importer, as_number
from .timestamps import parse_gpx_time

GPX_NAMESPACE = "{http://www.topografix.com/GPX/1/1}"
TRACK_POINT = GPX_NAMESPACE + "trkpt"


def import_routes(importer: Importer, archive: ExportArchive) -> int:
    """Import the route files of every already-imported workout.

    Returns the number of route files imported. Files whose workout was filtered
    out (by `--since`) or that no workout names are left alone, and the archive
    reports them. A file that is named but absent or damaged goes into the result:
    one bad route is no reason to give up a whole export, and its points are
    dropped whole, because half a track reads like a shorter run.
    """
    members = {
        member.rsplit("/", 1)[-1].lower(): member for member in archive.route_members()
    }
    imported = 0
    for workout_id, files in importer.route_references.items():
        # Read before anything is written, so one workout's points, and not the
        # whole export's, are in memory at a time.
        tracks = []
        for name in files:
            member = members.get(name.lower())
            if member is None:
                importer.result.routes_missing.append(name)
                continue
            try:
                with archive.open(member) as stream:
                    tracks.append(_read_points(stream))
            except DamagedExport:
                importer.result.routes_damaged.append(name)
        imported += len(tracks)

        # Version 0.1.0 kept the last of several routes, from seq 0, which is now
        # where the first one belongs. Replaced only when every route is at hand,
        # so that an incomplete export cannot cost points already in the database.
        if len(files) > 1 and len(tracks) == len(files):
            importer.connection.execute(
                "DELETE FROM workout_points WHERE workout_id = ?", (workout_id,)
            )

        seq = 0
        for points in tracks:
            for point in points:
                importer.add("workout_points", (workout_id, seq, *point))
                seq += 1
    importer.flush()
    return imported


def _read_points(stream) -> list[tuple]:
    """The columns of `workout_points` after its key, for each point of a track."""
    points = []
    for _event, element in ElementTree.iterparse(stream, events=("end",)):
        if element.tag != TRACK_POINT:
            continue
        values = {
            child.tag.rpartition("}")[2]: child.text
            for child in element.iter()
            if child is not element and child.text
        }
        time_text = values.get("time")
        points.append(
            (
                as_number(element.get("lat")),
                as_number(element.get("lon")),
                as_number(values.get("ele")),
                time_text,
                parse_gpx_time(time_text),
                as_number(values.get("speed")),
                as_number(values.get("course")),
                as_number(values.get("hAcc")),
                as_number(values.get("vAcc")),
            )
        )
        element.clear()
    return points
