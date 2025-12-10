from hera.workflows import DAG, Artifact, Parameter, Script
from hera.workflows.models.io.argoproj.workflow.v1alpha1 import RetryStrategy

from argo.argodefaults import (MEMORY_EMPTY_DIR, argo_worker,
                               get_workflow_template)


@argo_worker(outputs=Artifact(name="queue", path="/workflow/queue.json"), volumes=MEMORY_EMPTY_DIR)
def queuefunc(source: str, intermediate: str, workercount: int) -> None:
    import json
    import logging
    import sys
    from pathlib import Path

    from roofhelper.defaultlogging import setup_logging
    from roofhelper.io import SchemeFileHandler

    log = setup_logging(logging.INFO)

    file_handler = SchemeFileHandler(Path("/workflow"))
    tifs_source = file_handler.list_entries_shallow(source, regex=r"(?i)^.*\.tif$")
    tifs_generated_set = {x.name for x in file_handler.list_entries_shallow(intermediate, regex=r"(?i)^.*\.tif$")}

    queue = []
    for worker, entry in enumerate(tifs_source):
        if entry.name in tifs_generated_set:
            log.info(f"Skipped {entry.name}, it already exists")
            continue

        log.info(f"Queued {entry.name}")
        queue.append({"worker": worker % workercount,  # We can also do this implicitly by list index, but lets make it explicit to we can choose based
                      "file": entry.name})

    with open("/workflow/queue.json", 'w') as f:
        json.dump(queue, f)

    if len(queue) == 0:
        log.info("All TIFs are already warped, skipping worker stage")
        json.dump([], sys.stdout)
    else:
        log.info(f"Starting {workercount} workers")
        json.dump([i for i in range(workercount)], sys.stdout)


@argo_worker(inputs=Artifact(name="queue", path="/workflow/queue.json"), retry_strategy=RetryStrategy(limit=5))  # type: ignore
def workerfunc(workerid: int, source: str, intermediate: str) -> None:
    import json
    import logging
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from osgeo import gdal

    from qmesh.raster import reproject_image
    from roofhelper.defaultlogging import setup_logging
    from roofhelper.geo.interpolation import image_interpolation_idw
    from roofhelper.io import SchemeFileHandler

    gdal.AllRegister()
    gdal.UseExceptions()

    log = setup_logging(logging.INFO)
    log.info("Initializing worker node")

    temporary_directory = Path("/workflow")
    queue = temporary_directory / "queue.json"
    with open(queue) as f:
        global_queue = json.load(f)

    log.info(f"Done reading the global queue, it contains {len(global_queue)} items")
    local_queue = [str(x["file"]) for x in global_queue if int(x["worker"]) == workerid]
    log.info(f"Worker has to process {len(local_queue)} items of the queue")

    handler = SchemeFileHandler(temporary_directory)

    def _process_task(index: int, name: str) -> None:
        log.info(f"Processing [{index}/{len(local_queue)}] {name}.")

        if handler.file_exists(handler.navigate(intermediate, name)):
            log.info(f"Skipping {name}, it already exists, seems we're retrying")
            return

        log.info(f"Downloading {name}")
        t0 = time.perf_counter()
        tile = handler.download_file(source, name)
        log.info(f"Downloaded {name} in {time.perf_counter() - t0:.2f}s")

        tile_filled = temporary_directory / f"{tile.stem}_filled.tif"
        log.info(f"Fill no data for {name}")
        t1 = time.perf_counter()
        image_interpolation_idw(input_file=tile, output_file=tile_filled, k=8, power=2.0, batch_size=500_000)  # batch size has the most influence on memory usage
        log.info(f"Filled nodata for {name} in {time.perf_counter() - t1:.2f}s")

        log.info(f"Warping {name}")
        tile_warped = temporary_directory / f"{name}_warped_4326.tif"
        t2 = time.perf_counter()
        reproject_image(tile_filled, "EPSG:7415", tile_warped, "EPSG:4979")
        log.info(f"Warped {name} in {time.perf_counter() - t2:.2f}s")

        handler.upload_file_directory(tile_warped, intermediate, name)

        os.unlink(tile)
        os.unlink(tile_filled)
        os.unlink(tile_warped)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_process_task, idx, work) for idx, work in enumerate(local_queue)]
        for future in futures:
            future.result()


