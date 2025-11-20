
import numpy as np
import geopandas as gpd
import json
import pdal
from pathlib import Path
from shapely.geometry import box, Polygon
from shapely.geometry.base import BaseGeometry
import shapely
import warnings
from typing import Any
from numpy.typing import NDArray

LAZ_CLASSIFICATION_BUILDING = 6
warnings.filterwarnings('ignore')


def get_pointcloud_bounds(laz_path: Path | str) -> Polygon:
    """Get bounding box of the LAZ file."""
    pipeline = {
        "pipeline": [
            {"type": "readers.las", "filename": str(laz_path), "count": 0}
        ]
    }
    pl = pdal.Pipeline(json.dumps(pipeline))
    pl.execute()
    meta = pl.metadata['metadata']['readers.las']
    if isinstance(meta, list):
        meta = meta[0]
    return box(meta['minx'], meta['miny'], meta['maxx'], meta['maxy'])


def get_geometries_intersecting(gpkg_path: Path | str, bounds: Polygon, buffer_distance: float = 0.0) -> list[BaseGeometry]:
    """Load geometries from geopackage that intersect with bounds.

    Args:
        gpkg_path: Path to geopackage file
        bounds: Bounding box to filter geometries
        buffer_distance: Distance in meters to buffer each geometry (default: 0.0)
    """
    try:
        gdf = gpd.read_file(gpkg_path, bbox=bounds)
        if buffer_distance > 0:
            gdf.geometry = gdf.geometry.buffer(buffer_distance)
        return gdf.geometry.tolist()
    except Exception as e:
        print(f"Error reading geometries: {e}")
        return []


def load_pointcloud_with_pdal(laz_path: Path | str) -> NDArray[Any]:
    """Load point cloud with SMRF and HAG Delaunay filters applied.
    SMRF automatically sets Classification to 2 for ground points."""
    smrf_params = {
        "scalar": 0.5,
        "slope": 0.10,
        "threshold": 0.5,
        "window": 16.0
    }
    pipeline = {
        "pipeline": [
            {"type": "readers.las", "filename": str(laz_path)},
            {"type": "filters.smrf", **smrf_params},
            {"type": "filters.hag_delaunay"}
        ]
    }
    pl = pdal.Pipeline(json.dumps(pipeline))
    pl.execute()
    if not pl.arrays:
        raise RuntimeError("PDAL pipeline produced no points.")
    return pl.arrays[0]


def get_points_intersecting_with(pointcloud: NDArray[Any], geometry: BaseGeometry, exclude_ground: bool = False) -> NDArray[np.intp]:
    """Find indices of points intersecting with the geometry.

    Args:
        pointcloud: Point cloud array
        geometry: Shapely geometry to intersect with
        exclude_ground: If True, exclude points classified as ground (class 2)
    """
    # Filter by bounding box first for speed
    minx, miny, maxx, maxy = geometry.bounds
    mask = (pointcloud['X'] >= minx) & (pointcloud['X'] <= maxx) & \
           (pointcloud['Y'] >= miny) & (pointcloud['Y'] <= maxy)

    # Exclude ground points if requested
    if exclude_ground:
        mask = mask & (pointcloud['Classification'] != 2)

    candidate_indices = np.where(mask)[0]
    if len(candidate_indices) == 0:
        return np.array([])

    candidate_x = pointcloud['X'][candidate_indices]
    candidate_y = pointcloud['Y'][candidate_indices]

    # Use geopandas/shapely for precise check
    points = gpd.points_from_xy(candidate_x, candidate_y)

    # Check intersection
    if hasattr(shapely, 'contains') and callable(shapely.contains):
        inside_mask = shapely.contains(geometry, points)
    else:
        s = gpd.GeoSeries(points)
        inside_mask = s.within(geometry).values

    # Convert to numpy array to ensure proper type
    inside_mask_array = np.asarray(inside_mask, dtype=bool)
    return candidate_indices[inside_mask_array]


def set_classification(pointcloud: NDArray[Any], point_indices: NDArray[np.intp], classification: int) -> None:
    """Set classification for specified points."""
    pointcloud['Classification'][point_indices] = classification


def write_pointcloud(pointcloud: NDArray[Any], out_path: Path | str) -> None:
    """Write point cloud to LAZ file."""
    pipeline = {
        "pipeline": [
            {
                "type": "writers.las",
                "filename": str(out_path),
                "minor_version": 4,
                "extra_dims": "all",
                "compression": "laszip"
            }
        ]
    }
    pl = pdal.Pipeline(json.dumps(pipeline), arrays=[pointcloud])
    pl.execute()


def has_points(points: NDArray[np.intp]) -> bool:
    return len(points) > 0


def classify_pointcloud(laz_file: Path | str, gpkg_file: Path | str, output_file: Path | str, buffer_distance: float = 0.2) -> None:
    """Classify points in a LAZ file based on intersecting geometries from a geopackage.

    Args:
        laz_file: Path to input LAZ file
        gpkg_file: Path to geopackage with building geometries
        output_file: Path to output LAZ file
        buffer_distance: Distance in meters to buffer geometries (default: 0.2)
    """
    print(f"Processing {laz_file}...")
    bounds = get_pointcloud_bounds(laz_file)
    print(f"Bounds: {bounds}")

    geometries = get_geometries_intersecting(gpkg_file, bounds, buffer_distance=buffer_distance)
    print(f"Found {len(geometries)} intersecting geometries (with {buffer_distance * 100:.0f}cm buffer).")

    pointcloud = load_pointcloud_with_pdal(laz_file)
    print(f"Loaded {len(pointcloud)} points.")

    for i, geometry in enumerate(geometries):
        point_indices = get_points_intersecting_with(pointcloud, geometry, exclude_ground=True)
        if not has_points(point_indices):
            continue

        set_classification(pointcloud, point_indices, LAZ_CLASSIFICATION_BUILDING)
        if (i + 1) % 100 == 0:
            print(f"Processed {i + 1} geometries...")

    write_pointcloud(pointcloud, output_file)
    print(f"Written result to {output_file}")


if __name__ == "__main__":
    laz_file = "DSM_0242_3782.laz"
    gpkg_file = "bag2021.gpkg"
    output_file = "test.laz"

    classify_pointcloud(laz_file, gpkg_file, output_file)
