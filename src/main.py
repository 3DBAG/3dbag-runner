import argparse
import glob
import json
import logging
import multiprocessing
import os
import shutil
import subprocess
import threading
import re
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from osgeo import gdal

from roofhelper.io.EntryProperties import EntryProperties

import fiona
import geopandas
import laspy
from shapely import box
from tqdm import tqdm

from roofhelper import defaultlogging, processing, zip, tyler
from roofhelper.cityjson.geluid import (GELUID_SCHEMA, HOOGTE_SCHEMA,
                                        building_to_gpkg_dict,
                                        building_to_hoogte_gpkg_dict,
                                        read_height_from_cityjson)
from roofhelper.classifier.intersect_classifier import classify_pointcloud
from roofhelper.io import SchemeFileHandler, download_if_not_exists
from roofhelper.kadaster import bag
from roofhelper.kadaster.geo import grid_create_on_intersecting_centroid
from roofhelper.pdok import PdokS3Uploader, PdokUpdateTrigger, UploadResult
from roofhelper.pdok.PdokDeliverySound import PDOK_DELIVERY_SCHEMA_SOUND, get_pdok_sound_features
from roofhelper.pdok.PdokGeopackageWriter import write_features_to_geopackage
from roofhelper.pointcloud import laz
from roofhelper.roofer import PointcloudConfig, roofer_config_generate
from roofhelper.geo.interpolation import image_interpolation_idw

log = defaultlogging.setup_logging(logging.INFO)


def classify_operation(args: argparse.Namespace) -> None:
    """Operation function for classifying pointcloud."""
    classify_pointcloud(args.laz_file, args.gpkg_file, args.output_file, args.buffer)


def createlazdb_operation(args: argparse.Namespace) -> None:
    createlazdb(args.sas_uri, args.database, args.pattern, args.epsg, args.processing_chunk_size)


def createlazdb(uri: str, target: Path, pattern: str = "(?i)^.*(las|laz)$", epsg: int = 28992, processing_chunk_size: int = 100) -> None:
    handler = SchemeFileHandler(Path(""))

    def _worker(entry: EntryProperties) -> dict[str, Any]:
        """ Used in createlazdb with multiprocessing to processes multiple laz files in parallel """
        header_raw = handler.get_bytes_range(entry.full_uri, 0, 4096)
        with laspy.open(BytesIO(header_raw), "r") as laz_file:
            laz_header = laz_file.header
            extent_polygon = laz.extent_to_polygon(laz_header)
            return {
                'geometry': extent_polygon,
                'path': entry.name,
                'date': laz_header.creation_date
            }

    file_iterator = (entry for entry in handler.list_entries_shallow(uri, regex=pattern) if entry.is_file)
    counter: int = 0
    for blob_chunk in processing.chunked(file_iterator, processing_chunk_size):
        counter += len(blob_chunk)
        log.info(f"Proccessing: {counter}")

        with ThreadPoolExecutor(max_workers=processing_chunk_size) as executor:
            results = list(executor.map(_worker, blob_chunk))
            gpkg = geopandas.GeoDataFrame(results, geometry="geometry", crs=f"EPSG:{epsg}")
            gpkg.to_file(target, layer="laz_index", driver="GPKG", mode="a")


def createlazindex_operation(args: argparse.Namespace) -> None:
    createlazindex(args.destination, args.temporary_directory)


def createlazindex(destination: str, temporary_directory: str) -> None:
    log.info("Creating index of laz files")
    index_path = Path(os.path.join(temporary_directory, "index.gpkg"))
    createlazdb(destination, index_path)

    log.info("Done creating the index, start uploading the index.gpkg")
    handler = SchemeFileHandler(Path(temporary_directory))
    handler.upload_file_directory(index_path, destination)

    log.info("Done")


def createbagdb_operation(args: argparse.Namespace) -> None:
    createbagdb(args.temporary_directory, args.database, args.year)


def createbagdb(temp_dir: Path, destination: Path, year: int) -> None:
    """ Retrieves the BAG Pand database (NL only) """

    log.info(f"Creating output directory {temp_dir}")
    os.makedirs(temp_dir, exist_ok=True)

    # Download, unpack and load the zip
    log.info("Download footprints")
    bag_extract_url = "https://service.pdok.nl/kadaster/adressen/atom/v1_0/downloads/lvbag-extract-nl.zip"
    bag_extract_zip = Path(os.path.join(temp_dir, "lvbag-extract-nl.zip"))
    download_if_not_exists(bag_extract_url, bag_extract_zip)

    pnd_extract_name = zip.list_files(bag_extract_zip, "^.*PND.*\\.zip$")[0]
    zip.unzip(bag_extract_zip, temp_dir, pnd_extract_name)

    pnd_extract_zip = Path(os.path.join(temp_dir, pnd_extract_name))
    pnd_extract_by_year = Path(os.path.join(temp_dir, destination))
    bag.extract_by_year(pnd_extract_zip, pnd_extract_by_year, year)

    log.info("Finished processing buildings")


def runsingleroofertile_operation(args: argparse.Namespace) -> None:
    runsingleroofertile(tuple(args.extent), args.footprints, args.pointclouds, args.pointclouds_labels, args.year, args.destination, args.temporary_directory, args.pointclouds_low_lod, args.pointclouds_low_lod_labels)


