# Contributing

For humans and for agents — the workflow is the same either way. This file is
the authority on *how to work here*: setup, commands, conventions, recipes.

[AGENTS.md](AGENTS.md) is the companion: it lists the design invariants every
change has to preserve, plus a few rules that only apply to agents. It does not
repeat anything below. Read both once; then this one is the day-to-day
reference.

## Ground rules

1. **No real health data in this repository. Ever.** Not as a fixture, not
   "temporarily", not in a branch. Everything the tests need is synthesized —
   see [Test data](#test-data).
2. **English**, in code, comments, docs, commit messages and CLI output.
3. **Standard library first.** See [Dependencies](#dependencies).

## Setting up

### Devcontainer (recommended)

Open the repository in VS Code and reopen in the container. Everything is
installed, Datasette is forwarded on port 8001.

### Locally

The importer itself needs nothing but Python ≥ 3.11 — `python3 -m applehealth`
works in a bare checkout. Only the dev tooling needs installing:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

If you prefer not to have a virtualenv, `pipx install poethepoet`,
`pipx install datasette` and `pipx install ruff` give you the three commands
the tasks call.

## Commands

The task runner is [poe](https://poethepoet.natn.io/), configured in
`pyproject.toml` under `[tool.poe.tasks]`. `poe` on its own lists everything
below with its description.

| Command | What it does |
| --- | --- |
| `poe test` | fast test suite (a few seconds) |
| `poe test-all` | everything, including the slow large-export test |
| `poe test-slow` | only the slow large-export test — what the `slow` CI job runs |
| `poe lint` | `ruff check` and `ruff format --check` |
| `poe format` | apply formatting and safe fixes |
| `poe check` | lint + fast tests — what CI runs |
| `poe demo` | synthesize an export, import it, serve it |
| `poe serve` | serve an already-built demo database |
| `poe clean` | remove build artefacts, caches and `demo/` |

Arguments after `--` reach the underlying command, so `poe demo -- --days 30`
and `poe serve -- --port 9000` work. `demo` and `serve` are thin wrappers
around `python3 -m applehealth demo` and `… serve`, which you can equally call
directly.

There was a Makefile here until August 2026. It was replaced because half its
recipes were Unix shell — `mkdir -p`, `cp`, `find -exec` — so none of it ran on
Windows. Two rules keep that from creeping back:

* **Nothing in `[tool.poe.tasks]` may rely on a shell.** No pipes, no `&&`, no
  Unix utilities. A task is one command, a `sequence` of commands, or a Python
  function.
* **Anything that runs Python goes through `tasks.py`.** A bare `python` is not
  on the PATH on every machine that has Python; poe resolves it for `script`
  tasks and not for `cmd` tasks. `tasks.py` says more about this.

Run a single test file or case with unittest directly:

```bash
python3 -m unittest tests.test_import_workouts -v
python3 -m unittest tests.test_derive.SleepViewTest.test_a_night_spans_midnight
```

The slow test (`tests/test_large_export.py`) is skipped unless
`APPLEHEALTH_SLOW_TESTS=1` is set. It synthesizes a few hundred thousand records
and takes a minute or two; CI runs it once per push, not per Python version.

## Layout

```
applehealth/
    cli.py          argparse subcommands, output formatting
    pipeline.py     orchestration: archive → schema check → import → derive
    archive.py      finding files inside an export (zip, directory, bare XML)
    dtd.py          parsing, version-gating and diffing Apple's schema
    schema/         the committed DTD — see its NOTES.md
    importer.py     streaming XML → rows, one handler per element
    routes.py       GPX tracks → workout_points
    timestamps.py   Apple's timestamp format
    db.py           DDL, indexes, content-addressed row ids
    derive.py       daily_metrics and the views
    synthetic.py    synthetic exports
    demo.py         building a directory for Datasette, and serving it
    datasette/      metadata.yml and settings.json — facets, canned queries
tasks.py            the development tasks poe calls
tests/              unittest suite; tests/fixtures/*.xml are hand-written
```

`pipeline.py` exists so `routes.py` can import from `importer.py` without a
cycle. Start there when tracing what an import does.

## Conventions

Formatting and lint rules are ruff's job, configured in `pyproject.toml`; run
`poe format` rather than arguing about style. Beyond that:

* **Names spell things out.** `correlation_id`, not `cid`. The exception is
  loop-local scratch in a two-line block.
* **Comments explain why, not what.** The reader can see that a distance is
  copied out of `WorkoutStatistics`; what they cannot see is that the schema
  offers the same number in two places, that exports disagree about which one
  they fill in, and that reading only the attribute is why other tools show
  blanks. Write that.
* **Docstrings on modules and non-obvious functions.** A module docstring
  should say what the module is *for*, not list its functions.
* **Errors aimed at the person reading them.** Say what was wrong and what to
  do about it — compare `dtd.SchemaError` in `dtd.py`.
* **Tests assert behaviour, not implementation.** Name them as sentences:
  `test_totals_are_backfilled_from_workout_statistics`.

### Dependencies

The package has **no runtime dependencies** and should keep it that way. The
reason is concrete: the import runs on someone's own machine against a
multi-gigabyte file, and "install this first" is the step where that stops
happening.

Dev-only dependencies are fine when they earn their place — currently
`datasette` (the thing the output is for, and what validates `metadata.yml`),
`ruff`, and `poethepoet` (the task runner, which is why there is no Makefile).
To add one, say in the pull request what it does that the standard library
cannot, and whether it is needed at runtime or only for development.
Adding a runtime dependency needs a stronger case than convenience.

## Recipes

### Adding support for a new element

When Apple adds an element, `schema-diff` and the `unhandled` table will point
at it. Then:

1. **Update the schema.** Add the declaration to
   `applehealth/schema/healthkit-export-v14.dtd`, copying it from a real
   export's DTD (see below). This is the source of truth; nothing else moves
   until it does.
2. **Add a handler** in `applehealth/importer.py`, and list the element in
   `TOP_LEVEL_ELEMENTS` or `CHILD_ELEMENTS`. `tests/test_dtd.py` fails until
   every declared element is accounted for, which is the point.
3. **Add a table** in `applehealth/db.py` if the data does not fit an existing
   one. Give it a content-addressed primary key so re-import stays idempotent;
   child tables key on `(parent_id, seq)` or `(parent_id, name)`.
4. **Synthesize it.** Teach `applehealth/synthetic.py` to emit the element —
   `tests/test_synthetic.py` asserts the synthesizer covers every element that
   can occur, so this is not optional. Synthetic exports must stay valid
   against the schema: only declared attributes, only declared nesting. An
   element that no content model references cannot be synthesized at all;
   cover that with a fixture instead, as `WorkoutActivity` does.
5. **Test it.** A focused fixture in `tests/fixtures/` for the edge cases, and
   assertions on the synthetic data for the normal path.
6. **Document it** in the database layout table in README.md.

### Refreshing the bundled schema

Only someone with a real export can do this, and they can do it without
sharing the file:

```bash
python3 -m applehealth schema-diff /path/to/Export.zip
```

The output is declaration differences only — element and attribute names — so
it is safe to paste into an issue. Apply those differences by hand to
`applehealth/schema/healthkit-export-v14.dtd`, or replace the file wholesale
with `extract-schema` output if the version has moved on. Keep Apple's own
comments: at least one of them ("Any Records that appear as children of a
correlation also appear as top-level records") describes behaviour the importer
depends on, and a test asserts it is still there.

Then follow the recipe above for anything newly declared, and record what you
learned in `applehealth/schema/NOTES.md` — its Findings section is where the
non-obvious parts of the format live.

Supported versions are 14 and newer. Adding support for older exports is not a
goal; see NOTES.md for what would be involved.

### Test data

Two sources, both synthetic:

* `applehealth/synthetic.py` for realistic, multi-day data. `support.sample_database()`
  caches one per test run.
* `tests/fixtures/*.xml` for edge cases, where reading the exact input matters
  more than realism. Fixtures contain only the `<HealthData>` body;
  `support.write_export()` wraps them in the real header and zips them.

If you hit a bug that only your real export triggers, reproduce it as a
fixture: reduce it to the structure that breaks, replace the values with
obviously fake ones, and commit *that*.

## Pull requests

* `poe check` passes.
* One concern per pull request. Formatting churn in files you did not otherwise
  touch makes review harder — leave it out.
* Say why, not just what. If the change is driven by something in a real
  export, describe the structure, never paste the data.
* New behaviour comes with a test. Bug fixes come with the test that failed
  first.
