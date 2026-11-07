# The HealthKit export schema

Apple does not publish a specification for the Health export. It does, however,
ship the schema *inside every export*: `Export.xml` opens with a
`<!DOCTYPE HealthData [ … ]>` internal subset declaring every element and
attribute, prefixed by a version comment:

```
<!-- HealthKit Export Version: 17 -->
```

That DTD is the authority this project builds on. `healthkit-export-v17.dtd` in
this directory is a verbatim copy of it, used three ways:

1. `apple_health_to_sqlite/synthetic.py` copies it into the synthetic exports the test
   suite runs on, and emits only elements, attributes and nesting the
   schema declares — so synthesizer and importer cannot drift apart.
2. `tests/test_dtd.py` asserts that every declared element is handled by the
   importer, and `tests/test_synthetic.py` that every element which *can occur*
   is exercised.
3. `apple-health-to-sqlite schema-diff` compares a real export against it.

## Provenance

Taken from a real HealthKit **export version 17** file, including Apple's own
comments. It replaced the one from a version 14 file, which is in the git history;
what changed is listed under [Findings](#findings) below.

To check it against another export:

```bash
python3 -m apple_health_to_sqlite schema-diff /path/to/Export.zip
```

That prints declaration differences only — element and attribute names, never
record content — so its output is safe to paste into an issue. See
CONTRIBUTING.md, "Refreshing the bundled schema", for how to fold changes in.

## Findings

Things this schema says that are worth knowing, all of which drive code:

**Correlation children are duplicated.** Apple's own comment:

> Any Records that appear as children of a correlation also appear as
> top-level records in this document.

So a blood pressure reading appears twice — once inside the `<Correlation>`,
once at the top level. The importer gives both copies the same content-derived
id so they collapse into one row, and upserts the correlation link so it
survives regardless of which copy the file lists first.

**So are workout children.** Version 17 lets a `<Workout>` contain `<Record>`s,
with the same comment. The copies collapse the same way, and `workout_records`
keeps which workout a record came from.

**What version 17 added.** Besides those records: `WorkoutZoneGroup` and
`WorkoutZone`, as children of a workout and of a segment. A group states `type`,
`source` and `unit`, and each zone its `duration`, with `minimum` and `maximum`
optional. The schema says nothing about the values of `type` and `source`, and
this project has not seen them: the importer stores them as they are, and what the
synthesizer writes there is invented. No attribute of an existing element changed.

**Workout totals exist in two places.** The schema declares `totalDistance`,
`totalDistanceUnit`, `totalEnergyBurned` and `totalEnergyBurnedUnit` on
`<Workout>` *and* `<WorkoutStatistics>` children that carry the same
quantities. Exports differ in which they populate. The importer reads the
attributes when present and falls back to the statistics otherwise; reading
only the attributes is why some tools show empty distances.

**`WorkoutActivity` was declared but unreachable until version 17.** Version 14
declares the element, but no content model references it — not even `<Workout>`
— and lists only `MetadataEntry` as its child. Version 17 makes it a child of
`<Workout>`, with events, statistics and zone groups of its own.

**Every vision measurement carries its own unit.** `sphere` comes with
`sphereUnit`, `axis` with `axisUnit`, and so on. Pupillary distances are
spelled `farPD` / `nearPD`.

**Audiograms carry more than raw thresholds.** `SensitivityPoint` has masking
flags and a clamping range per ear, which say whether a value is a real
threshold or the limit of the test.

## Supported versions

Version **14 and newer**. The importer reads the version comment and refuses
anything older with an explanation, because pre-14 exports differ in ways this
project deliberately does not carry. `<WorkoutActivity>` and the sleep-stage
category values (`HKCategoryValueSleepAnalysisAsleepREM` and friends, iOS 16+)
are the visible examples.

Newer versions are expected to *add* declarations. That case is handled
gracefully: unknown elements, at any depth, land in the `unhandled` table and are
reported at the end of an import rather than being dropped.

## Related files that are not covered

An export contains more than `Export.xml`:

| Path | Handled? |
| --- | --- |
| `Export.xml` | yes — this schema |
| `export_cda.xml` | no — clinical documents in HL7 CDA format, a different schema entirely |
| `workout-routes/*.gpx` | yes — GPX 1.1, parsed by `apple_health_to_sqlite/routes.py` |
| `electrocardiograms/*.csv` | no — one CSV per ECG, with its own header format |

`ClinicalRecord` elements in `Export.xml` are imported as *references* (they
carry a `resourceFilePath`); the FHIR resources they point at are not parsed.