def runsingleroofertile(extent: tuple[float, float, float, float],
                        footprints: str,
                        pointclouds: list[str],
                        pointclouds_labels: list[str],
                        year: int,
                        destination: str,
                        temporary_directory: Path,
                        pointclouds_low_lod: list[str] = [],
                        pointclouds_low_lod_labels: list[str] = [],
                        error_on_missing_tiles: bool = False) -> None:
    """ Generate a single roofer tile for a certain extent """
    log.info(f"Running single roofer tile {destination}")
    file_handler = SchemeFileHandler(temporary_directory)

    footprint_file = file_handler.download_file(footprints)
    building_footprints = geopandas.read_file(footprint_file, bbox=extent, layer=0)

    # All building centroids that intersect with the rectangle will participate with the roofer config
    # This will prevent buildings being present in multiple roofer configs
    building_footprints_filtered = building_footprints[building_footprints.centroid.within(box(*extent))]
    # Use the total bounds of the selected buildings to figure out which laz files participate
    minx, miny, maxx, maxy = building_footprints_filtered.total_bounds
    filtered_extent_box = box(minx, miny, maxx, maxy)

    pointclouds_to_use: list[PointcloudConfig] = []
    pointcloud_priority = 0  # Highest priority

    # Process the normal pointclouds first, they always have higher priority
    all_pointclouds = pointclouds.copy()
    all_pointclouds.extend(pointclouds_low_lod)

    for pointcloud in all_pointclouds:
        pointcloud_footprint_file = file_handler.download_file(pointcloud, "index.gpkg")
        pointcloud_footprint_selected = geopandas.read_file(pointcloud_footprint_file, bbox=filtered_extent_box, layer=0)

        pointcloud_paths = pointcloud_footprint_selected['path'].tolist()
        pointclouds_downloaded: list[str] = []

        def _safe_download(path: str) -> Optional[str]:
            try:
                return str(file_handler.download_file(pointcloud, path))
            except Exception as e:
                if error_on_missing_tiles:
                    raise e
                else:
                    log.warning(f"Skipped tile {path}: {e}")
                    return None

        with ThreadPoolExecutor() as executor:
            results = list(executor.map(_safe_download, pointcloud_paths))
            pointclouds_downloaded = [r for r in results if r is not None]

        if len(pointclouds_downloaded) == 0:  # Seems I wasn't able to download any pointclouds for this pointcloud source, skip it
            break

        if pointcloud not in pointclouds_low_lod:
            index = pointclouds.index(pointcloud)
            pointclouds_to_use.append(PointcloudConfig(name=pointclouds_labels[index],
                                      quality=pointcloud_priority, source=pointclouds_downloaded))
        else:
            index = pointclouds_low_lod.index(pointcloud)
            pointclouds_to_use.append(PointcloudConfig(name=pointclouds_low_lod_labels[index], date=year,
                                      quality=pointcloud_priority, force_lod11=True,
                                      select_only_for_date=True, source=pointclouds_downloaded))

        pointcloud_priority += 1  # Decrease priority for the next pointcloud

    if any(len(x.source) > 0 for x in pointclouds_to_use):
        config: str = roofer_config_generate(str(footprint_file.absolute()),
                                             pointclouds=pointclouds_to_use,
                                             bbox=list(extent),
                                             id_attribute="identificatie",
                                             yoc_column="oorspronkelijkBouwjaar",
                                             output_directory=str(temporary_directory))

        log.info(f"Generated the following configuration:\n{config}")
        config_path = file_handler.create_file(suffix=".toml", text=config)

        # Run roofer with timeout and retries via helper
        MAX_RETRIES = 3  # max retries on failure
        TIMEOUT_SECONDS = 6 * 60 * 60  # 6 hours per attempt
        try:
            processing.run_with_retries([
                "roofer", "-c", str(config_path), "--no-tiling", "--lod12", "--lod13", "--lod22", "--no-simplify"
            ], timeout=TIMEOUT_SECONDS, max_attempts=MAX_RETRIES + 1, check=True, text=True, capture_output=True)
            roofer_success = True
        except subprocess.TimeoutExpired:
            log.error(f"Roofer timed out for {destination} after {MAX_RETRIES + 1} attempts.")
            roofer_success = False
        except subprocess.CalledProcessError as e:
            # run_with_retries already logs stderr/command when capture_output=True; keep only a summary here.
            log.error(f"Roofer failed for {destination} after {MAX_RETRIES + 1} attempts, exit code {e.returncode}.")
            roofer_success = False

        if roofer_success:
            candidates = glob.glob(os.path.join(temporary_directory, "*.jsonl"))
            if len(candidates) > 0:
                jsonl = Path(glob.glob(os.path.join(temporary_directory, "*.jsonl"))[0])
                cityjson = Path(os.path.join(temporary_directory, "data.city.json"))

                subprocess.run(f"cat {jsonl} | cjseq collect > {cityjson}", shell=True, check=True)

                file_handler.upload_file_direct(cityjson, destination)
                log.info(f"Uploading {destination}")

                os.unlink(jsonl)
                os.unlink(cityjson)
            else:
                log.info(f"Tile {destination} didn't produce any output, validate what is going on")
        else:
            log.error(f"Roofer did not complete successfully for {destination} after {MAX_RETRIES + 1} attempts.")

        for file in file_handler.file_handles:  # Delete all temporary files
            file_handler.delete_if_not_local(file.path)
    else:
        log.info(f"This tile contains footprints, but no corresponding point clouds found, skipping {destination} tile")

    log.info(f"Done processing {destination}")


def runallconfigtiles_operation(args: argparse.Namespace) -> None:
    runallconfigtiles(args.footprints,
                      args.pointclouds,
                      args.pointclouds_labels,
                      args.year,
                      args.filename,
                      args.temporary_directory,
                      args.destination,
                      args.pointclouds_low_lod,
                      args.pointclouds_low_lod_labels,
                      args.max_workers,
                      args.error_on_missing_tiles)


