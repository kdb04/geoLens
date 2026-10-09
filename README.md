# geoLens

A FastAPI backend that accepts a geospatial file (a **KML** file or a **ZIP containing a Shapefile**), extracts its features, and returns measurement information: **area** for polygons and **length** for lines, calculated in a projected, metre-based coordinate system.

- Upload a file: `POST /api/files/`
- Read the file's information and features: `GET /api/files/{id}/`
- Read the measurements: `GET /api/files/{id}/measurements/`

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer (developed and tested on 3.12). uv can download a suitable Python if you do not have one. GDAL does **not** need to be installed separately, as the Python wheels used here bundle it.

```bash
git clone https://github.com/kdb04/geoLens.git geoLens
cd geoLens
uv sync
uv run uvicorn app.main:app --reload
```

The server listens on http://localhost:8000.

- Interactive API docs (Swagger UI): http://localhost:8000/docs
- Health check: `curl http://localhost:8000/health` → `{"status":"ok"}`

Uploaded files are stored under `uploads/` in the project root (created automatically, not committed). There is no database and no configuration to set.

## API

All three endpoints live under `/api/files/`. IDs are 32-character lowercase hex strings generated on upload.

### `POST /api/files/` — upload

Multipart form upload with a single field named `file`. Accepted extensions: `.kml`, `.zip` (a ZIP containing a Shapefile). The file is stored under a new ID and **parsed during the upload** to extract its features. If it cannot be parsed (invalid KML, a ZIP that is not a valid Shapefile archive, ...) the upload is rejected with `422` and nothing is kept. Measurements are not calculated at upload. They are calculated by the measurements endpoint.

```bash
curl -F "file=@survey.kml" http://localhost:8000/api/files/
```

`201 Created`:

```json
{"id": "3f2a9c0d4b7e4d1a8c5e6f7a8b9c0d1e", "filename": "survey.kml", "status": "COMPLETED", "feature_count": 4, "crs": "EPSG:4326"}
```

`crs` is `null` when the file declares none (for example a Shapefile without a `.prj`). Such a file is still accepted, but its measurements will be refused.

- `201` → file stored and parsed
- `400` → uploaded part has no filename
- `415` → extension is not `.kml` or `.zip`
- `422` → the file cannot be parsed (invalid KML, not a valid ZIP, no `.shp`, missing `.shx`/`.dbf`, more than one Shapefile, malformed Shapefile, with the same messages as the GET endpoints), or no `file` field in the request
- `500` → the file could not be written to disk

### `GET /api/files/{id}/` — file information and features

Parses the stored file and returns the file information plus every feature: its index, geometry type, geometry, CRS and properties.

```bash
curl http://localhost:8000/api/files/3f2a9c0d4b7e4d1a8c5e6f7a8b9c0d1e/
```

`200 OK` (shortened, for a Shapefile ZIP `sample.zip` containing two polygons in EPSG:32643):

```json
{
  "id": "3f2a9c0d4b7e4d1a8c5e6f7a8b9c0d1e",
  "filename": "sample.zip",
  "feature_count": 2,
  "crs": "EPSG:32643",
  "status": "COMPLETED",
  "features": [
    {
      "id": 0,
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[500000.0, 1435000.0], [500000.0, 1435100.0], [500100.0, 1435100.0], [500100.0, 1435000.0], [500000.0, 1435000.0]]]
      },
      "crs": "EPSG:32643",
      "properties": {"name": "Plot A", "crop": "wheat", "rows": 12}
    },
    {
      "id": 1,
      "geometry_type": "Polygon",
      "geometry": {"type": "Polygon", "coordinates": ["..."]},
      "crs": "EPSG:32643",
      "properties": {"name": "Plot B", "crop": null, "rows": 7}
    }
  ]
}
```

Notes:

