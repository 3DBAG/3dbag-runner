import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass

from shapely.geometry import Polygon
from roofhelper.defaultlogging import setup_logging
from roofhelper.io import SchemeFileHandler
from roofhelper.pdok.PdokDelivery import createBaseSchema, PdokDeliveryProperties
from roofhelper.pdok.PdokGeopackageWriter import FeatureWithGeometry

log = setup_logging()

PDOK_DELIVERY_SCHEMA_GEBOUW = createBaseSchema({"jaargang_luchtfoto": "int", "bladnr": "str"})


@dataclass
class PdokDeliveryPropertiesBuilding(PdokDeliveryProperties):
    """Properties for PDOK delivery features."""
    jaargang_luchtfoto: int


def _extract_dsm_coordinates_from_filename(filename: str) -> Optional[tuple[int, int]]:
    """
    Extracts DSM coordinates from filename.
    Expected pattern: DSM_<x>_<y>.laz
    Example: DSM_1234_5678.laz -> (1234, 5678)
    """
    basename = os.path.basename(filename)
    regex_match = re.search(r"^(?:.*)DSM_(?P<x>\d{4})_(?P<y>\d{4})\.laz$", basename)
    if regex_match:
        x = int(regex_match.group("x"))
        y = int(regex_match.group("y"))
        return (x, y)
    return None


def _extract_ahn_key_from_filename(filename: str) -> Optional[str]:
    """
    Extracts AHN key from filename.
    Expected pattern: <ahn_key>_<anything>
    Example: 13bn1_something.laz -> 13bn1
    """
    basename = os.path.basename(filename)
    regex_match = re.match(r"^(?:.*)([0-9]{2}[a-z]{2}\d)(?:_.*)$", basename)
    if regex_match:
        return regex_match.group(1)
    return None


def _create_dsm_geometry(x: int, y: int) -> Polygon:
    """Create geometry for DSM tile based on coordinates."""
    dsm_grid_size = 250  # 250 meters

    # Convert to actual coordinates
    x = x * 100
    y = y * 100

    # Apply offset for certain coordinate patterns
    if str(x)[-3] in ('2', '7'):
        x = x + 50
    if str(y)[-3] in ('2', '7'):
        y = y + 50

    return Polygon([
        (x, y),
        (x, y + dsm_grid_size),
        (x + dsm_grid_size, y + dsm_grid_size),
        (x + dsm_grid_size, y)
    ])


def _process_dsm_layers(file_handler: SchemeFileHandler, source_uri: str, download_url_prefix: str) -> Dict[str, List[FeatureWithGeometry]]:
    """
    Process DSM layers and return features grouped by layer type.

    Args:
        file_handler: SchemeFileHandler instance
        source_uri: URI to source directory containing year folders
        download_url_prefix: URL prefix for download links

    Returns:
        Dictionary mapping DSM layer types to lists of features
    """
    years = [2018, 2019, 2020, 2021]
    layers_dsm = ["dsm8", "dsm20"]

    features_by_type: Dict[str, List[FeatureWithGeometry]] = {}

    # Initialize feature collections
    for layer in layers_dsm:
        features_by_type[f"basisbestand_{layer}"] = []

    # Process each year
    for year in years:
        year_uri = file_handler.navigate(source_uri, str(year))

        try:
            # Check if year directory exists
            year_entries = list(file_handler.list_entries_shallow(year_uri))
            if not year_entries:
                log.warning(f"No entries found in {year_uri}, skipping year {year}")
                continue

            # Process DSM layers
            for layer_name in layers_dsm:
                try:
                    layer_uri = file_handler.navigate(year_uri, f"{layer_name}/laz")

                    for file_entry in file_handler.list_entries_shallow(layer_uri, regex=r'.*\.laz$'):
                        if not file_entry.is_file:
                            continue

                        # Extract coordinates from filename
                        coords = _extract_dsm_coordinates_from_filename(file_entry.name)
                        if not coords:
                            continue

                        x, y = coords
                        geometry = _create_dsm_geometry(x, y)

                        # Create start and end dates
                        start_date = datetime(year, 1, 1)
                        end_date = datetime(year, 12, 31, 23, 59, 59)

                        # Create bladnr from filename
                        bladnr = file_entry.name.replace(".laz", "").replace("DSM_", "")

                        # Construct download link
                        relative_path = file_entry.path.lstrip('/')
                        download_link = f"{download_url_prefix}{relative_path}"

                        # Create feature
                        properties = PdokDeliveryPropertiesBuilding(
                            bladnr=bladnr,
                            jaargang_luchtfoto=year,
                            download_size_bytes=file_entry.size or 0,
                            download_link=download_link,
                            startdatum=start_date,
                            einddatum=end_date,
                        )

                        feature = FeatureWithGeometry(
                            geometry=geometry,
                            properties=properties
                        )
                        features_by_type[f"basisbestand_{layer_name}"].append(feature)

                except Exception as e:
                    log.warning(f"Failed to process layer {layer_name} for year {year}: {e}")
                    continue

        except Exception as e:
            log.warning(f"Failed to process year {year}: {e}")
            continue

    return features_by_type


