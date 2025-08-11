import logging
from multiprocessing import Queue, Process

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from osgeo import ogr

import fiona
from fiona.crs import from_epsg
from shapely import Polygon, wkb

# Schema used in gpkg for lvbag
_bag_schema = {
    'geometry': 'Polygon',
    'properties': {
        'identificatie': 'str',
        'oorspronkelijkBouwjaar': 'int',
        'status': 'str',
        'documentDatum': 'str',
        'documentNummer': 'str',
        'voorkomenIdentificatie': 'int',
        'beginGeldigheid': 'str',
        'eindGeldigheid': 'str',
        'tijdstipRegistratie': 'str',
        'eindRegistratie': 'str',
        'tijdstipRegistratieLV': 'str',
        'tijdstipEindRegistratieLV': 'str',
        'tijdstipInactiefLV': 'str',
        'tijdstipInactief': 'str',
        'tijdstipNietBagLV': 'str',
        'geconstateerd': 'str',
        'force_low_lod': 'bool'
    }
}
BAG_DATE_FORMAT = "%Y/%m/%d"

# Function to convert a string to datetime or return None


def _to_datetime_optional(date_str: Optional[str]) -> Optional[datetime]:
    """ Converts a string to datetime, date_str is optional, if none is present, return none """
    if date_str is None:
        return None
    try:
        return datetime.strptime(date_str[:10], BAG_DATE_FORMAT)
    except ValueError:
        return None


def _to_datetime(date_str: str) -> datetime:
    """ Converts a string to datetime, date_str is required """
    return datetime.strptime(date_str[:10], BAG_DATE_FORMAT)


def _bag_filtered_by_year(source: Path, year: int) -> Iterator[Optional[dict[Any, Any]]]:
    """ Filter BAG by a specific year """
    reference_date = datetime(year=year, month=12, day=31)

    for feature in bag_zip_read(source):
        tijdstipInactiefLV = _to_datetime_optional(feature["tijdstipInactiefLV"])
        tijdstipNietBagLV = _to_datetime_optional(feature["tijdstipNietBagLV"])
        tijdstipRegistratieLV = _to_datetime(feature["tijdstipRegistratieLV"])
        tijdstipEindRegistratieLV = _to_datetime_optional(feature["tijdstipEindRegistratieLV"])
        beginGeldigheid = _to_datetime(feature["beginGeldigheid"])
        eindGeldigheid = _to_datetime_optional(feature["eindGeldigheid"])

        if (feature["status"] not in ["Niet gerealiseerd pand", "Pand gesloopt", "Bouwvergunning verleend", "Pand ten onrechte opgevoerd", None]) and \
            (tijdstipInactiefLV is None or tijdstipInactiefLV > reference_date) and \
            (tijdstipNietBagLV is None or tijdstipNietBagLV > reference_date) and \
            (tijdstipRegistratieLV <= reference_date) and \
            (tijdstipEindRegistratieLV is None or tijdstipEindRegistratieLV > reference_date) and \
            (beginGeldigheid <= reference_date) and \
                (eindGeldigheid is None or eindGeldigheid == beginGeldigheid or eindGeldigheid > reference_date):
            feature["force_low_lod"] = False
            yield feature

    yield None


def _bag_reader_producer(source: Path, year: int, output_queue: "Queue[Optional[dict[Any, Any]]]") -> None:
    """ This is the producer part of the  """
    print(f"Worker process started for year {year}...")
    for feature in _bag_filtered_by_year(source, year):
        output_queue.put(feature)


def remove_spikes(polygon: Polygon, epsilon: float = 1e-4) -> Polygon:
    def is_colinear(p1: tuple[float, ...], p2: tuple[float, ...], p3: tuple[float, ...]) -> bool:
        # Calculate the area of the triangle formed by the three points
        val = abs((p1[0] * (p2[1] - p3[1]) + p2[0] * (p3[1] - p1[1]) + p3[0] * (p1[1] - p2[1])) / 2.0)

        if val == 0.0:  # The starting point will always create a triangle with surface area of 0.
            # As begin point equals endpoint.
            return False
        else:
            return val < epsilon

    coords = list(polygon.exterior.coords)
    cleaned = []

    for i in range(len(coords)):
        prev = coords[i - 1]
        curr = coords[i]
        nxt = coords[(i + 1) % len(coords)]

        # Skip if current point is colinear with neighbors
        if not is_colinear(prev, curr, nxt):
            cleaned.append(curr)

    if len(cleaned) > 3:
        return Polygon(cleaned)
    else:
        return polygon


def bag_zip_read(lvbag_zip: Path) -> Iterator[dict[Any, Any]]:
    """ Reads the pand table from the LVBAG zip uploaded by PDOK """
    ogr.UseExceptions()  # Allows gdal to communicate errors to python

    # Open the shapefile
    driver = ogr.GetDriverByName("LVBAG")
    if driver is None:
        raise Exception("LVBAG driver is not available.")

    relative_path = os.path.relpath(lvbag_zip, os.getcwd())  # /vsizip only works with relative paths
    dataset = driver.Open("/vsizip/" + relative_path)
    if dataset is None:
        raise Exception(f"Could not open shapefile: {lvbag_zip}")

    layer = dataset.GetLayer()
    layer_definition = layer.GetLayerDefn()

    feature = layer.GetNextFeature()  # Get the first feature

    while feature:
        feature_data = {}

        for i in range(layer_definition.GetFieldCount()):
            field_definition = layer_definition.GetFieldDefn(i)
            feature_data[field_definition.GetName()] = feature.GetField(i)

        geometry = feature.GetGeometryRef()
        wkbgeom = wkb.loads(bytes(geometry.ExportToIsoWkb()))
        # BAG doesn't always contain valid shapes in this case we want to check for the following:
        # Remove any duplicate vertices and prevent self intersecting geometries
        # using simplify and buffer functions of shapely
        simplified_geometry = wkbgeom.simplify(tolerance=0.05, preserve_topology=True).buffer(0)
        clean_geometry = remove_spikes(simplified_geometry)  # remove any points that create a triangle smaller than 0.1 square millimeters

        feature_data['geometry'] = clean_geometry
        feature = layer.GetNextFeature()  # Get the next feature

        yield feature_data


BATCH_SIZE = 100000
logger = logging.getLogger()


def extract_by_year(source: Path, target: Path, year: int) -> None:
    """ Reads the lvbag zip and outputs a filtered by year gpkg, date set for year is {year}-12-31 """
    feature_queue: Queue[Optional[dict[Any, Any]]] = Queue(BATCH_SIZE * 2)
    worker_process = Process(
        target=_bag_reader_producer,  # Producer of the feature queue, reads panden from lvbag
        args=(source, year, feature_queue)
    )
    worker_process.start()

    # Consumer of the feature queue, responsible for writing the filtered by year
    # pand output to a gpkg
    batch: list[dict[str, Any]] = []
    with fiona.open(target, 'w', driver="GPKG", schema=_bag_schema, crs=from_epsg(28992)) as gpkg_target:
        while True:
            feature = feature_queue.get()
            if feature is None:
                break

            batch.append({'geometry': feature.pop('geometry', None), 'properties': feature})
            if (len(batch) % BATCH_SIZE) == 0:
                gpkg_target.writerecords(batch)
                batch.clear()
                logger.info(f"Finished processing {BATCH_SIZE} records, waiting for next batch")

        if len(batch) > 0:
            gpkg_target.writerecords(batch)
            logger.info(f"Finished processing {len(batch)} records, done reading bag")

        worker_process.join()
