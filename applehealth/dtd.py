"""Read, parse and compare the DTD that Apple embeds in every Health export.

`Export.xml` opens with a `<!DOCTYPE HealthData [ … ]>` internal subset that
declares every element and attribute the file can contain, preceded by a
version comment. That block is the only schema Apple ships, so this project
treats it as authoritative: a copy lives in `applehealth/schema/`, the
generator emits it verbatim, and `schema-diff` compares a real export to it.

The parser here is intentionally small. It understands `<!ELEMENT>` and
`<!ATTLIST>` well enough to answer "which elements and attributes exist", which
is all the rest of the project asks of it. It is not a validating parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import IO

#: Oldest HealthKit export version this project claims to support.
MIN_EXPORT_VERSION = 14

BUNDLED_DTD = "healthkit-export-v14.dtd"

#: How much of Export.xml to read while looking for the end of the DOCTYPE.
#: The real block is ~5 kB; the cap only exists so a malformed file cannot pull
#: a multi-gigabyte export into memory.
_DTD_SEARCH_LIMIT = 512 * 1024

#: Keywords that appear inside a content model but are not element names.
_CONTENT_KEYWORDS = frozenset({"EMPTY", "ANY", "PCDATA"})

_NAME_RE = re.compile(r"[A-Za-z_][\w.-]*")
_ELEMENT_RE = re.compile(r"<!ELEMENT\s+(\S+)\s+([^>]*)>", re.DOTALL)
_ATTLIST_RE = re.compile(r"<!ATTLIST\s+(\S+)\s+([^>]*)>", re.DOTALL)
_VERSION_RE = re.compile(r"HealthKit Export Version:\s*(\d+)")
_DOCTYPE_START = "<!DOCTYPE"
_DOCTYPE_END = "]>"


class SchemaError(Exception):
    """Raised when an export's schema is missing or unsupported."""


@dataclass
class Element:
    """One `<!ELEMENT>` declaration plus the `<!ATTLIST>` that belongs to it."""

    name: str
    content: str
    attributes: dict[str, str] = field(default_factory=dict)

    def children(self) -> set[str]:
        """Element names appearing in this element's content model."""
        return {
            name
            for name in _NAME_RE.findall(self.content)
            if name not in _CONTENT_KEYWORDS
        }


@dataclass
class Schema:
    """A parsed DTD: the element declarations and the export version."""

    elements: dict[str, Element] = field(default_factory=dict)
    version: int | None = None
    text: str = ""

    def __contains__(self, name: str) -> bool:
        return name in self.elements

    def attributes_of(self, name: str) -> set[str]:
        element = self.elements.get(name)
        return set(element.attributes) if element else set()

    def reachable_elements(self, root: str = "HealthData") -> set[str]:
        """Elements that can actually occur in a document, from `root` down.

        Apple's DTD declares at least one element that no content model
        references — `WorkoutActivity` in version 14 — so "declared" and "can
        appear" are not the same set. The generator can only produce the
        reachable ones without emitting invalid XML.
        """
        seen: set[str] = set()
        pending = [root]
        while pending:
            name = pending.pop()
            if name in seen or name not in self.elements:
                continue
            seen.add(name)
            pending.extend(self.elements[name].children())
        return seen

    def require_supported(self) -> int:
        """Check the version gate, returning the version on success."""
        if self.version is None:
            raise SchemaError(
                "No 'HealthKit Export Version' comment found in the DTD. This "
                "does not look like an Apple Health export."
            )
        if self.version < MIN_EXPORT_VERSION:
            raise SchemaError(
                f"HealthKit export version {self.version} is not supported; "
                f"this tool requires version {MIN_EXPORT_VERSION} or newer. "
                f"Re-export from a current version of iOS, or see "
                f"applehealth/schema/NOTES.md for what changed."
            )
        return self.version


