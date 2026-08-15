# The HealthKit export schema

Apple does not publish a specification for the Health export. It does, however,
ship the schema *inside every export*: `Export.xml` opens with a
`<!DOCTYPE HealthData [ … ]>` internal subset declaring every element and
attribute, prefixed by a version comment:

```
<!-- HealthKit Export Version: 14 -->
```

That DTD is the authority this project builds on. `healthkit-export-v14.dtd` in
this directory is a committed copy of it, used three ways:

1. `applehealth/generate.py` copies it verbatim into the synthetic exports the
   test suite runs on, so generator and importer cannot drift apart.
2. `tests/test_dtd.py` asserts that every element declared here is handled by
   the importer (or is a documented exception).
3. `apple-health-to-sqlite schema-diff` compares a real export against it.

## Provenance and how to correct it

⚠️ This copy was **reconstructed** rather than lifted from a real version 14
export. It is expected to be close, not certain. Anyone with a real export
should run:

```bash
python3 -m applehealth schema-diff /path/to/Export.zip
```

That prints only declaration differences — element and attribute names, never
record content — so it is safe to paste into an issue. See CONTRIBUTING.md,
"Refreshing the bundled schema", for how to fold the result back in.

## Supported versions

Version **14 and newer**. The importer reads the version comment and refuses
anything older with an explanation, because pre-14 exports differ in ways this
project deliberately does not carry:

- Up to roughly version 12, `<Workout>` carried `totalDistance`,
  `totalDistanceUnit`, `totalEnergyBurned` and `totalEnergyBurnedUnit`
  attributes. Those moved into `<WorkoutStatistics>` child elements. The
  importer still reads the old attributes when present, since that costs three
  lines and makes a stray old export merge cleanly, but nothing generates them
  and no test covers them.
- `<WorkoutActivity>` (segmented multi-sport workouts) only appears in newer
  exports.

Newer versions are expected to *add* declarations. That case is handled
gracefully: unknown elements land in the `unhandled` table and are reported at
the end of an import rather than being dropped.

## Related files that are not covered

An export contains more than `Export.xml`:

| Path | Handled? |
| --- | --- |
| `Export.xml` | yes — this schema |
| `export_cda.xml` | no — clinical documents in HL7 CDA format, a different schema entirely |
| `workout-routes/*.gpx` | yes — GPX 1.1, parsed by `applehealth/routes.py` |
| `electrocardiograms/*.csv` | no — one CSV per ECG, with its own header format |

`ClinicalRecord` elements in `Export.xml` are imported as *references* (they
carry a `resourceFilePath`); the FHIR resources they point at are not parsed.