def runallconfigtiles(footprints: str,
                      pointclouds: list[str],
                      pointclouds_labels: list[str],
                      year: int,
                      filename: str,
                      temporary_directory: Path,
                      destination: str,
                      pointclouds_low_lod: list[str] = [],
                      pointclouds_low_lod_labels: list[str] = [],
                      max_workers: int = 0,
                      error_on_missing_tiles: bool = False) -> None:
    """ Generate all roofer tiles using a footprint database """

    log.info("Run all tiles")

    if max_workers <= 0:
        max_workers = multiprocessing.cpu_count()

    file_handler = SchemeFileHandler(temporary_directory)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a list to store the futures
        futures = []
        footprint_path = file_handler.download_file(footprints)
        for extent in grid_create_on_intersecting_centroid(footprint_path, 2000):
            data = {"x": int(extent[0]), "y": int(extent[1])}
            name_generated = filename.format(**data)
            output = file_handler.navigate(destination, f"{name_generated}.city.jsonl")
            if not file_handler.file_exists(output):
                log.info(f"Submitted tile {name_generated}")
                futures.append(executor.submit(partial(runsingleroofertile,
                                               extent,
                                               footprints=f"file:{footprint_path}",  # Explicitly set this to a file source
                                               pointclouds=pointclouds,
                                               pointclouds_labels=pointclouds_labels,
                                               year=year,
                                               destination=output,
                                               temporary_directory=Path(os.path.join(temporary_directory, name_generated)),
                                               pointclouds_low_lod=pointclouds_low_lod,
                                               pointclouds_low_lod_labels=pointclouds_low_lod_labels,
                                               error_on_missing_tiles=error_on_missing_tiles)))

        log.info("Done submitting all tiles, show progress")
        # Use tqdm to add a progress bar
        for future in tqdm(futures, total=len(futures), desc="Processing tiles"):
            future.result()  # This will block until the future is done


def pointcloudsplit_operation(args: argparse.Namespace) -> None:
    pointcloudsplit(args.input_connection, args.output_connection, args.grid_size, args.temporary_directory, args.max_workers)


def pointcloudsplit(input_connection: str, output_connection: str, grid_size: int, temporary_directory: Path, max_workers: int = 0) -> None:
    """ Split laz files into smaller, manageable chunks """
    log.info(f"Splitting laz files, source: {input_connection} destination: {output_connection}")
    os.makedirs(temporary_directory, exist_ok=True)
    handler = SchemeFileHandler(temporary_directory)

    if max_workers <= 0:
        max_workers = multiprocessing.cpu_count()

    file_list = list(entry for entry in handler.list_entries_shallow(input_connection, regex=r"(?i)^.*\.LAZ$") if entry.is_file)

    def _upload_and_cleanup(file_path: Path, filename: str) -> None:
        try:
            # Extract x and y coordinates from the file path using regex (format: tempname_x_y.laz)
            file_stem = file_path.stem  # removes .laz extension
            # Use regex to match pattern: anything_digits_digits
            match = re.search(r'.*_(\d+)_(\d+)$', file_stem)
            if match:
                x_coord = match.group(1)
                y_coord = match.group(2)

                # Create new filename: something_x_y.laz (remove .laz from original filename if present)
                base_filename = filename.replace('.laz', '').replace('.LAZ', '')
                new_filename = f"{base_filename}_{x_coord}_{y_coord}.laz"
            else:
                raise ValueError(f"Filename {filename} does not match expected pattern for x and y coordinates.")

            handler.upload_file_directory(file_path, output_connection, new_filename)
            os.remove(file_path)
            log.info(f"Uploaded and removed: {file_path} as {new_filename}")
        except Exception as e:
            log.error(f"Failed to process {file_path}: {e}")

    def _process_laz_file(entry: Any) -> None:
        """Process a single LAZ file: download, split, and upload tiles"""
        try:
            log.info(f"Processing {entry.name}")

            log.info(f"Downloading point cloud {entry.name}")
            downloaded_tile = handler.download_file(entry.full_uri)

            log.info(f"Splitting point cloud {entry.name}")
            generated_tiles = laz.laz_tile_split(downloaded_tile, temporary_directory, grid_size)

            log.info(f"Generated {len(generated_tiles)} for {entry.name}, start uploading them")
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(_upload_and_cleanup, Path(tile), entry.name) for tile in generated_tiles]

                for future in as_completed(futures):
                    future.result()

            log.info(f"Finished uploading {len(generated_tiles)} files for {entry.name}")
            handler.delete_if_not_local(downloaded_tile)

        except Exception as e:
            log.error(f"Failed to process file {entry.name}: {e}")

    # Process files concurrently
    log.info(f"Processing {len(file_list)} LAZ files with {max_workers} workers")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_laz_file, entry) for entry in file_list]

        # Use tqdm to show progress
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing LAZ files"):
            future.result()

    log.info("Finished processing all LAZ files")


def hoogte_operation(args: argparse.Namespace) -> None:
    height_database(args.source, args.destination, args.temporary_directory, args.year, False)


def geluid_operation(args: argparse.Namespace) -> None:
    height_database(args.source, args.destination, args.temporary_directory, args.year, True)


def height_database(source: str, destination: str, temporary_directory: Path, year: int, isgeluid: bool = True) -> None:
    logging.info("Start geluid workflow")
    scheme_handler = SchemeFileHandler(temporary_directory)

    os.makedirs(temporary_directory, exist_ok=True)

    gpkg_name_format = ""
    if isgeluid:
        gpkg_name_format = f"{year}_NL_3d_geluid_gebouwen"
    else:
        gpkg_name_format = f"{year}_3d_hoogtestatistieken_gebouwen"

    log.info(f"Creating database {gpkg_name_format}")
    batch_size = 100000
    queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=batch_size * 2)  # type: ignore
    temporary_db = Path(os.path.join(temporary_directory, f"{gpkg_name_format}.gpkg"))

    def _reader(uri: str) -> None:
        file = scheme_handler.download_file(uri)
        try:
            for building in read_height_from_cityjson(file):
                if isgeluid:
                    queue.put(building_to_gpkg_dict(building))
                else:
                    queue.put(building_to_hoogte_gpkg_dict(building))
        except Exception as e:
            log.error(f"Failed processing {uri} {e}")
            raise

        scheme_handler.delete_if_not_local(file)
        log.info(f"Processed {uri}")

    def _producer() -> None:
        log.info("Start retrieving city.json files")
        futures = []
        with ThreadPoolExecutor() as executor:
            for entry in scheme_handler.list_entries_shallow(source, regex="(?i)^.*city\\.json$"):
                if not entry.is_file:
                    continue
                futures.append(executor.submit(_reader, entry.full_uri))

            for future in as_completed(futures):
                future.result()

        queue.put(None)
        log.info("Stopping producer")

    def _consumer() -> None:
        log.info("Starting writing to height database")
        batch = []
        schema = GELUID_SCHEMA if isgeluid else HOOGTE_SCHEMA
        with fiona.open(temporary_db, 'w', driver='GPKG', schema=schema, crs='EPSG:28992', layer='buildings') as gpkg:
            while True:
                item = queue.get()
                if item is None:
                    break

                batch.append(item)
                if (len(batch) % batch_size) == 0:
                    gpkg.writerecords(batch)
                    log.info(f"Finished processing {batch_size} records, waiting for next batch")
                    batch.clear()

            if len(batch) > 0:
                gpkg.writerecords(batch)
                log.info(f"Finished processing {len(batch)} records, done reading bag")
        log.info("Stopping consumer")

    # Start threads
    producer_thread = threading.Thread(target=_producer)
    consumer_thread = threading.Thread(target=_consumer)

    producer_thread.start()
    consumer_thread.start()

    producer_thread.join()
    consumer_thread.join()
    log.info("Done creating sound database, start creating zip file")

    # Create zip file containing the database
    zip_path = Path(os.path.join(temporary_directory, f"{gpkg_name_format}.zip"))
    zip.zip_file(temporary_db, zip_path)
    log.info(f"Created zip file: {zip_path}")

    log.info("Start uploading zip file")
    scheme_handler.upload_file_direct(zip_path, destination)
    log.info("Uploaded zip file, stopping workflow")


