"""The changelog, the single version number, and tools/release.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from apple_health_to_sqlite import __version__

from . import support

ROOT = Path(__file__).parent.parent
REPOSITORY = "https://github.com/sercxanto/apple-health-to-sqlite"

UNRELEASED_FIX = "## [Unreleased]\n\n### Fixed\n\n- A fix that is not released yet.\n\n"
RELEASED = f"""# Changelog

Preamble.

{UNRELEASED_FIX}## [0.2.0] - 2026-12-01

### Added

- A new option.

### Fixed

- A fix.

## [0.1.0] - 2026-11-01

### Added

- The first release.

[Unreleased]: {REPOSITORY}/compare/v0.2.0...HEAD
[0.2.0]: {REPOSITORY}/releases/tag/v0.2.0
[0.1.0]: {REPOSITORY}/releases/tag/v0.1.0
"""

UNRELEASED_ONLY = f"""# Changelog

## [Unreleased]

### Added

- Everything.

[Unreleased]: {REPOSITORY}/commits/main
"""


def load_release_tool():
    """tools/ is not a package, so the module is loaded from its path."""
    spec = importlib.util.spec_from_file_location(
        "release_tool", ROOT / "tools" / "release.py"
    )
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up here while the class is being created.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = load_release_tool()


def write_release(changelog: str, version: str, newline: str = "\n") -> Path:
    root = support.workspace("release")
    (root / "CHANGELOG.md").write_bytes(changelog.replace("\n", newline).encode())
    init = root / "apple_health_to_sqlite" / "__init__.py"
    init.parent.mkdir()
    init.write_bytes(
        f'"""Docstring."""{newline}{newline}__version__ = "{version}"{newline}'.encode()
    )
    return root


class ThisRepositoryTest(unittest.TestCase):
    def changelog(self) -> str:
        return (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_the_changelog_has_the_expected_structure(self):
        self.assertEqual(release.problems(self.changelog()), [])

    def test_the_newest_release_of_the_changelog_is_the_package_version(self):
        newest = release.newest_release(self.changelog())
        # Before the first release, any version is accepted.
        if newest is not None:
            self.assertEqual(newest.name, __version__)

    def test_the_version_is_kept_in_one_place(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
        self.assertNotIn("version", pyproject["project"])
        self.assertEqual(
            pyproject["tool"]["setuptools"]["dynamic"]["version"],
            {"attr": "apple_health_to_sqlite.__version__"},
        )

    def test_the_tool_reads_the_version_the_package_reports(self):
        self.assertEqual(release.package_version(ROOT), __version__)


class ChangelogStructureTest(unittest.TestCase):
    def test_a_well_formed_changelog_has_no_problems(self):
        for changelog in (RELEASED, UNRELEASED_ONLY):
            self.assertEqual(release.problems(changelog), [])

    def test_a_changelog_that_breaks_the_structure_is_reported(self):
        without_unreleased = RELEASED.replace(UNRELEASED_FIX, "")
        cases = {
            "unreleased missing": (without_unreleased, "first section"),
            "unreleased below a release": (
                without_unreleased.replace(
                    "## [0.1.0]", "## [Unreleased]\n\n## [0.1.0]"
                ),
                "first section",
            ),
            "date missing": (
                RELEASED.replace("## [0.2.0] - 2026-12-01", "## [0.2.0]"),
                "no valid date",
            ),
            "invalid date": (
                RELEASED.replace("2026-12-01", "2026-13-01"),
                "no valid date",
            ),
            "date in another format": (
                RELEASED.replace("2026-12-01", "1 December 2026"),
                "no valid date",
            ),
            "two-part version": (
                RELEASED.replace("## [0.2.0]", "## [0.2]"),
                "does not name a version",
            ),
            "heading without brackets": (
                RELEASED.replace("## [0.2.0]", "## 0.2.0"),
                "is neither",
            ),
            "wrong order": (
                RELEASED.replace("[0.2.0] - 2026-12-01", "[0.0.9] - 2026-12-01"),
                "newest first",
            ),
            "unknown category": (
                RELEASED.replace("### Fixed\n\n- A fix.", "### Improved\n\n- A fix."),
                "'### Improved' in [0.2.0]",
            ),
            "category on the wrong level": (
                RELEASED.replace("### Added\n\n- A new", "#### Added\n\n- A new"),
                "'#### Added' in [0.2.0]",
            ),
            "link reference missing": (
                RELEASED.replace(f"[0.1.0]: {REPOSITORY}/releases/tag/v0.1.0\n", ""),
                "link reference of [0.1.0]",
            ),
            "unreleased link not moved on": (
                RELEASED.replace("compare/v0.2.0...HEAD", "commits/main"),
                "link reference of [Unreleased]",
            ),
        }
        for name, (changelog, problem) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(changelog, RELEASED)
                found = release.problems(changelog)
                self.assertTrue(any(problem in text for text in found), found)


class CheckTest(unittest.TestCase):
    def test_accepts_a_tag_that_agrees_with_the_version_and_the_changelog(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline):
                root = write_release(RELEASED, "0.2.0", newline)
                self.assertEqual(release.check("v0.2.0", root), [])

    def test_names_both_versions_if_the_tag_does_not_match(self):
        found = release.check("v0.2.1", write_release(RELEASED, "0.2.0"))
        self.assertTrue(any("v0.2.1" in text and "0.2.0" in text for text in found))
        self.assertTrue(any("no section ## [0.2.1]" in text for text in found))

    def test_requires_a_changelog_section_for_the_version(self):
        self.assertEqual(
            release.check("v0.1.0", write_release(UNRELEASED_ONLY, "0.1.0")),
            [
                "CHANGELOG.md has no section ## [0.1.0] - YYYY-MM-DD "
                "as its newest release (newest: none)"
            ],
        )

    def test_requires_the_version_to_be_the_newest_release(self):
        self.assertEqual(
            release.check("v0.1.0", write_release(RELEASED, "0.1.0")),
            [
                "CHANGELOG.md has no section ## [0.1.0] - YYYY-MM-DD "
                "as its newest release (newest: [0.2.0])"
            ],
        )

    def test_requires_entries_in_the_section(self):
        changelog = RELEASED.replace(
            "### Added\n\n- A new option.\n\n### Fixed\n\n- A fix.\n", ""
        )
        self.assertEqual(
            release.check("v0.2.0", write_release(changelog, "0.2.0")),
            ["the section [0.2.0] of CHANGELOG.md has no entries"],
        )

    def test_reports_the_problems_of_the_changelog(self):
        changelog = RELEASED.replace("## [0.1.0] - 2026-11-01", "## [0.1.0]")
        self.assertEqual(
            release.check("v0.2.0", write_release(changelog, "0.2.0")),
            ["'## [0.1.0]' has no valid date YYYY-MM-DD"],
        )


class NotesTest(unittest.TestCase):
    def test_are_the_section_without_its_heading_and_the_link_references(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline):
                root = write_release(RELEASED, "0.2.0", newline)
                self.assertEqual(
                    release.notes("0.2.0", root),
                    "### Added\n\n- A new option.\n\n### Fixed\n\n- A fix.",
                )
                self.assertEqual(
                    release.notes("0.1.0", root), "### Added\n\n- The first release."
                )

    def test_of_a_missing_or_empty_section_are_none(self):
        changelog = RELEASED.replace("- The first release.\n", "")
        root = write_release(changelog, "0.2.0")
        self.assertIsNone(release.notes("0.3.0", root))
        self.assertIsNone(release.notes("0.1.0", root))


class CommandLineTest(unittest.TestCase):
    def run_tool(self, root: Path, *arguments: str) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(release, "ROOT", root),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            status = release.main(list(arguments))
        return status, output.getvalue(), errors.getvalue()

    def test_exits_with_the_verdict_and_prints_the_problems(self):
        root = write_release(RELEASED, "0.2.0")
        self.assertEqual(self.run_tool(root, "check", "v0.2.0")[0], 0)

        status, _, errors = self.run_tool(root, "check", "v0.3.0")
        self.assertEqual(status, 1)
        self.assertIn(
            "error: the tag v0.3.0 does not match the package version 0.2.0", errors
        )

    def test_prints_the_notes_of_a_version(self):
        root = write_release(RELEASED, "0.2.0")
        status, output, _ = self.run_tool(root, "notes", "0.1.0")
        self.assertEqual(status, 0)
        self.assertEqual(output, "### Added\n\n- The first release.\n")
        self.assertEqual(self.run_tool(root, "notes", "0.3.0")[0], 1)


if __name__ == "__main__":
    unittest.main()
