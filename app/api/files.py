import json
import re
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from pyproj import CRS
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from app.services.crs import CRSHandlingError, project_for_measurement
from app.services.measurement import MeasurementError, measure_file
from app.services.parser import GeoParseError, ParsedFile, parse_file

router = APIRouter(prefix="/api/files", tags=["files"])

# Anchored to the project root so uploads land in the same place regardless of
# the directory the server is started from
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"

ALLOWED_EXTENSIONS = {".kml", ".zip"}

# Upload ids are uuid4 hex strings, anything else is rejected
FILE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

class FileUploadResponse(BaseModel):
    id: str
    filename: str
    status: str

class FeatureResponse(BaseModel):
    id: int
    geometry_type: str | None
    # GeoJSON-style geometry whose coordinates stay in the file's own CRS 
    geometry: dict[str, Any] | None
    crs: str | None
    properties: dict[str, Any]

class FileDetailResponse(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: str
    features: list[FeatureResponse]

class FeatureMeasurementResponse(BaseModel):
    feature_id: int
    geometry_type: str | None
    area_sq_m: float | None
    length_m: float | None
    note: str | None

class FileMeasurementsResponse(BaseModel):
    id: str
    measurement_crs: str | None
    measurements: list[FeatureMeasurementResponse]

def _validated_extension(filename: str | None) -> str:
    """
    Return the lowercase extension of the client filename if it is supported.

    Raises:
        HTTPException: 400 if no filename was sent, 415 if the extension is unsupported.
    """
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file has no filename")

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{extension}'. Allowed: {allowed}",
        )
    return extension

def _store_upload(
    upload: UploadFile, 
    file_id: str, 
    extension: str
) -> None:
    """
    Persist the upload under UPLOAD_DIR/<file_id>/ alongside its metadata.

    Raises:
        HTTPException: 500 if the file or its metadata cannot be written.
    """
    file_dir = UPLOAD_DIR / file_id
    try:
        file_dir.mkdir(parents=True)
        with open(file_dir / f"file{extension}", "wb") as destination:
            shutil.copyfileobj(upload.file, destination)
        metadata = {"id": file_id, "filename": upload.filename, "status": "UPLOADED"}
        (file_dir / "metadata.json").write_text(json.dumps(metadata))
    except OSError as exc:
        shutil.rmtree(file_dir, ignore_errors=True)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to store uploaded file"
        ) from exc


@router.post("/", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_file(file: UploadFile = File(...)) -> FileUploadResponse:
    """
    Accept a .kml or .zip (Shapefile) upload and store it for later processing.
    """
    extension = _validated_extension(file.filename)
    file_id = uuid.uuid4().hex
    _store_upload(file, file_id, extension)
    return FileUploadResponse(id=file_id, filename=file.filename, status="UPLOADED")

def _load_stored_file(file_id: str) -> tuple[dict[str, Any], Path]:
    """
    Locate a stored upload by id and return its metadata and data file path.

    The id comes from the URL and is joined into a filesystem path, so it is
    checked against FILE_ID_PATTERN first.

    Raises:
        HTTPException: 404 if the id is malformed or unknown, 500 if the upload
            directory exists but its metadata or data file is missing or corrupt.
    """
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    file_dir = UPLOAD_DIR / file_id
    if not file_dir.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    data_path = next(
        (
            file_dir / f"file{extension}"
            for extension in sorted(ALLOWED_EXTENSIONS)
            if (file_dir / f"file{extension}").is_file()
        ),
        None,
    )
    try:
        metadata = json.loads((file_dir / "metadata.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Stored file is missing or corrupt"
        ) from exc
    if data_path is None or not isinstance(metadata, dict) or "filename" not in metadata:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Stored file is missing or corrupt"
        )
    return metadata, data_path

def _parse_stored_file(file_id: str) -> tuple[dict[str, Any], ParsedFile]:
    """
    Look up a stored upload and parse it with the Stage 3 parser.

    Raises:
        HTTPException: 404/500 from the lookup, 422 if the file cannot be parsed.
    """
    metadata, data_path = _load_stored_file(file_id)
    try:
        return metadata, parse_file(data_path)
    except GeoParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

def _crs_label(crs: CRS | None) -> str | None:
    """Readable CRS label: 'EPSG:<code>', else the CRS name, or None if there is no CRS."""
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.name

def _geometry_to_json(geometry: BaseGeometry | None) -> dict[str, Any] | None:
    """GeoJSON-style geometry dict in the geometry's own CRS (not reprojected); None stays None."""
    return None if geometry is None else mapping(geometry)

@router.get("/{file_id}/", response_model=FileDetailResponse)
def get_file(file_id: str) -> FileDetailResponse:
    """
    Return the uploaded file's information and its parsed features.

    The file is parsed on every request; "COMPLETED" means parsing succeeded now.
    Geometries are returned in the file's own CRS.
    """
    metadata, parsed = _parse_stored_file(file_id)
    crs = _crs_label(parsed.crs)
    features = [
        FeatureResponse(
            id=feature.id,
            geometry_type=feature.geometry_type,
            geometry=_geometry_to_json(feature.geometry),
            crs=_crs_label(feature.crs),
            properties=feature.properties,
        )
        for feature in parsed.features
    ]
    return FileDetailResponse(
        id=file_id,
        filename=metadata["filename"],
        feature_count=len(features),
        crs=crs,
        status="COMPLETED",
        features=features,
    )

@router.get("/{file_id}/measurements/", response_model=FileMeasurementsResponse)
def get_file_measurements(file_id: str) -> FileMeasurementsResponse:
    """
    Return per-feature area (m²) and length (m), computed in a metre-based projected CRS.

    Raises:
        HTTPException: 422 if the file cannot be parsed, has no CRS, or no
            measurement CRS can be selected.
    """
    _, parsed = _parse_stored_file(file_id)
    try:
        projected = project_for_measurement(parsed)
        measurements = measure_file(projected)
    except (CRSHandlingError, MeasurementError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return FileMeasurementsResponse(
        id=file_id,
        measurement_crs=_crs_label(projected.measurement_crs),
        measurements=[FeatureMeasurementResponse(**asdict(m)) for m in measurements],
    )
