import multiprocessing
import os
from pathlib import Path
import re
import shutil
from typing import BinaryIO, Generator, Optional
from urllib.parse import urlparse
from datetime import datetime

from .AbstractSchemeFileHandler import AbstractSchemeHandler
from .FileHandle import FileHandle
from .EntryProperties import EntryProperties


class FileSchemeFileHandler(AbstractSchemeHandler):
    @staticmethod
    def _get_local_path(uri: str, filename: Optional[str] = None) -> Path:
        parsed_uri = urlparse(uri)
        if filename is not None:
            return Path(os.path.join(parsed_uri.netloc + parsed_uri.path, filename))
        elif parsed_uri.path != "":
            return Path(os.path.join(parsed_uri.netloc + parsed_uri.path))
        else:
            return Path(parsed_uri.netloc)

    @staticmethod
    def download_file(uri: str, temporary_directory: Optional[Path], file: Optional[str] = None) -> FileHandle:
        return FileHandle(FileSchemeFileHandler._get_local_path(uri, file), False)

    @staticmethod
    def _list_files_impl(uri: str, regex: Optional[str] = None, recursive: bool = False) -> Generator[EntryProperties, None, None]:
        """
        Internal implementation for listing files in local filesystem.

        Args:
            uri: File URI to list files from
            regex: Optional regex pattern to filter files
            recursive: If True, list files recursively; if False, only list files in the current directory
        """
        path = FileSchemeFileHandler._get_local_path(uri)
        if not os.path.isdir(path):
            raise ValueError(f"The provided uri '{uri}' is not a valid directory.")

        if recursive:
            for root, dirs, files in os.walk(path):
                # Yield directories first
                for dir_name in dirs:
                    full_path = os.path.join(root, dir_name)
                    relative_path = os.path.relpath(full_path, path)
                    stat_info = os.stat(full_path)

                    if regex is not None:
                        if not re.match(regex, full_path):
                            continue

                    entry = EntryProperties(
                        name=dir_name,
                        full_uri="file://" + full_path,
                        path=relative_path,
                        is_file=False,
                        size=None,  # Directories don't have a meaningful size
                        last_modified=datetime.fromtimestamp(stat_info.st_mtime),
                    )
                    yield entry

                # Then yield files
                for file in files:
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, path)
                    stat_info = os.stat(full_path)

                    if regex is not None:
                        if not re.match(regex, full_path):
                            continue

                    entry = EntryProperties(
                        name=file,
                        full_uri="file://" + full_path,
                        path=relative_path,
                        is_file=True,
                        size=stat_info.st_size,
                        last_modified=datetime.fromtimestamp(stat_info.st_mtime),
                    )
                    yield entry
        else:
            for entry_name in os.listdir(path):
                full_path = os.path.join(path, entry_name)
                stat_info = os.stat(full_path)
                is_file = os.path.isfile(full_path)

                if regex is not None:
                    if not re.match(regex, full_path):
                        continue

                entry_props = EntryProperties(
                    name=entry_name,
                    full_uri="file://" + full_path,
                    path=entry_name,
                    is_file=is_file,
                    size=stat_info.st_size if is_file else None,
                    last_modified=datetime.fromtimestamp(stat_info.st_mtime),
                )
                yield entry_props

    @staticmethod
    def list_entries_shallow(uri: str, regex: Optional[str] = None) -> Generator[EntryProperties]:
        """List files in the current directory (shallow listing)."""
        return FileSchemeFileHandler._list_files_impl(uri, regex, recursive=False)

    @staticmethod
    def list_entries_recursive(uri: str, regex: Optional[str] = None) -> Generator[EntryProperties]:
        """List files recursively through all subdirectories."""
        return FileSchemeFileHandler._list_files_impl(uri, regex, recursive=True)

    @staticmethod
    def upload_file_directory(file: Path, uri: str, filename: Optional[str]) -> None:
        destination = FileSchemeFileHandler._get_local_path(uri, filename)
        os.makedirs(destination.parent, exist_ok=True)
        shutil.copy(file, destination)

    @staticmethod
    def upload_file_direct(file: Path, uri: str) -> None:
        destination = FileSchemeFileHandler._get_local_path(uri)
        shutil.copy(file, destination)

    @staticmethod
    def get_bytes(uri: str) -> bytes:
        source = FileSchemeFileHandler._get_local_path(uri)
        with open(source, "rb") as f:
            return f.read()

    @staticmethod
    def get_bytes_range(uri: str, offset: int, length: int) -> bytes:
        with open(uri, 'rb') as f:
            f.seek(offset)
            return f.read(length)

    @staticmethod
    def navigate(uri: str, path: str) -> str:
        # Get the current base path from the URI
        current_path = FileSchemeFileHandler._get_local_path(uri)

        # Handle empty path case - return base path without trailing slash
        if not path:
            return "file://" + str(current_path)

        # Strip leading slash to ensure relative path behavior
        if path.startswith('/'):
            path = path.lstrip('/')

        # Join with the relative path
        new_path = os.path.join(current_path, path)
        return "file://" + str(new_path)

    @staticmethod
    def file_exists(uri: str) -> bool:
        path = FileSchemeFileHandler._get_local_path(uri)
        return os.path.isfile(path)

    @staticmethod
    def upload_folder(folder: Path, uri: str, recursive: bool = True, consumer_count: int = multiprocessing.cpu_count(), queue_size: int = 128) -> None:
        destination = FileSchemeFileHandler._get_local_path(uri)
        shutil.copytree(folder, destination, dirs_exist_ok=True)

    @staticmethod
    def upload_stream_direct(stream: BinaryIO, uri: str) -> None:
        dest_path = FileSchemeFileHandler._get_local_path(uri)
        with open(dest_path, "wb") as out_f:
            shutil.copyfileobj(stream, out_f)

    @staticmethod
    def upload_stream_directory(stream: BinaryIO, uri: str, filename: str) -> None:
        dest_path = FileSchemeFileHandler._get_local_path(uri, filename)
        os.makedirs(dest_path.parent, exist_ok=True)
        with open(dest_path, "wb") as out_f:
            shutil.copyfileobj(stream, out_f)

    @staticmethod
    def get_file_size(uri: str) -> int:
        source = FileSchemeFileHandler._get_local_path(uri)
        return os.path.getsize(source)
