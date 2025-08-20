from hera.workflows import Artifact, DAG, WorkflowTemplate, Script, Parameter
from argo.argodefaults import argo_worker, MEMORY_EMPTY_DIR

# Create a list to store the futures


@argo_worker(outputs=Artifact(name="queue", path="/workflow/queue.json"), volumes=MEMORY_EMPTY_DIR)
def queuefunc(workercount: int, source: str) -> None:
    import logging
    import json
    import sys
    import re
    from collections import defaultdict

    from roofhelper.io import SchemeFileHandler
    from roofhelper.defaultlogging import setup_logging
    from pathlib import Path

    logger = setup_logging(logging.INFO)
    file_handler = SchemeFileHandler(Path("/workflow"))

    FILENAME_RE = re.compile(r".+_(\d+)_(\d+)\.city\.json$")
    TILE_SIZE = 2_000  # 2 km grid spacing in RD New

    def _parse_tile_coords(filename: str) -> tuple[int, int]:
        m = FILENAME_RE.match(filename)
        if not m:
            raise ValueError(f"Cannot extract RD New coordinates from '{filename}'")
        return int(m.group(1)), int(m.group(2))

    def region_key(x: int, y: int, region_tiles: int = 10) -> tuple[int, int]:
        region_size_m = region_tiles * TILE_SIZE
        return x // region_size_m, y // region_size_m

    buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    for entry in file_handler.list_entries_shallow(uri=source, regex="(i?)^.*\\.city\\.json$"):
        if not entry.is_file:
            continue
        x, y = _parse_tile_coords(entry.name)
        buckets[region_key(x, y)].append(entry.full_uri)

    regions_sorted = sorted(buckets.keys(), key=lambda k: (k[1], k[0]))

    queue = []
    for index, region in enumerate(buckets[k] for k in regions_sorted):
        logger.info(f"Queued {region}")
        queue.append({
            "worker": index % workercount,
            "partition": region
        })

    with open("/workflow/queue.json", 'w') as f:
        json.dump(queue, f)

    logger.info(f"Starting {workercount} workers")
    json.dump([i for i in range(workercount)], sys.stdout)

# Create a list to store the futures


@argo_worker(inputs=Artifact(name="queue", path="/workflow/queue.json"))  # type: ignore
def workerfunc(workerid: int, mode: str, intermediate: str) -> None:
    import logging
    import json
    import os
    import shutil
    import glob

    from main import tyler_runner
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor

    from roofhelper import zip
    from roofhelper.defaultlogging import setup_logging
    from roofhelper.io import SchemeFileHandler

    logger = setup_logging(logging.INFO)
    logger.info("Initializing worker node")

    with open("/workflow/queue.json") as f:
        global_queue = json.load(f)

    logger.info(f"Done reading the global queue, it contains {len(global_queue)} items")
    local_queue: list[list[str]] = [x["partition"] for x in global_queue if int(x["worker"]) == workerid]
    logger.info(f"Worker has to process {len(local_queue)} items of the queue")

    handler = SchemeFileHandler(Path("/workflow/handler"))

    def _prepare_files(index: int, partition: list[str]) -> None:
        logger.info(f"Downloading [{index}/{len(local_queue)}].")

        partition_directory = f"/workflow/partitions/{index}"
        os.makedirs(partition_directory, exist_ok=True)
        for tileidx, tile in enumerate(partition):
            file = handler.download_file(tile)
            shutil.copy(file, os.path.join(partition_directory, f"{tileidx}.city.json"))

    def _runtyler(index: int, folder: str, total: int) -> None:
        logger.info(f"Running tyler [{index}/{total}] {folder}.")
        tyler_output_directory = f"/workflow/output/{index}"

        tyler_runner(f"file://{folder}", f"file://{tyler_output_directory}", Path(f"/workflow/tempdir/{index}"), mode, Path("/metadata.city.json"))

        zip_name = f"/workflow/zips/{workerid}_{index}.zip"
        zip.zip_dir(Path(tyler_output_directory), Path(zip_name))
        handler.upload_file_directory(Path(zip_name), intermediate)
        logger.info(f"Done running tyler [{index}/{total}] {folder}.")
        shutil.rmtree(folder)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_prepare_files, idx, work) for idx, work in enumerate(local_queue)]
        for future in futures:
            future.result()

    os.makedirs("/workflow/zips")
    with ThreadPoolExecutor(max_workers=4) as executor:
        folders = [f for f in glob.glob("/workflow/partitions/*") if os.path.isdir(f)]
        futures = [executor.submit(_runtyler, idx, work, len(folders)) for idx, work in enumerate(folders)]
        for future in futures:
            future.result()


@argo_worker()
def mergerfunc(intermediate: str, destination: str) -> None:
    import logging
    import subprocess
    import glob
    from pathlib import Path

    from roofhelper.defaultlogging import setup_logging
    from roofhelper.io import SchemeFileHandler
    from roofhelper import zip

    log = setup_logging(logging.INFO)
    log.info("Merging all results")

    handler = SchemeFileHandler(Path("/workflow/downloads"))

    zipfile_list = (entry for entry in handler.list_entries_shallow(uri=intermediate, regex="(i?)^.*\\.zip$") if entry.is_file)
    for zipfile_index, entry in enumerate(zipfile_list):
        log.info(f"Downloading and unzipping {entry.name}")

        zip_path = handler.download_file(entry.full_uri)
        zip.unzip(zip_path, Path(f"/workflow/inputs/{zipfile_index}"))

        handler.delete_if_not_local(zip_path)

    # Merge the results, however the merge command will contain external references, so we have to 'combine' afterwards.
    merge_cmd = ["npx", "3d-tiles-tools", "merge"]
    input_dirs = glob.glob("/workflow/inputs/*")

    for input_dir in input_dirs:
        merge_cmd.extend(["-i", input_dir])

    merge_cmd.extend(["-o", "/workflow/merge"])
    log.info(f"Running merger with {merge_cmd}")
    subprocess.run(merge_cmd)

    log.info("Uploading results")
    handler.upload_folder(Path("/workflow/merge"), destination)
    log.info("Done merging all results")


def generate_workflow() -> None:
    with WorkflowTemplate(name="tyler",
                          generate_name="tyler-",
                          entrypoint="tylerdag",
                          namespace="argo",
                          service_account_name="workflow-runner",
                          image_pull_secrets="acrdddprodman",
                          arguments=[Parameter(name="source", default="azure://<sas>"),
                                     Parameter(name="intermediate", default="azure://<sas>"),
                                     Parameter(name="destination", default="azure://<sas>"),
                                     Parameter(name="mode", default="buildings", enum=["buildings", "terrain"]),
                                     Parameter(name="workercount", default="5")]) as w:
        with DAG(name="tylerdag"):
            queue: Script = queuefunc(arguments={  # type: ignore
                "workercount": w.get_parameter("workercount"),
                "source": w.get_parameter("source")})  # type: ignore

            worker: Script = workerfunc(with_param=queue.result,  # type: ignore
                                        arguments=[queue.get_artifact("queue").with_name("queue"), {"workerid": "{{item}}",  # type: ignore
                                                                                                    "mode": w.get_parameter("mode"),
                                                                                                    "intermediate": w.get_parameter("intermediate")}])  # type: ignore
            merger: Script = mergerfunc(arguments={"intermediate": w.get_parameter("intermediate"),  # type: ignore
                                        "destination": w.get_parameter("destination")})  # type: ignore
            queue >> worker >> merger  # type: ignore

        with open("generated/tyler.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
