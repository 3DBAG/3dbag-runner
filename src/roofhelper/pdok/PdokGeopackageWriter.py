import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List

import fiona
from shapely.geometry import Polygon, mapping

from roofhelper.defaultlogging import setup_logging
from roofhelper.io import SchemeFileHandler
from roofhelper.pdok.PdokDelivery import PdokDeliveryProperties

log = setup_logging()


@dataclass
class FeatureWithGeometry:
    """Container for a feature with geometry and properties."""
    geometry: Polygon
    properties: PdokDeliveryProperties


def write_features_to_geopackage(
    schema: dict[str, Any],
    features_by_layer: Dict[str, List[FeatureWithGeometry]],
    destination: str,
    temporary_directory: Path
) -> None:
    """
    Write features organized by layer to a geopackage file.

    Args:
        features_by_layer: Dictionary mapping layer names to lists of FeatureWithGeometry objects
        destination: Destination URI for the geopackage file
        file_handler: SchemeFileHandler instance for file operations
        schema: Fiona schema for the geopackage
    """
    total_features = sum(len(features) for features in features_by_layer.values())

    if total_features == 0:
        log.info("No valid features to write")
        return

    file_handler = SchemeFileHandler(temporary_directory)
    # Create a temporary file using SchemeFileHandler
    temp_file = file_handler.create_file(suffix=".gpkg")

    try:
        os.unlink(temp_file)  # Ensure the file is removed if it already exists, TODO: we need a cleaner way to create temporary filenames

        # Write each layer
        for layer_name, features in features_by_layer.items():
            if features:  # Only create layer if there are features
                fiona_features = []
                for feature in features:
                    # Convert dataclass to dictionary
                    properties_dict = asdict(feature.properties)
                    fiona_feature = {
                        'geometry': mapping(feature.geometry),
                        'properties': properties_dict
                    }
                    fiona_features.append(fiona_feature)

                with fiona.open(temp_file, 'w', driver='GPKG', schema=schema, crs='EPSG:28992', layer=layer_name) as gpkg:
                    gpkg.writerecords(fiona_features)
                log.info(f"Created layer '{layer_name}' with {len(features)} features")

        # Upload temporary file to destination URI using SchemeFileHandler
        file_handler.upload_file_direct(temp_file, destination)

        log.info(f"Created PDOK index with {total_features} total features across {len([t for t, f in features_by_layer.items() if f])} layers at {destination}")
    finally:
        # Delete the temporary file using SchemeFileHandler
        file_handler.delete_if_not_local(temp_file)
