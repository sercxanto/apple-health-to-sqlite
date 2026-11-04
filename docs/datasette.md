# Datasette

## Opening the database

Datasette 0.65 is not part of this package. `uvx` fetches it on demand; to
install it instead, use `uv tool install "datasette<1"`, and then call plain
`datasette`.

```bash
uvx --from apple-health-to-sqlite health2sqlite import Export.zip mine/
uvx "datasette<1" mine/
```

Datasette is configured through a *configuration directory*: point it at a
folder and it picks up `metadata.yml`, `settings.json` and every `*.db` in it,
so nothing has to be repeated on the command line. `import` builds that folder:
it writes `health.db` and copies the two bundled configuration files next to
it. Every run overwrites those copies, so make lasting changes in
`apple_health_to_sqlite/datasette/`.

The database file must be named `health.db`, since Datasette keys the
configuration on the filename.

Outside such a directory, the equivalent is:

```bash
datasette health.db -m apple_health_to_sqlite/datasette/metadata.yml --setting sql_time_limit_ms 10000
```

## Ready-made queries

The bundled [metadata.yml](../apple_health_to_sqlite/datasette/metadata.yml) adds facets
and nineteen ready queries, in four groups:

* **Day to day** — steps per day, steps with 7- and 28-day averages, steps by
  weekday, weekly totals, weight over time, resting heart rate against HRV, and
  heart rate on one day.
* **Sleep** — sleep per night, bedtime and wake time with sleep efficiency, and
  sleep against the next day's resting heart rate and HRV.
* **Activity** — workouts, moving time against elapsed time, the activity rings
  with how many of them closed, and the routes of one day on a map.
* **What is in the export** — an inventory of the metrics, which source
  recorded what and when, the days a metric has no data at all, the symptom
  diary, and whether the import got everything.

The step queries and the weekly totals read `deduplicated_value`, so a phone
and a watch counting the same steps are not doubled; see
[Several sources on one day](database.md#several-sources-on-one-day).

## Settings

**Settings are not metadata.** In Datasette 0.65 a `settings:` block inside
`metadata.yml` is *silently ignored* — no warning, no error, the default stays
in force. They belong in `settings.json` beside it (configuration directory
mode, above), or on the command line.

[settings.json](../apple_health_to_sqlite/datasette/settings.json) raises
`sql_time_limit_ms` from its 1000 ms default to 10 000. Not because the
queries need it — see [Performance](performance.md) — but because the first
query against a large database pays for reading cold pages off disk, and
1.3 GB of cold cache can take longer than a second all by itself.

If a query does get cut off, raising the limit is the second thing to try. The
first is to check whether it is scanning `records` when `daily_metrics` or
`metric_summary` would answer it.

## Plugins

```bash
uvx --with datasette-cluster-map --with datasette-vega "datasette<1" mine/
```

To install Datasette with them once and for all, `uv tool install "datasette<1"
--with datasette-cluster-map --with datasette-vega`.

`datasette-cluster-map` picks up `workout_points` automatically (it looks for
`latitude`/`longitude` columns); `datasette-vega` adds a chart tab to every
query.

## Do not publish this database

`datasette publish` would put your health data on the public internet. Run it
locally; if it truly has to be remote, put it behind
`datasette-auth-passwords` and HTTPS.
