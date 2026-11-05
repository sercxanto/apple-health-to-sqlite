"""Command line interface.

python3 -m apple_health_to_sqlite <command>    # from a checkout, no install
apple-health-to-sqlite <command>               # once installed
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from . import __version__, dtd, layout, pipeline, schema_tools, synthetic
from .archive import ExportNotFound
from .importer import ImportOptions


def _wrapped(text: str, width: int = 76) -> str:
    """Wrap every paragraph of `text`, keeping the blank lines between them.

    argparse offers a formatter that reflows to the terminal width but
    collapses the paragraphs into one while doing it, and a raw one that keeps
    the paragraphs and wraps nothing. The descriptions below want both, so
    they are wrapped here and handed to the raw formatter.
    """
    return "\n\n".join(
        textwrap.fill(paragraph, width) for paragraph in text.split("\n\n")
    )


IMPORT_DESCRIPTION = _wrapped(
    f"""\
Import an export into a directory that Datasette can open as it is.

Creates DIRECTORY if necessary, creates or extends DIRECTORY/{layout.DATABASE_NAME}
in it, and copies the bundled configuration ({" and ".join(layout.CONFIG_FILES)})
next to the database, overwriting the copies of an earlier run. Afterwards,
'datasette DIRECTORY' shows the database with its facets and canned queries.

