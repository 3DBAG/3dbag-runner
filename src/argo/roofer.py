from hera.workflows import Artifact, DAG, WorkflowTemplate, Parameter, Script
from hera.workflows.models.io.argoproj.workflow.v1alpha1 import RetryStrategy

from argo.argodefaults import argo_worker, MEMORY_EMPTY_DIR

# Create a list to store the futures


@argo_worker(outputs=Artifact(name="queue", path="/workflow/queue.json"), volumes=MEMORY_EMPTY_DIR)
def queuefunc(workercount: int, footprints: str, cityjsonfolder: str, year: int) -> None:
    import logging
    import json
    import sys

    from roofhelper.kadaster.geo import grid_create_on_intersecting_centroid
    from roofhelper.io import SchemeFileHandler
    from roofhelper.defautlogging import setup_logging
    from pathlib import Path

    logger = setup_logging(logging.INFO)

    file_handler = SchemeFileHandler(Path("/workflow"))
    footprint_path = file_handler.download_file(footprints)
    grid = grid_create_on_intersecting_centroid(footprint_path, 2000)

    queue = []
    for worker, extent in enumerate(grid):
        name = f"buildings_{year}_{int(extent[0])}_{int(extent[1])}"
        cityjson_file = file_handler.navigate(cityjsonfolder, f"{name}.city.json")
        logger.info(f"Preparing to queue {name}")

        if not file_handler.file_exists(cityjson_file):
            logger.info(f"Queued {name}")
            queue.append({"worker": worker % workercount,  # We can also do this implicitly by list index, but lets make it explicit to we can choose based
                          "extent": extent,  # on footprint count so we can control who does what in the future.
                          "destination": cityjson_file})
        else:
            logger.info(f"Skipped {name}, it already exists")

    with open("/workflow/queue.json", 'w') as f:
        json.dump(queue, f)

    logger.info(f"Starting {workercount} workers")
    json.dump([i for i in range(workercount)], sys.stdout)


@argo_worker(inputs=Artifact(name="queue", path="/workflow/queue.json"), retry_strategy=RetryStrategy(limit=5))  # type: ignore
def workerfunc(workerid: int, footprints: str, year: int, dsm: str, ahn4: str, ahn3: str) -> None:
    import json
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor
    import logging

    from main import runsingleroofertile
    from roofhelper.defautlogging import setup_logging
    from roofhelper.io import SchemeFileHandler

    logger = setup_logging(logging.INFO)
    logger.info("Initializing worker node")

    with open("/workflow/queue.json") as f:
        global_queue = json.load(f)

    logger.info(f"Done reading the global queue, it contains {len(global_queue)} items")
    local_queue = [x for x in global_queue if int(x["worker"]) == workerid]
    logger.info(f"Worker has to process {len(local_queue)} items of the queue")

    file_handler = SchemeFileHandler(Path("/workflow/footprints"))
    footprints_file = file_handler.download_file(footprints)

    def process_task(index: int, work: dict[str, str]) -> None:
        destination = work['destination']
        logger.info(f"Processing [{index}/{len(local_queue)}] {destination}.")
        if file_handler.file_exists(destination):
            logger.info(f"Skipping {destination}")

        x = work["extent"]
        extent = (float(x[0]), float(x[1]), float(x[2]), float(x[3]))
        runsingleroofertile(
            extent,
            f"file://{footprints_file}",
            [ahn4, ahn3],
            ["AHN4", "AHN3"],
            year,
            destination,
            Path(f"/workflow/{index}"),
            [dsm],
            [str(year)]
        )

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(process_task, idx, work) for idx, work in enumerate(local_queue)]
        for future in futures:
            future.result()


def generate_workflow() -> None:
    with WorkflowTemplate(name="roofer",
                          generate_name="roofer-",
                          entrypoint="rooferdag",
                          namespace="argo",
                          service_account_name="workflow-runner",
                          image_pull_secrets="acrdddprodman",
                          arguments=[
                              Parameter(name="footprints", default="azure://<sas>"),
                              Parameter(name="year", default="2022"),
                              Parameter(name="dsm", default="azure://<sas>"),
                              Parameter(name="ahn4", default="azure://<sas>"),
                              Parameter(name="ahn3", default="azure://<sas>"),
                              Parameter(name="destination", default="azure://<sas>"),
                              Parameter(name="workercount", default="1")
                          ]) as w:
        with DAG(name="rooferdag"):
            queue: Script = queuefunc(arguments={"workercount": w.get_parameter("workercount"),  # type: ignore
                                                 "footprints": w.get_parameter("footprints"),
                                                 "cityjsonfolder": w.get_parameter("destination"),
                                                 "year": w.get_parameter("year")})  # type: ignore
            worker = workerfunc(with_param=queue.result, arguments=[queue.get_artifact("queue").with_name("queue"), {"workerid": "{{item}}",  # type: ignore
                                                                    "footprints": w.get_parameter("footprints"),
                                                                    "year": w.get_parameter("year"),
                                                                    "dsm": w.get_parameter("dsm"),
                                                                    "ahn4": w.get_parameter("ahn4"),
                                                                    "ahn3": w.get_parameter("ahn3")}])  # type: ignore
            queue >> worker  # type: ignore

        with open("generated/roofer.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
