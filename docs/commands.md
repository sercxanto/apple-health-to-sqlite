# Commands

```
python3 -m apple_health_to_sqlite import EXPORT DIRECTORY
python3 -m apple_health_to_sqlite synthesize [OUTPUT]
python3 -m apple_health_to_sqlite extract-schema EXPORT [-o FILE]
python3 -m apple_health_to_sqlite schema-diff EXPORT
```

That form works in a checkout with nothing installed. Outside one,
`uvx apple-health-to-sqlite <command>` runs the same commands; after
`uv tool install apple-health-to-sqlite` they are available as
`apple-health-to-sqlite <command>`. Every command has `--help`.

## import

Reads an export and writes it into a directory that Datasette can open as it
is:

| Path | Holds |
| --- | --- |
| `DIRECTORY/health.db` | the SQLite database, created if it does not exist yet |
| `DIRECTORY/metadata.yml`, `DIRECTORY/settings.json` | Datasette's configuration, copied from the package on every run |

The directory is created if necessary; `datasette DIRECTORY` opens the result.
See [Datasette](datasette.md).

The command changes nothing if `DIRECTORY` is a file, or if it holds
`metadata.yml` or `settings.json` but no `health.db`, since those files would
then not be copies from an earlier run.

`EXPORT` can be the `Export.zip` from the iPhone, an unpacked directory, or a
bare XML file. The main file is spelled `Export.xml` on some devices and
`export.xml` on others; both work.

| Option | Effect |
| --- | --- |
| `--types StepCount HeartRate` | only these record types, short or fully qualified |
| `--exclude-types HeartRate` | skip these; excluding heart rate typically halves the database |
| `--since 2024-01-01` | only data from this calendar day onwards, in the time zone each entry was recorded in; applies to everything with a date, and keeps entries whose date cannot be read |
| `--no-routes` | skip the GPX workout routes |
| `--no-derived` | skip rebuilding `daily_metrics` and the views |
| `--quiet` | no progress output and no summary; what could not be imported is still reported, on stderr |

Running the same import twice is safe. Every row's primary key is a hash of the
fields that identify it, so re-importing merges: unchanged rows are ignored,
new ones are added.

At the end of the run, the importer reports any element it did not recognise.
Those are kept verbatim in the `unhandled` table rather than dropped.

If `Export.xml` or a workout route turns out to be damaged, the import stops
with a message naming the file, exit status 1, and no new data in the database.

## synthesize

Writes an invented export with the same structure as a real one: realistic
steps, heart rate, sleep stages, workouts with GPS routes and indoor ones
without. Default output is `export.zip`.

| Option | Effect |
| --- | --- |
| `--days N` | how many days to cover (default 30) |
| `--end-date YYYY-MM-DD` | last day, for reproducible output (default today) |
| `--scale F` | sampling density; `4` means four times as many samples per day |
| `--seed N` | random seed (default 42) |
| `--utc-offset N` | timezone offset of the timestamps, in hours (default +2) |
| `--locale` | locale written into the export (default `en_US`) |
| `--quiet` | no progress output |

## extract-schema

Prints the schema embedded at the top of an export (see
[The export format](schema.md)), or writes it to a file with `-o`.

## schema-diff

Compares an export's schema with the copy bundled with this project. It
prints declaration differences only — element and attribute names, never a
measurement — so the output is safe to paste into an issue.
