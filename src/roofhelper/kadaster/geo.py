import math
import multiprocessing
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import fiona
import geopandas as gpd
from shapely.geometry import Polygon, box


def _process_cell(args: Tuple[int, int, int, Path]) -> Optional[Tuple[float, float, float, float]]:
    x, y, grid_size, filepath = args
    cell: Polygon = box(x, y, x + grid_size, y + grid_size)
    footprints: gpd.GeoDataFrame = gpd.read_file(filepath, bbox=cell, layer=0)
    footprints_within: gpd.GeoDataFrame = footprints[footprints.centroid.within(cell)]
    if len(footprints_within) > 0:
        return float(x), float(y), float(x + grid_size), float(y + grid_size)
    return None


def _generate_cells(
    minx: float,
    maxx: float,
    miny: float,
    maxy: float,
    grid_size: int,
    filepath: Path
) -> Generator[Tuple[float, float, float, float], None, None]:
    tasks: List[Tuple[int, int, int, Path]] = [
        (x, y, grid_size, filepath)
        for x in range(int(minx), int(maxx), grid_size)
        for y in range(int(miny), int(maxy), grid_size)
    ]

    with multiprocessing.Pool() as pool:
        results: List[Optional[Tuple[float, float, float, float]]] = pool.map(_process_cell, tasks)

    for result in results:
        if result is not None:
            yield result


def grid_create_on_intersecting_centroid(filepath: Path, grid_size: int) -> Generator[tuple[float, float, float, float]]:
    bounds = None
    with fiona.open(filepath, 'r') as src:
        bounds = src.bounds
    minx, miny, maxx, maxy = bounds

    minx = math.floor(minx / grid_size) * grid_size
    miny = math.floor(miny / grid_size) * grid_size

    maxx = math.ceil(maxx / grid_size) * grid_size
    maxy = math.ceil(maxy / grid_size) * grid_size

    for cell in _generate_cells(minx, maxx, miny, maxy, grid_size, filepath):
        yield cell