Importing into the same directory again is safe: rows you already have are
skipped and new ones are added.\
"""
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apple-health-to-sqlite",
        description="Convert an Apple Health export to SQLite, ready for Datasette.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser(
        "import",
        help="import an export into a directory Datasette can open",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=IMPORT_DESCRIPTION,
    )
    importer.add_argument(
        "export", help="Export.zip, an unpacked directory, or Export.xml"
    )
    importer.add_argument(
        "directory",
        help=f"directory holding {layout.DATABASE_NAME}; created if missing",
    )
    importer.add_argument(
        "--types",
        nargs="+",
        metavar="TYPE",
        help="only these record types, short or fully qualified "
        "(e.g. StepCount HeartRate)",
    )
    importer.add_argument(
        "--exclude-types",
        nargs="+",
        metavar="TYPE",
        help="skip these record types; excluding HeartRate typically halves "
        "the database",
    )
    importer.add_argument(
        "--since", metavar="YYYY-MM-DD", help="only data from this date on"
    )
    importer.add_argument(
        "--no-routes", action="store_true", help="skip the GPX workout routes"
    )
    importer.add_argument(
        "--no-derived",
        action="store_true",
        help="skip rebuilding daily_metrics and the views",
    )
    importer.add_argument("--quiet", action="store_true", help="no progress output")

    synthesizer = commands.add_parser(
        "synthesize", help="write a synthetic export for testing"
    )
    synthesizer.add_argument("output", nargs="?", default="export.zip")
    synthesizer.add_argument("--days", type=int, default=30)
    synthesizer.add_argument(
        "--end-date", metavar="YYYY-MM-DD", help="last day (default: today)"
    )
    synthesizer.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="sampling density multiplier; 4 means four times as many samples",
    )
    synthesizer.add_argument("--seed", type=int, default=42)
    synthesizer.add_argument(
        "--utc-offset", type=int, default=2, help="timezone offset of the timestamps"
    )
    synthesizer.add_argument("--locale", default="en_US")
    synthesizer.add_argument("--quiet", action="store_true")

    extract = commands.add_parser(
        "extract-schema", help="print the DTD embedded in an export"
    )
    extract.add_argument("export")
    extract.add_argument("-o", "--output", help="write to this file instead of stdout")

    compare = commands.add_parser(
        "schema-diff", help="compare an export's schema with the bundled one"
    )
    compare.add_argument("export")

    return parser


def command_import(args: argparse.Namespace) -> int:
    layout.check_target(args.directory)
    database = layout.database_path(args.directory)
    options = ImportOptions(
        types=tuple(args.types or ()),
        exclude_types=tuple(args.exclude_types or ()),
        since=args.since,
        routes=not args.no_routes,
        derived=not args.no_derived,
        progress=not args.quiet,
    )

    def on_progress(seen: int, elapsed: float) -> None:
        rate = seen / max(elapsed, 0.001)
        print(f"  {seen:,} elements ({rate:,.0f}/s)", file=sys.stderr)

    result = pipeline.run_import(
        args.export,
        str(database),
        options,
        on_progress=on_progress if options.progress else None,
    )
    # After the import, so that a failed one leaves no configuration without a
    # database, which the next run would have to refuse as somebody else's.
    layout.copy_configuration(args.directory)

    if options.progress:
        _report(args, database, result)
    return 0


def _report(args: argparse.Namespace, database: Path, result) -> None:
    print(
        f"\nImported {args.export} into {database} "
        f"in {result.elapsed_s:.1f}s (export version {result.version})"
    )
    for table, count in pipeline.table_counts(str(database)).items():
        print(f"  {table:22} {count:>12,}")
    if result.routes_imported:
        print(f"  {'(gpx routes)':22} {result.routes_imported:>12,}")
    print(f"\nDatabase size: {database.stat().st_size / 1e6:.1f} MB")
    print(f"View it with: {layout.view_command(args.directory)}")

    if result.unhandled:
        print("\nElements without a handler (raw XML kept in table 'unhandled'):")
        for tag, count in sorted(result.unhandled.items(), key=lambda kv: -kv[1]):
            print(f"  {tag:22} {count:>12,}")
    if result.schema_differences:
        print(
            "\nThis export's schema differs from the bundled one. Run "
            "'schema-diff' for details; the output is safe to share."
        )


def command_synthesize(args: argparse.Namespace) -> int:
    options = synthetic.SynthesizeOptions(
        days=args.days,
        end_date=args.end_date,
        scale=args.scale,
        seed=args.seed,
        utc_offset=args.utc_offset,
        locale=args.locale,
    )

    def on_progress(done: int, total: int) -> None:
        print(f"  {done}/{total} days", file=sys.stderr)

    result = synthetic.synthesize(
        args.output, options, on_progress=None if args.quiet else on_progress
    )
    if not args.quiet:
        print(
            f"{result.path}: {result.size_bytes / 1e6:.1f} MB compressed, "
            f"{result.start_day} to {result.end_day} ({args.days} days), "
            f"{result.routes} workout routes"
        )
    return 0


def command_extract_schema(args: argparse.Namespace) -> int:
    text = schema_tools.extract_schema(args.export, args.output)
    if args.output:
        print(f"Wrote {args.output} ({len(text)} bytes)")
    else:
        print(text, end="")
    return 0


def command_schema_diff(args: argparse.Namespace) -> int:
    theirs, differences = schema_tools.compare_schema(args.export)
    bundled = dtd.bundled()
    print(f"bundled schema: export version {bundled.version}")
    print(f"your export:    export version {theirs.version}")
    if theirs.version is not None and theirs.version < dtd.MIN_EXPORT_VERSION:
        print(
            f"\nThis export is older than version {dtd.MIN_EXPORT_VERSION} and is "
            f"not supported."
        )
    if not differences:
        print("\nNo differences. The bundled schema matches your export.")
        return 0
    print(f"\n{len(differences)} difference(s) — declarations only, no health data:\n")
    for line in differences:
        print(f"  {line}")
    print(
        "\nSee CONTRIBUTING.md, 'Refreshing the bundled schema', for how to "
        "fold these in."
    )
    return 0


COMMANDS = {
    "import": command_import,
    "synthesize": command_synthesize,
    "extract-schema": command_extract_schema,
    "schema-diff": command_schema_diff,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (
        dtd.SchemaError,
        ExportNotFound,
        FileNotFoundError,
        layout.UnusableDirectory,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
