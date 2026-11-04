"""Regenerate the catalogues in `apple_health_to_sqlite/catalog` from Apple's docs.

The export's DTD declares `value CDATA #IMPLIED` and stops there, so nothing in
the file says that a `HKCategoryTypeIdentifierLowerBackPain` record carries one
of the five `HKCategoryValueSeverity` names while a
`HKCategoryTypeIdentifierSleepAnalysis` record carries one of eight sleep
stages. Nor does it say that a day of `StepCount` samples is meant to be added
up while a day of `HeartRate` samples is not. Apple documents both per
identifier, and this script turns the documentation into the two lookup tables
`apple_health_to_sqlite/catalog` ships:

* `category_values.json` — which value names a category type can carry.
* `quantity_types.json` — whether a quantity type's samples are cumulative or
  discrete, which is what tells `daily_metrics` whether `total` or `mean` is
  the meaningful number.

They are two files rather than one so that their diffs stay separately
readable: a documentation reshuffle among the symptoms should not land in the
same review as one among the nutrients. `--only` refreshes a single file.

**Why a generator and not a hand-written table.** There are roughly ninety
category identifiers across a dozen value enumerations, and Apple adds a few
every iOS release. Typing that once is tedious; keeping it correct by hand is
the part that would quietly fail.

**Where the data comes from.** The documentation pages are rendered from JSON
that is served under a parallel URL:

    https://developer.apple.com/documentation/healthkit/hkcategorytypeidentifier
    https://developer.apple.com/tutorials/data/documentation/healthkit/hkcategorytypeidentifier.json

The HTML pages are JavaScript-rendered and yield nothing useful to a plain
fetch; the JSON is the same content the page itself consumes. Three fields
carry the weight:

* `metadata.externalID` — `c:@HKCategoryTypeIdentifierBloating`, the Objective-C
  name, which is exactly the spelling the XML export uses. Deriving it from the
  Swift name (`bloating`) would be a guess; this is the fact.
* the reference to a `HKCategoryValue…` symbol in a type's discussion, which is
  how the docs say which enumeration that type's values come from.
* `topicSections` on the index pages, which is where the grouping comes from.

This runs at development time and its output is committed, so the package keeps
its "no runtime dependencies" promise and imports never touch the network.
Nothing here is on the import path.

Usage:

    poe refresh-catalog                        # rewrite both committed files
    poe refresh-catalog -- --only quantities   # just one of them
    poe refresh-catalog -- --dry-run

Read `apple_health_to_sqlite/catalog/NOTES.md` before changing what this writes; it
documents which fields are Apple's and which one is a judgement call.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "apple_health_to_sqlite" / "catalog"
CATEGORY_OUTPUT = CATALOG / "category_values.json"
QUANTITY_OUTPUT = CATALOG / "quantity_types.json"

DOCS = "https://developer.apple.com/documentation/healthkit"
DATA = "https://developer.apple.com/tutorials/data/documentation/healthkit"

#: The two index pages that between them list every category type identifier.
#: The symptom diary lives on its own page, linked from the index as a section
#: with a single entry, so it has to be walked separately.
INDEX_PAGES = ("hkcategorytypeidentifier", "symptom-type-identifiers")

#: The one index page that lists every quantity type identifier.
QUANTITY_INDEX = "hkquantitytypeidentifier"

#: Doc sections that are API surface rather than identifiers.
SKIP_SECTIONS = frozenset({"Initializers", "Instance Properties", "Operators"})

#: Apple's catch-all heading for identifiers it has not filed under a topic.
UNGROUPED = "Type Properties - generated"

#: Value names that sit outside their enumeration's ladder rather than at one
#: end of it, and so get no ordinal. Both spellings appear in exports.
UNORDERED_MEMBERS = frozenset({"Unspecified", "NotApplicable"})

#: Enumerations whose members form an intensity ladder, so that ordering them
#: means something: `Mild` really is less than `Moderate`.
#:
#: This is the one field here that is a judgement rather than something Apple
#: states — the documentation lists members in an order, but never says that
#: the order means anything. It is an explicit list rather than something
#: inferred from that order, because the order is not always a ladder:
#: `HKCategoryValueVaginalBleeding` reads Unspecified, Light, Medium, Heavy,
#: None, which would rank `None` above `Heavy`. Enumerations left out still get
#: their `value_short`; they just get no `ordinal`.
SCALE_ENUMS = frozenset(
    {
        "HKCategoryValueSeverity",
        "HKCategoryValuePresence",
        "HKCategoryValueMenstrualFlow",
        "HKCategoryValueCervicalMucusQuality",
    }
)

#: Politeness delay between requests. The crawl is about a hundred pages and
#: runs a few times a year; there is no reason to be fast about it.
DELAY_S = 0.2

_EXTERNAL_ID_RE = re.compile(r"^c:(?:@E@\w+)?@(HK\w+)$")

#: A discussion links either the enumeration itself (`HKCategoryValueSeverity`)
#: or one of its members (`HKCategoryValue.notApplicable`, which is how the
#: pages for mindful sessions and the like phrase it). Both name the
#: enumeration; the capture group is it.
_VALUE_SYMBOL_RE = re.compile(r"^(HKCategoryValue\w*)(?:\.\w+)?$")

#: How a quantity type's page states its aggregation style. The style itself is
#: prose — "measure cumulative values" — but the sentence always anchors it on
#: a link to `HKQuantityAggregationStyle`, and that link is what makes the
#: adjective a fact rather than a word that happens to appear nearby.
#:
#: The span between the two tolerates the phrasings Apple actually uses, which
#: differ more than the category pages do: "(described in …)", "(see …)", and
#: the audio-exposure wording "discrete values of the equivalent continuous
#: sound pressure level, described in …". It stops at a sentence end, so an
#: adjective from one sentence cannot bind to a link in the next.
_AGGREGATION_RE = re.compile(
    r"measures? (cumulative|discrete)\b[^.@]*@HKQuantityAggregationStyle[/@]"
)


class DocsError(RuntimeError):
    """The documentation was reachable but did not look the way we expect.

    Raised rather than papered over: a silently half-filled catalogue would
    show up as missing value names months later, which is exactly the failure
    this file exists to prevent.
    """


def fetch(path: str) -> dict[str, Any]:
    """Fetch one documentation page as parsed JSON. `path` is relative to DATA."""
    url = f"{DATA}/{path}.json"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise DocsError(f"{url} returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise DocsError(f"{url} is unreachable: {error.reason}") from error
    time.sleep(DELAY_S)
    return payload


def external_name(page: dict[str, Any]) -> str | None:
    """The Objective-C symbol name, which is the spelling the XML export uses."""
    match = _EXTERNAL_ID_RE.match(page.get("metadata", {}).get("externalID", ""))
    return match.group(1) if match else None


def sections(page: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Topic sections as `(title, references)`, minus the API-surface ones."""
    result = []
    for section in page.get("topicSections", []):
        title = section.get("title", "")
        if title in SKIP_SECTIONS:
            continue
        references = [
            page["references"][identifier]
            for identifier in section.get("identifiers", [])
            if identifier in page["references"]
        ]
        result.append((title, references))
    return result


