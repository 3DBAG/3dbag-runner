"""
Test cases for navigate behavior consistency between File and Azure scheme handlers.

This module tests that both FileSchemeFileHandler and AzureSchemeFileHandler
have consistent relative path navigation behavior.
"""

import os
import pytest
import tempfile
import shutil
import uuid
import requests
from pathlib import Path
from datetime import datetime, timedelta, UTC

from azure.storage.blob import BlobServiceClient
from azure.storage.blob import generate_container_sas, ContainerSasPermissions

from src.roofhelper.io.FileSchemeFileHandler import FileSchemeFileHandler
from src.roofhelper.io.AzureSchemeFileHandler import AzureSchemeFileHandler


def is_azurite_running() -> bool:
    """Check if Azurite is running locally on default port."""
    try:
        response = requests.get("http://127.0.0.1:10000/devstoreaccount1", timeout=2)
        return response.status_code in [200, 400, 404]  # Any response means it's running
    except BaseException:
        return False


def parse_connection_string(connection_string: str) -> tuple[str, str, str]:
    """Parse Azure connection string to extract account name, key, and endpoint suffix."""
    parts = {}
    for part in connection_string.split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            parts[key] = value

    account_name = parts.get('AccountName', '')
    account_key = parts.get('AccountKey', '')
    endpoint_suffix = parts.get('EndpointSuffix', 'core.windows.net')

    return account_name, account_key, endpoint_suffix


def generate_sas_token_from_connection_string(
    connection_string: str,
    container_name: str,
    permissions: ContainerSasPermissions,
    expiry: datetime
) -> str:
    """Generate SAS token using connection string."""
    account_name, account_key, _ = parse_connection_string(connection_string)

    return generate_container_sas(
        account_name=account_name,
        container_name=container_name,
        account_key=account_key,
        permission=permissions,
        expiry=expiry
    )