def parse(text: str) -> Schema:
    """Parse DTD text into a `Schema`."""
    schema = Schema(text=text)

    match = _VERSION_RE.search(text)
    if match:
        schema.version = int(match.group(1))

    for name, content in _ELEMENT_RE.findall(text):
        schema.elements[name] = Element(name=name, content=" ".join(content.split()))

    for name, body in _ATTLIST_RE.findall(text):
        element = schema.elements.setdefault(name, Element(name=name, content=""))
        element.attributes.update(_parse_attlist(body))

    return schema


def _parse_attlist(body: str) -> dict[str, str]:
    """Turn an ATTLIST body into `{attribute: declared type}`.

    The body is a flat sequence of `name type default` triples. HealthKit only
    ever uses `CDATA`, but enumerated types `(a|b|c)` are tokenised correctly
    anyway so that a future export does not silently confuse the parser.
    """
    tokens = body.split()
    attributes: dict[str, str] = {}
    index = 0
    while index < len(tokens) - 1:
        name = tokens[index]
        index += 1
        if tokens[index].startswith("("):
            parts = []
            while index < len(tokens):
                parts.append(tokens[index])
                index += 1
                if parts[-1].endswith(")"):
                    break
            declared = " ".join(parts)
        else:
            declared = tokens[index]
            index += 1
        attributes[name] = declared
        # Skip the default declaration (#REQUIRED, #IMPLIED, a literal, ...).
        if (
            index < len(tokens)
            and tokens[index].startswith("#")
            or index < len(tokens)
            and tokens[index].startswith(('"', "'"))
        ):
            index += 1
    return attributes


def bundled() -> Schema:
    """The committed schema shipped inside the package."""
    return parse(bundled_text())


def bundled_text() -> str:
    return (
        resources.files("applehealth.schema").joinpath(BUNDLED_DTD).read_text("utf-8")
    )


def extract(stream: IO[bytes]) -> str:
    """Pull the DOCTYPE block out of the head of an `Export.xml` byte stream."""
    buffer = b""
    while len(buffer) < _DTD_SEARCH_LIMIT:
        chunk = stream.read(65536)
        if not chunk:
            break
        buffer += chunk
        text = buffer.decode("utf-8", errors="replace")
        start = text.find(_DOCTYPE_START)
        if start != -1:
            end = text.find(_DOCTYPE_END, start)
            if end != -1:
                return text[start : end + len(_DOCTYPE_END)] + "\n"
    raise SchemaError(
        "No <!DOCTYPE HealthData [ … ]> block found at the start of the export. "
        "Apple normally embeds the schema there; without it the export cannot "
        "be version-checked."
    )


def diff(expected: Schema, actual: Schema) -> list[str]:
    """Compare two schemas, returning human-readable difference lines.

    Deliberately reports declarations only — element and attribute names — so
    the output of `schema-diff` on a real export is safe to share.
    """
    lines: list[str] = []

    if expected.version != actual.version:
        lines.append(
            f"export version: bundled {expected.version}, export {actual.version}"
        )

    for name in sorted(set(actual.elements) - set(expected.elements)):
        lines.append(f"+ element {name} (in export, missing from bundled schema)")
    for name in sorted(set(expected.elements) - set(actual.elements)):
        lines.append(f"- element {name} (in bundled schema, missing from export)")

    for name in sorted(set(expected.elements) & set(actual.elements)):
        mine, theirs = expected.elements[name], actual.elements[name]
        for attribute in sorted(set(theirs.attributes) - set(mine.attributes)):
            lines.append(f"+ attribute {name}/@{attribute}")
        for attribute in sorted(set(mine.attributes) - set(theirs.attributes)):
            lines.append(f"- attribute {name}/@{attribute}")
        if mine.content != theirs.content and mine.content and theirs.content:
            lines.append(
                f"~ content model {name}:\n"
                f"    bundled: {mine.content}\n"
                f"    export:  {theirs.content}"
            )

    return lines
