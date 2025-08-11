from dataclasses import dataclass
from typing import Optional


@dataclass
class UploadResult:
    """Result structure containing upload information and trigger data."""
    s3_upload_path: str
    s3_destination: str
    date_marker: str
    success: bool
    error_message: Optional[str] = None
