import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from shapely.geometry import Polygon

from roofhelper.defaultlogging import setup_logging
from roofhelper.io import EntryProperties, SchemeFileHandler
from roofhelper.pdok.PdokDelivery import createBaseSchema, PdokDeliveryProperties
from roofhelper.pdok.PdokGeopackageWriter import FeatureWithGeometry

log = setup_logging()

PDOK_DELIVERY_SCHEMA_SOUND = createBaseSchema({"bag_peildatum": "int"})


@dataclass
class PdokDeliveryPropertiesSound(PdokDeliveryProperties):
    """Properties for PDOK delivery features."""
    bag_peildatum: int


def _extract_ahn_key_from_filename(filename: str) -> Optional[str]:
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


def get_pdok_sound_features(source_uri: str, ahn_json_path: Path, download_url_prefix: str) -> Dict[str, List[FeatureWithGeometry]]:
    """
    Retrieves PDOK sound delivery features from files in the source directory.

    Args:
        source_uri: URI to source directory containing year folders (e.g., "file:///data" or "azure://...")
        ahn_json_path: Path to ahn.json file containing tile geometries
        download_url_prefix: URL prefix for download links
        temporary_directory: Optional temporary directory for file operations

    Returns:
        Dictionary mapping folder types to lists of features
    """
    # Initialize file handler
    file_handler = SchemeFileHandler()

    # Load AHN geometry data
    with open(ahn_json_path, 'r') as f:
        ahn_data = json.load(f)
        # Convert all keys to lowercase for consistent matching
        ahn_geometries = {key.lower(): value for key, value in ahn_data.items()}

    # Expected folder structure: /<year>/geluid/<type>/
    folder_types = ['gebouwen', 'tin', 'bodemvlakken']

    # Collect features grouped by folder type
    features_by_type: Dict[str, List[FeatureWithGeometry]] = {folder_type: [] for folder_type in folder_types}

    for year_entry in (x for x in file_handler.list_entries_shallow(source_uri) if x.is_directory):
        if not year_entry.name.isdigit() or int(year_entry.name) < 2020:
            continue

        if list(file_handler.list_entries_shallow(year_entry.full_uri, regex="geluid")) == 0:
            log.warning(f"No 'geluid' folder found in {year_entry.full_uri}, skipping year {year_entry.name}")
            continue

        # Check for geluid folder in year directory
        geluid_uri = file_handler.navigate(year_entry.full_uri, "geluid")
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
                    ahn_key = _extract_ahn_key_from_filename(filename)
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
                    properties = PdokDeliveryPropertiesSound(
                        bladnr=ahn_key,
                        bag_peildatum=year,
                        download_size_bytes=file_entry.size or 0,
                        download_link=download_link,
                        startdatum=start_date,
                        einddatum=end_date,
                    )

                    feature = FeatureWithGeometry(
                        geometry=geometry,
                        properties=properties
                    )
                    features_by_type[layer.name].append(feature)
            except Exception:
                continue  # Skip if folder doesn't exist or can't be accessed

    # Return the collected features
    return features_by_type
