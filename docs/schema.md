# The export format

## What is in an export

The iPhone's *Export All Health Data* produces an `Export.zip` containing:

```
apple_health_export/
    Export.xml               # every record, with the schema in its header
    export_cda.xml           # clinical documents (ignored)
    workout-routes/*.gpx     # GPS tracks for outdoor workouts
    electrocardiograms/*.csv # ECGs, if you have any (ignored)
```

The main file is spelled `Export.xml` on some devices and `export.xml` on
others; both work, as does an unpacked directory or a bare XML file.

## The schema

Apple publishes no specification for the export format, but **ships the schema
inside every export**: `Export.xml` opens with a `<!DOCTYPE HealthData [ … ]>`
block declaring every element and attribute, headed by a version comment. That
DTD is what this project is built on, and a verbatim copy of it — taken from a
real version 14 export — lives in
[`applehealth/schema/`](../applehealth/schema/). Its
[NOTES.md](../applehealth/schema/NOTES.md) collects what the schema reveals.

To see it in your own export (the block ends just before `<Me …>`, which holds
your date of birth and blood type):

```bash
python3 -m applehealth extract-schema Export.zip
```

To check your export against the bundled copy:

```bash
python3 -m applehealth schema-diff Export.zip
```

That prints declaration differences only — element and attribute names, never a
measurement — so the output is safe to paste into an issue.

## Corners that are easy to get wrong

**Workout totals live in two places.** Version 14 declares `totalDistance` and
`totalEnergyBurned` as attributes of `<Workout>` *and* `<WorkoutStatistics>`
children carrying the same quantities, and exports differ in which they fill
in. Importers that read only the attributes show empty distances. This one
reads the attributes when present and falls back to the statistics.

**Correlation records appear twice.** The schema says so in a comment: a record
nested in a `<Correlation>` is also emitted at the top level. Counting both
would double every blood pressure reading. Here the two copies collapse into
one row that keeps its correlation link.

Also handled: HRV records carrying a nested list of individual heartbeats;
sleep stages as enumerated names rather than numbers; multi-sport workouts with
`<WorkoutActivity>` segments (which the version 14 schema declares but never
references — the importer takes them anyway); vision prescriptions where every
measurement has its own unit.

Anything the importer does not recognise — a new element in a future export
version — is kept verbatim in the `unhandled` table and reported at the end of
the run, rather than dropped.
