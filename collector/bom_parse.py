"""Parsers for the Bureau of Meteorology river-height products used by SEQ River Watch.

Four source formats, all from BoM's public feed (ftp://ftp.bom.gov.au/anon/gen/fwo/):

* River height bulletin (e.g. IDQ60286.html) - one row per gauge, grouped by area.
* Station history table (e.g. IDQ65389.540111.tbl.shtml) - 7 days of readings
  plus the gauge's flood classification levels.
* Flood-warning map page (e.g. IDQ65389.html) - a PopupRiver(...) call per gauge
  carrying coordinates, latest level, class, trend and minor/moderate/major levels.
* BOM-HCS observation files (e.g. IDQ65911_<timestamp>.hcs) - CSV of 15-minute
  water-level readings for every gauge in the state, timestamped in UTC.

Every parser is defensive: anything it cannot read stays missing (None) and is
never replaced with zero or a guess.
"""

from __future__ import annotations

import csv
import html
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

TZ_OFFSETS = {"AEST": 10, "AEDT": 11, "ACST": 9.5, "ACDT": 10.5, "AWST": 8, "UTC": 0}
AEST = timezone(timedelta(hours=10))  # Queensland has no daylight saving
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
WEEKDAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}

FLOOD_CLASSES = ("below", "minor", "moderate", "major")


class ParseError(ValueError):
    """The source did not have the structure we rely on; reject it rather than guess."""


# ---------------------------------------------------------------- small helpers

def clean(text: str | None) -> str:
    return " ".join(html.unescape(text or "").replace("\xa0", " ").split())


def to_float(text) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # reject NaN


def norm_id(station_id: str | None) -> str | None:
    """BoM station numbers are 6-digit strings with leading zeros (e.g. 040387)."""
    if not station_id:
        return None
    sid = station_id.strip()
    if sid.isdigit() and len(sid) < 6:
        sid = sid.zfill(6)
    return sid or None


def epoch_minutes(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() // 60)


def norm_class(text: str | None) -> tuple[str | None, str]:
    """Map BoM's flood classification wording to below/minor/moderate/major."""
    raw = clean(text)
    low = raw.lower()
    if not low:
        return None, raw
    if low.startswith("below"):
        return "below", raw
    for name in ("major", "moderate", "minor"):
        if low.startswith(name):
            return name, raw
    return None, raw  # e.g. "No Classification" - unknown, never "safe"


def norm_tendency(text: str | None) -> str | None:
    low = clean(text).lower()
    return low if low in ("rising", "falling", "steady") else None


def parse_issued(text: str) -> datetime | None:
    """'Issued at 10:24 AM AEST Thursday 22 October 2026' -> aware datetime."""
    m = re.search(
        r"Issued at\s+(\d{1,2})[:.](\d{2})\s*([AP]M)\s+([A-Z]{3,4})\s+\w+\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
    )
    if not m:
        return None
    hour, minute, ampm, tzname, day, month, year = m.groups()
    month_no = MONTHS.get(month.lower())
    if month_no is None or tzname not in TZ_OFFSETS:
        return None
    hour = int(hour) % 12 + (12 if ampm == "PM" else 0)
    tz = timezone(timedelta(hours=TZ_OFFSETS[tzname]))
    return datetime(int(year), month_no, int(day), hour, int(minute), tzinfo=tz)


def infer_obs_time(text: str, issued: datetime | None) -> datetime | None:
    """'10.14AM Thu' -> the most recent such time at or before the bulletin issue time."""
    m = re.search(r"(\d{1,2})[.:](\d{2})\s*([AP]M)\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)", text or "", re.I)
    if not m or issued is None:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    minute = int(m.group(2))
    weekday = WEEKDAYS[m.group(4).lower()[:3]]
    # Allow a few minutes of clock skew past the issue time, look back up to 7 days.
    limit = issued + timedelta(minutes=10)
    for back in range(0, 8):
        day = (issued - timedelta(days=back)).date()
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=issued.tzinfo)
        if candidate.weekday() == weekday and candidate <= limit:
            return candidate
    return None


def parse_crossing(text: str | None) -> dict | None:
    """'3.74 below Bridge' -> {'distance': 3.74, 'relation': 'below', 'feature': 'Bridge'}."""
    raw = clean(text)
    if not raw:
        return None
    m = re.match(r"(-?\d+(?:\.\d+)?)\s+(above|below)\s+(.+)$", raw, re.I)
    if not m:
        return {"text": raw}
    return {"text": raw, "distance": float(m.group(1)), "relation": m.group(2).lower(), "feature": m.group(3)}


def split_waterway(name: str) -> tuple[str, str]:
    """'Kedron Brook at Mitchelton (Osborne Rd)' -> ('Kedron Brook', 'Mitchelton (Osborne Rd)')."""
    m = re.match(r"(.+?)\s+at\s+(.+)$", name)
    return (m.group(1).strip(), m.group(2).strip()) if m else (name.strip(), "")


# ---------------------------------------------------------------- bulletin