@argo_worker(retry_strategy=RetryStrategy(limit=1))  # type: ignore
def mergerfunc(intermediate: str, destination: str) -> None:
    import logging
    import os
    import subprocess
    from pathlib import Path

    from qmesh.cesium import render_layerjson
    from qmesh.mesh import (apply_vertex_averaging, encode_terrain_tiles,
                            generate_intermediate_tiles,
                            generate_terrain_dummy)
    from qmesh.pyramid import calculate_pyramid
    from qmesh.raster import image_get_boundingbox
    from roofhelper.defaultlogging import setup_logging
    from roofhelper.io import SchemeFileHandler

    log = setup_logging(logging.INFO)

    temporary_directory = Path("/workflow")
    handler = SchemeFileHandler(temporary_directory)

    log.info("Listing files")
    warped_files = []
    files_to_download = list(handler.list_entries_shallow(intermediate, regex=r"(?i)^.*\.tif$"))
    for index, tile in enumerate(files_to_download):
        log.info(f"Downloading [{len(files_to_download)}/{index}] {tile.name}")
        warped_tile_path = str(handler.download_file(tile.full_uri))
        warped_files.append(warped_tile_path + "\n")

    log.info(f"Found {len(warped_files)} warped tiles")

    warped_file_list = temporary_directory / "warped.txt"
    with open(warped_file_list, "w") as f:
        f.writelines(warped_files)

    # Build a VRT from our list of warped tifs into the temporary directory
    log.info("Build vrt for tiles")
    heightmap = temporary_directory / "heightmap.vrt"
    subprocess.run(["gdalbuildvrt", "-input_file_list", warped_file_list, heightmap], check=True)

    dataset_bbox = image_get_boundingbox(heightmap)
    log.info(
        "Dataset bounds:",
        dataset_bbox.minx,
        dataset_bbox.miny,
        dataset_bbox.maxx,
        dataset_bbox.maxy,
    )

    layerjson_str = render_layerjson(dataset_bbox, 15)

    tiles_output_dir = temporary_directory / "tiles"
    tiles_output_dir.mkdir(parents=True, exist_ok=True)
    layerjson_path = tiles_output_dir / "layer.json"
    layerjson_path.write_text(layerjson_str)
    log.info(f"Wrote layer.json to {layerjson_path}")

    # Generate dummy tiles for lower zoom levels (0-6)
    dummy_tiles = calculate_pyramid(dataset_bbox, 0, 6)
    log.info(f"Generating {len(dummy_tiles)} dummy tiles (zoom 0-6)...")
    generate_terrain_dummy(dummy_tiles, tiles_output_dir)

    # Generate real tiles for higher zoom levels (7-15)
    real_tiles = calculate_pyramid(dataset_bbox, 6, 15)
    log.info(f"Generating {len(real_tiles)} real tiles (zoom 7-15)...")

    # Configuration
    max_workers = os.cpu_count()  # Use all available CPU cores
    intermediate_dir = temporary_directory / "intermediate"

    log.info(f"  Workers: {max_workers}")
    log.info(f"  Intermediate directory: {intermediate_dir}")

    # Phase 1: Generate intermediate tiles (TINs)
    log.info(f"\n{'=' * 70}")
    generate_intermediate_tiles(
        heightmap,
        real_tiles,
        intermediate_dir,
        max_workers=max_workers,
    )

    # Phase 2: Apply vertex averaging
    log.info(f"\n{'=' * 70}")
    apply_vertex_averaging(
        intermediate_dir,
        max_workers=max_workers,
    )

    # Phase 3: Encode terrain tiles
    log.info(f"\n{'=' * 70}")
    encode_terrain_tiles(
        intermediate_dir,
        tiles_output_dir,
        max_workers=max_workers
    )

    log.info(f"\n{'=' * 70}")
    log.info("Tile generation complete!")

    log.info("Uploading tiles")
    handler.upload_folder(tiles_output_dir, destination)

    log.info("Done with the workflow, enjoy!")


def generate_workflow() -> None:
    with get_workflow_template(__name__.split('.')[-1],
                               entrypoint="dag",
                               arguments=[
                                   Parameter(name="source", default="azure://<sas>"),
                                   Parameter(name="destination", default="azure://<sas>"),
                                   Parameter(name="intermediate", default="azure://<sas>"),
                                   Parameter(name="workercount", default="1")
    ]) as w:
        with DAG(name="dag", inputs=[Parameter(name="source"),
                                     Parameter(name="destination"),
                                     Parameter(name="intermediate"),
                                     Parameter(name="workercount")]):
            queue: Script = queuefunc(arguments={  # type: ignore
                "source": "{{inputs.parameters.source}}",
                "intermediate": "{{inputs.parameters.intermediate}}",
                "workercount": "{{inputs.parameters.workercount}}"})  # type: ignore

            worker = workerfunc(with_param=queue.result, arguments=[queue.get_artifact("queue").with_name("queue"), {  # type: ignore
                                                                    "workerid": "{{item}}",
                                                                    "source": "{{inputs.parameters.source}}",
                                                                    "intermediate": "{{inputs.parameters.intermediate}}"}])  # type: ignore
            merger = mergerfunc(arguments={  # type: ignore
                "intermediate": "{{inputs.parameters.intermediate}}",
                "destination": "{{inputs.parameters.destination}}"
            })  # type: ignore

            queue >> worker >> merger  # type: ignore

        with open(f"generated/{w.name}.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
