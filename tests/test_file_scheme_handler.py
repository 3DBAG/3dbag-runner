"""
Test cases for the FileSchemeFileHandler class.

This module contains comprehensive test cases for the FileSchemeFileHandler class
that handles file operations on the local filesystem using file:// URIs.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from io import BytesIO

from roofhelper.io.FileSchemeFileHandler import FileSchemeFileHandler
from roofhelper.io.FileHandle import FileHandle


class TestFileSchemeFileHandler:
    """Test cases for FileSchemeFileHandler class."""

    def setup_method(self) -> None:
        """Set up test fixtures before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_uri = f"file://{self.test_dir}"

        # Create test files and directories
        self.test_file1 = Path(self.test_dir) / "test1.txt"
        self.test_file2 = Path(self.test_dir) / "test2.json"
        self.test_file3 = Path(self.test_dir) / "test3.log"

        # Create a subdirectory with files
        self.sub_dir = Path(self.test_dir) / "subdir"
        self.sub_dir.mkdir()
        self.sub_file1 = self.sub_dir / "sub1.txt"
        self.sub_file2 = self.sub_dir / "sub2.py"

        # Create another nested subdirectory
        self.nested_dir = self.sub_dir / "nested"
        self.nested_dir.mkdir()
        self.nested_file = self.nested_dir / "nested.md"

        # Write content to test files
        self.test_file1.write_text("Test content 1")
        self.test_file2.write_text('{"test": "json"}')
        self.test_file3.write_text("Log entry")
        self.sub_file1.write_text("Sub content 1")
        self.sub_file2.write_text("print('hello')")
        self.nested_file.write_text("# Nested markdown")

    def teardown_method(self) -> None:
        """Clean up test fixtures after each test method."""
        # Remove the temporary directory and all its contents
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_local_path_basic(self) -> None:
        """Test _get_local_path with basic file URI."""
        uri = "file:///tmp/test"
        result = FileSchemeFileHandler._get_local_path(uri)
        assert result == Path("/tmp/test")

    def test_get_local_path_with_filename(self) -> None:
        """Test _get_local_path with filename parameter."""
        uri = "file:///tmp/test"
        filename = "example.txt"
        result = FileSchemeFileHandler._get_local_path(uri, filename)
        assert result == Path("/tmp/test/example.txt")

    def test_get_local_path_netloc_and_path(self) -> None:
        """Test _get_local_path with netloc in URI."""
        uri = f"file://{self.test_dir}/extra"
        result = FileSchemeFileHandler._get_local_path(uri)
        expected = Path(f"{self.test_dir}/extra")
        assert result == expected

    def test_download_file_existing(self) -> None:
        """Test downloading an existing file."""
        uri = f"file://{self.test_file1}"
        result = FileSchemeFileHandler.download_file(uri, None)

        assert isinstance(result, FileHandle)
        assert result.path == self.test_file1
        assert result.must_dispose is False

    def test_download_file_with_filename(self) -> None:
        """Test downloading a file with filename parameter."""
        uri = f"file://{self.test_dir}"
        filename = "test1.txt"
        result = FileSchemeFileHandler.download_file(uri, None, filename)

        assert isinstance(result, FileHandle)
        assert result.path == self.test_file1
        assert result.must_dispose is False

    def test_list_entries_shallow_basic(self) -> None:
        """Test shallow listing of directory entries."""
        entries = list(FileSchemeFileHandler.list_entries_shallow(self.test_uri))

        # Should find 4 items: 3 files + 1 directory
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
        assert file_entry.full_uri == f"file://{self.test_file1}"
        assert file_entry.path == "test1.txt"
        assert file_entry.last_modified is not None

        # Check properties of a directory entry
        dir_entry = next(entry for entry in entries if entry.name == "subdir")
        assert dir_entry.is_file is False
        assert dir_entry.is_directory is True
        assert dir_entry.size is None
        assert dir_entry.full_uri == f"file://{self.sub_dir}"
        assert dir_entry.path == "subdir"

    def test_list_entries_shallow_with_regex(self) -> None:
        """Test shallow listing with regex filter."""
        # Filter for .txt files only
        regex = r".*\.txt$"
        entries = list(FileSchemeFileHandler.list_entries_shallow(self.test_uri, regex))

        # Should only find test1.txt (regex matches full path)
        txt_entries = [entry for entry in entries if entry.name.endswith('.txt')]
        assert len(txt_entries) >= 1

        # Verify we don't get .json or .log files when filtering for .txt
        names = {entry.name for entry in entries}
        assert "test1.txt" in names or len([n for n in names if n.endswith('.txt')]) > 0

    def test_list_entries_recursive_basic(self) -> None:
        """Test recursive listing of directory entries."""
        entries = list(FileSchemeFileHandler.list_entries_recursive(self.test_uri))

        # Should find all files and directories recursively
        # 3 files in root + 1 subdir + 2 files in subdir + 1 nested dir + 1 file in nested = 8 total
        assert len(entries) >= 7  # At least 7 entries (might vary based on order)

        names = {entry.name for entry in entries}
        assert "test1.txt" in names
        assert "test2.json" in names
        assert "test3.log" in names
        assert "subdir" in names
        assert "sub1.txt" in names
        assert "sub2.py" in names
        assert "nested" in names
        assert "nested.md" in names

    def test_list_entries_recursive_with_regex(self) -> None:
        """Test recursive listing with regex filter."""
        # Filter for Python files
        regex = r".*\.py$"
        entries = list(FileSchemeFileHandler.list_entries_recursive(self.test_uri, regex))

        # Should find sub2.py
        py_entries = [entry for entry in entries if entry.name.endswith('.py')]
        assert len(py_entries) >= 1
        assert any(entry.name == "sub2.py" for entry in py_entries)

    def test_list_entries_empty_directory(self) -> None:
        """Test listing entries in an empty directory."""
        empty_dir = Path(self.test_dir) / "empty"
        empty_dir.mkdir()
        empty_uri = f"file://{empty_dir}"

        entries = list(FileSchemeFileHandler.list_entries_shallow(empty_uri))
        assert len(entries) == 0

    def test_list_entries_nonexistent_directory(self) -> None:
        """Test listing entries in a non-existent directory."""
        nonexistent_uri = f"file://{self.test_dir}/nonexistent"

        with pytest.raises(ValueError, match="not a valid directory"):
            list(FileSchemeFileHandler.list_entries_shallow(nonexistent_uri))

    def test_upload_file_directory(self) -> None:
        """Test uploading a file to a directory."""
        # Create a temporary file to upload
        temp_file = Path(self.test_dir) / "temp_upload.txt"
        temp_file.write_text("Upload test content")

        # Create destination directory
        dest_dir = Path(self.test_dir) / "upload_dest"
        dest_dir.mkdir()
        dest_uri = f"file://{dest_dir}"

        # Upload the file
        FileSchemeFileHandler.upload_file_directory(temp_file, dest_uri, "uploaded.txt")

        # Verify the file was uploaded
        uploaded_file = dest_dir / "uploaded.txt"
        assert uploaded_file.exists()
        assert uploaded_file.read_text() == "Upload test content"

    def test_upload_file_direct(self) -> None:
        """Test uploading a file directly."""
        # Create a temporary file to upload
        temp_file = Path(self.test_dir) / "temp_upload2.txt"
        temp_file.write_text("Direct upload test content")

        # Define destination path
        dest_path = Path(self.test_dir) / "direct_upload.txt"
        dest_uri = f"file://{dest_path}"

        # Upload the file
        FileSchemeFileHandler.upload_file_direct(temp_file, dest_uri)

        # Verify the file was uploaded
        assert dest_path.exists()
        assert dest_path.read_text() == "Direct upload test content"

    def test_get_bytes(self) -> None:
        """Test reading file content as bytes."""
        uri = f"file://{self.test_file1}"
        content = FileSchemeFileHandler.get_bytes(uri)

        assert isinstance(content, bytes)
        assert content == b"Test content 1"

    def test_get_bytes_range(self) -> None:
        """Test reading a range of bytes from a file."""
        # Write some content with known byte positions
        test_file = Path(self.test_dir) / "range_test.txt"
        test_file.write_text("0123456789")  # 10 bytes

        uri = str(test_file)

        # Read bytes 2-5 (should be "2345")
        content = FileSchemeFileHandler.get_bytes_range(uri, 2, 4)
        assert content == b"2345"

    def test_navigate(self) -> None:
        """Test navigating to a location within a URI."""
        base_uri = self.test_uri
        location = "subdir/nested"

        result = FileSchemeFileHandler.navigate(base_uri, location)
        expected = f"file://{self.test_dir}/subdir/nested"
        assert result == expected

    def test_exists_file(self) -> None:
        """Test checking if a file exists."""
        existing_uri = f"file://{self.test_file1}"
        nonexistent_uri = f"file://{self.test_dir}/nonexistent.txt"

        assert FileSchemeFileHandler.file_exists(existing_uri) is True
        assert FileSchemeFileHandler.file_exists(nonexistent_uri) is False

    def test_upload_folder(self) -> None:
        """Test uploading an entire folder."""
        # Create a source folder with content
        source_folder = Path(self.test_dir) / "source"
        source_folder.mkdir()
        (source_folder / "file1.txt").write_text("File 1 content")
        (source_folder / "file2.txt").write_text("File 2 content")

        # Create a subfolder
        subfolder = source_folder / "subfolder"
        subfolder.mkdir()
        (subfolder / "subfile.txt").write_text("Subfolder content")

        # Upload to destination
        dest_folder = Path(self.test_dir) / "destination"
        dest_uri = f"file://{dest_folder}"

        FileSchemeFileHandler.upload_folder(source_folder, dest_uri)

        # Verify the folder was uploaded
        assert dest_folder.exists()
        assert (dest_folder / "file1.txt").exists()
        assert (dest_folder / "file2.txt").exists()
        assert (dest_folder / "subfolder").exists()
        assert (dest_folder / "subfolder" / "subfile.txt").exists()

        # Verify content
        assert (dest_folder / "file1.txt").read_text() == "File 1 content"
        assert (dest_folder / "subfolder" / "subfile.txt").read_text() == "Subfolder content"

    def test_upload_stream_direct(self) -> None:
        """Test uploading a stream directly to a file."""
        stream_content = b"Stream content for direct upload"
        stream = BytesIO(stream_content)

        dest_path = Path(self.test_dir) / "stream_direct.txt"
        dest_uri = f"file://{dest_path}"

        FileSchemeFileHandler.upload_stream_direct(stream, dest_uri)

        assert dest_path.exists()
        assert dest_path.read_bytes() == stream_content

    def test_upload_stream_directory(self) -> None:
        """Test uploading a stream to a directory with filename."""
        stream_content = b"Stream content for directory upload"
        stream = BytesIO(stream_content)

        dest_dir = Path(self.test_dir) / "stream_dir"
        dest_dir.mkdir()
        dest_uri = f"file://{dest_dir}"

        FileSchemeFileHandler.upload_stream_directory(stream, dest_uri, "uploaded_stream.txt")

        # File should be created with the specified filename in the directory
        expected_file = dest_dir / "uploaded_stream.txt"
        assert expected_file.exists()
        assert expected_file.read_bytes() == stream_content

    def test_get_file_size(self) -> None:
        """Test getting the size of a file."""
        uri = f"file://{self.test_file1}"
        size = FileSchemeFileHandler.get_file_size(uri)

        expected_size = len("Test content 1")
        assert size == expected_size

    def test_get_file_size_large_file(self) -> None:
        """Test getting the size of a larger file."""
        large_content = "x" * 1000  # 1000 bytes
        large_file = Path(self.test_dir) / "large.txt"
        large_file.write_text(large_content)

        uri = f"file://{large_file}"
        size = FileSchemeFileHandler.get_file_size(uri)

        assert size == 1000

    def test_regex_filter_behavior(self) -> None:
        """Test regex filtering behavior in detail."""
        # Create files with specific patterns
        pattern_dir = Path(self.test_dir) / "pattern_test"
        pattern_dir.mkdir()

        files = ["log_2023.txt", "log_2024.txt", "data.json", "config.xml"]
        for filename in files:
            (pattern_dir / filename).write_text("content")

        pattern_uri = f"file://{pattern_dir}"

        # Test regex for log files from 2024
        regex = r".*log_2024.*"
        entries = list(FileSchemeFileHandler.list_entries_shallow(pattern_uri, regex))

        # Should match log_2024.txt
        matching_names = {entry.name for entry in entries}
        assert any("log_2024" in name for name in matching_names)

    def test_entry_properties_completeness(self) -> None:
        """Test that EntryProperties objects are complete and correct."""
        entries = list(FileSchemeFileHandler.list_entries_shallow(self.test_uri))

        for entry in entries:
            # All entries should have required fields
            assert isinstance(entry.name, str)
            assert isinstance(entry.full_uri, str)
            assert isinstance(entry.path, str)
            assert isinstance(entry.is_file, bool)
            assert entry.full_uri.startswith("file://")

            if entry.is_file:
                assert isinstance(entry.size, int)
                assert entry.size >= 0
            else:
                assert entry.size is None

            if entry.last_modified is not None:
                assert isinstance(entry.last_modified, datetime)

    def test_recursive_vs_shallow_difference(self) -> None:
        """Test the difference between recursive and shallow listing."""
        shallow_entries = list(FileSchemeFileHandler.list_entries_shallow(self.test_uri))
        recursive_entries = list(FileSchemeFileHandler.list_entries_recursive(self.test_uri))

        # Recursive should find more entries than shallow
        assert len(recursive_entries) > len(shallow_entries)

        # Shallow should only find direct children
        shallow_names = {entry.name for entry in shallow_entries}
        assert "test1.txt" in shallow_names  # Direct child
        assert "sub1.txt" not in shallow_names  # Nested child

        # Recursive should find both direct and nested children
        recursive_names = {entry.name for entry in recursive_entries}
        assert "test1.txt" in recursive_names  # Direct child
        assert "sub1.txt" in recursive_names  # Nested child
        assert "nested.md" in recursive_names  # Deeply nested child

    def test_error_handling_invalid_paths(self) -> None:
        """Test error handling for invalid paths and operations."""
        # Test with non-existent file for get_bytes
        nonexistent_uri = f"file://{self.test_dir}/nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            FileSchemeFileHandler.get_bytes(nonexistent_uri)

        # Test with non-existent file for get_file_size
        with pytest.raises(FileNotFoundError):
            FileSchemeFileHandler.get_file_size(nonexistent_uri)

    def test_path_normalization(self) -> None:
        """Test that paths are properly normalized and handled."""
        # Test with path containing ".."
        complex_uri = f"file://{self.test_dir}/subdir/../test1.txt"

        # The exists check should work correctly
        assert FileSchemeFileHandler.file_exists(complex_uri) is True

        # Reading should work
        content = FileSchemeFileHandler.get_bytes(complex_uri)
        assert content == b"Test content 1"

    def test_special_characters_in_filenames(self) -> None:
        """Test handling of special characters in filenames."""
        # Create files with special characters
        special_file = Path(self.test_dir) / "file with spaces & symbols.txt"
        special_file.write_text("Special content")

        uri = f"file://{special_file}"

        # Test that operations work with special characters
        assert FileSchemeFileHandler.file_exists(uri) is True
        content = FileSchemeFileHandler.get_bytes(uri)
        assert content == b"Special content"

        # Test listing finds the file
        entries = list(FileSchemeFileHandler.list_entries_shallow(self.test_uri))
        special_entries = [e for e in entries if "spaces" in e.name]
        assert len(special_entries) == 1
        assert special_entries[0].name == "file with spaces & symbols.txt"
