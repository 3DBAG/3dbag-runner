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
    tifs_source = file_handler.list_entries_shallow(source)
    tifs_generated_set = {x.name for x in file_handler.list_entries_shallow(intermediate)}

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
    import subprocess
    import time
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from osgeo import gdal

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

        tile_filled = f"{temporary_directory}/{tile.stem}_filled.tif"
        log.info(f"Fill no data for {name}")
        t1 = time.perf_counter()
        image_interpolation_idw(input_file=tile, output_file=tile_filled, k=8, power=2.0, batch_size=500_000)  # batch size has the most influence on memory usage
        log.info(f"Filled nodata for {name} in {time.perf_counter() - t1:.2f}s")

        log.info(f"Warping {name}")
        tile_warped = temporary_directory / f"{name}_warped_4326.tif"
        t2 = time.perf_counter()
        subprocess.run(["gdalwarp", "-s_srs", "EPSG:7415", "-q", "-t_srs", "EPSG:4326", tile_filled, tile_warped], check=True)
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
    import glob
    import logging
    import os
    import subprocess
    from pathlib import Path

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
        warped_files.append(str(handler.download_file(tile.full_uri)) + "\n")

    log.info(f"Found {len(warped_files)} warped tiles")
    file_list = temporary_directory / "tif_4_vrt.txt"
    with open(file_list, "w") as f:
        f.writelines(warped_files)

    # Build a VRT from our list of warped tifs into the temporary directory
    log.info(f"Build vrt for tiles")
    vrt_path = temporary_directory / "ahn.vrt"
    subprocess.run(["gdalbuildvrt", "-input_file_list", file_list, vrt_path, "-a_srs", "EPSG:4326"], check=True)

    start_zoom = 15
    break_zoom = 11
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