def referenced_symbols(node: Any, found: list[str]) -> list[str]:
    """Collect `reference` identifiers in document order, depth first.

    Order matters: a type's discussion names its own value enumeration first
    and may go on to mention others.
    """
    if isinstance(node, dict):
        if node.get("type") == "reference" and "identifier" in node:
            found.append(node["identifier"])
        for value in node.values():
            referenced_symbols(value, found)
    elif isinstance(node, list):
        for value in node:
            referenced_symbols(value, found)
    return found


def value_enum_of(page: dict[str, Any]) -> str | None:
    """Which `HKCategoryValue…` enumeration this type's values come from.

    The docs express it as a link from the type's discussion — "you use the
    values in HKCategoryValueSeverity" — so the first such link is the answer.
    """
    for section in page.get("primaryContentSections", []):
        if section.get("kind") != "content":
            continue
        for identifier in referenced_symbols(section, []):
            title = page.get("references", {}).get(identifier, {}).get("title", "")
            match = _VALUE_SYMBOL_RE.match(title)
            if match:
                return match.group(1)
    return None


def inline_text(node: Any, out: list[str]) -> list[str]:
    """Flatten a content node into text, symbol links inlined as `@Name@`.

    The JSON splits a sentence into runs, so "measure cumulative values
    (described in HKQuantityAggregationStyle)" arrives as two text runs with a
    reference between them. Joining them back into one string is what lets a
    regular expression read the sentence the way a person does; the `@…@`
    markers keep the links visible to it, and cannot collide with the prose.
    """
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node.get("text", ""))
        elif node.get("type") == "codeVoice":
            out.append(node.get("code", ""))
        elif node.get("type") == "reference":
            symbol = node.get("identifier", "").split("/documentation/HealthKit/")[-1]
            out.append(f"@{symbol}@")
        else:
            for key in ("inlineContent", "content", "items"):
                if key in node:
                    inline_text(node[key], out)
    elif isinstance(node, list):
        for value in node:
            inline_text(value, out)
    return out


def discussion(page: dict[str, Any]) -> str:
    """The page's prose sections, flattened by `inline_text`."""
    return "".join(
        text
        for section in page.get("primaryContentSections", [])
        if section.get("kind") == "content"
        for text in inline_text(section, [])
    )


