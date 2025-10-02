import subprocess
from typing import Optional
from hera.workflows import Artifact, DAG, Parameter, Script
from hera.workflows.models.io.argoproj.workflow.v1alpha1 import RetryStrategy

from argo.argodefaults import argo_worker, MEMORY_EMPTY_DIR, get_workflow_template


@argo_worker()
def workerfunc(source_a: str, source_b: str, destination: str, destination_name_pattern: str) -> None:
    import shutil
    import re
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from functools import partial

    from roofhelper.io import SchemeFileHandler, EntryProperties
    from roofhelper.defaultlogging import setup_logging

    log = setup_logging()
    pattern = re.compile(r"^(?P<test>[a-zA-Z]*)_(?P<x>\d)_(?P<y>\d$)")
    
    class FileCoordinate: 
        x: int
        y: int
        name: str
        uri: str
        
        def __init__(self, x: int, y: int, name: str, uri: str):
            self.x = x
            self.y = y
            self.name = name
            self.uri = uri

        def _key(self) -> tuple[int, int]:
            return (self.x, self.y)
        
        def __hash__(self) -> int:
            return hash(self._key())

    def get_file_coordinate(entry: EntryProperties) -> Optional[FileCoordinate]:
        match = pattern.match(entry.name)
        if match: 
            x = int(match.group("x"))
            y = int(match.group("y"))
            return FileCoordinate(x, y, entry.name, entry.full_uri)
        else: 
            log.warning(f"Warning, i'm unable to match {entry}")
        return None
        

    handler = SchemeFileHandler(Path("/workflow"))
    source_a_entries = {coord for x in handler.list_entries_shallow(source_a, r"$.*\.city\.json$") if x.is_file and (coord := get_file_coordinate(x)) is not None}
    source_b_entries = {coord for x in handler.list_entries_shallow(source_b, r"$.*\.city\.json$") if x.is_file and (coord := get_file_coordinate(x)) is not None}
    
    # Divide into two buckets using set operations
    only_in_a = source_a_entries - source_b_entries
    only_in_b = source_b_entries - source_a_entries
    in_both = source_a_entries & source_b_entries
    
    log.info(f"I found {len(only_in_a)} features that are not overlapping from source a, only copying the result to the output accoding to the following naming pattern {destination_name_pattern}")
    log.info(f"I found {len(only_in_b)} features that are not overlapping from source b, only copying the result to the output accoding to the following naming pattern {destination_name_pattern}")
    log.info(f"I found {len(in_both)} features that are overlapping, merging them with cjseq")

    log.info("Start copying the files that are only_in_a or only_in_b")
    def copy_tile(tile: FileCoordinate, handler: SchemeFileHandler, destination: str, destination_name_pattern: str) -> None:
        """Copy a single tile from source to destination"""
        # Example: "3d_gebouwen_5_10.city.json" if destination_name_pattern is "3d_gebouwen_{x}_{y}.city.json"
        tile_dest_uri = handler.navigate(destination, destination_name_pattern.format(x=tile.x, y=tile.y))
        if not handler.file_exists(tile_dest_uri):
            file = Path()
            try:
                log.info(f"Copying tile {tile.name} from source a to destination")
                file = handler.download_file(tile.uri)
                handler.upload_file_direct(file, tile_dest_uri)
            except Exception as e:
                log.info(f"Something went wrong while uploading {tile._key()}")
            finally:
                handler.delete_if_not_local(file)
            
        else:
            log.info(f"Tile {tile.name} already exists in destination")

    # Process only_in_a files in parallel
    if only_in_a:
        copy_from_a = partial(copy_tile, handler=handler, 
                             destination=destination, destination_name_pattern=destination_name_pattern)
        with ThreadPoolExecutor() as executor:
            list(executor.map(copy_from_a, only_in_a))

    # Process only_in_b files in parallel
    if only_in_b:
        copy_from_b = partial(copy_tile, handler=handler, 
                             destination=destination, destination_name_pattern=destination_name_pattern)
        with ThreadPoolExecutor() as executor:
            list(executor.map(copy_from_b, only_in_b))
    
    def process_tile(tile: FileCoordinate, 
                     source_a: dict[FileCoordinate, FileCoordinate], 
                     source_b: dict[FileCoordinate, FileCoordinate], 
                     index: int,
                     destination: str, 
                     destination_name_pattern: str) -> None:
        """Copy a single tile from source to destination"""
        # Example: "3d_gebouwen_5_10.city.json" if destination_name_pattern is "3d_gebouwen_{x}_{y}.city.json"
        dest_name = destination_name_pattern.format(x=tile.x, y=tile.y)

        # use a dedicated index working directory for intermediate files
        index_workdir = Path(f"/workflow/{index}")
        index_workdir.mkdir(parents=True, exist_ok=True)
        handler = SchemeFileHandler(Path("/workflow"))
        tile_dest_uri = handler.navigate(destination, dest_name)
        if not handler.file_exists(tile_dest_uri):
            log.info(f"Copying tile {tile.name} from source a to destination")
            try:
                file_a = source_a[tile]
                file_b = source_b[tile]

                # download returns a local Path
                local_a = handler.download_file(file_a.uri)
                local_b = handler.download_file(file_b.uri)

                jsonl_a = index_workdir / f"a{tile.name}"
                jsonl_b = index_workdir / f"b{tile.name}"
                merged = index_workdir / dest_name

                subprocess.run(f"cjseq cat {local_a} > {jsonl_a}", shell=True, check=True)
                subprocess.run(f"cjseq cat {local_b} > {jsonl_b}", shell=True, check=True)
                subprocess.run(f"cat {jsonl_a} {jsonl_b} | cjseq collect > {merged}", shell=True, check=True)

                handler.upload_file_direct(Path(merged), tile_dest_uri)
            except Exception as e:
                log.info(f"Something went wrong while uploading {tile._key()}: {e}")
            finally:
                # remove the entire index working directory regardless of success
                try:
                    shutil.rmtree(index_workdir)
                except Exception:
                    log.warning(f"Failed to remove working directory {index_workdir}")
        else:
            log.info(f"Tile {tile.name} already exists in destination")

    log.info("Processing files that are in both, (meaning we have to merge them)")
    # Process in_both
    if in_both:
        # create lookup dicts so process_tile can index into source maps
        source_a_lookup = {obj: obj for obj in source_a_entries}
        source_b_lookup = {obj: obj for obj in source_b_entries}

        with ThreadPoolExecutor() as pool:
            # submit tasks and collect Future objects
            futures = {pool.submit(process_tile, tile, source_a_lookup, source_b_lookup, index, destination, destination_name_pattern)
                       for index, tile in enumerate(in_both)}

            for future in as_completed(futures):
                future.result()

def generate_workflow() -> None:
    with get_workflow_template(__name__.split('.')[-1],
                               entrypoint="splitgpkgdag",
                               arguments=[
                                   Parameter(name="source_a", default="azure://<sas>"),
                                   Parameter(name="source_b", default="azure://<sas>"),
                                   Parameter(name="destination", default="2022"),
                                   Parameter(name="destination_name_pattern", default="3d_gebouwen")
    ]) as w:
        with DAG(name="splitgpkgdag", inputs=[Parameter(name="source"), Parameter(name="destination"), Parameter(name="year"), Parameter(name="postfix")]):
            worker: Script = workerfunc(arguments={  # type: ignore  # noqa: F841
                "source_a": "{{inputs.parameters.source_a}}",
                "source_b": "{{inputs.parameters.source_b}}",
                "destination": "{{inputs.parameters.destination}}",
                "destination_name_pattern": "{{inputs.parameters.destination_name_pattern}}"
            })  # type: ignore

        with open(f"generated/{w.name}.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()