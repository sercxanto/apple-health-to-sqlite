# AGENTS.md

**Read [CONTRIBUTING.md](CONTRIBUTING.md) first.** Setup, commands, layout,
conventions and step-by-step recipes live there and are deliberately not
repeated here. This file adds two things: the invariants a change must not
break, and the rules that apply specifically to agents.

## The privacy boundary

This repository processes personal health data. The person you are working with
has a real `Export.zip`; you must treat it as off limits.

* **Never ask for it**, and never propose a step that involves you reading it —
  not "just the first few lines", not to check a hunch.
* **If a path to it appears** in the conversation, in a config, or in shell
  history: do not open it. Say why, and offer the alternative below.
* **When something genuinely needs facts from the real export**, hand over a
  command the user runs themselves, whose output contains structure but no
  measurements. `python3 -m applehealth schema-diff Export.zip` is the built-in
  one; if you need something else, design it to print declarations, names or
  counts — never values — and say so explicitly when you hand it over.
* **Never** add a real export, or anything derived from one, to the repository,
  a scratch directory you will commit, an issue, a commit message, or a
  published artifact.

You never need the real export. Every input you develop and test against is
synthetic — the real format, invented data — and you make your own:
`python3 -m applehealth synthesize --days 120 export.zip`.

## Invariants

These hold across the whole project. If a change would break one, that is a
design decision to raise with the user, not something to work around quietly.

1. **The bundled DTD is the source of truth.** `applehealth/schema/*.dtd`
   drives the synthesizer, the handler-coverage test, and `schema-diff`. Code
   must not carry its own idea of the schema.
2. **Every declared element is handled or visibly unhandled.** Either an
   importer handler consumes it, or it lands in the `unhandled` table and is
   reported at the end of the run. Silently dropping data is the one outcome
   this project refuses.
3. **The import streams.** `iterparse` over the archive stream, tree cleared
   after each top-level element, rows flushed in batches. Memory must stay flat
   for a 5 GB export. Nothing may accumulate per-record state in a dict or list
   that lives for the whole run.
4. **Re-import is idempotent.** Content-addressed primary keys plus
   `INSERT OR IGNORE`. A new table without a content-addressed key breaks this,
   and the failure is silent until someone re-imports a year later.
5. **Local time and UTC are both kept.** `start_ts` for instants, `local_date`
   for calendar grouping. Never derive a calendar date from the UTC timestamp.
6. **No runtime dependencies.**

## Definition of done

`make check` passes — lint clean, fast suite green — and any behaviour you
added has a test that fails without your change. If you touched the importer,
the schema, or the database layout, also run
`APPLEHEALTH_SLOW_TESTS=1 make test-all`; invariants 3 and 4 are only really
exercised there.

Report honestly: if something is untested, unfinished, or you worked around a
failure rather than fixing it, say so plainly rather than letting `make check`
speak for work it did not cover.

## Working style here

* **Follow the recipes.** Adding an element type, refreshing the schema and
  writing test data each have a procedure in CONTRIBUTING.md. They exist
  because the steps are easy to half-do — a handler with no synthesizer
  support looks finished and is not.
* **Do not reformat what you did not change.** `ruff format` on a file you
  edited is fine; a formatting sweep buried in a behaviour change is not.
* **Prefer extending a fixture over inventing a new sample path.** If an edge
  case fits an existing file in `tests/fixtures/`, put it there.
* **The `unhandled` table and `schema-diff` are features, not diagnostics to
  clean up.** Do not "fix" a failing handler-coverage test by shrinking the
  DTD.
