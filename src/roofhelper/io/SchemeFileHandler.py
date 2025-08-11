# Currently not https://datatracker.ietf.org/doc/html/rfc3986/ compliant, should improve on it later.
import os
from pathlib import Path
import tempfile
import threading
from typing import BinaryIO, Generator, Optional
from urllib.parse import urlparse

from .AbstractSchemeFileHandler import AbstractSchemeHandler
from .AzureSchemeFileHandler import AzureSchemeFileHandler
from .FileHandle import FileHandle
from .FileSchemeFileHandler import FileSchemeFileHandler
from .EntryProperties import EntryProperties


class SchemeFileHandler:
    def __init__(self, temporary_directory: Optional[Path] = None) -> None:
        self.file_handles: list[FileHandle] = []
        self.scheme_handlers: dict[str, type[AbstractSchemeHandler]] = {"azure": AzureSchemeFileHandler,
                                                                        "file": FileSchemeFileHandler}
        self.temporary_directory = temporary_directory
        self._lock = threading.Lock()

    def download_file(self, uri: str, file: Optional[str] = None) -> Path:
        parsed_uri = urlparse(uri)
        handle: FileHandle = self.scheme_handlers[parsed_uri.scheme].download_file(uri, self.temporary_directory, file)
        self.file_handles.append(handle)
        return handle.path

    def list_entries_shallow(self, uri: str, regex: str = '') -> Generator[EntryProperties]:
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].list_entries_shallow(uri, regex)

    def list_entries_recursive(self, uri: str, regex: str = '') -> Generator[EntryProperties]:
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].list_entries_recursive(uri, regex)

    def upload_file_directory(self, file: Path, uri: str, filename: Optional[str] = None) -> None:
        parsed_uri = urlparse(uri)
        self.scheme_handlers[parsed_uri.scheme].upload_file_directory(file, uri, filename)

    def upload_file_direct(self, file: Path, uri: str) -> None:
        parsed_uri = urlparse(uri)
        self.scheme_handlers[parsed_uri.scheme].upload_file_direct(file, uri)

    def upload_bytes_direct(self, stream: BinaryIO, uri: str) -> None:
        parsed_uri = urlparse(uri)
        self.scheme_handlers[parsed_uri.scheme].upload_stream_direct(stream, uri)

    def upload_bytes_directory(self, stream: BinaryIO, uri: str, filename: str) -> None:
        parsed_uri = urlparse(uri)
        self.scheme_handlers[parsed_uri.scheme].upload_stream_directory(stream, uri, filename)

    def get_bytes(self, uri: str) -> bytes:
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].get_bytes(uri)

    def get_bytes_range(self, uri: str, offset: int, length: int) -> bytes:
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].get_bytes_range(uri, offset, length)

    # Create_text_file does not adhere to solid, move this to a separate class?
    def create_text_file(self, text: str, suffix: Optional[str]) -> Path:
        if self.temporary_directory:
            os.makedirs(self.temporary_directory, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=self.temporary_directory, suffix=suffix, delete=False) as f:
            f.write(text.encode('utf-8'))
            path = Path(f.name)
            self.file_handles.append(FileHandle(path, True))
            return path

    def create_file(self, suffix: Optional[str] = None, text: Optional[str] = None) -> Path:
        """
        Create a temporary file with optional text content.

        Args:
            suffix: Optional file suffix (e.g., '.gpkg', '.txt')
            text: Optional text content to write to the file

        Returns:
            Path to the created temporary file
        """
        if self.temporary_directory:
            os.makedirs(self.temporary_directory, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=self.temporary_directory, suffix=suffix, delete=False) as f:
            if text is not None:
                f.write(text.encode('utf-8'))
            path = Path(f.name)
            self.file_handles.append(FileHandle(path, True))
            return path

    def delete_if_not_local(self, path: Path) -> None:
        """
        Delete temp file is aware if the file was already a pre-existing file on disk or a remote file downloaded to a temporary location,
        This function will only remove the file if it wasn't local
        """
        with self._lock:
            to_remove = [handle for handle in self.file_handles if handle.must_dispose and handle.path == path]
            for handle in to_remove:
                if handle.path.exists():
                    os.unlink(handle.path)
                    self.file_handles.remove(handle)

    def navigate(self, uri: str, path: str) -> str:
        """
        Navigating between specific destinations differs per URI, this function helps with uri navigation.
        Say you're in /home you can set path to "test.txt" and this function will return /home/test.txt
        Just a small reminder that this function does not support parent directory or other special operations.
        """
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].navigate(uri, path)

    def file_exists(self, uri: str) -> bool:
        """
        Checks for the existence of a file at the specified URI.
        The check is performed by the handler corresponding to the URI's scheme.
        """
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].file_exists(uri)

    def upload_folder(self, folder: Path, uri: str) -> None:
        parsed_uri = urlparse(uri)
        self.scheme_handlers[parsed_uri.scheme].upload_folder(folder, uri)

    def get_file_size(self, uri: str) -> int:
        """
        Get the size of a file in bytes at the specified URI.
        The size check is performed by the handler corresponding to the URI's scheme.
        """
        parsed_uri = urlparse(uri)
        return self.scheme_handlers[parsed_uri.scheme].get_file_size(uri)
