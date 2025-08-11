import logging
import re
import zipfile

from pathlib import Path
from typing import Optional

log = logging.getLogger()


def unzip(zip: Path, directory: Path, file_to_extract: Optional[str] = None) -> None:
    with zipfile.ZipFile(zip, 'r') as zip_ref:  # Extract all the contents to the specified directory
        if file_to_extract:
            zip_ref.extract(file_to_extract, directory)
        else:
            zip_ref.extractall(directory)


def zip_dir(source: Path, zip_path: Path, file: Optional[str] = None) -> None:
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
        if file:
            file_path = source / file
            if not file_path.exists():
                raise FileNotFoundError(f"{file_path} does not exist.")
            zip_ref.write(file_path, arcname=file)
        else:
            for path in source.rglob('*'):
                if path.is_file():
                    zip_ref.write(path, arcname=path.relative_to(source))


def list_files(zip_path: Path, regex_pattern: str) -> list[str]:
    """
    Lists all the files in a given zip that match regex_pattern,
    will return an empty list if no matches are found
    """
    matching_files = []

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        all_files = zip_ref.namelist()
        pattern = re.compile(regex_pattern)

        for file_name in all_files:
            if pattern.match(file_name):
                matching_files.append(file_name)

    return matching_files