def tyler_operation(args: argparse.Namespace) -> None:
    tyler_runner(args.source, args.destination, args.temporary_directory, args.mode, args.metadata_city_json)


def tyler_runner(source: str, destination: str, temporary_directory: Path, mode: str, metadata_city_json: Path) -> None:
    log.info("Staring tyler workflow")

    log.info("Download and fix cityjson files to be tyler compatible")

    tyler_input_directory = Path(os.path.join(str(temporary_directory), "input"))
    found_schema = tyler.prepare_files(source, tyler_input_directory)
    log.info(f"Using schema {found_schema}")

    tyler_output_directory = Path(os.path.join(str(temporary_directory), "output"))
    os.makedirs(tyler_output_directory, exist_ok=True)

    match mode:  # run should be a config file
        case "buildings":
            log.info("Running tyler for buildings")
            building_schema = "beginGeldigheid:string,documentDatum:string,documentNummer:string,eindGeldigheid:string,eindRegistratie:string,force_low_lod:bool,geconstateerd:string,identificatie:string,oorspronkelijkBouwjaar:int,rf_extrusion_mode:string,rf_force_lod11:bool,rf_h_ground:float,rf_h_pc_98p:float,rf_h_roof_ridge:float,rf_is_glass_roof:bool,rf_is_mutated_AHN3_2023:bool,rf_is_mutated_AHN4_AHN3:bool,rf_nodata_frac_2023:float,rf_nodata_frac_AHN3:float,rf_nodata_frac_AHN4:float,rf_nodata_r_2023:float,rf_nodata_r_AHN3:float,rf_nodata_r_AHN4:float,rf_pc_select:string,rf_pc_source:string,rf_pc_year:int,rf_pointcloud_unusable:bool,rf_pt_density_2023:float,rf_pt_density_AHN3:float,rf_pt_density_AHN4:float,rf_reconstruction_time:int,rf_ridgelines:int,rf_rmse_lod12:float,rf_rmse_lod13:float,rf_rmse_lod22:float,rf_roof_elevation_50p:float,rf_roof_elevation_70p:float,rf_roof_elevation_max:float,rf_roof_elevation_min:float,rf_roof_n_planes:int,rf_roof_type:string,rf_success:bool,rf_val3dity_lod12:string,rf_val3dity_lod13:string,rf_val3dity_lod22:string,rf_volume_lod12:float,rf_volume_lod13:float,rf_volume_lod22:float,status:string,tijdstipEindRegistratieLV:string,tijdstipInactief:string,tijdstipInactiefLV:string,tijdstipNietBagLV:string,tijdstipRegistratie:string,tijdstipRegistratieLV:string,voorkomenIdentificatie:int"
            tyler.cityjsonbuilding_to_glb(tyler_input_directory, metadata_city_json, tyler_output_directory, building_schema)
        case "terrain":
            log.info("Running tyler for terrain")
            terrain_schema = "3df_id:string,3df_class:string,relatievehoogteligging:string,plus_type:string,bgt_status:string,bgt_fysiekvoorkomen:string,bgt_functie:string,optalud:string,p_punt_datum:string,plus_functie:string,starttijd_3dfier_flow:string,namespace:string,plus_status:string,isbufferobject:int,bgt_type:string,gml_id:string,bladnaam:string,objecteindtijd:string,objectbegintijd:string,eindregistratie:string,isaltered:int,rh_standardized:string,bronhouder:string,lv_publicatiedatum:string,plus_fysiekvoorkomen:string,tijdstipregistratie:string,hoortbijbrug:string,aantal_punten:string"
            tyler.cityjsonterrain_to_glb(tyler_input_directory, metadata_city_json, tyler_output_directory, terrain_schema)
        case _:
            raise ValueError(f"Invalid mode '{mode}': must be 'buildings' or 'terrain'")

    scheme_handler = SchemeFileHandler()
    scheme_handler.upload_folder(tyler_output_directory, destination)
    shutil.rmtree(temporary_directory)
    log.info("Done")


def trigger_pdok_update_operation(args: argparse.Namespace) -> None:
    trigger_pdok_update(args.source,
                        args.destination_s3_url,
                        args.destination_s3_user,
                        args.destination_s3_key,
                        args.s3_prefix,
                        args.trigger_update_url,
                        args.trigger_private_key_content,
                        args.expected_gpkg_name)


