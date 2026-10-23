"""Tests for the SEQ River Watch collector.  Run:  python -m unittest discover -s tests

fixtures/IDQ60286_2026-10-22.html and fixtures/IDQ65389.540111.tbl.shtml are real BoM
pages (CC BY 4.0 AU). The flood-map page and HCS files below are synthetic, built in the
documented formats, and exist only to exercise the code paths.
"""

import json
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collector"))
import bom_parse as bp  # noqa: E402
import collect  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
BULLETIN = (FIX / "IDQ60286_2026-10-22.html").read_text(encoding="utf-8")
HISTORY = (FIX / "IDQ65389.540111.tbl.shtml").read_text(encoding="utf-8")
AEST = timezone(timedelta(hours=10))


def hcs(rows):
    head = ("# HEADER: File Format: BOM-HCS\n"
            "# HEADER: Data Fields: IndexNo, SensorType, SensorDataType, SiteIdType, SiteId, ObservationTimestamp, "
            "RealValue, Unit, SensorParam1, SensorParam2, Quality, Comment\n")
    body = "".join(
        f'{i},"{kind}",1,"SR","{sid}","{t}",{"" if v is None else v},"{unit}","{datum}",,1,""\n'
        for i, (kind, sid, t, v, unit, datum) in enumerate(rows, 1))
    return head + body


def tiny_png():
    """A 1x1 indexed PNG: palette entry 0 transparent, 1 and 2 opaque (like BoM radar frames)."""
    import struct
    import zlib

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", bytes([0, 0, 0, 245, 245, 255, 180, 180, 255])) + chunk(b"tRNS", bytes([0, 255, 255]))
            + chunk(b"IDAT", zlib.compress(b"\x00\x01")) + chunk(b"IEND", b""))


def map_page(entries):
    areas = []
    for name, sid, lat, lon, level, klass, trend, stamp, mi, mo, ma in entries:
        areas.append(
            '<area shape="circle" onmouseover="javascript:PopupRiver(&quot;' + name.replace('"', "&quot;") + '&quot;,'
            f"'{sid}','{lat}','{lon}','{level}','{klass}','{trend}','{stamp}','{mi}','{mo}','{ma}','Click mouse to display plot')\" />")
    return "<html><body><map>" + "\n".join(areas) + "</map></body></html>"


class ParserTests(unittest.TestCase):
    def test_real_bulletin(self):
        b = bp.parse_bulletin(BULLETIN)
        self.assertEqual(b["product"], "IDQ60286")
        self.assertEqual(b["issued"], "2026-10-22T10:24:00+10:00")
        self.assertEqual([g["name"] for g in b["groups"]],
                         ["Ipswich/Brisbane Creeks", "Pine/Caboolture", "Stanley/Upper Brisbane", "Lower Brisbane"])
        self.assertEqual(len(b["stations"]), 233)
        self.assertEqual(len({s["id"] for s in b["stations"]}), 233)
        first = b["stations"][0]
        self.assertEqual((first["id"], first["height"], first["datum"], first["floodClass"]), ("540111", 31.66, "AHD", "below"))
        self.assertEqual(first["time"], "2026-10-22T10:14:00+10:00")
        burp = next(s for s in b["stations"] if s["id"] == "540245")
        self.assertEqual(burp["floodClass"], "moderate")
        # blank height stays missing, never zero
        self.assertTrue(any(s["height"] is None for s in b["stations"]))
        self.assertTrue(all(s["height"] != 0 or s["timeRaw"] for s in b["stations"]))

    def test_obs_time_rolls_back_over_midnight(self):
        issued = datetime(2026, 10, 22, 0, 10, tzinfo=AEST)  # Thursday 00:10
        self.assertEqual(bp.infer_obs_time("11.50PM Wed", issued), datetime(2026, 10, 21, 23, 50, tzinfo=AEST))
        self.assertEqual(bp.infer_obs_time("12.05AM Thu", issued), datetime(2026, 10, 22, 0, 5, tzinfo=AEST))
        self.assertEqual(bp.infer_obs_time("12.00PM Thu", issued), datetime(2026, 10, 15, 12, 0, tzinfo=AEST))
        self.assertIsNone(bp.infer_obs_time("", issued))

    def test_crossing_and_class_wording(self):
        self.assertEqual(bp.parse_crossing("3.74 below Bridge"),
                         {"text": "3.74 below Bridge", "distance": 3.74, "relation": "below", "feature": "Bridge"})
        self.assertEqual(bp.norm_class("Below Flood Level")[0], "below")
        self.assertEqual(bp.norm_class("Major")[0], "major")
        self.assertIsNone(bp.norm_class("No Classification")[0])  # unknown is not "safe"

    def test_real_history_table(self):
        h = bp.parse_history_table(HISTORY)
        self.assertEqual(h["id"], "540111")
        self.assertEqual(h["levels"], {"minor": 33.5, "moderate": 35.0, "major": 36.0, "datum": "AHD"})
        self.assertGreater(len(h["readings"]), 200)
        last = h["readings"][-1]
        self.assertEqual(last[1], 31.66)

    def test_map_page_tokens(self):
        page = map_page([
            ("Kedron Brook at Mitchelton (Osborne Rd)", "540111", "-27.40", "152.97", "31.66", "Below Flood Level",
             "steady", "22-10-2026 10:14:00", "33.50", "35.00", "36.00"),
            ("O'Reilly's Weir (test)", "40387", "-27.1", "152.5", "", "No Classification", "", "", "", "", ""),
        ])
        found = bp.parse_map_page(page)
        self.assertEqual(found["540111"]["levels"], {"minor": 33.5, "moderate": 35.0, "major": 36.0})
        self.assertEqual(found["540111"]["time"], "2026-10-22T10:14:00+10:00")
        self.assertEqual(found["040387"]["name"], "O'Reilly's Weir (test)")
        self.assertEqual(found["040387"]["levels"], {})
        self.assertIsNone(found["040387"]["height"])

    def test_hcs(self):
        text = hcs([("WL", "540111", "2026-10-22T00:30:00Z", 31.7, "metres", "AHD"),
                    ("RN", "540111", "2026-10-22T00:30:00Z", 1.0, "mm", ""),
                    ("WL", "540478", "2026-10-22T00:30:00Z", None, "metres", "AHD"),
                    ("WL", "999999", "2026-10-22T00:30:00Z", 2.0, "metres", "LGH")])
        rows = bp.parse_hcs(text, {"540111", "540478"})
        self.assertEqual(rows, [("540111", bp.epoch_minutes(datetime(2026, 10, 22, 0, 30, tzinfo=timezone.utc)), 31.7, "AHD")])


class CollectorRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.feed = self.tmp / "feed"
        (self.feed / "IDQ65389").mkdir(parents=True)
        (self.feed / "IDQ60286.html").write_text(BULLETIN, encoding="utf-8")
        (self.feed / "IDQ65389" / "IDQ65389.540111.tbl.shtml").write_text(HISTORY, encoding="utf-8")
        cat = json.loads((ROOT / "collector" / "stations.json").read_text(encoding="utf-8"))
        entries = []
        for s in cat["stations"][:50]:
            lat, lon = s.get("lat", -27.5), s.get("lon", 153.0)
            entries.append((s["name"], s["id"], lat, lon, "", "Below Flood Level", "steady", "22-10-2026 10:00:00", "", "", ""))
        # synthetic flood levels for one gauge so classification can be computed
        entries.append(("Kedron Brook at Gordon Park (McCord St)", "540478", "-27.41", "153.03", "9.31",
                        "Below Flood Level", "rising", "22-10-2026 10:10:00", "10.00", "11.00", "12.00"))
        (self.feed / "IDQ65325.html").write_text(map_page(entries), encoding="utf-8")
        (self.feed / "IDQ65911_20261022003500.hcs").write_text(hcs([
            ("WL", "540478", f"2026-10-22T00:{m:02d}:00Z", 9.31 + m / 100, "metres", "AHD") for m in (15, 30)
        ] + [
            ("WL", "540478", "2026-10-22T00:35:00Z", 10.20, "metres", "AHD"),   # newer than the bulletin
            ("WL", "540111", "2026-10-22T00:20:00Z", 5.00, "metres", "LGH"),    # wrong datum -> skipped
        ]), encoding="utf-8")
        self.out = self.tmp / "site" / "data"
        self.now = "2026-10-22T00:40:00+00:00"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_collector(self, *extra):
        return collect.main(["--feed-dir", str(self.feed), "--out", str(self.out), "--now", self.now, *extra])

    def load(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_full_run(self):
        self.assertEqual(self.run_collector(), 0)
        latest = self.load("latest.json")
        self.assertEqual(latest["bulletin"]["issued"], "2026-10-22T10:24:00+10:00")
        self.assertEqual(len(latest["stations"]), 233)
        self.assertTrue(latest["sources"]["bulletin"]["ok"])
        self.assertEqual(latest["sources"]["observations"]["readings"], 4)
        st = {s["id"]: s for s in latest["stations"]}
        gp = st["540478"]
        self.assertEqual((gp["height"], gp["timeSource"]), (10.2, "feed"))
        self.assertEqual((gp["floodClass"], gp["classSource"]), ("minor", "computed"))
        self.assertEqual(gp["tendency"], "rising")
        self.assertEqual(gp["levels"], {"minor": 10.0, "moderate": 11.0, "major": 12.0})
        mitch = st["540111"]
        self.assertEqual(mitch["levels"], {"minor": 33.5, "moderate": 35.0, "major": 36.0})  # from history table
        self.assertGreater(mitch["historyPoints"], 200)
        self.assertNotIn(5.0, [v for _, v in self.load("history.json")["series"]["540111"]])  # datum guard
        self.assertEqual(st["540245"]["floodClass"], "moderate")  # BoM's own class kept
        self.assertEqual(st["540245"]["classSource"], "bom")
        self.assertTrue(all(s["lat"] is not None for s in latest["stations"][:50]))

    def test_history_continues_from_previous_run(self):
        self.assertEqual(self.run_collector(), 0)
        prev = self.tmp / "prev"
        shutil.copytree(self.out, prev)
        for f in self.feed.glob("*.hcs"):
            f.unlink()
        (self.feed / "IDQ65911_20261022011500.hcs").write_text(hcs([
            ("WL", "540478", "2026-10-22T01:15:00Z", 10.6, "metres", "AHD")]), encoding="utf-8")
        self.now = "2026-10-22T01:20:00+00:00"
        self.assertEqual(self.run_collector("--previous", str(prev)), 0)
        series = dict((t, v) for t, v in self.load("history.json")["series"]["540478"])
        self.assertIn(10.2, series.values())   # carried forward
        self.assertIn(10.6, series.values())   # new
        self.assertEqual({s["id"]: s for s in self.load("latest.json")["stations"]}["540478"]["height"], 10.6)

    def test_old_history_is_trimmed(self):
        prev = self.tmp / "prev"
        prev.mkdir()
        old = bp.epoch_minutes(datetime(2026, 10, 1, tzinfo=timezone.utc))
        (prev / "history.json").write_text(json.dumps({"series": {"540478": [[old, 1.0]]}}))
        self.assertEqual(self.run_collector("--previous", str(prev)), 0)
        self.assertNotIn(1.0, [v for _, v in self.load("history.json")["series"]["540478"]])

    def test_empty_feed_fails_without_writing(self):
        for f in list(self.feed.iterdir()):
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        self.assertEqual(self.run_collector(), 2)
        self.assertFalse((self.out / "latest.json").exists())

    def test_works_without_bulletin(self):
        (self.feed / "IDQ60286.html").unlink()
        self.assertEqual(self.run_collector(), 0)
        latest = self.load("latest.json")
        self.assertFalse(latest["sources"]["bulletin"]["ok"])
        self.assertEqual(len(latest["stations"]), 233)  # bundled catalogue keeps the station list
        self.assertEqual({s["id"]: s for s in latest["stations"]}["540478"]["height"], 10.2)

    def test_council_positions_win(self):
        council = FIX / "council_locations.json"
        self.assertEqual(self.run_collector("--council-file", str(council)), 0)
        st = {s["id"]: s for s in self.load("latest.json")["stations"]}
        kg = st["540118"]  # Enoggera Creek at Kelvin Grove (Bancroft Park)
        self.assertEqual((kg["lat"], kg["lon"]), (-27.444481, 153.005314))
        self.assertEqual((kg["coordSource"], kg["coordAccuracy"]), ("Brisbane City Council gauge register", "surveyed"))
        self.assertEqual(st["540245"]["coordSource"], "City of Moreton Bay gauge register")
        self.assertEqual(st["540156"]["coordAccuracy"], "bom")  # not in either register
        self.assertEqual(self.load("latest.json")["sources"]["locations"]["surveyed"], 90)

    def test_minute_rounded_positions(self):
        self.assertTrue(collect.minute_rounded(-27.516667, 152.933333))   # 27°31' 152°56'
        self.assertTrue(collect.minute_rounded(-27.5167, 152.9333))
        self.assertFalse(collect.minute_rounded(-27.4369, 153.0044))
        self.assertFalse(collect.minute_rounded(-27.444481, 153.005314))

    def test_fetch_council_locations(self):
        mb = {"features": [
            {"attributes": {"BOM_STATION_NUMBER": 540111, "SENSOR_TYPE": "RIVER"}, "geometry": {"x": 152.978437, "y": -27.402933}},
            {"attributes": {"BOM_STATION_NUMBER": 540119, "SENSOR_TYPE": "RAINFALL"}, "geometry": {"x": 152.9264, "y": -27.4469}},
            {"attributes": {"BOM_STATION_NUMBER": 540245, "SENSOR_TYPE": "RIVER"}, "geometry": {"x": 152.937252, "y": -27.164452}},
            {"attributes": {"BOM_STATION_NUMBER": 999999, "SENSOR_TYPE": "RIVER"}, "geometry": {"x": 0, "y": 0}},
        ]}
        bcc_pages = [
            {"total_count": 3, "results": [
                {"location_id": "540111", "sensor_type": "Stream Height AHD", "latitude": -27.403391, "longitude": 152.978646},
                {"location_id": "40788", "sensor_type": "Stream Height AHD", "latitude": -27.655308, "longitude": 153.000406}]},
            {"total_count": 3, "results": [
                {"location_id": "540099", "sensor_type": "Rainfall", "latitude": -27.5, "longitude": 152.9}]},
        ]
        def get(url):
            if "arcgis" in url:
                return mb
            return bcc_pages[0] if "offset=0" in url else bcc_pages[1]
        points, errors = collect.fetch_council_locations(get)
        self.assertEqual(errors, [])
        self.assertEqual(set(points), {"540111", "540245", "040788"})
        self.assertEqual(points["540111"]["source"], "Brisbane City Council gauge register")  # Brisbane wins overlaps
        self.assertEqual(points["540245"]["lat"], -27.164452)

    def test_council_failure_keeps_run_going(self):
        def broken():
            raise OSError("network down")
        code = collect.run(collect.DirSource(str(self.feed)), None, self.out, datetime.fromisoformat(self.now),
                           ROOT / "collector" / "stations.json", council_fetcher=broken)
        self.assertEqual(code, 0)
        self.assertEqual({s["id"]: s for s in self.load("latest.json")["stations"]}["540118"]["coordAccuracy"], "bom")

    def test_radar_frames_published(self):
        radar = self.feed / "radar"
        coords = self.feed / "radar_coords"
        radar.mkdir()
        coords.mkdir()
        png = tiny_png()
        for stamp in ("202610220025", "202610220030", "202610220035"):
            (radar / f"IDR662.T.{stamp}.png").write_bytes(png)
        (radar / "IDR662.gif").write_bytes(b"GIF89a")
        (coords / "IDR662.map").write_text("North: -25.4 South: -30.0\nEast: 155.8 West: 150.6\n")
        self.assertEqual(self.run_collector(), 0)
        manifest = json.loads((self.out.parent / "data" / "radar.json").read_text())
        r = {x["id"]: x for x in manifest["radars"]}["IDR662"]
        self.assertEqual(r["bounds"], [[-30.0, 150.6], [-25.4, 155.8]])
        self.assertEqual([f["url"] for f in r["frames"]][-1], "radar/IDR662.T.202610220035.png")
        self.assertEqual(r["frames"][-1]["time"], "2026-10-22T10:35:00+10:00")
        self.assertEqual(r["palette"], ["#f5f5ff", "#b4b4ff"])
        self.assertTrue((self.out.parent / "radar" / "IDR662.T.202610220035.png").is_file())
        self.assertTrue(self.load("latest.json")["sources"]["radar"]["ok"])

    def test_radar_map_formats(self):
        self.assertEqual(collect.parse_radar_map("Top lat: -25.42 Bottom lat: -30.03 Left lon: 150.67 Right lon: 155.81"),
                         [[-30.03, 150.67], [-25.42, 155.81]])
        self.assertEqual(collect.parse_radar_map("IDR662\n150.6700 -25.4200\n155.8100 -30.0300\n"),
                         [[-30.03, 150.67], [-25.42, 155.81]])
        self.assertIsNone(collect.parse_radar_map("no coordinates here"))

    def test_over_real_ftp(self):
        try:
            from pyftpdlib.authorizers import DummyAuthorizer
            from pyftpdlib.handlers import FTPHandler
            from pyftpdlib.servers import FTPServer
        except ImportError:
            self.skipTest("pyftpdlib not installed")
        root = self.tmp / "ftproot"
        (root / "anon" / "gen").mkdir(parents=True)
        shutil.copytree(self.feed, root / "anon" / "gen" / "fwo")
        auth = DummyAuthorizer()
        auth.add_anonymous(str(root))
        handler = type("H", (FTPHandler,), {"authorizer": auth})
        server = FTPServer(("127.0.0.1", 0), handler)
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.2}, daemon=True)
        thread.start()
        try:
            src = collect.FtpSource(host="127.0.0.1", pause=0)
            orig = collect.ftplib.FTP.connect
            collect.ftplib.FTP.connect = lambda self, host="", *a, **k: orig(self, host, port)
            try:
                code = collect.run(src, None, self.out, datetime.fromisoformat(self.now), ROOT / "collector" / "stations.json", council_fetcher=None)
            finally:
                collect.ftplib.FTP.connect = orig
            self.assertEqual(code, 0)
            latest = self.load("latest.json")
            self.assertEqual(latest["sources"]["historyTables"]["fetched"], 1)
            self.assertEqual(len(latest["stations"]), 233)
        finally:
            server.close_all()


if __name__ == "__main__":
    unittest.main()
