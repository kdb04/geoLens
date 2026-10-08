import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

router = APIRouter(prefix="/api/files", tags=["files"])

# Anchored to the project root so uploads land in the same place regardless of
# the directory the server is started from
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"

ALLOWED_EXTENSIONS = {".kml", ".zip"}


class FileUploadResponse(BaseModel):
    id: str
    filename: str
    status: str


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
