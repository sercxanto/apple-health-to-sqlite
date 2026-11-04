"""Synthesize an Apple Health export: same structure, invented data.

The point of this module is that nobody should have to hand over their real
health data to work on this project. It produces an `export.zip` with the same
structure the Health app writes — the bundled DTD verbatim, records, workouts
with GPX routes, activity summaries — at any size you like.

It copies the DTD from `apple_health_to_sqlite/schema/` rather than embedding its
own, and writes every element that schema allows to occur — so the test suite exercises
every importer handler, and this module and the importer cannot drift apart.
The one exception is `WorkoutActivity`, which version 14 declares but
references from no content model: emitting it would produce an invalid
document, so a fixture covers it instead.
"""

from __future__ import annotations

import math
import random
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import quoteattr

from . import catalog, dtd

EXPORT_DIRECTORY = "apple_health_export"

# Sources as they appear in a real export: (name, version, device string).
WATCH = (
    "Apple Watch",
    "10.3",
    "<<HKDevice: 0x2823a1f40>, name:Apple Watch, manufacturer:Apple Inc., "
    "model:Watch, hardware:Watch6,2, software:10.3>",
)
PHONE = (
    "iPhone",
    "17.4",
    "<<HKDevice: 0x2823a0be0>, name:iPhone, manufacturer:Apple Inc., "
    "model:iPhone, hardware:iPhone15,2, software:17.4>",
)
SCALES = ("Smart Scale", "6.2.1", None)
MANUAL = ("Health", "17.4", None)


@dataclass
class SynthesizeOptions:
    days: int = 30
    end_date: str | None = None
    scale: float = 1.0
    seed: int = 42
    utc_offset: int = 2
    locale: str = "en_US"
    export_filename: str = "Export.xml"
    compresslevel: int = 6


@dataclass
class SynthesizeResult:
    path: Path
    start_day: date
    end_day: date
    routes: int
    size_bytes: int


def format_timestamp(value: datetime) -> str:
    """Apple's timestamp format: `2026-08-15 20:48:13 +0200`."""
    return value.strftime("%Y-%m-%d %H:%M:%S %z")


def _attributes(attributes: dict) -> str:
    return "".join(
        f" {key}={quoteattr(str(value))}"
        for key, value in attributes.items()
        if value is not None
    )


def empty_tag(name: str, indent: int = 1, **attributes) -> str:
    return f"{' ' * indent}<{name}{_attributes(attributes)}/>\n"


def wrapped_tag(name: str, inner: str, indent: int = 1, **attributes) -> str:
    padding = " " * indent
    return f"{padding}<{name}{_attributes(attributes)}>\n{inner}{padding}</{name}>\n"


def symptom_choices() -> list[tuple[str, list[str]]]:
    """Symptom types with the value names each one accepts, from the catalogue.

    Taking both sides from `apple_health_to_sqlite/catalog` rather than listing a few
    here keeps the synthesized pairs legal — a symptom's value has to be a member of
    the enumeration its own type declares — and keeps this module from growing
    a second opinion about which is which.
    """
    catalogue = catalog.load()
    members = {}
    for enum, entry in catalogue["enums"].items():
        # People log a symptom they have. `NotPresent` and `Unspecified` are
        # legal and do occur, but a diary made mostly of them would misrepresent
        # what this data looks like; a fixture covers them instead.
        felt = [
            member["name"]
            for member in entry["members"]
            if (member["ordinal"] or 0) > 0
        ]
        members[enum] = felt or [member["name"] for member in entry["members"]]
    return sorted(
        (type_name, members[entry["enum"]])
        for type_name, entry in catalogue["types"].items()
        if entry["family"] == "Symptoms" and entry["enum"] in members
    )


