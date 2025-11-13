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
        subprocess.run(["gdalwarp", "-s_srs", "EPSG:7415", "-co", "TILES=YES", "-co", "BLOCKXSIZE=67", "-co", "BLOCKYSIZE=67", "-q", "-t_srs", "EPSG:4326", tile_filled, tile_warped], check=True)
        subprocess.run(["gdaladdo", "-r", "nearest", tile_warped, "2", "4", "8"], check=True)
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
        warped_tile_path = str(handler.download_file(tile.full_uri))
        warped_files.append(warped_tile_path + "\n")

    log.info(f"Found {len(warped_files)} warped tiles")

    start_zoom = 15
    end_zoom = 0

    log.info("")
    warped_file_list = temporary_directory / f"warped.txt"
    with open(warped_file_list, "w") as f:
        f.writelines(warped_files)

    # Build a VRT from our list of warped tifs into the temporary directory
    log.info(f"Build vrt for tiles")
    warped_tile_vrt = temporary_directory / f"level{start_zoom+1}.vrt"
    subprocess.run(["gdalbuildvrt", "-input_file_list", warped_file_list, warped_tile_vrt], check=True)

    output_directory: Path = temporary_directory / "quantized_mesh"
    os.makedirs(output_directory, exist_ok=True)

    try:
        log.info("Creating layer.json file...")
        subprocess.run([
            "ctb-tile", "-v", "-f", "Mesh", "-C", "-N", "-s", str(start_zoom), "-e", str(end_zoom), "-l", "--output-dir", str(output_directory), warped_tile_vrt
        ], check=True)

        for zoom_level in range(start_zoom, -1, -1):
            zoom_vrt: str = str(temporary_directory / f"level{zoom_level+1}.vrt")
            zoom_level_dir: Path = temporary_directory / "zoom" / str(zoom_level)
            os.makedirs(zoom_level_dir, exist_ok=True)

            log.info(f"Creating terrain mesh for {zoom_level}...")
            subprocess.run([
                "ctb-tile", "-v", "-s", str(zoom_level), "-e", str(zoom_level), "--output-dir", str(output_directory), zoom_vrt
            ], check=True)

            
            log.info(f"Done creating the terrain mesh, now create a tif for zoomlevel, we will use it to create another mesh {zoom_level}...")
            subprocess.run([
                "ctb-tile", "-v", "--output-format", "GTiff", "-s", str(zoom_level), "-e", str(zoom_level), "--output-dir", str(zoom_level_dir), zoom_vrt
            ], check=True)

            # Create the list of files
            tif_files: list[str] = []

            # Find all files matching the pattern
            for file_path in zoom_level_dir.rglob("*.tif"):
                if file_path.is_file():
                    tif_files.append(str(file_path))
            
            if not tif_files:
                raise FileNotFoundError("No files found matching the pattern")
            
            # Sort files for consistent ordering
            tif_files.sort()
            
            # Write file list to temporary text file
            temp_list_file: Path = zoom_level_dir / "tiffs.txt"
            with open(temp_list_file, 'w') as f:
                for tif_file in tif_files:
                    f.write(f"{tif_file}\n")

            level_vrt = temporary_directory / f"level{zoom_level}.vrt"
            log.info(f"Create vrt for GTiff tiles on level {zoom_level}...")
            subprocess.run(["gdalbuildvrt", '-input_file_list', temp_list_file, str(level_vrt)], check=True)

        handler.upload_folder(output_directory, handler.navigate(destination, "mesh"))
        handler.upload_folder(temporary_directory / "zoom", handler.navigate(destination, "source"))
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
