#!/usr/bin/env python3
"""Download and simplify catchment boundaries for the map's Catchments overlay.

Writes site/geo/catchments.json with two layers:

* basins: the Queensland Government's drainage sub-basins for the rivers in the
  IDQ60286 bulletin (Stanley, Lockyer, Bremer, Brisbane, North Pine, South Pine,
  Caboolture), each with where it drains to.
* creeks: Brisbane City Council's waterway catchments (58 creek catchments).

Run by the "Refresh catchment boundaries" workflow (monthly or on demand), or by hand:
    python tools/fetch_catchments.py site/geo/catchments.json
Standard library only.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

QLD_SUB_BASINS = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/InlandWaters/DrainageBasins/MapServer/2/query?"
    + urllib.parse.urlencode({
        "where": "sub_number IN ('1420','1421','1422','1430','1431','1432','1433')",
        "outFields": "sub_name,sub_number", "outSR": "4326", "f": "geojson",
    })
)
BCC_CATCHMENTS = ("https://data.brisbane.qld.gov.au/api/explore/v2.1/catalog/datasets/"
                  "waterway-catchment-catchment-areas/records?limit=100&offset={offset}&select=objectid,name,geo_shape")

# Where each sub-basin's water goes, and the first gauge downstream of the junction on the
# receiving river (matched by name in the bulletin). Kept to well-known junctions only.
DRAINS_TO = {
    "1433": {"into": "Brisbane River", "via": "through Somerset Dam into Lake Wivenhoe", "receivingRiver": "Brisbane River", "firstGauge": "Wivenhoe Dam HG"},
    "1432": {"into": "Brisbane River", "via": "below Wivenhoe Dam, upstream of Lowood", "receivingRiver": "Brisbane River", "firstGauge": "Lowood"},
    "1431": {"into": "Brisbane River", "via": "near Moggill", "receivingRiver": "Brisbane River", "firstGauge": "Moggill"},
    "1430": {"into": "Moreton Bay", "via": "at the river mouth near Brisbane Bar"},
    "1421": {"into": "Bramble Bay", "via": "through the Pine River, after meeting the South Pine River near Lawnton"},
    "1422": {"into": "Bramble Bay", "via": "through the Pine River, after meeting the North Pine River near Lawnton"},
    "1420": {"into": "Deception Bay", "via": "at the Caboolture River mouth"},
}
MAIN_RIVER = {"1433": "Stanley River", "1432": "Lockyer Creek", "1431": "Bremer River", "1430": "Brisbane River",
              "1421": "North Pine River", "1422": "South Pine River", "1420": "Caboolture River"}


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "seq-river-watch (github.com)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def douglas_peucker(points: list, tol: float) -> list:
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a][:2], points[b][:2]
        dx, dy = bx - ax, by - ay
        length = (dx * dx + dy * dy) ** 0.5
        best, idx = tol, -1
        for i in range(a + 1, b):
            px, py = points[i][:2]
            if length < 1e-12:  # closed ring: measure from the shared endpoint
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / length
            if d > best:
                best, idx = d, i
        if idx > 0:
            keep[idx] = True
            stack.extend(((a, idx), (idx, b)))
    return [p for p, k in zip(points, keep) if k]


def ring_area(ring: list) -> float:
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                   for i in range(len(ring)))) / 2


def simplify(geom: dict, tol: float, min_area: float) -> dict | None:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"] if geom["type"] == "MultiPolygon" else []
    out = []
    for poly in polys:
        rings = []
        for ring in poly:
            s = [[round(p[0], 4), round(p[1], 4)] for p in douglas_peucker(ring, tol)]
            if len(s) >= 4:
                rings.append(s)
        if rings and ring_area(rings[0]) >= min_area:
            out.append(rings)
    if not out:
        return None
    return {"type": "Polygon", "coordinates": out[0]} if len(out) == 1 else {"type": "MultiPolygon", "coordinates": out}


def build() -> dict:
    basins = []
    for f in get_json(QLD_SUB_BASINS).get("features", []):
        code = str(f["properties"]["sub_number"])
        geom = simplify(f["geometry"], 0.0012, 0.00002)
        if geom:
            basins.append({"type": "Feature", "geometry": geom, "properties": {
                "name": f["properties"]["sub_name"], "code": code, "level": "basin",
                "mainRiver": MAIN_RIVER.get(code), **DRAINS_TO.get(code, {})}})
    creeks, offset = [], 0
    while offset < 1000:
        page = get_json(BCC_CATCHMENTS.format(offset=offset))
        rows = page.get("results", [])
        for r in rows:
            shape = (r.get("geo_shape") or {}).get("geometry")
            geom = simplify(shape, 0.0004, 0.000002) if shape else None
            if geom:
                creeks.append({"type": "Feature", "geometry": geom, "properties": {
                    "name": " ".join(str(r.get("name", "")).split()), "code": r.get("objectid"), "level": "creek"}})
        offset += len(rows)
        if not rows or offset >= page.get("total_count", 0):
            break
    if len(basins) < 7 or len(creeks) < 40:
        raise SystemExit(f"unexpected result: {len(basins)} basins, {len(creeks)} creek catchments - not writing")
    return {
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": "Drainage sub-basins © State of Queensland (Department of Resources); "
                       "creek catchments © Brisbane City Council. Both CC BY 4.0.",
        "basins": {"type": "FeatureCollection", "features": basins},
        "creeks": {"type": "FeatureCollection", "features": creeks},
    }


def main(path: str) -> None:
    data = build()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"wrote {path}: {len(data['basins']['features'])} basins, {len(data['creeks']['features'])} creek catchments")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "site/geo/catchments.json")
