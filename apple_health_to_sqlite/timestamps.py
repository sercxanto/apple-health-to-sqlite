"""Parsing of the timestamp format Apple writes into Health exports.

Every date in an export looks like `2026-08-15 20:48:13 +0200`: local wall
clock plus the UTC offset that was in force at the time. Both halves matter, so
the importer stores both — a UTC instant for sorting and arithmetic, and the
local calendar date for grouping. Aggregating by UTC day would be wrong; a
night's sleep would land in two different days.

`strptime` handles this format, but it is roughly an order of magnitude slower
than the hand-rolled parser below, and an import touches this function several
million times.
"""

from __future__ import annotations

from datetime import date, datetime

#: Ordinal of the Unix epoch, used to turn a calendar date into a timestamp.
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


class InvalidDate(ValueError):
    """Raised when a date given as an option cannot be understood."""


#: Cache of `YYYY-MM-DD` to the UTC timestamp of that date at midnight. Exports
#: are ordered by time, so this hits on essentially every row after the first
#: of each day.
_day_cache: dict[str, int] = {}


def parse(value: str | None) -> tuple[str | None, str | None, int | None]:
    """Split an Apple timestamp into (local date, local ISO, UTC timestamp).

    Returns `(None, None, None)` for missing or malformed input rather than
    raising: a single unparsable timestamp in a multi-gigabyte export should
    not abort the import.

    >>> parse("2026-08-15 20:48:13 +0200")
    ('2026-08-15', '2026-08-15T20:48:13', 1786574893)
    """
    if not value or len(value) < 19:
        return None, None, None

    day = value[:10]
    midnight = _day_cache.get(day)
    if midnight is None:
        try:
            midnight = (
                date(int(day[:4]), int(day[5:7]), int(day[8:10])).toordinal()
                - _EPOCH_ORDINAL
            ) * 86400
        except ValueError:
            return None, None, None
        _day_cache[day] = midnight

    try:
        seconds = int(value[11:13]) * 3600 + int(value[14:16]) * 60 + int(value[17:19])
    except ValueError:
        return None, None, None

    timestamp = midnight + seconds
    if len(value) >= 25 and value[20] in "+-":
        try:
            offset = int(value[21:23]) * 3600 + int(value[23:25]) * 60
        except ValueError:
            return day, day + "T" + value[11:19], timestamp
        timestamp += -offset if value[20] == "+" else offset

    return day, day + "T" + value[11:19], timestamp


def parse_gpx_time(value: str | None) -> int | None:
    """Parse a GPX timestamp (`2026-08-01T15:12:00Z`) into a UTC timestamp.

    GPX times inside an export are always UTC, with or without fractional
    seconds.
    """
    if not value:
        return None
    normalised = value.replace("T", " ").replace("Z", "").strip()
    return parse(normalised[:19] + " +0000")[2]


def calendar_day(value: str) -> str:
    """Check a date the user typed and return it as `YYYY-MM-DD`.

    Unlike `parse`, this raises. A bad timestamp is one row among millions, but
    a bad date here is a mistyped option: carrying on without it would import
    everything while the user believes they filtered.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise InvalidDate(
            f"'{value}' is not a valid date. Use the form YYYY-MM-DD, "
            f"for example 2024-01-31."
        ) from None
