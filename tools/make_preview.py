"""Build a single-file preview of the site with a real BoM snapshot embedded.

The snapshot is the IDQ60286 bulletin issued 10:24 am AEST Thursday 22 October 2026
plus one station's 7-day table (tests/fixtures). The page is marked as sample data.

Usage:
    python tools/make_preview.py <output.html> [--artifact] [--data-dir DIR]

--artifact   strip the <!doctype>/<html>/<head>/<body> skeleton (for claude.ai artifacts)
--data-dir   also write latest.json / history.json there (to test the live-site loader)
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collector"))
import collect  # noqa: E402


def snapshot(demo: bool = True) -> tuple[dict, dict]:
    tmp = Path(tempfile.mkdtemp())
    try:
        feed = tmp / "feed"
        (feed / "IDQ65389").mkdir(parents=True)
        shutil.copy(ROOT / "tests/fixtures/IDQ60286_2026-10-22.html", feed / "IDQ60286.html")
        shutil.copy(ROOT / "tests/fixtures/IDQ65389.540111.tbl.shtml", feed / "IDQ65389" / "IDQ65389.540111.tbl.shtml")
        out = tmp / "out"
        council = json.loads((ROOT / "tests/fixtures/council_locations.json").read_text(encoding="utf-8"))
        code = collect.run(collect.DirSource(str(feed)), None, out,
                           datetime.fromisoformat("2026-10-22T10:40:00+10:00"), ROOT / "collector/stations.json",
                           council_fetcher=lambda: (council, []))
        if code:
            raise SystemExit(f"collector failed with {code}")
        latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
        history = json.loads((out / "history.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp)
    latest["demo"] = demo
    latest["generated"] = "2026-10-22T10:40:00+10:00"
    return latest, history


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output")
    ap.add_argument("--artifact", action="store_true")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    latest, history = snapshot()
    geo = json.loads((ROOT / "site/geo/seq-base.json").read_text(encoding="utf-8"))
    catch_path = ROOT / "site/geo/catchments.json"
    catchments = json.loads(catch_path.read_text(encoding="utf-8")) if catch_path.is_file() else None
    page = (ROOT / "site/index.html").read_text(encoding="utf-8")
    blob = json.dumps({"latest": latest, "history": history, "geo": geo, "catchments": catchments},
                      separators=(",", ":"), ensure_ascii=False)
    blob = blob.replace("</", "<\\/")
    page = page.replace('<script type="application/json" id="embedded-data"></script>',
                        f'<script type="application/json" id="embedded-data">{blob}</script>')
    if args.artifact:
        for pat in (r"<!doctype html>\s*", r"<html[^>]*>\s*", r"</?head>\s*", r"<body>\s*", r"</body>\s*", r"</html>\s*",
                    r'<meta charset="utf-8">\s*', r'<meta name="viewport"[^>]*>\s*'):
            page = re.sub(pat, "", page, count=1, flags=re.I)
    Path(args.output).write_text(page, encoding="utf-8")
    if args.data_dir:
        d = Path(args.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        live = dict(latest, demo=False)
        (d / "latest.json").write_text(json.dumps(live), encoding="utf-8")
        (d / "history.json").write_text(json.dumps(history), encoding="utf-8")
    print(f"wrote {args.output} ({len(page) // 1024} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