def aggregation_of(page: dict[str, Any]) -> str | None:
    """Whether this quantity type's samples are `cumulative` or `discrete`.

    Which is the fact `daily_metrics` needs: a day of cumulative samples adds
    up to the day's number, a day of discrete ones averages to it. Apple states
    it in the discussion, linking either `HKQuantityAggregationStyle` or one of
    its members — `discreteEquivalentContinuousLevel`, on the audio-exposure
    types — the same way category pages link either an enumeration or one of
    its values.

    `None` where the page says nothing, which for a handful of recent
    identifiers is simply the case: they have no discussion at all.
    """
    match = _AGGREGATION_RE.search(discussion(page))
    return match.group(1) if match else None


def collect_types() -> dict[str, dict[str, str | None]]:
    """Walk the index pages and return `{page path: {family, topic}}`.

    `family` is the heading the index files a type under — Symptoms,
    Reproductive Health, Mindfulness and Sleep. `topic` is the finer heading,
    which only the symptom page has: Musculoskeletal, Heart and Lung.
    """
    found: dict[str, dict[str, str | None]] = {}
    for index in INDEX_PAGES:
        page = fetch(index)
        symptoms = index == "symptom-type-identifiers"
        for title, references in sections(page):
            family = "Symptoms" if symptoms else (None if title == UNGROUPED else title)
            topic = title if symptoms else family
            for reference in references:
                url = reference.get("url", "")
                # Sections mix in the odd enum or sub-page: `Hearing` lists
                # HKCategoryValueEnvironmentalAudioExposureEvent beside the
                # identifiers, and `Symptoms` links the page walked next.
                if "/hkcategorytypeidentifier/" not in url:
                    continue
                found[url.rsplit("/documentation/healthkit/", 1)[-1]] = {
                    "family": family,
                    "topic": topic,
                }
    if not found:
        raise DocsError(
            "no category type identifiers found — the documentation layout has "
            "probably changed; check the JSON at "
            f"{DATA}/hkcategorytypeidentifier.json"
        )
    return found


def is_member(reference: dict[str, Any], enum: str) -> bool:
    """Is this reference a value of `enum`, rather than API around it?

    An enumeration page lists more than its values. `HKCategoryValueSleepAnalysis`
    also offers `allAsleepValues`, a `Set` of the asleep states, which is useful
    in Swift and is not something a record's `value` attribute can ever contain.
    Two things are values: a plain `case`, and the deprecated aliases Apple now
    exposes as a `static var` of the enumeration's own type — `asleep`, which
    predates the sleep stages and still appears in older samples.
    """
    fragments = reference.get("fragments", [])
    if not fragments:
        return False
    if fragments[0].get("text", "").strip() == "case":
        return True
    # `static var asleep: HKCategoryValueSleepAnalysis` is a value;
    # `static var allAsleepValues: Set<HKCategoryValueSleepAnalysis>` is not,
    # and both mention the enumeration — so compare the whole declared type.
    declaration = "".join(fragment.get("text", "") for fragment in fragments)
    _, separator, declared_type = declaration.partition(":")
    return bool(separator) and declared_type.strip() == enum


def members_of(enum: str) -> list[dict[str, Any]]:
    """The enumeration's members, in the order Apple documents them.

    Ordinals are positions in that order, skipping the members that are not on
    the ladder (`Unspecified`). Apple's numeric `rawValue`s are not in the
    documentation JSON at all, and would be the wrong thing anyway: for
    `HKCategoryValueSeverity` the raw values put `unspecified` at zero, below
    `notPresent`, which no query wants.
    """
    page = fetch(enum.lower())
    members: list[dict[str, Any]] = []
    ordinal = 0
    scale = enum in SCALE_ENUMS
    for _title, references in sections(page):
        for reference in references:
            if not is_member(reference, enum):
                continue
            # `HKCategoryValueSeverity.moderate` in Swift is
            # `HKCategoryValueSeverityModerate` in the export.
            case = reference.get("title", "").split(".")[-1]
            if not case:
                continue
            name = enum + case[0].upper() + case[1:]
            short = name[len(enum) :]
            on_ladder = scale and short not in UNORDERED_MEMBERS
            members.append(
                {
                    "name": name,
                    "short": short,
                    "ordinal": ordinal if on_ladder else None,
                }
            )
            if on_ladder:
                ordinal += 1
    if not members:
        raise DocsError(f"{enum} lists no members")
    return members


def collect_quantity_types() -> dict[str, str | None]:
    """Walk the quantity index and return `{page path: family}`.

    `family` is the heading the index files a type under — Activity, Nutrition,
    Vital signs — the same grouping the category catalogue carries. There is no
    finer topic here: the quantity index is one page and one level deep.
    """
    page = fetch(QUANTITY_INDEX)
    found: dict[str, str | None] = {}
    for title, references in sections(page):
        for reference in references:
            url = reference.get("url", "")
            if f"/{QUANTITY_INDEX}/" not in url:
                continue
            path = url.rsplit("/documentation/healthkit/", 1)[-1]
            found[path] = None if title == UNGROUPED else title
    if not found:
        raise DocsError(
            "no quantity type identifiers found — the documentation layout has "
            f"probably changed; check the JSON at {DATA}/{QUANTITY_INDEX}.json"
        )
    return found


