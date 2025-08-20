from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import logging
from typing import Any, Optional

from roofhelper.io import SchemeFileHandler

log = logging.getLogger()


def infer_type(val: Any) -> str:
    """
    Map Python types to your target schema types.
    """
    if isinstance(val, bool):
        return 'bool'
    if isinstance(val, int):
        return 'int'
    if isinstance(val, float):
        return 'float'
    if isinstance(val, str):
        return 'string'
    if val is None:
        return 'string'   # or 'null' if you prefer
    # fallback for lists/objects
    return 'string'


def extract_schema(data: dict[Any, Any]) -> str:
    schema = {}
    city_objects = data.get('CityObjects', {})
    for _, obj in city_objects.items():
        attrs = obj.get('attributes', {})
        for name, val in attrs.items():
            schema[name] = infer_type(val)
        # only one object needed for schema
        break

    return ','.join(f"{k}:{t}" for k, t in schema.items())

# Make these functions auto detect the object types


def cityjsonbuilding_to_glb(features_path: Path, metadata_path: Path, output_path: Path, attribute_mapping: Optional[str] = None) -> None:
    """
    Executes the `tyler` command to convert CityJSON data with fixed parameters,
    allowing customization of features, metadata, and output paths.
    """
    command: list[str] = [
        "tyler",
        "--metadata", str(metadata_path),
        "--features", str(features_path),
        "--output", str(output_path),
        # "--3dtiles-implicit",
        "--object-type", "Building",
        "--object-type", "BuildingPart",
        # "--object-attribute", "objectid:int,bouwjaar:int,bagpandid:string",
        "--3dtiles-metadata-class", "building",
        '--grid-minz=-20',
        '--grid-maxz=300',
        "--color-building", "#ECB7A9",
        "--color-building-installation", "#ECB7A9",
        "--color-building-part", "#ECB7A9",
        "--grid-cellsize=250",
    ]

    if attribute_mapping:
        command.extend(["--object-attribute", attribute_mapping])

    try:
        subprocess.run(command)
        print("tyler command executed successfully.")
    except subprocess.CalledProcessError as e:
        print("Error occurred while executing tyler command:", e)


def cityjsonterrain_to_glb(features_path: Path, metadata_path: Path, output_path: Path, attribute_mapping: Optional[str] = None) -> None:
    """
    Executes the `tyler` command to convert CityJSON terrain data with fixed parameters,
    allowing customization of features, metadata, and output paths.
    """
    command: list[str] = [
        "tyler",
        "--metadata", str(metadata_path),
        "--features", str(features_path),
        "--output", str(output_path),
        # "--3dtiles-implicit",
        "--object-type", "LandUse",
        "--object-type", "PlantCover",
        "--object-type", "WaterBody",
        "--object-type", "Road",
        "--object-type", "GenericCityObject",
        "--object-type", "Bridge",
        # "--object-attribute", "objectid:int,bronhouder:string,bgt_fysiekvoorkomen:string,bgt_type:string",
        "--3dtiles-metadata-class", "terrain",
        "--grid-minz=-15",
        "--grid-maxz=400",
        "--color-bridge", "#B8BBB8",
        "--color-bridge-construction-element", "#B8BBB8",
        "--color-bridge-installation", "#B8BBB8",
        "--color-bridge-part", "#B8BBB8",
        "--color-city-furniture", "#B8BBB8",
        "--color-generic-city-object", "#B8BBB8",
        "--color-land-use", "#C0D9B4",
        "--color-plant-cover", "#CCF085",
        "--color-railway", "#B8BBB8",
        "--color-road", "#B8BBB8",
        "--color-solitary-vegetation-object", "#CCF085",
        "--color-tin-relief", "#CCF085",
        "--color-transport-square", "#B8BBB8",
        "--color-tunnel", "#B8BBB8",
        "--color-tunnel-installation", "#B8BBB8",
        "--color-tunnel-part", "#B8BBB8",
        "--color-water-body", "#04FFF6",
        "--grid-cellsize=250",
    ]

    if attribute_mapping:
        command.extend(["--object-attribute", attribute_mapping])

    try:
        subprocess.run(command)
        print("tyler terrain command executed successfully.")
    except subprocess.CalledProcessError as e:
        print("Error occurred while executing tyler terrain command:", e)


