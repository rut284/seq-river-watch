"""Tests for the catchment boundary simplifier (tools/fetch_catchments.py)."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fetch_catchments as fc  # noqa: E402


def circle(cx, cy, r, n=400):
    pts = [[cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)] for i in range(n)]
    return pts + [pts[0]]  # closed ring, as GeoJSON requires


class SimplifyTests(unittest.TestCase):
    def test_closed_ring_survives_and_shrinks(self):
        ring = circle(152.9, -27.4, 0.05)
        geom = fc.simplify({"type": "Polygon", "coordinates": [ring]}, 0.0012, 0.00002)
        self.assertEqual(geom["type"], "Polygon")
        out = geom["coordinates"][0]
        self.assertLess(len(out), len(ring) / 4)
        self.assertGreaterEqual(len(out), 8)
        self.assertEqual(out[0], out[-1])
        self.assertAlmostEqual(fc.ring_area(out), math.pi * 0.05 ** 2, delta=0.0003)

    def test_tiny_islands_dropped(self):
        big, tiny = circle(152.9, -27.4, 0.05), circle(153.1, -27.4, 0.0005, 20)
        geom = fc.simplify({"type": "MultiPolygon", "coordinates": [[big], [tiny]]}, 0.0004, 0.000002)
        self.assertEqual(geom["type"], "Polygon")

    def test_every_basin_has_a_drain(self):
        self.assertEqual(set(fc.DRAINS_TO), set(fc.MAIN_RIVER))


if __name__ == "__main__":
    unittest.main()
