import os
import re
from typing import Optional
import fiona
from shapely.geometry import mapping, box


def extract_coordinates_from_filename(filename: str) -> Optional[tuple[int, int]]:
    """
    Extracts X and Y coordinates from filename.
    Expects: buildings_2022_<X>_<Y>.city.jsonl
    """
    match = re.search(r'buildings_2022_(\d+)_(\d+)\.city\.jsonl$', filename)
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

    with fiona.open(output_gpkg_path, 'w', driver='GPKG', schema=schema, crs=crs, layer='rectangles') as gpkg:
        for filepath in filenames:
            filename = os.path.basename(filepath)
            coords = extract_coordinates_from_filename(filename)
            if coords:
                x, y = coords
                # Create a 2000x2000 box starting from (x, y)
                rect = box(x, y, x + 2000, y + 2000)
                gpkg.write({
                    'geometry': mapping(rect),
                    'properties': {'filename': filename}
                })
            else:
                print(f"Skipped invalid filename: {filename}")


# Example usage
if __name__ == "__main__":
    filenames = [
        "buildings2022/buildings_2022_13603_366904.city.jsonl",
        "buildings2022/buildings_2022_13604_366905.city.jsonl"
    ]
    create_gpkg_with_rectangles(filenames, "rectangles.gpkg")
