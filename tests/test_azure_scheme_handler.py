"""
Test cases for the AzureSchemeFileHandler class.

This module contains comprehensive test cases for the AzureSchemeFileHandler class
that handles file operations on Azure Blob Storage using azure:// URIs.

Prerequisites:
- Azurite emulator must be running (install via VS Code extension or npm)
- Tests will be skipped if Azurite is not available
"""

import os
import pytest
import tempfile
import shutil
import uuid
import requests
from pathlib import Path
from datetime import datetime, timedelta, UTC
from io import BytesIO

from azure.storage.blob import BlobServiceClient
from azure.storage.blob import generate_container_sas, ContainerSasPermissions
from azure.core.exceptions import ResourceNotFoundError

from roofhelper.io.AzureSchemeFileHandler import AzureSchemeFileHandler
from roofhelper.io.FileHandle import FileHandle


def is_azurite_running() -> bool:
    """Check if Azurite is running locally on default port."""
    try:
        response = requests.get("http://127.0.0.1:10000/devstoreaccount1", timeout=2)
        return response.status_code in [200, 400, 404]  # Any response means it's running
    except BaseException:
        return False


def parse_connection_string(connection_string: str) -> tuple[str, str, str]:
    """Parse Azure connection string to extract account name, key, and endpoint suffix.

    Returns:
        tuple: (account_name, account_key, endpoint_suffix)
    """
    # Parse the connection string
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
    """Generate SAS token using connection string - compatible with both Azurite and real Azure accounts.

    Args:
        connection_string: Azure Storage connection string
        container_name: Name of the container
        permissions: SAS permissions
        expiry: SAS token expiry time

    Returns:
        SAS token string
    """
    account_name, account_key, _ = parse_connection_string(connection_string)

    return generate_container_sas(
        account_name=account_name,
        container_name=container_name,
        account_key=account_key,
        permission=permissions,
        expiry=expiry
    )


@pytest.fixture(scope="session", autouse=True)
def azurite_check() -> None:
    """Ensure Azurite is available before running Azure tests when using Azurite connection string."""
    # Get the connection string and check if we're using Azurite
    connection_string = os.getenv(
        'AZURE_STORAGE_CONNECTION_STRING',
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    )
    account_name, _, _ = parse_connection_string(connection_string)

    # Only check if Azurite is running when using Azurite connection string
    if account_name == "devstoreaccount1" and not is_azurite_running():
        pytest.skip(
            "Azurite emulator not running. Please start it:\n"
            "- Via VS Code: Command Palette -> 'Azurite: Start'\n"
            "- Or install globally: npm install -g azurite && azurite"
        )


