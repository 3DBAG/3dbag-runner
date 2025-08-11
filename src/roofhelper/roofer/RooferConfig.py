from dataclasses import dataclass, field
from typing import List, Optional

from .PointCloudConfig import PointcloudConfig
from .OutputAttributesConfig import OutputAttributesConfig


@dataclass
class RooferConfig:
    """Main configuration dataclass for the roofer process."""

    # --- Input Sources ---
    # Vector source with polygon roofprints. Any OGR supported format is supported.
    polygon_source: str
    # Source column of build year aka year_of_construction_attribute
    yoc_attribute: str
    # Output directory for results
    output_directory: str
    # List of pointcloud configurations
    pointclouds: List[PointcloudConfig]  # Corresponds to [[pointclouds]]

    # Load this layer from <polygon-source> [default: first layer assumed if None]
    polygon_source_layer: Optional[str] = None  # TOML Example: "SomeLayer"
    # Building ID attribute
    id_attribute: str = "id_attribute"
    # Boolean building attribute for forcing LoD 1.1 (simple roofprint extrustion)
    # and skipping reconstruction in a higher LoD for this building.
    force_lod11_attribute: Optional[str] = None  # TOML Example: "SomeAttribute"
    # Specify WHERE clause in OGR SQL to select specfic features from <polygon-source>
    # filter: Optional[str] = None # TOML Example: "id_attribute='SomeID'"

    # Area in square cm? When is this triggered
    lod11_fallback_area: int = 30000

    # Limit the amount of time you can spend on a single building to 60 seconds top
    lod11_fallback_time: int = 60000

    # --- General Processing ---
    # Override SRS for both inputs and outputs
    srs: Optional[str] = "EPSG:7415"

    # LAS classification code that contains the building points.
    bld_class: Optional[int] = 6

    # LAS classification code that contains the ground points.
    grnd_class: Optional[int] = 2

    # Region of interest. Data outside of this region will be ignored.
    # Format: [x_min, y_min, x_max, y_max].
    box: Optional[List[float]] = None  # TOML Example: [0.0, 0.0, 1000.0, 1000.0] needs float
    # Enfore this point density ceiling on each building pointcloud.
    # ceil_point_density: Optional[float] = None # TOML Example: 20
    # Tilesize used for output tiles. Format: [size_x, size_y].
    # tilesize: List[int] = field(default_factory=lambda: [1000, 1000]) # Default based on example
    # Cellsize used for quick pointcloud analysis
    # cellsize: float = 0.5 # Default based on example

    # --- Reconstruction options ---
    # Plane detect epsilon
    # plane_detect_epsilon: float = 0.3
    # # Plane detect k
    # plane_detect_k: int = 15
    # # Plane detect min points
    # plane_detect_min_points: int = 15
    # # Step height used for LoD1.3 generation
    # lod13_step_height: float = 3.0 # Allow float for step height
    # # Complexity factor building reconstruction
    # complexity_factor: float = 0.7

    # --- Output options ---
    # Output CityJSONSequence file for each building [default: one file per output tile]
    # split_cjseq: bool = False # Default is False based on comment
    # Omit metadata from output CityJSON
    # omit_metadata: bool = False # TOML shows 'true', assuming False is default

    # cj_tranlate and cj_scale are important for tyler to function, make this a required option when running roofer
    cj_translate: Optional[List[float]] = field(default_factory=lambda: [171800.0, 472700.0, 0.0])
    cj_scale: Optional[List[float]] = field(default_factory=lambda: [0.001, 0.001, 0.001])

    # --- Output Attributes Renaming ---
    # Nested configuration for output attribute names
    output_attributes: OutputAttributesConfig = field(default_factory=OutputAttributesConfig)
