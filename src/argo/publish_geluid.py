from hera.workflows import DAG, Script, EmptyDirVolume, SecretVolume, Parameter
from argo.argodefaults import argo_worker, get_workflow_template


@argo_worker(volumes=[
    EmptyDirVolume(name="workflow", mount_path="/workflow"),
    SecretVolume(name="pdok-secrets", mount_path="/var/secrets/pdok-delivery-secrets", secret_name="pdok-delivery-secrets")
])
def pdok_workflow_func(create_test_index: str = "false", additional_index_destination: str = "") -> None:
    """Combined workflow to create PDOK index and trigger update using secrets."""
    import logging
    import os
    from pathlib import Path
    from main import trigger_pdok_update
    from roofhelper.pdok.PdokDeliverySound import get_pdok_sound_features, PDOK_DELIVERY_SCHEMA_SOUND
    from roofhelper.pdok.PdokGeopackageWriter import write_features_to_geopackage
    from roofhelper.defaultlogging import setup_logging
    from roofhelper.io import SchemeFileHandler

    logger = setup_logging(logging.INFO)

    # Read configuration from mounted secrets
    secrets_path = Path("/var/secrets/pdok-delivery-secrets")

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
    # Parameters: additional copy destination and create_test_index flag
    additional_index_destination = (additional_index_destination or "").strip()
    create_test_index_flag = str(create_test_index).strip().lower() in {"1", "true", "yes", "y"}

    logger.info("Successfully loaded configuration from secrets")

    # Step 1: Create PDOK index
    logger.info("Creating PDOK index")
    os.makedirs("/workflow/cache", exist_ok=True)

    # Download the ahn source file to get the path
    file_handler = SchemeFileHandler(Path("/workflow/cache"))
    ahn_path = file_handler.download_file(ahn_source)

    # Always create the pdok_index locally so trigger_pdok_update can read/upload quickly
    local_index_destination = "file:///workflow/cache/pdok_index.gpkg"
    features = get_pdok_sound_features(source, ahn_path, url_prefix)
    write_features_to_geopackage(PDOK_DELIVERY_SCHEMA_SOUND, features, local_index_destination, Path("/workflow/cache"))

    logger.info("PDOK index created successfully")

    # If user defined an additional destination, make an extra copy
    if additional_index_destination:
        try:
            logger.info(f"Copying PDOK index to additional destination: {additional_index_destination}")
            local_index_path = Path("/workflow/cache/pdok_index.gpkg")
            file_handler.upload_file_direct(local_index_path, additional_index_destination)
            logger.info("Additional PDOK index copy uploaded successfully")
        except Exception as e:
            logger.error(f"Failed to upload additional PDOK index copy: {e}")
            raise

    # Step 2: Trigger PDOK update using the created index
    if create_test_index_flag:
        logger.info("create_test_index=true; skipping PDOK update trigger")
    else:
        logger.info("Starting PDOK update trigger")
        trigger_pdok_update(local_index_destination,
                            destination_s3_url,
                            destination_s3_user,
                            destination_s3_key,
                            s3_prefix,
                            trigger_update_url,
                            trigger_private_key_content,
                            expected_gpkg_name)

    logger.info("PDOK workflow completed successfully")


def generate_workflow() -> None:
    with get_workflow_template(__name__.split('.')[-1],
                               entrypoint="pdokupdategeluiddag",
                               arguments=[
                                   Parameter(name="create_test_index", default="false", enum=["true", "false"]),
                                   Parameter(name="additional_index_destination", default="")
    ]) as w:
        with DAG(name="pdokupdategeluiddag", inputs=[
            Parameter(name="create_test_index"),
            Parameter(name="additional_index_destination"),
        ]):
            workflow: Script = pdok_workflow_func(arguments={  # type: ignore   # noqa: F841
                "create_test_index": "{{inputs.parameters.create_test_index}}",
                "additional_index_destination": "{{inputs.parameters.additional_index_destination}}",
            })

        with open(f"generated/{w.name}.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