def copy_attributes_to_building_parts(cityjson_data: dict[Any, Any]) -> dict[Any, Any]:
    """Copy attributes from parent Building to its BuildingParts."""
    # Get the main objects
    city_objects = cityjson_data.get('CityObjects', {})

    # Keep track of processed buildings
    for obj_id, obj_data in city_objects.items():
        if obj_data.get('type') == 'Building':
            # Get parent building attributes
            parent_attributes = obj_data.get('attributes', {})
            # Get children (BuildingParts)
            children_ids = obj_data.get('children', [])

            # Copy attributes to each child
            for child_id in children_ids:
                if child_id in city_objects:
                    child_obj = city_objects[child_id]
                    if child_obj.get('type') == 'BuildingPart':
                        # Create attributes dict if it doesn't exist
                        if 'attributes' not in child_obj:
                            child_obj['attributes'] = {}
                        # Update child attributes with parent attributes
                        child_obj['attributes'].update(parent_attributes)

    return cityjson_data


def translate_cityjson(data: dict[Any, Any]) -> dict[Any, Any]:
    translate_base_x = 171800.0
    translate_base_y = 472700.0
    translate_base_z = 0

    scale_base_x = 0.001
    scale_base_y = 0.001
    scale_base_z = 0.001

    scale_x, scale_y, scale_z = data["transform"]["scale"]
    translate_x, translate_y, translate_z = data["transform"]["translate"]

    dX = (translate_x - translate_base_x) / scale_x
    dY = (translate_y - translate_base_y) / scale_y
    dZ = (translate_z - translate_base_z) / scale_z

    scale_difference_x = scale_base_x / scale_x
    scale_difference_y = scale_base_y / scale_y
    scale_difference_z = scale_base_z / scale_z

    for i, (x, y, z) in enumerate(data["vertices"]):
        data["vertices"][i] = (
            int(round((x + dX) / scale_difference_x)),
            int(round((y + dY) / scale_difference_y)),
            int(round((z + dZ) / scale_difference_z))
        )

    data["transform"]["translate"] = (translate_base_x, translate_base_y, translate_base_z)
    data["transform"]["scale"] = (scale_base_x, scale_base_y, scale_base_z)

    return data


def prepare_files(input_folder: str, output_folder: Path) -> Optional[str]:  # This function does too much, split it
    log.info("Start fixing and splitting cityjson files")

    os.makedirs(output_folder, exist_ok=True)
    handler = SchemeFileHandler()

    schema = None

    def _consumer(uri: str, destination: Path) -> None:
        os.makedirs(destination, exist_ok=True)
        cityjson_content = handler.get_bytes(uri).decode()

        cityjson_read = translate_cityjson(json.loads(cityjson_content))
        cityjson_converted = json.dumps(cityjson_read) + "\n"

        result = subprocess.run(["cjseq", "cat"], input=cityjson_converted, capture_output=True, text=True, check=True)
        output = result.stdout

        def _writer(line: str) -> None:
            j = json.loads(line)
            j = copy_attributes_to_building_parts(j)
            if j["type"] == "CityJSONFeature":
                theid = j["id"]
                output_file = Path(os.path.join(destination, f"{theid}.city.jsonl"))

                with open(output_file, "w") as out_file:
                    json.dump(j, out_file, separators=(",", ":"))

        with ThreadPoolExecutor() as executor:
            features = [executor.submit(_writer, x) for x in output.split("\n") if x]
            for feature in as_completed(features):
                feature.result()

            log.info(f"Written {uri} to {destination}, it contained {len(features)} items")

    with ThreadPoolExecutor(max_workers=32) as executor:
        tasks = []
        for entry in handler.list_entries_shallow(input_folder, regex="(i?)^.*\\.city\\.json$"):
            if not entry.is_file:
                continue
            if schema is None:
                cityjson_content = json.loads(handler.get_bytes(entry.full_uri).decode())
                schema = extract_schema(cityjson_content)

            filename_without_extension = entry.name.replace(".city.json", "")
            destination = Path(os.path.join(output_folder, filename_without_extension))
            tasks.append(executor.submit(partial(_consumer, uri=entry.full_uri, destination=destination)))

        if len(tasks) == 0:
            log.error("Could not find any city.json files, aborting")
            raise Exception("Could not find any city.json files, aborting")

        log.info("Done submitting all cityjson files")

        # Use tqdm to add a progress bar
        for task in as_completed(tasks):
            task.result()

    log.info("Done fixing and splitting cityjson files")
    return schema