class Synthesizer:
    """Builds one synthetic export."""

    def __init__(self, options: SynthesizeOptions):
        self.options = options
        self.tz = timezone(timedelta(hours=options.utc_offset))
        self.random = random.Random(options.seed)
        self.routes: list[tuple[str, str]] = []
        self.weight = 78.5
        self.symptom_choices = symptom_choices()

    # -- helpers -----------------------------------------------------------

    def at(self, day: date, hour: float) -> datetime:
        midnight = datetime(day.year, day.month, day.day, tzinfo=self.tz)
        return midnight + timedelta(hours=hour)

    def around(self, base: float, spread: float) -> float:
        return base + self.random.uniform(-spread, spread)

    def record(
        self,
        type_name: str,
        start: datetime,
        end: datetime,
        value=None,
        unit: str | None = None,
        source=PHONE,
        metadata: dict | None = None,
        children: str = "",
        indent: int = 1,
    ) -> str:
        name, version, device = source
        attributes = {
            "type": type_name,
            "unit": unit,
            "value": value,
            "sourceName": name,
            "sourceVersion": version,
            "device": device,
            "creationDate": format_timestamp(
                end + timedelta(seconds=self.random.randint(1, 90))
            ),
            "startDate": format_timestamp(start),
            "endDate": format_timestamp(end),
        }
        inner = children
        for key, entry in (metadata or {}).items():
            inner += empty_tag("MetadataEntry", indent=indent + 1, key=key, value=entry)
        if not inner:
            return empty_tag("Record", indent=indent, **attributes)
        return wrapped_tag("Record", inner, indent=indent, **attributes)

    # -- metrics -----------------------------------------------------------

    def steps(self, day: date):
        step = 0.25 / self.options.scale
        hour = 6.5
        while hour < 22.5:
            start = self.at(day, hour)
            end = start + timedelta(hours=step)
            busy = (
                1.0
                + 0.8 * (abs(hour - 8) < 1)
                + 1.2 * (abs(hour - 12.5) < 1)
                + 1.0 * (abs(hour - 18) < 1.5)
            )
            count = max(0, int(self.random.gauss(70 * busy, 45)))
            if count:
                yield self.record(
                    "HKQuantityTypeIdentifierStepCount",
                    start,
                    end,
                    value=count,
                    unit="count",
                    source=WATCH,
                )
                yield self.record(
                    "HKQuantityTypeIdentifierDistanceWalkingRunning",
                    start,
                    end,
                    value=round(count * 0.00072, 5),
                    unit="km",
                    source=WATCH,
                )
            hour += step

    def energy(self, day: date):
        step = 0.25 / self.options.scale
        hour = 0.0
        while hour < 24:
            start = self.at(day, hour)
            awake = 7 <= hour < 23
            yield self.record(
                "HKQuantityTypeIdentifierActiveEnergyBurned",
                start,
                start + timedelta(hours=step),
                value=round(self.random.uniform(0.2, 9.0 if awake else 0.6), 3),
                unit="kcal",
                source=WATCH,
            )
            hour += step
        for hour_index in range(24):
            start = self.at(day, hour_index)
            yield self.record(
                "HKQuantityTypeIdentifierBasalEnergyBurned",
                start,
                start + timedelta(hours=1),
                value=round(self.random.uniform(60, 78), 3),
                unit="kcal",
                source=WATCH,
            )

    def heart_rate(self, day: date):
        step = 5 / 60 / self.options.scale
        hour = 0.0
        while hour < 24:
            moment = self.at(day, hour)
            asleep = hour < 6.5 or hour > 23
            bpm = self.random.gauss(52 if asleep else 74, 6 if asleep else 12)
            yield self.record(
                "HKQuantityTypeIdentifierHeartRate",
                moment,
                moment,
                value=round(max(40.0, bpm), 3),
                unit="count/min",
                source=WATCH,
                metadata={
                    "HKMetadataKeyHeartRateMotionContext": self.random.choice(
                        ["0", "1", "2"]
                    )
                },
            )
            hour += step

        for type_name, base, spread, hour_of_day in (
            ("HKQuantityTypeIdentifierRestingHeartRate", 54, 4, 3),
            ("HKQuantityTypeIdentifierWalkingHeartRateAverage", 96, 8, 12),
        ):
            moment = self.at(day, hour_of_day)
            yield self.record(
                type_name,
                moment,
                moment,
                value=round(self.around(base, spread), 3),
                unit="count/min",
                source=WATCH,
            )

    def heart_rate_variability(self, day: date):
        """HRV records carry a nested list of individual beats."""
        for index in range(6):
            start = self.at(day, 2 + index * 3.5)
            beats = ""
            offset = 0.0
            for _ in range(self.random.randint(4, 9)):
                bpm = round(self.random.gauss(58, 7))
                beats += empty_tag(
                    "InstantaneousBeatsPerMinute",
                    indent=3,
                    bpm=bpm,
                    time=format_timestamp(start + timedelta(seconds=offset)),
                )
                offset += 60.0 / max(bpm, 1)
            yield self.record(
                "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
                start,
                start + timedelta(seconds=60),
                value=round(self.around(42, 12), 4),
                unit="ms",
                source=WATCH,
                children=wrapped_tag(
                    "HeartRateVariabilityMetadataList", beats, indent=2
                ),
            )

    def body(self, day: date, index: int):
        if index % 3 == 0:
            self.weight += self.random.uniform(-0.4, 0.35)
            moment = self.at(day, 7.2)
            yield self.record(
                "HKQuantityTypeIdentifierBodyMass",
                moment,
                moment,
                value=round(self.weight, 2),
                unit="kg",
                source=SCALES,
                metadata={"HKWasUserEntered": "0"},
            )
            yield self.record(
                "HKQuantityTypeIdentifierBodyFatPercentage",
                moment,
                moment,
                value=round(self.around(0.19, 0.02), 4),
                unit="%",
                source=SCALES,
            )
        if index == 0:
            moment = self.at(day, 7)
            yield self.record(
                "HKQuantityTypeIdentifierHeight",
                moment,
                moment,
                value=1.83,
                unit="m",
                source=MANUAL,
                metadata={"HKWasUserEntered": "1"},
            )

    def sleep(self, day: date):
        """Sleep stages as category records, using the iOS 16+ value names."""
        stages = [
            "HKCategoryValueSleepAnalysisAsleepCore",
            "HKCategoryValueSleepAnalysisAsleepDeep",
            "HKCategoryValueSleepAnalysisAsleepREM",
            "HKCategoryValueSleepAnalysisAwake",
        ]
        bed_start = self.at(day, -1.0)
        bed_end = self.at(day, 6.6)
        yield self.record(
            "HKCategoryTypeIdentifierSleepAnalysis",
            bed_start,
            bed_end,
            value="HKCategoryValueSleepAnalysisInBed",
            source=WATCH,
        )
        cursor = bed_start + timedelta(minutes=self.random.randint(5, 25))
        while cursor < bed_end:
            end = min(cursor + timedelta(minutes=self.random.randint(20, 75)), bed_end)
            yield self.record(
                "HKCategoryTypeIdentifierSleepAnalysis",
                cursor,
                end,
                value=self.random.choices(stages, weights=[6, 2, 3, 1])[0],
                source=WATCH,
            )
            cursor = end

        for index in range(4):
            moment = bed_start + timedelta(hours=1.5 * index)
            yield self.record(
                "HKQuantityTypeIdentifierOxygenSaturation",
                moment,
                moment,
                value=round(self.around(0.965, 0.015), 4),
                unit="%",
                source=WATCH,
            )
            yield self.record(
                "HKQuantityTypeIdentifierRespiratoryRate",
                moment,
                moment,
                value=round(self.around(14.5, 2), 3),
                unit="count/min",
                source=WATCH,
            )

    def blood_pressure(self, day: date, index: int):
        """Blood pressure arrives as a Correlation wrapping two Records.

        The DTD notes that those child records *also* appear as top-level
        records in the same document, so they are emitted twice here. The
        importer collapses the copies and keeps the correlation link.
        """
        if index % 4:
            return
        moment = self.at(day, 8.1)
        systolic = self.record(
            "HKQuantityTypeIdentifierBloodPressureSystolic",
            moment,
            moment,
            value=round(self.around(124, 9)),
            unit="mmHg",
            source=MANUAL,
            indent=2,
        )
        diastolic = self.record(
            "HKQuantityTypeIdentifierBloodPressureDiastolic",
            moment,
            moment,
            value=round(self.around(79, 6)),
            unit="mmHg",
            source=MANUAL,
            indent=2,
        )
        name, version, _ = MANUAL
        yield wrapped_tag(
            "Correlation",
            systolic + diastolic,
            type="HKCorrelationTypeIdentifierBloodPressure",
            sourceName=name,
            sourceVersion=version,
            creationDate=format_timestamp(moment),
            startDate=format_timestamp(moment),
            endDate=format_timestamp(moment),
        )
        yield systolic
        yield diastolic

    def miscellaneous(self, day: date):
        yield self.record(
            "HKQuantityTypeIdentifierFlightsClimbed",
            self.at(day, 17),
            self.at(day, 17.1),
            value=self.random.randint(2, 22),
            unit="count",
            source=PHONE,
        )
        yield self.record(
            "HKQuantityTypeIdentifierAppleExerciseTime",
            self.at(day, 18),
            self.at(day, 19),
            value=self.random.randint(10, 70),
            unit="min",
            source=WATCH,
        )
        yield self.record(
            "HKCategoryTypeIdentifierMindfulSession",
            self.at(day, 21),
            self.at(day, 21.2),
            value="HKCategoryValueNotApplicable",
            source=PHONE,
        )

    def symptoms(self, day: date, index: int):
        """Entries from the Health app's symptom diary.

        Two things set these apart from the measured metrics: the value is a
        name rather than a number, and an entry can cover days rather than a
        moment — a headache logged for one afternoon, back pain logged for the
        week it lasted.
        """
        if index % 5:
            return
        type_name, values = self.random.choice(self.symptom_choices)
        start = self.at(day, self.random.uniform(8, 20))
        long_episode = index % 15 == 0
        end = start + (
            timedelta(days=self.random.randint(2, 5))
            if long_episode
            else timedelta(hours=self.random.uniform(1, 6))
        )
        yield self.record(
            type_name,
            start,
            end,
            value=self.random.choice(values),
            source=MANUAL,
            metadata={"HKWasUserEntered": "1"},
        )

    def indoor_workout(self, day: date, index: int):
        """A workout the watch records without a route: strength training in
        the morning, on a day that also has an outdoor one in the evening.

        Real weeks are full of these, and they are what makes "the workout on
        that day" an ambiguous idea: anything that picks one workout per day
        picks this one and misses the route. Indoor workouts also carry no
        distance, which is Apple's shape rather than a gap in the data.
        """
        if index % 4:
            return
        start = self.at(day, 7.0 + self.random.uniform(-0.5, 0.5))
        minutes = self.random.randint(20, 45)
        end = start + timedelta(minutes=minutes)
        energy = round(minutes * self.random.uniform(4.5, 8.0), 3)

        inner = empty_tag("MetadataEntry", indent=2, key="HKIndoorWorkout", value="1")
        inner += empty_tag(
            "WorkoutStatistics",
            indent=2,
            type="HKQuantityTypeIdentifierActiveEnergyBurned",
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
            sum=energy,
            unit="kcal",
        )
        inner += empty_tag(
            "WorkoutStatistics",
            indent=2,
            type="HKQuantityTypeIdentifierHeartRate",
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
            average=round(self.around(124, 6), 4),
            minimum=round(self.around(88, 5), 4),
            maximum=round(self.around(152, 6), 4),
            unit="count/min",
        )
        name, version, device = WATCH
        yield wrapped_tag(
            "Workout",
            inner,
            workoutActivityType="HKWorkoutActivityTypeTraditionalStrengthTraining",
            duration=round(minutes, 4),
            durationUnit="min",
            totalEnergyBurned=energy,
            totalEnergyBurnedUnit="kcal",
            sourceName=name,
            sourceVersion=version,
            device=device,
            creationDate=format_timestamp(end),
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
        )

    def workout(self, day: date, index: int):
        """A workout with events, statistics and a GPS route.

        Every fourth one is a multi-sport workout with `WorkoutActivity`
        segments, so that path gets exercised too.
        """
        if index % 2:
            return
        activity, speed_kmh = self.random.choice(
            [
                ("HKWorkoutActivityTypeRunning", 10.5),
                ("HKWorkoutActivityTypeCycling", 24.0),
                ("HKWorkoutActivityTypeWalking", 5.2),
            ]
        )
        start = self.at(day, 18.0 + self.random.uniform(-1, 1))
        minutes = self.random.randint(28, 75)
        end = start + timedelta(minutes=minutes)
        distance = round(speed_kmh * minutes / 60, 3)
        energy = round(minutes * self.random.uniform(7.5, 12.5), 3)

        inner = empty_tag("MetadataEntry", indent=2, key="HKIndoorWorkout", value="0")
        inner += empty_tag(
            "MetadataEntry",
            indent=2,
            key="HKAverageMETs",
            value=f"{self.random.uniform(5, 11):.4f} kcal/hr·kg",
        )
        inner += empty_tag(
            "WorkoutEvent",
            indent=2,
            type="HKWorkoutEventTypePause",
            date=format_timestamp(start + timedelta(minutes=minutes // 2)),
        )
        inner += empty_tag(
            "WorkoutEvent",
            indent=2,
            type="HKWorkoutEventTypeResume",
            date=format_timestamp(start + timedelta(minutes=minutes // 2, seconds=95)),
        )
        inner += empty_tag(
            "WorkoutEvent",
            indent=2,
            type="HKWorkoutEventTypeLap",
            date=format_timestamp(start + timedelta(minutes=minutes // 3)),
            duration=round(minutes / 3, 4),
            durationUnit="min",
        )

        distance_type = (
            "HKQuantityTypeIdentifierDistanceCycling"
            if "Cycling" in activity
            else "HKQuantityTypeIdentifierDistanceWalkingRunning"
        )
        inner += empty_tag(
            "WorkoutStatistics",
            indent=2,
            type=distance_type,
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
            sum=distance,
            unit="km",
        )
        inner += empty_tag(
            "WorkoutStatistics",
            indent=2,
            type="HKQuantityTypeIdentifierActiveEnergyBurned",
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
            sum=energy,
            unit="kcal",
        )
        inner += empty_tag(
            "WorkoutStatistics",
            indent=2,
            type="HKQuantityTypeIdentifierHeartRate",
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
            average=round(self.around(148, 8), 4),
            minimum=round(self.around(96, 6), 4),
            maximum=round(self.around(176, 6), 4),
            unit="count/min",
        )

        route_name = "route_{}_{}.gpx".format(
            day.isoformat(), start.strftime("%I.%M%p").lstrip("0").lower()
        )
        self.routes.append((route_name, self.gpx(start, minutes, speed_kmh)))
        name, version, device = WATCH
        inner += wrapped_tag(
            "WorkoutRoute",
            empty_tag("FileReference", indent=3, path=f"/workout-routes/{route_name}"),
            indent=2,
            sourceName=name,
            sourceVersion=version,
            device=device,
            creationDate=format_timestamp(end),
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
        )

        # The watch samples heart rate far more densely during a workout.
        for minute in range(minutes):
            moment = start + timedelta(minutes=minute)
            yield self.record(
                "HKQuantityTypeIdentifierHeartRate",
                moment,
                moment,
                value=round(self.random.gauss(150, 12), 3),
                unit="count/min",
                source=WATCH,
                metadata={"HKMetadataKeyHeartRateMotionContext": "2"},
            )

        # The totals are declared as attributes *and* available as statistics.
        # Real exports vary in which they populate, so do the same: every
        # fourth workout carries the statistics only, which is what exercises
        # the importer's fallback.
        totals: dict[str, object] = {}
        if index % 4:
            totals = {
                "totalDistance": distance,
                "totalDistanceUnit": "km",
                "totalEnergyBurned": energy,
                "totalEnergyBurnedUnit": "kcal",
            }

        yield wrapped_tag(
            "Workout",
            inner,
            workoutActivityType=activity,
            duration=round(minutes, 4),
            durationUnit="min",
            **totals,
            sourceName=name,
            sourceVersion=version,
            device=device,
            creationDate=format_timestamp(end),
            startDate=format_timestamp(start),
            endDate=format_timestamp(end),
        )

    def gpx(self, start: datetime, minutes: int, speed_kmh: float) -> str:
        latitude, longitude = 48.1372, 11.5756
        heading = self.random.uniform(0, math.tau)
        moment = start
        points = []
        for _ in range(minutes * 6):  # one point every ten seconds
            heading += self.random.uniform(-0.25, 0.25)
            step_degrees = speed_kmh / 3600 * 10 / 111.0
            latitude += step_degrees * math.cos(heading)
            longitude += step_degrees * math.sin(heading) / 0.67
            stamp = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            points.append(
                f'    <trkpt lon="{longitude:.7f}" lat="{latitude:.7f}">\n'
                f"      <ele>{520 + self.random.uniform(-15, 15):.2f}</ele>\n"
                f"      <time>{stamp}</time>\n"
                f"      <extensions>\n"
                f"        <speed>"
                f"{speed_kmh / 3.6 * self.random.uniform(0.85, 1.15):.2f}</speed>\n"
                f"        <course>{heading * 57.3 % 360:.2f}</course>\n"
                f"        <hAcc>{self.random.uniform(1.5, 6.0):.2f}</hAcc>\n"
                f"        <vAcc>{self.random.uniform(1.5, 8.0):.2f}</vAcc>\n"
                f"      </extensions>\n"
                f"    </trkpt>\n"
            )
            moment += timedelta(seconds=10)
        opening = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Apple Health Export" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f"  <metadata>\n    <time>{opening}</time>\n  </metadata>\n"
            "  <trk>\n    <name>Route</name>\n    <trkseg>\n"
            + "".join(points)
            + "    </trkseg>\n  </trk>\n</gpx>\n"
        )

    def activity_summary(self, day: date) -> str:
        return empty_tag(
            "ActivitySummary",
            dateComponents=day.isoformat(),
            activeEnergyBurned=round(self.random.uniform(280, 820), 3),
            activeEnergyBurnedGoal=600,
            activeEnergyBurnedUnit="kcal",
            appleMoveTime=0,
            appleMoveTimeGoal=0,
            appleExerciseTime=self.random.randint(5, 95),
            appleExerciseTimeGoal=30,
            appleStandHours=self.random.randint(6, 14),
            appleStandHoursGoal=12,
        )

    def occasional_elements(self, day: date) -> str:
        """One of each rarer element, so every handler sees data."""
        moment = self.at(day, 11)
        points = empty_tag(
            "MetadataEntry", indent=2, key="HKMetadataKeyAudiogramTestMethod", value="1"
        )
        for frequency in (500, 1000, 2000, 4000, 8000):
            points += empty_tag(
                "SensitivityPoint",
                indent=2,
                frequencyValue=frequency,
                frequencyUnit="Hz",
                leftEarValue=round(self.around(12, 8), 1),
                leftEarUnit="dBHL",
                leftEarMasked="0",
                leftEarClampingRangeLowerBound=0,
                leftEarClampingRangeUpperBound=90,
                rightEarValue=round(self.around(12, 8), 1),
                rightEarUnit="dBHL",
                rightEarMasked="0",
                rightEarClampingRangeLowerBound=0,
                rightEarClampingRangeUpperBound=90,
            )
        output = wrapped_tag(
            "Audiogram",
            points,
            type="HKDataTypeIdentifierAudiogram",
            sourceName="Health",
            sourceVersion="17.4",
            creationDate=format_timestamp(moment),
            startDate=format_timestamp(moment),
            endDate=format_timestamp(moment),
        )
        output += empty_tag(
            "ClinicalRecord",
            type="HKClinicalTypeIdentifierLabResultRecord",
            identifier="urn:uuid:0f3f0a1c-lab-result",
            sourceName="Example Health System",
            sourceURL="https://example.org/fhir",
            fhirVersion="4.0.1",
            receivedDate=format_timestamp(moment),
            resourceFilePath="/clinical-records/LabResult-0f3f0a1c.json",
        )
        eyes = ""
        for tag, sphere, cylinder, axis in (
            ("RightEye", "-1.25", "-0.5", "180"),
            ("LeftEye", "-1.5", "-0.25", "175"),
        ):
            eyes += empty_tag(
                tag,
                indent=2,
                sphere=sphere,
                sphereUnit="diopters",
                cylinder=cylinder,
                cylinderUnit="diopters",
                axis=axis,
                axisUnit="degrees",
                add="0",
                addUnit="diopters",
                vertex="12",
                vertexUnit="mm",
                farPD="32",
                farPDUnit="mm",
                nearPD="30",
                nearPDUnit="mm",
            )
        eyes += empty_tag("Attachment", indent=2, identifier="prescription-scan-1")
        eyes += empty_tag(
            "MetadataEntry",
            indent=2,
            key="HKMetadataKeyDeviceSerialNumber",
            value="n/a",
        )
        output += wrapped_tag(
            "VisionPrescription",
            eyes,
            type="Glasses",
            dateIssued=format_timestamp(moment),
            expirationDate=format_timestamp(moment + timedelta(days=730)),
            brand="Example Optics",
        )
        return output

    # -- assembly ----------------------------------------------------------

    def day_chunks(self, day: date, index: int):
        yield from self.steps(day)
        yield from self.energy(day)
        yield from self.heart_rate(day)
        yield from self.heart_rate_variability(day)
        yield from self.sleep(day)
        yield from self.body(day, index)
        yield from self.blood_pressure(day, index)
        yield from self.miscellaneous(day)
        yield from self.symptoms(day, index)
        yield from self.indoor_workout(day, index)
        yield from self.workout(day, index)
        yield self.activity_summary(day)

    def write(self, destination: str | Path, on_progress=None) -> SynthesizeResult:
        options = self.options
        end_day = (
            date.fromisoformat(options.end_date) if options.end_date else date.today()
        )
        start_day = end_day - timedelta(days=options.days - 1)
        path = Path(destination)

        with zipfile.ZipFile(
            path, "w", zipfile.ZIP_DEFLATED, compresslevel=options.compresslevel
        ) as archive:
            member = f"{EXPORT_DIRECTORY}/{options.export_filename}"
            with archive.open(member, "w") as raw:

                def write(text: str) -> None:
                    raw.write(text.encode("utf-8"))

                write('<?xml version="1.0" encoding="UTF-8"?>\n')
                write(dtd.bundled_text())
                write(f"<HealthData locale={quoteattr(options.locale)}>\n")
                write(
                    empty_tag(
                        "ExportDate",
                        value=format_timestamp(self.at(end_day, 21.5)),
                    )
                )
                write(
                    empty_tag(
                        "Me",
                        HKCharacteristicTypeIdentifierDateOfBirth="1979-04-12",
                        HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet",
                        HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet",
                        HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet",
                        HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None",
                    )
                )
                for index in range(options.days):
                    day = start_day + timedelta(days=index)
                    for chunk in self.day_chunks(day, index):
                        write(chunk)
                    if on_progress is not None and (index + 1) % 30 == 0:
                        on_progress(index + 1, options.days)
                write(self.occasional_elements(end_day))
                write("</HealthData>\n")

            for name, content in self.routes:
                archive.writestr(f"{EXPORT_DIRECTORY}/workout-routes/{name}", content)

            archive.writestr(
                f"{EXPORT_DIRECTORY}/export_cda.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ClinicalDocument xmlns="urn:hl7-org:v3">\n'
                "  <!-- Clinical documents; deliberately ignored on import. -->\n"
                "</ClinicalDocument>\n",
            )

        return SynthesizeResult(
            path=path,
            start_day=start_day,
            end_day=end_day,
            routes=len(self.routes),
            size_bytes=path.stat().st_size,
        )


def synthesize(destination: str | Path, options: SynthesizeOptions, on_progress=None):
    return Synthesizer(options).write(destination, on_progress=on_progress)