class _RhbTable(HTMLParser):
    """Collect rows of the river height bulletin table (class contains 'rhb')."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[tuple[str, str, int]], list[str]]] = []
        self._depth = 0
        self._row = None
        self._cell = None
        self._links: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self._depth or "rhb" in (a.get("class") or "").split():
                self._depth += 1
            return
        if not self._depth:
            return
        if tag == "tr":
            self._row, self._links = [], []
        elif tag in ("td", "th") and self._row is not None:
            span = a.get("colspan") or "1"
            self._cell = [tag, [], int(span) if span.isdigit() else 1]
        elif tag == "a" and a.get("href"):
            self._links.append(a["href"])
        elif tag == "br" and self._cell is not None:
            self._cell[1].append(" ")

    def handle_endtag(self, tag):
        if not self._depth:
            return
        if tag == "table":
            self._depth -= 1
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append((self._cell[0], clean("".join(self._cell[1])), self._cell[2]))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append((self._row, self._links))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[1].append(data)


HEADER_KEYS = {
    "station name": "name",
    "station type": "type",
    "time/day": "time",
    "height (m)": "height",
    "gauge datum": "datum",
    "tendency": "tendency",
    "crossing (m)": "crossing",
    "flood classification": "class",
    "recent data": "links",
}
LINK_RE = re.compile(r"/fwo/(ID[A-Z]\d+)/\1\.(\w+)\.(plt|tbl)\.shtml")


def parse_bulletin(text: str) -> dict:
    """Parse a river height bulletin into {product, title, issued, groups, stations}."""
    pid = re.search(r'class="pid"[^>]*>\s*(ID[A-Z]\d+)', text)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    issued = parse_issued(clean(re.sub(r"<[^>]+>", " ", text)))
    if issued is None:
        raise ParseError("bulletin has no readable issue time")

    table = _RhbTable()
    table.feed(text)
    columns = None
    group = None
    groups: list[dict] = []
    stations: list[dict] = []
    unresolved: list[str] = []

    for cells, links in table.rows:
        if cells and all(c[0] == "th" for c in cells) and len(cells) > 1:
            columns = [HEADER_KEYS.get(c[1].lower()) for c in cells]
            continue
        if len(cells) == 1 and cells[0][0] == "th":
            group = {"name": cells[0][1], "stations": []}
            groups.append(group)
            continue
        if columns is None:
            continue
        if len(cells) != len(columns):
            raise ParseError(f"bulletin row has {len(cells)} cells, header has {len(columns)}")
        row = {key: cells[i][1] for i, key in enumerate(columns) if key}
        if not row.get("name"):
            continue
        product = station_id = None
        for href in links:
            m = LINK_RE.search(href)
            if m:
                product, station_id = m.group(1), norm_id(m.group(2))
                break
        if not station_id:
            unresolved.append(row["name"])
            continue
        if group is None:
            group = {"name": "Other", "stations": []}
            groups.append(group)
        flood_class, class_raw = norm_class(row.get("class"))
        obs = infer_obs_time(row.get("time", ""), issued)
        waterway, place = split_waterway(row["name"])
        stations.append({
            "id": station_id,
            "name": row["name"],
            "waterway": waterway,
            "place": place,
            "group": group["name"],
            "type": row.get("type") or None,
            "height": to_float(row.get("height")),
            "time": obs.isoformat() if obs else None,
            "timeRaw": row.get("time") or None,
            "datum": (row.get("datum") or "").upper() or None,
            "tendency": norm_tendency(row.get("tendency")),
            "crossing": parse_crossing(row.get("crossing")),
            "floodClass": flood_class,
            "floodClassRaw": class_raw or None,
            "product": product,
        })
        group["stations"].append(station_id)

    if columns is None or "name" not in columns or "height" not in columns:
        raise ParseError("bulletin table header not found")
    if not stations:
        raise ParseError("bulletin contained no gauge rows")
    return {
        "product": pid.group(1) if pid else None,
        "title": clean(title.group(1)) if title else None,
        "issued": issued.isoformat(),
        "groups": [g for g in groups if g["stations"]],
        "stations": stations,
        "unresolved": unresolved,
    }


# ---------------------------------------------------------------- station history table

def parse_history_table(text: str) -> dict:
    """Parse a station .tbl.shtml page: station id, flood levels, crossings and readings."""
    flat = clean(re.sub(r"<[^>]+>", " ", text))
    sid = re.search(r"Station Number:\s*(\w+)", flat)
    if not sid:
        raise ParseError("history page has no station number")
    issued = parse_issued(flat)
    tz = issued.tzinfo if issued else AEST

    levels = {}
    m = re.search(r"Flood Classification Levels:(.*?)(?:Crossing Levels:|Data from|$)", flat)
    if m:
        for name, value, datum in re.findall(r"(Minor|Moderate|Major):\s*(-?\d+(?:\.\d+)?)\s*m\b\s*(AHD|LGH)?", m.group(1)):
            levels[name.lower()] = float(value)
            if datum:
                levels["datum"] = datum
    crossings = []
    m = re.search(r"Crossing Levels:(.*?)(?:Data from|$)", flat)
    if m:
        for name, value, datum in re.findall(r"([A-Za-z][\w ()/-]*?):\s*(-?\d+(?:\.\d+)?)\s*m\b\s*(AHD|LGH)?", m.group(1)):
            crossings.append({"name": name.strip(), "level": float(value), **({"datum": datum} if datum else {})})

    readings = []
    for stamp, value in re.findall(
        r"<td[^>]*>\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})\s*</td>\s*<td[^>]*>\s*([^<]*?)\s*</td>", text
    ):
        v = to_float(value)
        if v is None:
            continue
        dt = datetime.strptime(stamp, "%d/%m/%Y %H:%M").replace(tzinfo=tz)
        readings.append((epoch_minutes(dt), v))
    return {
        "id": norm_id(sid.group(1)),
        "issued": issued.isoformat() if issued else None,
        "levels": levels,
        "crossings": crossings,
        "readings": readings,
    }


# ---------------------------------------------------------------- flood-warning map page

def _js_args(text: str, start: int) -> tuple[list[str], int] | None:
    """Read quoted, comma-separated JS string arguments starting just after '('."""
    args, i, n = [], start, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n:
            return None
        ch = text[i]
        if ch == ")":
            return args, i + 1
        if ch not in "'\"":
            # Unquoted literal (number); read to the next comma or bracket.
            j = i
            while j < n and text[j] not in ",)":
                j += 1
            args.append(text[i:j].strip())
            i = j
            continue
        j = i + 1
        buf = []
        while j < n and text[j] != ch:
            if text[j] == "\\" and j + 1 < n:
                j += 1
            buf.append(text[j])
            j += 1
        if j >= n:
            return None
        args.append("".join(buf))
        i = j + 1
    return None


def parse_map_page(text: str) -> dict[str, dict]:
    """PopupRiver(name, id, lat, lon, level, class, trend, time, minor, moderate, major, ...)."""
    page = html.unescape(text)
    found: dict[str, dict] = {}
    for m in re.finditer(r"PopupRiver\s*\(", page):
        parsed = _js_args(page, m.end())
        if not parsed:
            continue
        args = parsed[0]
        if len(args) < 11:
            continue
        name, sid, lat, lon, level, klass, trend, stamp, minor, moderate, major = args[:11]
        sid = norm_id(sid)
        lat_f, lon_f = to_float(lat), to_float(lon)
        if not sid:
            continue
        when = None
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                when = datetime.strptime(stamp.strip(), fmt).replace(tzinfo=AEST)
                break
            except ValueError:
                continue
        flood_class, class_raw = norm_class(klass)
        found[sid] = {
            "id": sid,
            "name": clean(name),
            "lat": lat_f if lat_f is not None and -90 <= lat_f <= 90 else None,
            "lon": lon_f if lon_f is not None and -180 <= lon_f <= 180 else None,
            "height": to_float(level),
            "time": when.isoformat() if when else None,
            "floodClass": flood_class,
            "floodClassRaw": class_raw or None,
            "tendency": norm_tendency(trend),
            "levels": {k: v for k, v in (("minor", to_float(minor)), ("moderate", to_float(moderate)),
                                         ("major", to_float(major))) if v is not None},
        }
    return found


# ---------------------------------------------------------------- BOM-HCS observations

DEFAULT_HCS_FIELDS = ["IndexNo", "SensorType", "SensorDataType", "SiteIdType", "SiteId",
                      "ObservationTimestamp", "RealValue", "Unit", "SensorParam1", "SensorParam2",
                      "Quality", "Comment"]


def parse_hcs(text: str, wanted: set[str] | None = None) -> list[tuple[str, int, float, str]]:
    """Water-level readings as (station_id, epoch_minute, value_m, datum)."""
    fields = DEFAULT_HCS_FIELDS
    body = []
    for line in text.splitlines():
        if line.startswith("#"):
            m = re.search(r"Data Fields:\s*(.+)$", line)
            if m:
                fields = [f.strip() for f in m.group(1).split(",")]
            continue
        if line.strip():
            body.append(line)
    idx = {f: i for i, f in enumerate(fields)}
    try:
        i_type, i_site, i_time, i_val = (idx[k] for k in ("SensorType", "SiteId", "ObservationTimestamp", "RealValue"))
    except KeyError as exc:
        raise ParseError(f"HCS file missing field {exc}") from None
    i_unit, i_datum = idx.get("Unit"), idx.get("SensorParam1")
    out = []
    for row in csv.reader(body):
        if len(row) <= max(i_type, i_site, i_time, i_val) or row[i_type].strip() != "WL":
            continue
        sid = norm_id(row[i_site])
        if wanted is not None and sid not in wanted:
            continue
        value = to_float(row[i_val])
        if value is None:
            continue
        if i_unit is not None and i_unit < len(row) and row[i_unit].strip().lower() not in ("metres", "m", ""):
            continue
        try:
            when = datetime.fromisoformat(row[i_time].strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        datum = row[i_datum].strip().upper() if i_datum is not None and i_datum < len(row) else ""
        out.append((sid, epoch_minutes(when), value, datum))
    return out
