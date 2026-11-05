"""The links of the documentation.

The README is written to work as a project description on PyPI, where relative
links and links to its own sections lead nowhere, so it links only to absolute
URLs. Its links into this repository, and the relative links of the other
documents, must name files and headings that exist. Nothing here goes near the
network, so links to other sites are not checked.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"
PAGES = [
    *sorted((ROOT / "docs").glob("*.md")),
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "apple_health_to_sqlite" / "catalog" / "NOTES.md",
    ROOT / "apple_health_to_sqlite" / "schema" / "NOTES.md",
]

FENCED_CODE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)
INLINE_CODE = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^#+\s+(.+?)\s*$", re.MULTILINE)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]*)\)")
# The forms LINK does not see. A document using one would go unchecked, silently.
OTHER_LINK_FORM = re.compile(
    r"<a\s|<img\s|^\[[^\]]+\]:\s", re.IGNORECASE | re.MULTILINE
)
EXTERNAL = re.compile(r"[a-z]+:")


def read_without_fences(path: Path) -> str:
    return FENCED_CODE.sub("", path.read_text(encoding="utf-8"))


def read_prose(path: Path) -> str:
    """The text with all code removed: a snippet can hold brackets of its own."""
    return INLINE_CODE.sub("", read_without_fences(path))


def links(path: Path) -> list[str]:
    return LINK.findall(read_prose(path))


def anchors(path: Path) -> set[str]:
    """The ids GitHub gives the headings of a Markdown file.

    From the text with its inline code still in place, because a heading's code
    is part of its id.
    """
    return {
        re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        for heading in HEADING.findall(read_without_fences(path))
    }


def repository_prefixes() -> tuple[str, ...]:
    """How a link to a file on the main branch of this repository starts."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    repository = pyproject["project"]["urls"]["Homepage"]
    return tuple(f"{repository}/{kind}/main/" for kind in ("blob", "tree", "raw"))


def problem_with(source: Path, target: Path, anchor: str) -> str | None:
    """What is wrong with a link from `source`, or None if it resolves."""
    if not target.exists():
        return f"{source.name} links to {target}, which does not exist"
    if anchor and anchor not in anchors(target):
        return f"{source.name} links to #{anchor}, not a heading of {target.name}"
    return None


class ReadmeLinkTest(unittest.TestCase):
    def test_every_link_is_absolute(self):
        found = links(README)
        self.assertTrue(found, "no links found; is the pattern still right?")
        for link in found:
            with self.subTest(link=link):
                self.assertTrue(
                    link.startswith(("https://", "http://", "mailto:")),
                    "PyPI cannot resolve this link",
                )

    def test_links_into_the_repository_name_what_exists(self):
        prefixes = repository_prefixes()
        into_repository = [link for link in links(README) if link.startswith(prefixes)]
        self.assertTrue(into_repository, "no links found; has the repository moved?")
        for link in into_repository:
            prefix = next(prefix for prefix in prefixes if link.startswith(prefix))
            file, _, anchor = link.removeprefix(prefix).partition("#")
            with self.subTest(link=link):
                self.assertIsNone(problem_with(README, ROOT / file, anchor))


class PageLinkTest(unittest.TestCase):
    def test_relative_links_resolve(self):
        checked = 0
        for page in PAGES:
            for link in links(page):
                if EXTERNAL.match(link):
                    continue
                checked += 1
                file, _, anchor = link.partition("#")
                target = (page.parent / file).resolve() if file else page
                with self.subTest(page=page.name, link=link):
                    self.assertIsNone(problem_with(page, target, anchor))
        self.assertTrue(checked, "no links found; is the pattern still right?")


class LinkFormTest(unittest.TestCase):
    def test_documents_use_only_the_link_form_these_tests_check(self):
        for path in (README, *PAGES):
            with self.subTest(document=path.name):
                self.assertIsNone(OTHER_LINK_FORM.search(read_prose(path)))


if __name__ == "__main__":
    unittest.main()
