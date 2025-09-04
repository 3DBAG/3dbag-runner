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
    Expected patterns:
    - <ahn_key>_<anything> (legacy format) - Example: 13bn1_something.laz -> 13bn1
    - <name>_<year>_<x>_<y> (new format) - Example: gebouwen_2021_123_456.laz -> use coordinates to create key
    """
    basename = os.path.basename(filename)

    # Try new format first: <name>_<year>_<x>_<y>
    new_format_match = re.match(r"^(.+)_(\d{4})_(\d+)_(\d+)(?:\.\w+)?$", basename)
    if new_format_match:
        x, y = int(new_format_match.group(3)), int(new_format_match.group(4))
        # Create a synthetic key from coordinates for 2x2km tiles
        return f"{x}_{y}"

    # Try legacy format: <ahn_key>_<anything>
    legacy_match = re.match(r"^(?:.*)([0-9]{2}[a-z]{2}\d)(?:_.*)$", basename)
    if legacy_match:
        return legacy_match.group(1)

    return None


def _extract_coordinates_from_new_format(filename: str) -> Optional[tuple[int, int]]:
    """
    Extracts coordinates from new filename format.
    Expected pattern: <name>_<year>_<x>_<y>
    Example: gebouwen_2021_123_456.laz -> (123, 456)
    """
    basename = os.path.basename(filename)
    regex_match = re.match(r"^(.+)_(\d{4})_(\d+)_(\d+)(?:\.\w+)?$", basename)
    if regex_match:
        x = int(regex_match.group(3))
        y = int(regex_match.group(4))
        return (x, y)
    return None


def _create_geometry_from_coordinates(x: int, y: int, tile_size_km: int = 2) -> Polygon:
    """Create geometry for tile based on coordinates and tile size."""
    tile_size_m = tile_size_km * 1000  # Convert km to meters

    # Convert coordinates to actual coordinates (assuming they're in km units)
    x_m = x * 1000
    y_m = y * 1000

    return Polygon([
        (x_m, y_m),
        (x_m + tile_size_m, y_m),
        (x_m + tile_size_m, y_m + tile_size_m),
        (x_m, y_m + tile_size_m),
        (x_m, y_m)  # close polygon
    ])


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
    # Dynamically discover year directories (directories with numeric names)
    year_directories = []
    for entry in file_handler.list_entries_shallow(source_uri):
        if entry.is_directory and entry.name.isdigit():
            year_directories.append(int(entry.name))

    year_directories.sort()  # Sort years for consistent processing
    log.info(f"Found year directories for DSM processing: {year_directories}")

    features_by_type: Dict[str, List[FeatureWithGeometry]] = {}

    # Process each year directory
    for year in year_directories:
        year_uri = file_handler.navigate(source_uri, str(year))

        try:
            # Check if year directory exists
            year_entries = list(file_handler.list_entries_shallow(year_uri))
            if not year_entries:
                log.warning(f"No entries found in {year_uri}, skipping year {year}")
                continue

            # Dynamically discover DSM layer directories
            dsm_layer_directories = []
            for entry in year_entries:
                if entry.is_directory and entry.name.startswith("dsm"):
                    dsm_layer_directories.append(entry.name)

            log.info(f"Found DSM layer directories for year {year}: {dsm_layer_directories}")

            # Process DSM layers
            for layer_name in dsm_layer_directories:
                try:
                    layer_uri = file_handler.navigate(year_uri, f"{layer_name}/laz")

                    # Initialize feature collection for this layer if not exists
                    layer_key = f"basisbestand_{layer_name}"
                    if layer_key not in features_by_type:
                        features_by_type[layer_key] = []

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
            ahn_geometry = Polygon([
                (bbox[0], bbox[1]),  # bottom-left
                (bbox[2], bbox[1]),  # bottom-right
                (bbox[2], bbox[3]),  # top-right
                (bbox[0], bbox[3]),  # top-left
                (bbox[0], bbox[1])   # close polygon
            ])
            ahn_geometries[key.lower()] = ahn_geometry

    # Dynamically discover year directories (directories with numeric names)
    year_directories = []
    for entry in file_handler.list_entries_shallow(source_uri):
        if entry.is_directory and entry.name.isdigit():
            year_directories.append(int(entry.name))

    year_directories.sort()  # Sort years for consistent processing
    log.info(f"Found year directories: {year_directories}")

    features_by_type: Dict[str, List[FeatureWithGeometry]] = {}

    # Process each year directory
    for year in year_directories:
        year_uri = file_handler.navigate(source_uri, str(year))

        try:
            # Check if year directory exists
            year_entries = list(file_handler.list_entries_shallow(year_uri))
            if not year_entries:
                log.warning(f"No entries found in {year_uri}, skipping year {year}")
                continue

            # Dynamically discover layer directories
            layer_directories = []
            for entry in year_entries:
                if entry.is_directory:
                    layer_directories.append(entry.name)

            log.info(f"Found layer directories for year {year}: {layer_directories}")

            # Process each layer
            for layer_name in layer_directories:
                try:
                    layer_uri = file_handler.navigate(year_uri, layer_name)

                    # Initialize feature collection for this layer if not exists
                    layer_key = f"basisbestand_{layer_name}"
                    if layer_key not in features_by_type:
                        features_by_type[layer_key] = []

                    for file_entry in file_handler.list_entries_shallow(layer_uri):
                        if not file_entry.is_file:
                            continue

                        # Extract AHN key from filename (handles both old and new formats)
                        ahn_key = _extract_ahn_key_from_filename(file_entry.name)
                        if not ahn_key:
                            continue

                        # Determine geometry based on filename format
                        geometry: Optional[Polygon] = None

                        # Check if it's the new coordinate-based format
                        coords = _extract_coordinates_from_new_format(file_entry.name)
                        if coords:
                            # New format: create geometry from coordinates (2x2 km tiles)
                            x, y = coords
                            geometry = _create_geometry_from_coordinates(x, y, tile_size_km=2)
                        else:
                            # Legacy format: use AHN geometries
                            if ahn_key.lower() in ahn_geometries:
                                geometry = ahn_geometries[ahn_key.lower()]

                        if not geometry:
                            log.warning(f"Could not determine geometry for file {file_entry.name}")
                            continue

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
                        features_by_type[layer_key].append(feature)

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
