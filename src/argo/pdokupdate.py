from hera.workflows import DAG, WorkflowTemplate, Script, EmptyDirVolume, SecretVolume
from argo.argodefaults import argo_worker


@argo_worker(volumes=[
    EmptyDirVolume(name="workflow", mount_path="/workflow"),
    SecretVolume(name="pdok-secrets", mount_path="/var/secrets/pdok-delivery-secrets", secret_name="pdok-delivery-secrets")
])
def pdok_workflow_func() -> None:
    """Combined workflow to create PDOK index and trigger update using secrets."""
    import logging
    import os
    from pathlib import Path
    from main import trigger_pdok_update
    from roofhelper.pdok.PdokDeliveryProperties import create_pdok_index
    from roofhelper.defautlogging import setup_logging
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
    ahn_source = "/ahn.json"
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

    index_destination = Path("/workflow/cache/pdok_index.gpkg")
    create_pdok_index(source, ahn_path, index_destination, url_prefix, Path("/workflow/cache"))

    logger.info("PDOK index created successfully")

    # Step 2: Trigger PDOK update using the created index
    logger.info("Starting PDOK update trigger")
    trigger_pdok_update(f"file://{index_destination}",
                        destination_s3_url,
                        destination_s3_user,
                        destination_s3_key,
                        s3_prefix,
                        trigger_update_url,
                        trigger_private_key_content,
                        expected_gpkg_name)

    logger.info("PDOK workflow completed successfully")


def generate_workflow() -> None:
    with WorkflowTemplate(name="pdokupdate",
                          generate_name="pdokupdate-",
                          entrypoint="pdokupdatedag",
                          namespace="argo",
                          service_account_name="workflow-runner",
                          image_pull_secrets="acrdddprodman") as w:
        with DAG(name="pdokupdatedag"):
            workflow: Script = pdok_workflow_func()  # type: ignore   # noqa: F841

        with open("generated/pdokupdate.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
