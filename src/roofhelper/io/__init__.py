import os
from pathlib import Path

import urllib.request

from .SchemeFileHandler import SchemeFileHandler

__all__ = ['SchemeFileHandler']


def download_if_not_exists(url: str, file: Path) -> None:
    if not os.path.exists(file):
        urllib.request.urlretrieve(url, file)