class TestNavigateBehaviorConsistency:
    """Test navigate behavior consistency between File and Azure scheme handlers."""

    CONNECTION_STRING = os.getenv(
        'AZURE_STORAGE_CONNECTION_STRING',
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    )

    def setup_method(self) -> None:
        """Set up test fixtures before each test method."""
        # File scheme setup
        self.test_dir = tempfile.mkdtemp()
        self.file_base_uri = f"file://{self.test_dir}"

        # Create test file structure
        self.test_file1 = Path(self.test_dir) / "test1.txt"
        self.sub_dir = Path(self.test_dir) / "subdir"
        self.sub_dir.mkdir()
        self.sub_file1 = self.sub_dir / "sub1.txt"
        self.nested_dir = self.sub_dir / "nested"
        self.nested_dir.mkdir()
        self.nested_file = self.nested_dir / "nested.md"

        # Write content to test files
        self.test_file1.write_text("Test content 1")
        self.sub_file1.write_text("Sub content 1")
        self.nested_file.write_text("# Nested markdown")

        # Azure scheme setup (only if Azurite is running)
        if is_azurite_running():
            self.container_name = f"test-{uuid.uuid4().hex[:8]}"
            self.blob_service_client = BlobServiceClient.from_connection_string(self.CONNECTION_STRING)
            self.container_client = self.blob_service_client.create_container(self.container_name)
            self.azure_base_uri = self._generate_azure_sas_uri()

            # Create test blobs
            self._setup_azure_test_blobs()

    def teardown_method(self) -> None:
        """Clean up test fixtures after each test method."""
        # Clean up file system
        shutil.rmtree(self.test_dir, ignore_errors=True)

        # Clean up Azure (if available)
        if hasattr(self, 'container_client'):
            try:
                self.container_client.delete_container()
            except BaseException:
                pass

    def _generate_azure_sas_uri(self) -> str:
        """Generate SAS URI for the container."""
        account_name, _, _ = parse_connection_string(self.CONNECTION_STRING)

        sas_token = generate_sas_token_from_connection_string(
            connection_string=self.CONNECTION_STRING,
            container_name=self.container_name,
            permissions=ContainerSasPermissions(read=True, write=True, delete=True, list=True),
            expiry=datetime.now(UTC) + timedelta(hours=1)
        )

        if account_name == "devstoreaccount1":
            # Azurite local endpoint
            blob_endpoint = f"http://127.0.0.1:10000/{account_name}"
        else:
            # Azure endpoint
            blob_endpoint = f"https://{account_name}.blob.core.windows.net"

        return f"azure://{blob_endpoint}/{self.container_name}?{sas_token}"

    def _setup_azure_test_blobs(self) -> None:
        """Create test blob structure."""
        test_files = {
            "test1.txt": "Test content 1",
            "subdir/sub1.txt": "Sub content 1",
            "subdir/nested/nested.md": "# Nested markdown"
        }

        for blob_name, content in test_files.items():
            blob_client = self.container_client.get_blob_client(blob_name)
            blob_client.upload_blob(content.encode('utf-8'), overwrite=True)

    def test_file_navigate_relative_single_level(self) -> None:
        """Test File scheme navigate with single level relative path."""
        base_uri = self.file_base_uri
        result = FileSchemeFileHandler.navigate(base_uri, "test1.txt")

        # Should be relative - just append to base
        expected = f"file://{self.test_dir}/test1.txt"
        assert result == expected
        assert FileSchemeFileHandler.file_exists(result)

    def test_file_navigate_relative_nested_path(self) -> None:
        """Test File scheme navigate with nested relative path."""
        base_uri = self.file_base_uri
        result = FileSchemeFileHandler.navigate(base_uri, "subdir/nested")

        # Should be relative - just append to base
        expected = f"file://{self.test_dir}/subdir/nested"
        assert result == expected
        assert os.path.exists(FileSchemeFileHandler._get_local_path(result))

    def test_file_navigate_from_subdirectory(self) -> None:
        """Test File scheme navigate from a subdirectory."""
        sub_uri = f"file://{self.sub_dir}"
        result = FileSchemeFileHandler.navigate(sub_uri, "nested/nested.md")

        # Should be relative to the subdirectory
        expected = f"file://{self.sub_dir}/nested/nested.md"
        assert result == expected
        assert FileSchemeFileHandler.file_exists(result)

    @pytest.mark.skipif(not is_azurite_running(), reason="Azurite not running")
    def test_azure_navigate_relative_single_level(self) -> None:
        """Test Azure scheme navigate with single level relative path."""
        base_uri = self.azure_base_uri
        result = AzureSchemeFileHandler.navigate(base_uri, "test1.txt")

        # Should be relative - just append to base
        assert result.startswith("azure://")
        assert "test1.txt" in result
        assert AzureSchemeFileHandler.file_exists(result)

    @pytest.mark.skipif(not is_azurite_running(), reason="Azurite not running")
    def test_azure_navigate_relative_nested_path(self) -> None:
        """Test Azure scheme navigate with nested relative path."""
        base_uri = self.azure_base_uri
        result = AzureSchemeFileHandler.navigate(base_uri, "subdir/nested")

        # Should be relative - just append to base
        assert result.startswith("azure://")
        assert "subdir/nested" in result

    @pytest.mark.skipif(not is_azurite_running(), reason="Azurite not running")
    def test_azure_navigate_from_subdirectory(self) -> None:
        """Test Azure scheme navigate from a subdirectory."""
        # First navigate to subdirectory
        sub_uri = AzureSchemeFileHandler.navigate(self.azure_base_uri, "subdir")
        # Then navigate to nested file
        result = AzureSchemeFileHandler.navigate(sub_uri, "nested/nested.md")

        # Should be relative to the subdirectory
        assert result.startswith("azure://")
        assert "subdir/nested/nested.md" in result
        assert AzureSchemeFileHandler.file_exists(result)

    def test_navigate_behavior_consistency(self) -> None:
        """Test that File and Azure navigation behavior is consistent."""
        # Test with simple relative path
        file_result = FileSchemeFileHandler.navigate(self.file_base_uri, "subdir/test.txt")

        # Both should just append the path to base URI
        assert file_result == f"file://{self.test_dir}/subdir/test.txt"

        if is_azurite_running():
            azure_result = AzureSchemeFileHandler.navigate(self.azure_base_uri, "subdir/test.txt")
            assert azure_result.startswith("azure://")
            assert "subdir/test.txt" in azure_result

    def test_navigate_multiple_levels(self) -> None:
        """Test navigating multiple levels deep."""
        # File scheme
        level1 = FileSchemeFileHandler.navigate(self.file_base_uri, "subdir")
        level2 = FileSchemeFileHandler.navigate(level1, "nested")
        level3 = FileSchemeFileHandler.navigate(level2, "nested.md")

        expected = f"file://{self.test_dir}/subdir/nested/nested.md"
        assert level3 == expected
        assert FileSchemeFileHandler.file_exists(level3)

        if is_azurite_running():
            # Azure scheme
            azure_level1 = AzureSchemeFileHandler.navigate(self.azure_base_uri, "subdir")
            azure_level2 = AzureSchemeFileHandler.navigate(azure_level1, "nested")
            azure_level3 = AzureSchemeFileHandler.navigate(azure_level2, "nested.md")

            assert azure_level3.startswith("azure://")
            assert "subdir/nested/nested.md" in azure_level3
            assert AzureSchemeFileHandler.file_exists(azure_level3)

    def test_navigate_empty_path(self) -> None:
        """Test navigate with empty path."""
        # File scheme
        result = FileSchemeFileHandler.navigate(self.file_base_uri, "")
        # Empty path should return the base path without trailing slash
        assert result == f"file://{self.test_dir}"

        if is_azurite_running():
            # Azure scheme
            azure_result = AzureSchemeFileHandler.navigate(self.azure_base_uri, "")
            assert azure_result.startswith("azure://")

    def test_navigate_with_leading_slash(self) -> None:
        """Test navigate with leading slash (should not become an absolute path)."""
        # File scheme
        result = FileSchemeFileHandler.navigate(self.file_base_uri, "/subdir/test.txt")
        # os.path.join makes leading slashes absolute, prevent this behavior
        assert result == f"file://{self.test_dir}/subdir/test.txt"

        if is_azurite_running():
            # Azure scheme
            azure_result = AzureSchemeFileHandler.navigate(self.azure_base_uri, "/subdir/test.txt")
            assert azure_result.startswith("azure://")
            assert "/subdir/test.txt" in azure_result