- `id` (per feature) is the 0-based position of the feature in the file. For KML, features from all Folders are numbered continuously in file order.
- `geometry` is a GeoJSON-style geometry object whose coordinates remain in the source CRS specified by `crs`. They are not necessarily longitude/latitude coordinates. A feature without geometry has `"geometry": null` and `"geometry_type": null`. Z values are kept when present.
- `crs` is `EPSG:<code>` when the CRS has an EPSG code, otherwise the CRS name, and `null` if the file declares no CRS (for example a Shapefile without a `.prj`).
- `properties` are the attributes of the feature. For KML they include the standard fields GDAL exposes (`Name`, `description`, `timestamp`, ...) plus any `ExtendedData`.
- `status` is `COMPLETED` for a successfully parsed file.

### `GET /api/files/{id}/measurements/` — measurements

Parses the file, brings the geometries into a metre-based projected CRS (see [CRS handling](#crs-handling)), and measures each feature.

```bash
curl http://localhost:8000/api/files/3f2a9c0d4b7e4d1a8c5e6f7a8b9c0d1e/measurements/
```

`200 OK` for a KML file in EPSG:4326 with a polygon, a line, a point and a `MultiGeometry`:

```json
{
  "id": "3f2a9c0d4b7e4d1a8c5e6f7a8b9c0d1e",
  "measurement_crs": "EPSG:32643",
  "measurements": [
    {"feature_id": 0, "geometry_type": "Polygon", "area_sq_m": 12016.970249021719, "length_m": null, "note": null},
    {"feature_id": 1, "geometry_type": "LineString", "area_sq_m": null, "length_m": 586.2127593883339, "note": null},
    {"feature_id": 2, "geometry_type": "Point", "area_sq_m": null, "length_m": null, "note": "No measurement defined for Point"},
    {"feature_id": 3, "geometry_type": "GeometryCollection", "area_sq_m": null, "length_m": null, "note": "Unsupported geometry type for measurement: GeometryCollection"}
  ]
}
```

- `area_sq_m` is set for `Polygon` / `MultiPolygon` (square metres, holes subtracted, parts summed).
- `length_m` is set for `LineString` / `MultiLineString` (metres, parts summed).
- `Point`, `MultiPoint` and any other geometry type (for example `GeometryCollection`) get no measurement and an explanatory `note`. Features with no geometry, or an empty geometry, get a note too. None of these is an error.
- A geometry that is not valid (for example a self-intersecting polygon) is **not repaired**. It is measured as-is and carries a note saying the geometry is invalid and the value may be unreliable.
- `measurement_crs` is the CRS the values were computed in. It is `null` when there was nothing to measure (no feature has a usable geometry).
- Values are not rounded.

### Errors (both GET endpoints)

Errors use FastAPI's standard body, `{"detail": "<message>"}`.

- `404` → unknown or malformed ID
- `422` → the stored file cannot be parsed (invalid KML, not a valid ZIP, no `.shp`, missing `.shx`/`.dbf`, more than one Shapefile, malformed Shapefile)
- `422` → measurements only: the file has no CRS
- `422` → measurements only: no measurement CRS can be chosen (data beyond 84°N / 80°S or impossible coordinates)
- `500` → the upload directory exists but its stored file or `metadata.json` is missing or corrupt

Malformed files are normally rejected at upload, so the `422` parsing errors above only occur if a stored file is damaged afterwards. A file with no CRS is accepted at upload and can still be read with `GET /api/files/{id}/` (it returns `"crs": null`). Only the measurements are refused.

## Architecture

![GeoLens architecture](architecture.png)

### Application structure

```text
app/
├── main.py              # FastAPI app, registers the routers
├── api/
│   ├── health.py        # GET /health
│   └── files.py         # upload + the two GET endpoints, response models, upload-directory lookup
└── services/            # framework-independent processing, no FastAPI imports
    ├── parser.py        # KML / Shapefile ZIP  ->  ParsedFile(crs, features)
    ├── crs.py           # ParsedFile  ->  ProjectedFile (metre-based CRS)
    └── measurement.py   # ProjectedFile  ->  list[FeatureMeasurement]
uploads/                 # created at runtime: uploads/<id>/file.kml|file.zip + metadata.json
```

The API layer handles HTTP requests and responses, while geospatial parsing, CRS transformation and measurement are implemented in separate service modules.

The processing pipeline uses typed internal data structures, while separate Pydantic models define the API responses.

### File-processing flow

```text
POST /api/files/
  -> check the extension (.kml / .zip)
  -> generate an id (uuid4 hex), independent of the client filename
  -> stream the upload to uploads/<id>/file.<ext>
  -> parse_file() the stored file (same parser as the GET endpoints)
       on a parsing error: delete uploads/<id>/ and answer 422
  -> write uploads/<id>/metadata.json  {"id", "filename" (original name), "status": "COMPLETED"}
  -> 201 {id, filename, status, feature_count, crs}

GET /api/files/{id}/
  -> validate the id (32 hex chars) and locate uploads/<id>/
  -> parse_file(): choose the reader by extension
       .kml : read every layer (one per Folder/Document) with GeoPandas/pyogrio (GDAL)
       .zip : validate and extract the Shapefile components, then read it (below)
  -> normalize every row into a Feature (index, geometry type, Shapely geometry, CRS, properties)
  -> 200 file information + features
```

Shapefile ZIP handling:

1. The archive must contain exactly one `.shp` together with its `.shx` and `.dbf` (a `.prj` and `.cpg` are used if present). Otherwise a descriptive `422` is returned. macOS `__MACOSX/` and `._*` entries are ignored.
2. Only those component files are extracted, into a temporary directory, under **fixed names** (`shapefile.shp`, `shapefile.dbf`, ...). Names and directory components from inside the archive are never used to build a path, which rules out path traversal ("Zip Slip"), and `extractall` is never called.
3. The Shapefile is read into memory and the temporary directory is removed immediately afterwards (also when reading fails). The original uploaded ZIP is never modified, so it stays available for later requests.

### Measurement calculation flow

```text
GET /api/files/{id}/measurements/
  -> locate and parse the file                  (parser.parse_file)
  -> choose one metre-based CRS and transform   (crs.project_for_measurement)
  -> measure each feature                       (measurement.measure_file)
  -> 200 {id, measurement_crs, measurements}
```

`measure_file` works only on the already-projected geometries and does no CRS handling of its own. Each feature is dispatched on its Shapely geometry type: `Polygon`/`MultiPolygon` use `.area`, `LineString`/`MultiLineString` use `.length`, everything else yields no measurement and a note. Measurements are planar and 2D (Z values are ignored, so lengths are horizontal distances). A file without any CRS raises an error here, because its coordinates cannot be interpreted. A file that has a CRS but no usable geometry simply yields notes.

### CRS handling

The assignment requires that area and distance are never computed on latitude/longitude degrees. One **measurement CRS is chosen per file** from the CRS that was read from the file (it is read and preserved by the parser, never assumed), and every geometry is transformed into it before measuring:

- none (e.g. Shapefile without `.prj`) → **never guessed.** Nothing is transformed. The file can be read (`crs: null`) but measurements return `422`.
- projected, in metres, not a Mercator / Pseudo-Mercator projection (e.g. UTM EPSG:32643, national grids) → used as-is, **no transformation**.
- geographic (e.g. EPSG:4326) → transformed to a UTM zone.
- projected but in feet (e.g. EPSG:2263), or Mercator / Web Mercator (EPSG:3857) → transformed to a UTM zone.
- anything else (geocentric, engineering, ...) → `422`: unsupported CRS type.

**UTM selection:** the zone is the WGS 84 UTM zone (EPSG:326xx north, EPSG:327xx south) containing the centre of the combined bounds of all geometries in the file, computed with GeoPandas' `estimate_utm_crs()` on the usable (non-null, non-empty) geometries. The transformation is done in one batch with pyproj (via GeoPandas). The result is always metre-based, so values are always square metres and metres.

Details:

- Null and empty geometries are carried over unchanged and ignored when locating the file.
- Invalid geometries are transformed as-is, never repaired.
- Data beyond 84°N / 80°S (outside UTM) or with impossible coordinates (e.g. latitude 200°) fails with `422`. A post-transform check also rejects non-finite coordinates.
- The geometry returned by `GET /api/files/{id}/` is always in the **original** CRS. The projected geometries are only used for measuring.

## Design decisions

- **FastAPI rather than Django.** The service is a small, stateless API: FastAPI gives request validation, Pydantic response models and interactive docs (`/docs`) with very little code. Django + DRF would add an ORM and project scaffolding that this assignment does not need.
- **Filesystem storage instead of a database.** Each upload lives in `uploads/<id>/` with a small `metadata.json` holding the original filename. The ID comes from `uuid4`, never from the client filename, and the stored name is built only from the ID and an allowlisted extension, so a hostile filename cannot influence the path. Alternatives considered: SQLite/PostgreSQL (more moving parts, and nothing here needs queries), object storage (unnecessary locally). The cost is that there is no listing, no atomic status tracking and no cleanup of old uploads.
- **Parse at upload, re-parse on read.** `POST` stores the file, parses it with the same `parse_file` the GET endpoints use, and only then accepts it: malformed files are rejected immediately with `422` and their directory is removed, so `uploads/` only ever contains files that parse. `metadata.json` is written last, with status `COMPLETED`. Measurements are deliberately not computed at upload: they are produced by the measurements endpoint. The parsed features are **not** persisted, so each GET parses the stored file again. This keeps the design free of a database or cache at the price of repeated work for large files.
- **GeoPandas (pyogrio / GDAL) for reading, one dependency.** It reads both KML and Shapefile, yields Shapely geometries and pyproj CRS objects, and the same stack does the transformation and the measurements. Alternatives: `fiona` (older engine), a dedicated KML library or hand-written XML parsing (more code, more edge cases). Caveat: GDAL's KML driver exposes one layer per Folder, so all layers are read explicitly, and it adds its own standard columns to the properties.
- **Services separated from the web layer.** Parsing, CRS selection and measurement are plain functions over dataclasses, each in its own module with a single exception type, so the routes contain no geospatial logic and each stage can be exercised on its own.
- **One UTM zone per file.** Alternatives: a zone per feature (slightly more accurate for far-apart features, but the file would have no single measurement CRS and values would come from different projections). Geodesic (ellipsoidal) calculation with `pyproj.Geod` (very accurate, but the assignment asks for a projected CRS). An equal-area projection centred on the data (exact areas but no EPSG code and distorted lengths). Web Mercator (rejected, it inflates areas by roughly 1/cos²(latitude), about 4× at 60° latitude). UTM is conformal, metre-based, and has EPSG codes that can be reported. Checked on the sample KML (a 0.001° × 0.001° polygon near 13°N): 12016.97 m² and a 586.21 m line against 12003.11 m² and 585.87 m from an independent ellipsoidal calculation.
- **A projected CRS is only trusted if it is in metres and not Mercator.** This is a deliberate simplification: it guarantees unambiguous square-metre and metre results at the cost of re-projecting some inputs (feet-based or Web Mercator data) that could in principle be measured directly. Other metre-based projections are trusted as given, and their area of use is not checked.
- **A missing CRS is never assumed.** Silently assuming EPSG:4326 would produce confidently wrong areas for a projected file. The file stays readable. Measuring it returns a clear `422`.
- **Unsupported geometries are handled gracefully, not rejected.** Parsing identifies every geometry. Measurement returns a `note` for `Point`, `MultiPoint`, `GeometryCollection` and other types (collections are not recursed into, to keep the rule predictable).
- **Invalid geometries are flagged, not repaired.** Repairing changes the user's data and can hide problems. A note tells the client the value may be unreliable.
- **Geometry in the response stays in the source CRS.** Reprojecting the output would add CRS logic the assignment does not ask for, and the `crs` field says how to interpret the coordinates. The geometry objects are GeoJSON-style dictionaries, which are not necessarily WGS 84 (strict RFC 7946 GeoJSON is).
- **Separate response models.** The Pydantic models define the HTTP contract and are decoupled from the internal dataclasses, so internals can change without breaking the API.
- **Error handling stays simple.** Service-specific errors become `422`, an unknown ID `404`, a damaged upload directory `500`. Unexpected exceptions are left to FastAPI's default handling instead of being swallowed.

## Command-line helpers

For inspecting each processing stage without the API:

```bash
uv run python -m app.services.parser       <file.kml|file.zip>   # parsed features and CRS
uv run python -m app.services.crs          <file.kml|file.zip>   # source CRS, measurement CRS, original vs projected geometry
uv run python -m app.services.measurement  <file.kml|file.zip>   # per-feature area / length
```

## Learning

- **GDAL's KML driver is layer-based.** KML files with multiple Folders may expose multiple layers, so each layer must be read explicitly to avoid missing features. GDAL also adds standard columns such as `Name`, `description` and `timestamp` alongside `ExtendedData`.
- **A Shapefile holds exactly one geometry type** (and is really a set of files: `.shp`, `.shx`, `.dbf`, optionally `.prj`), so mixed-geometry test data has to come from KML, and the missing `.prj` case is a real, common situation that must be handled explicitly.
- **Safe archive handling.** A ZIP's member names are untrusted input. Looking members up by name and writing them to fixed filenames in a temporary directory avoids Zip Slip without having to sanitize paths.
- **Why not measure in degrees.** The same polygon has an area of about `0.000001` "square degrees" and about 12,017 m² once projected, and a degree of longitude shrinks with latitude. Not every projected CRS is safe either: Web Mercator is metre-based but distorts areas heavily, and feet-based CRSs would make units ambiguous.
- **UTM zone selection and its limits.** Zones are 6° wide, the choice follows the centre of the data, data beyond 84°N / 80°S has no UTM zone, and distortion grows for files spanning many zones.
- **PROJ edge cases.** Out-of-range coordinates do not always raise: a transformation can silently return infinite coordinates, so the result has to be checked.
- **Making results JSON-safe.** DataFrame missing values (`NaN`/`NaT`) must be turned into `None` before they reach the response, and Shapely geometries need an explicit serialization (`mapping()`) that matches the coordinates' actual CRS.
- **FastAPI details.** Blocking file and geospatial work belongs in plain `def` routes (run in a threadpool). Path parameters that end up in filesystem paths must be validated before use.

## Future scope and limitations

Limitations of the current implementation:

- Parsed features are not persisted or cached, so each GET request reparses the file, which can increase response times for large files.
- Uploads are neither size-limited nor expired.
- No pagination: `GET /api/files/{id}/` returns every feature with its full geometry.
- Accuracy degrades for files spanning many UTM zones (one zone per file). Data crossing the antimeridian is not handled meaningfully. Data beyond 84°N / 80°S is rejected.
- `GeometryCollection` contents are not measured. Invalid geometries are measured as-is with a warning.
- KML `properties` include GDAL's standard KML columns, unfiltered.
- A projected CRS in metres is accepted without checking that it suits the data's location.
- Only KML and Shapefile ZIP are supported, one Shapefile per archive.

Possible next steps:

- Process uploads in a background worker with a real status lifecycle (`PROCESSING`/`FAILED`), and store the results in a database such as PostgreSQL/PostGIS, with listing and deleting of uploads and retention and size limits.
- Automated tests, CI and a Dockerfile.
- Protection against decompression bombs and upload size limits.
- Pagination, filtering, or a bounding-box summary for large files, plus optional reprojection of returned geometry to WGS 84.
- Better CRS handling: UPS for polar data, per-feature or equal-area options for very large extents, validation of a projected CRS's area of use, and geodesic measurement as an alternative.
- Measuring the contents of `GeometryCollection`s, optional geometry repair, and more input formats (GeoJSON, GeoPackage).
- Authentication and rate limiting if the service is exposed publicly.
