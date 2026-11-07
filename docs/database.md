# Database layout

```
records                 every sample; type_short is the type without its HK prefix,
                        value (numeric) / value_text (enumerations),
                        start_ts (UTC) + start_local + local_date (wall clock)
record_metadata         MetadataEntry per record
hrv_beats               individual beats from HRV measurements
correlations            groups of related records (blood pressure)
workouts                one row per workout, totals filled in from statistics
workout_statistics      per-workout aggregates (distance, energy, heart rate)
workout_activities      segments of a multi-sport workout
workout_events          pause / resume / lap
workout_metadata        MetadataEntry per workout, and of its segments (<uuid>:<key>),
                        events (event:<seq>:<key>) and routes (route:<key> for the
                        first, route:<seq>:<key> for any later one)
workout_records         which records were taken during which workout
workout_routes          one row per route of a workout: its file, source and time;
                        workouts.route_file names the first
workout_zone_groups     a workout's or segment's zones: what they measure, and the unit
workout_zones           the zones of a group: bounds and the time spent in them
workout_points          GPS points from the GPX routes, numbered through all routes
                        of a workout
activity_summaries      the three rings, per day
clinical_records        references to clinical documents
audiograms(+_points)    hearing tests, with masking and clamping range per ear
vision_prescriptions    one row per eye, every measurement with its own unit
extra_metadata          MetadataEntry and Attachment of correlations and the rarer
                        elements above
export_meta             locale, export date, version, the <Me> block
unhandled               raw XML of unrecognised elements
daily_metrics           per metric and calendar day: n / total / mean / min / max,
                        plus day_value — whichever of those the metric means —
                        and deduplicated_value where several sources overlap
daily_metrics_by_source the same aggregation, split by source
metric_summary          per metric, unit and source: sample count and date range
nightly_sleep           per night: hours asleep and in bed, minutes per stage, with
                        several sources settled to one; a night is named after the
                        day you went to bed, and the boundary is noon
category_types          what a category record's value means, and how Apple groups
category_values         the value names themselves: Moderate, AsleepREM, …
quantity_types          whether a metric's day adds up or averages out
```

Views: `category_samples`, `symptoms`, `sleep_segments`, `workout_summary`.

Everything from `daily_metrics` downwards, and the views, is derived: it is
rebuilt after every import unless you pass `--no-derived`.

## Category records

Sleep stages, symptoms and mindful sessions state their value as a name —
`HKCategoryValueSeverityModerate` — and the export's schema says only that it
is text. Which names a type can carry is documented by Apple and not by the
file, so it is kept in `apple_health_to_sqlite/catalog/` and loaded into the two
`category_*` tables; `category_samples` joins them, giving `Moderate` beside
the raw value and, where the values form a scale, an `ordinal` to sort by. The
`symptoms` view is the same thing narrowed to the symptom diary.

Nothing depends on it: a type or value the catalogue does not know keeps its
raw name. See [apple_health_to_sqlite/catalog/NOTES.md](../apple_health_to_sqlite/catalog/NOTES.md).

## Sums and averages

A day of step samples adds up to the day's steps; a day of heart rates
averages to the day's heart rate, and adding them up gives a number that means
nothing. Which metric is which is documented by Apple as an aggregation style,
cumulative or discrete, so it is in the catalogue too, as `quantity_types`.

`daily_metrics` therefore has an `aggregation` column and a `day_value` one —
the sum for cumulative metrics, the mean for discrete ones. It is a signpost,
not a replacement: `total`, `mean`, `min_value` and `max_value` are all still
there, and a metric the catalogue has never heard of keeps them and leaves
`day_value` empty rather than picking wrong.

## Several sources on one day

When an iPhone and an Apple Watch both count steps, `records` holds both sets,
and `total` and `day_value` add them together — 19 638 steps on a day the
Health app shows as 10 110. Health does not sum: it splits the timeline and,
where two sources overlap, keeps the one it trusts more.

`deduplicated_value` does the same. For each cumulative metric and day with
more than one source, it ranks the sources by how much of the day they cover,
credits each moment to the highest-ranked source recording it, and prorates
samples that are only partly overlapped. A day where the Watch counted the
morning and the iPhone the afternoon comes back as nearly the sum; a day where
both counted everything comes back as one source's count. The ranking is per
day, so switching devices does not throw off every day after the switch.

A sample with no duration, such as a glass of water logged at one moment, cannot
overlap anything and counts in full. So two apps logging the same glass both
count: the export cannot tell that from two different glasses.

`nightly_sleep` settles a night the same way, separately for time asleep, time in
bed and time awake, because an iPhone recording time in bed says nothing against
a Watch recording sleep. Minutes per stage come from whichever source won those
minutes, and `sleep_segments` keeps every source's segments as recorded.

This is a reconstruction, laid beside the raw numbers rather than replacing
them: `total` and `day_value` stay the honest sum, which is the right answer
when two apps log genuinely different things, such as two meals. Where a day
has a single source, or the metric is discrete (averaging two heart rates does
not inflate anything), `deduplicated_value` is empty and `day_value` is
already right. So one query reads correctly for every metric:

```sql
select local_date, coalesce(deduplicated_value, day_value) as value
from daily_metrics
where type_short = 'StepCount'
```

Swap in `BodyMass` or `HeartRate` and it still reads correctly.

To see what each source contributed, use `daily_metrics_by_source`: the same
aggregation as `daily_metrics`, with `source_name` added to the grouping.
`metric_summary` gives the same split across the whole export.

## Timezones

Apple writes local wall clock plus offset (`2026-08-15 20:48:13 +0200`). Both
halves are kept: `start_ts` as a UTC instant for sorting and arithmetic,
`local_date` and `start_local` for grouping by calendar day. Aggregating by UTC
day would split a night's sleep across two dates.

## Querying fast

Filter on `type_short` **and** `local_date`/`start_ts` — that is what the
indexes are for — and prefer `daily_metrics` or `metric_summary` over `records`
where they answer the question. See [Performance](performance.md) for the
difference it makes.
