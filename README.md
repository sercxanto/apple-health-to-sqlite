# apple-health-to-sqlite

Convert an Apple Health export into a SQLite database, ready to explore in
[Datasette](https://datasette.io) — or anything else that reads SQLite.

Built for exports that keep growing: the import streams, so memory stays flat
regardless of file size, and it is idempotent, so a fresh export every few
months merges into the same database instead of forcing a rebuild.

**Standard library only.** No runtime dependencies, so it runs on a stock
`python3` against a multi-gigabyte export without setting anything up.

```bash
python3 -m applehealth import Export.zip health.db
datasette health.db -m datasette/metadata.yml
```

## Requirements

* Python 3.11 or newer
* HealthKit export version 14 or newer (roughly iOS 16 onwards)
* Datasette, only if you want the web interface

## Getting your export

On the iPhone: Health → your profile picture → *Export All Health Data*. You
get an `Export.zip` containing:

```
apple_health_export/
    Export.xml               # every record, with the schema in its header
    export_cda.xml           # clinical documents (ignored)
    workout-routes/*.gpx     # GPS tracks for outdoor workouts
    electrocardiograms/*.csv # ECGs, if you have any (ignored)
```

The main file is spelled `Export.xml` on some devices and `export.xml` on
others; both work, as does an unpacked directory or a bare XML file.

## Try it without your own data

The project can synthesize an export for you — same structure as the real
thing, invented data: realistic steps, heart rate, sleep stages, workouts with
GPS routes, the lot:

```bash
python3 -m applehealth synthesize --days 120 export.zip
python3 -m applehealth import export.zip health.db
```

Or in one step: `make demo`.

## The schema

Apple publishes no specification for the export format, but **ships the schema
inside every export**: `Export.xml` opens with a `<!DOCTYPE HealthData [ … ]>`
block declaring every element and attribute, headed by a version comment. That
DTD is what this project is built on, and a verbatim copy of it — taken from a
real version 14 export — lives in
[`applehealth/schema/`](applehealth/schema/). Its
[NOTES.md](applehealth/schema/NOTES.md) collects what the schema reveals.

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

### Corners that are easy to get wrong

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

## Commands

```
python3 -m applehealth import  EXPORT DATABASE
python3 -m applehealth synthesize [OUTPUT]
python3 -m applehealth extract-schema EXPORT [-o FILE]
python3 -m applehealth schema-diff EXPORT
```

Installed via `pip install .`, the same commands are available as
`apple-health-to-sqlite <command>`.

### import

| Option | Effect |
| --- | --- |
| `--types StepCount HeartRate` | only these record types, short or fully qualified |
| `--exclude-types HeartRate` | skip these; excluding heart rate typically halves the database |
| `--since 2024-01-01` | only data from this date onwards |
| `--no-routes` | skip the GPX workout routes |
| `--no-derived` | skip rebuilding `daily_metrics` and the views |
| `--quiet` | no progress output |

Running the same import twice is safe. Every row's primary key is a hash of the
fields that identify it, so re-importing merges: unchanged rows are ignored,
new ones are added.

### synthesize

| Option | Effect |
| --- | --- |
| `--days N` | how many days to cover (default 30) |
| `--end-date` | last day, for reproducible output |
| `--scale F` | sampling density; `4` means four times as many samples per day |
| `--seed N` | random seed |
| `--utc-offset` | timezone offset of the timestamps (default +2) |

## Database layout

```
records              every sample; type_short is the type without its HK prefix,
                     value (numeric) / value_text (enumerations),
                     start_ts (UTC) + start_local + local_date (wall clock)
record_metadata      MetadataEntry per record
hrv_beats            individual beats from HRV measurements
correlations         groups of related records (blood pressure)
workouts             one row per workout, totals filled in from statistics
workout_statistics   per-workout aggregates (distance, energy, heart rate)
workout_activities   segments of a multi-sport workout
workout_events       pause / resume / lap
workout_metadata     MetadataEntry per workout
workout_points       GPS points from the GPX routes
activity_summaries   the three rings, per day
clinical_records     references to clinical documents
audiograms(+_points) hearing tests, with masking and clamping range per ear
vision_prescriptions one row per eye, every measurement with its own unit
extra_metadata       MetadataEntry and Attachment of the rarer elements above
export_meta          locale, export date, version, the <Me> block
unhandled            raw XML of unrecognised elements
daily_metrics        per metric and calendar day: n / total / mean / min / max
metric_summary       per metric, unit and source: sample count and date range
```

Views: `sleep_segments`, `nightly_sleep` (a night is named after the day you
went to bed; the boundary is noon), `workout_summary`.

### Timezones

Apple writes local wall clock plus offset (`2026-08-15 20:48:13 +0200`). Both
halves are kept: `start_ts` as a UTC instant for sorting and arithmetic,
`local_date` and `start_local` for grouping by calendar day. Aggregating by UTC
day would split a night's sleep across two dates.

## Datasette

Put the database next to the two configuration files and point Datasette at
the directory:

```bash
mkdir -p serve && cp datasette/metadata.yml datasette/settings.json serve/
ln -s "$PWD/health.db" serve/health.db
datasette serve/
```

That is Datasette's *configuration directory* mode: it picks up `metadata.yml`,
`settings.json` and every `*.db` in the directory, so nothing has to be
repeated on the command line. `make demo` and `make serve` do exactly this.

The bundled [metadata.yml](datasette/metadata.yml) adds facets and nine ready
queries: steps per day, weight over time, resting heart rate against HRV, sleep
per night, workouts, weekly totals, heart rate on one day, a workout route on a
map, and an inventory of what your export actually contains. The database file
must be named `health.db`, since Datasette keys the configuration on the
filename.

### Settings

**Settings are not metadata.** In Datasette 0.65 a `settings:` block inside
`metadata.yml` is *silently ignored* — no warning, no error, the default stays
in force. They belong in `settings.json` beside it (configuration directory
mode, above), or on the command line:

```bash
datasette health.db -m datasette/metadata.yml --setting sql_time_limit_ms 10000
```

[settings.json](datasette/settings.json) raises `sql_time_limit_ms` from its
1000 ms default to 10 000. Not because the queries need it — see
[Performance](#performance) — but because the first query against a large
database pays for reading cold pages off disk, and 1.3 GB of cold cache can
take longer than a second all by itself.

If a query does get cut off, raising the limit is the second thing to try. The
first is to check whether it is scanning `records` when `daily_metrics` or
`metric_summary` would answer it.

Useful plugins:

```bash
datasette install datasette-cluster-map datasette-vega
```

`datasette-cluster-map` picks up `workout_points` automatically (it looks for
`latitude`/`longitude` columns); `datasette-vega` adds a chart tab to every
query.

**Do not publish this database.** `datasette publish` would put your health
data on the public internet. Run it locally; if it truly has to be remote, put
it behind `datasette-auth-passwords` and HTTPS.

## Performance

Measured on an Apple Silicon Mac with a synthetic five-year export (45 MB zip,
2.0 M records, 286 000 GPS points), synthesized with
`synthesize --days 1825 --scale 2`:

```
import              53 s     (35 s of it parsing XML, the rest indexes and aggregates)
peak memory         55 MB    <- flat, independent of export size
database           1.3 GB
```

Query times on that database:

| Query | Time |
| --- | --- |
| steps per day across five years (`daily_metrics`) | 0.5 ms |
| what is in my export (`metric_summary`) | 0.1 ms |
| heart rate for a single day (`records`, 640 rows) | 1.4 ms |
| `nightly_sleep` across five years | 52 ms |
| full scan of every heart-rate record (worst case) | 271 ms |

Filter on `type_short` **and** `local_date`/`start_ts` — that is what the
indexes are for — and prefer `daily_metrics` or `metric_summary` over `records`
where they answer the question. The inventory query is the illustration:
against `records` it needs a full scan and a grouping temp b-tree, four seconds
on this database and well past the point where Datasette gives up. Precomputed
at import time, it is a lookup over nineteen rows. The whole derivation step
costs 11 seconds of the 53-second import.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests run with `make test`; the whole
suite works against synthetic data, so no real health data is ever needed —
and none should ever be committed.

## Licence

MIT.
