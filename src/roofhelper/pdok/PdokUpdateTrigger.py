from datetime import datetime, timedelta, timezone
import jwt
import requests
import base64

from roofhelper.defaultlogging import setup_logging
from roofhelper.pdok.UploadResult import UploadResult

log = setup_logging()


class PdokUpdateTrigger:
    """Handles triggering PDOK updates after S3 upload."""

    def __init__(self, url: str, private_key_content: str):
        self.url = url
        self.private_key_content = base64.b64decode(private_key_content)

    def trigger_update(self, upload_result: UploadResult) -> bool:
        """Trigger PDOK update using upload result data."""
        if not upload_result.success:
            log.error("Cannot trigger update - upload was not successful")
            return False

        log.info("Send a signal to pdok that the upload is ready")

        try:
            payload = {
                "iss": "3dbasisvoorziening",
                "exp": datetime.now(timezone.utc) + timedelta(hours=1)
            }

            jwt_bearer: str = jwt.encode(payload, self.private_key_content, algorithm="RS256")

            # Data to send in the POST request
            # from_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            to_time = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            data = {
                "params": {
                    "key": upload_result.s3_upload_path,
                },
                "timeWindow": {
                    # "from": from_time,
                    "to": to_time,
                }
            }

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {jwt_bearer}'
            }

            response = requests.post(self.url, json=data, headers=headers)
            if response.status_code != 200:
                log.error(f"Failed to trigger update. Status code: {response.status_code}, Response: {response.text}")
                return False

            log.info("Successfully triggered PDOK update")
            return True

        except Exception as e:
            log.error(f"Exception occurred while triggering update: {str(e)}")
            return False
