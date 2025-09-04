from hera.workflows import DAG, WorkflowTemplate, Script, EmptyDirVolume, SecretVolume
from argo.argodefaults import argo_worker
from roofhelper.pdok.PdokGeopackageWriter import write_features_to_geopackage


@argo_worker(volumes=[
    EmptyDirVolume(name="workflow", mount_path="/workflow"),
    SecretVolume(name="pdok-secrets", mount_path="/var/secrets/pdok-delivery-secrets-buildings", secret_name="pdok-delivery-secrets-buildings")
])
def pdok_workflow_func() -> None:
    """Combined workflow to create PDOK index and trigger update using secrets."""
    import logging
    import os
    from pathlib import Path
    from main import trigger_pdok_update
    from roofhelper.pdok.PdokDeliveryGebouw import get_pdok_building_features, PDOK_DELIVERY_SCHEMA_GEBOUW
    from roofhelper.defaultlogging import setup_logging
    from roofhelper.io import SchemeFileHandler

    logger = setup_logging(logging.INFO)

    # Read configuration from mounted secrets
    secrets_path = Path("/var/secrets/pdok-delivery-secrets-buildings")

    def read_secret(key: str) -> str:
        """Read a secret value from the mounted secret volume."""
        try:
            secret_file = secrets_path / key
            if secret_file.exists():
                return secret_file.read_text().strip()
            else:
                raise FileNotFoundError(f"Secret key '{key}' not found in mounted secret")
        except Exception as e:
            logger.error(f"Failed to read secret '{key}': {e}")
            raise

    # Read all required configuration from secrets
    source = read_secret("source")
    ahn_source = "file:///ahn.json"
    url_prefix = read_secret("url_prefix")
    destination_s3_url = read_secret("destination_s3_url")
    destination_s3_user = read_secret("destination_s3_user")
    destination_s3_key = read_secret("destination_s3_key")
    s3_prefix = read_secret("s3_prefix")
    trigger_update_url = read_secret("trigger_update_url")
    trigger_private_key_content = read_secret("trigger_private_key_content")
    expected_gpkg_name = read_secret("expected_gpkg_name")

    logger.info("Successfully loaded configuration from secrets")

    # Step 1: Create PDOK index
    logger.info("Creating PDOK index")
    os.makedirs("/workflow/cache", exist_ok=True)

    # Download the ahn source file to get the path
    file_handler = SchemeFileHandler(Path("/workflow/cache"))
    ahn_path = file_handler.download_file(ahn_source)

    index_destination = "file:///workflow/cache/pdok_index.gpkg"
    features = get_pdok_building_features(source, ahn_path, url_prefix)
    write_features_to_geopackage(PDOK_DELIVERY_SCHEMA_GEBOUW, features, index_destination, Path("/workflow/cache"))

    logger.info("PDOK index created successfully")

    # Step 2: Trigger PDOK update using the created index
    logger.info("Starting PDOK update trigger")
    trigger_pdok_update(index_destination,
                        destination_s3_url,
                        destination_s3_user,
                        destination_s3_key,
                        s3_prefix,
                        trigger_update_url,
                        trigger_private_key_content,
                        expected_gpkg_name)

    logger.info("PDOK workflow completed successfully")


def generate_workflow() -> None:
    with WorkflowTemplate(name="pdokupdategebouw",
                          generate_name="pdokupdategebouw-",
                          entrypoint="pdokupdategebouwdag",
                          namespace="argo",
                          service_account_name="workflow-runner",
                          image_pull_secrets="acrdddprodman") as w:
        with DAG(name="pdokupdategebouwdag"):
            workflow: Script = pdok_workflow_func()  # type: ignore   # noqa: F841

        with open("generated/pdokupdategebouw.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
