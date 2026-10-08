import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import geopandas as gpd
from pyogrio.errors import DataLayerError, DataSourceError
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

# Every Shapefile needs these three; .prj (CRS) and .cpg (encoding) are optional.
SHAPEFILE_REQUIRED = (".shp", ".shx", ".dbf")
SHAPEFILE_OPTIONAL = (".prj", ".cpg")

# GDAL always reports KML coordinates as WGS84.
KML_CRS = CRS.from_epsg(4326)

class GeoParseError(Exception):
    """
    Raised when an uploaded file cannot be read as KML or a Shapefile ZIP.
    """

@dataclass(frozen=True)
class Feature:
    """
    One geospatial feature in the normalized, format-independent form.

    Attributes:
        id: 0-based position of the feature in the file, in file order
        geometry_type: Shapely geometry type (e.g. "Polygon", "MultiPolygon")
        geometry: The live Shapely geometry, in the file's own CRS (untransformed)
        crs: CRS exactly as read from the file
        properties: Attribute values keyed by field name
    """
    id: int
    geometry_type: str | None
    geometry: BaseGeometry | None
    crs: CRS | None
    properties: dict[str, Any]

@dataclass(frozen=True)
class ParsedFile:
    """
    All features of an uploaded file plus the file-level CRS.
    """
    crs: CRS | None
    features: list[Feature]


def parse_file(path: Path) -> ParsedFile:
    """
    Parse a stored upload (.kml or .zip containing a Shapefile) into features.

    Raises:
        GeoParseError: If the file is not a readable KML / valid Shapefile ZIP.
    """
    suffix = path.suffix.lower()
    if suffix == ".kml":
        layers, crs = _read_kml(path)
    elif suffix == ".zip":
        layers, crs = _read_shapefile_zip(path)
    else:
        raise GeoParseError(f"Unsupported file type '{suffix}'")

    features: list[Feature] = []
    for layer in layers:
        features.extend(_to_features(layer, crs, start_id=len(features)))
    return ParsedFile(crs=crs, features=features)


def _read_kml(path: Path) -> tuple[list[gpd.GeoDataFrame], CRS]:
    """
    Read every layer of a KML file.
    """
    try:
        layer_names = gpd.list_layers(path)["name"]
        layers = [gpd.read_file(path, layer=name) for name in layer_names]
    except (DataSourceError, DataLayerError) as exc:
        raise GeoParseError("Invalid or unreadable KML file") from exc
    return layers, layers[0].crs if layers else KML_CRS


def _read_shapefile_zip(path: Path) -> tuple[list[gpd.GeoDataFrame], CRS | None]:
    """
    Validate a Shapefile ZIP, extract only its components, and read it.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                name
                for name in archive.namelist()
                if not name.endswith("/")
                and not name.startswith("__MACOSX/")
                and not PurePosixPath(name).name.startswith("._")
            ]
            shp_members = [name for name in members if name.lower().endswith(".shp")]
            if not shp_members:
                raise GeoParseError("ZIP does not contain a .shp file")
            if len(shp_members) > 1:
                raise GeoParseError("ZIP must contain exactly one Shapefile")

            stem = shp_members[0][: -len(".shp")].lower()
            by_extension = {
                ext: next((name for name in members if name.lower() == stem + ext), None)
                for ext in SHAPEFILE_REQUIRED + SHAPEFILE_OPTIONAL
            }
            missing = [ext for ext in SHAPEFILE_REQUIRED if by_extension[ext] is None]
            if missing:
                raise GeoParseError(f"Shapefile is missing required component(s): {', '.join(missing)}")

            with tempfile.TemporaryDirectory() as tmp:
                for ext, name in by_extension.items():
                    if name is not None:
                        (Path(tmp) / f"shapefile{ext}").write_bytes(archive.read(name))
                try:
                    gdf = gpd.read_file(Path(tmp) / "shapefile.shp")
                except (DataSourceError, DataLayerError) as exc:
                    raise GeoParseError("Malformed or unreadable Shapefile") from exc
    except zipfile.BadZipFile as exc:
        raise GeoParseError("Not a valid ZIP archive") from exc
    return [gdf], gdf.crs


def _to_features(layer: gpd.GeoDataFrame, crs: CRS | None, start_id: int) -> list[Feature]:
    """
    Convert one GeoDataFrame into normalized features, numbered from start_id.
    """
    attributes = layer.drop(columns=layer.geometry.name)
    # NaN / NaT / None all become None so properties have one "missing" value.
    attributes = attributes.astype(object).where(attributes.notna(), None)
    records = attributes.to_dict("records")

    features = []
    for offset, (geometry, properties) in enumerate(zip(layer.geometry, records)):
        features.append(
            Feature(
                id=start_id + offset,
                geometry_type=geometry.geom_type if geometry is not None else None,
                geometry=geometry,
                crs=crs,
                properties=properties,
            )
        )
    return features


def _describe(parsed: ParsedFile) -> None:
    """
    Print a parsed file in a human-checkable form (used by the manual CLI).
    """
    crs = parsed.crs.to_string() if parsed.crs else None
    print(f"CRS: {crs}")
    print(f"Features: {len(parsed.features)}")
    for feature in parsed.features:
        wkt = feature.geometry.wkt if feature.geometry is not None else None
        if wkt and len(wkt) > 90:
            wkt = wkt[:87] + "..."
        print(f"\n[{feature.id}] {feature.geometry_type}")
        print(f"  geometry:   {wkt}")
        print(f"  properties: {feature.properties}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.services.parser <file.kml|file.zip>")
    try:
        _describe(parse_file(Path(sys.argv[1])))
    except GeoParseError as error:
        sys.exit(f"Parse error: {error}")
