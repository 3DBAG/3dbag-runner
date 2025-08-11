from abc import ABC, abstractmethod
import multiprocessing
from pathlib import Path
from typing import BinaryIO, Generator, Optional

from roofhelper.io.FileHandle import FileHandle
from roofhelper.io.EntryProperties import EntryProperties


class AbstractSchemeHandler(ABC):
    @staticmethod
    @abstractmethod
    def download_file(uri: str, temporary_directory: Optional[Path], file: Optional[str] = None) -> FileHandle:
        pass

    @staticmethod
    @abstractmethod
    def upload_file_directory(file: Path, uri: str, filename: Optional[str]) -> None:
        pass

    @staticmethod
    @abstractmethod
    def upload_file_direct(file: Path, uri: str) -> None:
        pass

    @staticmethod
    @abstractmethod
    def list_entries_shallow(uri: str, regex: str = '') -> Generator[EntryProperties]:
        pass

    @staticmethod
    @abstractmethod
    def list_entries_recursive(uri: str, regex: str = '') -> Generator[EntryProperties]:
        pass

    @staticmethod
    @abstractmethod
    def navigate(uri: str, path: str) -> str:
        pass

    @staticmethod
    @abstractmethod
    def file_exists(uri: str) -> bool:
        pass

    @staticmethod
    @abstractmethod
    def get_bytes(uri: str) -> bytes:
        pass

    @staticmethod
    @abstractmethod
    def get_bytes_range(uri: str, offset: int, length: int) -> bytes:
        pass

    @staticmethod
    @abstractmethod
    def upload_folder(folder: Path, uri: str, recursive: bool = True, consumer_count: int = multiprocessing.cpu_count(), queue_size: int = 128) -> None:
        pass

    @staticmethod
    @abstractmethod
    def upload_stream_direct(stream: BinaryIO, uri: str) -> None:
        pass

    @staticmethod
    @abstractmethod
    def upload_stream_directory(stream: BinaryIO, uri: str, filename: str) -> None:
        pass

    @staticmethod
    @abstractmethod
    def get_file_size(uri: str) -> int:
        pass