def trigger_pdok_update(source: str,
                        destination_s3_url: str,
                        destination_s3_user: str,
                        destination_s3_key: str,
                        s3_prefix: str,
                        trigger_update_url: str,
                        trigger_private_key_content: str,
                        expected_gpkg_name: str) -> None:
    """Main function to trigger PDOK update process."""
    log.info("Starting update of pdok geopackage")

    try:
        # Download the geopackage file from the source URI
        log.info(f"Downloading geopackage from source: {source}")
        file_handler = SchemeFileHandler()
        local_geopackage_path = file_handler.download_file(source)
        log.info(f"Downloaded geopackage to: {local_geopackage_path}")

        # Create S3 uploader and upload file
        uploader = PdokS3Uploader(destination_s3_url, destination_s3_user, destination_s3_key)
        upload_result: UploadResult = uploader.upload_file(local_geopackage_path, s3_prefix, expected_gpkg_name)

        if not upload_result.success:
            log.error(f"Upload failed: {upload_result.error_message}")
            exit(-1)

        # Create trigger and send update notification
        trigger = PdokUpdateTrigger(trigger_update_url, trigger_private_key_content)
        success = trigger.trigger_update(upload_result)

        if not success:
            log.error("Failed to trigger PDOK update")
            exit(-1)

        log.info("Successfully completed PDOK update process")

    except Exception as e:
        log.error(f"Error during PDOK update process: {str(e)}")
        exit(-1)


def create_pdok_index_operation(args: argparse.Namespace) -> None:

    features = get_pdok_sound_features(args.source, args.ahn_source, args.url_prefix)
    write_features_to_geopackage(PDOK_DELIVERY_SCHEMA_SOUND, features, args.destination, args.temporary_directory)


def splitgpkg_operation(args: argparse.Namespace) -> None:
    splitgpkg(args.source, args.destination, args.split_source, args.file_pattern, args.readme, args.temporary_directory)


def splitgpkg(
    source: str,
    destination: str,
    split_source: str,
    file_pattern: str,
    readme: list[str],
    temporary_directory: Path
) -> None:
    """
    Split `source` (a GeoPackage) into <tile>.gpkg files whose features fall
    inside the bbox of each tile defined in `split_source` (JSON).

    * Uses a thread pool to parallelise the I/O-heavy per-tile work.
    * Each output GPKG goes into `temporary_directory` and keeps the same
      layer schema/CRS as the first layer of the source file.
    """
    log.info("Start splitting gpkg %s", source)
    file_handler = SchemeFileHandler(temporary_directory)

    gpkg_source: Path = file_handler.download_file(source)
    zip_extract_dir: Optional[Path] = None

    # Check if the downloaded file is a zip file and unpack it if necessary
    if gpkg_source.suffix.lower() == '.zip':
        log.info("Detected zip file, unpacking...")
        zip_extract_dir = temporary_directory / "extracted"
        os.makedirs(zip_extract_dir, exist_ok=True)
        zip.unzip(gpkg_source, zip_extract_dir)

        # Find the .gpkg file in the extracted contents
        gpkg_files = list(zip_extract_dir.glob("**/*.gpkg"))
        if not gpkg_files:
            raise FileNotFoundError("No .gpkg file found in the zip archive")
        if len(gpkg_files) > 1:
            log.warning(f"Multiple .gpkg files found in zip, using the first one: {gpkg_files[0]}")

        gpkg_source = gpkg_files[0]
        log.info(f"Using extracted geopackage: {gpkg_source}")

    split_source_path: Path = file_handler.download_file(split_source)
    # -----------------------------------------------------------------------
    # load tile index {tile_code: [xmin, ymin, xmax, ymax]}
    # -----------------------------------------------------------------------
    with open(split_source_path, "r") as f:
        tiles: dict[str, list[float]] = json.load(f)

    layer_name: str = fiona.listlayers(gpkg_source)[0]

    # -----------------------------------------------------------------------
    # worker function (runs in pool threads)
    # -----------------------------------------------------------------------
    def _write_tile_gpkg(item: tuple[str, list[float]]) -> Optional[Path]:
        """
        Read, filter, and write a single tile.

        Returns the tile code on success, or None if the tile was empty.
        """
        key, bbox = item
        try:
            name = file_pattern % key
            zipfile_name = f"{name}.zip"

            # Check if the file already exists at destination
            if file_handler.file_exists(file_handler.navigate(destination, zipfile_name)):
                log.info("Skipping %s - already exists at destination", zipfile_name)
                return None

            log.debug("Generate gpkg %s", name)
            minx, miny, maxx, maxy = bbox
            bbox_geom = box(minx, miny, maxx, maxy)
            # 1) read features intersecting the bbox
            features = geopandas.read_file(gpkg_source, bbox=bbox_geom, layer=0)

            # 2) keep only those whose centroid is *inside* the bbox polygon

            features_by_centroid = features[features.centroid.within(bbox_geom)]

            if features_by_centroid.empty:
                log.debug("Tile %s: empty after centroid filter", name)
                return None

            os.makedirs(temporary_directory / name, exist_ok=True)

            # 3) write to <key>.gpkg in the temp dir
            out_path = temporary_directory / name / f"{name}.gpkg"
            features_by_centroid.to_file(
                out_path,
                driver="GPKG",
                layer=layer_name,
                index=False,
            )
            log.info("Wrote %s", out_path.name)
            zipfile = temporary_directory / f"{name}.zip"

            with open(temporary_directory / name / "readme.md", "w") as f:
                f.write("\n".join(readme) + "\n")

            zip.zip_dir(temporary_directory / name, zipfile)

            file_handler.upload_file_directory(zipfile, destination)

            shutil.rmtree(temporary_directory / name)
            os.unlink(zipfile)
            return out_path

        except Exception as exc:
            # Never let one failure kill the whole pool
            log.exception("Tile %s failed: %s", key, exc)
            return None

    # -----------------------------------------------------------------------
    # run pool
    # -----------------------------------------------------------------------
    max_workers = min(32, (os.cpu_count() or 1))

    log.info("Using %d worker threads", max_workers)

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_write_tile_gpkg, item): item[0]
            for item in tiles.items()
        }

        for future in as_completed(futures):
            if future.result():
                completed += 1

    log.info("Finished %d / %d tiles", completed, len(tiles))

    log.info("All tiles uploaded to %s", destination)

    # Clean up extracted files if we unpacked a zip
    if zip_extract_dir is not None and zip_extract_dir.exists():
        log.info("Cleaning up extracted zip files...")
        shutil.rmtree(zip_extract_dir)
        log.info("Extracted files cleaned up")

    log.info("Done")


