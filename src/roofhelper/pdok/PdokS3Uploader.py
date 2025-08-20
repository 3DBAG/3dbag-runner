from datetime import datetime
from pathlib import Path
import requests
from requests_aws4auth import AWS4Auth

from roofhelper.defaultlogging import setup_logging
from roofhelper.pdok.UploadResult import UploadResult

log = setup_logging()


class PdokS3Uploader:
    """Handles S3 file uploads for PDOK delivery using direct requests with AWS4Auth."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key

    def upload_file(self, geopackage_file: Path, s3_prefix: str, expected_gpkg_name: str) -> UploadResult:
        """Upload a geopackage file to S3 and return upload results."""
        log.info(f"Uploading {geopackage_file} to {self.endpoint}")

        try:
            date_marker: str = datetime.now().strftime("%Y%m%d%H%M%S")
            s3_destination: str = f"{s3_prefix}/rel{date_marker}/{expected_gpkg_name}"
            trigger_update_path: str = f"{s3_prefix}/rel{date_marker}"

            log.info("Starting file upload...")

            # Create AWS4Auth for signing requests
            auth = AWS4Auth(
                self.access_key,
                self.secret_key,
                'us-east-1',  # region (can be arbitrary for S3-compatible services)
                's3'
            )

            # Construct the full URL
            url = f"{self.endpoint}/deliveries/{s3_destination}"

            # Get file size and read file
            import os
            file_size = os.path.getsize(geopackage_file)

            # Prepare headers
            headers = {
                'Content-Type': 'application/octet-stream',
                'Content-Length': str(file_size)
            }

            log.info(f"Uploading to URL: {url}")
            log.info(f"File size: {file_size} bytes")

            # Upload file using requests
            with open(geopackage_file, 'rb') as file_data:
                response = requests.put(
                    url,
                    data=file_data,
                    headers=headers,
                    auth=auth,
                    timeout=300  # 5 minute timeout
                )

            # Check response
            response.raise_for_status()
            log.info(f"Upload successful. Status code: {response.status_code}")

            log.info(f"Done uploading {geopackage_file} to {self.endpoint}/{s3_destination}")

            return UploadResult(
                s3_upload_path=trigger_update_path,
                s3_destination=s3_destination,
                date_marker=date_marker,
                success=True
            )
        except Exception as e:
            error_msg = f"Failed to upload {geopackage_file}: {str(e)}"
            log.error(error_msg)
            log.error(f"Exception type: {type(e).__name__}")
            # Log the full exception for debugging
            import traceback
            log.error(f"Full traceback: {traceback.format_exc()}")
            return UploadResult(
                s3_upload_path="",
                s3_destination="",
                date_marker="",
                success=False,
                error_message=error_msg
            )
