#!/usr/bin/env python3
"""SEQ River Watch collector.

Runs every ~15 minutes (GitHub Actions). Each run:

1. Loads the previous run's history from the live site (so no database is needed).
2. Reads BoM's public feed, ftp://ftp.bom.gov.au/anon/gen/fwo/ :
   - IDQ60286.html  river height bulletin (areas, order, class, crossings) - if present
   - IDQ65389.html  flood-warning map page (coordinates, flood levels, latest level)
   - IDQ65911_*.hcs 15-minute water levels for the last ~3 hours (all QLD gauges)
   - IDQ65910_*.hcs latest water level per gauge
   - station history tables (7 days) when the feed offers them, a few per run
3. Merges everything into a rolling 7-day history and writes JSON for the website.

Only Python's standard library is used. Nothing is guessed: unreadable values stay
missing, and a run that gets no new data at all exits non-zero so the previous
site stays published.
"""

from __future__ import annotations

import argparse
import ftplib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bom_parse as bp

HERE = Path(__file__).resolve().parent
FTP_HOST = "ftp.bom.gov.au"
FTP_DIR = "/anon/gen/fwo"
BULLETIN = "IDQ60286"
DEFAULT_MAP_PAGES = ["IDQ65389"]
HCS_PREFIXES = ["IDQ65911", "IDQ65910"]
HISTORY_DAYS = 7
FULL_SCAN_EVERY = timedelta(hours=24)
TABLE_PROBE_EVERY = timedelta(hours=24)
TABLE_RETRY_AFTER = timedelta(days=7)
TABLES_PER_RUN = 20
BOM_WEB = "https://www.bom.gov.au"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# ---------------------------------------------------------------- sources

class FtpSource:
    """BoM's anonymous FTP feed. One connection per run, files fetched one at a time."""

    def __init__(self, host: str = FTP_HOST, directory: str = FTP_DIR, timeout: float = 60, pause: float = 0.3):
        self.host, self.directory, self.timeout, self.pause = host, directory, timeout, pause
        self.ftp: ftplib.FTP | None = None

    def __enter__(self):
        self.ftp = ftplib.FTP(timeout=self.timeout)
        self.ftp.connect(self.host)
        self.ftp.login()  # anonymous
        self.ftp.cwd(self.directory)
        return self

    def __exit__(self, *exc):
        try:
            if self.ftp:
                self.ftp.quit()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass

    def list(self, sub: str | None = None) -> list[str]:
        names = self.ftp.nlst(sub) if sub else self.ftp.nlst()
        return [n.rsplit("/", 1)[-1] for n in names]

    def get(self, name: str) -> str:
        buf = io.BytesIO()
        self.ftp.retrbinary(f"RETR {name}", buf.write)
        if self.pause:
            time.sleep(self.pause)
        return decode(buf.getvalue())


