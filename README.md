# SEQ River Watch

A self-updating website that maps river heights and flood classes for every gauge in two Bureau of Meteorology river height bulletins, 336 gauges in all:

- [IDQ60286](https://www.bom.gov.au/cgi-bin/wrap_fwo.pl?IDQ60286.html) **Brisbane, Pine and Caboolture rivers and tributaries** (233 gauges): Ipswich/Brisbane Creeks, Pine/Caboolture, Stanley/Upper Brisbane, Lower Brisbane.
- [IDQ60285](https://www.bom.gov.au/cgi-bin/wrap_fwo.pl?IDQ60285.html) **Gold Coast rivers, Logan–Albert rivers and Tingalpa Creek** (103 gauges): Tingalpa Creek, Stradbroke Islands, Logan–Albert, Nerang, Coomera, Pimpama.

- **Map**: every gauge as a dot coloured by BoM flood class (below / minor / moderate / major), with an arrow inside when the river is rising or falling. Gauges at or above minor flood level carry a label with their height. Choose the background:
  - **Streets** (default): a clean grey street map from [OpenFreeMap](https://openfreemap.org), built from OpenStreetMap, with suburbs, roads and waterways.
  - **Topo**: the Queensland Government's topographic map.
  - **Aerial**: the Queensland Government's latest aerial imagery.
  - **Outline**: a simple built-in coastline with each waterway's gauges joined upstream to downstream. Used automatically if the others can't load.

  None of these need an account or key. Each option only appears if it loads for the visitor.
- **Catchments** (map button): the Queensland Government's drainage sub-basins (Stanley, Lockyer, Bremer, Brisbane, North and South Pine, Caboolture, Logan, Albert, Coomera and Nerang, Stradbroke Islands) and Brisbane City Council's 58 creek catchments, each shaded by the most serious flood class among its gauges. Pick a gauge to outline its catchment; its panel lists every gauge downstream on the same creek or river, in order. For a gauge on a sub-basin's main river it also follows the water into the next river (for example Lockyer Creek → Brisbane River from Lowood, Albert River → Logan River). Sub-basins also take in neighbouring creeks that reach the sea separately (Kedron Brook, for instance, sits in the South Pine sub-basin but flows to Moreton Bay), so for those creeks the panel names the sub-basin and makes no claim about the route.
- **Rivers**: each waterway drawn as a line of gauges from upstream to downstream, with the latest height and how far it is from the next flood level.
- **Gauge**: a flood gauge board, latest reading, BoM's minor/moderate/major levels, crossing heights, and a 24-hour / 3-day / 7-day hydrograph with a readings table.
- Updates itself about every 15 minutes, for free, on GitHub. No server, database or API key.

> **Not an official warning service.** Always follow [BoM Queensland warnings](https://www.bom.gov.au/qld/warnings/) and emergency services. SES: 132 500. Emergency: 000.

---

## Set it up (about 15 minutes, no coding)

You need a free GitHub account. Everything below happens in your web browser.

1. **Create the repository.** Sign in at [github.com](https://github.com), click **+ → New repository**, name it `seq-river-watch`, choose **Public** (free GitHub Pages sites must be public), and click **Create repository**.

2. **Upload the files.** On the new repository page click **uploading an existing file**, then drag in everything from this folder *except* the `.github` folder, and click **Commit changes**.
   The `.github` folder is hidden on most computers, so add its workflow by hand:
   click **Add file → Create new file**, type the name `.github/workflows/update.yml`, paste in the contents of that file from this folder, and click **Commit changes**. Repeat for `.github/workflows/keepalive.yml` (and `tests.yml` if you want the automatic tests).

3. **Turn on the website.** Go to **Settings → Pages**. Under *Build and deployment → Source*, choose **GitHub Actions**.

4. **Run the first update.** Go to the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**. Choose **Update river heights → Run workflow → Run workflow**. It takes about a minute.

5. **Open your site** at `https://<your-github-username>.github.io/seq-river-watch/`. The address is also shown on the finished workflow run and under **Settings → Pages**.

From then on it refreshes itself about every 15 minutes (each run starts the next one, because GitHub's own schedule proved unreliable, running only about every 4 hours), and the page reloads its data every 5 minutes while open. The 7-day hydrographs fill in as readings accumulate.

### If something goes wrong

| What you see | What it means / what to do |
|---|---|
| A workflow run failed at *Collect river heights from BoM* | BoM's feed couldn't be reached or returned nothing new. The site keeps its last good data and the next run tries again. Open the run's log to see which file failed. |
| A run failed at *configure-pages* | GitHub Pages isn't turned on yet: do step 3. |
| Yellow "may be out of date" banner on the site | No successful update in the last hour. Check the Actions tab. |
| Some runs in the Actions list show as *cancelled* | Normal. Only one update runs at a time, and a spare queued run is cancelled. |
| The site is more than an hour old | The 15-minute chain stopped (for example, a run was cancelled by hand). The schedule restarts it within a few hours, or start it now: **Actions → Update river heights → Run workflow**. |
| Updates stopped after a couple of months | GitHub pauses schedules in repositories with no commits for 60 days. `keepalive.yml` prevents this; if it happened anyway, open **Actions → Update river heights** and click **Enable workflow**. |
| Gauge shows no flood levels | BoM hasn't published levels for that gauge (common for small creeks and storages). |

---

## Where the data comes from

All data comes from BoM's **public data feed** for automated use, `ftp://ftp.bom.gov.au/anon/gen/fwo/`, not by scraping the BoM website. Each run makes one connection and downloads a handful of files:

| File | Gives |
|---|---|
| `IDQ60286.html`, `IDQ60285.html` | the two bulletins: areas, station order, gauge type, datum, BoM's flood class, tendency, crossing heights |
| `IDQ65325.html` | BoM's flood-warning map page: each gauge's coordinates, latest level, and minor/moderate/major flood levels (about 300 of the 336 gauges) |
| `IDQ65911_<time>.hcs` | 15-minute water levels for the last ~3 hours, every Queensland gauge |
| `IDQ65910_<time>.hcs` | latest water level per gauge |
| `IDQ65389/IDQ65389.<station>.tbl.shtml` | 7-day tables, used a few per run to fill early history, *only if the feed offers them* (it didn't as of October 2026, so history builds up from the first run) |

**Gauge positions.** BoM's published positions are often rounded, some to the nearest minute of latitude and longitude (up to about 1 km out). So once a day the collector also reads the councils' own gauge registers, which list each gauge's surveyed position by BoM station number:

- [Brisbane City Council: Telemetry sensors, rainfall and stream heights](https://www.data.qld.gov.au/dataset/telemetry-sensors-rainfall-and-stream-heights-metadata) (about 45 gauges)
- [City of Moreton Bay: Flood Alert Gauge](https://datahub.moretonbay.qld.gov.au/datasets/moretonbay::flood-alert-gauge) (about 45 gauges, CC BY 4.0)

These cover 90 of the 336 gauges; no council publishes an equivalent list for the Logan, Gold Coast, Ipswich, Lockyer or Somerset gauges. The rest use BoM's position, and each gauge's panel says which source its marker uses and how precise it is.

**Catchments.** `site/geo/catchments.json` is built by `tools/fetch_catchments.py` and refreshed monthly by the *Refresh catchment boundaries* workflow (run it from the Actions tab at any time). How each sub-basin drains (for example Lockyer Creek into the Brisbane River below Wivenhoe Dam) is set in that script.

Other `IDQ65xxx.html` map pages are scanned at most once a day, and only to find gauges missing from `IDQ65325.html`; any page that supplies gauges is remembered and read on every run.

Each run's results (which files were found, how many gauges got readings, coordinates and flood levels) appear as notes on the run's page under **Actions**.

The collector keeps going if any single file is missing. If a bulletin can't be read, its gauges come from the station list in `collector/stations.json` (built from the 26 October 2026 bulletins) and takes classes from the map page or works them out from BoM's flood levels. Each gauge in the page says where its class, tendency and flood levels came from.

**History without a database.** Each run downloads the previous `data/history.json` from your own published site, adds the new readings, trims to 7 days and republishes it.

**Careful handling.** Missing values stay missing (never zero). Heights keep their own datum (local gauge height or AHD); readings on a different datum from the bulletin are dropped rather than mixed. Flood levels are never borrowed from a neighbouring gauge. Bulletin times like "10.14AM Thu" are dated from the issue time.

### Credits and licences

- River heights: © Commonwealth of Australia, Bureau of Meteorology, [CC BY 4.0 AU](https://creativecommons.org/licenses/by/4.0/au/). Most gauge data is supplied to BoM by other agencies, in real time and without quality control, and is subject to those agencies' terms.
- Gauge positions: Brisbane City Council and City of Moreton Bay gauge registers (open data); otherwise BoM's flood-warning map page and Queensland flood warning river height station catalogue (5 January 2022).
- Coastline, Brisbane River and Lake Wivenhoe outlines: [Natural Earth](https://www.naturalearthdata.com/) (public domain).
- Catchments: drainage sub-basins © State of Queensland (Department of Resources); creek catchments © Brisbane City Council.
- Map library: [Leaflet](https://leafletjs.com/) 1.9.4 (BSD-2-Clause); street map drawn with [MapLibre GL JS](https://maplibre.org/) 5.24 through [maplibre-gl-leaflet](https://github.com/maplibre/maplibre-gl-leaflet).
- Street map: [OpenFreeMap](https://openfreemap.org) © [OpenMapTiles](https://www.openmaptiles.org/), data from [OpenStreetMap](https://www.openstreetmap.org/copyright). Free with no key or usage limit, but no uptime guarantee; the project runs on donations.
- Topographic map and aerial imagery: © State of Queensland, from the public `QldMap_Topo` and `LatestStateProgram_AllUsers` basemap services on QSpatial. Their catalogue entries don't state a licence, so keep the © State of Queensland credit visible (the map does this automatically).

### Other map providers

If you'd rather use a different look, these need a free account and an API key added to `BASEMAPS` in `site/index.html`:

| Provider | Free tier (check current terms) | Good for |
|---|---|---|
| [MapTiler](https://www.maptiler.com/) | free key for small sites | polished streets, topo and satellite styles |
| [Stadia Maps](https://stadiamaps.com/) | free for non-commercial use, register your domain | the very clean "Alidade Smooth" style |
| [CARTO](https://carto.com/basemaps) | free key, up to 5 million tile requests a month for non-commercial use | the classic "Positron" light map used by many news sites |
| [Esri / ArcGIS Location Platform](https://developers.arcgis.com/) | free developer account and key | Esri topographic and world imagery, common on government sites |

---

## For developers

```
collector/collect.py      the scheduled job (Python 3.10+, standard library only)
collector/bom_parse.py    parsers for the bulletin, station tables, map pages and BOM-HCS files
collector/stations.json   bundled station list and fallback coordinates
site/index.html           the whole website: one file, Leaflet from cdnjs, MapLibre loaded only for the street map
site/geo/seq-base.json    coastline / river / lake outlines for the map
site/data/                written by the collector (not committed)
tests/                    unit tests, including a real FTP round-trip against a local server
tools/                    catalogue builder and the sample-data preview builder
```

Run it locally:

```bash
python collector/collect.py --out site/data          # needs internet access to ftp.bom.gov.au
python -m http.server -d site 8000                   # then open http://localhost:8000
python -m unittest discover -s tests                 # pip install pyftpdlib for the FTP test
python tools/make_preview.py preview.html            # single file with the labelled sample snapshot
```

To add another BoM bulletin: run the *Capture BoM feed files* workflow with its file name (e.g. `IDQ60284.html`), add it to `BULLETINS` at the top of `collector/collect.py`, then rebuild the station list with `python tools/build_catalogue.py <coords.json> <each bulletin, in BULLETINS order>`.
