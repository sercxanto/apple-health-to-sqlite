# Changelog

All notable changes to apple-health-to-sqlite are documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
apple-health-to-sqlite follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