def create_quantized_mesh_operation(args: argparse.Namespace) -> None:
    # Pass through interpolation choice
    create_quantized_mesh(args.source, args.destination, args.temporary_directory, args.parallel)


def create_quantized_mesh(source: str, destination: str, temporary_directory: Path, parallel: Optional[int] = os.cpu_count()) -> None:
    gdal.AllRegister()
    gdal.UseExceptions()

    handler = SchemeFileHandler(temporary_directory)
    entries = handler.list_entries_shallow(source, regex=r"(?i)^.*\.tif$")

    def _warp_and_fill_images(entry: EntryProperties) -> str:
        log.info(f"Downloading {entry.name}")
        t0 = time.perf_counter()
        tile = handler.download_file(entry.full_uri)
        log.info(f"Downloaded {entry.name} in {time.perf_counter() - t0:.2f}s")

        tile_filled = f"{temporary_directory}/{tile.stem}_filled.tif"
        log.info(f"Fill no data for {entry.name}")
        t1 = time.perf_counter()
        image_interpolation_idw(input_file=tile, output_file=tile_filled, k=8, power=2.0, batch_size=500_000)  # batch size has the most influence on memory usage
        log.info(f"Filled nodata for {entry.name} in {time.perf_counter() - t1:.2f}s")

        log.info(f"Warping {entry.name}")
        tile_warped = f"{temporary_directory}/{tile.stem}_warped_4326.tif"
        t2 = time.perf_counter()
        subprocess.run(["gdalwarp", "-q", "-t_srs", "EPSG:4326+4979", tile_filled, tile_warped], check=True)
        log.info(f"Warped {entry.name} in {time.perf_counter() - t2:.2f}s")

        os.unlink(tile)
        os.unlink(tile_filled)
        return tile_warped

    if parallel is None:
        parallel = os.cpu_count()

    with ThreadPoolExecutor(max_workers=parallel) as p:
        warped_files = list(p.map(_warp_and_fill_images, entries))

    file_list: str = f"{temporary_directory}/tif_4_vrt.txt"
    with open(file_list, "w") as f:
        f.writelines(warped_files)

    # Build a VRT from our list of warped tifs into the temporary directory
    vrt_path = os.path.join(str(temporary_directory), "ahn.vrt")
    subprocess.run(["gdalbuildvrt", "-input_file_list", file_list, vrt_path, "-a_srs", "EPSG:4326"], check=True)

    start_zoom = 15
    break_zoom = 9
    end_zoom = 0

    output_directory: Path = temporary_directory / "quantized_mesh"
    os.makedirs(output_directory, exist_ok=True)

    # Create quantized mesh tiles for level start_zoom to break_zoom using ctb-tile
    # You can find ctb-tile on https://github.com/geo-data/cesium-terrain-builder (it's an old executable...)
    try:
        log.info(f"Running ctb-tile from {start_zoom} to level {break_zoom}...")
        subprocess.run([
            "ctb-tile", "-v", "-f", "Mesh", "-C", "-N",
            "-e", str(break_zoom), "-s", str(start_zoom), "-o", str(output_directory), vrt_path
        ], check=True)

        # create layer.json file
        log.info("Creating layer.json file...")
        subprocess.run([
            "ctb-tile", "-f", "Mesh", "-C", "-N",
            "-e", str(end_zoom), "-s", str(start_zoom), "-c", "1", "-l", "-o", str(output_directory), vrt_path
        ], check=True)

        # Workaround: generate GeoTIFF tiles on level break_zoom
        log.info(f"Creating GTiff tiles for level {break_zoom}...")
        subprocess.run([
            "ctb-tile", "-v", "--output-format", "GTiff", "--output-dir", str(temporary_directory),
            "-s", str(break_zoom), "-e", str(break_zoom), vrt_path
        ], check=True)

        # Create VRT for GeoTIFF tiles on level break_zoom
        level_vrt = os.path.join(str(temporary_directory), f"level{break_zoom}.vrt")
        tiff_pattern = os.path.join(str(temporary_directory), str(break_zoom), "*", "*.tif")
        tiff_files = glob.glob(tiff_pattern)
        if tiff_files:
            log.info(f"Create vrt for GTiff tiles on level {break_zoom}...")
            subprocess.run(["gdalbuildvrt", level_vrt] + tiff_files, check=True)
        else:
            log.warning(f"No GTiff tiles found for level {break_zoom} (pattern: {tiff_pattern})")

        # Make terrain tiles for level break_zoom-1 to 0
        log.info(f"Run ctb-tile on level {break_zoom - 1} to {end_zoom}")
        subprocess.run([
            "ctb-tile", "-v", "-f", "Mesh", "-C", "-N",
            "-e", str(end_zoom), "-s", str(break_zoom - 1), "-o", str(output_directory), level_vrt
        ], check=True)

        handler.upload_folder(output_directory, destination)
    except FileNotFoundError as e:
        log.error(f"External command not found: {e}. Ensure ctb-tile and gdalbuildvrt are installed and in PATH.")
    except subprocess.CalledProcessError as e:
        log.error(f"External command failed with exit code {e.returncode}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool for generating toml configuration files for roofer")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    splitlaz = subparsers.add_parser("splitlaz", help="Split laz files")
    splitlaz.add_argument("--input_connection", type=str, required=True, help="SAS URI including the directory containing the laz files")
    splitlaz.add_argument("--output_connection", type=str, required=True, help="SAS URI destination of generated tiles", default=0)
    splitlaz.add_argument("--temporary_directory", type=Path, required=True, help="Temporary directory for the devided laz tiles")
    splitlaz.add_argument("--grid_size", type=int, required=True, help="Size of the grid for said laz tiles")
    splitlaz.add_argument("--max_workers", type=int, required=False, default=0, help="Maximum number of concurrent file processing workers (0 = use CPU count)")
    splitlaz.set_defaults(func=pointcloudsplit_operation)

    createbagdb = subparsers.add_parser("createbagdb", help="Creates a geopackage containing building footprints")
    createbagdb.add_argument("--temporary_directory", type=Path, help="Temporary directory for processing files in")
    createbagdb.add_argument("--year", type=int, required=False, help="Reconstruction year", default=0)
    createbagdb.add_argument("--database", type=Path, required=True, help="Geopackage to write the footprints to, the geopackage will always be newly created")
    createbagdb.add_argument("--low_lod_source", type=Path, required=False, help="Geopackage containing polygons, if the polygon intersects with the building, mark it for low LOD")
    createbagdb.set_defaults(func=createbagdb_operation)

    createlazdb = subparsers.add_parser("createlazdb", help="Create a geopackage containing the laz footprints")
    createlazdb.add_argument("--sas_uri", type=str, required=True, help="An Azure blob sas token, including the directory path where to read the *.laz files from.")
    createlazdb.add_argument("--database", type=Path, required=True, help="Geopackage to write the footprints to, the geopackage will always be newly created")
    createlazdb.add_argument("--pattern", type=str, required=False, help="Regex pattern each laz file must match with", default="(?i)^.*(las|laz)$")
    createlazdb.add_argument("--epsg", type=int, required=False, help="EPSG Code, eg 28992 for RD", default=28992)
    createlazdb.add_argument("--processing-chunk-size", type=int, required=False, help="Batch size of laz files to concurrently process, default 100, concurrency will equal the amount of cpu cores you have in the system", default=100)
    createlazdb.set_defaults(func=createlazdb_operation)

    classify = subparsers.add_parser("classify", help="Classify pointcloud based on intersecting building geometries from geopackage")
    classify.add_argument("laz_file", type=str, help="Input LAZ file path")
    classify.add_argument("gpkg_file", type=str, help="Input geopackage file with building geometries")
    classify.add_argument("output_file", type=str, help="Output LAZ file path")
    classify.add_argument("--buffer", type=float, default=0.2, help="Buffer distance in meters for geometries (default: 0.2)")
    classify.set_defaults(func=classify_operation)

    runsingleroofertile = subparsers.add_parser("runsingleroofertile", help="Create roofer configuration files using gpkgs as input")
    runsingleroofertile.add_argument("--destination", type=str, required=True)
    runsingleroofertile.add_argument("--footprints", type=str, required=True, help="SQL Lite database containing all file references", default="footprints.gpkg")
    runsingleroofertile.add_argument("--year", type=int, required=True, help="Year being processed")
    runsingleroofertile.add_argument("--temporary_directory", type=Path, required=True, help="Directory for temporary files")
    runsingleroofertile.add_argument("--pointclouds", type=str, required=True, nargs="*", help="List of laz files to use, point to a directory that contains a index.gpkg file. Format is file://./laz or azure://https:... sas token")
    runsingleroofertile.add_argument("--pointclouds_labels", type=str, required=True, nargs="*", help="Label for each pointcloud, must have the same amount of arguments as pointclouds, example: 2022")
    runsingleroofertile.add_argument("--extent", type=float, required=False, nargs="*", help="Extent, formatted in xmin,ymin,xmax,ymax")
    runsingleroofertile.add_argument("--pointclouds_low_lod", type=str, required=False, default=[], nargs="*", help="List of laz files to use, point to a directory that contains a index.gpkg file. Format is file://./laz or azure://https:... sas token")
    runsingleroofertile.add_argument("--pointclouds_low_lod_labels", type=str, required=False, default=[], nargs="*", help="Label for each pointcloud, must have the same amount of arguments as pointclouds_low_low, example: 2022")
    runsingleroofertile.set_defaults(func=runsingleroofertile_operation)

    runallroofertiles = subparsers.add_parser("runallroofertiles", help="Create roofer configuration files using gpkgs as input")
    runallroofertiles.add_argument("--footprints", type=str, required=True, help="SQL Lite database containing all file references", default="footprints.gpkg")
    runallroofertiles.add_argument("--gridsize", type=int, required=True, help="Area of each tile config file, both x and y")
    runallroofertiles.add_argument("--year", type=int, required=True, help="Year being processed")
    runallroofertiles.add_argument("--temporary_directory", type=Path, required=True, help="Directory for temporary files")
    runallroofertiles.add_argument("--pointclouds", type=str, required=True, nargs="*", help="List of laz files to use, point to a directory that contains a index.gpkg file. Format is file://./laz or azure://https:... sas token")
    runallroofertiles.add_argument("--pointclouds_labels", type=str, required=True, nargs="*", help="Label for each pointcloud, must have the same amount of arguments as pointclouds, example: 2022")
    runallroofertiles.add_argument("--destination", type=str, required=True, help="Destination to write the files to, format is file://./laz or azure://https:... sas token")
    runallroofertiles.add_argument("--pointclouds_low_lod", type=str, required=False, default=[], nargs="*", help="List of laz files to use, point to a directory that contains a index.gpkg file. Format is file://./laz or azure://https:... sas token")
    runallroofertiles.add_argument("--pointclouds_low_lod_labels", type=str, required=False, default=[], nargs="*", help="Label for each pointcloud, must have the same amount of arguments as pointclouds_low_low, example: 2022")
    runallroofertiles.add_argument("--max_workers", type=int, required=False, default=0, help="Keep or delete point clouds after generating city.json files")
    runallroofertiles.add_argument("--filename", type=str, required=False, default="tile_{x}_{y}", help="Name of the tiles to generate")
    runallroofertiles.set_defaults(func=runallconfigtiles_operation)

    runtyler = subparsers.add_parser("tyler", help="Convert CityJSON files to GLB format for visualization")
    runtyler.add_argument("--source", type=str, required=True, help="azure://source")
    runtyler.add_argument("--destination", type=str, required=True, help="azure://destination")
    runtyler.add_argument("--temporary_directory", type=Path, required=True, help="Directory for temporary files")
    runtyler.add_argument("--mode", type=str, required=True, choices=["buildings", "terrain"], help="Terrain")
    runtyler.add_argument("--metadata_city_json", type=Path, required=True, help="Path to metadata.city.json")
    runtyler.set_defaults(func=tyler_operation)

    createlazindex = subparsers.add_parser("createlazindex", help="Create index file for LAZ point cloud files")
    createlazindex.add_argument("--destination", type=str, required=True, help="azure://destination")
    createlazindex.add_argument("--temporary_directory", type=str, required=True, help="Directory for temporary files")
    createlazindex.set_defaults(func=createlazindex_operation)

    geluid = subparsers.add_parser("geluid", help="Create 3D sound model geopackage from CityJSON files")
    geluid.add_argument("--source", type=str, required=True, help="azure://source")
    geluid.add_argument("--destination", type=str, required=True, help="azure://destination")
    geluid.add_argument("--temporary_directory", type=str, required=True, help="Directory for temporary files")
    geluid.set_defaults(func=geluid_operation)

    hoogte = subparsers.add_parser("hoogte", help="Create height model geopackage from CityJSON files")
    hoogte.add_argument("--source", type=str, required=True, help="azure://source")
    hoogte.add_argument("--destination", type=str, required=True, help="azure://destination")
    hoogte.add_argument("--temporary_directory", type=str, required=True, help="Directory for temporary files")
    hoogte.set_defaults(func=hoogte_operation)

    trigger_pdok_update = subparsers.add_parser("trigger_pdok_update", help="Trigger PDOK update")
    trigger_pdok_update.add_argument("--source", type=str, required=True, help="Source URI of the geopackage to update, e.g. azure://source/path/to/geopackage.gpkg")
    trigger_pdok_update.add_argument("--destination_s3_url", type=str, required=True, help="Destination S3 URL for the uploaded file")
    trigger_pdok_update.add_argument("--destination_s3_user", type=str, required=True, help="S3 user for authentication")
    trigger_pdok_update.add_argument("--destination_s3_key", type=str, required=True, help="S3 key for authentication")
    trigger_pdok_update.add_argument("--s3_prefix", type=str, required=True, help="S3 prefix path for the uploaded file, e.g. kadaster/3d-basisvoorziening-features")
    trigger_pdok_update.add_argument("--trigger_update_url", type=str, required=True, help="URL to trigger the PDOK update")
    trigger_pdok_update.add_argument("--trigger_private_key_content", type=str, required=True, help="Private key content for triggering the update, encoded in base64")
    trigger_pdok_update.add_argument("--expected_gpkg_name", type=str, required=True, help="PDOK always expects a certain name for the gpkg stored in the s3 bucket, for instance 3dgeluid.gpkg")
    trigger_pdok_update.set_defaults(func=trigger_pdok_update_operation)

    splitgpkg = subparsers.add_parser("splitgpkg", help="Split geopackage into tiles based on spatial index")
    splitgpkg.add_argument("--source", type=str, required=True, help="handle://source")
    splitgpkg.add_argument("--destination", type=str, required=True, help="handle://destination")
    splitgpkg.add_argument("--split_source", type=str, required=True, help="handle://splitsource.json")
    splitgpkg.add_argument("--file_pattern", type=str, required=True, help="%s_2022_3dgeluid_gebouwen.zip")
    splitgpkg.add_argument("--readme", type=str, nargs="*", required=True, help="%s_2022_3dgeluid_gebouwen.zip")
    splitgpkg.add_argument("--temporary_directory", type=Path, required=True, help="Directory for temporary files")
    splitgpkg.set_defaults(func=splitgpkg_operation)

    create_pdok_index = subparsers.add_parser("create_pdok_index", help="Create PDOK index")
    create_pdok_index.add_argument("--source", type=str, required=True, help="handle://source")
    create_pdok_index.add_argument("--destination", type=str, required=True, help="handle://destination")
    create_pdok_index.add_argument("--ahn_source", type=str, required=True, help="handle://splitsource.json")
    create_pdok_index.add_argument("--url_prefix", type=str, required=True, help="%s_2022_3dgeluid_gebouwen.zip")
    create_pdok_index.add_argument("--temporary_directory", type=Path, required=True, help="Directory for temporary files")
    create_pdok_index.set_defaults(func=create_pdok_index_operation)

    create_quantized_mesh = subparsers.add_parser("create_quantized_mesh", help="Create a quantized meshh")
    create_quantized_mesh.add_argument("--input", required=True, help="input dataset, can be a cog, vrt or mosaic, make sure the path/url is exactly the same as the one being supplied to the server when requesting tiles.")
    parser.add_argument("--output", required=True, help="Specify the output folder for the cache.")
    parser.add_argument("--meshing-method", default="grid", help="The meshing method to use: grid, delatin, martini. Defaults to grid.")
    parser.add_argument(
        "-z",
        "--zoom-levels",
        metavar="zoom_levels",
        required=True,
        help="The zoom levels to create a cache for. Separate multiple levels with '-'.",
    )
    parser.add_argument(
        "--port",
        metavar="port",
        required=False,
        default="5580",
        help="The port to run the server on. Defaults to 5580.",
    )
    parser.add_argument(
        "-r",
        "--request-count",
        metavar="request_count",
        required=False,
        default="10",
        help="Amount of simultaneous requests send to CTOD. Defaults to 10.",
    )
    parser.add_argument(
        "-p",
        "--params",
        metavar="request_parameters",
        required=False,
        default=None,
        help="Pass options to tile requests, e.g. 'resamplingMethod=bilinear&defaultGridSize=20'. Defaults to None.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Add --overwrite to overwrite existing tiles in the cache. Defaults to False.",
    )
    parser.add_argument(
        "--export-layer-json",
        metavar="export_layer_json",
        required=False,
        default=None,
        help="Add --export-layer-json followed by the max zoom level to create a layer.json in the root directory.",
    )

    parser.add_argument(
        "--no-gzip",
        action="store_true",
        help="Add --no-gzip to disable gzip compression. Defaults to False. Only use if you are going to statically serve the tiles and don't want to use gzip compression.",
    )

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
