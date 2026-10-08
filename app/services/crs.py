import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import geopandas as gpd
import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.services.parser import Feature, GeoParseError, ParsedFile, parse_file

class CRSHandlingError(Exception):
    """
    Raised when geometries cannot be placed in a measurement CRS.
    """

@dataclass(frozen=True)
class ProjectedFile:
    """
    A parsed file prepared for measurement. The original ParsedFile is not modified.

    Attributes:
        source_crs: CRS exactly as parsed 
        measurement_crs: Metre-based CRS the features are expressed in
        transformed: False when the source CRS was already suitable and was kept
        features: Same ids, types, properties and order as the parsed features
    """
    source_crs: CRS | None
    measurement_crs: CRS | None
    transformed: bool
    features: list[Feature]

def project_for_measurement(parsed: ParsedFile) -> ProjectedFile:
    """
    Express a parsed file in a single metre-based CRS suitable for measurement.

    Raises:
        CRSHandlingError: If the CRS kind is unsupported, the data lies outside UTM
            coverage, or the transformation yields non-finite coordinates.
    """
    geometries = [feature.geometry for feature in parsed.features]
    target = select_projected_crs(parsed.crs, geometries)

    if target is None:
        return ProjectedFile(parsed.crs, None, False, list(parsed.features))
    if target == parsed.crs:
        return ProjectedFile(parsed.crs, target, False, list(parsed.features))

    projected = transform_geometries(geometries, parsed.crs, target)
    features = [
        replace(feature, geometry=geometry, crs=target)
        for feature, geometry in zip(parsed.features, projected)
    ]
    return ProjectedFile(parsed.crs, target, True, features)

def select_projected_crs(
    source_crs: CRS | None, 
    geometries: Sequence[BaseGeometry | None]
) -> CRS | None:
    """
    Choose the CRS in which the file will be measured. One CRS is chosen per file.

    Raises:
        CRSHandlingError: If the CRS is neither geographic nor projected, or no UTM
            zone exists for the data.
    """
    if source_crs is None:
        return None
    if not (source_crs.is_geographic or source_crs.is_projected):
        raise CRSHandlingError(f"Unsupported CRS type: {source_crs.name}")

    usable = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    if not usable:
        return None
    if _is_measurement_ready(source_crs):
        return source_crs

    try:
        return gpd.GeoSeries(usable, crs=source_crs).estimate_utm_crs()
    except RuntimeError as exc:
        raise CRSHandlingError(
            "Cannot select a UTM measurement CRS: geometry lies beyond 84°N / 80°S "
            "or its coordinates are outside valid longitude/latitude ranges"
        ) from exc

def transform_geometries(
    geometries: Sequence[BaseGeometry | None], 
    source_crs: CRS, 
    target_crs: CRS
) -> list[BaseGeometry | None]:
    """
    Transform all geometries from source_crs to target_crs in one batch.

    Raises:
        CRSHandlingError: If any transformed coordinate is not finite, e.g. latitude
            outside ±90° or a point far outside the target projection's valid area.
    """
    transformed = gpd.GeoSeries(list(geometries), crs=source_crs).to_crs(target_crs)

    usable = transformed[~(transformed.isna() | transformed.is_empty)]
    if len(usable) and not all(math.isfinite(value) for value in usable.total_bounds):
        raise CRSHandlingError(
            f"Transformation to {target_crs.to_string()} produced invalid coordinates; "
            "check that the file's coordinates are valid for its CRS"
        )
    return list(transformed)

def _is_measurement_ready(crs: CRS) -> bool:
    """
    True for a projected CRS in metres that is not a Mercator / Pseudo-Mercator.
    """
    if not crs.is_projected or crs.axis_info[0].unit_name != "metre":
        return False
    method = crs.coordinate_operation.method_name if crs.coordinate_operation else ""
    return not (method.startswith("Mercator") or "Pseudo Mercator" in method)


def _describe_crs(crs: CRS | None) -> str:
    """
    Format a CRS as e.g. 'EPSG:32643 (projected, metre)' for the manual CLI.
    """
    if crs is None:
        return "None"
    kind = "geographic" if crs.is_geographic else "projected" if crs.is_projected else "other"
    return f"{crs.to_string()} ({kind}, {crs.axis_info[0].unit_name})"


def _wkt(geometry: BaseGeometry | None, precision: int) -> str:
    """
    Shortened, rounded WKT for the manual CLI.
    """
    if geometry is None:
        return "None"
    text = shapely.to_wkt(geometry, rounding_precision=precision)
    return text if len(text) <= 110 else text[:107] + "..."


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.services.crs <file.kml|file.zip>")
    try:
        parsed = parse_file(Path(sys.argv[1]))
        projected = project_for_measurement(parsed)
    except GeoParseError as error:
        sys.exit(f"Parse error: {error}")
    except CRSHandlingError as error:
        sys.exit(f"CRS error: {error}")

    print(f"Source CRS:      {_describe_crs(projected.source_crs)}")
    print(f"Measurement CRS: {_describe_crs(projected.measurement_crs)}")
    print(f"Transformed:     {projected.transformed}")
    for original, feature in zip(parsed.features, projected.features):
        print(f"\n[{feature.id}] {feature.geometry_type}")
        print(f"  original:  {_wkt(original.geometry, 6)}")
        print(f"  projected: {_wkt(feature.geometry, 3)}")
