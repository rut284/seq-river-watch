"""One-off helper: build collector/stations.json from saved bulletins + a coordinate list.

The collector refreshes station details from BoM's feed on every run, so this file is only
the starting point / fallback when a bulletin can't be read. It records each bulletin's areas
and station order (BoM lists gauges roughly upstream to downstream within each waterway) and
fallback coordinates from BoM's Queensland flood-warning river station catalogue.

Usage:
    python tools/build_catalogue.py <coords.json> <saved IDQ60286.html> [<saved IDQ60285.html> ...] > collector/stations.json

coords.json maps station number -> {"lat": .., "lon": .., "catalogueDate": ..}.
The bulletins must be given in the same order as BULLETINS in collector/collect.py.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import bom_parse  # noqa: E402
from collect import ORDER_BLOCK  # noqa: E402


def main(coords_path: str, *bulletin_paths: str) -> None:
    coords = json.loads(Path(coords_path).read_text(encoding="utf-8"))
    bulletins, stations, seen = [], [], set()
    for position, path in enumerate(bulletin_paths):
        b = bom_parse.parse_bulletin(Path(path).read_text(encoding="utf-8", errors="replace"))
        bulletins.append({"product": b["product"], "title": b["title"], "builtFrom": b["issued"],
                          "groups": [g["name"] for g in b["groups"]]})
        for row, s in enumerate(b["stations"]):
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            c = coords.get(s["id"]) or {}
            entry = {
                "id": s["id"], "name": s["name"], "waterway": s["waterway"], "place": s["place"],
                "group": s["group"], "bulletin": b["product"], "order": position * ORDER_BLOCK + row,
                "type": s["type"], "datum": s["datum"], "product": s["product"],
            }
            if c.get("lat") is not None and c.get("lon") is not None:
                entry.update(lat=round(c["lat"], 6), lon=round(c["lon"], 6),
                             coordSource=f"BoM QLD river height station catalogue ({c.get('catalogueDate', 'n.d.')})")
            stations.append(entry)
    json.dump({"bulletins": bulletins, "stations": stations}, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    missing = [s["name"] for s in stations if "lat" not in s]
    print(f"{len(stations)} stations, {len(missing)} without fallback coordinates: {missing}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
