from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import os


@dataclass
class EntryProperties:
    """
    Properties of a file or directory entry across different storage systems.

    This class provides a unified interface for file/directory properties
    that can be efficiently retrieved from various storage backends like
    Azure Blob Storage, local filesystem, S3, etc.
    """
    name: str  # Basename of the file/directory
    full_uri: str  # Fully qualified URI with scheme (e.g., azure://..., file://...)
    path: str  # Relative path from the base URI
    is_file: bool  # True if it's a file, False if it's a directory
    size: Optional[int] = None  # Size in bytes (None for directories or if not available)
    last_modified: Optional[datetime] = None  # Last modification time

    @property
    def is_directory(self) -> bool:
        """Returns True if this entry is a directory."""
        return not self.is_file

    @property
    def extension(self) -> str:
        """Returns the file extension (without the dot) or empty string if none."""
        if not self.is_file:
            return ""
        _, ext = os.path.splitext(self.name)
        return ext.lstrip('.')

    @property
    def size_mb(self) -> Optional[float]:
        """Returns the size in megabytes, or None if size is not available."""
        if self.size is None:
            return None
        return self.size / (1024 * 1024)

    @property
    def size_gb(self) -> Optional[float]:
        """Returns the size in gigabytes, or None if size is not available."""
        if self.size is None:
            return None
        return self.size / (1024 * 1024 * 1024)

    def has_extension(self, *extensions: str) -> bool:
        """
        Check if the file has one of the specified extensions.

        Args:
            *extensions: File extensions to check (without dots)

        Returns:
            True if the file has one of the specified extensions

        Example:
            entry.has_extension('json', 'txt')  # True if file ends with .json or .txt
        """
        if not self.is_file:
            return False
        return self.extension.lower() in [ext.lower() for ext in extensions]
