"""One-off helper: build collector/stations.json from a saved bulletin + a coordinate list.

The collector refreshes station details from BoM's feed on every run, so this file
is only the starting point / fallback. It records the bulletin's areas and station
order (BoM lists gauges roughly upstream to downstream within each waterway) and
fallback coordinates from BoM's Queensland flood-warning river station catalogue.

Usage:
    python tools/build_catalogue.py <saved IDQ60286.html> <coords.json> > collector/stations.json

coords.json maps station number -> {"lat": .., "lon": .., "source": .., "catalogueDate": ..}.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import bom_parse  # noqa: E402


def main(bulletin_path: str, coords_path: str) -> None:
    bulletin = bom_parse.parse_bulletin(Path(bulletin_path).read_text(encoding="utf-8", errors="replace"))
    coords = json.loads(Path(coords_path).read_text(encoding="utf-8"))
    stations = []
    for order, s in enumerate(bulletin["stations"]):
        c = coords.get(s["id"]) or {}
        entry = {
            "id": s["id"], "name": s["name"], "waterway": s["waterway"], "place": s["place"],
            "group": s["group"], "order": order, "type": s["type"], "datum": s["datum"],
            "product": s["product"],
        }
        if c.get("lat") is not None and c.get("lon") is not None:
            entry.update(lat=round(c["lat"], 6), lon=round(c["lon"], 6),
                         coordSource=f"BoM QLD river height station catalogue ({c.get('catalogueDate', 'n.d.')})")
        stations.append(entry)
    out = {
        "bulletin": bulletin["product"],
        "title": bulletin["title"],
        "builtFrom": bulletin["issued"],
        "groups": [g["name"] for g in bulletin["groups"]],
        "stations": stations,
    }
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    missing = [s["name"] for s in stations if "lat" not in s]
    print(f"{len(stations)} stations, {len(missing)} without fallback coordinates: {missing}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:3])