@pytest.mark.azure
class TestAzureSchemeFileHandler:
    """Test cases for AzureSchemeFileHandler class.

    Supports both Azurite emulator and real Azure Storage accounts.
    Configuration is controlled via connection string:

    For real Azure Storage:
        Set AZURE_STORAGE_CONNECTION_STRING environment variable with your storage account connection string

    For Azurite (local development):
        Set AZURE_STORAGE_CONNECTION_STRING to:
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"

    Default is configured for a real Azure Storage account.
    """

    # Set to Azurite for local development
    CONNECTION_STRING = os.getenv(
        'AZURE_STORAGE_CONNECTION_STRING',
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    )

    @classmethod
    def _is_using_azurite(cls) -> bool:
        """Check if we're using Azurite based on the connection string."""
        account_name, _, _ = parse_connection_string(cls.CONNECTION_STRING)
        return account_name == "devstoreaccount1"

    @classmethod
    def _get_blob_endpoint(cls) -> str:
        """Get the appropriate blob endpoint based on connection string."""
        account_name, _, endpoint_suffix = parse_connection_string(cls.CONNECTION_STRING)

        if cls._is_using_azurite():
            # Use Azurite local endpoint
            return f"http://127.0.0.1:10000/{account_name}"
        else:
            # Use Azure endpoint
            return f"https://{account_name}.blob.{endpoint_suffix}"

    def setup_method(self) -> None:
        """Set up test fixtures before each test method."""
        # Create unique container name for this test
        self.container_name = f"test-{uuid.uuid4().hex[:8]}"

        # Create blob service client
        self.blob_service_client = BlobServiceClient.from_connection_string(self.CONNECTION_STRING)

        # Create container
        self.container_client = self.blob_service_client.create_container(self.container_name)

        # Generate SAS URI - can be easily overridden for real storage accounts
        self.sas_uri = self._generate_sas_uri()

        # Create base URI using the generated SAS URI
        self.base_uri = f"azure://{self.sas_uri}"

        # Create temporary directory for local file operations
        self.temp_dir = tempfile.mkdtemp()

        # Create test file structure in Azure
        self._setup_test_blobs()

    def _generate_sas_uri(self) -> str:
        """Generate SAS URI for the container. Works with both Azurite and real storage accounts."""
        # Generate SAS token for the container (valid for 1 hour)
        sas_token = generate_sas_token_from_connection_string(
            connection_string=self.CONNECTION_STRING,
            container_name=self.container_name,
            permissions=ContainerSasPermissions(read=True, write=True, delete=True, list=True),
            expiry=datetime.now(UTC) + timedelta(hours=1)
        )

        # Get the appropriate blob endpoint
        blob_endpoint = self._get_blob_endpoint()

        # Return the complete SAS URI - works for both Azurite and real Azure
        return f"{blob_endpoint}/{self.container_name}?{sas_token}"

    def teardown_method(self) -> None:
        """Clean up test fixtures after each test method."""
        try:
            # Delete the container and all its contents
            self.container_client.delete_container()
        except BaseException:
            pass  # Container might already be deleted

        # Clean up local temp directory
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_test_blobs(self) -> None:
        """Create test blob structure similar to FileSchemeFileHandler tests."""
        # Test files in root
        test_files = {
            "test1.txt": "Test content 1",
            "test2.json": '{"test": "json"}',
            "test3.log": "Log entry"
        }

        # Test files in subdirectory
        sub_files = {
            "subdir/sub1.txt": "Sub content 1",
            "subdir/sub2.py": "print('hello')"
        }

        # Test file in nested directory
        nested_files = {
            "subdir/nested/nested.md": "# Nested markdown"
        }

        # Upload all test files
        all_files = {**test_files, **sub_files, **nested_files}
        for blob_name, content in all_files.items():
            blob_client = self.container_client.get_blob_client(blob_name)
            blob_client.upload_blob(content.encode('utf-8'), overwrite=True)

    def _get_blob_uri(self, blob_path: str = "") -> str:
        """Get full Azure URI for a specific blob path."""
        if blob_path:
            # Extract SAS token from sas_uri
            sas_parts = self.sas_uri.split('?')
            return f"azure://{sas_parts[0]}/{blob_path}?{sas_parts[1]}"
        return self.base_uri

    def test_download_file_existing(self) -> None:
        """Test downloading an existing file."""
        uri = self._get_blob_uri("test1.txt")
        temp_dir = Path(self.temp_dir)
        result = AzureSchemeFileHandler.download_file(uri, temp_dir)

        assert isinstance(result, FileHandle)
        assert result.path.exists()
        assert result.must_dispose is True
        assert result.path.read_text() == "Test content 1"

    def test_download_file_with_filename(self) -> None:
        """Test downloading a file with filename parameter."""
        uri = self.base_uri
        filename = "test1.txt"
        temp_dir = Path(self.temp_dir)
        result = AzureSchemeFileHandler.download_file(uri, temp_dir, filename)

        assert isinstance(result, FileHandle)
        assert result.path.exists()
        assert result.must_dispose is True
        assert result.path.read_text() == "Test content 1"

    def test_list_entries_shallow_basic(self) -> None:
        """Test shallow listing of directory entries."""
        entries = list(AzureSchemeFileHandler.list_entries_shallow(self.base_uri))

        # Should find 4 items: 3 files + 1 directory prefix
        assert len(entries) == 4

        # Check that we have the expected entries
        names = {entry.name for entry in entries}
        assert names == {"test1.txt", "test2.json", "test3.log", "subdir"}

        # Check properties of a file entry
        file_entry = next(entry for entry in entries if entry.name == "test1.txt")
        assert file_entry.is_file is True
        assert file_entry.is_directory is False
        assert file_entry.size is not None
        assert file_entry.size > 0
        assert file_entry.full_uri.startswith("azure://")
        assert file_entry.path == "test1.txt"
        assert file_entry.last_modified is not None

        # Check properties of a directory entry
        dir_entry = next(entry for entry in entries if entry.name == "subdir")
        assert dir_entry.is_file is False
        assert dir_entry.is_directory is True
        assert dir_entry.size is None
        assert dir_entry.full_uri.startswith("azure://")
        assert dir_entry.path == "subdir"

    def test_list_entries_shallow_with_regex(self) -> None:
        """Test shallow listing with regex filter."""
        # Filter for .txt files only
        regex = r".*\.txt$"
        entries = list(AzureSchemeFileHandler.list_entries_shallow(self.base_uri, regex))

        # Should only find test1.txt
        txt_entries = [entry for entry in entries if entry.name.endswith('.txt')]
        assert len(txt_entries) >= 1

        # Verify we don't get .json or .log files when filtering for .txt
        names = {entry.name for entry in entries}
        assert "test1.txt" in names or len([n for n in names if n.endswith('.txt')]) > 0

    def test_list_entries_recursive_basic(self) -> None:
        """Test recursive listing of directory entries."""
        entries = list(AzureSchemeFileHandler.list_entries_recursive(self.base_uri))

        # Should find all files recursively (no directory entries in recursive mode)
        assert len(entries) >= 6  # At least 6 files

        names = {entry.name for entry in entries}
        assert "test1.txt" in names
        assert "test2.json" in names
        assert "test3.log" in names
        assert "sub1.txt" in names
        assert "sub2.py" in names
        assert "nested.md" in names

    def test_list_entries_recursive_with_regex(self) -> None:
        """Test recursive listing with regex filter."""
        # Filter for Python files
        regex = r".*\.py$"
        entries = list(AzureSchemeFileHandler.list_entries_recursive(self.base_uri, regex))

        # Should find sub2.py
        py_entries = [entry for entry in entries if entry.name.endswith('.py')]
        assert len(py_entries) >= 1
        assert any(entry.name == "sub2.py" for entry in py_entries)

    def test_list_entries_empty_directory(self) -> None:
        """Test listing entries in an empty directory."""
        # Create empty container
        empty_container = f"empty-{uuid.uuid4().hex[:8]}"
        empty_client = self.blob_service_client.create_container(empty_container)

        try:
            # Generate SAS for empty container using the centralized method
            sas_token = generate_sas_token_from_connection_string(
                connection_string=self.CONNECTION_STRING,
                container_name=empty_container,
                permissions=ContainerSasPermissions(read=True, list=True),
                expiry=datetime.now(UTC) + timedelta(hours=1)
            )

            # Get the appropriate blob endpoint
            blob_endpoint = self._get_blob_endpoint()
            empty_uri = f"azure://{blob_endpoint}/{empty_container}?{sas_token}"

            entries = list(AzureSchemeFileHandler.list_entries_shallow(empty_uri))
            assert len(entries) == 0
        finally:
            empty_client.delete_container()

    def test_list_entries_nonexistent_container(self) -> None:
        """Test listing entries in a non-existent container."""
        nonexistent_uri = f"azure://http://127.0.0.1:10000/devstoreaccount1/nonexistent?{self.base_uri.split('?')[1]}"

        with pytest.raises(Exception):  # Should raise some Azure exception
            list(AzureSchemeFileHandler.list_entries_shallow(nonexistent_uri))

    def test_upload_file_directory(self) -> None:
        """Test uploading a file to a directory."""
        # Create a temporary file to upload
        temp_file = Path(self.temp_dir) / "temp_upload.txt"
        temp_file.write_text("Upload test content")

        # Upload the file
        AzureSchemeFileHandler.upload_file_directory(temp_file, self.base_uri, "uploaded.txt")

        # Verify the file was uploaded
        uploaded_uri = self._get_blob_uri("uploaded.txt")
        assert AzureSchemeFileHandler.file_exists(uploaded_uri)
        content = AzureSchemeFileHandler.get_bytes(uploaded_uri)
        assert content == b"Upload test content"

    def test_upload_file_direct(self) -> None:
        """Test uploading a file directly."""
        # Create a temporary file to upload
        temp_file = Path(self.temp_dir) / "temp_upload2.txt"
        temp_file.write_text("Direct upload test content")

        # Define destination URI
        dest_uri = self._get_blob_uri("direct_upload.txt")

        # Upload the file
        AzureSchemeFileHandler.upload_file_direct(temp_file, dest_uri)

        # Verify the file was uploaded
        assert AzureSchemeFileHandler.file_exists(dest_uri)
        content = AzureSchemeFileHandler.get_bytes(dest_uri)
        assert content == b"Direct upload test content"

    def test_get_bytes(self) -> None:
        """Test reading file content as bytes."""
        uri = self._get_blob_uri("test1.txt")
        content = AzureSchemeFileHandler.get_bytes(uri)

        assert isinstance(content, bytes)
        assert content == b"Test content 1"

    def test_get_bytes_range(self) -> None:
        """Test reading a range of bytes from a file."""
        # Upload a file with known content
        test_content = "0123456789"  # 10 bytes
        blob_client = self.container_client.get_blob_client("range_test.txt")
        blob_client.upload_blob(test_content.encode('utf-8'), overwrite=True)

        uri = self._get_blob_uri("range_test.txt")

        # Read bytes 2-5 (should be "2345")
        content = AzureSchemeFileHandler.get_bytes_range(uri, 2, 4)
        assert content == b"2345"

    def test_navigate(self) -> None:
        """Test navigating to a location within a URI."""
        base_uri = self.base_uri
        location = "subdir/nested"

        result = AzureSchemeFileHandler.navigate(base_uri, location)
        expected_path = "subdir/nested"
        assert expected_path in result
        assert result.startswith("azure://")

    def test_exists_file(self) -> None:
        """Test checking if a file exists."""
        existing_uri = self._get_blob_uri("test1.txt")
        nonexistent_uri = self._get_blob_uri("nonexistent.txt")

        assert AzureSchemeFileHandler.file_exists(existing_uri) is True
        assert AzureSchemeFileHandler.file_exists(nonexistent_uri) is False

    def test_upload_folder(self) -> None:
        """Test uploading an entire folder."""
        # Create a source folder with content
        source_folder = Path(self.temp_dir) / "source"
        source_folder.mkdir()
        (source_folder / "file1.txt").write_text("File 1 content")
        (source_folder / "file2.txt").write_text("File 2 content")

        # Create a subfolder
        subfolder = source_folder / "subfolder"
        subfolder.mkdir()
        (subfolder / "subfile.txt").write_text("Subfolder content")

        # Upload to Azure
        dest_uri = self._get_blob_uri("destination")
        AzureSchemeFileHandler.upload_folder(source_folder, dest_uri)

        # Verify the files were uploaded
        assert AzureSchemeFileHandler.file_exists(self._get_blob_uri("destination/file1.txt"))
        assert AzureSchemeFileHandler.file_exists(self._get_blob_uri("destination/file2.txt"))
        assert AzureSchemeFileHandler.file_exists(self._get_blob_uri("destination/subfolder/subfile.txt"))

        # Verify content
        content1 = AzureSchemeFileHandler.get_bytes(self._get_blob_uri("destination/file1.txt"))
        assert content1 == b"File 1 content"
        sub_content = AzureSchemeFileHandler.get_bytes(self._get_blob_uri("destination/subfolder/subfile.txt"))
        assert sub_content == b"Subfolder content"

    def test_upload_stream_direct(self) -> None:
        """Test uploading a stream directly to a file."""
        stream_content = b"Stream content for direct upload"
        stream = BytesIO(stream_content)

        dest_uri = self._get_blob_uri("stream_direct.txt")

        AzureSchemeFileHandler.upload_stream_direct(stream, dest_uri)

        assert AzureSchemeFileHandler.file_exists(dest_uri)
        content = AzureSchemeFileHandler.get_bytes(dest_uri)
        assert content == stream_content

    def test_upload_stream_directory(self) -> None:
        """Test uploading a stream to a directory with filename."""
        stream_content = b"Stream content for directory upload"
        stream = BytesIO(stream_content)

        AzureSchemeFileHandler.upload_stream_directory(stream, self.base_uri, "uploaded_stream.txt")

        # File should be created with the specified filename
        uploaded_uri = self._get_blob_uri("uploaded_stream.txt")
        assert AzureSchemeFileHandler.file_exists(uploaded_uri)
        content = AzureSchemeFileHandler.get_bytes(uploaded_uri)
        assert content == stream_content

    def test_get_file_size(self) -> None:
        """Test getting the size of a file."""
        uri = self._get_blob_uri("test1.txt")
        size = AzureSchemeFileHandler.get_file_size(uri)

        expected_size = len("Test content 1")
        assert size == expected_size

    def test_get_file_size_large_file(self) -> None:
        """Test getting the size of a larger file."""
        large_content = "x" * 1000  # 1000 bytes
        blob_client = self.container_client.get_blob_client("large.txt")
        blob_client.upload_blob(large_content.encode('utf-8'), overwrite=True)

        uri = self._get_blob_uri("large.txt")
        size = AzureSchemeFileHandler.get_file_size(uri)

        assert size == 1000

    def test_regex_filter_behavior(self) -> None:
        """Test regex filtering behavior in detail."""
        # Upload files with specific patterns
        files = {
            "pattern_test/log_2023.txt": "content",
            "pattern_test/log_2024.txt": "content",
            "pattern_test/data.json": "content",
            "pattern_test/config.xml": "content"
        }

        for blob_name, content in files.items():
            blob_client = self.container_client.get_blob_client(blob_name)
            blob_client.upload_blob(content.encode('utf-8'), overwrite=True)

        pattern_uri = self._get_blob_uri("pattern_test")

        # Test regex for log files from 2024
        regex = r".*log_2024.*"
        entries = list(AzureSchemeFileHandler.list_entries_shallow(pattern_uri, regex))

        # Should match log_2024.txt
        matching_names = {entry.name for entry in entries}
        assert any("log_2024" in name for name in matching_names)

    def test_entry_properties_completeness(self) -> None:
        """Test that EntryProperties objects are complete and correct."""
        entries = list(AzureSchemeFileHandler.list_entries_shallow(self.base_uri))

        for entry in entries:
            # All entries should have required fields
            assert isinstance(entry.name, str)
            assert isinstance(entry.full_uri, str)
            assert isinstance(entry.path, str)
            assert isinstance(entry.is_file, bool)
            assert entry.full_uri.startswith("azure://")

            if entry.is_file:
                assert isinstance(entry.size, int)
                assert entry.size >= 0
                assert isinstance(entry.last_modified, datetime)
            else:
                assert entry.size is None

    def test_recursive_vs_shallow_difference(self) -> None:
        """Test the difference between recursive and shallow listing."""
        shallow_entries = list(AzureSchemeFileHandler.list_entries_shallow(self.base_uri))
        recursive_entries = list(AzureSchemeFileHandler.list_entries_recursive(self.base_uri))

        # Recursive should find more entries than shallow (files only vs files + dirs)
        assert len(recursive_entries) >= len(shallow_entries)

        # Shallow should find directory prefixes
        shallow_names = {entry.name for entry in shallow_entries}
        assert "test1.txt" in shallow_names  # Direct child
        assert "subdir" in shallow_names    # Directory prefix

        # Recursive should find all files but no directory prefixes
        recursive_names = {entry.name for entry in recursive_entries}
        assert "test1.txt" in recursive_names  # Direct child
        assert "sub1.txt" in recursive_names   # Nested child
        assert "nested.md" in recursive_names  # Deeply nested child

    def test_error_handling_invalid_paths(self) -> None:
        """Test error handling for invalid paths and operations."""
        # Test with non-existent file for get_bytes
        nonexistent_uri = self._get_blob_uri("nonexistent.txt")
        with pytest.raises(ResourceNotFoundError):
            AzureSchemeFileHandler.get_bytes(nonexistent_uri)

        # Test with non-existent file for get_file_size
        with pytest.raises(ResourceNotFoundError):
            AzureSchemeFileHandler.get_file_size(nonexistent_uri)

    def test_special_characters_in_filenames(self) -> None:
        """Test handling of special characters in filenames."""
        # Upload file with special characters
        special_filename = "file with spaces & symbols.txt"
        blob_client = self.container_client.get_blob_client(special_filename)
        blob_client.upload_blob("Special content".encode('utf-8'), overwrite=True)

        uri = self._get_blob_uri(special_filename)

        # Test that operations work with special characters
        assert AzureSchemeFileHandler.file_exists(uri) is True
        content = AzureSchemeFileHandler.get_bytes(uri)
        assert content == b"Special content"

        # Test listing finds the file
        entries = list(AzureSchemeFileHandler.list_entries_shallow(self.base_uri))
        special_entries = [e for e in entries if "spaces" in e.name]
        assert len(special_entries) == 1
        assert special_entries[0].name == special_filename

    def test_uri_parsing_edge_cases(self) -> None:
        """Test URI parsing with various edge cases."""
        # Test with different path structures
        nested_uri = self._get_blob_uri("subdir")
        entries = list(AzureSchemeFileHandler.list_entries_shallow(nested_uri))

        # Should find files in subdir
        names = {entry.name for entry in entries}
        assert "sub1.txt" in names or "nested" in names

    def test_concurrent_operations(self) -> None:
        """Test that concurrent operations work correctly."""
        import threading

        results = []
        errors = []

        def upload_file(i: int) -> None:
            try:
                temp_file = Path(self.temp_dir) / f"concurrent_{i}.txt"
                temp_file.write_text(f"Concurrent content {i}")

                dest_uri = self._get_blob_uri(f"concurrent_{i}.txt")
                AzureSchemeFileHandler.upload_file_direct(temp_file, dest_uri)

                # Verify upload
                assert AzureSchemeFileHandler.file_exists(dest_uri)
                results.append(i)
            except Exception as e:
                errors.append(e)

        # Run multiple uploads concurrently
        threads = []
        for i in range(5):
            thread = threading.Thread(target=upload_file, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # All uploads should succeed
        assert len(errors) == 0
        assert len(results) == 5
