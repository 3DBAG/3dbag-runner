#!/usr/bin/env python3
"""Download files referenced in an Atom `files.xml` and upload them to a
given Azure Blob SAS destination in parallel.

Usage: python src/copy_files_to_sas.py --sas-url "https://.../container/subfolder?sv=..."

The script extracts all <link href="..."> attributes from the Atom feed and
downloads each file, then uploads it to the provided SAS destination. If the
SAS URL contains a path (subfolder), the uploaded blobs will be placed under
that path. Filenames are taken from the source URL's last path segment.

This implementation streams downloads to temporary files to avoid high memory
use, and uploads using HTTP PUT with header x-ms-blob-type: BlockBlob so it
works with a container-level SAS or container/subfolder SAS.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
from typing import Iterable, List, Optional, Any
from multiprocessing import cpu_count
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from threading import Lock
try:
    from tqdm import tqdm
except Exception:
    tqdm = None


ATOM_NS = "http://www.w3.org/2005/Atom"


def make_session(retries: int = 3, backoff: float = 0.5, status_forcelist: tuple[int, ...] = (500, 502, 503, 504)) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=retries, backoff_factor=backoff, status_forcelist=status_forcelist, allowed_methods=frozenset(["GET", "PUT", "HEAD"]))
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def parse_files_xml(path: str) -> List[str]:
    """Parse the Atom feed and return a list of href URLs from <link> elements."""
    tree = ET.parse(path)
    root = tree.getroot()
    # link elements are in ATOM_NS
    links = []
    for link in root.findall('.//{' + ATOM_NS + '}link'):
        href = link.get('href')
        if href:
            links.append(href)
    return links


def filename_from_url(url: str) -> str:
    p = urlsplit(url).path
    return os.path.basename(p)


def build_dest_url(sas_url: str, filename: str) -> str:
    """Append filename to the sas_url path and preserve the query (SAS token).

    Example:
      sas_url = https://account.blob.core.windows.net/container/subfolder?sv=...
      filename = file.tif
    Returns: https://account.blob.core.windows.net/container/subfolder/file.tif?sv=...
    """
    parts = urlsplit(sas_url)
    query = parts.query
    base_path = parts.path or ""
    # ensure single slash separation
    new_path = base_path.rstrip('/') + '/' + filename
    # urlunsplit takes (scheme, netloc, path, query, fragment)
    new_parts = (parts.scheme, parts.netloc, new_path, query, '')
    return urlunsplit(new_parts)


# We stream directly from source to Azure Blob; no temp files are used.


def upload_file_to_sas(dest_url: str, stream: Any, content_type: Optional[str] = None, length: Optional[int] = None, overwrite: bool = True) -> None:
    """Upload a readable stream to the destination SAS URL using the Azure SDK.

    dest_url must be the full blob URL including the SAS token. `stream` must
    be a file-like object with a .read() method (for example, requests' resp.raw).
    If `length` is known, passing it may avoid buffering.
    """
    try:
        from azure.storage.blob import BlobClient, ContentSettings
    except Exception:
        raise RuntimeError("azure-storage-blob is not installed in this environment")

    blob = BlobClient.from_blob_url(dest_url)

    content_settings = None
    if content_type:
        content_settings = ContentSettings(content_type=content_type)

    # upload_blob accepts a stream/file-like object. Provide length if available.
    if length is not None:
        blob.upload_blob(stream, overwrite=overwrite, length=length, content_settings=content_settings)
    else:
        blob.upload_blob(stream, overwrite=overwrite, content_settings=content_settings)


def copy_one(session_download: requests.Session, src_url: str, sas_url: str, dry_run: bool, progress: Optional[Any], lock: Lock, known_length: Optional[int]) -> dict[str, object]:
    name = filename_from_url(src_url)
    # main() enforces sas_url exists when not dry-run
    dest_url = build_dest_url(sas_url, name)
    logging.info("Processing %s -> %s", src_url, dest_url or '<no-dest>')
    if dry_run:
        return {"src": src_url, "dest": dest_url, "status": "dry-run"}

    # Stream from source directly into azure sdk upload
    try:
        resp = session_download.get(src_url, stream=True, timeout=60)
        resp.raise_for_status()
        ctype = resp.headers.get('Content-Type')
        length = None
        hdr = resp.headers.get('Content-Length')
        if known_length is not None:
            length = known_length
        elif hdr is not None:
            try:
                length = int(hdr)
            except Exception:
                length = None

        # If we discover length now and progress exists, increase total
        if length and progress is not None:
            try:
                with lock:
                    # add to total if not already counted (we used HEAD to precompute known lengths)
                    progress.total += length
                    progress.refresh()
            except Exception:
                pass

        # resp.raw is a file-like object
        # ensure underlying urllib3 response doesn't decode the stream prematurely
        resp.raw.decode_content = True

        # Wrap resp.raw to update progress on reads
        class ReadWithProgress:
            def __init__(self, raw: Any, progress: Optional[Any], lock: Lock) -> None:
                self.raw = raw
                self.progress = progress
                self.lock = lock

            def read(self, amt: Optional[int] = None) -> Optional[bytes]:
                chunk = self.raw.read(amt)
                if chunk and self.progress is not None:
                    n = len(chunk)
                    try:
                        with self.lock:
                            self.progress.update(n)
                    except Exception:
                        pass
                return chunk

            def readinto(self, b: bytearray) -> int:
                # b is a writable buffer
                mv = memoryview(b)
                data = self.raw.read(len(mv))
                if not data:
                    return 0
                mv[:len(data)] = data
                try:
                    with self.lock:
                        if self.progress is not None:
                            self.progress.update(len(data))
                except Exception:
                    pass
                return len(data)

            def close(self) -> None:
                try:
                    self.raw.close()
                except Exception:
                    pass

        wrapper = ReadWithProgress(resp.raw, progress, lock)

        upload_file_to_sas(dest_url, wrapper, content_type=ctype, length=length, overwrite=True)
        logging.info("Uploaded %s", dest_url)
        return {"src": src_url, "dest": dest_url, "status": "ok"}
    except Exception as exc:
        logging.exception("Failed %s -> %s: %s", src_url, dest_url, exc)
        return {"src": src_url, "dest": dest_url, "status": "error", "error": str(exc)}


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download files listed in an Atom files.xml and upload to an Azure Blob SAS destination.")
    p.add_argument('--files-xml', default='files.xml', help='Path to files.xml (Atom feed)')
    p.add_argument('--sas-url', default=None, help='Azure Blob SAS URL pointing at container or container/subfolder (include SAS token). Required unless --dry-run')
    p.add_argument('--dry-run', action='store_true', help='Only print planned actions without downloading/uploading')
    p.add_argument('--last', action='store_true', help='Process only the last URL from the feed (quick test)')
    args = p.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    if not args.dry_run and not args.sas_url:
        logging.error('sas-url is required unless --dry-run')
        return 2

    try:
        urls = parse_files_xml(args.files_xml)[:]
    except Exception as exc:
        logging.exception('Could not parse files.xml: %s', exc)
        return 2

    logging.info('Found %d links in %s', len(urls), args.files_xml)

    if args.last:
        if not urls:
            logging.error('No links found in %s', args.files_xml)
            return 2
        urls = urls[-1:]

    session_download = make_session(retries=3)

    # Precompute known lengths via HEAD to get total bytes where possible
    total_bytes = 0
    known_lengths = {}
    for u in urls:
        try:
            h = session_download.head(u, timeout=30)
            if h.status_code == 200:
                hdr = h.headers.get('Content-Length')
                if hdr is not None:
                    try:
                        ln = int(hdr)
                        known_lengths[u] = ln
                        total_bytes += ln
                    except Exception:
                        pass
        except Exception:
            # ignore HEAD failures; we'll try to get length from GET later
            pass

    # create progress bar
    progress = None
    lock = Lock()
    if tqdm is not None:
        progress = tqdm(total=total_bytes, unit='B', unit_scale=True, desc='transfer')

    # use CPU count for concurrency
    workers = cpu_count()
    results = []
    sas_url = args.sas_url or ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(copy_one, session_download, url, sas_url, args.dry_run, progress, lock, known_lengths.get(url))
            for url in urls
        ]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results.append(res)

    if progress is not None:
        progress.close()

    # Summarize
    ok = sum(1 for r in results if r.get('status') == 'ok' or r.get('status') == 'dry-run')
    err = [r for r in results if r.get('status') == 'error']
    logging.info('Completed: %d ok, %d error', ok, len(err))
    if err:
        for e in err:
            logging.error('Error: %s -> %s : %s', e.get('src'), e.get('dest'), e.get('error'))
    return 0 if not err else 3


if __name__ == '__main__':
    raise SystemExit(main())
