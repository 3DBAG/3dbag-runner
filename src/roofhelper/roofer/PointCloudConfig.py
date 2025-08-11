from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PointcloudConfig:
    """Configuration for a single pointcloud source."""
    # name of the pointcloud
    name: str

    # source can be a list of files and/or directories with pointcloud files.
    # If a directory is given roofer recursively looks for files
    # with the extensions `.laz`, `.las`, `.LAZ`, and `.LAS`.
    source: List[str]

    # Year of point cloud collection
    date: Optional[int] = None

    # Priority of the point cloud
    quality: Optional[int] = None

    # Only select this source for a certain date
    select_only_for_date: Optional[bool] = None

    # force lod11 if this is the pointcloud source.
    force_lod11: Optional[bool] = None
