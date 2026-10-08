import sys
from dataclasses import dataclass
from pathlib import Path

from app.services.crs import CRSHandlingError, ProjectedFile, project_for_measurement
from app.services.parser import Feature, GeoParseError, parse_file

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}

class MeasurementError(Exception):
    """
    Raised when a file cannot be measured at all.
    """

@dataclass(frozen=True)
class FeatureMeasurement:
    """
    Measurement of one feature. Values are unrounded and in metres / square metres.

    Attributes:
        feature_id: Id of the measured Feature.
        geometry_type: Shapely geometry type, or None if the feature has no geometry.
        area_sq_m: Area in square metres; only set for Polygon / MultiPolygon.
        length_m: Length in metres; only set for LineString / MultiLineString.
        note: Why nothing was measured, or a caution about the value.    
    """
    feature_id: int
    geometry_type: str | None
    area_sq_m: float | None
    length_m: float | None
    note: str | None

def measure_file(projected: ProjectedFile) -> list[FeatureMeasurement]:
    """
    Measure every feature of a ProjectedFile: one result per feature, in feature order.

    Raises:
        MeasurementError: If the file declared no CRS, so its coordinates cannot be
            interpreted. A file that has a CRS but no usable geometry is not an error
    """
    if projected.source_crs is None:
        raise MeasurementError(
            "Cannot calculate measurements: the file has no CRS "
            "(include a .prj with the Shapefile)"
        )
    return [_measure_feature(feature) for feature in projected.features]


def _measure_feature(feature: Feature) -> FeatureMeasurement:
    """
    Measure a single feature, dispatching on its actual Shapely geometry type.
    """
    geometry = feature.geometry
    if geometry is None:
        return FeatureMeasurement(feature.id, None, None, None, "Feature has no geometry")

    kind = geometry.geom_type
    if geometry.is_empty:
        return FeatureMeasurement(feature.id, kind, None, None, "Empty geometry")
    if kind in POINT_TYPES:
        return FeatureMeasurement(feature.id, kind, None, None, f"No measurement defined for {kind}")

    note = None if geometry.is_valid else "Geometry is invalid; value may be unreliable"
    if kind in AREA_TYPES:
        return FeatureMeasurement(feature.id, kind, geometry.area, None, note)
    if kind in LENGTH_TYPES:
        return FeatureMeasurement(feature.id, kind, None, geometry.length, note)
    return FeatureMeasurement(
        feature.id, kind, None, None, f"Unsupported geometry type for measurement: {kind}"
    )

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.services.measurement <file.kml|file.zip>")
    try:
        projected = project_for_measurement(parse_file(Path(sys.argv[1])))
        measurements = measure_file(projected)
    except GeoParseError as error:
        sys.exit(f"Parse error: {error}")
    except CRSHandlingError as error:
        sys.exit(f"CRS error: {error}")
    except MeasurementError as error:
        sys.exit(f"Measurement error: {error}")

    crs = projected.measurement_crs.to_string() if projected.measurement_crs else None
    print(f"Measurement CRS: {crs}")
    print(f"Transformed:     {projected.transformed}")
    for m in measurements:
        if m.area_sq_m is not None:
            result = f"area   = {m.area_sq_m:.3f} m²"
        elif m.length_m is not None:
            result = f"length = {m.length_m:.3f} m"
        else:
            result = "no measurement"
        print(f"[{m.feature_id}] {m.geometry_type}: {result}" + (f"  ({m.note})" if m.note else ""))
