# Changelog

All notable changes to apple-health-to-sqlite are documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
apple-health-to-sqlite follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Exports of HealthKit version 17 are fully imported: time per workout zone is in the new tables `workout_zone_groups` and `workout_zones`, and `workout_records` says which records belong to which workout.
- `schema-diff` shows the content and attributes of an element it does not know, not just its name.
- The summary of `import` counts the files in the export that were not imported, such as ECG recordings, per directory.
- `workout_routes` keeps the source, device and time of each workout route, which were not imported before.

### Changed

- `nightly_sleep` is now a table rather than a view, with the same columns. Queries that read it keep working.

### Fixed

- Elements nested inside a known element could be lost without a word: version 17 workout zones were, and so was the metadata of correlations, workout events and workout routes. All of these are now imported, and anything unrecognised is kept in `unhandled` and reported at any depth, under a path such as `Workout/SomeNewElement`. To get the missing data, import your export again; existing rows stay as they are.
- `import --since` now goes by the calendar day an entry was recorded on. Before, it cut at midnight UTC, so data from just after midnight could be missing east of UTC, and data from the evening before could slip in west of it.
- `import --since` now also filters correlations, activity summaries, clinical records, audiograms and vision prescriptions, not just records and workouts.
- `import --since` refuses a date it cannot read. Before, a mistyped date turned the filter off and imported everything without a word.
- A record with an unreadable end date no longer aborts the import on a day with several sources.
- A damaged export or workout route, a database the tool cannot use and a target it cannot write to now end in a message saying what to do, rather than a Python traceback. If `Export.xml` is damaged, nothing from that export reaches the database.
- A damaged workout route no longer stops the import: it is skipped and reported by name, and so is a route the export names but does not hold. Both are reported with `--quiet` too.
- All routes of a workout with several are imported, in the new table `workout_routes` and with all their points in `workout_points`. Before, only the last one was. To get the missing points, import your export again.
- `import --quiet` still reports elements it could not import and a differing schema, on stderr. Before, it said nothing about them.
- `deduplicated_value` no longer drops samples without a duration, such as a glass of water logged at one moment. A day where two sources logged only such samples read 0. To get the right numbers, import your export again.
- `nightly_sleep` counts a night recorded by several sources once, where it used to add them up. Importing your export again updates it; `sleep_segments` still shows every source.
- A new unrecognised element is kept in `unhandled` even if an earlier import stored a different one in the same position, and the same element in another order is no longer stored twice. The limit of 500 stored rows now applies per element, so that one frequent element cannot leave no example of a rarer one.

## [0.1.0] - 2026-11-07

### Added

- `apple-health-to-sqlite import` turns an Apple Health export (HealthKit export version 14 or newer) into a SQLite database, in a directory that also holds the configuration for browsing it with Datasette.
- A new export can be imported into the same directory later: rows you already have are skipped and new ones are added.
- The import streams, so memory use stays flat however large the export is.
- Data the tool does not recognise is kept in an `unhandled` table and reported at the end of the run, rather than dropped.
- `apple-health-to-sqlite synthesize` writes an export of invented data, to try the tool without your own.
- `apple-health-to-sqlite schema-diff` compares an export's declared format with the one the tool knows, and prints declarations only, no health data.
- Runs on Linux, macOS and Windows with Python 3.11 or newer, without further dependencies.

[Unreleased]: https://github.com/sercxanto/apple-health-to-sqlite/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sercxanto/apple-health-to-sqlite/releases/tag/v0.1.0
