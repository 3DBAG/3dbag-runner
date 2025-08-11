import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass

import fiona
from shapely.geometry import Polygon, mapping

from ..io import SchemeFileHandler
from ..io.EntryProperties import EntryProperties
from ..defautlogging import setup_logging

log = setup_logging()


PDOK_DELIVERY_SCHEMA = {
    'geometry': 'Polygon',
    'properties': {
        'bladnr': 'str',
        'bag_peildatum': 'int',
        'download_size_bytes': 'int',
        'download_link': 'str',
        'startdatum': 'datetime',
        'einddatum': 'datetime',
    }
}


@dataclass
class PdokDeliveryProperties:
    """Properties for PDOK delivery features."""
    bladnr: str
    bag_peildatum: int
    download_size_bytes: int
    download_link: str
    startdatum: datetime
    einddatum: datetime


def extract_ahn_key_from_filename(filename: str) -> Optional[str]:
    """
    Extracts AHN tile key from filename.
    Expected pattern: <ahn_key>_<anything>.zip
    Example: 13bn1_something.zip -> 13bn1
    """
    # Remove directory path and extract just the filename
    basename = os.path.basename(filename)

    # Pattern to match AHN filenames
    # Expects: <ahn_key>_<anything>.zip
    match = re.search(r'^([a-zA-Z0-9]+)_.*\.zip$', basename)
    if not match:
        return None

    ahn_key = match.group(1).lower()  # Convert to lowercase to match ahn.json keys
    return ahn_key


def create_pdok_index(source_uri: str, ahn_json_path: Path, destination: Path,
                      download_url_prefix: str,
                      temporary_directory: Optional[Path] = None) -> None:
    """
    Creates a PDOK delivery index geopackage from files in the source directory.

    Args:
        source_uri: URI to source directory containing year folders (e.g., "file:///data" or "azure://...")
        ahn_json_path: Path to ahn.json file containing tile geometries
        output_gpkg_path: Path where the output geopackage will be created
        download_url_prefix: URL prefix for download links
        temporary_directory: Optional temporary directory for file operations
    """
    # Initialize file handler
    file_handler = SchemeFileHandler(temporary_directory)

    # Load AHN geometry data
    with open(ahn_json_path, 'r') as f:
        ahn_data = json.load(f)
        # Convert all keys to lowercase for consistent matching
        ahn_geometries = {key.lower(): value for key, value in ahn_data.items()}

    # Expected folder structure: /<year>/geluid/<type>/
    folder_types = ['gebouwen', 'tin', 'bodemvlakken']

    # Collect features grouped by folder type
    features_by_type: dict[str, list[dict[str, Any]]] = {folder_type: [] for folder_type in folder_types}

    for year_entry in (x for x in file_handler.list_entries_shallow(source_uri) if x.is_directory):
        if not year_entry.name.isdigit() or int(year_entry.name) < 2020:
            continue

        if list(file_handler.list_entries_shallow(year_entry.full_uri, regex="geluid")) == 0:
            log.warning(f"No 'geluid' folder found in {year_entry.full_uri}, skipping year {year_entry.name}")
            continue

        # Check for geluid folder in year directory
        geluid_uri = file_handler.navigate(year_entry.full_uri, "geluid")
        if list(file_handler.list_entries_shallow(geluid_uri)) == 0:
            continue

        layer_entries: list[EntryProperties] = []
        # Check if all expected folder types exist before processing
        for folder in file_handler.list_entries_shallow(geluid_uri):
            if folder.name not in folder_types or not folder.is_directory:
                continue

            layer_entries.append(folder)

        # Only process folder types that actually exist
        for layer in layer_entries:
            try:
                for file_entry in (x for x in file_handler.list_entries_shallow(layer.full_uri, regex=r'.*\.zip$') if x.is_file):
                    year = int(year_entry.name)
                    filename = file_entry.name

                    # Extract AHN key from filename
                    ahn_key = extract_ahn_key_from_filename(filename)
                    if not ahn_key or ahn_key not in ahn_geometries:
                        continue

                    # Get geometry from AHN data
                    bbox = ahn_geometries[ahn_key]  # [minx, miny, maxx, maxy]
                    geometry = Polygon([
                        (bbox[0], bbox[1]),  # bottom-left
                        (bbox[2], bbox[1]),  # bottom-right
                        (bbox[2], bbox[3]),  # top-right
                        (bbox[0], bbox[3]),  # top-left
                        (bbox[0], bbox[1])   # close polygon
                    ])

                    # Create start and end dates for the year
                    start_date = datetime(year, 1, 1)
                    end_date = datetime(year, 12, 31, 23, 59, 59)

                    # Construct download link
                    # Remove leading slash if present to avoid double slashes
                    relative_path = file_entry.path.lstrip('/').replace("geluid/", "")  # Remove "geluid/" prefix, pdok already adds it during the url rewrite
                    download_link = f"{download_url_prefix}{relative_path}"

                    # Create feature for geopackage
                    feature = {
                        'geometry': mapping(geometry),
                        'properties': {
                            'bladnr': ahn_key,
                            'bag_peildatum': year,
                            'download_size_bytes': file_entry.size,
                            'download_link': download_link,
                            'startdatum': start_date,
                            'einddatum': end_date,
                        }
                    }
                    features_by_type[layer.name].append(feature)
            except Exception:
                continue  # Skip if folder doesn't exist or can't be accessed

    # Write features to geopackage, one layer per folder type
    total_features = sum(len(features) for features in features_by_type.values())

    if total_features > 0:
        # Create a temporary file using SchemeFileHandler
        temp_file = file_handler.create_file(suffix=".gpkg")

        try:
            os.unlink(temp_file)  # Ensure the file is removed if it already exists, TODO: we need a cleaner way to create temporary filenames

            # Write each folder type to a separate layer
            for folder_type, features in features_by_type.items():
                if features:  # Only create layer if there are features
                    with fiona.open(temp_file, 'w', driver='GPKG', schema=PDOK_DELIVERY_SCHEMA, crs='EPSG:28992', layer=folder_type) as gpkg:
                        gpkg.writerecords(features)
                    log.info(f"Created layer '{folder_type}' with {len(features)} features")

            # Upload temporary file to destination URI using SchemeFileHandler
            file_handler.upload_file_direct(temp_file, str(destination))

            log.info(f"Created PDOK index with {total_features} total features across {len([t for t, f in features_by_type.items() if f])} layers at {destination}")
        finally:
            # Delete the temporary file using SchemeFileHandler
            file_handler.delete_if_not_local(temp_file)
    else:
        log.info("No valid files found matching the expected structure")
