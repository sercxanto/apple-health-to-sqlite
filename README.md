# apple-health-to-sqlite

Your iPhone and Apple Watch have been taking notes for years: every step,
every heartbeat they sampled, every night's sleep stages, every workout with
its GPS route. The Health app shows you that a day at a time. This tool turns
the whole export into one SQLite database, so you can ask it anything — and,
with [Datasette](https://datasette.io), browse it as a website on your own
machine.

```bash
uvx --from apple-health-to-sqlite health2sqlite import Export.zip mine/
```

That is the whole setup, given [uv](https://docs.astral.sh/uv/). The tool has no
runtime dependencies, so nothing else gets installed, and it stays light even on
an export of several gigabytes.

## What you can ask it

Datasette opens with nineteen ready-made questions, one click each:

* How many steps per day — with 7- and 28-day averages, by weekday, by week?
* How long did I sleep each night, when did I go to bed, and how did that
  night show up in the next day's resting heart rate and HRV?
* Which workouts did I do, how much of them was moving rather than standing
  around, and where did the routes go? (on a map)
* How often did I close my activity rings?
* What is actually in my export — which metrics, from which device, since
  when, and where are the gaps?

And because it is plain SQLite, anything else is one query away:

```sql
select local_date, coalesce(deduplicated_value, day_value) as steps
from daily_metrics
where type_short = 'StepCount'
order by local_date desc
```

## Try it in a minute, without your own data

The project can invent a realistic export — steps, heart rate, sleep stages,
workouts with routes — so you can look around before touching your own:

```bash
uvx --from apple-health-to-sqlite health2sqlite synthesize --days 120 export.zip
uvx --from apple-health-to-sqlite health2sqlite import export.zip sample/
uvx "datasette<1" sample/
```

Then open <http://localhost:8001>.

## Use it on your own export

1. On the iPhone: **Health → your profile picture → Export All Health Data**,
   and get the `Export.zip` onto your computer.
2. Import it and open it:

   ```bash
   uvx --from apple-health-to-sqlite health2sqlite import Export.zip mine/
   uvx "datasette<1" mine/
   ```

The directory holds the database and Datasette's configuration, so that is all
Datasette needs to be told. A new export a few months later goes into the same
directory: re-importing is safe, rows you already have are skipped and new ones
are added.

To keep the command around instead of spelling it out each time:

```bash
uv tool install apple-health-to-sqlite
health2sqlite import Export.zip mine/
```

## Why this one

* **Flat memory.** The import streams. A five-year export with two million
  records imports in under a minute on a recent Mac and never needs more than
  about 55 MB.
* **Nothing silently dropped.** Anything the importer does not recognise — a
  new element in a future iOS — is kept verbatim and reported at the end.
* **Numbers that match the Health app.** When iPhone and Watch both count your
  steps, you get one day's steps, not twice as many.
* **Calendar days in your time zone.** A night's sleep is not split at
  midnight UTC.
* **Built on Apple's own schema**, which ships inside every export, and checks
  yours against it.

## Your data stays yours

Everything runs locally; nothing is uploaded anywhere. Please keep it that
way: **do not `datasette publish` this database**, and never attach your
export or database to an issue — `health2sqlite schema-diff Export.zip` prints
only the structure of your export and is safe to share.

## Documentation

* [Commands](docs/commands.md) — every command and option
* [Database layout](docs/database.md) — the tables, and what their numbers mean
* [Datasette](docs/datasette.md) — opening the database, settings, plugins
* [The export format](docs/schema.md) — Apple's schema and its traps
* [Performance](docs/performance.md) — import and query timings

Requires uv, which fetches Python 3.11 or newer if you do not have it, and an
export of HealthKit version 14 or newer (roughly iOS 16 onwards). Contributions
are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT.