class DirSource:
    """A local folder laid out like the feed - for tests and offline runs."""

    def __init__(self, path: str):
        self.root = Path(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list(self, sub: str | None = None) -> list[str]:
        folder = self.root / sub if sub else self.root
        if not folder.is_dir():
            raise ftplib.error_perm(f"550 {sub}: No such directory")
        return sorted(p.name for p in folder.iterdir())

    def get(self, name: str) -> str:
        path = self.root / name
        if not path.is_file():
            raise ftplib.error_perm(f"550 {name}: No such file")
        return decode(path.read_bytes())


def latest_named(names: list[str], prefix: str) -> str | None:
    """BoM timestamps files as PREFIX_YYYYMMDDHHMMSS.ext; the newest sorts last."""
    matches = [n for n in names if re.fullmatch(rf"{prefix}_\d{{14}}\.\w+", n)]
    if matches:
        return max(matches)
    exact = [n for n in names if n.startswith(prefix + ".")]
    return exact[0] if exact else None


# ---------------------------------------------------------------- previous state

def load_previous(base: str | None) -> tuple[dict, dict]:
    """Return (history series, meta) from the last published site, or empty ones."""
    if not base:
        return {}, {}
    out = []
    for name in ("history.json", "meta.json"):
        try:
            if base.startswith(("http://", "https://")):
                url = base.rstrip("/") + "/data/" + name + f"?t={int(time.time())}"
                req = urllib.request.Request(url, headers={"User-Agent": "seq-river-watch-collector"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    out.append(json.loads(resp.read().decode("utf-8")))
            else:
                out.append(json.loads((Path(base) / name).read_text(encoding="utf-8")))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log(f"previous {name}: not available ({exc}); starting fresh")
            out.append({})
    history, meta = out
    series = history.get("series", {}) if isinstance(history, dict) else {}
    return {k: v for k, v in series.items() if isinstance(v, list)}, meta if isinstance(meta, dict) else {}


# ---------------------------------------------------------------- merging helpers

def iso_to_min(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return bp.epoch_minutes(datetime.fromisoformat(iso))
    except ValueError:
        return None


def min_to_iso(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, tz=timezone.utc).astimezone(bp.AEST).isoformat()


def classify(height: float | None, levels: dict) -> str | None:
    if height is None:
        return None
    named = [(k, levels.get(k)) for k in ("minor", "moderate", "major") if levels.get(k) is not None]
    if not named:
        return None
    reached = [k for k, v in named if height >= v]
    return reached[-1] if reached else "below"


def computed_tendency(points: list[list]) -> str | None:
    """Rising/falling/steady comparing the latest reading with the one nearest 1 hour earlier."""
    if len(points) < 2:
        return None
    last_t, last_v = points[-1]
    earlier = [p for p in points if last_t - 180 <= p[0] <= last_t - 15]
    if not earlier:
        return None
    base = min(earlier, key=lambda p: abs(p[0] - (last_t - 60)))
    change = last_v - base[1]
    if change > 0.01:
        return "rising"
    if change < -0.01:
        return "falling"
    return "steady"


def sparkline(points: list[list], now_min: int, hours: int = 24, step: int = 30) -> list:
    """Last `hours` of readings, one value per `step` minutes (last reading in each slot)."""
    start = now_min - hours * 60
    slots: dict[int, float] = {}
    for t, v in points:
        if t >= start:
            slots[(t - start) // step] = v
    return [[start + k * step, slots[k]] for k in sorted(slots)]


# ---------------------------------------------------------------- the run

def run(source, previous: str | None, out_dir: Path, now: datetime, catalogue_path: Path) -> int:
    now_min = bp.epoch_minutes(now)
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    series_prev, meta = load_previous(previous)
    meta_st: dict = meta.get("stations", {})
    status: dict = {}

    stations: dict[str, dict] = {s["id"]: dict(s) for s in catalogue["stations"]}
    groups: list[str] = list(catalogue["groups"])
    bulletin_info = {"product": catalogue.get("bulletin", BULLETIN), "title": catalogue.get("title"), "issued": None}
    reported: dict[str, dict] = {}  # BoM's own latest reading/class per station
    map_info: dict[str, dict] = {}
    table_info: dict[str, dict] = {}
    readings: list[tuple[str, int, float, str, str]] = []  # id, minute, value, datum, source
    got_new_data = False

    with source as src:
        names = src.list()
        log(f"feed listing: {len(names)} files")

        # 1. bulletin -------------------------------------------------------
        try:
            if f"{BULLETIN}.html" not in names:
                raise FileNotFoundError(f"{BULLETIN}.html not in feed listing")
            bulletin = bp.parse_bulletin(src.get(f"{BULLETIN}.html"))
            bulletin_info = {"product": bulletin["product"], "title": bulletin["title"], "issued": bulletin["issued"]}
            fresh: dict[str, dict] = {}
            for order, row in enumerate(bulletin["stations"]):
                base = stations.get(row["id"], {})
                fresh[row["id"]] = {**base, **{k: row[k] for k in ("id", "name", "waterway", "place", "group", "type", "datum", "product")},
                                    "order": order}
                reported[row["id"]] = row
            stations = fresh  # the current bulletin decides which gauges exist
            groups = [g["name"] for g in bulletin["groups"]]
            status["bulletin"] = {"ok": True, "issued": bulletin["issued"], "stations": len(fresh),
                                  "unresolved": bulletin["unresolved"]}
            got_new_data = True
        except (OSError, EOFError, ftplib.Error, bp.ParseError) as exc:
            status["bulletin"] = {"ok": False, "error": str(exc)}
            log(f"bulletin: {exc}")

        wanted = set(stations)

        # 2. flood-warning map pages -----------------------------------------
        pages = list(dict.fromkeys(DEFAULT_MAP_PAGES + sorted({s.get("product") for s in stations.values() if s.get("product")})))
        fetched_pages = []

        def read_map(page: str) -> None:
            text = src.get(f"{page}.html")
            found = bp.parse_map_page(text)
            fetched_pages.append(page)
            for sid, entry in found.items():
                if sid in wanted and sid not in map_info:
                    map_info[sid] = {**entry, "page": page}

        for page in pages:
            try:
                if f"{page}.html" not in names:
                    raise FileNotFoundError(f"{page}.html not in feed listing")
                read_map(page)
            except (OSError, EOFError, ftplib.Error) as exc:
                log(f"map page {page}: {exc}")

        def lacks_details(sid: str) -> bool:
            """True when this gauge has never been found on any flood-warning map page."""
            seen_on_map = str(meta_st.get(sid, {}).get("coordSource") or "").startswith("BoM flood warning map")
            return sid not in map_info and not seen_on_map

        last_scan = iso_to_min(meta.get("lastFullScan"))
        if any(lacks_details(s) for s in wanted) and (last_scan is None or now_min - last_scan >= FULL_SCAN_EVERY.total_seconds() / 60):
            others = sorted(n[:-5] for n in names if re.fullmatch(r"IDQ65\d{3}\.html", n) and n[:-5] not in fetched_pages)
            log(f"scanning {len(others)} other map pages for missing gauges")
            for page in others:
                try:
                    read_map(page)
                except (OSError, EOFError, ftplib.Error) as exc:
                    log(f"map page {page}: {exc}")
                if not any(lacks_details(s) for s in wanted):
                    break
            meta["lastFullScan"] = now.isoformat()
        status["mapPages"] = {"ok": bool(map_info), "pages": fetched_pages, "gauges": len(map_info)}
        if map_info:
            got_new_data = True
        for sid, entry in map_info.items():
            if entry["height"] is not None and entry["time"]:
                readings.append((sid, iso_to_min(entry["time"]), entry["height"], "", "map"))

        # 3. 15-minute observations (BOM-HCS) -------------------------------
        hcs_files = []
        hcs_count = 0
        for prefix in HCS_PREFIXES:
            name = latest_named(names, prefix)
            if not name:
                log(f"HCS {prefix}: no file in listing")
                continue
            try:
                rows = bp.parse_hcs(src.get(name), wanted)
                hcs_files.append(name)
                hcs_count += len(rows)
                readings.extend((sid, t, v, datum, "feed") for sid, t, v, datum in rows)
            except (OSError, EOFError, ftplib.Error, bp.ParseError) as exc:
                log(f"HCS {name}: {exc}")
        status["observations"] = {"ok": hcs_count > 0, "files": hcs_files, "readings": hcs_count}
        if hcs_count:
            got_new_data = True

        # 4. station history tables (bootstrap 7 days, a few per run) --------
        tables = meta.get("tables", {})
        probe_at = iso_to_min(tables.get("checked"))
        listings: dict[str, set] = {}
        if tables.get("available") or probe_at is None or now_min - probe_at >= TABLE_PROBE_EVERY.total_seconds() / 60:
            for product in sorted({s.get("product") or DEFAULT_MAP_PAGES[0] for s in stations.values()}):
                try:
                    found = {n for n in src.list(product) if n.endswith(".tbl.shtml")}
                except (OSError, EOFError, ftplib.Error) as exc:
                    log(f"history tables {product}/: not offered on this feed ({exc})")
                    continue
                if found:
                    listings[product] = found
            tables = {"available": bool(listings), "checked": now.isoformat()}
        fetched_tables = 0
        tried = meta.get("tablesTried", {})
        for sid in sorted(wanted, key=lambda s: stations[s].get("order", 0)):
            if fetched_tables >= TABLES_PER_RUN:
                break
            product = stations[sid].get("product") or DEFAULT_MAP_PAGES[0]
            fname = f"{product}.{sid}.tbl.shtml"
            last_try = iso_to_min(tried.get(sid))
            if product not in listings or fname not in listings[product]:
                continue
            if last_try is not None and now_min - last_try < TABLE_RETRY_AFTER.total_seconds() / 60:
                continue
            tried[sid] = now.isoformat()
            fetched_tables += 1
            try:
                info = bp.parse_history_table(src.get(f"{product}/{fname}"))
            except (OSError, EOFError, ftplib.Error, bp.ParseError) as exc:
                log(f"history table {fname}: {exc}")
                continue
            table_info[sid] = info
            readings.extend((sid, t, v, info["levels"].get("datum", ""), "table") for t, v in info["readings"])
        meta["tables"], meta["tablesTried"] = tables, tried
        status["historyTables"] = {"available": tables.get("available", False), "fetched": fetched_tables}
        if table_info:
            got_new_data = True

    if not got_new_data:
        log("no new data from any source - leaving the published site unchanged")
        return 2

    # ---------------------------------------------------------------- merge history
    cutoff = now_min - HISTORY_DAYS * 24 * 60
    merged: dict[str, dict[int, float]] = {}
    for sid in wanted:
        merged[sid] = {int(t): float(v) for t, v in series_prev.get(sid, []) if isinstance(t, (int, float)) and t >= cutoff}
    # Bulletin times are inferred from weekday + clock, so they go in first and exact sources override.
    for sid, row in reported.items():
        t = iso_to_min(row.get("time"))
        if row.get("height") is not None and t is not None:
            merged[sid].setdefault(t, row["height"])
    skipped_datum = 0
    rank = {"map": 1, "table": 2, "feed": 3}
    best: dict[tuple[str, int], int] = {}
    for sid, t, v, datum, source_name in readings:
        if t is None or t < cutoff or t > now_min + 30:
            continue
        station_datum = (stations[sid].get("datum") or "").upper()
        if datum and station_datum and datum.upper() != station_datum:
            skipped_datum += 1
            continue
        key = (sid, t)
        if rank[source_name] >= best.get(key, 0):
            merged[sid][t] = v
            best[key] = rank[source_name]
    if skipped_datum:
        log(f"skipped {skipped_datum} readings whose datum differs from the gauge's bulletin datum")

    # ---------------------------------------------------------------- latest state per gauge
    out_stations = []
    history_out = {}
    for sid, st in sorted(stations.items(), key=lambda kv: kv[1].get("order", 0)):
        points = [[t, merged[sid][t]] for t in sorted(merged[sid])]
        history_out[sid] = points
        cached = meta_st.get(sid, {})
        mp = map_info.get(sid, {})
        tb = table_info.get(sid, {})

        # coordinates: live map page > cached from an earlier map page > bundled catalogue
        if mp.get("lat") is not None:
            lat, lon, coord_src = mp["lat"], mp["lon"], f"BoM flood warning map {mp['page']}"
        elif cached.get("lat") is not None:
            lat, lon, coord_src = cached["lat"], cached["lon"], cached.get("coordSource")
        else:
            lat, lon, coord_src = st.get("lat"), st.get("lon"), st.get("coordSource")

        # flood classification levels: map page > station table > cache. Never borrowed from another gauge.
        levels, levels_src = {}, None
        if mp.get("levels"):
            levels, levels_src = dict(mp["levels"]), f"BoM flood warning map {mp['page']}"
        elif tb.get("levels"):
            tl = {k: v for k, v in tb["levels"].items() if k in ("minor", "moderate", "major")}
            td = tb["levels"].get("datum")
            if tl and (not td or not st.get("datum") or td == st.get("datum")):
                levels, levels_src = tl, "BoM station history table"
        elif cached.get("levels"):
            levels, levels_src = cached["levels"], cached.get("levelsSource")
        crossings = tb.get("crossings") or cached.get("crossings") or []

        from_map = str(coord_src or "").startswith("BoM flood warning map")
        meta_st[sid] = {k: v for k, v in {
            "lat": lat if from_map else cached.get("lat"),
            "lon": lon if from_map else cached.get("lon"),
            "coordSource": coord_src if from_map else cached.get("coordSource"),
            "levels": levels or None, "levelsSource": levels_src, "crossings": crossings or None,
        }.items() if v is not None}

        # latest reading: whichever source is newest
        rep = reported.get(sid) or {}
        rep_src = "bulletin"
        if not rep and mp:
            rep, rep_src = mp, "map"
        rep_t = iso_to_min(rep.get("time"))
        last_t, last_v = (points[-1] if points else (None, None))
        if rep.get("height") is not None and rep_t is not None and (last_t is None or rep_t >= last_t - 1):
            height, obs_t, obs_src = rep["height"], rep_t, rep_src
        elif last_t is not None:
            height, obs_t, obs_src = last_v, last_t, "feed"
        else:
            height, obs_t, obs_src = None, None, None

        bom_class, bom_class_t = rep.get("floodClass"), rep_t
        if bom_class is None and mp.get("floodClass"):
            bom_class, bom_class_t = mp["floodClass"], iso_to_min(mp.get("time"))
        if obs_src in ("bulletin", "map") and bom_class:
            flood_class, class_src = bom_class, "bom"
        elif classify(height, levels):
            flood_class, class_src = classify(height, levels), "computed"
        else:
            flood_class, class_src = bom_class, "bom" if bom_class else None

        if obs_src in ("bulletin", "map") and rep.get("tendency"):
            tendency, tend_src = rep["tendency"], "bom"
        else:
            tendency, tend_src = computed_tendency(points), "computed"

        product = st.get("product") or DEFAULT_MAP_PAGES[0]
        out_stations.append({
            "id": sid, "name": st.get("name"), "waterway": st.get("waterway"), "place": st.get("place"),
            "group": st.get("group"), "order": st.get("order"), "type": st.get("type"), "datum": st.get("datum"),
            "lat": lat, "lon": lon, "coordSource": coord_src,
            "height": height, "time": min_to_iso(obs_t) if obs_t is not None else None, "timeSource": obs_src,
            "floodClass": flood_class, "floodClassRaw": rep.get("floodClassRaw") or mp.get("floodClassRaw"),
            "classSource": class_src,
            "classTime": min_to_iso(bom_class_t) if class_src == "bom" and bom_class_t is not None else None,
            "tendency": tendency, "tendencySource": tend_src if tendency else None,
            "crossing": rep.get("crossing") if rep_src == "bulletin" else None,
            "crossings": crossings,
            "levels": levels, "levelsSource": levels_src,
            "historyPoints": len(points),
            "spark": sparkline(points, now_min),
            "links": {
                "plot": f"{BOM_WEB}/fwo/{product}/{product}.{sid}.plt.shtml",
                "table": f"{BOM_WEB}/fwo/{product}/{product}.{sid}.tbl.shtml",
            },
        })

    group_list = [{"name": g, "stations": [s["id"] for s in out_stations if s["group"] == g]} for g in groups]
    group_list = [g for g in group_list if g["stations"]]
    meta["stations"] = meta_st

    out_dir.mkdir(parents=True, exist_ok=True)
    latest = {
        "generated": now.astimezone(bp.AEST).isoformat(),
        "demo": False,
        "bulletin": bulletin_info,
        "sources": status,
        "historyDays": HISTORY_DAYS,
        "groups": group_list,
        "stations": out_stations,
        "attribution": "Source: Bureau of Meteorology (CC BY 4.0 AU). Most river height data is supplied to the "
                       "Bureau by other agencies and is subject to their terms. Real-time data, not quality controlled.",
    }
    write_json(out_dir / "latest.json", latest)
    write_json(out_dir / "history.json", {"generated": latest["generated"], "days": HISTORY_DAYS, "series": history_out})
    write_json(out_dir / "meta.json", meta)
    counts: dict = {}
    for s in out_stations:
        counts[s["floodClass"] or "unknown"] = counts.get(s["floodClass"] or "unknown", 0) + 1
    log(f"wrote {len(out_stations)} gauges {counts}; sources: {json.dumps(status)}")
    return 0


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="site/data", help="folder to write latest.json / history.json / meta.json")
    ap.add_argument("--previous", default=None, help="URL of the published site (or a local data folder) to continue history from")
    ap.add_argument("--feed-dir", default=None, help="read feed files from this local folder instead of BoM's FTP (testing)")
    ap.add_argument("--now", default=None, help="override the current time (ISO 8601, testing)")
    ap.add_argument("--catalogue", default=str(HERE / "stations.json"))
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    source = DirSource(args.feed_dir) if args.feed_dir else FtpSource()
    try:
        return run(source, args.previous, Path(args.out), now, Path(args.catalogue))
    except (OSError, EOFError, ftplib.Error) as exc:
        log(f"could not reach the BoM feed: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