def _process_3d_layers(file_handler: SchemeFileHandler, source_uri: str, ahn_json_path: Path, download_url_prefix: str) -> Dict[str, List[FeatureWithGeometry]]:
    """
    Process 3D layers and return features grouped by layer type.

    Args:
        file_handler: SchemeFileHandler instance
        source_uri: URI to source directory containing year folders
        ahn_json_path: Path to ahn.json file containing tile geometries
        download_url_prefix: URL prefix for download links

    Returns:
        Dictionary mapping 3D layer types to lists of features
    """
    # Load AHN geometry data
    with open(ahn_json_path, 'r') as f:
        ahn_data = json.load(f)
        # Convert all keys to lowercase and create geometry objects
        ahn_geometries: Dict[str, Polygon] = {}
        for key, bbox in ahn_data.items():
            # bbox format: [minx, miny, maxx, maxy]
            geometry = Polygon([
                (bbox[0], bbox[1]),  # bottom-left
                (bbox[2], bbox[1]),  # bottom-right
                (bbox[2], bbox[3]),  # top-right
                (bbox[0], bbox[3]),  # top-left
                (bbox[0], bbox[1])   # close polygon
            ])
            ahn_geometries[key.lower()] = geometry

    years = [2018, 2019, 2020, 2021]
    layers_3d = ["volledig", "hoogtestatistieken", "gebouwen"]

    features_by_type: Dict[str, List[FeatureWithGeometry]] = {}

    # Initialize feature collections
    for layer in layers_3d:
        features_by_type[f"basisbestand_{layer}"] = []

    # Process each year
    for year in years:
        year_uri = file_handler.navigate(source_uri, str(year))

        try:
            # Check if year directory exists
            year_entries = list(file_handler.list_entries_shallow(year_uri))
            if not year_entries:
                log.warning(f"No entries found in {year_uri}, skipping year {year}")
                continue

            # Process 3D layers
            for layer_name in layers_3d:
                try:
                    layer_uri = file_handler.navigate(year_uri, layer_name)

                    for file_entry in file_handler.list_entries_shallow(layer_uri):
                        if not file_entry.is_file:
                            continue

                        # Extract AHN key from filename
                        ahn_key = _extract_ahn_key_from_filename(file_entry.name)
                        if not ahn_key or ahn_key not in ahn_geometries:
                            continue

                        geometry = ahn_geometries[ahn_key]

                        # Create start and end dates
                        start_date = datetime(year, 1, 1)
                        end_date = datetime(year, 12, 31, 23, 59, 59)

                        # Construct download link
                        relative_path = file_entry.path.lstrip('/')
                        download_link = f"{download_url_prefix}{relative_path}"

                        # Create feature
                        properties = PdokDeliveryPropertiesBuilding(
                            bladnr=ahn_key,
                            jaargang_luchtfoto=year,
                            download_size_bytes=file_entry.size or 0,
                            download_link=download_link,
                            startdatum=start_date,
                            einddatum=end_date,
                        )

                        feature = FeatureWithGeometry(
                            geometry=geometry,
                            properties=properties
                        )
                        features_by_type[f"basisbestand_{layer_name}"].append(feature)

                except Exception as e:
                    log.warning(f"Failed to process layer {layer_name} for year {year}: {e}")
                    continue

        except Exception as e:
            log.warning(f"Failed to process year {year}: {e}")
            continue

    return features_by_type


def get_pdok_building_features(source_uri: str, ahn_json_path: Path, download_url_prefix: str) -> Dict[str, List[FeatureWithGeometry]]:
    """
    Retrieves PDOK building delivery features from files in the source directory.

    Args:
        source_uri: URI to source directory containing year folders
        ahn_json_path: Path to ahn.json file containing tile geometries
        download_url_prefix: URL prefix for download links

    Returns:
        Dictionary mapping layer types to lists of features
    """
    # Initialize file handler
    file_handler = SchemeFileHandler()

    # Process DSM layers
    dsm_features = _process_dsm_layers(file_handler, source_uri, download_url_prefix)

    # Process 3D layers
    ahn_3d_features = _process_3d_layers(file_handler, source_uri, ahn_json_path, download_url_prefix)

    # Combine results
    features_by_type = {**dsm_features, **ahn_3d_features}

    return features_by_type
