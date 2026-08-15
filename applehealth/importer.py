"""Streaming import of `Export.xml` into SQLite.

The export is parsed with `iterparse` straight off the zip stream and the tree
is discarded after every top-level element, so memory stays flat whether the
file is 50 MB or 5 GB. Rows are buffered per table and written with
`executemany`.

Every element declared in the bundled DTD has a handler here. Anything else —
an element Apple adds in a future export version — is counted, stored raw in
the `unhandled` table, and reported at the end of the import. Silence would be
the worse failure mode: you would not notice missing data until you went
looking for it.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import IO

from . import db, timestamps

#: Prefixes stripped to produce the friendlier `type_short` column.
TYPE_PREFIXES = (
    "HKQuantityTypeIdentifier",
    "HKCategoryTypeIdentifier",
    "HKCorrelationTypeIdentifier",
    "HKCharacteristicTypeIdentifier",
    "HKDataTypeIdentifier",
    "HKWorkoutActivityType",
    "HKWorkoutEventType",
    "HKCategoryValueSleepAnalysis",
)

#: Top-level elements, each with a dedicated handler.
TOP_LEVEL_ELEMENTS = frozenset(
    {
        "Record",
        "Correlation",
        "Workout",
        "ActivitySummary",
        "ClinicalRecord",
        "Audiogram",
        "VisionPrescription",
    }
)

#: Elements consumed by the handler of their parent.
CHILD_ELEMENTS = frozenset(
    {
        "MetadataEntry",
        "HeartRateVariabilityMetadataList",
        "InstantaneousBeatsPerMinute",
        "WorkoutEvent",
        "WorkoutStatistics",
        "WorkoutActivity",
        "WorkoutRoute",
        "FileReference",
        "SensitivityPoint",
        "RightEye",
        "LeftEye",
        "Attachment",
    }
)

#: Elements carrying export-level information rather than measurements.
HEADER_ELEMENTS = frozenset({"HealthData", "ExportDate", "Me"})

#: Everything the importer knows about. `tests/test_dtd.py` asserts this covers
#: every element declared in the bundled schema.
HANDLED_ELEMENTS = TOP_LEVEL_ELEMENTS | CHILD_ELEMENTS | HEADER_ELEMENTS

#: Cap on rows kept in `unhandled`; the counter keeps counting past it.
UNHANDLED_LIMIT = 500

_DEVICE_NAME_RE = re.compile(r"name:([^,>]+)")
_device_cache: dict[str, str | None] = {}


def short_type(value: str | None) -> str | None:
    """Strip the `HK…Identifier` prefix that fronts every type name."""
    if not value:
        return value
    for prefix in TYPE_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def device_name(device: str | None) -> str | None:
    """Pull the readable name out of Apple's device description string.

    `<<HKDevice: 0x…>, name:Apple Watch, manufacturer:Apple Inc., …>` becomes
    `Apple Watch`.
    """
    if not device:
        return None
    if device not in _device_cache:
        match = _DEVICE_NAME_RE.search(device)
        _device_cache[device] = match.group(1).strip() if match else None
    return _device_cache[device]


def as_number(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def split_value(value: str | None) -> tuple[float | None, str | None]:
    """Separate numeric readings from enumerated ones.

    Most records carry a number, but category records carry names such as
    `HKCategoryValueSleepAnalysisAsleepREM`. Keeping them in one column would
    force every query to cast.
    """
    if value is None:
        return None, None
    try:
        return float(value), None
    except ValueError:
        return None, value


@dataclass
class ImportOptions:
    """Filters and switches for a single import run."""

    types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()
    since: str | None = None
    routes: bool = True
    derived: bool = True
    progress: bool = True


@dataclass
class ImportResult:
    """What an import did, for reporting and for tests to assert on."""

    version: int | None = None
    locale: str | None = None
    export_date: str | None = None
    me: dict[str, str] = field(default_factory=dict)
    unhandled: dict[str, int] = field(default_factory=dict)
    #: Differences between the export's own DTD and the bundled one, if any.
    schema_differences: list[str] = field(default_factory=list)
    routes_imported: int = 0
    elements_seen: int = 0
    elapsed_s: float = 0.0


class Importer:
    """Parses an export into an open SQLite connection."""

    def __init__(self, connection, options: ImportOptions | None = None):
        self.connection = connection
        self.options = options or ImportOptions()
        self.result = ImportResult()
        self.buffers: dict[str, list[tuple]] = {}
        self.route_owners: dict[str, int] = {}
        self._unhandled_stored = 0

        self._wanted_types: set[str] | None = None
        if self.options.types:
            self._wanted_types = {t.lower() for t in self.options.types}
            self._wanted_types |= {
                short.lower()
                for short in (short_type(t) for t in self.options.types)
                if short
            }
        self._excluded_types = {t.lower() for t in self.options.exclude_types}
        self._since_ts = (
            timestamps.start_of_day(self.options.since) if self.options.since else None
        )

    # -- buffered writes ---------------------------------------------------

    #: Buffers that need something other than a plain INSERT OR IGNORE. The key
    #: is a buffer name, not a table name.
    STATEMENTS = {
        # Apple's DTD says it outright: "Any Records that appear as children of
        # a correlation also appear as top-level records in this document." The
        # two copies are identical apart from the correlation link, so they
        # share a content id and collapse into one row — but which copy the
        # database keeps would otherwise depend on which came first in the
        # file. Upserting the link means the correlation survives either order.
        "records@correlated": (
            "INSERT INTO records VALUES ({placeholders}) "
            "ON CONFLICT(id) DO UPDATE SET correlation_id = excluded.correlation_id"
        ),
    }

    def add(self, buffer: str, row: tuple) -> None:
        rows = self.buffers.setdefault(buffer, [])
        rows.append(row)
        if len(rows) >= db.BATCH_SIZE:
            self.flush(buffer)

    def flush(self, buffer: str | None = None) -> None:
        for name in [buffer] if buffer else list(self.buffers):
            rows = self.buffers.get(name)
            if not rows:
                continue
            placeholders = ",".join("?" * len(rows[0]))
            table = name.split("@", 1)[0]
            statement = self.STATEMENTS.get(
                name, f"INSERT OR IGNORE INTO {table} VALUES ({{placeholders}})"
            )
            self.connection.executemany(
                statement.format(placeholders=placeholders), rows
            )
            rows.clear()

    # -- filtering ---------------------------------------------------------

    def wanted(self, type_name: str | None, start_ts: int | None) -> bool:
        if (
            self._since_ts is not None
            and start_ts is not None
            and start_ts < self._since_ts
        ):
            return False
        if not type_name:
            return True
        short = (short_type(type_name) or "").lower()
        if short in self._excluded_types or type_name.lower() in self._excluded_types:
            return False
        if self._wanted_types is not None:
            return (
                short in self._wanted_types or type_name.lower() in self._wanted_types
            )
        return True

    # -- element handlers --------------------------------------------------

    def handle_record(self, element, correlation_id: int | None = None) -> int | None:
        attributes = element.attrib
        type_name = attributes.get("type")
        local_date, start_local, start_ts = timestamps.parse(
            attributes.get("startDate")
        )
        if not self.wanted(type_name, start_ts):
            return None

        _, end_local, end_ts = timestamps.parse(attributes.get("endDate"))
        value, value_text = split_value(attributes.get("value"))
        device = attributes.get("device")
        record_id = db.content_id(
            type_name,
            attributes.get("sourceName"),
            attributes.get("startDate"),
            attributes.get("endDate"),
            attributes.get("value"),
            attributes.get("unit"),
            device,
        )
        self.add(
            "records@correlated" if correlation_id is not None else "records",
            (
                record_id,
                type_name,
                short_type(type_name),
                attributes.get("unit"),
                value,
                value_text,
                attributes.get("sourceName"),
                attributes.get("sourceVersion"),
                device,
                device_name(device),
                attributes.get("creationDate"),
                attributes.get("startDate"),
                attributes.get("endDate"),
                start_local,
                end_local,
                local_date,
                start_ts,
                end_ts,
                None if start_ts is None or end_ts is None else end_ts - start_ts,
                correlation_id,
            ),
        )

        for child in element:
            if child.tag == "MetadataEntry":
                self.add(
                    "record_metadata",
                    (record_id, child.get("key"), child.get("value")),
                )
            elif child.tag == "HeartRateVariabilityMetadataList":
                for seq, beat in enumerate(child):
                    self.add(
                        "hrv_beats",
                        (
                            record_id,
                            seq,
                            as_number(beat.get("bpm")),
                            beat.get("time"),
                            timestamps.parse(beat.get("time"))[2],
                        ),
                    )
        return record_id

    def handle_correlation(self, element) -> None:
        attributes = element.attrib
        local_date, _, start_ts = timestamps.parse(attributes.get("startDate"))
        _, _, end_ts = timestamps.parse(attributes.get("endDate"))
        type_name = attributes.get("type")
        correlation_id = db.content_id(
            type_name,
            attributes.get("sourceName"),
            attributes.get("startDate"),
            attributes.get("endDate"),
        )
        self.add(
            "correlations",
            (
                correlation_id,
                type_name,
                short_type(type_name),
                attributes.get("sourceName"),
                attributes.get("sourceVersion"),
                attributes.get("device"),
                attributes.get("creationDate"),
                attributes.get("startDate"),
                attributes.get("endDate"),
                local_date,
                start_ts,
                end_ts,
            ),
        )
        for child in element:
            if child.tag == "Record":
                self.handle_record(child, correlation_id=correlation_id)

    def _statistics_row(self, workout_id: int, activity_uuid: str, element) -> tuple:
        attributes = element.attrib
        type_name = attributes.get("type")
        return (
            workout_id,
            activity_uuid,
            type_name,
            short_type(type_name),
            attributes.get("startDate"),
            attributes.get("endDate"),
            as_number(attributes.get("average")),
            as_number(attributes.get("minimum")),
            as_number(attributes.get("maximum")),
            as_number(attributes.get("sum")),
            attributes.get("unit"),
        )

    def handle_workout(self, element) -> None:
        attributes = element.attrib
        local_date, start_local, start_ts = timestamps.parse(
            attributes.get("startDate")
        )
        if (
            self._since_ts is not None
            and start_ts is not None
            and start_ts < self._since_ts
        ):
            return
        _, _, end_ts = timestamps.parse(attributes.get("endDate"))
        activity_type = attributes.get("workoutActivityType")
        workout_id = db.content_id(
            activity_type,
            attributes.get("sourceName"),
            attributes.get("startDate"),
            attributes.get("endDate"),
        )

        route_file: str | None = None
        statistics: list[tuple] = []
        event_seq = 0

        for child in element:
            if child.tag == "MetadataEntry":
                self.add(
                    "workout_metadata",
                    (workout_id, child.get("key"), child.get("value")),
                )
            elif child.tag == "WorkoutEvent":
                event_type = child.get("type")
                self.add(
                    "workout_events",
                    (
                        workout_id,
                        event_seq,
                        event_type,
                        short_type(event_type),
                        child.get("date"),
                        timestamps.parse(child.get("date"))[2],
                        as_number(child.get("duration")),
                        child.get("durationUnit"),
                    ),
                )
                event_seq += 1
            elif child.tag == "WorkoutStatistics":
                statistics.append(self._statistics_row(workout_id, "", child))
            elif child.tag == "WorkoutActivity":
                event_seq = self._handle_workout_activity(
                    workout_id, child, statistics, event_seq
                )
            elif child.tag == "WorkoutRoute":
                for reference in child.iter("FileReference"):
                    path = reference.get("path", "")
                    route_file = path.rsplit("/", 1)[-1]

        for row in statistics:
            self.add("workout_statistics", row)

        distance = as_number(attributes.get("totalDistance"))
        distance_unit = attributes.get("totalDistanceUnit")
        energy = as_number(attributes.get("totalEnergyBurned"))
        energy_unit = attributes.get("totalEnergyBurnedUnit")
        # Version 14 declares both the totals as attributes and the
        # WorkoutStatistics children, and exports differ in which they
        # actually populate. Reading only the attributes is why some tools
        # show empty distances, so fall back to the statistics.
        for row in statistics:
            _, activity_uuid, stat_type, _, _, _, _, _, _, total, unit = row
            if activity_uuid or total is None or not stat_type:
                continue
            if distance is None and "Distance" in stat_type:
                distance, distance_unit = total, unit
            if energy is None and stat_type.endswith("ActiveEnergyBurned"):
                energy, energy_unit = total, unit

        if route_file:
            self.route_owners[route_file.lower()] = workout_id

        self.add(
            "workouts",
            (
                workout_id,
                activity_type,
                short_type(activity_type),
                as_number(attributes.get("duration")),
                attributes.get("durationUnit"),
                distance,
                distance_unit,
                energy,
                energy_unit,
                attributes.get("sourceName"),
                attributes.get("sourceVersion"),
                attributes.get("device"),
                device_name(attributes.get("device")),
                attributes.get("creationDate"),
                attributes.get("startDate"),
                attributes.get("endDate"),
                start_local,
                local_date,
                start_ts,
                end_ts,
                None if start_ts is None or end_ts is None else end_ts - start_ts,
                route_file,
                None,
            ),
        )

    def _handle_workout_activity(
        self, workout_id: int, element, statistics: list[tuple], event_seq: int
    ) -> int:
        """A segment of a multi-sport workout.

        Version 14 declares `WorkoutActivity` but no content model references
        it, so by that schema it cannot legally appear anywhere — Apple's DTD
        is inconsistent here. The declaration exists for a reason, so this
        stays tolerant: segments are imported wherever they turn up, including
        the statistics and events the schema does not (yet) declare as their
        children. Being permissive on input costs a few lines; dropping data
        because a schema disagreed with reality costs a lot more.
        """
        attributes = element.attrib
        uuid = attributes.get("uuid") or ""
        self.add(
            "workout_activities",
            (
                workout_id,
                uuid,
                attributes.get("startDate"),
                attributes.get("endDate"),
                as_number(attributes.get("duration")),
                attributes.get("durationUnit"),
            ),
        )
        for child in element:
            if child.tag == "WorkoutStatistics":
                statistics.append(self._statistics_row(workout_id, uuid, child))
            elif child.tag == "MetadataEntry":
                self.add(
                    "workout_metadata",
                    (workout_id, f"{uuid}:{child.get('key')}", child.get("value")),
                )
            elif child.tag == "WorkoutEvent":
                event_type = child.get("type")
                self.add(
                    "workout_events",
                    (
                        workout_id,
                        event_seq,
                        event_type,
                        short_type(event_type),
                        child.get("date"),
                        timestamps.parse(child.get("date"))[2],
                        as_number(child.get("duration")),
                        child.get("durationUnit"),
                    ),
                )
                event_seq += 1
        return event_seq

    def handle_activity_summary(self, element) -> None:
        attributes = element.attrib
        self.add(
            "activity_summaries",
            (
                attributes.get("dateComponents"),
                as_number(attributes.get("activeEnergyBurned")),
                as_number(attributes.get("activeEnergyBurnedGoal")),
                attributes.get("activeEnergyBurnedUnit"),
                as_number(attributes.get("appleMoveTime")),
                as_number(attributes.get("appleMoveTimeGoal")),
                as_number(attributes.get("appleExerciseTime")),
                as_number(attributes.get("appleExerciseTimeGoal")),
                as_number(attributes.get("appleStandHours")),
                as_number(attributes.get("appleStandHoursGoal")),
            ),
        )

    def handle_clinical_record(self, element) -> None:
        attributes = element.attrib
        self.add(
            "clinical_records",
            (
                db.content_id(
                    attributes.get("identifier"),
                    attributes.get("sourceName"),
                    attributes.get("receivedDate"),
                ),
                attributes.get("type"),
                attributes.get("identifier"),
                attributes.get("sourceName"),
                attributes.get("sourceURL"),
                attributes.get("fhirVersion"),
                attributes.get("receivedDate"),
                attributes.get("resourceFilePath"),
            ),
        )

    def handle_audiogram(self, element) -> None:
        attributes = element.attrib
        audiogram_id = db.content_id(
            attributes.get("type"),
            attributes.get("sourceName"),
            attributes.get("startDate"),
        )
        self.add(
            "audiograms",
            (
                audiogram_id,
                attributes.get("type"),
                attributes.get("sourceName"),
                attributes.get("device"),
                attributes.get("startDate"),
                attributes.get("endDate"),
                timestamps.parse(attributes.get("startDate"))[2],
            ),
        )
        seq = 0
        for child in element:
            if child.tag == "MetadataEntry":
                self.add_extra_metadata(
                    "audiograms", audiogram_id, child.get("key"), child.get("value")
                )
            elif child.tag == "SensitivityPoint":
                self.add(
                    "audiogram_points",
                    (
                        audiogram_id,
                        seq,
                        as_number(child.get("frequencyValue")),
                        child.get("frequencyUnit"),
                        as_number(child.get("leftEarValue")),
                        child.get("leftEarUnit"),
                        child.get("leftEarMasked"),
                        as_number(child.get("leftEarClampingRangeLowerBound")),
                        as_number(child.get("leftEarClampingRangeUpperBound")),
                        as_number(child.get("rightEarValue")),
                        child.get("rightEarUnit"),
                        child.get("rightEarMasked"),
                        as_number(child.get("rightEarClampingRangeLowerBound")),
                        as_number(child.get("rightEarClampingRangeUpperBound")),
                    ),
                )
                seq += 1

    #: Eye measurements, as (export attribute, unit attribute). Every value in
    #: a prescription carries its own unit, so both are stored.
    EYE_MEASUREMENTS = (
        ("sphere", "sphereUnit"),
        ("cylinder", "cylinderUnit"),
        ("axis", "axisUnit"),
        ("add", "addUnit"),
        ("vertex", "vertexUnit"),
        ("prismAmount", "prismAmountUnit"),
        ("prismAngle", "prismAngleUnit"),
        ("farPD", "farPDUnit"),
        ("nearPD", "nearPDUnit"),
        ("baseCurve", "baseCurveUnit"),
        ("diameter", "diameterUnit"),
    )

    def handle_vision_prescription(self, element) -> None:
        attributes = element.attrib
        prescription_id = db.content_id(
            attributes.get("type"),
            attributes.get("dateIssued"),
            attributes.get("brand"),
        )
        attachments = 0
        for child in element:
            if child.tag in ("RightEye", "LeftEye"):
                eye = "right" if child.tag == "RightEye" else "left"
                measurements: list = []
                for value_attribute, unit_attribute in self.EYE_MEASUREMENTS:
                    measurements.append(as_number(child.get(value_attribute)))
                    measurements.append(child.get(unit_attribute))
                self.add(
                    "vision_prescriptions",
                    (
                        db.content_id(prescription_id, eye),
                        prescription_id,
                        attributes.get("type"),
                        attributes.get("dateIssued"),
                        attributes.get("expirationDate"),
                        attributes.get("brand"),
                        eye,
                        *measurements,
                    ),
                )
            elif child.tag == "MetadataEntry":
                self.add_extra_metadata(
                    "vision_prescriptions",
                    prescription_id,
                    child.get("key"),
                    child.get("value"),
                )
            elif child.tag == "Attachment":
                self.add_extra_metadata(
                    "vision_prescriptions",
                    prescription_id,
                    f"attachment:{attachments}",
                    child.get("identifier"),
                )
                attachments += 1

    def add_extra_metadata(
        self, owner_type: str, owner_id: int, key: str | None, value: str | None
    ) -> None:
        self.add("extra_metadata", (owner_type, owner_id, key, value))

    def handle_unknown(self, element) -> None:
        tag = element.tag
        self.result.unhandled[tag] = self.result.unhandled.get(tag, 0) + 1
        if self._unhandled_stored < UNHANDLED_LIMIT:
            self._unhandled_stored += 1
            self.add(
                "unhandled",
                (
                    db.content_id(tag, self._unhandled_stored),
                    tag,
                    ElementTree.tostring(element, encoding="unicode")[:4000],
                ),
            )

    # -- main loop ---------------------------------------------------------

    def parse(self, stream: IO[bytes], on_progress=None) -> ImportResult:
        handlers = {
            "Record": self.handle_record,
            "Correlation": self.handle_correlation,
            "Workout": self.handle_workout,
            "ActivitySummary": self.handle_activity_summary,
            "ClinicalRecord": self.handle_clinical_record,
            "Audiogram": self.handle_audiogram,
            "VisionPrescription": self.handle_vision_prescription,
        }
        started = time.monotonic()
        depth = 0
        root = None
        seen = 0

        for event, element in ElementTree.iterparse(stream, events=("start", "end")):
            if event == "start":
                depth += 1
                if depth == 1:
                    root = element
                    self.result.locale = element.get("locale")
                continue

            depth -= 1
            if depth != 1:
                # Children are consumed by their parent's handler.
                continue

            tag = element.tag
            if tag == "ExportDate":
                self.result.export_date = element.get("value")
            elif tag == "Me":
                self.result.me = dict(element.attrib)
            else:
                handler = handlers.get(tag)
                if handler is not None:
                    handler(element)
                else:
                    self.handle_unknown(element)

            seen += 1
            if on_progress is not None and seen % 250_000 == 0:
                on_progress(seen, time.monotonic() - started)

            # Clearing the element is not enough: its now-processed self stays
            # in the root's child list, so the tree would grow across the whole
            # export.
            if root is not None:
                root.clear()

        self.flush()
        self.result.elements_seen = seen
        self.result.elapsed_s = time.monotonic() - started
        return self.result
