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
installed and on the PATH, so plain `poe check` works; Datasette is forwarded on
port 8001. The container keeps its environment in `~/.venv` rather than in the
checkout, because the workspace is bind-mounted and a `.venv/` built on the host
would be for the wrong operating system.

### Locally

All you need is [uv](https://docs.astral.sh/uv/); there is no setup step of its
own:

```bash
uv run poe check
```

`uv run` creates `.venv/` if necessary, brings it in line with `uv.lock`,
including the `dev` dependency group and an editable install of the checkout,
and runs the command there. Everything below is written as plain `poe …`: put
`uv run` in front, or `source .venv/bin/activate` once. After changing a
dependency in `pyproject.toml`, run `uv lock` and commit `uv.lock`.

The importer itself is standard library only, so the
`python3 -m apple_health_to_sqlite` commands in these documents work in a bare
checkout on any Python ≥ 3.11.

## Commands

The task runner is [poe](https://poethepoet.natn.io/), configured in
`pyproject.toml` under `[tool.poe.tasks]`. `poe` on its own lists everything
below with its description.

| Command | What it does |
| --- | --- |
| `poe test` | fast test suite (a few seconds) |
| `poe test-all` | everything, including the slow large-export test |
| `poe test-slow` | only the slow large-export test — what the `slow` CI job runs |
| `poe test-sdist` | build the sdist and run the fast suite from it |
| `poe coverage` | fast suite under coverage; terminal summary + `htmlcov/`, fails under the gate |
| `poe lint` | `ruff check` and `ruff format --check` |
| `poe typecheck` | `ty check` on the package |
| `poe format` | apply formatting and safe fixes |
| `poe check` | lint + type check + fast tests — what CI runs |
| `poe refresh-catalog` | rebuild the catalogue from Apple's docs |
| `poe clean` | remove build artefacts and caches |

Arguments after `--` reach the underlying command, so
`poe refresh-catalog -- --only categories` works.

To look at the result, build a directory from invented data and open it:

```bash
python3 -m apple_health_to_sqlite synthesize --days 120 export.zip
python3 -m apple_health_to_sqlite import export.zip sample/
datasette sample/
```

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

`poe coverage` measures `apple_health_to_sqlite/` only, with branch coverage on, and fails
if the total drops below the `fail_under` in pyproject.toml — the `coverage` CI
job is that same command, so the threshold lives in one place and moves in one
place. It runs the fast suite on purpose: measured both ways, the slow test
reaches no line the small fixtures do not, and excluding it means code that
only the large-export path reaches is reported as uncovered rather than
quietly counted.

The slow test (`tests/test_large_export.py`) is skipped unless
`APPLE_HEALTH_TO_SQLITE_SLOW_TESTS=1` is set. It synthesizes a few hundred thousand records
and takes a minute or two; CI runs it once per push, on Linux, not per platform
or Python version. Its memory check needs the POSIX-only `resource` module and
is skipped on Windows.

## CI and dependency updates

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs the fast suite on Linux,
macOS and Windows; lint, type check, coverage, the slow test and the test from the
sdist run on Linux only. Locally you usually see only one of those platforms, so a
change that touches paths, file handling or text encodings needs a green run on
all three: open a pull request instead of pushing to `main`.

The workflow pins each action to a commit hash, with its version in a comment,
because a tag can be moved to other code. [Dependabot](.github/dependabot.yml)
proposes updates of the actions and of the development dependencies in `uv.lock`
once a month, each group in one pull request, and only for releases that are at
least a week old.

## Layout

```
apple_health_to_sqlite/
    cli.py          argparse subcommands, output formatting
    pipeline.py     orchestration: archive → schema check → import → derive
    archive.py      finding files inside an export (zip, directory, bare XML)
    dtd.py          parsing, version-gating and diffing Apple's schema
    schema_tools.py what extract-schema and schema-diff do with a real export
    schema/         the committed DTD — see its NOTES.md
    catalog/        what values mean, and how days add up — see its NOTES.md
    importer.py     streaming XML → rows, one handler per element
    routes.py       GPX tracks → workout_points
    timestamps.py   Apple's timestamp format
    db.py           DDL, indexes, content-addressed row ids
    derive.py       daily_metrics and the views
    dedup.py        folding several sources into one de-duplicated day
    synthetic.py    synthetic exports
    layout.py       the directory an import writes for Datasette
    datasette/      metadata.yml and settings.json — facets, canned queries
tasks.py            the development tasks poe calls
tools/              development-time scripts; not part of the package
tests/              unittest suite; tests/fixtures/*.xml are hand-written
```

`pipeline.py` exists so `routes.py` can import from `importer.py` without a
cycle. Start there when tracing what an import does.

## Conventions

Formatting and lint rules are ruff's job, configured in `pyproject.toml`; run
`poe format` rather than arguing about style. [ty](https://docs.astral.sh/ty/)
checks the type annotations of the package; it is still in beta, so an update of
it may report new findings. Beyond that:

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
* **The README links with absolute URLs only.** It will also be the project
  description on PyPI, where relative links and links to its own sections lead
  nowhere. Other documents link relatively. `tests/test_docs.py` checks both, and
  that every linked file and heading exists.

### Dependencies

The package has **no runtime dependencies** and should keep it that way. The
reason is concrete: the import runs on someone's own machine against a
multi-gigabyte file, and "install this first" is the step where that stops
happening.

Dev-only dependencies are fine when they earn their place — currently
`datasette` (the thing the output is for, and what validates `metadata.yml`),
`ruff`, `ty`, and `poethepoet` (the task runner, which is why there is no Makefile).
To add one, say in the pull request what it does that the standard library
cannot, and whether it is needed at runtime or only for development.
Adding a runtime dependency needs a stronger case than convenience.

## Recipes

### Adding support for a new element

When Apple adds an element, `schema-diff` and the `unhandled` table will point
at it. Then:

1. **Update the schema.** Add the declaration to
   `apple_health_to_sqlite/schema/healthkit-export-v17.dtd`, copying it from a real
   export's DTD (see below). This is the source of truth; nothing else moves
   until it does.
2. **Add a handler** in `apple_health_to_sqlite/importer.py`, and list the element in
   `TOP_LEVEL_ELEMENTS` or, under its parent, in `CHILD_ELEMENTS`. `tests/test_dtd.py`
   fails until every declared element, nesting and attribute is accounted for, which
   is the point.
3. **Add a table** in `apple_health_to_sqlite/db.py` if the data does not fit an existing
   one. Give it a content-addressed primary key so re-import stays idempotent;
   child tables key on `(parent_id, seq)` or `(parent_id, name)`. A new column on
   an existing table goes at the end of it, with a step in `MIGRATIONS`; and since
   `INSERT OR IGNORE` leaves existing rows as they are, it also needs an entry in
   `Importer.STATEMENTS` if a re-import is to fill it in.
4. **Synthesize it.** Teach `apple_health_to_sqlite/synthetic.py` to emit the element —
   `tests/test_synthetic.py` asserts the synthesizer covers every element that
   can occur, so this is not optional. Synthetic exports must stay valid
   against the schema: only declared attributes, only declared nesting. An
   element that no content model references cannot be synthesized at all;
   cover that with a fixture instead.
5. **Test it.** A focused fixture in `tests/fixtures/` for the edge cases, and
   assertions on the synthetic data for the normal path.
6. **Document it** in the database layout table in docs/database.md.

### Refreshing the bundled schema

Only someone with a real export can do this, and they can do it without
sharing the file:

```bash
python3 -m apple_health_to_sqlite schema-diff /path/to/Export.zip
```

The output is declaration differences only — element and attribute names — so
it is safe to paste into an issue. Apply those differences by hand to
`apple_health_to_sqlite/schema/healthkit-export-v17.dtd`, or, if the version has
moved on, replace the file wholesale with `extract-schema` output, named after the
new version, and update `BUNDLED_DTD` in `dtd.py`. Keep Apple's own comments: two of
them ("Any Records that appear as children of a correlation also appear as
top-level records", and the same for a Workout) describe behaviour the importer
depends on, and tests assert they are still there.

Then follow the recipe above for anything newly declared, and record what you
learned in `apple_health_to_sqlite/schema/NOTES.md` — its Findings section is where the
non-obvious parts of the format live.

Supported versions are 14 and newer. Adding support for older exports is not a
goal; see NOTES.md for what would be involved.

### Refreshing the catalogue

Two generated files, both read from Apple's HealthKit documentation, so
refreshing them needs no export and no permission:

* `apple_health_to_sqlite/catalog/category_values.json` — what a category record's value
  name means: that `HKCategoryValueSeverityModerate` is `Moderate`, and the
  third rung of a four-rung scale.
* `apple_health_to_sqlite/catalog/quantity_types.json` — whether a quantity type's
  samples are cumulative or discrete, which is what `daily_metrics` reads to
  fill in `day_value`.

```bash
poe refresh-catalog
```

A few minutes and a couple of hundred requests; `--only categories` or
`--only quantities` does one of them. Review the diff as documentation moving
rather than code changing, and read `apple_health_to_sqlite/catalog/NOTES.md` first if you
are changing *what* is generated — in particular before touching `SCALE_ENUMS`,
the one field there that is a judgement rather than something Apple states.

Worth doing when Apple ships new types, which is most iOS releases. Nothing
breaks in the meantime: an unknown type or value keeps its raw name and its
aggregates, and only loses the interpretation.

### Test data

Two sources, both synthetic:

* `apple_health_to_sqlite/synthetic.py` for realistic, multi-day data. `support.sample_database()`
  caches one per test run.
* `tests/fixtures/*.xml` for edge cases, where reading the exact input matters
  more than realism. Fixtures contain only the `<HealthData>` body;
  `support.write_export()` wraps them in the real header and zips them.

If you hit a bug that only your real export triggers, reproduce it as a
fixture: reduce it to the structure that breaks, replace the values with
obviously fake ones, and commit *that*.

## Releasing

Versions follow [Semantic Versioning](https://semver.org). The version is
`__version__` in `apple_health_to_sqlite/__init__.py`, and it changes only in a
release commit; between releases, `main` carries the version of the last
release. While the version is 0.x, a change that breaks the command line or the
database layout raises the minor version, everything else the patch version.

[CHANGELOG.md](CHANGELOG.md) follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/): `## [Unreleased]` first,
then `## [X.Y.Z] - YYYY-MM-DD` for each release, newest first, with entries under
`### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed` and
`### Security`. Link references end the file. A change that users can notice adds
an entry under `Unreleased`, written for them.
[tools/release.py](tools/release.py) parses the file, and
`tests/test_release.py` checks its structure.

Pushing a tag `vX.Y.Z` starts
[.github/workflows/release.yml](.github/workflows/release.yml). It checks that the
tagged commit is on `main` and that the tag, `__version__` and the changelog agree,
runs all CI jobs on that commit, builds the sdist and the wheel once, and tries the
wheel on Linux, macOS and Windows: `--version`, then an import of invented data.
Then it publishes the same files to TestPyPI, waits for the maintainer to approve
the deployment to PyPI, publishes them there, and creates a GitHub Release with the
changelog section as its notes. Neither index holds a token for this project: they
trust the workflow through
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/).

### Cutting release X.Y.Z

1. In CHANGELOG.md, move the entries under `Unreleased` into a new section
   `## [X.Y.Z] - <today>` below it, point the link reference `[Unreleased]` to
   `compare/vX.Y.Z...HEAD`, and add `[X.Y.Z]` pointing to `releases/tag/vX.Y.Z`.
   Set `__version__` to `"X.Y.Z"`.
2. Dry run:

   ```bash
   python3 tools/release.py check vX.Y.Z
   poe test-sdist
   uv build --clear
   uvx twine check --strict dist/*
   ```

3. Commit as "Release X.Y.Z", push to `main` and wait until CI is green.
4. Tag and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "X.Y.Z"
   git push origin vX.Y.Z
   ```

5. In the run of the release workflow (*Actions → release*), once "publish to
   TestPyPI" has finished, look at the version's page on TestPyPI. Then approve the
   deployment to `pypi` (*Review deployments*). Nothing on PyPI can be uploaded
   again, so this is the last point to stop.
6. Check the version's page on PyPI and the GitHub Release.

A version number is never reused, and a tag that was published is never moved. A
bad release is yanked on PyPI and followed by a patch release. After a failed run:

* **Before "publish to TestPyPI"** (the check, the tests, the build or the smoke
  test): fix the cause in a new commit, delete the tag and create it again on that
  commit. The version stays the same.
* **From "publish to TestPyPI" on, a rejected approval included:** the version is
  used up, because TestPyPI, like PyPI, accepts each file name only once. Release
  the fix as the next patch version, with the changelog section renamed to it.
* **After "publish to PyPI", in the GitHub Release:** re-run the failed job; it
  uses the same files.

## Pull requests

* `poe check` passes.
* One concern per pull request. Formatting churn in files you did not otherwise
  touch makes review harder — leave it out.
* Say why, not just what. If the change is driven by something in a real
  export, describe the structure, never paste the data.
* New behaviour comes with a test. Bug fixes come with the test that failed
  first.
