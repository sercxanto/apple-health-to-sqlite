"""What the values of category records mean.

The DTD declares `value CDATA #IMPLIED` and can say no more, because the
meaning of a category record's value depends on its type: a
`HKCategoryTypeIdentifierLowerBackPain` record carries one of the five
`HKCategoryValueSeverity` names, a `HKCategoryTypeIdentifierSleepAnalysis`
record one of eight sleep stages. That mapping exists only in Apple's HealthKit
documentation, so `tools/refresh_catalog.py` reads it from there and writes
`category_values.json`, which this module loads into two small tables.

**This is not a second schema.** The DTD stays the source of truth for what an
export contains; the catalogue only attaches meaning to values it already
contains, and nothing on the import path consults it. A type or value missing
from the catalogue — Apple adds both with every iOS release — imports exactly
as it did before and simply shows its raw name, which is why the views join
outwards and fall back to `value_text`.

Loaded into the database rather than applied during the import for two
reasons: the raw `value_text` stays untouched, so nothing is lost in
translation, and refreshing the catalogue after an iOS release means rerunning
`derive`, not reimporting a five-gigabyte export.
"""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).parent / "category_values.json"

SCHEMA = """
DROP TABLE IF EXISTS category_types;
CREATE TABLE category_types (
    type TEXT PRIMARY KEY,
    value_enum TEXT,
    -- Apple's own grouping of the identifiers: family is the broad one
    -- (Symptoms, Reproductive Health), topic the finer one within it
    -- (Musculoskeletal, Heart and Lung).
    family TEXT,
    topic TEXT
) WITHOUT ROWID;

DROP TABLE IF EXISTS category_values;
CREATE TABLE category_values (
    -- The name as it appears in the export, e.g.
    -- HKCategoryValueSeverityModerate. Unique across enumerations, since
    -- every member name is prefixed with the enumeration it belongs to.
    value TEXT PRIMARY KEY,
    value_enum TEXT,
    value_short TEXT,
    -- Position on the enumeration's ladder, or NULL where its members do not
    -- form one. Meant for ordering, not for arithmetic: the distance from
    -- Mild to Moderate is not a quantity.
    ordinal INTEGER
) WITHOUT ROWID;
"""


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """The catalogue as generated, cached for the life of the process."""
    return json.loads(DATA_FILE.read_text())


def install(connection: sqlite3.Connection) -> int:
    """(Re)create the catalogue tables. Returns the number of value names.

    Does not commit; the caller does. Rebuilt wholesale on every run, so that
    a refreshed catalogue reaches existing databases without a migration.
    """
    catalog = load()
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO category_types VALUES (?, ?, ?, ?)",
        [
            (type_name, entry["enum"], entry["family"], entry["topic"])
            for type_name, entry in catalog["types"].items()
        ],
    )
    values = [
        (member["name"], enum, member["short"], member["ordinal"])
        for enum, entry in catalog["enums"].items()
        for member in entry["members"]
    ]
    connection.executemany("INSERT INTO category_values VALUES (?, ?, ?, ?)", values)
    return len(values)
