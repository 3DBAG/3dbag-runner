import os
import re
from typing import Optional
import fiona
from shapely.geometry import mapping, box

from roofhelper.io import SchemeFileHandler


def extract_coordinates_from_filename(filename: str) -> Optional[tuple[int, int]]:
    match = re.search(r'buildings_2023_(\d+)_(\d+)\.city\.json$', filename)
    if not match:
        return None
    x, y = int(match.group(1)), int(match.group(2))
    return x, y


def create_gpkg_with_rectangles(filenames: list[str], output_gpkg_path: str) -> None:
    schema = {
        'geometry': 'Polygon',
        'properties': {'filename': 'str'}
    }

    crs = 'EPSG:28992'  # Amersfoort / RD New

    features = []

    for filepath in filenames:
        filename = os.path.basename(filepath)
        coords = extract_coordinates_from_filename(filename)
        if coords:
            x, y = coords
            rect = box(x, y, x + 2000, y + 2000)
            features.append({
                'geometry': mapping(rect),
                'properties': {'filename': filename}
            })
        else:
            print(f"Skipped invalid filename: {filename}")

    # Bulk write all features at once
    with fiona.open(output_gpkg_path, 'w', driver='GPKG', schema=schema, crs=crs, layer='rectangles') as gpkg:
        gpkg.writerecords(features)


# Example usage
handler = SchemeFileHandler()
sas_token = "azure://<sas_token>"
names = [entry.name for entry in handler.list_entries_shallow(sas_token) if entry.is_file]
create_gpkg_with_rectangles(names, "2023fix.gpkg")

exit()
