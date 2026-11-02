# Commands

```
python3 -m applehealth import EXPORT DATABASE
python3 -m applehealth demo [DIRECTORY]
python3 -m applehealth serve [DIRECTORY]
python3 -m applehealth synthesize [OUTPUT]
python3 -m applehealth extract-schema EXPORT [-o FILE]
python3 -m applehealth schema-diff EXPORT
```

Installed via `pip install .`, the same commands are available as
`apple-health-to-sqlite <command>`. Every command has `--help`.

## import

Reads an export and writes it into a SQLite database, creating the database
if it does not exist yet.

`EXPORT` can be the `Export.zip` from the iPhone, an unpacked directory, or a
bare XML file. The main file is spelled `Export.xml` on some devices and
`export.xml` on others; both work.

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

At the end of the run, the importer reports any element it did not recognise.
Those are kept verbatim in the `unhandled` table rather than dropped.

## demo

Builds a demonstration from invented data and serves it: synthesizes an
export into `DIRECTORY` (default `demo/`), imports it into
`DIRECTORY/health.db`, and runs `serve` on the directory. No health data of
yours is involved.

| Option | Effect |
| --- | --- |
| `--days N` | how many days to invent (default 120) |
| `--port N` | port to serve on (default 8001) |
| `--no-serve` | build the database but do not serve it |

## serve

Serves a database that has already been built, with Datasette, in the
foreground until Ctrl-C. `DIRECTORY` (default `demo/`) has to contain the
database as `health.db`, under exactly that name. See
[Datasette](datasette.md) for what the command sets up.

| Option | Effect |
| --- | --- |
| `--port N` | port to serve on (default 8001) |

Datasette 0.65 has to be installed (`pip install "datasette<1"`, or with pipx); this
package never imports it and finds it either way.

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
