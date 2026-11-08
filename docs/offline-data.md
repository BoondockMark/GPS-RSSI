# Offline maps and terrain data

The **Offline data** page prepares data for a computer without cellular service.
It is available in the workspace alongside Radios, Live view, and Surveys & exports.
Map images and terrain elevations are separate datasets: a topographic background
shows contours but does not supply the numeric elevations used for Fresnel checks.

## Before a drive

1. Open the workspace while connected. In **Offline data**, import a regional
   **raster MBTiles** map covering your driving area and required zoom levels.
   The layer appears as **Offline map** in live views and newly generated surveys.
   Importing another MBTiles file replaces this single regional background.
2. For an existing survey, load its route elevations and inspect the radio paths
   you expect to revisit. Use the intended fixed-site coordinates, spacing,
   query budget, and selected measurements. These actions cache the exact queried
   elevation locations and their available USGS source metadata.
3. Choose **Save offline pack**. Download `offline-data.zip` from Activity.
   Saving also pins current cache entries on this computer so working-cache
   eviction will not remove them. Filled gaps and imported pack entries are pinned
   too. Pinned data can exceed the ordinary 128 MB tile / 50,000-point cache budgets;
   check available disk space when preparing large datasets.
4. Turn on **Offline mode**. Reopen surveys and reload live views, then check
   the required area, backgrounds, zooms, and path inspections before leaving.
   If a regional map is present, it becomes the default background in Offline mode.

Offline mode prevents external tile and elevation requests, including refreshes.
It permits expired saved data and displays old elevation retrieval dates; source
metadata notes when elevations are older than 30 days. Missing tiles and elevation
points are queued on disk. A missing tile leaves a blank gap while available
tiles remain visible as you drive. Radio telemetry and local workspace access still work.
Without a regional map, only the background tiles actually viewed and retained
are available; the entry counts do not establish complete area coverage.

**Local terrain:** import the generated La Habra Heights GeoTIFF in **Offline data →
Use local terrain elevations** to sample new locations offline. The raster is
preferred over cached/online point values and travels in offline packs. See the
[height-map guide](heightmap.md) for setup, supported files, and fallback behavior.

USGS point caching does **not** provide a complete regional DEM. A different path,
measurement, fixed-site coordinate, or sampling setting can require new locations.
Offline missing elevations stay unknown, never zero or an invented interpolation.
Even a prepared path remains a sampled terrain check, not proof of coverage.

## Regional topographic MBTiles

Prepare a rendered topographic raster in QGIS or another GIS tool using a source
that permits offline use and transfer. For example, use downloaded USGS topo data,
render it as a map, and export a Web Mercator raster MBTiles file. Include lower
zoom overviews as well as the detailed driving scale. A DEM file containing raw
height values is not a rendered map. Import supported elevation GeoTIFFs using
**Import terrain GeoTIFF**, separately from the map background.

The importer supports PNG/JPEG raster images in standard MBTiles TMS row order,
with zoom levels 0–19. It preserves name, bounds, description, and source
attribution in the data pack; attribution is shown on the map. Vector PBF MBTiles,
PMTiles, and XYZ-row variants are not supported. Outside the included extent or
zoom levels, missing map tiles cannot be generated from the file.

The existing Street, Topographic, Shaded relief, and Satellite services continue
to cache interactive viewing. This feature does not bulk-download a region from
those public endpoints. Their service terms and offline permissions still apply;
use licensed/downloadable source data for regional packs.

## Transfer between computers

Copy `offline-data.zip` to the other computer, start its workspace, and choose
**Offline data → Import offline pack**. Import merges cached map tiles and
queried elevations, retaining newer destination entries and their retrieval dates.
An included regional map replaces the destination's regional map. Missing-data
queues are combined. The receiver's Offline mode setting is left unchanged;
turn it on explicitly before testing without connectivity.

Packs contain SQLite snapshots of the tile/elevation caches and regional data,
including committed WAL changes. Imports validate data in staging and write rows
into local schemas rather than installing database schemas from the ZIP. Packs
are limited to 1 GB expanded; browser uploads are limited to 128 MB. Larger files
can be imported with the command line below.

Recordings and radio setups travel separately: use recording **portable snapshots**
and import those from Surveys & exports. Create/open the survey on the receiving
workspace. Copying `map.html` alone does not transfer map or elevation caches, and
opening the HTML directly as a file cannot use workspace caches. Downloaded
coordinate data may identify driving routes and fixed sites; transfer packs as
part of your survey dataset.

## Fill missing data after a drive

Reconnect and turn off Offline mode. Choose **Fill missing data** and follow its
progress in Activity. Each operation attempts up to 1,000 queued requests, uses
the same fixed provider endpoints, and stops after three failures. Successful
cacheable results are pinned for future offline use. Missing tiles in an imported
MBTiles map need a replacement regional file; no server is contacted for them.

Reopen the survey and repeat the route/path inspection after filling. Adaptive
path refinement can discover more points once the initial missing elevations
are available, so another fill/inspection cycle may be needed. Alternatively,
finish the path inspection in connected mode, then save an updated offline pack.
Path results and analysis inputs are not saved in the pack; enter the same site
coordinates and settings when repeating an inspection. Reload the live view or
reselect a background that previously failed to request its tiles again.

## Command line

Run these commands from the app folder using its Python environment (or the
installed `gps-rssi` command). `--directory` chooses the app folder, not its `data`
subfolder. Paths after `--output` and input paths are relative to the current shell.

```sh
gps-rssi offline --directory . status
gps-rssi offline --directory . map /path/to/heights.mbtiles
gps-rssi offline --directory . export --output /path/to/offline-data.zip
gps-rssi offline --directory . import /path/to/offline-data.zip
gps-rssi offline --directory . mode on
# With connectivity restored:
gps-rssi offline --directory . mode off
gps-rssi offline --directory . fill --limit 1000
```

Data lives under `data/.gps-rssi-tiles`, `data/.gps-rssi-elevation`, and
`data/.gps-rssi-offline` in the app folder. Live recorders launched from the workspace share its prepared data and Offline
mode, including recordings stored in a different subfolder. Standalone dashboards
use the corresponding folders beside their recording database unless
`GPS_RSSI_MAP_DATA_DIRECTORY` names a shared data folder. Use pack export/import instead of copying live SQLite
files, which can omit active journal/WAL contents.
