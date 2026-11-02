# Performance

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

To keep the database smaller, `import --exclude-types HeartRate` typically
halves it.
