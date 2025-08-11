from io import BytesIO, TextIOBase
import logging
from multiprocessing import Queue
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import re
import tempfile
from threading import Thread
from typing import BinaryIO, Generator, Optional
from urllib.parse import urlparse
from azure.storage.blob import BlobClient, ContainerClient, BlobProperties

from .AbstractSchemeFileHandler import AbstractSchemeHandler
from .FileHandle import FileHandle
from .EntryProperties import EntryProperties

log = logging.getLogger()


class AzureSchemeFileHandler(AbstractSchemeHandler):
    @staticmethod
    def _parse_azure_uri(uri: str) -> tuple[str, str, str, str, str, str]:
        """
        Parse an Azure URI and extract components.

        Returns:
            tuple: (scheme, netloc, account_name, container_name, path_prefix, sas_token)
        """
        sas_uri = uri[8:]  # Remove 'azure://' prefix
        parsed_uri = urlparse(sas_uri)

        # Check if this is Azurite (local emulator) or real Azure Storage
        # Azurite format: http://localhost:10000/devstoreaccount1/container/path
        # Real Azure format: https://account.blob.core.windows.net/container/path

        if parsed_uri.netloc.startswith('localhost') or parsed_uri.netloc.startswith('127.0.0.1'):
            # Azurite format - account and container in path
            path_parts = parsed_uri.path.split('/')
            account_name = path_parts[1] if len(path_parts) > 1 else ''
            container_name = path_parts[2] if len(path_parts) > 2 else ''
            path_prefix = "/".join(path_parts[3:]) if len(path_parts) > 3 else ""
        else:
            # Real Azure Storage - account in netloc, container in path
            netloc_parts = parsed_uri.netloc.split('.')
            account_name = netloc_parts[0] if netloc_parts else ''
            path_parts = parsed_uri.path.split('/')
            container_name = path_parts[1] if len(path_parts) > 1 else ''
            path_prefix = "/".join(path_parts[2:]) if len(path_parts) > 2 else ""

        return parsed_uri.scheme, parsed_uri.netloc, account_name, container_name, path_prefix, parsed_uri.query

    @staticmethod
    def _make_container_url(scheme: str, netloc: str, account_name: str, container_name: str, sas_token: str) -> str:
        """
        Create a container URL that works with both Azurite and real Azure Storage.
        """
        if netloc.startswith('localhost') or netloc.startswith('127.0.0.1'):
            # Azurite format
            return f"{scheme}://{netloc}/{account_name}/{container_name}?{sas_token}"
        else:
            # Real Azure Storage format
            return f"{scheme}://{netloc}/{container_name}?{sas_token}"

    @staticmethod
    def _make_blob_url(scheme: str, netloc: str, account_name: str, container_name: str, blob_path: str, sas_token: str) -> str:
        """
        Create a blob URL that works with both Azurite and real Azure Storage.
        """
        if netloc.startswith('localhost') or netloc.startswith('127.0.0.1'):
            # Azurite format
            return f"{scheme}://{netloc}/{account_name}/{container_name}/{blob_path}?{sas_token}"
        else:
            # Real Azure Storage format
            return f"{scheme}://{netloc}/{container_name}/{blob_path}?{sas_token}"

    @staticmethod
    def download_file(uri: str, temporary_directory: Optional[Path], file: Optional[str] = None) -> FileHandle:
        sas_url = uri[8:]

        if file is not None:
            sas_url = AzureSchemeFileHandler.navigate(uri, file)[8:]

        parsed_url = urlparse(sas_url)
        _, extension = os.path.splitext(parsed_url.path)

        os.makedirs(str(temporary_directory), exist_ok=True)
        blob_client = BlobClient.from_blob_url(sas_url)

        with tempfile.NamedTemporaryFile(dir=temporary_directory, delete=False, suffix=extension) as f:
            stream = blob_client.download_blob(max_concurrency=10)
            stream.readinto(f)
            return FileHandle(Path(f.name), True)

    @staticmethod
    def upload_file_directory(file: Path, uri: str, filename: Optional[str]) -> None:
        if filename is not None:
            destination = AzureSchemeFileHandler.navigate(uri, filename)
        else:
            destination = AzureSchemeFileHandler.navigate(uri, file.name)

        AzureSchemeFileHandler.upload_file_direct(file, destination)

    @staticmethod
    def _get_read_buffer(stream: BinaryIO) -> BytesIO:
        # Read & convert text streams into bytes
        if isinstance(stream, TextIOBase):
            text = stream.read()
            data = text.encode('utf-8')  # type: ignore
            return BytesIO(data)
        else:
            # Assume binary; rewind to start
            stream.seek(0)
            return stream  # type: ignore

    @staticmethod
    def upload_stream_direct(stream: BinaryIO, uri: str) -> None:
        blob_client = BlobClient.from_blob_url(uri[8:])

        log.info("Uploading " + uri[8:])
        blob_client.upload_blob(AzureSchemeFileHandler._get_read_buffer(stream), overwrite=True)

    @staticmethod
    def upload_stream_directory(stream: BinaryIO, uri: str, filename: str) -> None:
        uri = AzureSchemeFileHandler.navigate(uri, filename)
        AzureSchemeFileHandler.upload_stream_direct(stream, uri)

    @staticmethod
    def upload_file_direct(file: Path, uri: str) -> None:
        blob_client = BlobClient.from_blob_url(uri[8:])
        log.info("Uploading " + uri[8:])

        with open(file, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)

    @staticmethod
    def _list_files_impl(uri: str, regex: str = '', recursive: bool = False) -> Generator[EntryProperties]:
        """
        Internal implementation for listing files in Azure blob storage.

        Args:
            uri: Azure URI to list files from
            regex: Optional regex pattern to filter files
            recursive: If True, list files recursively; if False, only list files in the current directory
        """
        # Parse the Azure URI components
        scheme, netloc, account_name, container_name, path_prefix, sas_token = AzureSchemeFileHandler._parse_azure_uri(uri)

        # Compile the regex filter if provided
        pattern = re.compile(regex) if regex else None

        # Get the container client using the helper function
        container_url = AzureSchemeFileHandler._make_container_url(scheme, netloc, account_name, container_name, sas_token)
        container_client = ContainerClient.from_container_url(container_url)

        # Walk through the blobs in the container
        # Use delimiter for shallow listing, no delimiter for recursive listing
        if recursive:
            name_starts_with = path_prefix if path_prefix else None
            blob_iter = container_client.list_blobs(name_starts_with=name_starts_with)
        else:
            # For shallow listing, we need to ensure path_prefix ends with "/" if it's not empty
            # to properly list files within a directory
            if path_prefix and not path_prefix.endswith("/"):
                name_starts_with = path_prefix + "/"
            elif path_prefix == "":
                name_starts_with = None
            else:
                name_starts_with = path_prefix

            blob_iter = container_client.walk_blobs(name_starts_with=name_starts_with, delimiter='/')

        for blob in blob_iter:
            if not isinstance(blob, BlobProperties):
                # This is a BlobPrefix (directory) when using walk_blobs with delimiter
                # Create EntryProperties for the directory prefix
                blob_prefix = blob  # This is actually a BlobPrefix object
                prefix_name = blob_prefix.name.rstrip('/')

                # If regex is provided, filter directories based on it
                if pattern and not pattern.match(prefix_name):
                    continue

                # Create directory URL using helper function
                directory_url = AzureSchemeFileHandler._make_blob_url(scheme, netloc, account_name, container_name, f"{prefix_name}/", sas_token)
                directory_entry = EntryProperties(
                    name=os.path.basename(prefix_name),
                    full_uri=f"azure://{directory_url}",
                    path=prefix_name,
                    is_file=False,  # This is a directory
                    size=None,  # Directories don't have size
                    last_modified=None,  # Prefixes don't have modification time
                )
                yield directory_entry
                continue

            # Create the full URL with the SAS token using helper function
            blob_url = AzureSchemeFileHandler._make_blob_url(scheme, netloc, account_name, container_name, blob.name, sas_token)

            # If regex is provided, filter files based on it
            if pattern and not pattern.match(blob.name):
                continue

            # Create EntryProperties with all available information from Azure Blob Storage
            entry = EntryProperties(
                name=os.path.basename(blob.name),
                full_uri=f"azure://{blob_url}",
                path=blob.name,
                is_file=True,  # Azure blob storage only has files, no directories
                size=blob.size,
                last_modified=blob.last_modified,
            )

            yield entry

    @staticmethod
    def list_entries_shallow(uri: str, regex: str = '') -> Generator[EntryProperties]:
        """List files in the current directory (shallow listing)."""
        return AzureSchemeFileHandler._list_files_impl(uri, regex, recursive=False)

    @staticmethod
    def list_entries_recursive(uri: str, regex: str = '') -> Generator[EntryProperties]:
        """List files recursively through all subdirectories."""
        return AzureSchemeFileHandler._list_files_impl(uri, regex, recursive=True)

    @staticmethod  # change to only
    def get_bytes(uri: str) -> bytes:
        blob_client = BlobClient.from_blob_url(uri[8:])

        stream = blob_client.download_blob()
        return stream.readall()

    @staticmethod
    def navigate(uri: str, path: str) -> str:
        # Parse the original URI to get the current path prefix
        scheme, netloc, account_name, container_name, current_path, sas_token = AzureSchemeFileHandler._parse_azure_uri(uri)

        # Combine current path with the new relative path
        if current_path and not current_path.endswith('/'):
            combined_path = f"{current_path}/{path}"
        elif current_path:
            combined_path = f"{current_path}{path}"
        else:
            combined_path = path

        blob_url = AzureSchemeFileHandler._make_blob_url(scheme, netloc, account_name, container_name, combined_path, sas_token)
        return f"azure://{blob_url}"

    @staticmethod
    def file_exists(uri: str) -> bool:
        blob_client = BlobClient.from_blob_url(uri[8:])
        return blob_client.exists()

    @staticmethod
    def get_bytes_range(uri: str, offset: int, length: int) -> bytes:
        blob_client = BlobClient.from_blob_url(blob_url=uri[8:])
        stream = blob_client.download_blob(offset=offset, length=length)
        return stream.readall()

    @staticmethod
    def upload_folder(folder: Path, uri: str, recursive: bool = True, consumer_count: int = multiprocessing.cpu_count(), queue_size: int = 128) -> None:
        folder = folder.expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f"'{folder}' is not a directory")

        # Parse Azure URI components
        scheme, netloc, account_name, container_name, path_prefix, sas_token = AzureSchemeFileHandler._parse_azure_uri(uri)

        task_queue: Queue[tuple[Path, str] | None] = Queue(maxsize=queue_size)

        def _producer() -> None:
            iterator = folder.rglob("*") if recursive else folder.glob("*")
            for local_path in iterator:
                if not local_path.is_file():
                    continue

                rel_path = local_path.relative_to(folder).as_posix()
                task_queue.put((local_path, rel_path))

            for _ in range(consumer_count):  # one sentinel per consumer so they all terminate cleanly
                task_queue.put(None)

        def _consumer() -> None:
            while True:
                try:
                    item = task_queue.get(timeout=0.5)
                except Empty:
                    continue  # allows thread to exit promptly on SIGINT

                if item is None:  # sentinel -> shut down
                    break

                local_path, rel_path = item
                # Construct blob path by combining path_prefix with relative path
                if path_prefix:
                    blob_path = f"{path_prefix}/{rel_path}"
                else:
                    blob_path = rel_path

                # Create blob URL using helper function
                blob_url = AzureSchemeFileHandler._make_blob_url(scheme, netloc, account_name, container_name, blob_path, sas_token)

                blob_client = BlobClient.from_blob_url(blob_url)
                with open(local_path, "rb") as data:
                    log.info(f"Uploading {local_path}")
                    blob_client.upload_blob(data, overwrite=True)

        _consumer_threads: list[Thread] = [Thread(target=_consumer, daemon=True) for i in range(consumer_count)]

        for t in _consumer_threads:
            t.start()

        _producer()

        for t in _consumer_threads:
            t.join()

    @staticmethod
    def get_file_size(uri: str) -> int:
        blob_client = BlobClient.from_blob_url(uri[8:])
        blob_properties = blob_client.get_blob_properties()
        return blob_properties.size