def build_quantities() -> dict[str, Any]:
    """Crawl the quantity identifiers and assemble their catalogue."""
    types: dict[str, dict[str, Any]] = {}

    paths = collect_quantity_types()
    print(f"{len(paths)} quantity type identifiers", file=sys.stderr)

    for path in sorted(paths):
        page = fetch(path)
        identifier = external_name(page)
        if identifier is None:
            raise DocsError(f"{path} has no usable externalID")
        types[identifier] = {
            "aggregation": aggregation_of(page),
            "family": paths[path],
        }

    silent = sorted(k for k, v in types.items() if not v["aggregation"])
    if len(silent) == len(types):
        # One undocumented type is normal; all of them means the phrasing this
        # reads has changed, and writing the file would quietly empty
        # `day_value` for every metric in every database that reran `derive`.
        raise DocsError(
            "no quantity type states an aggregation style — the wording the "
            "discussions use has probably changed; check the JSON at "
            f"{DATA}/{QUANTITY_INDEX}/stepcount.json"
        )
    if silent:
        # Not fatal: a type whose page states nothing keeps its `total` and
        # `mean` and simply gets no `day_value`. Worth seeing, though.
        print(
            f"{len(silent)} without a documented aggregation style: "
            + ", ".join(silent),
            file=sys.stderr,
        )

    return {
        "_comment": (
            "Generated by tools/refresh_catalog.py from Apple's HealthKit "
            "documentation. Do not edit by hand. See NOTES.md."
        ),
        "source": DOCS,
        "retrieved": dt.date.today().isoformat(),
        "types": dict(sorted(types.items())),
    }


def build() -> dict[str, Any]:
    """Crawl the documentation and assemble the category catalogue."""
    types: dict[str, dict[str, Any]] = {}
    enums: dict[str, dict[str, Any]] = {}

    paths = collect_types()
    print(f"{len(paths)} category type identifiers", file=sys.stderr)

    for path in sorted(paths):
        page = fetch(path)
        identifier = external_name(page)
        if identifier is None:
            raise DocsError(f"{path} has no usable externalID")
        enum = value_enum_of(page)
        types[identifier] = {"enum": enum, **paths[path]}
        if enum and enum not in enums:
            enums[enum] = {
                "scale": enum in SCALE_ENUMS,
                "members": members_of(enum),
            }

    unresolved = sorted(k for k, v in types.items() if not v["enum"])
    if unresolved:
        # Not fatal: a type whose page does not name an enumeration still
        # imports, it just gets no `value_short`. Worth seeing, though.
        print(
            f"{len(unresolved)} without a documented value enumeration: "
            + ", ".join(unresolved),
            file=sys.stderr,
        )

    return {
        "_comment": (
            "Generated by tools/refresh_catalog.py from Apple's HealthKit "
            "documentation. Do not edit by hand. See NOTES.md."
        ),
        "source": DOCS,
        "retrieved": dt.date.today().isoformat(),
        "types": dict(sorted(types.items())),
        "enums": dict(sorted(enums.items())),
    }


def category_summary(catalog: dict[str, Any]) -> str:
    documented = sum(1 for entry in catalog["types"].values() if entry["enum"])
    return (
        f"{len(catalog['types'])} types ({documented} with a value enumeration), "
        f"{len(catalog['enums'])} enumerations"
    )


def quantity_summary(catalog: dict[str, Any]) -> str:
    styles = [entry["aggregation"] for entry in catalog["types"].values()]
    return (
        f"{len(styles)} types ({styles.count('cumulative')} cumulative, "
        f"{styles.count('discrete')} discrete, {styles.count(None)} unstated)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        choices=("categories", "quantities"),
        help="refresh one catalogue instead of both, keeping the other's diff out",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print a summary instead of writing the catalogues",
    )
    arguments = parser.parse_args(argv)

    wanted = {
        "categories": (CATEGORY_OUTPUT, build, category_summary),
        "quantities": (QUANTITY_OUTPUT, build_quantities, quantity_summary),
    }
    if arguments.only:
        wanted = {arguments.only: wanted[arguments.only]}

    for output, crawl, summarise in wanted.values():
        try:
            catalog = crawl()
        except DocsError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        summary = summarise(catalog)
        if arguments.dry_run:
            print(f"{output.relative_to(ROOT)}: {summary} (not written)")
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(catalog, indent=2, sort_keys=False) + "\n")
        print(f"{output.relative_to(ROOT)}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
