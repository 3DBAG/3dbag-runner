import math
import os
from pathlib import Path
import numpy as np
import laspy
from laspy import LasHeader
from shapely import Polygon


def extent_to_polygon(header: LasHeader) -> Polygon:
    # header.mins and header.maxs are already scaled and offset by laspy
    min_x, min_y, _ = header.mins
    max_x, max_y, _ = header.maxs

    # Create a polygon from the bounding box coordinates
    return Polygon([
        (min_x, min_y),
        (min_x, max_y),
        (max_x, max_y),
        (max_x, min_y),
        (min_x, min_y)
    ])


def laz_tile_split(input_laz: Path, output_dir: Path, grid_size: float) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)

    with laspy.open(input_laz) as pointcloud:
        grid_origin_x = math.floor(pointcloud.header.x_min / grid_size) * grid_size
        grid_origin_y = math.floor(pointcloud.header.y_min / grid_size) * grid_size

        grid_max_x = math.ceil(pointcloud.header.x_max / grid_size) * grid_size
        grid_max_y = math.ceil(pointcloud.header.y_max / grid_size) * grid_size

        min_tile_x, max_tile_x = 0, int((grid_max_x - grid_origin_x) / grid_size)
        min_tile_y, max_tile_y = 0, int((grid_max_y - grid_origin_y) / grid_size)

        generated_tiles = {}

        estimated_point_size = 40 + 10  # point + overhead
        max_memory_bytes = 1 * 1024 * 1024 * 1024  # 1GB
        chunk_size = int(max_memory_bytes / estimated_point_size)

        point_batches = pointcloud.chunk_iterator(chunk_size)
        for point_batch in point_batches:
            tile_x_idx = ((point_batch.x - grid_origin_x) / grid_size).astype(np.int32)  # the type cast from float to int wil cast as math.floor
            tile_y_idx = ((point_batch.y - grid_origin_y) / grid_size).astype(np.int32)

            for tx in range(min_tile_x, max_tile_x):
                for ty in range(min_tile_y, max_tile_y):

                    mask = (tile_x_idx == tx) & (tile_y_idx == ty)
                    if not np.any(mask):
                        continue

                    selected_points = point_batch[mask]
                    start_x = grid_origin_x + tx * grid_size
                    start_y = grid_origin_y + ty * grid_size

                    output_filename = f"{Path(input_laz).stem}_{int(start_x)}_{int(start_y)}.laz"
                    tile_path = os.path.join(output_dir, output_filename)

                    if (tx, ty) not in generated_tiles:
                        # Create and write header for new tile
                        with laspy.open(tile_path, mode="w", header=pointcloud.header) as out_writer:
                            out_writer.write_points(selected_points)
                            generated_tiles[(tx, ty)] = str(tile_path)
                    else:
                        # Append to existing tile
                        with laspy.open(tile_path, mode="a") as out_writer:
                            out_writer.append_points(selected_points)

        return list(generated_tiles.values())
